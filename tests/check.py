import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

import yaml


ROOT = Path(__file__).resolve().parents[1]
HELM = ROOT / ".cache/linux-amd64/helm"
KUBESPRAY = ROOT / ".cache/kubespray"
CLUSTERDOS = ROOT / ".cache/clusterdos"


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs)


def load(path):
    return yaml.safe_load((ROOT / path).read_text())


def check(container):
    inventory = json.loads(run(
        str(ROOT / ".venv/bin/ansible-inventory"),
        "-i", str(ROOT / "inventory/example/hosts.yaml"), "--list",
    ))
    hosts = inventory["_meta"]["hostvars"]
    control_planes = inventory["kube_control_plane"]["hosts"]
    assert len(control_planes) == 3
    assert set(inventory["etcd"]["hosts"]) == set(control_planes)
    assert set(inventory["kube_node"]["hosts"]) == set(control_planes)
    for name in control_planes:
        host = hosts[name]
        assert host["ip"] == host["access_ip"]
        assert ipaddress.ip_address(host["ip"]).is_private
        assert host["ip"] != host["ansible_host"]
        assert host["kube_network_plugin"] == "cilium"
        assert host["kube_owner"] == "root"
        assert host["kube_version"] == "1.36.4"
        assert host["kubelet_rotate_server_certificates"] is True
        assert host["metrics_server_enabled"] is False
        assert host["cert_manager_enabled"] is False
        for setting in ("kube_vip_enabled", "kube_vip_controlplane_enabled",
                        "kube_vip_arp_enabled", "kube_vip_bgp_enabled",
                        "kube_vip_services_enabled"):
            assert host[setting] is False
        assert "loadbalancer_apiserver" not in host
        assert host["loadbalancer_apiserver_localhost"] is True
        assert host["kube_apiserver_bind_address"] == "::"
        assert host["kubeconfig_localhost"] is True
        assert host["kubeconfig_localhost_ansible_host"] is True
        assert host["kubectl_localhost"] is True
        assert host["supplementary_addresses_in_ssl_keys"] == ["192.0.2.11", "192.0.2.12", "192.0.2.13"]

    env = dict(os.environ, ANSIBLE_CONFIG=str(KUBESPRAY / "ansible.cfg"))
    run(str(ROOT / ".venv/bin/ansible-playbook"),
        "-i", str(ROOT / "inventory/example/hosts.yaml"),
        str(KUBESPRAY / "cluster.yml"), "--syntax-check", cwd=KUBESPRAY, env=env)
    print("PASS: inventory topology, private addresses, Kubespray syntax")

    install = load("install.yaml")
    source = install["spec"]["source"]
    values = source["helm"]["valuesObject"]
    assert values["clusterdos"]["clustername"] == "aranya-takehome"
    assert source["targetRevision"] == values["clusterdos"]["ref"] == "v0.4.8"
    chart = str(CLUSTERDOS / source["path"])
    run(str(HELM), "lint", chart, "-f", "-", input=yaml.safe_dump(values))
    applications = list(yaml.safe_load_all(run(
        str(HELM), "template", "clusterdos", chart, "-f", "-",
        input=yaml.safe_dump(values),
    )))
    applications = {app["metadata"]["name"]: app for app in applications if app}
    assert set(applications) == {
        "clusterdos-config", "clusterdos-certmanager",
        "clusterdos-metricsserver", "clusterdos-nfd",
    }
    config_source = applications["clusterdos-config"]["spec"]["source"]
    assert config_source["helm"]["valuesObject"]["clustername"] == "aranya-takehome"
    assert config_source["repoURL"] == "https://github.com/johngarciasre/aranya.git"
    assert config_source["path"] == "charts/config"
    config_chart = ROOT / config_source["path"]
    assert yaml.safe_load((config_chart / "Chart.yaml").read_text())["apiVersion"] == "v2"
    for path in ("values.yaml", "templates/clustername.yaml"):
        assert (config_chart / path).read_bytes() == (CLUSTERDOS / "gitapps/config" / path).read_bytes()
    assert (config_chart / "LICENSE").read_bytes() == (CLUSTERDOS / "LICENSE").read_bytes()
    run(str(HELM), "lint", str(config_chart), "--set", "clustername=aranya-takehome")
    config = yaml.safe_load(run(str(HELM), "template", "clusterdos-config", str(config_chart),
                               "--set", "clustername=aranya-takehome"))
    assert config["kind"] == "ConfigMap"
    assert config["metadata"]["name"] == "clustername"
    assert config["data"] == {"clustername": "aranya-takehome"}
    missing_name = subprocess.run([str(HELM), "template", "clusterdos-config", str(config_chart)],
                                  capture_output=True, text=True)
    assert missing_name.returncode != 0 and "clustername is required" in missing_name.stderr
    print("PASS: vendored config chart renders, requires clustername and preserves upstream source/license")
    for name in ("certmanager", "metricsserver", "nfd"):
        app = applications[f"clusterdos-{name}"]
        src = app["spec"]["source"]
        assert src["targetRevision"] not in ("main", "HEAD", "stable")
        rendered = list(yaml.safe_load_all(run(
            str(HELM), "template", f"clusterdos-{name}", src["chart"],
            "--repo", src["repoURL"], "--version", src["targetRevision"],
            "--namespace", app["spec"]["destination"]["namespace"],
            "--kube-version", "1.36.4", "--include-crds", "-f", "-",
            input=yaml.safe_dump(src["helm"]["valuesObject"]),
        )))
        deployments = {obj["metadata"]["name"]: obj for obj in rendered if obj and obj["kind"] == "Deployment"}
        if name == "certmanager":
            assert deployments
            assert all(obj["spec"]["replicas"] == 2 for obj in deployments.values())
            config_maps = [obj for obj in rendered if obj and obj["kind"] == "ConfigMap"]
            assert any("enableGatewayAPI: false" in str(obj["data"]) for obj in config_maps)
        if name == "metricsserver":
            deployment = deployments["clusterdos-metricsserver-metrics-server"]
            assert deployment["spec"]["replicas"] == 2
            args = deployment["spec"]["template"]["spec"]["containers"][0]["args"]
            assert "--kubelet-insecure-tls" not in args
            assert "--kubelet-certificate-authority=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt" in args
            assert "affinity" in deployment["spec"]["template"]["spec"]
            api_service = next(obj for obj in rendered if obj and obj["kind"] == "APIService")
            assert api_service["spec"].get("insecureSkipTLSVerify", False) is False
            assert "cert-manager.io/inject-ca-from" in api_service["metadata"]["annotations"]
            assert any(obj and obj["kind"] == "Certificate" for obj in rendered)
            assert "/spec/caBundle" in app["spec"]["ignoreDifferences"][0]["jsonPointers"]
        if name == "nfd":
            deployment = deployments["clusterdos-nfd-node-feature-discovery-master"]
            assert deployment["spec"]["replicas"] == 2
            assert "-enable-leader-election" in deployment["spec"]["template"]["spec"]["containers"][0]["args"]
            selector = deployment["spec"]["selector"]["matchLabels"]
            affinity = deployment["spec"]["template"]["spec"]["affinity"]
            labels = affinity["podAntiAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][0]["labelSelector"]["matchLabels"]
            assert all(selector[key] == value for key, value in labels.items())
        print(f"PASS: {name} pinned chart renders with requested settings")

    resources = list(yaml.safe_load_all((ROOT / "manifests/nginx/resources.yaml").read_text()))
    by_kind = {obj["kind"]: obj for obj in resources}
    deployment = by_kind["Deployment"]["spec"]
    pod = deployment["template"]["spec"]
    nginx = pod["containers"][0]
    assert deployment["replicas"] == 3
    assert nginx["image"].endswith("@sha256:84ed8c0dd296c188bfc24ed506f46589e9285fb0b11cf3a04d91d524d3067e11")
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert nginx["securityContext"]["readOnlyRootFilesystem"] is True
    assert nginx["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert nginx["readinessProbe"]["httpGet"]["port"] == "http"
    assert nginx["livenessProbe"]["httpGet"]["port"] == "http"
    assert by_kind["Service"]["spec"]["ports"][0]["nodePort"] == 30080
    assert by_kind["PodDisruptionBudget"]["spec"]["minAvailable"] == 2
    data = by_kind["ConfigMap"]["data"]
    assert "hello aranya" in data["index.html"]
    print("PASS: nginx manifests, security settings, probes and public NodePort")

    if container:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            config.chmod(0o755)
            for name, content in data.items():
                (config / name).write_text(content)
                (config / name).chmod(0o644)
            cid = run(
                "podman", "run", "--detach", "--read-only", "--cap-drop=ALL",
                "--user=101:101", "--security-opt=no-new-privileges",
                "--tmpfs=/tmp:rw,mode=1777,size=64m", "-p", "127.0.0.1::8080",
                "-v", f"{config / 'nginx.conf'}:/etc/nginx/nginx.conf:ro",
                "-v", f"{config / 'index.html'}:/usr/share/nginx/html/index.html:ro",
                nginx["image"], *nginx["command"], *nginx["args"],
            ).strip()
            try:
                address = run("podman", "port", cid, "8080/tcp").strip()
                for attempt in range(30):
                    try:
                        with urllib.request.urlopen(f"http://{address}/", timeout=2) as response:
                            assert response.status == 200
                            assert "hello aranya" in response.read().decode()
                        break
                    except (OSError, urllib.error.URLError):
                        if attempt == 29:
                            print(run("podman", "logs", cid))
                            raise
                        time.sleep(1)
                print("PASS: real nginx HTTP 200 and hello aranya with non-root, read-only container")
            finally:
                run("podman", "rm", "--force", cid)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local submission checks; no cluster connection.")
    parser.add_argument("--container", action="store_true", help="Also smoke-test nginx using local Podman.")
    check(parser.parse_args().container)
