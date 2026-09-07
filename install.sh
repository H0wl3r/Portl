#!/usr/bin/env sh
set -eu

if [ "$(uname -s)" = "Linux" ]; then
  PORTL_INSTALL_DIR="${PORTL_INSTALL_DIR:-/srv/portl}"
  PORTL_BIN_DIR="${PORTL_BIN_DIR:-/usr/local/bin}"
else
  PORTL_INSTALL_DIR="${PORTL_INSTALL_DIR:-$HOME/.local/share/portl}"
  PORTL_BIN_DIR="${PORTL_BIN_DIR:-$HOME/.local/bin}"
fi
PORTL_LAUNCHER_URL="${PORTL_LAUNCHER_URL:-https://raw.githubusercontent.com/H0wl3r/portl/main/portl.py}"
PORTL_COMPOSE_URL="${PORTL_COMPOSE_URL:-https://raw.githubusercontent.com/H0wl3r/portl/main/docker-compose.yml}"

echo "INFO | Installing Portl launcher into $PORTL_INSTALL_DIR"

if [ "$(id -u)" != "0" ] && { [ "$PORTL_INSTALL_DIR" = "/srv/portl" ] || [ "$PORTL_BIN_DIR" = "/usr/local/bin" ]; }; then
  echo "ERROR | The system installation requires administrator access. Run the installer with sudo sh." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR | python3 is required to run the Portl launcher." >&2
  exit 1
fi

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

# Keep settings and generated secrets inside a private installation directory.
umask 077
mkdir -p "$PORTL_INSTALL_DIR" "$PORTL_BIN_DIR"
download "$PORTL_LAUNCHER_URL" "$PORTL_INSTALL_DIR/portl.py"
download "$PORTL_COMPOSE_URL" "$PORTL_INSTALL_DIR/docker-compose.yml"

chmod +x "$PORTL_INSTALL_DIR/portl.py"

cat > "$PORTL_BIN_DIR/portl" <<EOF
#!/usr/bin/env sh
exec python3 "$PORTL_INSTALL_DIR/portl.py" "\$@"
EOF
chmod 755 "$PORTL_BIN_DIR/portl"

echo "INFO | Portl installed."
if [ "$(id -u)" = "0" ]; then
  echo "INFO | Run: sudo $PORTL_BIN_DIR/portl doctor"
  echo "INFO | Start: sudo $PORTL_BIN_DIR/portl start"
else
  echo "INFO | Run: $PORTL_BIN_DIR/portl doctor"
fi
echo "INFO | If portl is not found, add $PORTL_BIN_DIR to PATH."
