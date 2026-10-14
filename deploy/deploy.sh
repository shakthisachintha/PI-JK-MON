#!/usr/bin/env bash
# Build the dashboard locally, copy the project to the Pi and run install.sh there.
# Usage: ./deploy/deploy.sh pi@raspberrypi.local
set -euo pipefail

TARGET="${1:?usage: $0 user@host}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> building dashboard"
(cd "$ROOT/web" && npm ci --silent && npm run build)

echo "==> copying to $TARGET:~/jkbms-pi"
rsync -az --delete \
  --exclude .git --exclude .venv --exclude web/node_modules --exclude web/.next --exclude web/out \
  --exclude '*.db' --exclude '*.db-*' --exclude frames \
  "$ROOT/" "$TARGET:jkbms-pi/"

echo "==> installing"
ssh -t "$TARGET" "cd jkbms-pi && sudo ./deploy/install.sh"
