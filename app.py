import argparse
import asyncio
import base64
import getpass
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import sqlite3
import threading
import time
from urllib.parse import urlsplit

import paramiko
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('PANEL_DATA', ROOT / 'data'))
ORIGIN = os.environ.get('PANEL_ORIGIN', 'http://localhost:8090').rstrip('/')
SECURE = ORIGIN.startswith('https://')
COOKIE = 'panel_session'
SESSION_TTL = 8 * 3600
LOCK = threading.Lock()
FAILURES = {}
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

def db():
    connection = sqlite3.connect(DATA / 'panel.db', timeout=10)
    connection.row_factory = sqlite3.Row
    return connection

def init():
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(DATA, 0o700)
    keyfile = DATA / 'secret.key'
    if not keyfile.exists():
        fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(Fernet.generate_key())
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS admin (id INTEGER PRIMARY KEY CHECK(id=1), username TEXT, salt BLOB, hash BLOB);
        CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, expires REAL);
        CREATE TABLE IF NOT EXISTS servers (id TEXT PRIMARY KEY, name TEXT, host TEXT, port INTEGER, username TEXT, fingerprint TEXT, credentials BLOB);
        CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at REAL, action TEXT, server TEXT, detail TEXT);
        ''')
    os.chmod(DATA / 'panel.db', 0o600)

init()
cipher = Fernet((DATA / 'secret.key').read_bytes())

def audit(action, server='', detail=''):
    with db() as c:
        c.execute('INSERT INTO audit(at,action,server,detail) VALUES (?,?,?,?)', (time.time(), action, server, detail[:500]))

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

def authorized(token):
    if not token:
        return False
    with db() as c:
        row = c.execute('SELECT expires FROM sessions WHERE hash=?', (token_hash(token),)).fetchone()
    return bool(row and row['expires'] > time.time())

@app.middleware('http')
async def security(request: Request, call_next):
    path = request.url.path
    if path.startswith('/api/'):
        if request.method not in ('GET', 'HEAD') and request.headers.get('origin') != ORIGIN:
            return Response('Origin rejected', status_code=403)
        if path != '/api/login' and not authorized(request.cookies.get(COOKIE)):
            return Response('Authentication required', status_code=401)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    response.headers['Cache-Control'] = 'no-store' if path.startswith('/api/') else 'no-cache'
    return response

class Login(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=1024)

@app.post('/api/login')
def login(body: Login, request: Request, response: Response):
    ip = request.client.host
    now = time.time()
    with LOCK:
        attempts = [t for t in FAILURES.get(ip, []) if t > now - 300]
        if len(attempts) >= 5:
            raise HTTPException(429, 'Too many attempts. Try again in five minutes.')
        attempts.append(now)
        FAILURES[ip] = attempts
    with db() as c:
        admin = c.execute('SELECT * FROM admin WHERE id=1').fetchone()
    if not admin:
        raise HTTPException(503, 'Run the setup command on the panel host first.')
    derived = hashlib.scrypt(body.password.encode(), salt=admin['salt'], n=16384, r=8, p=1)
    if not hmac.compare_digest(derived, admin['hash']) or not hmac.compare_digest(body.username, admin['username']):
        raise HTTPException(401, 'Incorrect username or password')
    with LOCK:
        FAILURES.pop(ip, None)
    token = secrets.token_urlsafe(32)
    with db() as c:
        c.execute('DELETE FROM sessions WHERE expires < ?', (now,))
        c.execute('INSERT INTO sessions VALUES (?,?)', (token_hash(token), now + SESSION_TTL))
    response.set_cookie(COOKIE, token, httponly=True, secure=SECURE, samesite='strict', max_age=SESSION_TTL)
    audit('login')
    return {'ok': True}

@app.post('/api/logout')
def logout(request: Request, response: Response):
    with db() as c:
        c.execute('DELETE FROM sessions WHERE hash=?', (token_hash(request.cookies[COOKIE]),))
    response.delete_cookie(COOKIE)
    return {'ok': True}

class Server(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    host: str = Field(min_length=1, max_length=253, pattern=r'^[a-zA-Z0-9._:\-]+$')
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_.\-]+$')
    fingerprint: str = Field(pattern=r'^SHA256:[A-Za-z0-9+/]{43}$')
    private_key: str = Field(min_length=50, max_length=32768)
    passphrase: str = Field(default='', max_length=1024)

def parse_key(text, passphrase):
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key(io.StringIO(text), password=passphrase or None)
        except (paramiko.SSHException, ValueError):
            continue
    raise ValueError('Private key could not be read. Check its format and passphrase.')

@app.get('/api/servers')
def servers():
    with db() as c:
        return [dict(r) for r in c.execute('SELECT id,name,host,port,username,fingerprint FROM servers ORDER BY name')]

@app.post('/api/servers')
def add_server(body: Server):
    try:
        parse_key(body.private_key, body.passphrase)
    except ValueError as e:
        raise HTTPException(400, str(e))
    sid = secrets.token_hex(12)
    encrypted = cipher.encrypt(json.dumps({'key': body.private_key, 'passphrase': body.passphrase}).encode())
    with db() as c:
        c.execute('INSERT INTO servers VALUES (?,?,?,?,?,?,?)', (sid, body.name, body.host, body.port, body.username, body.fingerprint, encrypted))
    audit('server.add', sid, body.name)
    return {'id': sid}

@app.delete('/api/servers/{sid}')
def remove_server(sid: str):
    get_server(sid)
    with db() as c:
        c.execute('DELETE FROM servers WHERE id=?', (sid,))
    audit('server.remove', sid)
    return {'ok': True}

def get_server(sid):
    with db() as c:
        row = c.execute('SELECT * FROM servers WHERE id=?', (sid,)).fetchone()
    if not row:
        raise HTTPException(404, 'Server not found')
    return dict(row)

class PinnedHost(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected):
        self.expected = expected

    def missing_host_key(self, client, hostname, key):
        actual = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        if not hmac.compare_digest(actual, self.expected):
            raise paramiko.SSHException('SSH host fingerprint does not match. Verify it on the server console.')

def connect(sid):
    server = get_server(sid)
    creds = json.loads(cipher.decrypt(server['credentials']))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(PinnedHost(server['fingerprint']))
    try:
        client.connect(server['host'], port=server['port'], username=server['username'],
                       pkey=parse_key(creds['key'], creds['passphrase']), look_for_keys=False,
                       allow_agent=False, timeout=8, auth_timeout=8, banner_timeout=8)
        client.get_transport().set_keepalive(20)
        return client
    except Exception:
        client.close()
        raise

def run(sid, command, limit=1024 * 1024):
    client = None
    try:
        client = connect(sid)
        _, stdout, _ = client.exec_command(command, timeout=15)
        channel = stdout.channel
        channel.settimeout(15)
        out, err = bytearray(), bytearray()
        deadline = time.monotonic() + 20
        while True:
            if channel.recv_ready():
                out.extend(channel.recv(65536))
            if channel.recv_stderr_ready():
                err.extend(channel.recv_stderr(65536))
            if len(out) + len(err) > limit:
                raise ValueError('Server output exceeds the allowed size')
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if time.monotonic() > deadline:
                raise TimeoutError('Server operation timed out')
            time.sleep(0.01)
        if channel.recv_exit_status() != 0:
            raise ValueError(err.decode(errors='replace')[:500] or 'Command failed. Check SSH user permissions.')
        return out.decode(errors='replace')
    except HTTPException:
        raise
    except Exception as e:
        # No credentials or raw exception representations are returned.
        if isinstance(e, (ValueError, TimeoutError, paramiko.SSHException)):
            message = str(e)[:500]
        else:
            message = 'Unable to connect to the server. Check SSH access and the configured key.'
        raise HTTPException(502, message)
    finally:
        if client:
            client.close()

@app.get('/api/servers/{sid}/metrics')
def metrics(sid: str):
    output = run(sid, 'python3 -c ' + shlex.quote((ROOT / 'collector.py').read_text()))
    try:
        return json.loads(output)
    except ValueError:
        raise HTTPException(502, 'Server returned invalid metrics')

@app.get('/api/servers/{sid}/services')
def services(sid: str):
    output = run(sid, 'systemctl list-units --type=service --all --no-pager --no-legend --plain')
    result = []
    for line in output.splitlines():
        fields = line.split(None, 4)
        if len(fields) >= 4:
            result.append(dict(zip(('unit', 'load', 'active', 'sub', 'description'), fields)))
    return result

@app.get('/api/servers/{sid}/tunnel')
def tunnel_metrics(sid: str, port: int = Query(default=20241, ge=1, le=65535)):
    output = run(sid, 'python3 -c ' + shlex.quote((ROOT / 'tunnel_collector.py').read_text()) + ' ' + str(port))
    try:
        return json.loads(output)
    except ValueError:
        raise HTTPException(502, 'Server returned invalid tunnel metrics')

class ServiceAction(BaseModel):
    action: str = Field(pattern=r'^(start|stop|restart)$')

def valid_unit(unit):
    if not re.fullmatch(r'[A-Za-z0-9_@.:\-]+\.service', unit) or unit.startswith('-'):
        raise HTTPException(400, 'Invalid service unit')

@app.post('/api/servers/{sid}/services/{unit}')
def service_action(sid: str, unit: str, body: ServiceAction):
    valid_unit(unit)
    audit('service.request', sid, body.action + ' ' + unit)
    try:
        run(sid, 'sudo -n systemctl ' + body.action + ' -- ' + shlex.quote(unit))
    except HTTPException:
        audit('service.failed', sid, body.action + ' ' + unit)
        raise
    audit('service.success', sid, body.action + ' ' + unit)
    return {'ok': True}

@app.get('/api/servers/{sid}/logs')
def logs(sid: str, unit: str = ''):
    if unit:
        valid_unit(unit)
    command = 'journalctl --no-pager -n 200 -o short-iso' + (' -u ' + shlex.quote(unit) if unit else '')
    return {'text': run(sid, command)}

@app.get('/api/audit')
def audit_list():
    with db() as c:
        return [dict(r) for r in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 200')]

@app.websocket('/ws/terminal/{sid}')
async def terminal(ws: WebSocket, sid: str):
    token = ws.cookies.get(COOKIE)
    if ws.headers.get('origin') != ORIGIN or not authorized(token):
        await ws.close(code=1008)
        return
    await ws.accept()
    client = channel = None
    reader = writer = None
    try:
        client = await asyncio.to_thread(connect, sid)
        channel = await asyncio.to_thread(client.invoke_shell, term='xterm-256color', width=100, height=30)
        channel.settimeout(10)
        audit('terminal.open', sid)

        async def output():
            last_auth_check = 0
            while not channel.closed:
                if time.monotonic() - last_auth_check >= 1:
                    if not authorized(token):
                        await ws.close(code=1008)
                        return
                    last_auth_check = time.monotonic()
                if channel.recv_ready():
                    await ws.send_bytes(channel.recv(32768))
                elif channel.exit_status_ready():
                    return
                else:
                    await asyncio.sleep(0.025)

        async def inputs():
            while True:
                message = await ws.receive_json()
                if message.get('type') == 'input':
                    data = message.get('data', '')
                    if not isinstance(data, str) or len(data) > 32768:
                        raise ValueError('Input too large')
                    await asyncio.to_thread(channel.sendall, data.encode())
                elif message.get('type') == 'resize':
                    cols, rows = int(message['cols']), int(message['rows'])
                    if not (2 <= cols <= 500 and 2 <= rows <= 250):
                        raise ValueError('Invalid terminal dimensions')
                    await asyncio.to_thread(channel.resize_pty, width=cols, height=rows)

        reader, writer = asyncio.create_task(output()), asyncio.create_task(inputs())
        done, _ = await asyncio.wait((reader, writer), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except Exception:
        try:
            await ws.send_json({'error': 'Terminal disconnected. Check the server connection and SSH fingerprint.'})
        except Exception:
            pass
    finally:
        for task in (reader, writer):
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in (reader, writer) if t), return_exceptions=True)
        if channel:
            channel.close()
        if client:
            client.close()
        audit('terminal.close', sid)
        try:
            await ws.close()
        except Exception:
            pass

app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')

@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['setup'])
    parser.parse_args()
    username = input('Admin username [admin]: ').strip() or 'admin'
    password = getpass.getpass('Admin password (at least 12 characters): ')
    if len(password) < 12 or password != getpass.getpass('Repeat password: '):
        raise SystemExit('Passwords must match and contain at least 12 characters.')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    with db() as c:
        c.execute('INSERT OR REPLACE INTO admin VALUES (1,?,?,?)', (username, salt, digest))
        c.execute('DELETE FROM sessions')
    print('Administrator saved. All existing sessions have been revoked.')
