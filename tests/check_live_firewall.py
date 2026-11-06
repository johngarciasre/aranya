import json
from pathlib import Path
import shlex
import socket
import subprocess
import sys


root = Path(__file__).resolve().parents[1]
assert len(sys.argv) == 3 and sys.argv[2] == '--preinstall', 'Preinstall check only: binds temporary listeners on Kubernetes ports.'
inventory = json.loads(subprocess.check_output([
    str(root / '.venv/bin/ansible-inventory'), '-i', sys.argv[1], '--list',
], text=True))
hosts = inventory['_meta']['hostvars']
server = '''import socket, sys, threading
sockets = []
def serve(sock, udp):
    while True:
        if udp:
            data, address = sock.recvfrom(32)
            sock.sendto(data, address)
        else:
            conn, address = sock.accept()
            conn.close()
for port in (2379, 2380, 6443, 10250, 4240, 30080):
    sock = socket.socket()
    sock.bind(('0.0.0.0', port))
    sock.listen()
    sockets.append(sock)
    threading.Thread(target=serve, args=(sock, False), daemon=True).start()
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('0.0.0.0', 8472))
sockets.append(sock)
threading.Thread(target=serve, args=(sock, True), daemon=True).start()
print('READY', flush=True)
sys.stdin.read()
'''


def ssh(host):
    return ['ssh', '-i', host['ansible_ssh_private_key_file'],
            '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=10', '-o',
            f'UserKnownHostsFile={root / ".local/known_hosts"} {Path.home() / ".ssh/known_hosts"}',
            f'{host["ansible_user"]}@{host["ansible_host"]}']


listeners = []
try:
    for host in hosts.values():
        process = subprocess.Popen(ssh(host) + ['exec python3 -u -c ' + shlex.quote(server)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        listeners.append(process)
        assert process.stdout.readline().strip() == 'READY'
    for name, host in hosts.items():
        peers = [other['ip'] for other in hosts.values() if other['ip'] != host['ip']]
        client = f'''import socket
for peer in {peers!r}:
    for port in (2379, 2380, 6443, 10250, 4240):
        with socket.create_connection((peer, port), timeout=3): pass
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(3)
        sock.sendto(b'aranya', (peer, 8472))
        assert sock.recvfrom(32)[0] == b'aranya'
'''
        subprocess.run(ssh(host) + ['python3 -c ' + shlex.quote(client)], check=True)
        with socket.create_connection((host['ansible_host'], 30080), timeout=3):
            pass
        for port in (2379, 6443, 10250):
            try:
                with socket.create_connection((host['ansible_host'], port), timeout=2):
                    raise AssertionError(f'{name}: public port {port} is exposed')
            except TimeoutError:
                pass
        print(f'PASS: {name} private TCP/UDP connectivity, public HTTP allowed, API/etcd/kubelet filtered')
finally:
    for process in listeners:
        process.stdin.close()
        process.wait(timeout=15)
