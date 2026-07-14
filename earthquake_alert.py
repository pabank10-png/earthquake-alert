import os
import json
import requests
import smtplib
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from math import radians, cos, sin, asin, sqrt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta

# ============================================================
# ⚙️ ตั้งค่าระบบ
# ============================================================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_TOKEN", "")
LINE_GROUP_IDS = [gid.strip() for gid in os.getenv("LINE_GROUP_IDS", "").split(",") if gid.strip()]
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_RECEIVERS = [email.strip() for email in os.getenv("EMAIL_RECEIVERS", "").split(",") if email.strip()]

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
MIN_MAGNITUDE = 5.0
HOURS_BACK = 0.25 
MAX_EVENT_AGE_HOURS = 36 
SENT_FILE = "sent_earthquakes.json"
MAX_HISTORY = 200
SOURCE_MATCH_TIME_MINUTES = 5
SOURCE_MATCH_DISTANCE_KM = 50
SOURCE_MATCH_MAG_DIFF = 0.6
TMD_RSS_URL = "https://earthquake.tmd.go.th/feed/rss_tmd.xml"

MONITORED_POINTS = [
    {"lat": 39.60594025, "lon": -8.852126644},
    {"lat": 52.50569518, "lon": 5.084352641},
    {"lat": 52.00712918, "lon": 4.165127331},
    {"lat": 51.56344144, "lon": 5.706959515},
    {"lat": 52.41243645, "lon": 4.828799606},
    {"lat": 52.40515851, "lon": 5.244806542}
]
RADIUS_KM = 600
MONITORED_BOUNDS = [
    {"min_lat": -15, "max_lat": 30, "min_lon": 88, "max_lon": 145},
]
ICT = timezone(timedelta(hours=7))

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dLat = radians(lat2 - lat1)
    dLon = radians(lon2 - lon1)
    a = sin(dLat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dLon/2)**2
    c = 2 * asin(sqrt(a))
    return R * c

def is_target_location(event):
    lat, lon = event['lat'], event['lon']

    for bounds in MONITORED_BOUNDS:
        if (
            bounds["min_lat"] <= lat <= bounds["max_lat"]
            and bounds["min_lon"] <= lon <= bounds["max_lon"]
        ):
            return True

    for point in MONITORED_POINTS:
        if haversine(point['lat'], point['lon'], lat, lon) <= RADIUS_KM:
            return True

    return False

def load_history():
    """Load sent history, including legacy ID-only entries."""
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    if not isinstance(data, list):
        return []

    history = []
    for item in data:
        if isinstance(item, str) and item:
            history.append({"legacy_id": item})
        elif isinstance(item, dict):
            history.append(item)
    return history


def save_history(history):
    """Save recent event fingerprints and source IDs."""
    unique = {}
    for item in history:
        key = item.get("fingerprint") or f"{item.get('source', '')}:{item.get('source_id', '')}"
        if key:
            unique[key] = item

    recent = list(unique.values())[-MAX_HISTORY:]
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(recent, f, ensure_ascii=False, indent=2)
    print(f"💾 บันทึก history แล้ว {len(recent)} รายการ")


def fetch_earthquakes():
    """ดึงข้อมูลจาก EMSC - จัดการ Error กรณีข้อมูลว่าง"""
    now = datetime.now(timezone.utc)
    updated_after = now - timedelta(hours=HOURS_BACK)
    event_start_time = now - timedelta(hours=MAX_EVENT_AGE_HOURS)
    
    url = "https://www.seismicportal.eu/fdsnws/event/1/query"
    params = {
        "format": "json",
        "updatedafter": updated_after.strftime("%Y-%m-%dT%H:%M:%S"),
        "start": event_start_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "minmag": MIN_MAGNITUDE,
        "orderby": "time"
    }
    
    print(f"⚪ [EMSC] ดึงข้อมูลอัปเดตหลัง: {updated_after.astimezone(ICT).strftime('%H:%M:%S')} ICT")
    
    resp = requests.get(url, params=params, timeout=30)
    
    # แก้ไขปัญหา JSON เปล่าที่ทำให้ Error
    if resp.status_code == 204 or not resp.text.strip():
        return []
    
    try:
        # EMSC จะส่งมาเป็นรายบรรทัด (NDJSON) หรือ JSON ก้อนเดียว
        # เราต้องตรวจสอบว่ามีคำว่า "features" ไหม (ถ้าใช้ format: json แบบ GeoJSON)
        data = resp.json()
        if isinstance(data, dict) and "features" in data:
            return data["features"]
        elif isinstance(data, list):
            return data
        return []
    except:
        # ถ้าพยายามแกะ JSON แล้วพัง ให้ลองกวาดเป็นบรรทัด (กันเหนียว)
        events = []
        for line in resp.text.strip().split('\n'):
            try: events.append(json.loads(line))
            except: continue
        return events

def parse_source_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            return parsedate_to_datetime(str(value)).astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None


def fetch_tmd_earthquakes():
    """Fetch TMD's official RSS feed."""
    resp = requests.get(TMD_RSS_URL, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    ns = {"geo": "http://www.w3.org/2003/01/geo/", "tmd": "http://www.earthquake.tmd.go.th"}
    events = []

    for item in root.findall("./channel/item"):
        link = item.findtext("link", "")
        event_time = parse_source_time(item.findtext("tmd:time", "", ns).replace(" UTC", "+00:00"))
        available_at = parse_source_time(item.findtext("pubDate", ""))
        if not event_time:
            continue

        event_id = link.split("earthquake=")[-1] if "earthquake=" in link else ""
        events.append({
            "id": event_id or f"TMD-{event_time.isoformat()}",
            "source": "TMD",
            "source_id": event_id,
            "available_at": available_at or event_time,
            "event_dt_utc": event_time,
            "mag": float(item.findtext("tmd:magnitude", "0", ns)),
            "place": item.findtext("title", "Unknown Region").strip(),
            "time": event_time.astimezone(ICT).strftime("%d %b %Y %H:%M ICT"),
            "depth": float(item.findtext("tmd:depth", "0", ns)),
            "lat": float(item.findtext("geo:lat", "0", ns)),
            "lon": float(item.findtext("geo:long", "0", ns)),
            "source_url": link,
            "usgs_url": link,
            "map_url": f"https://www.google.com/maps?q={item.findtext('geo:lat', '0', ns)},{item.findtext('geo:long', '0', ns)}",
        })
    return events


def build_event_id(eq):
    # EMSC มี field ต่างจาก USGS เล็กน้อย
    if isinstance(eq, dict):
        props = eq.get("properties", eq)
        unid = props.get("unid") or eq.get("id")
        if unid: return str(unid)
        # ถ้าไม่มี ID จริงๆ ให้สร้างจากข้อมูล
        return f"{props.get('time')}|{props.get('mag')}|{props.get('lat')}|{props.get('lon')}"
    return str(eq)

def parse_event(eq):
    """แปลงข้อมูล EMSC ให้เข้ากับรูปแบบเดิมของเรา"""
    # ตรวจสอบว่าเป็นรูปแบบ GeoJSON (features) หรือแบบ Direct JSON
    props = eq.get("properties", eq)
    geom = eq.get("geometry", {})
    coords = geom.get("coordinates", [props.get("lon"), props.get("lat"), props.get("depth")])

    time_val = props.get("time", "")
    try:
        ev_dt = datetime.fromisoformat(time_val.replace("Z", "+00:00"))
    except:
        ev_dt = datetime.now(timezone.utc)

    # EMSC ใช้ flynn_region สำหรับชื่อสถานที่
    place = props.get("flynn_region") or props.get("place") or "Unknown Region"
    unid = props.get("unid") or eq.get("id", "")
    
    return {
        "id": build_event_id(eq),
        "mag": props.get("mag", 0),
        "place": place,
        "time": ev_dt.astimezone(ICT).strftime("%d %b %Y %H:%M ICT"),
        "event_dt_utc": ev_dt,
        "depth": coords[2] if len(coords) > 2 else 0,
        "lat": coords[1],
        "lon": coords[0],
        "usgs_url": f"https://www.seismicportal.eu/eventdetails.html?unid={unid}" if unid else "",
        "map_url": f"https://www.google.com/maps?q={coords[1]},{coords[0]}"
    }

def magnitude_emoji(mag):
    if mag >= 8: return "🔴🔴"
    if mag >= 7: return "🔴"
    if mag >= 6: return "🟠"
    return "🟡"

def send_line(events):
    lines = [f"🌍 แจ้งเตือนแผ่นดินไหว (≥ {MIN_MAGNITUDE})\n"]
    for i, e in enumerate(events[:10], 1):
        lines.append(f"{magnitude_emoji(e['mag'])} [{i}] M{e['mag']} — {e['place']}\n   🕐 {e['time']}\n   📍 ความลึก {e['depth']:.1f} กม.\n   🗺 {e['map_url']}\n")
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}
    message_text = "\n".join(lines)
    all_ok = True
    for group_id in LINE_GROUP_IDS:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers=headers,
            json={"to": group_id, "messages": [{"type": "text", "text": message_text}]},
            timeout=15
        )
        if resp.status_code != 200:
            print(f"⚠️ ส่ง LINE ไปยัง {group_id} ไม่สำเร็จ: {resp.text}")
            all_ok = False
    return all_ok
def send_email(events):
    now_str = datetime.now(ICT).strftime("%d %b %Y %H:%M ICT")
    rows = "".join([f"<tr><td style='text-align:center'>{i+1}</td><td style='text-align:center;font-weight:bold'>M{e['mag']}</td><td>{e['place']}</td><td style='text-align:center'>{e['time']}</td><td style='text-align:center'>{e['depth']:.1f} กม.</td><td style='text-align:center'><a href='{e['map_url']}'>📍 Map</a></td><td style='text-align:center'><a href='{e['usgs_url']}'>🔗 {e.get('source', 'EMSC')}</a></td></tr>" for i, e in enumerate(events)])
    body = f"<html><body><h2>🌍 รายงานแผ่นดินไหว ≥ {MIN_MAGNITUDE} (EMSC)</h2><table border='1' cellpadding='8' cellspacing='0' style='border-collapse:collapse;width:100%'><thead><tr style='background:#2c3e50;color:white'><th>#</th><th>ขนาด</th><th>สถานที่</th><th>เวลา (ICT)</th><th>ความลึก</th><th>แผนที่</th><th>ข้อมูล</th></tr></thead><tbody>{rows}</tbody></table></body></html>"
    msg = MIMEMultipart("alternative"); msg["Subject"] = f"🌍 แจ้งเตือนแผ่นดินไหว | {now_str}"; msg["From"] = EMAIL_SENDER; msg["To"] = ", ".join(EMAIL_RECEIVERS); msg.attach(MIMEText(body, "html", "utf-8"))
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls(); server.login(EMAIL_SENDER, EMAIL_PASSWORD); server.sendmail(EMAIL_SENDER, EMAIL_RECEIVERS, msg.as_string())
        return True
    except: return False

def same_event(a, b):
    """Match the same physical event across TMD and EMSC catalogs."""
    time_diff = abs((a["event_dt_utc"] - b["event_dt_utc"]).total_seconds()) / 60
    distance = haversine(a["lat"], a["lon"], b["lat"], b["lon"])
    magnitude_diff = abs(float(a["mag"]) - float(b["mag"]))
    return (
        time_diff <= SOURCE_MATCH_TIME_MINUTES
        and distance <= SOURCE_MATCH_DISTANCE_KM
        and magnitude_diff <= SOURCE_MATCH_MAG_DIFF
    )


def history_contains(event, history):
    for item in history:
        if event["source_id"] and event["source_id"] == item.get("source_id"):
            return True
        if item.get("event_time"):
            stored = dict(item)
            stored["event_dt_utc"] = parse_source_time(item["event_time"])
            if stored["event_dt_utc"] and same_event(event, stored):
                return True
        if event["id"] == item.get("legacy_id"):
            return True
    return False


def history_record(event):
    return {
        "fingerprint": "|".join((
            event["event_dt_utc"].strftime("%Y-%m-%dT%H:%M:%S"),
            f"{event['lat']:.3f}",
            f"{event['lon']:.3f}",
        )),
        "event_time": event["event_dt_utc"].isoformat(),
        "lat": event["lat"],
        "lon": event["lon"],
        "mag": event["mag"],
        "source": event["source"],
        "source_id": event["source_id"],
    }


def choose_first_source(events):
    """Collapse cross-source duplicates and keep the earliest published record."""
    chosen = []
    for event in sorted(events, key=lambda item: item["available_at"]):
        match = next((item for item in chosen if same_event(event, item)), None)
        if match is None:
            chosen.append(event)
    return chosen


if __name__ == "__main__":
    print("🔍 [TMD + EMSC] เริ่มการตรวจสอบแผ่นดินไหว...")
    try:
        history = load_history()
        print(f"📚 โหลด history มาแล้ว {len(history)} รายการ")

        emsc_raw = fetch_earthquakes()
        emsc_events = [parse_event(item) for item in emsc_raw if item]
        for raw, event in zip(emsc_raw, emsc_events):
            props = raw.get("properties", raw)
            event["source"] = "EMSC"
            event["source_id"] = event["id"]
            event["available_at"] = parse_source_time(props.get("lastupdate")) or event["event_dt_utc"]
            event["source_url"] = event.get("usgs_url", "")

        tmd_events = fetch_tmd_earthquakes()
        all_events = emsc_events + tmd_events
        print(f"📊 EMSC {len(emsc_events)} รายการ | TMD {len(tmd_events)} รายการ")

        now_utc = datetime.now(timezone.utc)
        recent = [e for e in all_events if (
            e["mag"] >= MIN_MAGNITUDE
            and timedelta(0) <= now_utc - e["event_dt_utc"] <= timedelta(hours=MAX_EVENT_AGE_HOURS)
        )]
        in_area = [e for e in recent if is_target_location(e)]
        selected = choose_first_source(in_area)
        new_events = [e for e in selected if not history_contains(e, history)]

        print(f"📊 เหลือ event ล่าสุดในพื้นที่ {len(selected)} รายการ")
        if new_events:
            print(f"🆕 พบ event ใหม่ {len(new_events)} รายการ")
            for event in new_events:
                print(f"   - [{event['source']}] {event['source_id']} | M{event['mag']} | {event['place']}")
            if send_line(new_events) and send_email(new_events):
                history.extend(history_record(event) for event in new_events)
                save_history(history)
        else:
            print("✅ ไม่มีแผ่นดินไหวใหม่ในพื้นที่เฝ้าระวัง")
            save_history(history)
        print("\n✅ สำเร็จทั้งหมด!")
    except Exception as e:
        print(f"\n❌ เกิดข้อผิดพลาด: {e}")
        raise
