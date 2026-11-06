from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile

from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template
import yaml


root = Path(__file__).resolve().parents[1]
inventory_path = sys.argv[1] if len(sys.argv) > 1 else str(root / 'inventory/example/hosts.yaml')
inventory = json.loads(subprocess.check_output([
    str(root / '.venv/bin/ansible-inventory'),
    '-i', inventory_path, '--list',
], text=True))
hosts = inventory['_meta']['hostvars']
variables = dict(next(iter(hosts.values())), hostvars=hosts, groups={
    'k8s_cluster': inventory['kube_control_plane']['hosts'],
})
template = (root / 'templates/firewall.nft.j2').read_text()
rules = Templar(loader=DataLoader(), variables=variables).template(trust_as_template(template))
assert 'flush ruleset' not in rules
assert 'policy drop' in rules
assert 'tcp dport 30080 accept' in rules
assert 'ct original proto-dst 30080 accept' in rules
assert 'tcp dport 6443 accept' not in rules
assert 'tcp dport 22 accept' in rules
assert 'iifname "eth0" drop' in rules
for host in hosts.values():
    assert host['ip'] in rules
    assert host['firewall_admin_sources'] == variables['firewall_admin_sources']
playbook = yaml.safe_load((root / 'firewall.yaml').read_text())[0]
assert playbook['serial'] == 1
assert playbook['any_errors_fatal'] is True
names = [task['name'] for task in playbook['tasks']]
assert names.index('Preserve previous firewall configuration') < names.index('Render isolated firewall table')
assert names.index('Arm lockout rollback before activation') < names.index('Activate firewall and enable persistence')
assert names.index('Verify a new authenticated connection') < names.index('Cancel rollback after reconnecting')
rollback = next(task['ansible.builtin.copy']['content'] for task in playbook['tasks']
                if task['name'] == 'Install lockout rollback')
for exists in (False, True):
    script = Templar(loader=DataLoader(), variables={'firewall_previous': {'stat': {'exists': exists}}}).template(trust_as_template(rollback))
    subprocess.run(['sh', '-n'], input=script, text=True, check=True)
    if exists:
        assert 'disable' not in script and 'delete table' not in script
        assert script.index('/usr/bin/cp') < script.index('/usr/sbin/nft --file') < script.index('enable --now')
    else:
        assert 'disable --now aranya-firewall.service' in script
        assert '/usr/bin/cp' not in script
wrapper = yaml.safe_load((root / 'deploy.yaml').read_text())
assert [play['ansible.builtin.import_playbook'] for play in wrapper] == [
    'firewall.yaml', '.cache/kubespray/cluster.yml',
]
env = dict(os.environ, ANSIBLE_CONFIG=str(root / '.cache/kubespray/ansible.cfg'))
command = [str(root / '.venv/bin/ansible-playbook')]
subprocess.run(command + ['-i', inventory_path, str(root / 'deploy.yaml'), '--syntax-check'],
               cwd=root / '.cache/kubespray', env=env, check=True)
tasks = subprocess.check_output(command + ['-i', inventory_path, str(root / 'deploy.yaml'), '--list-tasks'],
                                cwd=root / '.cache/kubespray', env=env, text=True)
assert tasks.index('Check required firewall inputs') < tasks.index('Check Ansible version')
with tempfile.TemporaryDirectory() as directory:
    local_inventory = Path(directory) / 'inventory.yaml'
    local_inventory.write_text('''all:
  children:
    k8s_cluster:
      hosts:
        test1:
          ansible_connection: local
        test2:
          ansible_connection: local
''')
    result = subprocess.run(command + [
        '-i', str(local_inventory), str(root / 'deploy.yaml'),
        '--extra-vars', '{"ansible_become":false,"firewall_admin_sources":[],"firewall_private_interface":"eth1","firewall_public_interface":"eth0"}',
    ], cwd=root / '.cache/kubespray', env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert 'Check required firewall inputs' in result.stdout, result.stdout + result.stderr
    assert 'Check Ansible version' not in result.stdout, result.stdout + result.stderr
print('PASS: firewall renders, wrapper ordering, syntax and failed preflight stops deployment')
