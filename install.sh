#!/usr/bin/env sh
set -efu

PORTL_OS="$(uname -s)"
if [ "$PORTL_OS" = "Linux" ]; then
  PORTL_INSTALL_DIR="${PORTL_INSTALL_DIR:-/srv/portl}"
  PORTL_BIN_DIR="${PORTL_BIN_DIR:-/usr/local/bin}"
else
  PORTL_INSTALL_DIR="${PORTL_INSTALL_DIR:-$HOME/.local/share/portl}"
  PORTL_BIN_DIR="${PORTL_BIN_DIR:-$HOME/.local/bin}"
fi
PORTL_LAUNCHER_URL="${PORTL_LAUNCHER_URL:-https://raw.githubusercontent.com/H0wl3r/portl/main/portl.py}"
PORTL_COMPOSE_URL="${PORTL_COMPOSE_URL:-https://raw.githubusercontent.com/H0wl3r/portl/main/docker-compose.yml}"

echo "INFO | Installing Portl launcher into $PORTL_INSTALL_DIR"

if [ "${PORTL_SUDO_COMMAND+x}" != "x" ]; then
  PORTL_SUDO_COMMAND=""
  if [ "$PORTL_OS" = "Linux" ] && [ "$(id -u)" != "0" ]; then
    if command -v sudo >/dev/null 2>&1; then
      PORTL_SUDO_COMMAND="sudo"
    else
      echo "ERROR | Install as root or install sudo and try again." >&2
      exit 1
    fi
  fi
fi

run_privileged() {
  if [ -n "$PORTL_SUDO_COMMAND" ] && [ "$(id -u)" != "0" ]; then
    # Intentional word splitting supports commands such as 'sudo -E'. No eval.
    $PORTL_SUDO_COMMAND "$@"
  else
    "$@"
  fi
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR | python3 is required to run the Portl launcher." >&2
  exit 1
fi

if [ -n "$PORTL_SUDO_COMMAND" ] && [ "$(id -u)" != "0" ]; then
  echo "INFO | Requesting administrator access using $PORTL_SUDO_COMMAND."
fi
# sudo prompts on the terminal, leaving the piped installer input untouched.
PORTL_EFFECTIVE_UID="$(run_privileged id -u)"
run_privileged sh -c 'command -v python3 >/dev/null' || {
  echo "ERROR | Python 3 must also be available to the account running Portl." >&2
  exit 1
}

if command -v curl >/dev/null 2>&1; then
  download() {
    curl -fsSL "$1" -o "$2"
  }
elif command -v wget >/dev/null 2>&1; then
  download() {
    wget -q "$1" -O "$2"
  }
else
  echo "ERROR | curl or wget is required to download Portl." >&2
  exit 1
fi

umask 077
PORTL_STAGE="$(mktemp -d)"
cleanup() {
  rm -f "$PORTL_STAGE/portl.py" "$PORTL_STAGE/docker-compose.yml" "$PORTL_STAGE/portl" "$PORTL_STAGE/.portl-install.json"
  rmdir "$PORTL_STAGE"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
download "$PORTL_LAUNCHER_URL" "$PORTL_STAGE/portl.py"
download "$PORTL_COMPOSE_URL" "$PORTL_STAGE/docker-compose.yml"

# Quote paths as shell literals so spaces and special characters survive.
python3 - "$PORTL_INSTALL_DIR/portl.py" "$PORTL_EFFECTIVE_UID" "$PORTL_SUDO_COMMAND" > "$PORTL_STAGE/portl" <<'PY'
import shlex
import sys

launcher, owner_uid, sudo_command = sys.argv[1:]
print("#!/usr/bin/env sh\nset -efu")
print("PORTL_LAUNCHER=" + shlex.quote(launcher))
if owner_uid == "0":
    print("PORTL_DEFAULT_SUDO=" + shlex.quote(sudo_command or "sudo"))
    print('''if [ "$(id -u)" != "0" ]; then
  PORTL_SUDO_COMMAND="${PORTL_SUDO_COMMAND-$PORTL_DEFAULT_SUDO}"
  if [ -z "$PORTL_SUDO_COMMAND" ]; then
    echo "ERROR | This installation requires root. Run sudo portl or use a root shell." >&2
    exit 1
  fi
  exec $PORTL_SUDO_COMMAND python3 "$PORTL_LAUNCHER" "$@"
fi''')
print('exec python3 "$PORTL_LAUNCHER" "$@"')
PY
python3 - "$PORTL_INSTALL_DIR" "$PORTL_BIN_DIR" > "$PORTL_STAGE/.portl-install.json" <<'PY'
import json
import os
import sys
print(json.dumps(dict(zip(("install_dir", "bin_dir"), map(os.path.abspath, sys.argv[1:])))))
PY

# Keep existing settings; only replace the downloaded distribution files.
run_privileged sh -c 'umask 077; mkdir -p "$1"' sh "$PORTL_INSTALL_DIR"
run_privileged sh -c 'umask 022; mkdir -p "$1"' sh "$PORTL_BIN_DIR"
run_privileged install -m 755 "$PORTL_STAGE/portl.py" "$PORTL_INSTALL_DIR/portl.py"
run_privileged install -m 600 "$PORTL_STAGE/docker-compose.yml" "$PORTL_INSTALL_DIR/docker-compose.yml"
run_privileged install -m 755 "$PORTL_STAGE/portl" "$PORTL_BIN_DIR/portl"
run_privileged install -m 600 "$PORTL_STAGE/.portl-install.json" "$PORTL_INSTALL_DIR/.portl-install.json"

echo "INFO | Portl installed."
echo "INFO | Run: portl doctor"
echo "INFO | Start: portl start"
echo "INFO | If portl is not found, add $PORTL_BIN_DIR to PATH."
PORTL_EXISTING_COMMAND="$(command -v portl 2>/dev/null || true)"
if [ -n "$PORTL_EXISTING_COMMAND" ] && [ "$PORTL_EXISTING_COMMAND" != "$PORTL_BIN_DIR/portl" ]; then
  echo "INFO | Another portl command exists at $PORTL_EXISTING_COMMAND. Use $PORTL_BIN_DIR/portl to run this installation."
fi
