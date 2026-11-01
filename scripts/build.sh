#!/usr/bin/env bash
# Build the OSW JupyterLite site.
#
# Everything happens in a container: the host has Python 3.10, but
# oold_jupyterlite_ai.config needs tomllib (3.11+) and the labextension build needs npm.
#
#   scripts/build.sh                 # build into _output/
#   OSW_SRC=/path/to/osw-python scripts/build.sh
#   STRIP_MAPS=0 scripts/build.sh    # keep source maps (44 MB of the 74)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${IMAGE:-osw-jupyterlite-build:dev}"
OUTPUT="${OUTPUT:-$ROOT/_output}"
STRIP_MAPS="${STRIP_MAPS:-1}"

# osw is built from source rather than taken from PyPI until the Pyodide fixes are
# released: PyPI's 2.8.2 still declares requires-python <3.14, and Pyodide 314 is CPython
# 3.14 - pip does not error on that, it silently resolves back to osw 1.1.2.
OSW_SRC="${OSW_SRC:-/usr/local/docker-container/.worktrees/osw-python-pyodide}"

docker image inspect "$IMAGE" >/dev/null 2>&1 || {
    echo "== building $IMAGE =="
    docker build -f "$ROOT/docker/Dockerfile" -t "$IMAGE" "$ROOT"
}

echo "== building the osw wheel from $OSW_SRC =="
mkdir -p "$ROOT/wheels/local"
rm -f "$ROOT/wheels/local"/osw-*.whl
# Copied out of the mount first: `python -m build` writes into the source tree, and the
# checkout is a git worktree shared with other work.
docker run --rm \
    -v "$ROOT":/flavour \
    -v "$OSW_SRC":/osw-src:ro \
    -w /flavour "$IMAGE" sh -c '
        pip install --quiet build
        cp -r /osw-src /tmp/osw-src
        cd /tmp/osw-src && python -m build --wheel --outdir /flavour/wheels/local .
    '

echo "== building the site =="
# PIP_FIND_LINKS is how the locally built osw wheel wins over the capped PyPI release,
# for both the resolver and Site._build_wheels.
docker run --rm \
    -v "$ROOT":/flavour \
    -e PIP_FIND_LINKS=/flavour/wheels/local \
    -w /flavour "$IMAGE" \
    python -m oold_jupyterlite_ai.cli build --config oold-lite.toml --output /flavour/_output

# Docker writes as root and this host has no passwordless sudo.
docker run --rm -v "$ROOT":/flavour alpine chown -R "$(id -u):$(id -g)" /flavour

if [ "$STRIP_MAPS" = "1" ]; then
    # 44 MB of the 74 MB build is .js.map, which a browser only fetches with devtools
    # open. Dropping them is what makes the site small enough to ship inside the
    # MediaWiki extension.
    before=$(du -sm "$OUTPUT" | cut -f1)
    find "$OUTPUT" -name '*.map' -delete
    echo "== stripped source maps: ${before} MB -> $(du -sm "$OUTPUT" | cut -f1) MB =="
fi

echo "== done: $OUTPUT =="
