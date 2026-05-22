import os
import json
import requests
import smtplib
from math import radians, cos, sin, asin, sqrt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta

# ============================================================
# ⚙️ ตั้งค่าระบบ
# ============================================================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_TOKEN", "")
LINE_GROUP_ID = os.getenv("LINE_GROUP_ID", "")
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

# 📍 รายการจุดที่ต้องการติดตามเพิ่มเติม (รัศมี 600 กม.)
MONITORED_POINTS = [
    {"lat": 39.60594025, "lon": -8.852126644},  # Portugal
    {"lat": 52.50569518, "lon": 5.084352641},   # Netherlands 1
    {"lat": 52.00712918, "lon": 4.165127331},   # Netherlands 2
    {"lat": 51.56344144, "lon": 5.706959515},   # Netherlands 3
    {"lat": 52.41243645, "lon": 4.828799606},   # Netherlands 4
    {"lat": 52.40515851, "lon": 5.244806542}    # Netherlands 5
]
RADIUS_KM = 600

ICT = timezone(timedelta(hours=7))

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dLat = radians(lat2 - lat1)
    dLon = radians(lon2 - lon1)
    a = sin(dLat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dLon/2)**2
    c = 2 * asin(sqrt(a))
    return R * c

def is_target_location(event):
    place = event['place'].lower()
    lat, lon = event['lat'], event['lon']
    sea_keywords = [
        "thailand", "myanmar", "burma", "laos", "vietnam", "cambodia",
        "malaysia", "singapore", "indonesia", "philippines", "brunei",
        "timor-leste", "papua new guinea", "andaman", "sumatra", "java",
        "sulawesi", "borneo", "luzon", "mindanao", "molucca", "banda sea",
        "celebes sea", "sulu sea", "south china sea", "gulf of thailand",
        "sea", "ocean"
    ]
    if any(k in place for k in sea_keywords): return True
    for point in MONITORED_POINTS:
        if haversine(point['lat'], point['lon'], lat, lon) <= RADIUS_KM: return True
    return False

def load_sent_ids():
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [str(item) for item in data if item] if isinstance(data, list) else []
    except: return []

def save_sent_ids(sent_ids):
    unique_ids = list(dict.fromkeys(str(item) for item in sent_ids if item))
    recent_ids = unique_ids[-MAX_HISTORY:]
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(recent_ids, f, ensure_ascii=False, indent=2)
    print(f"💾 บันทึก history แล้ว {len(recent_ids)} IDs")

def fetch_earthquakes():
    now = datetime.now(timezone.utc)
    updated_after = now - timedelta(hours=HOURS_BACK)
    event_start_time = now - timedelta(hours=MAX_EVENT_AGE_HOURS)
    url = "https://earthquake.usgs.gov/fdsnws/event/1/query"
    params = {
        "format": "geojson",
        "updatedafter": updated_after.strftime("%Y-%m-%dT%H:%M:%S"),
        "starttime": event_start_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "minmagnitude": MIN_MAGNITUDE,
        "orderby": "time"
    }
    print(f"⚪ ดึงข้อมูลที่ 'อัปเดต' หลังเวลา: {updated_after.astimezone(ICT).strftime('%H:%M:%S')} ICT")
    print(f"📅 รับเฉพาะเหตุการณ์ที่เกิดหลังเวลา: {event_start_time.astimezone(ICT).strftime('%d %b %Y %H:%M ICT')}")
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("features", [])

def build_event_id(eq):
    props = eq["properties"]; coords = eq["geometry"]["coordinates"]
    if eq.get("id"): return str(eq.get("id"))
    return f"{props.get('time')}|{props.get('mag')}|{coords[1]}|{coords[0]}|{coords[2]}"

def parse_event(eq):
    props = eq["properties"]; coords = eq["geometry"]["coordinates"]
    ts = props.get("time", 0) / 1000; ev_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return {
        "id": build_event_id(eq), "mag": props.get("mag", 0), "place": props.get("place", "Unknown"),
        "time": ev_dt.astimezone(ICT).strftime("%d %b %Y %H:%M ICT"), "event_dt_utc": ev_dt,
        "depth": coords[2], "lat": coords[1], "lon": coords[0], "usgs_url": props.get("url", ""),
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
    resp = requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json={"to": LINE_GROUP_ID, "messages": [{"type": "text", "text": "\n".join(lines)}]}, timeout=15)
    return resp.status_code == 200

def send_email(events):
    now_str = datetime.now(ICT).strftime("%d %b %Y %H:%M ICT")
    rows = ""
    for i, e in enumerate(events):
        rows += f"""
        <tr>
            <td style='text-align:center'>{i+1}</td>
            <td style='text-align:center;font-weight:bold'>M{e['mag']}</td>
            <td>{e['place']}</td>
            <td style='text-align:center'>{e['time']}</td>
            <td style='text-align:center'>{e['depth']:.1f} กม.</td>
            <td style='text-align:center'><a href='{e['map_url']}'>📍 Map</a></td>
            <td style='text-align:center'><a href='{e['usgs_url']}'>🔗 USGS</a></td>
        </tr>"""
    
    body = f"""
    <html><body>
        <h2>🌍 รายงานแผ่นดินไหว ≥ {MIN_MAGNITUDE}</h2>
        <table border='1' cellpadding='8' cellspacing='0' style='border-collapse:collapse;width:100%'>
            <thead>
                <tr style='background:#2c3e50;color:white'>
                    <th>#</th><th>ขนาด</th><th>สถานที่</th><th>เวลา (ICT)</th><th>ความลึก</th><th>แผนที่</th><th>ข้อมูล</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
    </body></html>"""
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🌍 แจ้งเตือนแผ่นดินไหว | {now_str}"
    msg["From"] = EMAIL_SENDER
    msg["To"] = ", ".join(EMAIL_RECEIVERS)
    msg.attach(MIMEText(body, "html", "utf-8"))
    
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVERS, msg.as_string())
        return True
    except: return False

if __name__ == "__main__":
    print(f"🔍 กำลังดึงข้อมูลแผ่นดินไหว ≥ {MIN_MAGNITUDE} ที่ API อัปเดตย้อนหลัง {HOURS_BACK*60:.0f} นาที และเกิดจริงไม่เกิน {MAX_EVENT_AGE_HOURS} ชั่วโมง...")
    try:
        sent_ids = load_sent_ids(); sent_id_set = set(sent_ids)
        print(f"📚 โหลด history มาแล้ว {len(sent_ids)} IDs")
        fetched_features = fetch_earthquakes()
        parsed_events = [parse_event(f) for f in fetched_features]
        print(f"📊 พบจาก USGS {len(parsed_events)} รายการ")
        
        now_utc = datetime.now(timezone.utc)
        events = [e for e in parsed_events if (timedelta(0) <= (now_utc - e["event_dt_utc"]) <= timedelta(hours=MAX_EVENT_AGE_HOURS))]
        print(f"📊 เหลือ event ที่เวลาเกิดอยู่ในช่วงที่อนุญาต {len(events)} รายการ")
        
        filtered_events = [e for e in events if is_target_location(e)]
        new_events = [e for e in filtered_events if e["id"] not in sent_id_set]

        if new_events:
            print(f"🆕 พบแผ่นดินไหวใหม่ {len(new_events)} รายการ")
            for event in new_events: print(f"   - {event['id']} | M{event['mag']} | {event['place']}")
            if send_line(new_events) and send_email(new_events):
                save_sent_ids(sent_ids + [e["id"] for e in new_events])
            else: print("⚠️ ส่งแจ้งเตือนไม่สำเร็จ")
        else:
            print("✅ ไม่มีแผ่นดินไหวใหม่")
            save_sent_ids(sent_ids)
        print("\n✅ สำเร็จทั้งหมด!")
    except Exception as e:
        print(f"\n❌ เกิดข้อผิดพลาด: {e}"); raise
