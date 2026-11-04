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

# JupyterLab stamps its build artifacts with a fixed, reproducible mtime of 1985-10-26 and
# rsync preserves it - 436 of 694 files in a current build carry it. Served without an
# explicit Cache-Control, a browser falls back to heuristic freshness, conventionally a
# tenth of the age implied by Last-Modified; from 1985 that is years. lab/index.html would
# then survive a redeploy, pinning the old bundle.js?_=<hash>, while jupyter-lite.json and
# the wheel index - which carry real mtimes - refresh within minutes. New config against an
# old bundle is a worse failure than uniform staleness, and it is invisible to any test
# that opens a fresh browser profile.
#
# Stamping everything with the deploy time makes the heuristic reasonable on any server.
# The extension also ships an .htaccess setting Cache-Control explicitly, which is better
# but needs AllowOverride and mod_headers; this works regardless and costs nothing.
find "$DIST" -exec touch {} +

echo "deployed $(du -sh "$DIST" | cut -f1) to $DIST"
