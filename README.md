# Mobile.de Monitor

A Python script to monitor mobile.de search results, track prices, and notify via Telegram.

## Features
- **SQLite Database**: Tracks every car seen, price history, and sold status.
- **Multiple Searches**: Monitor multiple search URLs at once.
- **Price Tracking**: Notifies when a price changes.
- **Re-list Detection**: Notifies if a "sold" car reappears.
- **Telegram Notifications**: Sends photos and details.

## Setup

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    playwright install chromium
    ```

2.  **Environment Variables**:
    ```bash
    cp .env.example .env
    ```
    Edit `.env` and fill in your Telegram bot token and chat ID.

3.  **Search Configuration**:
    ```bash
    cp searches.json.example searches.json
    ```
    Edit `searches.json` to add your mobile.de search URLs.

4.  **(Optional)** Create `proxies.txt` with one proxy URL per line to rotate IPs.

5.  **Run**:
    ```bash
    python mobile_monitor.py
    ```

## Deployment
For running this on a VPS (Ubuntu) with a schedule:
- See **[DEPLOY.md](DEPLOY.md)** for a step-by-step guide.


## Handling "Access Denied" / "No cars found"
`mobile.de` has strong anti-bot protection. If you see "No cars found" warnings or "Access Denied" in debug files:

1.  **Export Cookies**:
    - Install a browser extension like "Cookie-Editor" or "EditThisCookie".
    - Go to `mobile.de` in your browser and complete a search (solve any captchas).
    - Open the extension and click "Export" (JSON format).
    - Save the content to a file named `cookies.json` in the same folder as the script.
    - Restart the script. It will load these cookies and look like a real user.

2.  **Use a Proxy**:
    - If your IP is blocked, you may need a residential proxy.
