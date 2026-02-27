#!/bin/bash

# Mobile.de Monitor - VPS Setup Script
# Run this on your Ubuntu server to install all dependencies.

echo ">>> Updating System..."
sudo apt update && sudo apt upgrade -y

echo ">>> Installing Python 3 & Pip..."
sudo apt install -y python3 python3-pip python3-venv

echo ">>> Setting up Virtual Environment..."
python3 -m venv venv
source venv/bin/activate

echo ">>> Installing Python Dependencies..."
pip install -r requirements.txt

echo ">>> Installing Playwright Browsers..."
playwright install chromium
playwright install-deps

echo ">>> Setup Complete!"
echo ""
echo "IMPORTANT: Create your .env file before running:"
echo "  cp .env.example .env"
echo "  nano .env  # Add your Telegram bot token and chat ID"
echo ""
echo "To run the monitor manually:"
echo "  source venv/bin/activate"
echo "  python3 mobile_monitor.py"
echo ""
echo "To set up the cron job (schedule), see DEPLOY.md"
