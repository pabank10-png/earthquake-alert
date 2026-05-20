import os
import json
import requests
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta

# ============================================================
#  ⚙️  อ่านค่าจาก Environment Variables (GitHub Secrets)
# ============================================================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_TOKEN", "")
LINE_GROUP_ID             = os.getenv("LINE_GROUP_ID", "")

EMAIL_SENDER    = os.getenv("EMAIL_SENDER", "")
EMAIL_PASSWORD  = os.getenv("EMAIL_PASSWORD", "")
EMAIL_RECEIVERS = os.getenv("EMAIL_RECEIVERS", "").split(",")
SMTP_SERVER     = "smtp.gmail.com"
SMTP_PORT       = 587

MIN_MAGNITUDE   = 5.0
HOURS_BACK      = 0.25  # 15 นาที (ทับซ้อนเพื่อไม่พลาด)
SENT_FILE       = "sent_earthquakes.json"
# ============================================================

ICT = timezone(timedelta(hours=7))

def load_sent_ids():
    """โหลด ID แผ่นดินไหวที่แจ้งไปแล้ว"""
    try:
        with open(SENT_FILE, 'r') as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()

def save_sent_id(eq_id):
    """บันทึก ID แผ่นดินไหวที่แจ้งไปแล้ว"""
    sent_ids = load_sent_ids()
    sent_ids.add(eq_id)
    # เก็บแค่ 200 รายการล่าสุด
    with open(SENT_FILE, 'w') as f:
        json.dump(list(sent_ids)[-200:], f)

def fetch_earthquakes():
    """ดึงข้อมูลแผ่นดินไหวจาก USGS API"""
    end   = datetime.now(timezone.utc)
    start = end - timedelta(hours=HOURS_BACK)
    url = "https://earthquake.usgs.gov/fdsnws/event/1/query"
    params = {
        "format":       "geojson",
        "starttime":    start.strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime":      end.strftime("%Y-%m-%dT%H:%M:%S"),
        "minmagnitude": MIN_MAGNITUDE,
        "orderby":      "time",
        "limit":        50,
        "minlatitude":  -15,
        "maxlatitude":  30,
        "minlongitude": 88,
        "maxlongitude": 145
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("features", [])

def parse_event(eq):
    """แปลงข้อมูลแผ่นดินไหว"""
    props = eq["properties"]
    coords = eq["geometry"]["coordinates"]
    eq_id = eq.get("id", "")
    
    lon, lat = coords[0], coords[1]
    depth    = coords[2]
    mag      = props.get("mag", 0)
    place    = props.get("place", "Unknown")
    ts       = props.get("time", 0) / 1000
    dt_ict   = datetime.fromtimestamp(ts, tz=ICT).strftime("%d %b %Y %H:%M ICT")
    usgs_url = props.get("url", "")
    map_url  = f"https://www.google.com/maps?q={lat},{lon}"
    
    return {
        "id": eq_id,
        "mag": mag, "place": place, "time": dt_ict,
        "depth": depth, "lat": lat, "lon": lon,
        "usgs_url": usgs_url, "map_url": map_url
    }

def magnitude_emoji(mag):
    if mag >= 8:   return "🔴🔴"
    elif mag >= 7: return "🔴"
    elif mag >= 6: return "🟠"
    else:          return "🟡"

def send_line(events):
    """ส่งแจ้งเตือนไป LINE"""
    if not events:
        return True
    
    lines = [f"🌍 แจ้งเตือนแผ่นดินไหว (≥ {MIN_MAGNITUDE})\n"]
    for i, e in enumerate(events[:10], 1):
        em = magnitude_emoji(e['mag'])
        lines.append(
            f"{em} [{i}] M{e['mag']} — {e['place']}\n"
            f"   🕐 {e['time']}\n"
            f"   📍 ความลึก {e['depth']:.1f} กม.\n"
            f"   🗺 {e['map_url']}\n"
        )
    if len(events) > 10:
        lines.append(f"...และอีก {len(events)-10} รายการ ดูเพิ่มที่ Email")
    text = "\n".join(lines)

    payload = {
        "to": LINE_GROUP_ID,
        "messages": [{"type": "text", "text": text}]
    }
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers=headers, json=payload, timeout=15
        )
        if resp.status_code == 200:
            print("✅ LINE ส่งสำเร็จ")
            return True
        else:
            print(f"❌ LINE Error: {resp.status_code} — {resp.text}")
            return False
    except Exception as e:
        print(f"❌ LINE Exception: {e}")
        return False

def send_email(events):
    """ส่ง Email"""
    if not events:
        return True
    
    now_str = datetime.now(ICT).strftime("%d %b %Y %H:%M ICT")
    subject = f"🌍 แจ้งเตือนแผ่นดินไหว ≥ {MIN_MAGNITUDE} | {now_str}"

    rows = ""
    for i, e in enumerate(events, 1):
        em = magnitude_emoji(e['mag'])
        color = "#ff4444" if e['mag'] >= 7 else "#ff8800" if e['mag'] >= 6 else "#ffcc00"
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

    print(f"🔍 กำลังดึงข้อมูลแผ่นดินไหว ≥ {MIN_MAGNITUDE} ย้อนหลัง {HOURS_BACK*60:.0f} นาที...")
    try:
        features = fetch_earthquakes()
        events = [parse_event(f) for f in features]
        print(f"📊 พบ {len(events)} รายการ")

        # กรองเฉพาะแผ่นดินไหวใหม่
        sent_ids = load_sent_ids()
        new_events = [e for e in events if e['id'] not in sent_ids]

        if new_events:
            print(f"🆕 พบแผ่นดินไหวใหม่ {len(new_events)} รายการ")
            send_line(new_events)
            send_email(new_events)

            for event in new_events:
                save_sent_id(event['id'])
        else:
            print("✅ ไม่มีแผ่นดินไหวใหม่")

        print("\n✅ สำเร็จทั้งหมด!")
    except Exception as e:
        print(f"\n❌ เกิดข้อผิดพลาด: {e}")
        raise
