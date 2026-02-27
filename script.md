cd ~/carmonitor

cat > monitor.py << 'EOF'
"""
Car Inventory Monitor - unfallautovd.be
v3.3: Sanity check to prevent false "sold" alerts
"""

import asyncio
import json
import os
import re
import random
from datetime import datetime
from playwright.async_api import async_playwright
import httpx

# === CONFIGURE THESE ===
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
WEBSITE_URL = "https://unfallautovd.be/#ourcars"
CHECK_INTERVAL_MIN = 50
CHECK_INTERVAL_MAX = 90
HEALTH_CHECK_HOURS = 6
NOTIFY_SOLD = True
MAX_RETRIES = 3
MIN_CARS_EXPECTED = 3  # If less than this, assume site is broken
MAX_SOLD_AT_ONCE = 5   # If more than this "sold" at once, assume glitch
# =======================

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
]

DATA_FILE = "known_cars.json"
check_count = 0
error_count = 0

def load_known_cars():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_known_cars(cars):
    with open(DATA_FILE, "w") as f:
        json.dump(cars, f, indent=2)

async def send_telegram_message(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text})
    except Exception as e:
        print(f"    Telegram error: {e}")

async def send_telegram_photo_url(photo_url, caption=""):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, data={
                "chat_id": TELEGRAM_CHAT_ID, 
                "photo": photo_url,
                "caption": caption
            })
            return resp.status_code == 200
    except:
        return False

def format_duration(first_seen_iso):
    first_seen = datetime.fromisoformat(first_seen_iso)
    delta = datetime.now() - first_seen
    total_minutes = int(delta.total_seconds() / 60)
    
    if total_minutes < 60:
        return f"{total_minutes} min"
    elif total_minutes < 1440:
        hours = total_minutes // 60
        mins = total_minutes % 60
        return f"{hours}h {mins}m" if mins > 0 else f"{hours}h"
    else:
        days = total_minutes // 1440
        hours = (total_minutes % 1440) // 60
        return f"{days}d {hours}h" if hours > 0 else f"{days}d"

def format_km(km):
    if km is None:
        return "? km"
    return f"{km // 1000}k km" if km >= 1000 else f"{km} km"

def parse_car_data(img_attrs, info_text):
    attrs = img_attrs.lower()
    info = info_text.lower()
    
    brands = ['bmw', 'audi', 'mercedes', 'volkswagen', 'vw', 'opel', 'ford', 'peugeot', 
              'citroen', 'citroën', 'renault', 'fiat', 'kia', 'hyundai', 'toyota', 'honda', 
              'mazda', 'nissan', 'skoda', 'seat', 'volvo', 'mini', 'jeep', 'dacia', 'suzuki', 
              'aprilia', 'porsche', 'tesla', 'lexus', 'alfa', 'land', 'range']
    
    data = {'brand': None, 'year': None, 'km': None, 'gearbox': None, 'fuel': None, 'engine': None}
    
    for brand in brands:
        if f'{brand}=""' in attrs:
            data['brand'] = brand.upper()
            break
    
    year_match = re.search(r'20[1-2][0-9]', attrs)
    if year_match:
        data['year'] = int(year_match.group())
    
    for pattern in [r'(\d{5,6})=""', r'(\d{4,6})\s*km']:
        km_match = re.search(pattern, attrs)
        if km_match:
            km_val = int(km_match.group(1))
            if 1000 < km_val < 500000:
                data['km'] = km_val
                break
    
    if 'automaat' in attrs:
        data['gearbox'] = 'Auto'
    elif 'manueel' in attrs:
        data['gearbox'] = 'Manual'
    
    if 'diesel' in info:
        data['fuel'] = 'Diesel'
    elif 'hybride' in info:
        data['fuel'] = 'Hybrid'
    elif 'benzine' in info:
        data['fuel'] = 'Petrol'
    
    engine_match = re.search(r'(\d{3,4})cc', info)
    if engine_match:
        data['engine'] = f"{int(engine_match.group(1))/1000:.1f}L"
    
    return data

def format_car_message(car, is_new=True):
    meta = car.get('metadata', {})
    brand = meta.get('brand') or '?'
    model = car.get('name', '').split()[0] if car.get('name') else '?'
    
    details = []
    if meta.get('year'): details.append(f"📅 {meta['year']}")
    if meta.get('km'): details.append(f"🛣️ {format_km(meta['km'])}")
    if meta.get('fuel'): details.append(f"⛽ {meta['fuel']}")
    if meta.get('engine'): details.append(f"🔧 {meta['engine']}")
    if meta.get('gearbox'): details.append(f"⚙️ {meta['gearbox']}")
    
    if is_new:
        header = "🚗 NEW CAR!"
    else:
        header = "💰 SOLD!"
        if car.get('first_seen'):
            details.append(f"⏱️ Listed {format_duration(car['first_seen'])}")
    
    lines = [header, "", f"🏷️ {brand} {model}"]
    if details:
        lines.append("")
        lines.extend(details)
    lines.extend(["", f"🔗 {WEBSITE_URL}"])
    
    return "\n".join(lines)

async def fetch_cars_from_site():
    """Fetch cars with retry logic"""
    browser = None
    p = None
    
    for attempt in range(MAX_RETRIES):
        try:
            p = await async_playwright().start()
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage']
            )
            
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=random.choice(USER_AGENTS)
            )
            page = await context.new_page()
            
            await asyncio.sleep(random.uniform(0.5, 1.5))
            await page.goto(WEBSITE_URL, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(random.uniform(2, 3))
            
            cars = await page.evaluate("""
                () => {
                    const results = [];
                    const boxes = document.querySelectorAll('.box1car');
                    boxes.forEach(box => {
                        const info = box.querySelector('.box1carInfo');
                        const img = box.querySelector('img.box1carImage');
                        const link = box.querySelector('a');
                        
                        const onclick = link ? link.getAttribute('onclick') : '';
                        const idMatch = onclick.match(/"(\\d+)"/);
                        
                        let imgAttrs = '';
                        if (img) {
                            imgAttrs = [...img.attributes].map(a => `${a.name}="${a.value}"`).join(' ');
                        }
                        
                        if (info && info.innerText) {
                            const text = info.innerText.trim();
                            const nameMatch = text.match(/[A-Z0-9-]+\\s+\\d{1,2}\\/\\d{4}/);
                            
                            results.push({
                                name: nameMatch ? nameMatch[0] : text.split('\\n')[0],
                                image: img ? img.src : null,
                                infoText: text,
                                imgAttrs: imgAttrs,
                                carId: idMatch ? idMatch[1] : null
                            });
                        }
                    });
                    return results;
                }
            """)
            
            await browser.close()
            await p.stop()
            return cars
            
        except Exception as e:
            print(f"    Attempt {attempt + 1}/{MAX_RETRIES} failed: {str(e)[:50]}")
            if browser:
                try:
                    await browser.close()
                except:
                    pass
            if p:
                try:
                    await p.stop()
                except:
                    pass
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(5)
            else:
                raise e
    
    return []

async def check_inventory():
    global check_count, error_count
    check_count += 1
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Check #{check_count}...")
    
    known_cars = load_known_cars()
    
    try:
        raw_cars = await fetch_cars_from_site()
        error_count = 0
    except Exception as e:
        error_count += 1
        print(f"  Failed to fetch: {e}")
        if error_count >= 5:
            await send_telegram_message(f"⚠️ Monitor having issues\n\n{error_count} consecutive errors\nLast: {str(e)[:100]}")
            error_count = 0
        return
    
    # SANITY CHECK: If we got too few cars, site is probably broken
    if len(raw_cars) < MIN_CARS_EXPECTED:
        print(f"  ⚠️ Only {len(raw_cars)} cars found - site may be down. Skipping.")
        return
    
    current_cars = {}
    for car in raw_cars:
        car_id = car.get('carId')
        if not car_id and car.get('image'):
            car_id = str(hash(car['image']))[-10:]
        if not car_id:
            car_id = str(hash(car['name']))[-10:]
        
        metadata = parse_car_data(car.get('imgAttrs', ''), car.get('infoText', ''))
        
        current_cars[car_id] = {
            'name': car['name'],
            'image': car.get('image'),
            'metadata': metadata,
            'first_seen': known_cars.get(car_id, {}).get('first_seen') or datetime.now().isoformat()
        }
    
    if not known_cars:
        print(f"  First run. Found {len(current_cars)} cars.")
        save_known_cars(current_cars)
        return
    
    known_ids = set(known_cars.keys())
    current_ids = set(current_cars.keys())
    
    new_ids = current_ids - known_ids
    sold_ids = known_ids - current_ids
    
    # SANITY CHECK: If too many "sold" at once, it's probably a glitch
    if len(sold_ids) > MAX_SOLD_AT_ONCE:
        print(f"  ⚠️ {len(sold_ids)} cars 'sold' at once - likely a glitch. Skipping sold alerts.")
        print(f"     (Will still track new cars and update baseline)")
        sold_ids = set()  # Don't send sold alerts
    
    if new_ids:
        print(f"  🚗 NEW: {len(new_ids)}")
        for car_id in new_ids:
            car = current_cars[car_id]
            message = format_car_message(car, is_new=True)
            
            sent = await send_telegram_photo_url(car.get('image'), message) if car.get('image') else False
            if not sent:
                await send_telegram_message(message)
            
            print(f"    → {car['name']}")
    
    if sold_ids and NOTIFY_SOLD:
        print(f"  💰 SOLD: {len(sold_ids)}")
        for car_id in sold_ids:
            car = known_cars[car_id]
            message = format_car_message(car, is_new=False)
            
            sent = await send_telegram_photo_url(car.get('image'), message) if car.get('image') else False
            if not sent:
                await send_telegram_message(message)
            
            duration = format_duration(car['first_seen']) if car.get('first_seen') else '?'
            print(f"    → {car['name']} (after {duration})")
    
    if not new_ids and not sold_ids:
        print(f"  No changes. {len(current_cars)} cars.")
    
    avg_interval = (CHECK_INTERVAL_MIN + CHECK_INTERVAL_MAX) // 2
    checks_per_health = (HEALTH_CHECK_HOURS * 3600) // avg_interval
    if check_count % checks_per_health == 0:
        hours = check_count * avg_interval // 3600
        await send_telegram_message(f"💚 Monitor alive\n⏱️ ~{hours}h uptime\n🚗 Tracking: {len(current_cars)} cars")
    
    save_known_cars(current_cars)

async def main():
    print("=" * 50)
    print("Car Inventory Monitor v3.3")
    print(f"URL: {WEBSITE_URL}")
    print(f"Interval: {CHECK_INTERVAL_MIN}-{CHECK_INTERVAL_MAX}s")
    print(f"Sanity: min {MIN_CARS_EXPECTED} cars, max {MAX_SOLD_AT_ONCE} sold at once")
    print("=" * 50)
    
    await send_telegram_message(f"🟢 Monitor v3.3 started!\n\n📍 unfallautovd.be\n⏱️ ~{(CHECK_INTERVAL_MIN+CHECK_INTERVAL_MAX)//2}s interval\n🛡️ Sanity checks enabled")
    
    while True:
        try:
            await check_inventory()
        except Exception as e:
            print(f"  Unexpected error: {e}")
        
        await asyncio.sleep(random.randint(CHECK_INTERVAL_MIN, CHECK_INTERVAL_MAX))

if __name__ == "__main__":
    asyncio.run(main())
EOF