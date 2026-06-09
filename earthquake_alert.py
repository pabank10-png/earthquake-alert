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

MONITORED_POINTS = [
    {"lat": 39.60594025, "lon": -8.852126644},
    {"lat": 52.50569518, "lon": 5.084352641},
    {"lat": 52.00712918, "lon": 4.165127331},
    {"lat": 51.56344144, "lon": 5.706959515},
    {"lat": 52.41243645, "lon": 4.828799606},
    {"lat": 52.40515851, "lon": 5.244806542}
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
    rows = "".join([f"<tr><td style='text-align:center'>{i+1}</td><td style='text-align:center;font-weight:bold'>M{e['mag']}</td><td>{e['place']}</td><td style='text-align:center'>{e['time']}</td><td style='text-align:center'>{e['depth']:.1f} กม.</td><td style='text-align:center'><a href='{e['map_url']}'>📍 Map</a></td><td style='text-align:center'><a href='{e['usgs_url']}'>🔗 EMSC</a></td></tr>" for i, e in enumerate(events)])
    body = f"<html><body><h2>🌍 รายงานแผ่นดินไหว ≥ {MIN_MAGNITUDE} (EMSC)</h2><table border='1' cellpadding='8' cellspacing='0' style='border-collapse:collapse;width:100%'><thead><tr style='background:#2c3e50;color:white'><th>#</th><th>ขนาด</th><th>สถานที่</th><th>เวลา (ICT)</th><th>ความลึก</th><th>แผนที่</th><th>ข้อมูล</th></tr></thead><tbody>{rows}</tbody></table></body></html>"
    msg = MIMEMultipart("alternative"); msg["Subject"] = f"🌍 แจ้งเตือนแผ่นดินไหว | {now_str}"; msg["From"] = EMAIL_SENDER; msg["To"] = ", ".join(EMAIL_RECEIVERS); msg.attach(MIMEText(body, "html", "utf-8"))
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls(); server.login(EMAIL_SENDER, EMAIL_PASSWORD); server.sendmail(EMAIL_SENDER, EMAIL_RECEIVERS, msg.as_string())
        return True
    except: return False

if __name__ == "__main__":
    # === TEST MODE ===
    print("🧪 TEST MODE: ส่ง dummy event...")
    test_event = [{
        "mag": 5.5, "place": "TEST — ทดสอบระบบแจ้งเตือน LINE หลายกลุ่ม",
        "time": "09 Jun 2026 12:00 ICT", "depth": 10.0,
        "map_url": "https://www.google.com/maps", "usgs_url": ""
    }]
    result = send_line(test_event)
    print(f"LINE result: {result}")
    print("\n✅ TEST เสร็จแล้ว!")
