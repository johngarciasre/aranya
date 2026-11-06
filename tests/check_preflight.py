import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile

from ansible.template import Templar, trust_as_template
import yaml


root = Path(__file__).resolve().parents[1]
script = root / 'scripts/preflight.sh'
playbook_path = root / 'scripts/preflight.yaml'
ansible = root / '.venv/bin/ansible-playbook'
subprocess.run(['bash', '-n', str(script)], check=True)
plays = yaml.safe_load(playbook_path.read_text())
assert [play['hosts'] for play in plays] == ['localhost', 'preflight_nodes', 'localhost']
assert all(play['any_errors_fatal'] for play in plays)
controller, remote, output = (play['tasks'] for play in plays)
required = controller[0]['loop']
assert required == [f'NODE{i}_PUBLIC_IP' for i in range(1, 4)] + [
    'ANSIBLE_USER', 'SSH_KEY', 'FIREWALL_ADMIN_SOURCES',
    'FIREWALL_PRIVATE_INTERFACE', 'FIREWALL_PUBLIC_INTERFACE']
assert remote[0]['ansible.builtin.setup'] is None
assert plays[1]['vars']['ansible_become'] is False
assert remote[1]['vars']['ansible_become'] is True
allowed = {'ansible.builtin.setup', 'ansible.builtin.ping', 'ansible.builtin.assert',
           'ansible.builtin.command', 'ansible.builtin.uri', 'ansible.builtin.set_fact'}
assert all(next(key for key in task if key.startswith('ansible.builtin.')) in allowed for task in remote)
assert all(task['changed_when'] is False for task in remote if 'ansible.builtin.command' in task)
address_command = next(task['ansible.builtin.command']['argv'] for task in remote
                       if task['name'] == 'Inspect global IPv4 addresses on the private interface')
assert address_command == ['ip', '-j', '-4', 'address', 'show', 'dev',
                           '{{ firewall_private_interface }}', 'scope', 'global']
assert output[-2]['ansible.builtin.copy']['mode'] == '0600'
assert output[-3]['ansible.builtin.file']['mode'] == '0700'
assertions = {task['name']: task['ansible.builtin.assert']['that']
              for task in remote if 'ansible.builtin.assert' in task}
facts = {'system': 'Linux', 'architecture': 'x86_64', 'kernel': '6.8.0',
         'processor_vcpus': 4, 'memtotal_mb': 7900,
         'mounts': [{'mount': '/', 'size_total': 80000000000}],
         'env': {'SSH_CONNECTION': '203.0.113.10 50000 198.51.100.11 22'}}
variables = {'ansible_facts': facts, 'firewall_admin_sources': ['203.0.113.10'],
             'ip': '10.10.0.11', 'firewall_private_interface': 'eth1',
             'private_candidates': ['10.10.0.11'],
             'item': {'stdout': json.dumps([{'dev': 'eth1', 'prefsrc': '10.10.0.11'}])}}


def evaluate(expressions, values):
    if isinstance(expressions, str):
        expressions = [expressions]
    return all(Templar(variables=values).evaluate_expression(trust_as_template(expr)) for expr in expressions)


candidates_template = next(task['ansible.builtin.set_fact']['private_candidates'] for task in remote
                           if task['name'] == 'Collect private interface address candidates')
for addresses in ([], ['10.10.0.11'], ['10.10.0.11', '10.10.0.12']):
    values = {'private_address': {'stdout': json.dumps([{'addr_info': [{'local': x} for x in addresses]}])}}
    assert Templar(variables=values).template(trust_as_template(candidates_template)) == addresses
for expressions in assertions.values():
    assert evaluate(expressions, variables)
for key, value in [('architecture', 'aarch64'), ('kernel', '5.4.0'),
                   ('processor_vcpus', 2), ('memtotal_mb', 4000),
                   ('mounts', [{'mount': '/', 'size_total': 10000000000}]),
                   ('env', {'SSH_CONNECTION': '203.0.113.99 50000 198.51.100.11 22'})]:
    bad = copy.deepcopy(variables)
    bad['ansible_facts'][key] = value
    assert not evaluate(assertions['Verify node operating system and resources'], bad), key
for candidates in ([], ['10.10.0.11', '10.10.0.12']):
    assert not evaluate(assertions['Require exactly one address rather than guessing'],
                        dict(variables, private_candidates=candidates))
for address in ('8.8.8.8', '172.15.0.1', '172.32.0.1', '192.169.1.1'):
    assert not evaluate(assertions['Require an RFC1918 private address'],
                        dict(variables, private_candidates=[address]))
for address in ('10.0.0.1', '172.16.0.1', '172.31.255.254', '192.168.0.1'):
    assert evaluate(assertions['Require an RFC1918 private address'],
                    dict(variables, private_candidates=[address]))
for route in ({'dev': 'eth0', 'prefsrc': '10.10.0.11'}, {'dev': 'eth1', 'prefsrc': '10.10.0.99'}):
    assert not evaluate(assertions['Verify private peer routes'],
                        dict(variables, item={'stdout': json.dumps([route])}))
time_check = next(task['failed_when'] for task in remote if task['name'] == 'Verify time synchronization')
for status, failed in [({'rc': 0, 'stdout': 'yes'}, False),
                       ({'rc': 0, 'stdout': 'no'}, True), ({'rc': 1, 'stdout': 'yes'}, True)]:
    assert evaluate(time_check, {'time_status': status}) is failed

with tempfile.TemporaryDirectory() as directory:
    temp = Path(directory)
    key = temp / 'ssh key'
    key.write_text('test-only')
    key.chmod(0o600)
    env = {k: v for k, v in os.environ.items() if k not in required and not k.startswith('NODE')
           and not k.startswith('ANSIBLE_') and k not in ('KUBE_PODS_SUBNET', 'KUBE_SERVICE_ADDRESSES')}
    env.update({f'NODE{i}_PUBLIC_IP': f'198.51.100.{i + 10}' for i in range(1, 4)})
    env.update(ANSIBLE_USER='ubuntu', SSH_KEY=str(key), FIREWALL_ADMIN_SOURCES='203.0.113.10',
               FIREWALL_PRIVATE_INTERFACE='eth1', FIREWALL_PUBLIC_INTERFACE='eth0',
               ANSIBLE_LOCAL_TEMP=str(temp / 'ansible-tmp'))
    subprocess.run([str(ansible), '-i', 'localhost,', str(playbook_path), '--syntax-check'],
                   env=env, check=True, capture_output=True)
    for argument in ('--limit=node1', '--check', '--tags=all'):
        result = subprocess.run(['bash', str(script), argument], env=env, text=True, capture_output=True)
        assert result.returncode != 0 and 'Usage:' in result.stderr

    # Preserve the real controller and output plays while replacing only remote checks with synthetic facts.
    simulated = copy.deepcopy(plays)
    simulated[1]['connection'] = 'local'
    simulated[1]['tasks'] = [{'name': 'Synthetic node discovery', 'ansible.builtin.set_fact': {
        'ip': "{{ {'node1': '10.10.0.11', 'node2': '10.10.0.12', 'node3': '10.10.0.13'}[inventory_hostname] }}"}}]
    test_play = temp / 'scripts/preflight.yaml'
    test_play.parent.mkdir()
    test_play.write_text(yaml.safe_dump(simulated, sort_keys=False))
    command = [str(ansible), '-i', 'localhost,', str(test_play)]
    for name in required:
        absent = dict(env)
        del absent[name]
        result = subprocess.run(command, env=absent, text=True, capture_output=True)
        assert result.returncode != 0 and name in result.stdout + result.stderr, (name, result.stdout, result.stderr)
        assert 'Synthetic node discovery' not in result.stdout
    for change in ({'NODE1_PUBLIC_IP': env['NODE2_PUBLIC_IP']}, {'ANSIBLE_USER': 'bad user'},
                   {'SSH_KEY': 'relative'}, {'FIREWALL_ADMIN_SOURCES': '203.0.113.10,'},
                   {'FIREWALL_PRIVATE_INTERFACE': 'eth0'}, {'KUBE_PODS_SUBNET': '10.233.0.0/18'}):
        result = subprocess.run(command, env=dict(env, **change), text=True, capture_output=True)
        assert result.returncode != 0 and 'Synthetic node discovery' not in result.stdout, change
    result = subprocess.run(command, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    generated = temp / '.local/discovered.env'
    content = generated.read_text()
    assert content.splitlines()[1:] == [f'NODE{i}_PRIVATE_IP=10.10.0.{i + 10}' for i in range(1, 4)]
    assert generated.stat().st_mode & 0o777 == 0o600
    assert generated.parent.stat().st_mode & 0o777 == 0o700
    render_env = dict(env, **dict(line.split('=', 1) for line in content.splitlines()[1:]))
    inventory = temp / 'inventory'
    subprocess.run(['bash', str(root / 'scripts/render_inventory.sh'), '', str(inventory)],
                   env=render_env, check=True, capture_output=True)
    subprocess.run([str(root / '.venv/bin/python'), str(root / 'tests/check_private_inventory.py'),
                    str(inventory / 'hosts.yaml')], env=env, check=True, capture_output=True)
    simulated[1]['tasks'].append({'ansible.builtin.fail': {'msg': 'synthetic failure'},
                                  'when': "inventory_hostname == 'node3'"})
    test_play.write_text(yaml.safe_dump(simulated, sort_keys=False))
    result = subprocess.run(command, env=env, text=True, capture_output=True)
    assert result.returncode != 0 and generated.read_text() == content
    simulated[1]['tasks'].pop()
    simulated[1]['tasks'][0]['ansible.builtin.set_fact']['ip'] = '10.10.0.11'
    test_play.write_text(yaml.safe_dump(simulated, sort_keys=False))
    result = subprocess.run(command, env=env, text=True, capture_output=True)
    assert result.returncode != 0 and generated.read_text() == content
    simulated[1]['tasks'][0]['ansible.builtin.set_fact']['ip'] = "{{ {'node1': '10.233.64.1', 'node2': '10.10.0.12', 'node3': '10.10.0.13'}[inventory_hostname] }}"
    test_play.write_text(yaml.safe_dump(simulated, sort_keys=False))
    result = subprocess.run(command, env=env, text=True, capture_output=True)
    assert result.returncode != 0 and generated.read_text() == content
print('PASS: preflight input guards, node assertions, discovery output and inventory integration (local only)')
