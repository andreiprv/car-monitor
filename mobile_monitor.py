import asyncio
import json
import os
import re
import random
import sqlite3
import logging
import argparse
import hashlib
from datetime import datetime
from playwright.async_api import async_playwright
import httpx
from dotenv import load_dotenv

load_dotenv()

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("monitor.log"),
        logging.StreamHandler()
    ]
)

# Configuration
CONFIG_FILE = "searches.json"
DB_FILE = "cars.db"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
]

def init_db():
    """Initialize SQLite database with required tables"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS searches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE,
                    url TEXT
                )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS listings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_id TEXT UNIQUE,
                    car_fingerprint TEXT,
                    search_id INTEGER,
                    title TEXT,
                    make TEXT,
                    model TEXT,
                    year INTEGER,
                    mileage_km INTEGER,
                    first_registration TEXT,
                    power TEXT,
                    fuel_type TEXT,
                    gearbox TEXT,
                    location TEXT,
                    link TEXT,
                    image_url TEXT,
                    price INTEGER,
                    ad_created TEXT,
                    first_seen TEXT,
                    last_seen TEXT,
                    is_sold BOOLEAN DEFAULT 0,
                    FOREIGN KEY(search_id) REFERENCES searches(id)
                )''')
                
    c.execute('''CREATE TABLE IF NOT EXISTS prices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    listing_id INTEGER,
                    price INTEGER,
                    date_recorded TEXT,
                    FOREIGN KEY(listing_id) REFERENCES listings(id)
                )''')
    
    # Migration: add new columns if DB already exists from older version
    existing_cols = [row[1] for row in c.execute("PRAGMA table_info(listings)").fetchall()]
    new_cols = {
        'car_fingerprint': 'TEXT',
        'make': 'TEXT', 'model': 'TEXT', 'year': 'INTEGER',
        'mileage_km': 'INTEGER', 'first_registration': 'TEXT',
        'power': 'TEXT', 'fuel_type': 'TEXT', 'gearbox': 'TEXT',
        'location': 'TEXT', 'ad_created': 'TEXT'
    }
    for col, col_type in new_cols.items():
        if col not in existing_cols:
            c.execute(f"ALTER TABLE listings ADD COLUMN {col} {col_type}")
            logging.info(f"Migrated DB: added column '{col}'")
    
    conn.commit()
    conn.close()

def generate_fingerprint(car):
    """Generate a unique fingerprint for the physical car (not the ad).
    Uses: make + model + year + power (kW) + exact mileage.
    """
    make = (car.get('make') or '').strip().lower()
    model = (car.get('model') or '').strip().lower()
    year = str(car.get('year') or 0)
    # Extract just the kW number from power string like '302 kW (411 PS)'
    power_str = car.get('power') or ''
    kw_match = re.match(r'(\d+)', power_str)
    power_kw = kw_match.group(1) if kw_match else '0'
    mileage = str(car.get('mileage_km') or 0)
    
    raw = f"{make}|{model}|{year}|{power_kw}|{mileage}"
    fingerprint = hashlib.md5(raw.encode()).hexdigest()[:12]
    logging.debug(f"Fingerprint: {raw} -> {fingerprint}")
    return fingerprint

def load_searches():
    """Load searches from JSON and sync to DB"""
    if not os.path.exists(CONFIG_FILE):
        logging.error(f"{CONFIG_FILE} not found!")
        return []
        
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config_searches = json.load(f)
        
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    active_searches = []
    
    for s in config_searches:
        c.execute("INSERT OR IGNORE INTO searches (name, url) VALUES (?, ?)", (s['name'], s['url']))
        c.execute("UPDATE searches SET url = ? WHERE name = ?", (s['url'], s['name']))
        
        c.execute("SELECT id FROM searches WHERE name = ?", (s['name'],))
        db_id = c.fetchone()[0]
        active_searches.append({
            'id': db_id,
            'name': s['name'],
            'url': s['url']
        })
        
    conn.commit()
    conn.close()
    return active_searches

async def send_telegram(text, photo_url=None):
    """Send notification to Telegram"""
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if photo_url:
                await client.post(f"{base_url}/sendPhoto", data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "photo": photo_url,
                    "caption": text
                })
            else:
                await client.post(f"{base_url}/sendMessage", data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text
                })
        except Exception as e:
            logging.error(f"Telegram error: {e}")


def load_proxies():
    """Load proxies from proxies.txt if available.
    Format: http://user:pass@host:port (one per line)
    """
    if os.path.exists("proxies.txt"):
        with open("proxies.txt", "r") as f:
            proxies = [line.strip() for line in f if line.strip()]
        return proxies
    return []

def parse_proxy(proxy_url):
    """Parse proxy URL into Playwright proxy dict.
    Input:  http://user:pass@host:port
    Output: {'server': 'http://host:port', 'username': 'user', 'password': 'pass'}
    """
    from urllib.parse import urlparse
    parsed = urlparse(proxy_url)
    proxy_dict = {
        "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    }
    if parsed.username:
        proxy_dict["username"] = parsed.username
    if parsed.password:
        proxy_dict["password"] = parsed.password
    return proxy_dict

async def fetch_cars(url):
    """Scrape mobile.de using Playwright"""
    proxies = load_proxies()
    proxy = None
    if proxies:
        proxy_str = random.choice(proxies)
        proxy = parse_proxy(proxy_str)
        logging.info(f"Using proxy: {proxy['server']}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, 
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
            ],
            proxy=proxy
        )
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={'width': 1920, 'height': 1080},
            device_scale_factor=1,
            locale='de-DE',
            timezone_id='Europe/Berlin',
        )
        page = await context.new_page()
        
        # Apply stealth
        await page.add_init_script("""
            // Hide webdriver
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            
            // Fake plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            
            // Fake languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['de-DE', 'de', 'en-US', 'en']
            });
            
            // Remove chrome automation indicators
            window.chrome = { runtime: {} };
        """)
        
        # Load Cookies if available
        if os.path.exists("cookies.json"):
            try:
                with open("cookies.json", 'r') as f:
                    cookies = json.load(f)
                    await context.add_cookies(cookies)
                logging.info(f"Loaded {len(cookies)} cookies from cookies.json")
            except Exception as e:
                logging.error(f"Failed to load cookies: {e}")

        try:
            # Step 1: Visit homepage first to get cookies/pass initial challenge
            logging.info("Visiting mobile.de homepage first...")
            await page.goto("https://www.mobile.de", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(2, 4))
            
            # Try to accept cookie consent
            for selector in [
                'button[class*="mde-consent-accept"]',
                'button[id*="accept"]',
                'button:has-text("Akzeptieren")',
                'button:has-text("Alle akzeptieren")',
                'button:has-text("Accept")',
            ]:
                try:
                    await page.click(selector, timeout=2000)
                    logging.info("Cookie consent accepted")
                    await asyncio.sleep(1)
                    break
                except:
                    continue
            
            # Step 2: Navigate to actual search URL
            logging.info(f"Navigating to {url[:60]}...")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            # Random waits and mouse moves
            await asyncio.sleep(random.uniform(2, 5))
            await page.mouse.move(random.randint(100, 500), random.randint(100, 500))
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await asyncio.sleep(random.uniform(1, 3))
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(random.uniform(2, 4))
            
            # Extract Data with extended fields
            cars = await page.evaluate(r"""
                () => {
                    const items = document.querySelectorAll(
                        'a.link--muted.no--text--decoration.result-item, a.list-entry'
                    );
                    const results = [];
                    
                    items.forEach(item => {
                        // === TITLE ===
                        const titleEl = item.querySelector('.h3, h3, .headline-block');
                        const title = titleEl ? titleEl.innerText.trim() : 'Unknown Car';
                        
                        // === MAKE & MODEL from title ===
                        // Title is usually "BMW M2 Competition" or "Mercedes-Benz C 300"
                        const titleParts = title.split(/\s+/);
                        const make = titleParts[0] || '';
                        const model = titleParts.slice(1).join(' ') || '';
                        
                        // === PRICE ===
                        const priceEl = item.querySelector(
                            '.price-block, .h3[data-testid="price-label"], .pricePrimaryCountryOfSale'
                        );
                        let priceVal = 0;
                        if (priceEl) {
                            let clean = priceEl.innerText.replace(/[^0-9]/g, '');
                            priceVal = parseInt(clean) || 0;
                        }
                        
                        // === IMAGE ===
                        const img = item.querySelector('img');
                        const imgUrl = img ? (img.src || img.getAttribute('data-src')) : null;
                        
                        // === ID & LINK ===
                        const link = item.href;
                        let id = item.getAttribute('data-listing-id') || item.getAttribute('data-ad-id');
                        if (!id && link) {
                            const match = link.match(/id=(\d+)/);
                            if (match) id = match[1];
                        }
                        
                        // === VEHICLE DETAILS ===
                        // mobile.de shows details like: "12/2021 | 45.000 km | 302 kW (411 PS) | Diesel"
                        const detailEls = item.querySelectorAll(
                            '.vehicle-data--ad-with-financing-  , .rbt-regMilPow, .vehicle-information, [class*="vehicle-data"]'
                        );
                        let detailText = '';
                        detailEls.forEach(el => { detailText += ' ' + el.innerText; });
                        
                        // Also grab all text from the item for fallback parsing
                        const allText = item.innerText || '';
                        const searchText = detailText || allText;
                        
                        // Registration date (e.g. "12/2021" or "01/2020")
                        let firstRegistration = '';
                        let year = 0;
                        const regMatch = searchText.match(/(\d{2}\/\d{4})/);
                        if (regMatch) {
                            firstRegistration = regMatch[1];
                            year = parseInt(regMatch[1].split('/')[1]) || 0;
                        }
                        
                        // Mileage (e.g. "45.000 km" or "120.500 km")
                        let mileageKm = 0;
                        const kmMatch = searchText.match(/([\d.]+)\s*km/i);
                        if (kmMatch) {
                            mileageKm = parseInt(kmMatch[1].replace(/\./g, '')) || 0;
                        }
                        
                        // Power (e.g. "302 kW (411 PS)" or "225 kW")
                        let power = '';
                        const pwMatch = searchText.match(/(\d+)\s*kW\s*(\(\d+\s*PS\))?/i);
                        if (pwMatch) {
                            power = pwMatch[0].trim();
                        }
                        
                        // Fuel type
                        let fuelType = '';
                        const fuelLower = searchText.toLowerCase();
                        if (fuelLower.includes('diesel')) fuelType = 'Diesel';
                        else if (fuelLower.includes('benzin')) fuelType = 'Benzin';
                        else if (fuelLower.includes('elektro') || fuelLower.includes('electric')) fuelType = 'Elektro';
                        else if (fuelLower.includes('hybrid')) fuelType = 'Hybrid';
                        
                        // Gearbox
                        let gearbox = '';
                        if (fuelLower.includes('automatik') || fuelLower.includes('automatic')) gearbox = 'Automatik';
                        else if (fuelLower.includes('schaltgetriebe') || fuelLower.includes('manual')) gearbox = 'Schaltgetriebe';
                        
                        // Location / Dealer
                        let location = '';
                        const locEl = item.querySelector(
                            '.dealer-info, [class*="seller"], [class*="location"]'
                        );
                        if (locEl) location = locEl.innerText.trim();
                        
                        // Ad creation date (mobile.de sometimes shows "Online seit: ...")
                        let adCreated = '';
                        const adDateMatch = searchText.match(/(?:Online seit|Eingestellt am)[:\s]*(\d{2}\.\d{2}\.\d{4})/i);
                        if (adDateMatch) adCreated = adDateMatch[1];
                        
                        results.push({
                            external_id: id,
                            title: title,
                            make: make,
                            model: model,
                            year: year,
                            mileage_km: mileageKm,
                            first_registration: firstRegistration,
                            power: power,
                            fuel_type: fuelType,
                            gearbox: gearbox,
                            location: location,
                            ad_created: adCreated,
                            price: priceVal,
                            link: link,
                            image_url: imgUrl
                        });
                    });
                    
                    return results;
                }
            """)
            
            if not cars:
                logging.warning("No cars parsed. Dumping HTML.")
                try:
                    content = await page.content()
                    with open("debug_empty.html", "w", encoding="utf-8") as f:
                        f.write(content)
                except:
                    pass

            return cars
            
        except Exception as e:
            logging.error(f"Scraping error: {e}")
            return []
        finally:
            await browser.close()


def format_car_notification(car, search_name, event_type, old_price=None, days_online=None, is_repost=False):
    """Format a rich Telegram notification message"""
    lines = []
    
    if event_type == "new":
        if is_repost:
            lines.append(f"♻️ REPOST detected in {search_name}!")
        else:
            lines.append(f"🚗 NEW in {search_name}!")
    elif event_type == "price_change":
        diff = car['price'] - old_price
        icon = "📈" if diff > 0 else "📉"
        lines.append(f"{icon} PRICE CHANGE in {search_name}!")
    elif event_type == "relisted":
        lines.append(f"♻️ RE-LISTED in {search_name}!")
    elif event_type == "sold":
        lines.append(f"💰 SOLD / REMOVED in {search_name}")
    
    lines.append("")
    lines.append(f"🏷️ {car.get('title', '?')}")
    
    details = []
    if car.get('year'):
        details.append(f"📅 {car['year']}")
    if car.get('mileage_km'):
        km = car['mileage_km']
        details.append(f"🛣️ {km:,} km".replace(",", "."))
    if car.get('power'):
        details.append(f"⚡ {car['power']}")
    if car.get('fuel_type'):
        details.append(f"⛽ {car['fuel_type']}")
    if car.get('gearbox'):
        details.append(f"⚙️ {car['gearbox']}")
    if car.get('location'):
        details.append(f"📍 {car['location']}")
    
    if details:
        lines.append("")
        lines.extend(details)
    
    lines.append("")
    if event_type == "price_change" and old_price is not None:
        diff = car['price'] - old_price
        lines.append(f"💶 {old_price:,} € → {car['price']:,} € ({diff:+,} €)".replace(",", "."))
    else:
        lines.append(f"💶 {car.get('price', 0):,} €".replace(",", "."))
    
    if days_online is not None:
        lines.append(f"⏱️ Was online for {days_online} days")
    
    lines.append(f"🔗 {car.get('link', '')}")
    
    return "\n".join(lines)


async def process_search(search):
    """Process a single search criteria"""
    logging.info(f"Checking: {search['name']}")
    
    current_cars = await fetch_cars(search['url'])
    if not current_cars:
        logging.warning("No cars found (or blocking/error).")
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now().isoformat()
    
    # Get known cars for this search from DB
    c.execute("SELECT external_id, price, is_sold, id, car_fingerprint FROM listings WHERE search_id = ?", (search['id'],))
    known_map = {row[0]: {'price': row[1], 'is_sold': row[2], 'db_id': row[3], 'fingerprint': row[4]} for row in c.fetchall()}
    
    # === FIRST RUN DETECTION ===
    # If no existing listings for this search, this is a first run / new search
    is_first_run = len(known_map) == 0
    
    if is_first_run:
        logging.info(f"First run for '{search['name']}' — importing {len(current_cars)} cars silently.")
        prices = [car.get('price', 0) for car in current_cars if car.get('price', 0) > 0]
        
        for car in current_cars:
            eid = car.get('external_id')
            if not eid:
                continue
            price = car.get('price', 0)
            fingerprint = generate_fingerprint(car)
            
            c.execute('''INSERT OR IGNORE INTO listings 
                         (external_id, car_fingerprint, search_id, title, make, model, year, mileage_km,
                          first_registration, power, fuel_type, gearbox, location,
                          link, image_url, price, ad_created, first_seen, last_seen, is_sold)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)''', 
                         (eid, fingerprint, search['id'], car['title'], car.get('make'), car.get('model'),
                          car.get('year'), car.get('mileage_km'), car.get('first_registration'),
                          car.get('power'), car.get('fuel_type'), car.get('gearbox'),
                          car.get('location'), car['link'], car.get('image_url'),
                          price, car.get('ad_created'), now, now))
            listing_id = c.lastrowid
            if listing_id:
                c.execute("INSERT INTO prices (listing_id, price, date_recorded) VALUES (?, ?, ?)",
                           (listing_id, price, now))
        
        # Send ONE summary message
        avg_price = int(sum(prices) / len(prices)) if prices else 0
        min_price = min(prices) if prices else 0
        max_price = max(prices) if prices else 0
        summary = (
            f"📊 Initial scan: {search['name']}\n\n"
            f"🚗 {len(current_cars)} cars found\n"
            f"💶 Price range: {min_price:,} € – {max_price:,} €\n"
            f"📈 Average: {avg_price:,} €"
        ).replace(",", ".")
        await send_telegram(summary)
        
        conn.commit()
        conn.close()
        return
    
    # === SUBSEQUENT RUNS — detailed notifications ===
    
    # Build fingerprint index of all sold cars (for repost detection)
    c.execute("""SELECT car_fingerprint, id, title, first_seen, last_seen, price 
                 FROM listings WHERE search_id = ? AND is_sold = 1 AND car_fingerprint IS NOT NULL""", (search['id'],))
    sold_fingerprints = {row[0]: {'db_id': row[1], 'title': row[2], 'first_seen': row[3], 'last_seen': row[4], 'price': row[5]} for row in c.fetchall()}
    
    found_external_ids = set()
    
    for car in current_cars:
        eid = car.get('external_id')
        if not eid:
            continue
            
        found_external_ids.add(eid)
        price = car.get('price', 0)
        fingerprint = generate_fingerprint(car)
        
        if eid not in known_map:
            # === NEW CAR or REPOST ===
            is_repost = fingerprint in sold_fingerprints
            
            if is_repost:
                # Repost: reactivate old sold listing with new ad details
                prev = sold_fingerprints[fingerprint]
                old_db_id = prev['db_id']
                logging.info(f"Repost detected: {car['title']} (was: {prev['title']})")
                
                c.execute("""UPDATE listings SET 
                             external_id = ?, is_sold = 0, last_seen = ?,
                             title = ?, link = ?, image_url = ?, price = ?,
                             mileage_km = COALESCE(?, mileage_km),
                             location = COALESCE(?, location),
                             ad_created = COALESCE(?, ad_created)
                             WHERE id = ?""",
                          (eid, now, car['title'], car['link'], car.get('image_url'),
                           price, car.get('mileage_km'), car.get('location'),
                           car.get('ad_created'), old_db_id))
                
                # Record price if changed
                if prev['price'] != price and price > 0:
                    c.execute("INSERT INTO prices (listing_id, price, date_recorded) VALUES (?, ?, ?)",
                               (old_db_id, price, now))
                
                # Update known_map so it's not marked as sold later
                known_map[eid] = {'price': price, 'is_sold': 0, 'db_id': old_db_id, 'fingerprint': fingerprint}
                
                msg = format_car_notification(car, search['name'], "new", is_repost=True)
                await send_telegram(msg, car.get('image_url'))
            else:
                # Genuinely new car
                logging.info(f"New car: {car['title']}")
                c.execute('''INSERT INTO listings 
                             (external_id, car_fingerprint, search_id, title, make, model, year, mileage_km,
                              first_registration, power, fuel_type, gearbox, location,
                              link, image_url, price, ad_created, first_seen, last_seen, is_sold)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)''', 
                             (eid, fingerprint, search['id'], car['title'], car.get('make'), car.get('model'),
                              car.get('year'), car.get('mileage_km'), car.get('first_registration'),
                              car.get('power'), car.get('fuel_type'), car.get('gearbox'),
                              car.get('location'), car['link'], car.get('image_url'),
                              price, car.get('ad_created'), now, now))
                listing_id = c.lastrowid
                
                c.execute("INSERT INTO prices (listing_id, price, date_recorded) VALUES (?, ?, ?)",
                           (listing_id, price, now))
                
                msg = format_car_notification(car, search['name'], "new")
                await send_telegram(msg, car.get('image_url'))
            
        else:
            # === KNOWN CAR ===
            entry = known_map[eid]
            db_id = entry['db_id']
            
            # Update last_seen, fingerprint, and refreshed details
            c.execute("""UPDATE listings SET 
                         last_seen = ?, is_sold = 0, car_fingerprint = ?,
                         mileage_km = COALESCE(?, mileage_km),
                         power = COALESCE(?, power),
                         fuel_type = COALESCE(?, fuel_type),
                         gearbox = COALESCE(?, gearbox),
                         location = COALESCE(?, location)
                         WHERE id = ?""",
                      (now, fingerprint, car.get('mileage_km'), car.get('power'),
                       car.get('fuel_type'), car.get('gearbox'),
                       car.get('location'), db_id))
            
            # Check for Re-list (same ad ID reappeared)
            if entry['is_sold']:
                msg = format_car_notification(car, search['name'], "relisted")
                await send_telegram(msg, car.get('image_url'))
            
            # Check Price Change
            if entry['price'] != price and price > 0:
                c.execute("UPDATE listings SET price = ? WHERE id = ?", (price, db_id))
                c.execute("INSERT INTO prices (listing_id, price, date_recorded) VALUES (?, ?, ?)",
                           (db_id, price, now))
                msg = format_car_notification(car, search['name'], "price_change", old_price=entry['price'])
                await send_telegram(msg, car.get('image_url'))

    # === MARK SOLD ===
    for eid, entry in known_map.items():
        if eid not in found_external_ids and not entry['is_sold']:
            logging.info(f"Marking as sold: {eid}")
            c.execute("UPDATE listings SET is_sold = 1 WHERE id = ?", (entry['db_id'],))
            
            c.execute("""SELECT title, link, image_url, make, model, year, 
                                mileage_km, power, fuel_type, gearbox, location, price,
                                first_seen, last_seen
                         FROM listings WHERE id = ?""", (entry['db_id'],))
            row = c.fetchone()
            if row:
                sold_car = {
                    'title': row[0], 'link': row[1], 'image_url': row[2],
                    'make': row[3], 'model': row[4], 'year': row[5],
                    'mileage_km': row[6], 'power': row[7], 'fuel_type': row[8],
                    'gearbox': row[9], 'location': row[10], 'price': row[11]
                }
                # Calculate days online
                days_online = None
                try:
                    first_dt = datetime.fromisoformat(row[12])
                    last_dt = datetime.fromisoformat(row[13])
                    days_online = (last_dt - first_dt).days
                except:
                    pass
                msg = format_car_notification(sold_car, search['name'], "sold", days_online=days_online)
                await send_telegram(msg, sold_car.get('image_url'))

    conn.commit()
    conn.close()

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    init_db()
    logging.info("Car Monitor Started")
    if not args.once:
        await send_telegram("🤖 Car Monitor v2 (SQLite) Started")
    
    while True:
        searches = load_searches()
        if not searches:
            logging.warning("No searches found in searches.json")
        
        for search in searches:
            await process_search(search)
            await asyncio.sleep(random.uniform(5, 10))
            
        if args.once:
            logging.info("Run once completed.")
            break
            
        wait_time = random.uniform(300, 600)
        logging.info(f"Sleeping for {int(wait_time)}s...")
        await asyncio.sleep(wait_time)

if __name__ == "__main__":
    asyncio.run(main())
