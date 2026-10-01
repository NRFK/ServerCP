#!/usr/bin/env bash
# Connect a dashboard-managed Cloudflare Tunnel to an installed ServerCP host.
set -euo pipefail
if [[ ${EUID} -ne 0 || ! -t 0 ]]; then
  echo 'Run sudo bash install-tunnel.sh from an interactive terminal.' >&2
  exit 1
fi
if [[ ! -f /etc/servercp/servercp.env ]]; then
  echo 'Install ServerCP first.' >&2
  exit 1
fi
if [[ -f /etc/systemd/system/servercp-tunnel.service ]]; then
  echo 'A ServerCP tunnel already exists. Rotate its token using the README instructions.' >&2
  exit 1
fi
if ! command -v cloudflared >/dev/null; then
  echo 'Install cloudflared using Cloudflare’s official package instructions, then run this helper again.' >&2
  echo 'https://developers.cloudflare.com/tunnel/downloads/' >&2
  exit 1
fi
if ! cloudflared tunnel run --help 2>&1 | grep -q -- '--token-file'; then
  echo 'Update cloudflared: this helper requires support for --token-file (2025.4.0+).' >&2
  exit 1
fi
read -r -p 'Public panel hostname (for example panel.example.com): ' PANEL_HOSTNAME
PANEL_HOSTNAME=${PANEL_HOSTNAME,,}
if [[ ! "$PANEL_HOSTNAME" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ || "$PANEL_HOSTNAME" != *.* ]]; then
  echo 'Enter a hostname without https:// or a path.' >&2
  exit 1
fi
read -r -p 'Loopback metrics port [20241]: ' METRICS_PORT
METRICS_PORT=${METRICS_PORT:-20241}
if [[ ! "$METRICS_PORT" =~ ^[0-9]{1,5}$ ]] || (( 10#$METRICS_PORT < 1 || 10#$METRICS_PORT > 65535 )); then
  echo 'Invalid metrics port.' >&2
  exit 1
fi
METRICS_PORT=$((10#$METRICS_PORT))
python3 - "$METRICS_PORT" <<'PY'
import socket,sys
with socket.socket() as s:
    try:
        s.bind(('127.0.0.1',int(sys.argv[1])))
    except OSError:
        raise SystemExit('Metrics port is occupied. Choose another port.')
PY
read -r -s -p 'Tunnel token from the Cloudflare dashboard: ' TUNNEL_SECRET
echo
if [[ ! "$TUNNEL_SECRET" =~ ^[a-zA-Z0-9_+/=-]{40,}$ ]]; then
  unset TUNNEL_SECRET
  echo 'The tunnel token format is invalid.' >&2
  exit 1
fi
id servercp-tunnel >/dev/null 2>&1 || useradd --system --home /nonexistent --shell /usr/sbin/nologin servercp-tunnel
install -d -m 700 -o servercp-tunnel -g servercp-tunnel /etc/servercp/tunnel
umask 077
printf '%s' "$TUNNEL_SECRET" > /etc/servercp/tunnel/token
unset TUNNEL_SECRET
chown servercp-tunnel:servercp-tunnel /etc/servercp/tunnel/token
chmod 600 /etc/servercp/tunnel/token
CLOUDFLARED_BIN=$(command -v cloudflared)
cat > /etc/systemd/system/servercp-tunnel.service <<EOF
[Unit]
Description=ServerCP Cloudflare Tunnel
After=network-online.target servercp.service
Wants=network-online.target

[Service]
Type=simple
User=servercp-tunnel
Group=servercp-tunnel
ExecStart=$CLOUDFLARED_BIN tunnel --no-autoupdate --metrics 127.0.0.1:$METRICS_PORT run --token-file /etc/servercp/tunnel/token
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict

[Install]
WantedBy=multi-user.target
EOF
cp /etc/servercp/servercp.env /etc/servercp/servercp.env.before-tunnel
python3 - "$PANEL_HOSTNAME" <<'PY'
from pathlib import Path
import sys
p=Path('/etc/servercp/servercp.env')
lines=[line for line in p.read_text().splitlines() if not line.startswith('PANEL_ORIGIN=')]
lines.append('PANEL_ORIGIN=https://'+sys.argv[1])
p.write_text('\n'.join(lines)+'\n')
PY
chmod 600 /etc/servercp/servercp.env /etc/servercp/servercp.env.before-tunnel
systemctl daemon-reload
systemctl restart servercp
systemctl enable --now servercp-tunnel
echo "Connector started. Configure the tunnel route: https://$PANEL_HOSTNAME -> http://127.0.0.1:8090"
echo "Use metrics port $METRICS_PORT on ServerCP’s Cloudflare Tunnel page."
echo 'The public route must be added in Cloudflare before the panel hostname will work.'
