#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then
  echo 'Run this installer using sudo bash install.sh.' >&2
  exit 1
fi
source /etc/os-release
case "${ID}" in ubuntu|debian) ;; *) echo 'This installer supports Ubuntu and Debian.' >&2; exit 1;; esac
if [[ ! -t 0 ]]; then
  echo 'Run from an interactive terminal to create the administrator account.' >&2
  exit 1
fi
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEST=/opt/servercp
if [[ -e "$DEST/app.py" ]]; then
  echo 'ServerCP is already installed. Follow the update instructions in README.md.' >&2
  exit 1
fi
apt-get update
apt-get install -y python3 python3-venv openssh-client
python3 -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10 or later is required"'
id servercp >/dev/null 2>&1 || useradd --system --home /var/lib/servercp --shell /usr/sbin/nologin servercp
install -d -m 755 "$DEST"
install -d -m 700 -o servercp -g servercp /var/lib/servercp
cp "$SOURCE/app.py" "$SOURCE/collector.py" "$SOURCE/tunnel_collector.py" "$SOURCE/requirements.txt" "$DEST/"
cp -R "$SOURCE/static" "$DEST/"
cp "$SOURCE/install-tunnel.sh" "$DEST/"
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -r "$DEST/requirements.txt"
chown -R root:root "$DEST"
chmod -R go-w "$DEST"
PANEL_DATA=/var/lib/servercp runuser -u servercp -- "$DEST/.venv/bin/python" "$DEST/app.py" setup
install -d -m 755 /etc/servercp
cat > /etc/servercp/servercp.env <<'EOF'
PANEL_DATA=/var/lib/servercp
PANEL_ORIGIN=http://localhost:8090
EOF
chmod 600 /etc/servercp/servercp.env
install -m 644 "$SOURCE/deploy/servercp.service" /etc/systemd/system/servercp.service
systemctl daemon-reload
systemctl enable --now servercp
echo 'ServerCP is listening on 127.0.0.1:8090.'
echo 'From your computer: ssh -L 8090:127.0.0.1:8090 YOUR_USER@YOUR_SERVER'
echo 'Then open http://localhost:8090. HTTPS reverse-proxy setup is in README.md.'
