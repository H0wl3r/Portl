#!/usr/bin/env python3
"""Production Portl launcher CLI.

This script is intentionally dependency-free and only manages the released
Docker image through Docker Compose. Local builds and development commands live
in dev.py.
"""

from __future__ import annotations

import argparse
import os
import platform
import secrets
import socket
import string
import subprocess
import sys
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

    update_parser = subparsers.add_parser("update", help="Pull the latest image, then restart Portl.")
    update_parser.set_defaults(func=update)

    logs_parser = subparsers.add_parser("logs", help="Follow app logs.")
    logs_parser.add_argument("--service", default="app", help="Compose service to show logs for.")
    logs_parser.add_argument("--tail", default="200", help="Number of recent log lines to show.")
    logs_parser.set_defaults(func=logs)

    subparsers.add_parser("status", help="Show container status.").set_defaults(func=status)
    subparsers.add_parser("doctor", help="Run startup checks without starting containers.").set_defaults(func=doctor)

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
    compose(args, "pull", "db", "app")
    compose(args, "up", "-d", "--no-build")
    env = read_env(args.env_file)
    port = args.port or int(env_value(env, "PORTL_PORT", "5000"))
    print(f"{APP_NAME} updated and running at http://localhost:{port}")


def logs(args: argparse.Namespace) -> None:
    run_checks(args, docker_required=True, include_env_check=False)
    compose(args, "logs", "-f", "--tail", str(args.tail), args.service)


def status(args: argparse.Namespace) -> None:
    run_checks(args, docker_required=True, include_env_check=False)
    compose(args, "ps")


def doctor(args: argparse.Namespace) -> None:
    run_checks(args, docker_required=True, include_port_check=True)
    print("All checks passed.")


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
        raise CliError("Docker is installed, but the Docker daemon is not running.") from exc


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
