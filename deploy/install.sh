#!/bin/sh
# Ubuntu/Debian one-time setup. Run as the user that will trade (e.g. `trader`). Python 3.11+ only; no packages to install.
set -e
sudo timedatectl set-timezone Asia/Kolkata
python3 -c "import zoneinfo, ssl; print('python ok')"
mkdir -p ~/.dhan && chmod 700 ~/.dhan
echo "Now store the credentials:  python3 tools/dhan_token.py setup   (prompts for client id, PIN, TOTP secret)"
echo "Then:  python3 tools/dhan_token.py auto && python3 tools/dhan_token.py verify"
echo "Telegram (optional):  python3 tools/paper_trade.py --telegram-setup <BOT_TOKEN>"
echo "Whitelist this server's IPv4 on web.dhan.co (DhanHQ APIs → Static IP):  curl -4 -s https://api.ipify.org"
echo "Dashboard service:  sudo cp deploy/pivot-dashboard.service /etc/systemd/system/ && sudo systemctl enable --now pivot-dashboard"
echo "Cron:  crontab deploy/crontab.txt   (edit the path if not /opt/pivot_brain)"
