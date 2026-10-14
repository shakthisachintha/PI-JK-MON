#!/usr/bin/env bash
# Install or upgrade the JK BMS monitor on Raspberry Pi OS (Bookworm).
# Run from the project root on the Pi:  sudo ./deploy/install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX=/opt/jkbms-pi
CONF_DIR=/etc/jkbms-pi

if [[ ! -f "$SRC/jkbms_pi/static/index.html" ]]; then
  echo "warning: dashboard not built (jkbms_pi/static/index.html missing)." >&2
  echo "         build it on a computer with Node.js: cd web && npm ci && npm run build" >&2
  echo "         (deploy/deploy.sh does this for you). The API will still work." >&2
fi

echo "==> installing system packages"
apt-get update -qq
apt-get install -y -qq python3-venv bluez sqlite3

echo "==> creating service user"
if ! id jkbms >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/jkbms-pi --no-create-home --shell /usr/sbin/nologin jkbms
fi
usermod -aG bluetooth jkbms
rfkill unblock bluetooth || true

echo "==> creating venv in $PREFIX"
mkdir -p "$PREFIX"
[[ -d "$PREFIX/venv" ]] || python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet "$SRC"

echo "==> config"
mkdir -p "$CONF_DIR"
if [[ ! -f "$CONF_DIR/config.toml" ]]; then
  cp "$SRC/config.example.toml" "$CONF_DIR/config.toml"
  echo "    created $CONF_DIR/config.toml — set bms_mac before relying on it"
fi

echo "==> systemd"
install -m 0644 "$SRC/deploy/jkbms-pi.service" /etc/systemd/system/jkbms-pi.service
systemctl daemon-reload
systemctl enable jkbms-pi
systemctl restart jkbms-pi

echo
echo "Done. Dashboard: http://$(hostname).local:8080   Logs: journalctl -u jkbms-pi -f"
