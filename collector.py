"""Read-only Linux collector, executed with python3 on the selected SSH host."""
import json
import os
import platform
import shutil
import time

def cpu():
    with open('/proc/stat') as f:
        values = list(map(int, f.readline().split()[1:9]))
    return sum(values), values[3] + values[4]

def collect():
    a = cpu()
    time.sleep(0.25)
    b = cpu()
    memory = {}
    with open('/proc/meminfo') as f:
        for line in f:
            key, value = line.split(':', 1)
            memory[key] = int(value.strip().split()[0]) * 1024
    with open('/proc/uptime') as f:
        uptime = float(f.read().split()[0])
    osname = 'Linux'
    with open('/etc/os-release') as f:
        for line in f:
            if line.startswith('PRETTY_NAME='):
                osname = line.strip().split('=', 1)[1].strip('"')
    network = []
    with open('/proc/net/dev') as f:
        for line in f.readlines()[2:]:
            name, values = line.split(':')
            values = values.split()
            if name.strip() != 'lo':
                network.append({'name': name.strip(), 'rx': int(values[0]), 'tx': int(values[8])})
    disk = shutil.disk_usage('/')
    return {'hostname': platform.node(), 'os': osname, 'kernel': platform.release(),
            'cpu': round(100 * (1 - (b[1] - a[1]) / max(1, b[0] - a[0])), 1),
            'cores': os.cpu_count(), 'memory_total': memory['MemTotal'],
            'memory_used': memory['MemTotal'] - memory.get('MemAvailable', memory['MemFree']),
            'disk_total': disk.total, 'disk_used': disk.used, 'uptime': uptime,
            'load': list(os.getloadavg()), 'network': network, 'timestamp': time.time()}

if __name__ == '__main__':
    print(json.dumps(collect()))
