import ipaddress
import json
from pathlib import Path
import subprocess
import sys


root = Path(__file__).resolve().parents[1]
inventory = json.loads(subprocess.check_output([
    str(root / ".venv/bin/ansible-inventory"),
    "-i", sys.argv[1], "--list",
], text=True))
hosts = inventory["_meta"]["hostvars"]
assert len(hosts) == 3
for group in ("kube_control_plane", "etcd", "kube_node"):
    assert set(inventory[group]["hosts"]) == set(hosts)

private_networks = tuple(map(ipaddress.ip_network, (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
)))
addresses = set()
for host in hosts.values():
    address = ipaddress.ip_address(host["ip"])
    assert any(address in network for network in private_networks)
    assert host["access_ip"] == str(address)
    assert host["ansible_host"] != str(address)
    assert host["kube_network_plugin"] == "cilium"
    assert host["kube_owner"] == "root"
    assert host["cilium_tunnel_mode"] == "vxlan"
    assert host["cilium_kube_proxy_replacement"] is False
    assert host["cilium_operator_replicas"] == 2
    assert host["kube_version"] == "1.36.4"
    assert host["container_manager"] == "containerd"
    assert host["kube_proxy_mode"] == "ipvs"
    assert host["kubelet_rotate_server_certificates"] is True
    assert host["kubelet_csr_approver_enabled"] is False
    assert host["metrics_server_enabled"] is False
    assert host["cert_manager_enabled"] is False
    assert host["kubeconfig_localhost"] is True
    assert host["kubeconfig_localhost_ansible_host"] is True
    assert host["kubectl_localhost"] is True
    assert host["loadbalancer_apiserver_localhost"] is True
    assert host["kube_apiserver_bind_address"] == "::"
    for setting in ("kube_vip_enabled", "kube_vip_controlplane_enabled",
                    "kube_vip_arp_enabled", "kube_vip_bgp_enabled",
                    "kube_vip_services_enabled"):
        assert host[setting] is False
    assert "loadbalancer_apiserver" not in host
    assert set(host["supplementary_addresses_in_ssl_keys"]) == {
        node["ansible_host"] for node in hosts.values()
    }
    pod_network = ipaddress.ip_network(host["kube_pods_subnet"])
    service_network = ipaddress.ip_network(host["kube_service_addresses"])
    assert not pod_network.overlaps(service_network)
    assert address not in pod_network and address not in service_network
    addresses.add(address)
assert len(addresses) == len(hosts)
print("PASS: private inventory, three-node API access, public SANs and Cilium settings")
