#!/usr/bin/env python3
"""Read-only checks on the EXISTING Linux host; no install, firewall, or cloud API calls."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys

GIB = 1024 ** 3


def command(arguments):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=20, check=False)
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def existing_parent(path):
    path = path.resolve()
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path("/var/lib/ecoguard/models"))
    parser.add_argument("--stage-dir", type=Path, default=Path("/opt/ecoguard"))
    parser.add_argument("--image", help="Optional already-built local image tag/digest to inspect; never pulled")
    args = parser.parse_args()
    failures = []

    def check(label, passed, detail):
        print(f"{'PASS' if passed else 'BLOCK'} {label}: {detail}")
        if not passed:
            failures.append(label)

    check("OS", platform.system() == "Linux", platform.system())
    if platform.system() != "Linux":
        return 2
    release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        key, _, value = line.partition("=")
        release[key] = value.strip('"')
    check("Distribution", release.get("ID") in {"ubuntu", "debian"}, release.get("PRETTY_NAME", "unknown"))
    check("Python", sys.version_info >= (3, 10), platform.python_version())
    arch = platform.machine().lower()
    check("CPU architecture", arch in {"x86_64", "amd64"},
          f"{arch}; CPU cores={os.cpu_count()}. Current requirements lock targets linux/amd64; ARM needs a reviewed native lock/image")
    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    available = int(memory["MemAvailable"].split()[0]) * 1024
    # Respect a cgroup v2 ceiling if this supposedly existing VM shell is itself container-limited.
    limit_file, current_file = Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory.current")
    if limit_file.exists() and current_file.exists():
        limit = limit_file.read_text().strip()
        if limit != "max":
            available = min(available, max(0, int(limit) - int(current_file.read_text())))
    check("Available RAM", available >= 8 * GIB, f"{available / GIB:.2f} GiB available; at least 8 GiB required, swap excluded")

    paths = [("Model cache disk", args.cache_dir), ("Staging disk", args.stage_dir)]
    docker_status, docker_output = command(["docker", "info", "--format", "{{json .}}"])
    check("Docker engine", docker_status == 0, "reachable" if docker_status == 0 else "not installed/reachable; no automatic installation")
    if docker_status == 0:
        engine = json.loads(docker_output)
        paths.append(("Docker image disk", Path(engine["DockerRootDir"])))
        check("Docker platform", engine.get("OSType") == "linux", str(engine.get("OSType")))
    for label, path in paths:
        nearest = existing_parent(path)
        free = shutil.disk_usage(nearest).free
        check(label, free >= 8 * GIB, f"{free / GIB:.2f} GiB free at {nearest}; minimum 8 GiB (not a build-size guarantee)")
    version_status, version = command(["docker", "compose", "version", "--short"])
    try:
        version_numbers = tuple(int(part) for part in version.lstrip("v").split("-")[0].split(".")[:2])
    except ValueError:
        version_numbers = (0,)
    check("Compose", version_status == 0 and version_numbers >= (2, 24), version or "not installed; v2.24+ required")
    check("systemd", Path("/run/systemd/system").is_dir() and shutil.which("systemctl") is not None,
          "unit template requires a systemd host")
    check("Docker executable", Path("/usr/bin/docker").is_file(), "unit template uses /usr/bin/docker")
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", 18080))
            check("Private port", True, "127.0.0.1:18080 is available; no service started")
        except OSError:
            check("Private port", False, "18080 is in use; inspect the existing owner, never kill it automatically")
    for service in ("nginx", "caddy", "apache2"):
        status, output = command(["systemctl", "is-active", service])
        print(f"INFO Existing TLS candidate {service}: {output or 'not found'}; preserve all existing sites")
    if args.image:
        status, output = command(["docker", "image", "inspect", args.image, "--format", "{{.Os}}/{{.Architecture}}"])
        check("Local image", status == 0 and output == "linux/amd64", output or "absent; no pull attempted")
    else:
        print("PENDING Local image architecture/inference smoke: repeat with --image after building the reviewed image")
    print(json.dumps({"status": "blocked" if failures else "host-preflight-passed",
                      "blockedChecks": failures, "cloudAcceptance": False}))
    return 2 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print(f"Preflight blocked: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(2)
