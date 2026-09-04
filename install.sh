#!/usr/bin/env sh
set -eu

PORTL_INSTALL_DIR="${PORTL_INSTALL_DIR:-$HOME/.local/share/portl}"
PORTL_BIN_DIR="${PORTL_BIN_DIR:-$HOME/.local/bin}"
PORTL_LAUNCHER_URL="${PORTL_LAUNCHER_URL:-https://raw.githubusercontent.com/H0wl3r/portl/main/portl.py}"
PORTL_COMPOSE_URL="${PORTL_COMPOSE_URL:-https://raw.githubusercontent.com/H0wl3r/portl/main/docker-compose.yml}"

echo "INFO | Installing Portl launcher into $PORTL_INSTALL_DIR"

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

mkdir -p "$PORTL_INSTALL_DIR" "$PORTL_BIN_DIR"
download "$PORTL_LAUNCHER_URL" "$PORTL_INSTALL_DIR/portl.py"
download "$PORTL_COMPOSE_URL" "$PORTL_INSTALL_DIR/docker-compose.yml"

chmod +x "$PORTL_INSTALL_DIR/portl.py"

cat > "$PORTL_BIN_DIR/portl" <<EOF
#!/usr/bin/env sh
exec python3 "$PORTL_INSTALL_DIR/portl.py" "\$@"
EOF
chmod +x "$PORTL_BIN_DIR/portl"

echo "INFO | Portl installed."
echo "INFO | Run: portl doctor"
echo "INFO | If portl is not found, add $PORTL_BIN_DIR to PATH."
