#!/usr/bin/env bash
# Build the dashboard and install the service.
#
#   ./deploy/deploy.sh                    on the Pi: build and install in place
#   ./deploy/deploy.sh user@host          from another computer: build, copy to ~/jkbms-pi on the Pi, install there
set -euo pipefail

TARGET="${1:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> building dashboard"
(cd "$ROOT/web" && bun install --frozen-lockfile && bun run build)

if [[ -z "$TARGET" ]]; then
  echo "==> installing locally from $ROOT"
  if [[ $EUID -eq 0 ]]; then
    "$ROOT/deploy/install.sh"
  else
    sudo "$ROOT/deploy/install.sh"
  fi
  exit 0
fi

echo "==> copying to $TARGET:~/jkbms-pi"
rsync -az --delete \
  --exclude .git --exclude .venv --exclude web/node_modules --exclude web/.next --exclude web/out \
  --exclude '*.db' --exclude '*.db-*' --exclude frames \
  "$ROOT/" "$TARGET:jkbms-pi/"

echo "==> installing"
ssh -t "$TARGET" "cd jkbms-pi && sudo ./deploy/install.sh"
