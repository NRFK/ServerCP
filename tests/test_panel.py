import base64
import hashlib
import io
import json
import socket
import subprocess
import threading
import time

import paramiko
import pytest
from fastapi.testclient import TestClient
import app

ORIGIN = {'origin': app.ORIGIN}
PASSWORD = 'test-admin-password-123'

@pytest.fixture(autouse=True)
def reset():
    salt = b'test-salt-1234567'
    with app.db() as c:
        for table in ('admin', 'sessions', 'servers', 'audit'):
            c.execute('DELETE FROM ' + table)
        c.execute('INSERT INTO admin VALUES (1,?,?,?)', ('admin', salt, hashlib.scrypt(PASSWORD.encode(), salt=salt, n=16384, r=8, p=1)))
    app.FAILURES.clear()

@pytest.fixture
def client():
    with TestClient(app.app) as client:
        yield client

def sign_in(client):
    response = client.post('/api/login', headers=ORIGIN, json={'username': 'admin', 'password': PASSWORD})
    assert response.status_code == 200
    return response

def test_protected_routes_and_origin(client):
    assert client.get('/api/servers').status_code == 401
    assert client.post('/api/login', json={'username': 'admin', 'password': PASSWORD}).status_code == 403
    assert client.post('/api/login', headers={'origin': 'https://evil.example'}, json={'username': 'admin', 'password': PASSWORD}).status_code == 403
    response = sign_in(client)
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=strict' in response.headers['set-cookie']
    assert client.get('/api/servers').json() == []
    assert 'frame-ancestors' in response.headers['content-security-policy']

def test_login_throttle(client):
    for _ in range(5):
        assert client.post('/api/login', headers=ORIGIN, json={'username': 'admin', 'password': 'wrong'}).status_code == 401
    assert client.post('/api/login', headers=ORIGIN, json={'username': 'admin', 'password': PASSWORD}).status_code == 429

def test_logout_revokes_session(client):
    sign_in(client)
    old = client.cookies.get(app.COOKIE)
    assert client.post('/api/logout', headers=ORIGIN).status_code == 200
    assert not app.authorized(old)
    assert client.get('/api/servers').status_code == 401

def test_expired_session(client):
    sign_in(client)
    with app.db() as c:
        c.execute('UPDATE sessions SET expires=?', (time.time() - 1,))
    assert client.get('/api/audit').status_code == 401

def key_text(key):
    buffer = io.StringIO()
    key.write_private_key(buffer)
    return buffer.getvalue()

def fingerprint(key):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')

def server_body():
    return {'name': 'Test server', 'host': '127.0.0.1', 'port': 22, 'username': 'test', 'fingerprint': fingerprint(paramiko.RSAKey.generate(2048)), 'private_key': key_text(paramiko.RSAKey.generate(2048)), 'passphrase': ''}

def test_encrypted_keys_and_no_secret_disclosure(client):
    sign_in(client)
    body = server_body()
    response = client.post('/api/servers', headers=ORIGIN, json=body)
    assert response.status_code == 200
    sid = response.json()['id']
    stored = app.get_server(sid)
    assert body['private_key'].encode() not in stored['credentials']
    assert json.loads(app.cipher.decrypt(stored['credentials']))['key'] == body['private_key']
    returned = client.get('/api/servers').json()[0]
    assert 'credentials' not in returned and 'private_key' not in returned
    assert client.delete('/api/servers/' + sid, headers=ORIGIN).status_code == 200
    assert client.get('/api/servers/' + sid + '/metrics').status_code == 404

def test_invalid_server_input(client):
    sign_in(client)
    body = server_body()
    body['private_key'] = 'invalid' * 20
    assert client.post('/api/servers', headers=ORIGIN, json=body).status_code == 400
    body['host'] = 'host; touch /tmp/unsafe'
    assert client.post('/api/servers', headers=ORIGIN, json=body).status_code == 422

def test_service_command_validation(client, monkeypatch):
    sign_in(client)
    commands = []
    monkeypatch.setattr(app, 'run', lambda sid, command: commands.append(command) or '')
    assert client.post('/api/servers/a/services/nginx.service;whoami', headers=ORIGIN, json={'action': 'restart'}).status_code == 400
    assert client.post('/api/servers/a/services/-bad.service', headers=ORIGIN, json={'action': 'restart'}).status_code == 400
    assert client.post('/api/servers/a/services/nginx.service', headers=ORIGIN, json={'action': 'restart;whoami'}).status_code == 422
    assert commands == []
    assert client.post('/api/servers/a/services/nginx.service', headers=ORIGIN, json={'action': 'restart'}).status_code == 200
    assert commands == ['sudo -n systemctl restart -- nginx.service']
    actions = [x['action'] for x in client.get('/api/audit').json()]
    assert 'service.request' in actions and 'service.success' in actions

def test_host_key_mismatch():
    key = paramiko.RSAKey.generate(2048)
    policy = app.PinnedHost('SHA256:' + 'a' * 43)
    with pytest.raises(paramiko.SSHException, match='fingerprint does not match'):
        policy.missing_host_key(None, 'test', key)
    app.PinnedHost(fingerprint(key)).missing_host_key(None, 'test', key)

class SSHFixture(paramiko.ServerInterface):
    def __init__(self, expected):
        self.expected = expected
        self.resized = threading.Event()

    def check_auth_publickey(self, username, key):
        return paramiko.AUTH_SUCCESSFUL if username == 'test' and key == self.expected else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return 'publickey'

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, channel, term, width, height, pixelwidth, pixelheight, modes):
        return True

    def check_channel_window_change_request(self, channel, width, height, pixelwidth, pixelheight):
        self.resized.set()
        return True

    def check_channel_exec_request(self, channel, command):
        def execute():
            # Let Paramiko send the exec-request acknowledgement before this
            # very short fixture command sends exit status and closes.
            time.sleep(0.05)
            # Only execute the fixed read-only collector in the fixture.
            command_text = command.decode()
            if command_text.startswith('python3 -c '):
                result = subprocess.run(command_text, shell=True, capture_output=True, timeout=15)
                channel.sendall(result.stdout)
                channel.sendall_stderr(result.stderr)
                channel.send_exit_status(result.returncode)
            elif command_text.startswith('systemctl list-units'):
                channel.sendall(b'nginx.service loaded active running A web server\n')
                channel.send_exit_status(0)
            elif command_text.startswith('journalctl '):
                channel.sendall(b'2026-10-01T12:00:00 test nginx[1]: Started\n')
                channel.send_exit_status(0)
            else:
                channel.sendall_stderr(b'Unsupported fixture command')
                channel.send_exit_status(1)
            channel.close()
        threading.Thread(target=execute, daemon=True).start()
        return True

    def check_channel_shell_request(self, channel):
        def shell():
            channel.sendall(b'fixture ready\r\n$ ')
            try:
                while data := channel.recv(32768):
                    channel.sendall(data)
            except Exception:
                pass
        threading.Thread(target=shell, daemon=True).start()
        return True

@pytest.fixture
def ssh_host():
    host_key = paramiko.RSAKey.generate(2048)
    user_key = paramiko.RSAKey.generate(2048)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    listener.settimeout(0.1)
    port = listener.getsockname()[1]
    stop = threading.Event()
    instances, transports = [], []
    def serve():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            def session(conn):
                transport = paramiko.Transport(conn)
                transports.append(transport)
                transport.add_server_key(host_key)
                server = SSHFixture(user_key)
                instances.append(server)
                try:
                    transport.start_server(server=server)
                    channel = transport.accept(10)
                    while transport.is_active() and not stop.is_set():
                        time.sleep(0.05)
                except Exception:
                    pass
                finally:
                    transport.close()
            threading.Thread(target=session, args=(conn,), daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()
    yield {'name': 'SSH fixture', 'host': '127.0.0.1', 'port': port, 'username': 'test', 'fingerprint': fingerprint(host_key), 'private_key': key_text(user_key), 'passphrase': ''}, instances
    stop.set()
    listener.close()
    for transport in transports:
        transport.close()

def test_real_ssh_metrics_services_logs(client, ssh_host):
    sign_in(client)
    body, _ = ssh_host
    sid = client.post('/api/servers', headers=ORIGIN, json=body).json()['id']
    response = client.get('/api/servers/' + sid + '/metrics')
    assert response.status_code == 200, response.text
    metrics = response.json()
    assert 0 <= metrics['cpu'] <= 100
    assert metrics['memory_total'] > 0 and metrics['uptime'] > 0
    assert client.get('/api/servers/' + sid + '/services').json()[0]['unit'] == 'nginx.service'
    assert 'Started' in client.get('/api/servers/' + sid + '/logs').json()['text']

def test_real_terminal_resize_echo_and_cleanup(client, ssh_host):
    sign_in(client)
    body, instances = ssh_host
    sid = client.post('/api/servers', headers=ORIGIN, json=body).json()['id']
    with client.websocket_connect('/ws/terminal/' + sid, headers=ORIGIN) as ws:
        assert b'fixture ready' in ws.receive_bytes()
        ws.send_json({'type': 'resize', 'cols': 120, 'rows': 40})
        ws.send_json({'type': 'input', 'data': 'hello terminal\n'})
        assert b'hello terminal' in ws.receive_bytes()
        assert instances[-1].resized.wait(2)
    actions = [x['action'] for x in client.get('/api/audit').json()]
    assert 'terminal.open' in actions and 'terminal.close' in actions

def test_websocket_rejects_missing_auth_and_wrong_origin(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/ws/terminal/test', headers=ORIGIN):
            pass
    sign_in(client)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/ws/terminal/test', headers={'origin': 'https://evil.example'}):
            pass

def test_logout_closes_open_terminal(client, ssh_host):
    from starlette.websockets import WebSocketDisconnect
    sign_in(client)
    body, _ = ssh_host
    sid = client.post('/api/servers', headers=ORIGIN, json=body).json()['id']
    with client.websocket_connect('/ws/terminal/' + sid, headers=ORIGIN) as ws:
        assert b'fixture ready' in ws.receive_bytes()
        assert client.post('/api/logout', headers=ORIGIN).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            ws.receive_bytes()

def test_ssh_mismatch_blocks_real_connection(client, ssh_host):
    sign_in(client)
    body, _ = ssh_host
    body['fingerprint'] = 'SHA256:' + 'a' * 43
    sid = client.post('/api/servers', headers=ORIGIN, json=body).json()['id']
    response = client.get('/api/servers/' + sid + '/metrics')
    assert response.status_code == 502
    assert 'fingerprint does not match' in response.json()['detail']
