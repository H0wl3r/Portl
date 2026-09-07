#!/usr/bin/env python3
"""Production Portl launcher CLI.

This script is intentionally dependency-free and only manages the released
Docker image through Docker Compose. Local builds and development commands live
in dev.py.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import platform
import secrets
import shlex
import socket
import string
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


APP_NAME = "Portl"
ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
COMPOSE_FILE = ROOT / "docker-compose.yml"
DEFAULT_PORTL_IMAGE = "ghcr.io/h0wl3r/portl:latest"


class CliError(RuntimeError):
    """Expected command failure with a user-friendly message."""


def main() -> int:
    parser = argparse.ArgumentParser(prog="portl", description="Manage the Portl web app.")
    parser.add_argument("--compose-file", default=str(COMPOSE_FILE), help="Path to docker-compose.yml.")
    parser.add_argument("--env-file", default=str(ENV_FILE), help="Path to .env.")
    parser.add_argument("--port", type=int, default=None, help="Override PORTL_PORT for checks.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Check, pull, and start Portl.")
    start_parser.add_argument("--no-open", action="store_true", help="Do not print the browser URL.")
    start_parser.set_defaults(func=start)

    subparsers.add_parser("stop", help="Stop Portl containers.").set_defaults(func=stop)
    subparsers.add_parser("restart", help="Restart Portl containers.").set_defaults(func=restart)

    update_parser = subparsers.add_parser("update", help="Update launcher, Compose, and Docker image, then restart Portl.")
    update_parser.add_argument("--image-only", action="store_true", help="Keep local launcher and Compose files unchanged.")
    update_parser.set_defaults(func=update)

    logs_parser = subparsers.add_parser("logs", help="Follow app logs.")
    logs_parser.add_argument("--service", default="app", help="Compose service to show logs for.")
    logs_parser.add_argument("--tail", default="200", help="Number of recent log lines to show.")
    logs_parser.set_defaults(func=logs)

    subparsers.add_parser("status", help="Show container status.").set_defaults(func=status)
    subparsers.add_parser("doctor", help="Run startup checks without starting containers.").set_defaults(func=doctor)
    uninstall_parser = subparsers.add_parser("uninstall", help="Remove Portl containers, data, images, and installed files.")
    uninstall_parser.add_argument("--dry-run", action="store_true", help="Preview removal without changing anything.")
    uninstall_parser.add_argument("--yes", action="store_true", help="Confirm permanent removal without prompting.")
    uninstall_parser.set_defaults(func=uninstall)

    args = parser.parse_args()
    args.compose_file = Path(args.compose_file).resolve()
    args.env_file = Path(args.env_file).resolve()

    try:
        args.func(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return 0


def start(args: argparse.Namespace) -> None:
    run_checks(args, docker_required=True, include_port_check=True)
    compose(args, "pull", "db", "app")
    compose(args, "up", "-d", "--no-build")
    if not args.no_open:
        env = read_env(args.env_file)
        port = args.port or int(env_value(env, "PORTL_PORT", "5000"))
        print(f"{APP_NAME} is starting at http://localhost:{port}")


def stop(args: argparse.Namespace) -> None:
    run_checks(args, docker_required=True, include_env_check=False)
    compose(args, "down")


def restart(args: argparse.Namespace) -> None:
    run_checks(args, docker_required=True, include_port_check=False)
    compose(args, "restart")


def update(args: argparse.Namespace) -> None:
    run_checks(args, docker_required=True, include_port_check=True)
    checkout = any((parent / ".git").exists() for parent in (ROOT, *ROOT.parents))
    custom_compose = args.compose_file.resolve() != (ROOT / "docker-compose.yml").resolve()
    image = os.environ.get("PORTL_IMAGE") or env_value(read_env(args.env_file), "PORTL_IMAGE", DEFAULT_PORTL_IMAGE)
    if args.image_only or checkout or custom_compose or not image.startswith("ghcr.io/h0wl3r/portl:"):
        if not args.image_only:
            print("Keeping local launcher/Compose files for this source checkout or custom configuration.")
        compose(args, "pull", "db", "app")
        compose(args, "up", "-d", "--no-build", "--wait", "--wait-timeout", "120")
    else:
        update_installation(args, image)
    env = read_env(args.env_file)
    port = args.port or int(env_value(env, "PORTL_PORT", "5000"))
    print(f"{APP_NAME} updated and running at http://localhost:{port}")


def download_update(url: str) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": "Portl-launcher", "Cache-Control": "no-cache",
        "Accept": "application/vnd.github+json" if url.startswith("https://api.github.com/") else "*/*",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(2 * 1024 * 1024 + 1)
        if not data or len(data) > 2 * 1024 * 1024:
            raise CliError("Update download was empty or unexpectedly large.")
        return data
    except (OSError, urllib.error.URLError) as exc:
        raise CliError(f"Could not download update from {url}: {exc}") from exc


def update_snapshot(image: str) -> str:
    tag = image.rsplit(":", 1)[1]
    ref = "main" if tag == "latest" else (tag if tag.startswith("v") else "v" + tag)
    # Resolve once, then fetch both files from that immutable commit.
    from urllib.parse import quote
    url = f"https://api.github.com/repos/H0wl3r/portl/commits/{quote(ref, safe='')}?check={time.time_ns()}"
    try:
        sha = json.loads(download_update(url))["sha"]
        if not isinstance(sha, str) or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("invalid commit")
        return sha
    except (ValueError, KeyError, TypeError) as exc:
        raise CliError("GitHub did not return a valid update commit.") from exc


def update_installation(args: argparse.Namespace, image: str) -> None:
    lock = ROOT / ".portl-update.lock"
    try:
        lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise CliError(f"Another update may be running. If none is running, remove the stale lock: {lock}") from exc
    except OSError as exc:
        raise CliError(f"Cannot write to the installation directory: {exc}") from exc
    os.close(lock_fd)
    try:
        sha = update_snapshot(image)
        print(f"Downloading launcher and Compose from public Portl commit {sha[:12]}.")
        with tempfile.TemporaryDirectory(prefix=".portl-update-", dir=ROOT) as temporary:
            stage = Path(temporary)
            for name in ("portl.py", "docker-compose.yml"):
                (stage / name).write_bytes(download_update(
                    f"https://raw.githubusercontent.com/H0wl3r/portl/{sha}/{name}"))
            try:
                tree = ast.parse((stage / "portl.py").read_bytes(), filename="portl.py")
                compile(tree, "portl.py", "exec")
                if not any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body):
                    raise ValueError("missing launcher main function")
            except (SyntaxError, ValueError) as exc:
                raise CliError(f"Downloaded launcher is invalid: {exc}") from exc
            candidate_compose = ["docker", "compose", "--project-directory", str(ROOT),
                                 "--env-file", str(args.env_file), "-f", str(stage / "docker-compose.yml")]
            run([*candidate_compose, "config", "--quiet"])
            # Download images before replacing files or touching running containers.
            run([*candidate_compose, "pull", "db", "app"])
            originals = {}
            for name in ("portl.py", "docker-compose.yml"):
                target = ROOT / name
                if target.is_symlink():
                    raise CliError(f"Refusing to overwrite a symlink: {target}. Use update --image-only.")
                originals[name] = (target.read_bytes(), target.stat().st_mode & 0o777)
            replaced = []
            try:
                for name, (_, mode) in originals.items():
                    (stage / name).chmod(mode)
                    os.replace(stage / name, ROOT / name)
                    replaced.append(name)
                compose(args, "up", "-d", "--no-build", "--wait", "--wait-timeout", "120")
            except (OSError, CliError) as exc:
                for name in replaced:
                    content, mode = originals[name]
                    backup = stage / (name + ".restore")
                    backup.write_bytes(content)
                    backup.chmod(mode)
                    os.replace(backup, ROOT / name)
                raise CliError("Update failed; previous launcher and Compose files were restored. "
                               f"If container startup was attempted, check portl logs. {exc}") from exc
        print("Launcher and Compose updated. Existing .env settings were preserved.")
    except OSError as exc:
        raise CliError(f"Could not update installation files: {exc}") from exc
    finally:
        lock.unlink(missing_ok=True)


def logs(args: argparse.Namespace) -> None:
    run_checks(args, docker_required=True, include_env_check=False)
    compose(args, "logs", "-f", "--tail", str(args.tail), args.service)


def status(args: argparse.Namespace) -> None:
    run_checks(args, docker_required=True, include_env_check=False)
    compose(args, "ps")


def doctor(args: argparse.Namespace) -> None:
    run_checks(args, docker_required=True, include_port_check=True)
    print("All checks passed.")


PORTL_PROJECTS = {"portl", "portl-dev", "portl-ci"}
PORTL_IMAGE_REPOS = {"ghcr.io/h0wl3r/portl", "portl-app", "portl-candidate"}


def docker_json(*args: str) -> list[dict]:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise CliError(f"Docker inventory failed: {result.stderr.strip()}")
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def docker_inventory(kind: str) -> list[dict]:
    command = ["docker", kind, "ls", "-q"]
    if kind == "container":
        command.append("-a")
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise CliError(f"Could not list Docker {kind}s: {result.stderr.strip()}")
    ids = list(dict.fromkeys(result.stdout.split()))
    objects = []
    for identifier in ids:
        objects.extend(docker_json(kind, "inspect", "--format", "{{json .}}", identifier))
    return objects


def portl_image(reference: str) -> bool:
    return reference.split("@", 1)[0].split(":", 1)[0] in PORTL_IMAGE_REPOS


def plan_docker_uninstall(containers: list[dict], volumes: list[dict], networks: list[dict], images: list[dict]) -> dict:
    projects = set(PORTL_PROJECTS)
    for container in containers:
        if portl_image(container["Config"].get("Image", "")):
            project = (container["Config"].get("Labels") or {}).get("com.docker.compose.project")
            if project:
                projects.add(project)
    selected = [c for c in containers if portl_image(c["Config"].get("Image", "")) or
                (c["Config"].get("Labels") or {}).get("com.docker.compose.project") in projects]
    selected_ids = {c["Id"] for c in selected}
    others = [c for c in containers if c["Id"] not in selected_ids]
    mounted = {m["Name"] for c in selected for m in c.get("Mounts", []) if m["Type"] == "volume"}
    selected_volumes = [v["Name"] for v in volumes if v["Name"] in mounted or
                        (v.get("Labels") or {}).get("com.docker.compose.project") in projects or
                        v["Name"] in {"portl_portl_db", "portl-dev_portl_db", "portl-ci_portl_db", "portl_ssh_manager_db"}]
    shared = {m["Name"] for c in others for m in c.get("Mounts", []) if m["Type"] == "volume"}
    if shared.intersection(selected_volumes):
        raise CliError("Uninstall stopped: another Docker project uses these Portl volumes: " +
                       ", ".join(sorted(shared.intersection(selected_volumes))))
    selected_networks = [n["Id"] for n in networks if
                         (n.get("Labels") or {}).get("com.docker.compose.project") in projects]
    for network in networks:
        if network["Id"] in selected_networks and set(network.get("Containers", {})) - selected_ids:
            raise CliError(f"Uninstall stopped: network {network['Name']} is shared with other containers.")
    used_images = {c["Image"] for c in others}
    app_image_ids = {c["Image"] for c in selected if portl_image(c["Config"].get("Image", ""))}
    remove_images, retained_images = [], []
    for image in images:
        tags = image.get("RepoTags") or []
        owned_tags = [tag for tag in tags if portl_image(tag)]
        # PostgreSQL is shared software: remove its tag only when no other container uses it.
        if "postgres:16-alpine" in tags and (selected or selected_volumes):
            owned_tags.append("postgres:16-alpine")
        targets = owned_tags or ([image["Id"]] if not tags and image["Id"] in app_image_ids else [])
        if image["Id"] in used_images:
            retained_images.extend(targets)
        else:
            remove_images.extend(targets)
    return {"containers": sorted(selected_ids), "volumes": sorted(selected_volumes),
            "networks": sorted(selected_networks), "images": sorted(set(remove_images)),
            "names": {**{c["Id"]: c.get("Name", c["Id"]).lstrip('/') for c in selected},
                      **{n["Id"]: n["Name"] for n in networks}},
            "retained_images": sorted(set(retained_images))}


def installation_paths() -> tuple[list[Path], list[Path]]:
    home = Path.home()
    if os.name != "nt" and os.environ.get("SUDO_USER"):
        import pwd
        home = Path(pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir)
    roots = {ROOT, home / ".local/share/portl"}
    bins = {ROOT / "bin", home / ".local/bin"}
    if os.name == "nt":
        if os.environ.get("LOCALAPPDATA"):
            roots.add(Path(os.environ["LOCALAPPDATA"]) / "Portl")
    else:
        roots.add(Path("/srv/portl"))
        bins.add(Path("/usr/local/bin"))
    bins.update(Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p)
    for root in list(roots):
        marker = root / ".portl-install.json"
        if marker.is_file():
            try:
                record = json.loads(marker.read_text(encoding="utf-8-sig"))
                if Path(record["install_dir"]).resolve() == root.resolve():
                    bins.add(Path(record["bin_dir"]))
            except (ValueError, KeyError, OSError):
                pass
    return sorted(roots), sorted(bins)


def plan_file_uninstall(roots: list[Path], bins: list[Path]) -> tuple[list[Path], list[Path]]:
    files, directories = set(), set()
    valid_roots = []
    for root in roots:
        root = root.absolute()
        if root.is_symlink() or any((parent / ".git").exists() for parent in (root, *root.parents)):
            continue
        launcher = root / "portl.py"
        if launcher.is_file() and not launcher.is_symlink():
            content = launcher.read_text(encoding="utf-8")
            if 'APP_NAME = "Portl"' not in content or "DEFAULT_PORTL_IMAGE" not in content:
                continue
            valid_roots.append(root)
            directories.add(root)
            for name in ("portl.py", "docker-compose.yml", ".env", ".portl-install.json"):
                path = root / name
                if path.is_file() or path.is_symlink():
                    files.add(path)
            cache = root / "__pycache__"
            if cache.is_dir() and not cache.is_symlink():
                files.update(cache.glob("portl.*.pyc"))
                directories.add(cache)
    # Old wrappers may point to a default installation that no longer exists.
    wrapper_roots = [*valid_roots, *(r.absolute() for r in roots if not r.exists())]

    def points_to_portl(content: str) -> bool:
        return any(value in content for r in wrapper_roots for value in (
            str(r / "portl.py"), shlex.quote(str(r / "portl.py")), str(r / "portl.py").replace('%', '%%')))

    for directory in {*bins, *(r / "bin" for r in valid_roots)}:
        if any((parent / ".git").exists() for parent in (directory, *directory.parents)):
            continue
        for name in ("portl", "portl.cmd"):
            wrapper = directory.absolute() / name
            if wrapper.is_symlink():
                target = wrapper.resolve()
                owned = target.is_file() and (any(target == r / "portl.py" for r in valid_roots) or
                        (target.name in {"portl", "portl.cmd"} and points_to_portl(target.read_text(errors="replace"))))
            elif wrapper.is_file():
                content = wrapper.read_text(errors="replace")
                owned = points_to_portl(content)
            else:
                owned = False
            if owned:
                files.add(wrapper)
                if directory in {r / "bin" for r in valid_roots}:
                    directories.add(directory)
    return sorted(files), sorted(directories, key=lambda p: len(p.parts), reverse=True)


def remove_windows_path(directories: list[Path]) -> None:
    if os.name != "nt":
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        try:
            value, value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return
        targets = {os.path.normcase(str(p.resolve())) for p in directories}
        entries = [entry for entry in value.split(";") if os.path.normcase(os.path.abspath(
            os.path.expandvars(entry.strip().strip('"')))) not in targets]
        winreg.SetValueEx(key, "Path", 0, value_type, ";".join(entries))
    import ctypes
    result = ctypes.c_size_t()
    ctypes.windll.user32.SendMessageTimeoutW(0xffff, 0x001A, 0, "Environment", 2, 2000, ctypes.byref(result))


def finish_windows_uninstall(wrappers: list[Path], directories: list[Path]) -> None:
    """Let cmd.exe finish reading its wrapper before unlinking that exact file."""
    helper = r'''
import ctypes, json, sys, time
from pathlib import Path
parent, wrappers, directories = json.loads(sys.argv[1])
kernel = ctypes.windll.kernel32
kernel.OpenProcess.restype = ctypes.c_void_p
handle = kernel.OpenProcess(0x00100000, False, parent)
if handle:
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject(handle, 30000)
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle(handle)
time.sleep(1)
for name in wrappers:
    path = Path(name)
    if not path.is_absolute() or path.name != 'portl.cmd':
        continue
    for attempt in range(20):
        try:
            path.unlink(missing_ok=True)
            break
        except PermissionError:
            time.sleep(0.25)
for name in directories:
    path = Path(name)
    if path.is_absolute() and path != Path(path.anchor):
        try:
            path.rmdir()
        except OSError:
            pass
'''
    payload = json.dumps([os.getpid(), [str(p.absolute()) for p in wrappers],
                          [str(p.absolute()) for p in directories]])
    subprocess.Popen([sys.executable, "-c", helper, payload], cwd=os.environ.get("TEMP", str(Path.home())),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)


def uninstall(args: argparse.Namespace) -> None:
    ensure_docker()
    plan = plan_docker_uninstall(*(docker_inventory(kind) for kind in ("container", "volume", "network", "image")))
    roots, bins = installation_paths()
    files, directories = plan_file_uninstall(roots, bins)
    print("Portl uninstall permanently deletes its saved hosts, users, keys, database, and local settings.")
    for kind in ("containers", "volumes", "networks", "images"):
        print(f"{kind.capitalize()} to remove:")
        for name in plan[kind]:
            print(f"  {plan['names'].get(name, name)}")
    print("Installation files to remove:")
    for path in files:
        print(f"  {path}")
    for image in plan["retained_images"]:
        print(f"Keeping shared image used by another container: {image}")
    print("Git checkouts and unknown files are preserved. Empty installation directories are removed.")
    if args.dry_run:
        return
    if not args.yes:
        try:
            confirmed = input("Type DELETE PORTL to continue: ") == "DELETE PORTL"
        except EOFError:
            confirmed = False
        if not confirmed:
            print("Uninstall cancelled. Nothing was removed.")
            return
    # Stop on any Docker failure before removing settings needed for recovery.
    for kind, command in (("containers", ["container", "rm", "-f"]),
                          ("volumes", ["volume", "rm"]), ("networks", ["network", "rm"]),
                          ("images", ["image", "rm"])):
        for identifier in plan[kind]:
            run(["docker", *command, identifier])
    errors = []
    wrapper_dirs = []
    deferred_wrappers = []
    for path in files:
        try:
            if os.name == "nt" and path.name == "portl.cmd":
                deferred_wrappers.append(path)
            else:
                path.unlink(missing_ok=True)
            if path.name == "portl.cmd" and path.parent in directories and all(p in files for p in path.parent.iterdir()):
                wrapper_dirs.append(path.parent)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    remove_windows_path(wrapper_dirs)
    if deferred_wrappers:
        finish_windows_uninstall(deferred_wrappers, directories)
        print("Windows command wrapper cleanup will finish just after this command exits.")
    for directory in directories:
        if deferred_wrappers:
            continue
        try:
            directory.rmdir()  # Only empty directories; never recursively delete a source tree.
        except OSError:
            if directory.exists():
                print(f"Kept nonempty or inaccessible directory: {directory}")
    if errors:
        raise CliError("Some installation files could not be removed:\n" + "\n".join(errors))
    print("Portl uninstall complete. Restart your terminal to refresh command lookup and PATH.")


def run_checks(
    args: argparse.Namespace,
    *,
    docker_required: bool,
    include_env_check: bool = True,
    include_port_check: bool = False,
) -> None:
    ensure_file(args.compose_file, f"Compose file not found: {args.compose_file}")
    if include_env_check:
        ensure_env(args.env_file)
        ensure_release_image_configured(args.env_file)
    if docker_required:
        ensure_docker()
    if include_port_check:
        env = read_env(args.env_file)
        port = args.port or int(env_value(env, "PORTL_PORT", "5000"))
        ensure_port_available(args, port)


def ensure_env(env_file: Path) -> None:
    if not env_file.exists():
        env_text, initial_password = default_env_text()
        env_file.write_text(env_text, encoding="utf-8")
        print(f"Created {env_file} with generated secrets.")
        print("Initial Portl login:")
        print("  Username: admin")
        print(f"  Password: {initial_password}")
        print(f"Saved credentials in {env_file}. Keep this file secure.")

    env = read_env(env_file)
    if not env_value(env, "PORTL_DATA_ENCRYPTION_KEY"):
        append_env_value(env_file, "PORTL_DATA_ENCRYPTION_KEY", generate_secret(64))
        env = read_env(env_file)
        print(f"Added PORTL_DATA_ENCRYPTION_KEY to {env_file}.")

    missing = [
        key
        for key in ("PORTL_AUTH_PASSWORD", "PORTL_SECRET_KEY")
        if not env_value(env, key) or env_value(env, key).startswith("change-me")
    ]
    if missing:
        keys = ", ".join(missing)
        raise CliError(f"Set {keys} in {env_file} before starting.")


def ensure_release_image_configured(env_file: Path) -> None:
    env = read_env(env_file)
    image = env_value(env, "PORTL_IMAGE", DEFAULT_PORTL_IMAGE)
    if not image or image == "portl-app:local":
        raise CliError(f"Set PORTL_IMAGE in {env_file} to the official image. Use `python dev.py start` for local builds.")


def ensure_docker() -> None:
    if not command_exists("docker"):
        raise CliError("Docker is not installed or is not on PATH.")
    run(["docker", "--version"], quiet=True)
    try:
        run(["docker", "compose", "version"], quiet=True)
    except CliError as exc:
        raise CliError("Docker Compose v2 is required. Install/update Docker Desktop or Docker Engine.") from exc
    try:
        run(["docker", "info"], quiet=True)
    except CliError as exc:
        raise CliError(
            "Cannot access the Docker daemon. Check that Docker is running and your user "
            "has permission to connect. On Linux, try running Portl with sudo. "
            f"Docker check: {exc}"
        ) from exc


def ensure_port_available(args: argparse.Namespace, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        if sock.connect_ex(("127.0.0.1", port)) != 0:
            return
    if compose_service_running(args, "app"):
        print(f"Port {port} is already in use by the running Portl app.")
        return
    raise CliError(f"Port {port} is already in use. Set PORTL_PORT in .env to another port.")


def ensure_file(path: Path, message: str) -> None:
    if not path.exists():
        raise CliError(message)


def compose(args: argparse.Namespace, *compose_args: str) -> None:
    command = ["docker", "compose", "--env-file", str(args.env_file), "-f", str(args.compose_file), *compose_args]
    run(command)


def compose_service_running(args: argparse.Namespace, service: str) -> bool:
    command = [
        "docker",
        "compose",
        "--env-file",
        str(args.env_file),
        "-f",
        str(args.compose_file),
        "ps",
        "-q",
        service,
    ]
    try:
        completed = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
    except FileNotFoundError:
        return False
    container_id = completed.stdout.strip()
    if completed.returncode != 0 or not container_id:
        return False

    inspect = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return inspect.returncode == 0 and inspect.stdout.strip().lower() == "true"


def run(command: list[str], *, quiet: bool = False, env: dict[str, str] | None = None) -> None:
    if not quiet:
        print(f"$ {' '.join(command)}")
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=quiet,
            text=quiet,
        )
    except FileNotFoundError as exc:
        raise CliError(f"Command not found: {command[0]}") from exc
    if completed.returncode != 0:
        detail = ""
        if quiet:
            output = f"{completed.stderr or ''}\n{completed.stdout or ''}".strip()
            if output:
                detail = f" ({output.splitlines()[-1]})"
        raise CliError(f"Command failed with exit code {completed.returncode}: {' '.join(command)}{detail}")


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_value(env: dict[str, str], primary: str, default: str | None = None) -> str | None:
    value = env.get(primary)
    if value not in (None, ""):
        return value
    return default


def append_env_value(path: Path, key: str, value: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    separator = "" if not existing or existing.endswith(("\n", "\r")) else "\n"
    path.write_text(f"{existing}{separator}{key}={value}\n", encoding="utf-8")


def command_exists(name: str) -> bool:
    paths = os.environ.get("PATH", "").split(os.pathsep)
    extensions = [""]
    if platform.system().lower() == "windows":
        extensions = os.environ.get("PATHEXT", ".EXE;.BAT;.CMD").split(os.pathsep)
    for directory in paths:
        for extension in extensions:
            if (Path(directory) / f"{name}{extension}").exists():
                return True
    return False


def default_env_text() -> tuple[str, str]:
    password = generate_secret(24)
    secret_key = generate_secret(64)
    data_key = generate_secret(64)
    return (
        "POSTGRES_DB=portl\n"
        "POSTGRES_USER=portl\n"
        f"POSTGRES_PASSWORD={generate_secret(24)}\n"
        f"PORTL_IMAGE={DEFAULT_PORTL_IMAGE}\n"
        "PORTL_AUTH_USERNAME=admin\n"
        f"PORTL_AUTH_PASSWORD={password}\n"
        f"PORTL_SECRET_KEY={secret_key}\n"
        f"PORTL_DATA_ENCRYPTION_KEY={data_key}\n"
        "SESSION_COOKIE_SECURE=false\n"
        "MAX_UPLOAD_BYTES=67108864\n"
        "MAX_REMOTE_READ_BYTES=16777216\n"
        "SSH_KEEPALIVE_SECONDS=15\n"
        "CONNECTION_HEALTH_CHECK_SECONDS=10\n"
        "PORTL_PORT=5000\n"
    ), password


def generate_secret(length: int) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


if __name__ == "__main__":
    raise SystemExit(main())
