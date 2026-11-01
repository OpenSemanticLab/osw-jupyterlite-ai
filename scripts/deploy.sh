#!/usr/bin/env bash
# Copy a built site into a MediaWiki JupyterLite extension's dist/.
#
#   scripts/deploy.sh /usr/local/docker-container/osw-dev/mediawiki/extensions/JupyterLite
#
# The contents of dist/ are replaced, but the directory itself is never deleted. That
# matters: the extension directory is bind-mounted into the running container, and
# removing a mounted directory on the host orphans the mount - the container then serves
# an empty directory until it is recreated. `scripts/build.sh` deletes and recreates
# _output on every run, which is exactly why _output must not be the mount source.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${OUTPUT:-$ROOT/_output}"
TARGET="${1:-}"

if [ -z "$TARGET" ]; then
    echo "usage: $0 <path-to-JupyterLite-extension>" >&2
    exit 2
fi
if [ ! -f "$OUTPUT/jupyter-lite.json" ]; then
    echo "no built site at $OUTPUT - run scripts/build.sh first" >&2
    exit 1
fi
if [ ! -f "$TARGET/extension.json" ]; then
    echo "$TARGET does not look like a MediaWiki extension" >&2
    exit 1
fi

DIST="$TARGET/dist"
mkdir -p "$DIST"

if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "$OUTPUT/" "$DIST/"
else
    # Same effect without rsync: empty the directory in place rather than replacing it.
    find "$DIST" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    cp -a "$OUTPUT/." "$DIST/"
fi

echo "deployed $(du -sh "$DIST" | cut -f1) to $DIST"
