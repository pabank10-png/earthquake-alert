import os
import json
import requests
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta

# ============================================================
# Read values from Environment Variables (GitHub Secrets)
# ============================================================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_TOKEN", "")
LINE_GROUP_ID = os.getenv("LINE_GROUP_ID", "")

EMAIL_SENDER = os.getenv("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_RECEIVERS = [email.strip() for email in os.getenv("EMAIL_RECEIVERS", "").split(",") if email.strip()]
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

MIN_MAGNITUDE = 5.0
HOURS_BACK = 0.25  # 15 minutes overlap, so persisted IDs must dedupe repeats.
MAX_EVENT_AGE_HOURS = 36  # Alert only for recent actual earthquake times.
SENT_FILE = "sent_earthquakes.json"
MAX_HISTORY = 200
# ============================================================

ICT = timezone(timedelta(hours=7))


def load_sent_ids():
    """Load sent earthquake IDs while preserving file order."""
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        print(f"⚠️ {SENT_FILE} is invalid JSON; starting with empty history")
        return []

    if not isinstance(data, list):
        print(f"⚠️ {SENT_FILE} should contain a JSON list; starting with empty history")
        return []

    return [str(item) for item in data if item]


def save_sent_ids(sent_ids):
    """Save sent earthquake IDs in stable order."""
    unique_ids = list(dict.fromkeys(str(item) for item in sent_ids if item))
    recent_ids = unique_ids[-MAX_HISTORY:]

    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(recent_ids, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"💾 บันทึก history แล้ว {len(recent_ids)} IDs")


def fetch_earthquakes():
    """Fetch earthquakes based on the time data was posted or updated in the API."""
    now = datetime.now(timezone.utc)
    updated_after = now - timedelta(hours=HOURS_BACK)
    event_start_time = now - timedelta(hours=MAX_EVENT_AGE_HOURS)

    url = "https://earthquake.usgs.gov/fdsnws/event/1/query"
    params = {
        "format": "geojson",
        "updatedafter": updated_after.strftime("%Y-%m-%dT%H:%M:%S"),
        "starttime": event_start_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "minmagnitude": MIN_MAGNITUDE,
        "orderby": "time",
        "minlatitude": -15,
        "maxlatitude": 30,
        "minlongitude": 88,
        "maxlongitude": 145,
    }

    print(f"🕒 ดึงข้อมูลที่ 'อัปเดต' หลังเวลา: {updated_after.astimezone(ICT).strftime('%H:%M:%S')} ICT")
    print(
        "📅 รับเฉพาะเหตุการณ์ที่เกิดหลังเวลา: "
        f"{event_start_time.astimezone(ICT).strftime('%d %b %Y %H:%M ICT')}"
    )

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("features", [])


def build_event_id(eq):
    """Build a stable dedupe key for one USGS event."""
    props = eq.get("properties", {})
    geometry = eq.get("geometry") or {}
    coords = geometry.get("coordinates") or [None, None, None]

    usgs_id = eq.get("id")
    if usgs_id:
        return str(usgs_id)

    lon, lat, depth = coords[:3]
    return "|".join(
        str(part)
        for part in (
            props.get("time"),
            props.get("mag"),
            round(float(lat), 3) if lat is not None else "",
            round(float(lon), 3) if lon is not None else "",
            round(float(depth), 1) if depth is not None else "",
        )
    )


def parse_event(eq):
    """Convert raw earthquake data into alert data."""
    props = eq["properties"]
    coords = eq["geometry"]["coordinates"]
    eq_id = build_event_id(eq)

    lon, lat = coords[0], coords[1]
    depth = coords[2]
    mag = props.get("mag", 0)
    place = props.get("place", "Unknown")
    ts = props.get("time", 0) / 1000
    event_dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    dt_ict = event_dt_utc.astimezone(ICT).strftime("%d %b %Y %H:%M ICT")
    usgs_url = props.get("url", "")
    map_url = f"https://www.google.com/maps?q={lat},{lon}"

    return {
        "id": eq_id,
        "mag": mag,
        "place": place,
        "time": dt_ict,
        "event_dt_utc": event_dt_utc,
        "depth": depth,
        "lat": lat,
        "lon": lon,
        "usgs_url": usgs_url,
        "map_url": map_url,
    }


def magnitude_emoji(mag):
    if mag >= 8:
        return "🔴🔴"
    if mag >= 7:
        return "🔴"
    if mag >= 6:
        return "🟠"
    return "🟡"


def is_recent_event(event, now=None):
    """Return True when the actual earthquake time is recent enough to alert."""
    now = now or datetime.now(timezone.utc)
    max_age = timedelta(hours=MAX_EVENT_AGE_HOURS)
    event_age = now - event["event_dt_utc"]
    return timedelta(0) <= event_age <= max_age


def send_line(events):
    """Send alerts to LINE."""
    if not events:
        return True

    lines = [f"🌍 แจ้งเตือนแผ่นดินไหว (≥ {MIN_MAGNITUDE})\n"]
    for i, e in enumerate(events[:10], 1):
        em = magnitude_emoji(e["mag"])
        lines.append(
            f"{em} [{i}] M{e['mag']} — {e['place']}\n"
            f"   🕐 {e['time']}\n"
            f"   📍 ความลึก {e['depth']:.1f} กม.\n"
            f"   🗺 {e['map_url']}\n"
        )
    if len(events) > 10:
        lines.append(f"...และอีก {len(events) - 10} รายการ ดูเพิ่มที่ Email")
    text = "\n".join(lines)

    payload = {
        "to": LINE_GROUP_ID,
        "messages": [{"type": "text", "text": text}],
    }
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers=headers,
            json=payload,
            timeout=15,
        )
        if resp.status_code == 200:
            print("✅ LINE ส่งสำเร็จ")
            return True

        print(f"❌ LINE Error: {resp.status_code} — {resp.text}")
        return False
    except Exception as e:
        print(f"❌ LINE Exception: {e}")
        return False


def send_email(events):
    """Send email alerts."""
    if not events:
        return True

    now_str = datetime.now(ICT).strftime("%d %b %Y %H:%M ICT")
    subject = f"🌍 แจ้งเตือนแผ่นดินไหว ≥ {MIN_MAGNITUDE} | {now_str}"

    rows = ""
    for i, e in enumerate(events, 1):
        em = magnitude_emoji(e["mag"])
        color = "#ff4444" if e["mag"] >= 7 else "#ff8800" if e["mag"] >= 6 else "#ffcc00"
        rows += f"""
        <tr>
            <td style="text-align:center">{i}</td>
            <td style="text-align:center;font-weight:bold;color:{color}">{em} M{e['mag']}</td>
            <td>{e['place']}</td>
            <td style="text-align:center">{e['time']}</td>
            <td style="text-align:center">{e['depth']:.1f} กม.</td>
            <td style="text-align:center">
                <a href="{e['map_url']}" style="color:#1a73e8">📍 Map</a>
            </td>
            <td style="text-align:center">
                <a href="{e['usgs_url']}" style="color:#1a73e8">🔗 USGS</a>
            </td>
        </tr>"""

    body_html = f"""
    <html><body style="font-family:Arial,sans-serif;padding:20px">
    <h2 style="color:#c0392b">🌍 รายงานแผ่นดินไหว ≥ {MIN_MAGNITUDE}</h2>
    <p>พบทั้งหมด <b>{len(events)} รายการ</b> | {now_str}</p>
    <table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse;width:100%;font-size:14px">
        <thead style="background:#2c3e50;color:white">
            <tr>
                <th>#</th><th>ขนาด</th><th>สถานที่</th>
                <th>เวลา</th><th>ความลึก</th><th>แผนที่</th><th>ข้อมูล</th>
            </tr>
        </thead>
        <tbody>{rows}</tbody>
    </table>
    <br><p style="color:#888;font-size:12px">ข้อมูลจาก USGS Earthquake Hazards Program</p>
    </body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_SENDER
    msg["To"] = ", ".join(EMAIL_RECEIVERS)
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVERS, msg.as_string())
        print("✅ Email ส่งสำเร็จ")
        return True
    except Exception as e:
        print(f"❌ Email Error: {e}")
        return False


if __name__ == "__main__":
    if os.getenv("TEST_MODE", "false").lower() == "true":
        print("🧪 TEST_MODE เปิดอยู่: ส่งแจ้งเตือนทดสอบ")

        test_event = {
            "id": f"test-{datetime.now(ICT).strftime('%Y%m%d%H%M')}",
            "mag": 5.9,
            "place": "TEST ALERT - ระบบทดสอบแจ้งเตือนแผ่นดินไหว",
            "time": datetime.now(ICT).strftime("%d %b %Y %H:%M ICT"),
            "depth": 10.0,
            "lat": 13.7563,
            "lon": 100.5018,
            "usgs_url": "https://earthquake.usgs.gov/",
            "map_url": "https://www.google.com/maps?q=13.7563,100.5018",
        }

        line_ok = send_line([test_event])
        email_ok = send_email([test_event])

        if line_ok and email_ok:
            print("✅ ส่ง TEST alert สำเร็จ")
        else:
            print("⚠️ ส่ง TEST alert บางช่องทางไม่สำเร็จ")

        raise SystemExit(0)

    print(
        f"🔍 กำลังดึงข้อมูลแผ่นดินไหว ≥ {MIN_MAGNITUDE} "
        f"ที่ API อัปเดตย้อนหลัง {HOURS_BACK * 60:.0f} นาที "
        f"และเกิดจริงไม่เกิน {MAX_EVENT_AGE_HOURS:.0f} ชั่วโมง..."
    )
    try:
        sent_ids = load_sent_ids()
        sent_id_set = set(sent_ids)
        print(f"📚 โหลด history มาแล้ว {len(sent_ids)} IDs")

        features = fetch_earthquakes()
        fetched_events = [parse_event(f) for f in features]
        print(f"📊 พบจาก USGS {len(fetched_events)} รายการ")

        now_utc = datetime.now(timezone.utc)
        events = [e for e in fetched_events if is_recent_event(e, now_utc)]
        stale_events = [e for e in fetched_events if not is_recent_event(e, now_utc)]

        if stale_events:
            print(f"🕰️ ข้าม event เก่าเกิน {MAX_EVENT_AGE_HOURS:.0f} ชั่วโมง:")
            for event in stale_events:
                print(f"   - {event['id']} | {event['time']} | M{event['mag']} | {event['place']}")

        print(f"📊 เหลือ event ที่เวลาเกิดอยู่ในช่วงที่อนุญาต {len(events)} รายการ")

        new_events = [e for e in events if e["id"] not in sent_id_set]
        duplicate_events = [e for e in events if e["id"] in sent_id_set]

        if duplicate_events:
            print("♻️ ข้าม event ที่เคยส่งแล้ว:")
            for event in duplicate_events:
                print(f"   - {event['id']} | M{event['mag']} | {event['place']}")

        if new_events:
            print(f"🆕 พบแผ่นดินไหวใหม่ {len(new_events)} รายการ")
            for event in new_events:
                print(f"   - {event['id']} | M{event['mag']} | {event['place']}")

            line_ok = send_line(new_events)
            email_ok = send_email(new_events)

            if line_ok and email_ok:
                save_sent_ids(sent_ids + [event["id"] for event in new_events])
            else:
                raise RuntimeError("ส่งแจ้งเตือนไม่ครบทุกช่องทาง จึงยังไม่บันทึก event เป็น sent")
        else:
            print("✅ ไม่มีแผ่นดินไหวใหม่")
            save_sent_ids(sent_ids)

        print("\n✅ สำเร็จทั้งหมด!")
    except Exception as e:
        print(f"\n❌ เกิดข้อผิดพลาด: {e}")
        raise
