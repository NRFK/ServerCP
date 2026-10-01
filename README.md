# ServerCP

A self-hosted Linux server control panel with a modern light/dark interface, live metrics, services, logs and an interactive SSH terminal. Initial target: Ubuntu 22.04/24.04 and Debian 12/13 with Python 3.10+ and systemd. These distributions still need installation testing on actual VMs; the development checks use an isolated SSH test server.

## Version 0.1.0

- Manage local and remote Ubuntu/Debian servers through SSH. Add the panel's own host as `127.0.0.1` using a dedicated SSH account.
- Live CPU, memory, root filesystem, uptime, load and network rates; session resource charts.
- Search systemd services; start, stop and restart with confirmation.
- View the latest 200 journal entries, refreshed every five seconds.
- Real SSH pseudo-terminal with xterm.js, resizing, copy/paste, terminal applications and expansion.
- Administrator login; encrypted saved SSH private keys and passphrases; pinned SSH host fingerprints; activity history.
- Cloudflare Tunnel helper for panel access, plus per-server connector metrics, service status and journal inspection.
- Local terminal assets; no external CDN or analytics required.

This is an initial implementation, not a complete Webmin replacement. File management, packages/updates, users/groups, firewall administration, scheduled jobs, alerting, multiple panel accounts, roles and two-factor authentication are not included yet. The audit records sign-ins, connection changes, service requests/results and terminal open/close events; it does not record shell keystrokes or commands. Metrics history lasts only while the page is open. Logging access depends on the remote SSH user's permissions. There is no agent yet: the panel host must be able to reach each server's SSH port, directly or through a VPN.

## Install on Ubuntu or Debian

Download or clone this repository, then run from its directory:

```bash
sudo bash install.sh
```

The installer installs Python dependencies in a virtual environment, creates an unprivileged `servercp` service account, asks for an admin password, and starts the panel on **127.0.0.1:8090**. It does not change your web server, firewall or SSH configuration. It refuses to overwrite an existing installation.

The repository may be private. Download the ZIP from GitHub while signed in, or use an authenticated clone; an anonymous `curl` installer will not be able to fetch a private repository.

For initial access, run this **on your own computer**:

```bash
ssh -L 8090:127.0.0.1:8090 YOUR_USER@YOUR_SERVER
```

Keep that SSH connection open and visit `http://localhost:8090`. Use `localhost` exactly because requests are checked against the configured origin.

### Use a domain and HTTPS

Put a TLS reverse proxy in front of the loopback listener. A sample Nginx location block is in `deploy/nginx.conf.example`. Obtain a certificate through your usual hosting setup. Edit `/etc/servercp/servercp.env`:

```ini
PANEL_DATA=/var/lib/servercp
PANEL_ORIGIN=https://panel.example.com
```

Then restart:

```bash
sudo systemctl restart servercp
```

The browser URL and `PANEL_ORIGIN` must match exactly, including the scheme and port, without a path. HTTPS origins enable Secure session cookies. WebSockets must be forwarded by the proxy. The configured origin should be private or restricted to the administrators who need it.

## Connect a server

The remote machine needs OpenSSH server, Python 3, systemd and the standard `/proc` filesystem. Use an existing SSH account with only the permissions you want ServerCP to have.

1. Generate a dedicated key on your trusted computer: `ssh-keygen -t ed25519 -f servercp-access`.
2. Install **the public key** (`servercp-access.pub`) in that remote account's `~/.ssh/authorized_keys`, or use `ssh-copy-id -i servercp-access.pub USER@SERVER`.
3. On the remote server's trusted console, inspect host fingerprints:

   ```bash
   for key in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$key" -E sha256; done
   ```

4. In ServerCP, choose **Add server**, enter its hostname/IP, SSH port and username, then paste the private key, optional passphrase and the verified `SHA256:...` fingerprint. Ed25519 host keys are preferred; the fingerprint must match the host key negotiated by SSH.
5. The connection is saved immediately. The dashboard will attempt to connect and report any connection error. Saving does not claim the host is reachable.

Keys are encrypted with a randomly generated key stored in the panel data directory. Back up the **whole** `/var/lib/servercp` directory, not just the database. Encryption protects database-only disclosures; it does not protect against compromise of the panel host or service account.

### Permissions

The terminal runs with the remote SSH account's privileges. Use `sudo` interactively when needed. Service buttons run `sudo -n systemctl start|stop|restart`, so they require a root SSH account or explicit passwordless sudo rules for the intended units. ServerCP does not grant those rules automatically. For example, an administrator can use `visudo` to allow a dedicated account to restart only Nginx:

```sudoers
servercp-remote ALL=(root) NOPASSWD: /usr/bin/systemctl restart -- nginx.service
```

For journal access, use the permissions appropriate to your system, such as adding the remote user to `systemd-journal`. Reconnect after group changes. Do not grant blanket passwordless root access just to enable the buttons.

## Cloudflare Tunnel

Use a dashboard-managed tunnel to publish the panel without opening an inbound web port. The panel still listens on `127.0.0.1:8090`; `cloudflared` connects outward to Cloudflare. A real domain in your Cloudflare account is required for the public route.

1. In Cloudflare, create a tunnel under **Networking > Tunnels** and copy its connector token.
2. Install `cloudflared` on the panel host using the [official downloads/package instructions](https://developers.cloudflare.com/tunnel/downloads/). The helper requires version 2025.4.0+ for `--token-file`.
3. Run on the panel host:

   ```bash
   sudo bash /opt/servercp/install-tunnel.sh
   ```

4. Enter your panel hostname, an unused metrics port (default `20241`), and the tunnel token at the hidden prompt.
5. Add a **Published application** route in the Cloudflare dashboard: your chosen hostname → `http://127.0.0.1:8090`.
6. Visit the HTTPS hostname. Optionally add a Cloudflare Access application to restrict entry to your administrator accounts. ServerCP login remains required.

The helper creates a separate unprivileged `servercp-tunnel.service`, stores the token at `/etc/servercp/tunnel/token` with restrictive permissions, and updates the panel's exact HTTPS origin. It does not overwrite an existing `cloudflared.service`, create Cloudflare account resources, DNS records or Access policies. It refuses to replace its own existing connector. Configure the route in the Cloudflare dashboard; starting the service alone does not publish the hostname. After changing the origin, the old localhost tunnel URL will no longer accept sign-in requests; use the configured HTTPS hostname.

### Tunnel monitoring

Open **Cloudflare Tunnel** in ServerCP and select a server. Set the metrics port matching that server's connector. The panel reads `http://127.0.0.1:PORT/metrics` **on that remote server through SSH**, so metrics need not be exposed publicly. Cloudflared's default non-containerized port is the first available port from 20241–20245; set an explicit `--metrics 127.0.0.1:PORT` for predictable monitoring.

The page shows active edge connections, connector request/error counters, active streams, version, current edge locations, Go heap memory and connector uptime where supplied. Current diagnostic connection state is used when `/diag/tunnel` is available; older connectors fall back to their reported connection gauge. Missing metrics display `—`; an unavailable endpoint is never labelled healthy. Counts are local to that connector and reset when it restarts. This is connector telemetry, not account-wide Cloudflare analytics or a guarantee that the public application works.

The standard `cloudflared.service` and the helper's `servercp-tunnel.service` are detected independently. Journal viewing uses the remote user's permissions; restarting uses the same explicit sudo model as other service buttons. Restarting the connector through which you are visiting the panel may briefly interrupt access.

This first version does not use a tunnel as an SSH proxy. To administer remote servers behind NAT, provide a reachable SSH route via your VPN or configure Cloudflare private network routing/WARP on the panel host and use the remote server's routed private IP. Publishing the panel through an HTTP tunnel does not itself make every remote SSH server reachable.

### Connector maintenance

Inspect `sudo journalctl -u servercp-tunnel -n 100 --no-pager`. To rotate the token, refresh it in Cloudflare, replace `/etc/servercp/tunnel/token` through a trusted terminal without putting it in shell command history, retain owner `servercp-tunnel` and mode `600`, then restart `servercp-tunnel`. Update the installed `cloudflared` package through its package manager; the helper disables automatic binary updates.

To undo panel tunnel access, stop/disable `servercp-tunnel`, restore `/etc/servercp/servercp.env.before-tunnel` to `/etc/servercp/servercp.env`, and restart ServerCP. Remove the Cloudflare public route separately if no longer needed. To remove the connector fully, remove its unit file and token directory and reload systemd. Keep your token out of Git and backups you share.

References: [Tunnel setup](https://developers.cloudflare.com/tunnel/get-started/), [metrics and diagnostics](https://developers.cloudflare.com/tunnel/observability/), [run parameters](https://developers.cloudflare.com/tunnel/reference/run-parameters/).

## Administration

Service status/logs:

```bash
sudo systemctl status servercp
sudo journalctl -u servercp -n 100 --no-pager
```

Reset the panel administrator (revokes active sessions):

```bash
sudo -u servercp env PANEL_DATA=/var/lib/servercp /opt/servercp/.venv/bin/python /opt/servercp/app.py setup
```

To update: download the newer source, stop `servercp`, back up `/var/lib/servercp`, copy `app.py`, `collector.py`, `tunnel_collector.py`, `install-tunnel.sh`, `requirements.txt` and `static/` into `/opt/servercp`, install the requirements with `/opt/servercp/.venv/bin/pip`, then restart. Keep source root-owned. Preserve `/var/lib/servercp` and `/etc/servercp/servercp.env`. Future versions may need migrations, so check their notes before updating.

To remove: `sudo systemctl disable --now servercp`, then remove its unit file and `/opt/servercp`, and run `sudo systemctl daemon-reload`. Keep `/var/lib/servercp` unless you deliberately want to erase saved connections. Remove installed SSH public keys from remote accounts separately if no longer needed.

## Develop and test

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py setup
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8090 --ws-max-size 65536
```

Default origin: `http://localhost:8090`; default data directory: `./data`. Use one application worker because login throttling is held in memory. Sessions expire after eight hours; logout and password reset also terminate open terminal sessions. Failed login attempts are limited to five per five-minute window per directly connected peer. Behind a reverse proxy, peers share a throttle bucket in this first version; forwarded IP headers are deliberately not trusted.

```bash
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest -q
```

Tests exercise login/session protection, origin checks, encrypted credentials, command validation, host-key rejection and SSH metrics/terminal paths against an isolated local SSH fixture. They never administer your real servers.

## Dependencies

Backend: FastAPI, Uvicorn, Paramiko and cryptography. Frontend terminal: xterm.js 6.0.0 and addon-fit 0.11.0, vendored under `static/vendor` with their upstream MIT licenses. Dependency versions are pinned in `requirements.txt`. Review and update pins before a production rollout.
