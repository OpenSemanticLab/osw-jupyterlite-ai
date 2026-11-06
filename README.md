# osw-jupyterlite-ai

The OSW flavour of [oold-jupyterlite-ai](https://github.com/OO-LD/oold-jupyterlite-ai):
a static JupyterLite site with `osw-python`, the `opensemantic.*` packages and the
Jupyternaut agent, served from an OpenSemanticLab wiki's own origin.

Because it is same-origin with the wiki, the Pyodide kernel shares the browser's
MediaWiki session: `osw-python` reads and writes the wiki **as the logged-in user**, with
no credential file and no login prompt. That is the point of the deployment shape, and it
is also its main consequence - any notebook opened here runs with that user's rights.

## Build

```bash
scripts/build.sh          # -> _output/
```

Everything runs in a container (`docker/Dockerfile`): the host needs only Docker. The
image carries Python 3.12 (the base package's config reader needs `tomllib`), Node, and
the pinned `jupyterlite-ai==0.20.1` stack.

`osw` is built from a local checkout rather than taken from PyPI - see `OSW_SRC` in
`scripts/build.sh` for why.

## Layout

| | |
| --- | --- |
| `oold-lite.toml` | what this flavour is: contents, capabilities, brief, wheel set |
| `brief/` | the system prompt fragments this flavour owns |
| `contents/` | what ships on the notebook drive |
| `skills/` | staged into `.agents/skills`, where the agent looks |
| `piplite-requirements.txt` | the resolved wheel closure, generated |
| `scripts/resolve_wheels.py` | regenerates it, and fails on a version conflict |

## Wheels

`piplite-requirements.txt` is a *resolved* closure - `Site._build_wheels` downloads it
with `--no-deps`. Regenerate after changing `[agent] capabilities` or bumping osw:

```bash
docker run --rm -v "$PWD":/flavour -w /flavour \
    -e PIP_IGNORE_REQUIRES_PYTHON=1 \
    osw-jupyterlite-build:dev python scripts/resolve_wheels.py > piplite-requirements.txt
```

`osw` comes from PyPI like everything else since 2.9.0, which moved `dask` behind a
`parallel` extra. Before that the build compiled `osw` from a branch and stamped a local
version onto it, because the released wheel imported `dask` unconditionally and so could
not install under Pyodide at all.

The resolver checks every specifier in the closure against the single version the site
will ship, which the base's walk does not: it resolves a name once, first-come. That
check is what caught `osw` pinning `datamodel-code-generator==0.51.0` while the base agent
takes the latest, and `isort` 9 against that release's `isort<8`.

## License

Apache-2.0, matching the OSW packages this builds on. See `LICENSE` and `NOTICE`.
