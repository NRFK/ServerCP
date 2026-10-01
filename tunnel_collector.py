"""Read-only cloudflared metrics collector. HTTP stays on the SSH host's loopback."""
import json
import math
import re
import subprocess
import sys
import time
import urllib.request

def parse_metrics(text):
    values, locations, version = {}, set(), None
    for line in text.splitlines():
        match = re.fullmatch(r'([a-zA-Z_:][a-zA-Z0-9_:]*)(\{.*\})?\s+([-+0-9.eE]+)(?:\s+\d+)?', line)
        if not match:
            continue
        name, labels, raw = match.groups()
        value = float(raw)
        if not math.isfinite(value):
            continue
        values[name] = values.get(name, 0) + value
        labels = dict((key, json.loads(quoted)) for key, quoted in re.findall(r'(\w+)=("(?:[^"\\]|\\.)*")', labels or ''))
        if name == 'cloudflared_tunnel_server_locations' and value > 0 and labels.get('edge_location'):
            locations.add(labels['edge_location'])
        if name == 'build_info' and value > 0:
            version = labels.get('version')
    return {'connections': values.get('cloudflared_tunnel_ha_connections'),
            'requests': values.get('cloudflared_tunnel_total_requests'),
            'errors': values.get('cloudflared_tunnel_request_errors'),
            'active_streams': values.get('cloudflared_tunnel_active_streams'),
            'memory_bytes': values.get('go_memstats_heap_alloc_bytes'),
            'started_at': values.get('process_start_time_seconds'),
            'locations': sorted(locations), 'version': version}

def service_states():
    states = []
    for unit in ('servercp-tunnel.service', 'cloudflared.service'):
        result = subprocess.run(['systemctl', 'show', unit, '--property=LoadState,ActiveState,SubState', '--no-pager'], capture_output=True, text=True, timeout=3)
        properties = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if properties.get('LoadState') not in (None, 'not-found'):
            states.append({'unit': unit, 'active': properties.get('ActiveState'), 'sub': properties.get('SubState')})
    return states

def collect(port):
    result = {'available': False, 'port': port, 'timestamp': time.time(), 'services': service_states()}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open('http://127.0.0.1:%d/metrics' % port, timeout=2) as response:
            content = response.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError('Metrics output too large')
        text = content.decode('utf8')
        if 'cloudflared_' not in text:
            raise ValueError('Not a cloudflared metrics endpoint')
        result.update(parse_metrics(text))
        result['available'] = True
        try:
            with opener.open('http://127.0.0.1:%d/diag/tunnel' % port, timeout=2) as response:
                raw = response.read(65537)
            if len(raw) <= 65536:
                diag = json.loads(raw)
                result['tunnel_id'] = diag.get('tunnelID')
                result['connector_id'] = diag.get('connectorID')
                if isinstance(diag.get('connections'), list):
                    result['connections'] = sum(1 for c in diag['connections'] if c.get('isConnected'))
        except Exception:
            pass  # Older connectors may not expose diagnostics.
    except Exception:
        result['message'] = 'Metrics unavailable on loopback port %d. Check the connector and its --metrics setting.' % port
    return result

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 20241
    if not 1 <= port <= 65535:
        raise SystemExit('Invalid metrics port')
    print(json.dumps(collect(port)))
