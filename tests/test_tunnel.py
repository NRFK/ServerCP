from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import tunnel_collector
from test_panel import client, reset, ssh_host, sign_in, ORIGIN

METRICS = '''# HELP cloudflared_tunnel_ha_connections connections
cloudflared_tunnel_ha_connections 4
cloudflared_tunnel_total_requests{tunnel_id="test"} 1234
cloudflared_tunnel_request_errors{tunnel_id="test"} 2
cloudflared_tunnel_active_streams 8
cloudflared_tunnel_server_locations{connection_id="0",edge_location="LHR"} 1
cloudflared_tunnel_server_locations{connection_id="1",edge_location="LHR"} 1
cloudflared_tunnel_server_locations{connection_id="2",edge_location="AMS"} 0
build_info{version="2026.9.1",type="cloudflared"} 1
go_memstats_heap_alloc_bytes 2048
process_start_time_seconds 1700000000
'''

def test_metrics_parser_and_missing_values():
    parsed = tunnel_collector.parse_metrics(METRICS)
    assert parsed['connections'] == 4
    assert parsed['requests'] == 1234
    assert parsed['errors'] == 2
    assert parsed['locations'] == ['LHR']
    assert parsed['version'] == '2026.9.1'
    assert parsed['memory_bytes'] == 2048
    missing = tunnel_collector.parse_metrics('# only a comment')
    assert missing['connections'] is None and missing['errors'] is None

def test_metrics_unavailable_does_not_invent_healthy_status(monkeypatch):
    monkeypatch.setattr(tunnel_collector, 'service_states', lambda: [])
    monkeypatch.setattr(tunnel_collector.urllib.request, 'build_opener', lambda *a: None)
    result = tunnel_collector.collect(20241)
    assert not result['available']
    assert 'connections' not in result
    assert 'Metrics unavailable' in result['message']

def test_tunnel_over_real_ssh(client, ssh_host):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/metrics':
                payload = METRICS.encode()
            elif self.path == '/diag/tunnel':
                payload = json.dumps({'tunnelID': 'test-tunnel', 'connectorID': 'test-connector', 'connections': [{'isConnected': True}, {'isConnected': True}, {'isConnected': False}]}).encode()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    try:
        sign_in(client)
        body, _ = ssh_host
        sid = client.post('/api/servers', headers=ORIGIN, json=body).json()['id']
        assert client.get(f'/api/servers/{sid}/tunnel?port=0').status_code == 422
        result = client.get(f'/api/servers/{sid}/tunnel?port={http.server_port}')
        assert result.status_code == 200, result.text
        metrics = result.json()
        assert metrics['available']
        assert metrics['connections'] == 2  # Live diagnostic connections override the gauge.
        assert metrics['tunnel_id'] == 'test-tunnel'
        assert metrics['requests'] == 1234
        assert metrics['locations'] == ['LHR']
    finally:
        http.shutdown()
        http.server_close()
