import argparse
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import urllib.request


root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description='Read-only live acceptance; no failure injection or secret output.')
parser.add_argument('inventory')
parser.add_argument('--server', required=True)
parser.add_argument('--nginx', action='store_true', help='Also require config/nginx GitOps health and public HTTP.')
args = parser.parse_args()
inventory = json.loads(subprocess.check_output([
    str(root / '.venv/bin/ansible-inventory'), '-i', args.inventory, '--list',
], text=True))
hosts = inventory['_meta']['hostvars']
env = dict(os.environ, KUBECONFIG=str(root / 'inventory/production/artifacts/admin.conf'))
command = [str(root / 'inventory/production/artifacts/kubectl'), '--server=' + args.server]


def kubectl(*arguments):
    return subprocess.check_output(command + list(arguments), env=env, text=True)


def get(*arguments):
    return json.loads(kubectl('get', *arguments, '-o', 'json'))


def replicas(namespace, kind, name, count):
    resource = get(kind, name, '-n', namespace)
    assert resource['spec']['replicas'] == count, name
    assert resource['status'].get('readyReplicas', 0) == count, name
    return resource


assert kubectl('get', '--raw=/readyz').strip() == 'ok'
nodes = get('nodes')['items']
assert {node['metadata']['name'] for node in nodes} == set(hosts)
for node in nodes:
    name = node['metadata']['name']
    assert any(c['type'] == 'Ready' and c['status'] == 'True' for c in node['status']['conditions']), name
    assert any(a['type'] == 'InternalIP' and a['address'] == hosts[name]['ip'] for a in node['status']['addresses']), name
    assert any(k.startswith('feature.node.kubernetes.io/') for k in node['metadata']['labels']), name
cilium = get('daemonset', 'cilium', '-n', 'kube-system')['status']
assert cilium['desiredNumberScheduled'] == cilium['numberReady'] == 3
replicas('kube-system', 'deployment', 'cilium-operator', 2)
replicas('argocd', 'deployment', 'argocd-server', 2)
replicas('argocd', 'deployment', 'argocd-repo-server', 2)
replicas('argocd', 'statefulset', 'argocd-redis-ha-server', 3)
replicas('argocd', 'statefulset', 'argocd-application-controller', 1)
for suffix in ('', '-webhook', '-cainjector'):
    replicas('clusterdos-cert-manager', 'deployment', 'clusterdos-certmanager-cert-manager' + suffix, 2)
metrics = replicas('kube-system', 'deployment', 'clusterdos-metricsserver-metrics-server', 2)
assert '--kubelet-insecure-tls' not in metrics['spec']['template']['spec']['containers'][0]['args']
api = get('apiservice', 'v1beta1.metrics.k8s.io')
assert not api['spec'].get('insecureSkipTLSVerify', False)
assert base64.b64decode(api['spec']['caBundle']).startswith(b'-----BEGIN CERTIFICATE-----')
assert any(c['type'] == 'Available' and c['status'] == 'True' for c in api['status']['conditions'])
assert len(json.loads(kubectl('get', '--raw=/apis/metrics.k8s.io/v1beta1/nodes'))['items']) == 3
print(kubectl('top', 'nodes'), end='')
replicas('clusterdos-nfd', 'deployment', 'clusterdos-nfd-node-feature-discovery-master', 2)
workers = get('daemonset', 'clusterdos-nfd-node-feature-discovery-worker', '-n', 'clusterdos-nfd')['status']
assert workers['desiredNumberScheduled'] == workers['numberReady'] == 3
apps = ['clusterdos', 'clusterdos-certmanager', 'clusterdos-metricsserver', 'clusterdos-nfd']
if args.nginx:
    apps += ['clusterdos-config', 'hello-aranya']
for name in apps:
    app = get('application', name, '-n', 'argocd')
    assert app['status']['sync']['status'] == 'Synced', name
    assert app['status']['health']['status'] == 'Healthy', name
    assert not app['status'].get('conditions'), (name, app['status'].get('conditions'))
print('PASS: nodes, Cilium, Argo, required addon GitOps, verified metrics TLS/API and NFD')
if args.nginx:
    replicas('hello-aranya', 'deployment', 'hello-aranya', 3)
    pods = get('pods', '-n', 'hello-aranya', '-l', 'app=hello-aranya')['items']
    ready = [p for p in pods if any(c['type'] == 'Ready' and c['status'] == 'True' for c in p['status'].get('conditions', []))]
    assert len(ready) == 3 and {p['spec']['nodeName'] for p in ready} == set(hosts)
    for host in hosts.values():
        with urllib.request.urlopen('http://' + host['ansible_host'] + ':30080/', timeout=10) as response:
            assert response.status == 200 and 'hello aranya' in response.read().decode()
    print('PASS: three spread nginx pods, config/nginx GitOps and public HTTP 200 on all nodes')
for name, host in hosts.items():
    for port in (2379, 2380, 6443, 10250):
        try:
            with socket.create_connection((host['ansible_host'], port), timeout=2):
                raise AssertionError(f'{name}: public port {port} is exposed')
        except TimeoutError:
            pass
    print(f'PASS: {name} public etcd/API/kubelet filtered')
