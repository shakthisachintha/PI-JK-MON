#!/usr/bin/env bash
# Install or upgrade Ray Vault on Raspberry Pi OS (Bookworm).
# Run from the project root on the Pi:  sudo ./deploy/install.sh [--full]
#
# Re-runs are fast: apt is skipped when the packages are present, and Python dependencies are only
# resolved when pyproject.toml changes; otherwise just this package is rebuilt, offline.
# --full forces apt update, a pip upgrade and a full dependency install.
#
# Upgrading from the old jkbms-pi service migrates it once: the database and config are copied to the new
# locations, the old unit is removed, and the old directories are left in place for you to delete.
set -euo pipefail

FULL=false
[[ "${1:-}" == "--full" ]] && FULL=true

if [[ $EUID -ne 0 ]]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
NAME=ray-vault
SVC_USER=rayvault
PREFIX=/opt/$NAME
CONF_DIR=/etc/$NAME
DATA_DIR=/var/lib/$NAME
CONF=$CONF_DIR/config.toml

if [[ ! -f "$SRC/ray_vault/static/index.html" ]]; then
  echo "warning: dashboard not built (ray_vault/static/index.html missing)." >&2
  echo "         build it with Bun: cd web && bun install && bun run build" >&2
  echo "         (deploy/deploy.sh does this for you). The API will still work." >&2
fi

APT_PACKAGES=(python3-venv bluez sqlite3 polkitd)
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

echo "==> service user"
if ! id "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
fi
# bluetooth: BlueZ · video: vcgencmd (Pi power flags) · dialout: RS485 serial adapter
usermod -aG bluetooth,video,dialout "$SVC_USER"
rfkill unblock bluetooth || true

# --- one-time migration from jkbms-pi ---------------------------------------------
if [[ -f /etc/systemd/system/jkbms-pi.service ]]; then
  echo "==> migrating from jkbms-pi"
  systemctl disable --now jkbms-pi 2>/dev/null || true
  mkdir -p "$DATA_DIR" "$CONF_DIR"
  if [[ -f /var/lib/jkbms-pi/jkbms.db && ! -f "$DATA_DIR/$NAME.db" ]]; then
    sqlite3 /var/lib/jkbms-pi/jkbms.db ".backup '$DATA_DIR/$NAME.db'"  # consistent copy, WAL included
    echo "    database copied to $DATA_DIR/$NAME.db"
  fi
  if [[ -f /etc/jkbms-pi/config.toml && ! -f "$CONF" ]]; then
    # Old flat keys still work; drop a db_path that points at the old location.
    sed -E '/^[[:space:]]*db_path[[:space:]]*=.*jkbms-pi/d' /etc/jkbms-pi/config.toml >"$CONF"
    echo "    config copied to $CONF"
  fi
  rm -f /etc/systemd/system/jkbms-pi.service
  systemctl daemon-reload
  echo "    old files are still in /opt/jkbms-pi, /etc/jkbms-pi and /var/lib/jkbms-pi; delete them when happy"
fi
mkdir -p "$DATA_DIR"
chown -R "$SVC_USER:$SVC_USER" "$DATA_DIR"

# --- Python ---------------------------------------------------------------------------
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
  echo "==> updating $NAME (dependencies unchanged)"
  "${PIP[@]}" install --quiet --no-deps --no-index --no-build-isolation --force-reinstall "$SRC"
fi

# --- config ---------------------------------------------------------------------------
echo "==> config"
mkdir -p "$CONF_DIR"
if [[ ! -f "$CONF" ]]; then
  cp "$SRC/config.example.toml" "$CONF"
  echo "    created $CONF — set [bms] mac (and [inverter] / [control]) before relying on it"
elif ! grep -qE '^[#[:space:]]*\[inverter\]' "$CONF"; then
  {
    echo
    echo "# --- added by install.sh: inverter and control settings (see config.example.toml) ---"
    sed -n '/^\[inverter\]/,$p' "$SRC/config.example.toml" | sed -E 's/^([^#[:space:]])/# \1/'
  } >>"$CONF"
  echo "    appended commented [inverter] and [control] sections to $CONF"
fi
# The config holds the control PIN: readable by root and the service only.
chown "root:$SVC_USER" "$CONF"
chmod 640 "$CONF"

# --- polkit + systemd -----------------------------------------------------------------
echo "==> polkit rule (lets the service restart bluetoothd)"
install -m 0644 "$SRC/deploy/50-$NAME.rules" "/etc/polkit-1/rules.d/50-$NAME.rules"

echo "==> systemd"
install -m 0644 "$SRC/deploy/$NAME.service" "/etc/systemd/system/$NAME.service"
systemctl daemon-reload
systemctl enable "$NAME"
systemctl restart "$NAME"

echo
echo "Done. Dashboard: http://$(hostname).local:8080   Logs: journalctl -u $NAME -f"
