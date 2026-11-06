#!/usr/bin/env python3
"""Refuse to ship a site containing anything Pyodide cannot install.

    python scripts/check_pyodide_compat.py _output

Pyodide runs CPython compiled to WebAssembly. It can install two kinds of thing: wheels
the distribution itself ships (compiled to wasm ahead of time, e.g. numpy, pydantic-core,
bokeh), and **pure Python** wheels, which contain no compiled extension at all. A wheel
built for any other platform - manylinux, macosx, win_amd64, or tagged to a specific
CPython ABI - cannot be loaded, and the failure arrives in the user's browser rather than
in the build.

`scripts/resolve_wheels.py` already asks pip for `--only-binary=:all: --platform any`, so
the dependency walk should never produce one. This checks the artifact rather than the
intent, because three things reach `_output/pypi/` without passing through that walk:
the wheel built locally from osw-python, the wheels the pyodide kernel bundles, and
anything a future change copies in by hand. A check that only re-states what the resolver
promised would be worth little; this one can catch the resolver being wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: A wheel filename ends `<python tag>-<abi tag>-<platform tag>.whl`. Pure Python means an
#: `any` platform and an abi of `none`. `py2.py3-none-any` is the old universal form and is
#: equally fine. Anything else - `cp313-cp313-manylinux_2_17_x86_64`, `py3-none-win_amd64`
#: - is a wheel for a machine this will never run on.
def is_pure(wheel: str) -> bool:
    parts = wheel[: -len(".whl")].split("-")
    if len(parts) < 3:
        return False
    abi, platform = parts[-2], parts[-1]
    return abi == "none" and platform == "any"


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "_output")
    index = root / "pypi"
    if not index.is_dir():
        print(f"no wheel index at {index}", file=sys.stderr)
        return 2

    wheels = sorted(p.name for p in index.glob("*.whl"))
    if not wheels:
        # An empty index means the site ships no osw at all. It would load, and then
        # every notebook would fail on its first import.
        print(f"{index} contains no wheels", file=sys.stderr)
        return 1

    bad = [w for w in wheels if not is_pure(w)]
    for wheel in bad:
        print(f"not installable under pyodide: {wheel}", file=sys.stderr)

    if bad:
        print(
            f"\n{len(bad)} of {len(wheels)} wheels are platform specific. Pyodide can load "
            "only its own distribution packages and pure Python wheels.\n"
            "Either the package publishes a pure wheel at a different version - pin it in "
            "PINS in scripts/resolve_wheels.py - or it has to come from the pyodide "
            "distribution, or be dropped via EXCLUDED.",
            file=sys.stderr,
        )
        return 1

    print(f"all {len(wheels)} wheels are pure python")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
