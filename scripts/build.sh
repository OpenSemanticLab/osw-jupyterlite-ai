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

# Local version segment for the osw wheel built from OSW_SRC. Set it empty once a
# released osw carries the dask fix - then the plain PyPI wheel is correct and this
# whole local-build step can go.
OSW_LOCAL_VERSION="${OSW_LOCAL_VERSION:-pyodide1}"

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
    -e OSW_LOCAL_VERSION="$OSW_LOCAL_VERSION" \
    -w /flavour "$IMAGE" sh -c '
        pip install --quiet build
        cp -r /osw-src /tmp/osw-src
        cd /tmp/osw-src
        # Stamp a PEP 440 local version onto our build. Without it this wheel and the
        # released one are both "2.8.2", and nothing downstream can be made to prefer
        # ours: pip picked the index copy at vendoring time and the site silently shipped
        # the *unpatched* osw, which still requires dask unconditionally and therefore
        # cannot install in Pyodide at all.
        # A local segment sorts above the plain version, so ours wins deterministically -
        # and micropip.list()["osw"].version then says which one you actually got.
        # Remove this once a release carries the dask fix; see OSW_LOCAL_VERSION below.
        sed -i -E "s/^version = \"([^\"]+)\"/version = \"\1+${OSW_LOCAL_VERSION}\"/" pyproject.toml
        grep -m1 "^version" pyproject.toml
        python -m build --wheel --outdir /flavour/wheels/local .
    '

if [ ! -d "$ROOT/extension/node_modules" ]; then
    echo "== npm install (labextension) =="
    docker run --rm -v "$ROOT/extension":/e -w /e "$IMAGE" npm install --no-audit --no-fund
    docker run --rm -v "$ROOT/extension":/e alpine chown -R "$(id -u):$(id -g)" /e
fi

echo "== building the labextension =="
docker run --rm -v "$ROOT/extension":/e -w /e "$IMAGE" npm run build
docker run --rm -v "$ROOT/extension":/e alpine chown -R "$(id -u):$(id -g)" /e

echo "== building the site =="
# PIP_FIND_LINKS is how the locally built osw wheel wins over the capped PyPI release,
# for both the resolver and Site._build_wheels.
# PIP_IGNORE_REQUIRES_PYTHON: Site._build_wheels downloads the vendored set with a
# hardcoded --python-version 3.14, which was right while the base targeted a Pyodide 314
# kernel. This flavour runs a 3.13 one, and osw caps itself at <3.14 - correctly, because
# pydantic.v1 does not work on 3.14 - so pip would skip it and the site would ship without
# osw at all. The list being downloaded is already fully resolved against the real
# interpreter by scripts/resolve_wheels.py, so the check here adds nothing. Remove this
# once the base takes the python version from the pyodide lock.
#
# The labextension is pip-installed in the *same* container run as the site build. The
# base's Site._build_extension would do it, but it skips the install whenever the bundle
# is newer than src/ - and since every step here runs in a fresh container, a skipped
# install means the extension is simply missing from the site, with no error. That is why
# oold-lite.toml sets `rebuild = "never"`: the npm build above already happened.
docker run --rm \
    -v "$ROOT":/flavour \
    -e PIP_FIND_LINKS=/flavour/wheels/local \
    -e PIP_IGNORE_REQUIRES_PYTHON=1 \
    -w /flavour "$IMAGE" sh -c '
        pip install --quiet --no-deps --force-reinstall ./extension
        python -m oold_jupyterlite_ai.cli build --config oold-lite.toml --output /flavour/_output
    '

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

# Gate the artifact, not the intent. resolve_wheels.py asks pip for pure wheels only, but
# the locally built osw wheel and the kernel's bundled wheels never pass through that walk
# - and a platform specific wheel here fails in the user's browser, not in the build.
# Runs in the build image because the host python may be older than the f-strings used.
echo "== checking every wheel is installable under pyodide =="
docker run --rm -v "$ROOT":/flavour -w /flavour "$IMAGE" \
    python scripts/check_pyodide_compat.py "${OUTPUT#"$ROOT"/}"

echo "== done: $OUTPUT =="
