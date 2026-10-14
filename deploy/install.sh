#!/usr/bin/env bash
# Install or upgrade the JK BMS monitor on Raspberry Pi OS (Bookworm).
# Run from the project root on the Pi:  sudo ./deploy/install.sh [--full]
#
# Re-runs are fast: apt is skipped when the packages are present, and Python dependencies are only
# resolved when pyproject.toml changes; otherwise just this package is rebuilt, offline.
# --full forces apt update, a pip upgrade and a full dependency install.
set -euo pipefail

FULL=false
[[ "${1:-}" == "--full" ]] && FULL=true

if [[ $EUID -ne 0 ]]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX=/opt/jkbms-pi
CONF_DIR=/etc/jkbms-pi

if [[ ! -f "$SRC/jkbms_pi/static/index.html" ]]; then
  echo "warning: dashboard not built (jkbms_pi/static/index.html missing)." >&2
  echo "         build it on a computer with Bun: cd web && bun install && bun run build" >&2
  echo "         (deploy/deploy.sh does this for you). The API will still work." >&2
fi

APT_PACKAGES=(python3-venv bluez sqlite3)
missing=()
for pkg in "${APT_PACKAGES[@]}"; do
  dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed" || missing+=("$pkg")
done
if $FULL || ((${#missing[@]})); then
  echo "==> installing system packages ${missing[*]:-}"
  apt-get update -qq
  apt-get install -y -qq "${APT_PACKAGES[@]}"
else
  echo "==> system packages already installed"
fi

echo "==> creating service user"
if ! id jkbms >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/jkbms-pi --no-create-home --shell /usr/sbin/nologin jkbms
fi
usermod -aG bluetooth,video jkbms   # video: vcgencmd (Pi power/throttle flags)
rfkill unblock bluetooth || true

PIP=("$PREFIX/venv/bin/pip" --disable-pip-version-check --no-input)
DEPS_STAMP="$PREFIX/.deps-sha256"
deps_hash=$(sha256sum "$SRC/pyproject.toml" | cut -d' ' -f1)

mkdir -p "$PREFIX"
if [[ ! -x "$PREFIX/venv/bin/python" ]]; then
  echo "==> creating venv in $PREFIX"
  python3 -m venv "$PREFIX/venv"
  FULL=true
fi

if $FULL || [[ "$(cat "$DEPS_STAMP" 2>/dev/null)" != "$deps_hash" ]]; then
  echo "==> installing Python dependencies (pyproject.toml changed or --full)"
  "${PIP[@]}" install --upgrade pip
  # hatchling lives in the venv so later builds can skip build isolation (no download per run).
  "${PIP[@]}" install "hatchling>=1.25"
  "${PIP[@]}" install --no-build-isolation "$SRC"
  echo "$deps_hash" >"$DEPS_STAMP"
else
  echo "==> updating jkbms-pi (dependencies unchanged)"
  "${PIP[@]}" install --quiet --no-deps --no-index --no-build-isolation --force-reinstall "$SRC"
fi

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
