# Raspberry Pi as the always-on server for pivot_brain

Goal: a Raspberry Pi at home that runs the feed, the paper/live trader and the dashboard 24×7, reachable over SSH from anywhere (like a cloud VM), surviving power cuts and your laptop being asleep. Do this document first, then `SETUP_GUIDE.md` from section 2 for the algo itself.

A Pi has no "sleep mode": once it boots it runs until power is cut. The work is making sure it boots unattended, keeps correct time, stays on the network, is reachable from outside, and restarts everything by itself after a power cut.

---

## 1. Hardware

| item | recommendation |
|---|---|
| board | Raspberry Pi 4 (2 GB is enough) or Pi 5 |
| storage | 32 GB+ A2 microSD, or a USB SSD for the Pi 5 (SD cards wear out under daily logs; the project writes little, an A2 card lasts years) |
| power | the official PSU (5 V 3 A for Pi 4, 27 W for Pi 5). Undervoltage causes random reboots |
| network | **Ethernet** to the router. Wi-Fi works but drops more; the feed watchdog recovers, trades can still be missed |
| cooling | a case with a fan or heatsink; the Pi throttles at 80 °C |
| UPS (optional, strongly recommended for live) | a small UPS/power-bank HAT or a router+Pi UPS so a 10-minute power cut does not kill an open live position's monitoring (the broker-held legs still protect it) |

---

## 2. Operating system

1. On the laptop install **Raspberry Pi Imager**. Choose: Raspberry Pi OS **Lite (64-bit)** (Bookworm; ships Python 3.11, no desktop needed).
2. In Imager's settings (the gear / "Edit settings") set: hostname `pivot-pi`, username `trader` with a strong password, **enable SSH with public-key authentication** (paste the laptop's `~/.ssh/id_ed25519.pub`; create one with `ssh-keygen -t ed25519` if missing), Wi-Fi only if you must, locale/timezone **Asia/Kolkata**.
3. Flash, insert, power on with Ethernet connected. Wait two minutes.
4. From the laptop:

```bash
ssh trader@pivot-pi.local            # or the IP from your router's client list
sudo apt update && sudo apt full-upgrade -y && sudo reboot
```

5. After the reboot:

```bash
python3 --version                    # 3.11+
sudo timedatectl set-timezone Asia/Kolkata && timedatectl     # NTP synchronized: yes
sudo raspi-config nonint do_boot_wait 0 >/dev/null 2>&1 || true
```

---

## 3. Make it a server: fixed address, keys only, auto-recovery

**Fixed LAN address.** In the router, reserve the Pi's MAC → a fixed IP (e.g. 192.168.29.50) under DHCP reservation. Simpler and more robust than a static config on the Pi.

**SSH hardening** (keys were installed by Imager; now disable passwords):

```bash
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/; s/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
sudo apt install -y fail2ban && sudo systemctl enable --now fail2ban
```

Test from a second terminal before closing the first: `ssh trader@192.168.29.50` must log in without a password prompt.

**Never let the network sleep** (Wi-Fi power save off; Ethernet has no power save):

```bash
sudo tee /etc/NetworkManager/conf.d/wifi-powersave.conf >/dev/null <<EOF2
[connection]
wifi.powersave = 2
EOF2
sudo systemctl restart NetworkManager
```

**Hardware watchdog** (reboots a hung Pi within 15 s):

```bash
sudo apt install -y watchdog
echo "dtparam=watchdog=on" | sudo tee -a /boot/firmware/config.txt
sudo sed -i 's/^#\?watchdog-device.*/watchdog-device = \/dev\/watchdog/; s/^#\?max-load-1 .*/max-load-1 = 24/' /etc/watchdog.conf
sudo systemctl enable --now watchdog
```

**Unattended security updates** (no automatic reboots during market hours):

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
sudo sed -i 's|^//Unattended-Upgrade::Automatic-Reboot .*|Unattended-Upgrade::Automatic-Reboot "false";|' /etc/apt/apt.conf.d/50unattended-upgrades
```

---

## 4. Remote access from anywhere (the "cloud" part)

Home broadband in India (Jio, Airtel) usually sits behind carrier-grade NAT: port forwarding on the router does **not** give you a reachable address. Use a mesh VPN instead; it needs no open ports and works from mobile data.

**Tailscale (recommended, free for personal use):**

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh                  # prints a login URL; approve it in the browser with your Tailscale account
tailscale ip -4                          # the Pi's permanent 100.x.x.x address
```

Install Tailscale on the laptop (and phone) with the same account. From then on, from anywhere:

```bash
ssh trader@pivot-pi                      # MagicDNS name, or ssh trader@100.x.x.x
ssh -L 8765:127.0.0.1:8765 trader@pivot-pi   # then open http://127.0.0.1:8765 for the dashboard
```

Tailscale starts on boot and reconnects by itself. Alternatives with the same role: ZeroTier, or a Cloudflare Tunnel (`cloudflared`) if you only need the dashboard in a browser. Keep the dashboard bound to 127.0.0.1 and reach it through the tunnel; never expose port 8765 to the internet.

---

## 5. The Dhan static-IP question (read this before going live from the Pi)

Dhan places orders only from the whitelisted IPv4. The Pi's public IPv4 is your home router's, which is **dynamic** on most Indian broadband and may change after a router reboot. Three ways to deal with it, in order of robustness:

1. **Static IP add-on from the ISP** (Jio Fiber and Airtel sell one for a small monthly fee). Whitelist it once; done.
2. **Route only Dhan order traffic through a cheap VPS with a static IP** using Tailscale's exit node: run `tailscale up --advertise-exit-node` on a ₹300–500/month VPS, then on the Pi `sudo tailscale up --exit-node=<vps>` during market hours. The whitelisted IP is then the VPS's and never changes. (Everything, including the feed, goes via the VPS; fine for this volume.)
3. **Live with the dynamic address**: whitelist the current IP (`curl -4 -s https://api.ipify.org`), and add a cron job that checks every 15 minutes whether it changed and messages you on Telegram. Dhan locks the primary IP for 7 days after a change; set the new address as the secondary IP (`POST /v2/ip/setIP` with `ipFlag: SECONDARY`) and switch back later. Orders fail until you do, and the trader logs `DH-905 Invalid IP` and stays flat, so nothing breaks, but trades are missed.

Paper trading and the feed are unaffected by any of this; only order placement is IP-checked.

---

## 6. Install the algo

Follow `SETUP_GUIDE.md` from section 2, with these Pi specifics:

```bash
sudo mkdir -p /opt/pivot_brain && sudo chown trader /opt/pivot_brain
git clone https://github.com/jaysathvara1998/pivot_algo.git /opt/pivot_brain
cd /opt/pivot_brain && sh deploy/install.sh
```

Then sections 3 (Dhan account), 4 (credentials: `python3 tools/dhan_token.py setup`), 5 (Telegram), 6 (data), 7 (preflight), 9 (dashboard service), 10 (cron). Everything in the guide works unchanged on the Pi.

---

## 7. Boot recovery: start the day's processes after a power cut

Cron's 09:00 job starts the session only if the Pi is up at 09:00. Add a reboot hook that re-runs the session start when the Pi comes back during market hours (safe: `run_session.sh` only starts what is not running, and the trader reloads today's state without re-entering old signals):

```bash
crontab -e
# add:
@reboot sleep 60 && cd /opt/pivot_brain && H=$(date +\%H\%M) && [ "$H" -ge 0845 ] && [ "$H" -le 1515 ] && [ "$(date +\%u)" -le 5 ] && LIVE=0 sh deploy/run_session.sh >> logs/cron.log 2>&1
```

Set `LIVE=1` there only if you want the live trader to resume by itself after a reboot.

---

## 8. Keeping an eye on it

- **Telegram** is the primary monitor: trader start/stop, every entry and exit, warnings, the session summary.
- **Dashboard → Overview → Resources**: CPU and RAM of the feed, trader(s) and dashboard, the Pi's total CPU/RAM/load and SoC temperature, with a 15-minute CPU history (also as JSON at `/api/resources`). On a Pi 4/5 the whole algo idles at a few percent of one core and well under 200 MB; sustained high CPU or RAM growing day over day means something is wrong.
- **Health in one command** (run over SSH or from the dashboard's Overview page):

```bash
cd /opt/pivot_brain && pgrep -fa "dhan_feed|paper_trade" ; tail -3 paper/logs/paper_$(date +%Y-%m-%d).log ; vcgencmd measure_temp ; df -h / | tail -1 ; timedatectl | grep synchronized
```

- **Logs** rotate themselves per day under `paper/logs/`; delete logs older than 60 days with a monthly cron: `0 3 1 * * find /opt/pivot_brain/paper/logs -mtime +60 -delete`.
- **Backups**: the ledgers are small; pull them to the laptop weekly: `rsync -a trader@pivot-pi:/opt/pivot_brain/paper/ ~/pivot_backup/paper/`.
- **Time**: `timedatectl` must say `NTP synchronized: yes`; the trader's 15:15 exit and the token jobs depend on the clock.

---

## 9. Checklist before the first live day on the Pi

- [ ] `ssh trader@pivot-pi` works over Tailscale from mobile data (laptop off the home network).
- [ ] `timedatectl` shows Asia/Kolkata and NTP synchronized.
- [ ] `python3 tools/dhan_token.py verify` shows a token generated **from the Pi** after the IP whitelist.
- [ ] `/ip/getIP` reports `PRIMARY_MATCH` and `ordersAllowed: true` for the Pi's egress IP.
- [ ] `python3 tools/live_broker.py` says OK (funds, no stray positions).
- [ ] `python3 tools/live_order_test.py --probe` places and cancels a zero-cost limit order from the Pi.
- [ ] One full paper day ran from cron with the dashboard reachable through the tunnel.
- [ ] Telegram received the session summary.
- [ ] Decide the dynamic-IP strategy (section 5) and, if option 3, install the IP-change check.
