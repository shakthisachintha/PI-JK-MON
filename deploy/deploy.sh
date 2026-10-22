#!/usr/bin/env bash
# Build the dashboard and install the service.
#
#   ./deploy/deploy.sh                    on the Pi: build and install in place
#   ./deploy/deploy.sh user@host          from another computer: build, copy to ~/ray-vault on the Pi, install there
#   add --full to either form to force apt update and a full Python dependency install
set -euo pipefail

TARGET=""
INSTALL_ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--full" ]]; then INSTALL_ARGS+=(--full); else TARGET="$arg"; fi
done
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> building dashboard"
(cd "$ROOT/web" && bun install --frozen-lockfile && bun run build)

if [[ -z "$TARGET" ]]; then
  echo "==> installing locally from $ROOT"
  if [[ $EUID -eq 0 ]]; then
    "$ROOT/deploy/install.sh" ${INSTALL_ARGS[@]+"${INSTALL_ARGS[@]}"}
  else
    sudo "$ROOT/deploy/install.sh" ${INSTALL_ARGS[@]+"${INSTALL_ARGS[@]}"}
  fi
  exit 0
fi

echo "==> copying to $TARGET:~/ray-vault"
rsync -az --delete \
  --exclude .git --exclude .venv --exclude web/node_modules --exclude web/.next --exclude web/out \
  --exclude '*.db' --exclude '*.db-*' --exclude frames \
  "$ROOT/" "$TARGET:ray-vault/"

echo "==> installing"
ssh -t "$TARGET" "cd ray-vault && sudo ./deploy/install.sh ${INSTALL_ARGS[*]-}"
