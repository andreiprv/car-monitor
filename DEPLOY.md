# Mobile.de Monitor - Deployment Guide

This guide explains how to deploy the monitor on your Ubuntu VPS and schedule it to run 10 times a day.

## 1. Connect to VPS
Connect to your server via SSH:
```bash
ssh root@<YOUR_SERVER_IP>
```

## 2. Upload Files
You need to transfer the following files to your server (e.g., using `scp` or FileZilla):
- `mobile_monitor.py`
- `requirements.txt`
- `setup_vps.sh`
- `.env` (contains your Telegram credentials)
- `searches.json` (your search URLs)
- `proxies.txt` (Optional, create this with `user:pass@ip:port` per line)
- `cookies.json` (Optional, HIGHLY RECOMMENDED if getting blocked)

Example SCP command (run from your local PC):
```bash
scp mobile_monitor.py requirements.txt setup_vps.sh .env searches.json root@<YOUR_SERVER_IP>:~/car-monitor/
```

## 3. Install & Setup
On the VPS, navigate to the folder and run the setup script:
```bash
cd ~/car-monitor
chmod +x setup_vps.sh
./setup_vps.sh
```

## 4. Test Run
Activate the environment and run the script once to verify:
```bash
source venv/bin/activate
python mobile_monitor.py --once
```

## 5. Schedule (Cron)
To run the script 10 times a day (roughly every 2.5 hours), edit the crontab:

```bash
crontab -e
```

Add this line to the bottom:
```cron
# Run every 2 hours and 24 minutes (approx 10 times/day)
*/144 * * * * cd /root/car-monitor && venv/bin/python mobile_monitor.py --once >> monitor.log 2>&1
```

Or, for specific hours (e.g., 8am to 10pm, every 1.5 hours):
```cron
0 8,10,12,14,16,18,20,22 * * * cd /root/car-monitor && venv/bin/python mobile_monitor.py --once >> monitor.log 2>&1
```

## 6. managing Proxies
Create a `proxies.txt` file in the `car-monitor` directory.
Format (one per line):
```
http://user:pass@1.2.3.4:8080
http://user:pass@5.6.7.8:8080
```
The script will pick a random one for each run.
