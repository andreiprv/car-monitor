# Mobile.de Car Monitor

Monitors [mobile.de](https://www.mobile.de) search results and sends real-time Telegram notifications when cars are listed, sold, or change price.

## What It Does

You give it one or more mobile.de search URLs (e.g. "BMW M2, automatic, under 60k"). The script periodically scrapes those search pages with a headless browser, compares results against a local SQLite database, and sends you a Telegram message when something changes:

- **New listing** — a car appears that wasn't there before
- **Price drop / increase** — a known car changes price, with old vs new amount
- **Sold / removed** — a car disappears from search results, with how many days it was online
- **Repost detected** — a previously sold car reappears (matched by make, model, year, power, mileage)

On the first run for each search, it silently imports all current listings and sends a single summary with count, price range, and average.

### Notification Example

```
🚗 NEW in BMW M2 (User Search)!

🏷️ BMW M2 Competition
📅 2022  🛣️ 18.500 km  ⚡ 302 kW (411 PS)
⛽ Benzin  ⚙️ Automatik  📍 München

💶 54.900 €
🔗 https://suchen.mobile.de/...
```

### Data Tracked Per Car

| Field              | Source                |
| ------------------ | --------------------- |
| Title, make, model | Parsed from listing   |
| Year, registration | e.g. `12/2021`        |
| Mileage            | e.g. `45.000 km`      |
| Power              | e.g. `302 kW (411 PS)`|
| Fuel, gearbox      | Diesel/Benzin, Auto/Manual |
| Location / dealer  | Seller info block     |
| Price history      | Every change recorded |
| First/last seen    | Timestamps in DB      |

## Requirements

- Python 3.9+
- A Telegram bot token (create one via [@BotFather](https://t.me/BotFather))
- Your Telegram chat ID (get it from [@userinfobot](https://t.me/userinfobot))

## Installation

```bash
git clone https://github.com/andreiprv/car-monitor.git
cd car-monitor

python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium
```

## Configuration

### 1. Environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=123456789
```

### 2. Search URLs

```bash
cp searches.json.example searches.json
```

Edit `searches.json` — add your mobile.de search URLs:

```json
[
  {
    "name": "BMW M2",
    "url": "https://suchen.mobile.de/fahrzeuge/search.html?ms=3500%3B117%3B%3B&..."
  },
  {
    "name": "Porsche Cayman under 60k",
    "url": "https://suchen.mobile.de/fahrzeuge/search.html?..."
  }
]
```

The easiest way: do your search on mobile.de, then copy the full URL from your browser.

### 3. Proxies (optional)

If your IP gets blocked, create `proxies.txt` with one proxy per line:

```
http://user:pass@host:port
http://user:pass@host:port
```

The script picks a random proxy for each run.

### 4. Cookies (optional, recommended)

mobile.de has aggressive anti-bot protection. Exporting your browser cookies helps bypass it:

1. Open mobile.de in your browser and complete a search (solve any captcha)
2. Use a browser extension like "Cookie-Editor" to export cookies as JSON
3. Save to `cookies.json` in the project folder

## Usage

**Continuous mode** — runs in a loop, checking every 5-10 minutes:

```bash
python mobile_monitor.py
```

**Single run** — check once and exit (useful for cron):

```bash
python mobile_monitor.py --once
```

### Files created at runtime

| File              | Purpose                          |
| ----------------- | -------------------------------- |
| `cars.db`         | SQLite database with all data    |
| `monitor.log`     | Application log                  |
| `debug_empty.html`| Page dump when no cars are found |

All runtime files are gitignored.

## VPS Deployment

See [DEPLOY.md](DEPLOY.md) for a full guide, but the short version:

```bash
# On your server
scp mobile_monitor.py requirements.txt setup_vps.sh .env searches.json you@server:~/car-monitor/
ssh you@server
cd ~/car-monitor
chmod +x setup_vps.sh && ./setup_vps.sh
```

Schedule with cron to run every ~2 hours:

```cron
0 8,10,12,14,16,18,20,22 * * * cd /root/car-monitor && venv/bin/python mobile_monitor.py --once >> monitor.log 2>&1
```

## Project Structure

```
car-monitor/
├── mobile_monitor.py       # Main script
├── requirements.txt        # Python dependencies
├── setup_vps.sh            # Ubuntu/VPS setup script
├── .env.example            # Template for credentials
├── searches.json.example   # Template for search config
├── DEPLOY.md               # VPS deployment guide
├── script.md               # Older single-site monitor script (reference)
├── .env                    # Your credentials (gitignored)
├── searches.json           # Your search URLs (gitignored)
├── proxies.txt             # Your proxies (gitignored)
├── cookies.json            # Your cookies (gitignored)
├── cars.db                 # SQLite database (gitignored)
└── monitor.log             # Log file (gitignored)
```

## Troubleshooting

**"No cars found" on every run**
- Check `debug_empty.html` — if it contains a captcha page, you need cookies or a proxy
- Try exporting fresh cookies from your browser

**No Telegram messages**
- Verify your bot token and chat ID in `.env`
- Make sure you've started a conversation with your bot (send it `/start`)

**Script exits immediately**
- Check `monitor.log` for errors
- Make sure `searches.json` exists and is valid JSON
