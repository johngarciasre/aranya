import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    temp = Path(directory)
    key = temp / "ssh key"
    key.write_text("test-only")
    key.chmod(0o600)
    env_file = temp / "production.env"
    env_file.write_text(f'''NODE1_PUBLIC_IP=198.51.100.11
NODE2_PUBLIC_IP=198.51.100.12
NODE3_PUBLIC_IP=198.51.100.13
NODE1_PRIVATE_IP=10.10.0.11
NODE2_PRIVATE_IP=10.10.0.12
NODE3_PRIVATE_IP=10.10.0.13
ANSIBLE_USER=ubuntu
SSH_KEY="{key}"
FIREWALL_ADMIN_SOURCES=203.0.113.10,203.0.113.11
FIREWALL_PRIVATE_INTERFACE=enp8s0
FIREWALL_PUBLIC_INTERFACE=enp9s0
KUBE_PODS_SUBNET=10.240.0.0/16
KUBE_SERVICE_ADDRESSES=10.241.0.0/16
''')
    output = temp / "inventory"
    subprocess.run([
        "bash", str(root / "scripts/render_inventory.sh"),
        str(env_file), str(output),
    ], check=True)
    inventory = json.loads(subprocess.check_output([
        str(root / ".venv/bin/ansible-inventory"),
        "-i", str(output / "hosts.yaml"), "--list",
    ], text=True))
    hosts = inventory["_meta"]["hostvars"]
    assert hosts["node1"]["ansible_host"] == "198.51.100.11"
    assert hosts["node3"]["ip"] == hosts["node3"]["access_ip"] == "10.10.0.13"
    assert set(hosts) == {"node1", "node2", "node3"}
    all_vars = yaml.safe_load((output / "group_vars/all.yaml").read_text())
    assert all_vars["ansible_user"] == "ubuntu"
    assert all_vars["firewall_admin_sources"] == ["203.0.113.10", "203.0.113.11"]
    cluster_vars = yaml.safe_load((output / "group_vars/k8s_cluster.yaml").read_text())
    assert cluster_vars["kube_pods_subnet"] == "10.240.0.0/16"
    assert cluster_vars["kube_service_addresses"] == "10.241.0.0/16"
    assert cluster_vars["supplementary_addresses_in_ssl_keys"] == [
        "198.51.100.11", "198.51.100.12", "198.51.100.13",
    ]
    assert all_vars["ansible_ssh_private_key_file"] == str(key)
    assert all(path.stat().st_mode & 0o077 == 0 for path in output.rglob("*"))
    subprocess.run([
        str(root / ".venv/bin/python"), str(root / "tests/check_private_inventory.py"),
        str(output / "hosts.yaml"),
    ], check=True)
    firewall = subprocess.run([
        str(root / ".venv/bin/python"), str(root / "tests/check_firewall.py"),
        os.path.relpath(output / "hosts.yaml", root),
    ], cwd=root, capture_output=True, text=True, check=True)
    assert "Unable to parse" not in firewall.stderr and "No inventory was parsed" not in firewall.stderr
    shell_env = dict(os.environ, NODE1_PUBLIC_IP="198.51.100.11", NODE2_PUBLIC_IP="198.51.100.12",
                     NODE3_PUBLIC_IP="198.51.100.13", NODE1_PRIVATE_IP="10.10.0.11",
                     NODE2_PRIVATE_IP="10.10.0.12", NODE3_PRIVATE_IP="10.10.0.13",
                     ANSIBLE_USER="ubuntu", SSH_KEY=str(key),
                     FIREWALL_ADMIN_SOURCES="203.0.113.10,203.0.113.11",
                     FIREWALL_PRIVATE_INTERFACE="enp8s0", FIREWALL_PUBLIC_INTERFACE="enp9s0",
                     KUBE_PODS_SUBNET="10.240.0.0/16", KUBE_SERVICE_ADDRESSES="10.241.0.0/16")
    from_env = temp / "from-env"
    subprocess.run([
        "bash", str(root / "scripts/render_inventory.sh"), "", str(from_env),
    ], check=True, env=shell_env)
    for path in ("hosts.yaml", "group_vars/all.yaml", "group_vars/k8s_cluster.yaml"):
        assert (from_env / path).read_text() == (output / path).read_text()
    default_env = dict(shell_env)
    del default_env["KUBE_PODS_SUBNET"], default_env["KUBE_SERVICE_ADDRESSES"]
    defaults = temp / "defaults"
    subprocess.run([
        "bash", str(root / "scripts/render_inventory.sh"), "", str(defaults),
    ], check=True, env=default_env)
    default_vars = yaml.safe_load((defaults / "group_vars/k8s_cluster.yaml").read_text())
    assert default_vars["kube_pods_subnet"] == "10.233.64.0/18"
    assert default_vars["kube_service_addresses"] == "10.233.0.0/18"
    del default_env["NODE3_PUBLIC_IP"]
    missing_output = temp / "missing"
    missing = subprocess.run([
        "bash", str(root / "scripts/render_inventory.sh"), "", str(missing_output),
    ], capture_output=True, text=True, env=default_env)
    assert missing.returncode != 0 and "NODE3_PUBLIC_IP" in missing.stderr
    assert not missing_output.exists()
    before = (output / "hosts.yaml").read_text()
    rerun = subprocess.run([
        "bash", str(root / "scripts/render_inventory.sh"), str(env_file), str(output),
    ], capture_output=True, text=True)
    assert rerun.returncode != 0 and "Refusing to overwrite" in rerun.stderr
    assert (output / "hosts.yaml").read_text() == before
    invalid_env = temp / "invalid.env"
    invalid_env.write_text(env_file.read_text().replace("FIREWALL_ADMIN_SOURCES=203.0.113.10,203.0.113.11", "FIREWALL_ADMIN_SOURCES=invalid"))
    invalid_output = temp / "invalid-inventory"
    invalid = subprocess.run([
        "bash", str(root / "scripts/render_inventory.sh"), str(invalid_env), str(invalid_output),
    ], capture_output=True, text=True)
    assert invalid.returncode != 0 and not invalid_output.exists()
    invalid_env.write_text(env_file.read_text().replace("KUBE_SERVICE_ADDRESSES=10.241.0.0/16", "KUBE_SERVICE_ADDRESSES=10.240.0.0/16"))
    invalid = subprocess.run([
        "bash", str(root / "scripts/render_inventory.sh"), str(invalid_env), str(invalid_output),
    ], capture_output=True, text=True)
    assert invalid.returncode != 0 and not invalid_output.exists()
print("PASS: env inputs render a private inventory with derived SANs and reject invalid or destructive reruns")
