"""Resolve the pinned wheel set this flavour has to vendor.

`oold_jupyterlite_ai.wheels resolve` seeds from `oold_agent.requirements()`, which knows
nothing about osw - so running it alone would produce a list that sends micropip to PyPI
for the entire OSW stack on the first `import osw`. This adds the OSW seeds to the same
resolution and reuses every helper from that module, so the two lists cannot drift in how
they decide what the Pyodide distribution already carries.

    python scripts/resolve_wheels.py > piplite-requirements.txt

Output is a pinned requirement per line. `Site._build_wheels` downloads it with --no-deps,
so the file has to be the *resolved* closure, not a top-level list.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

from oold_jupyterlite_ai.wheels import (
    BUNDLED_SEEDS,
    EXTRA_SEEDS,
    agent_seeds,
    bundled_wheels,
    normalize,
    pip_download,
    pyodide_packages,
    runtime_requires,
    wheel_metadata,
)

#: What this flavour adds on top of the base agent's own requirements.
#:
#: `osw` drags in oold, opensemantic, opensemantic.core and opensemantic.base by itself.
#: `opensemantic-lab` is named explicitly because nothing requires it - it is the lab/ELN
#: model layer an OSW notebook wants and the brief tells the agent about.
#: `pyodide-http` is in EXTRA_SEEDS already but repeated here so this list stands alone
#: if the base ever drops it: without patch_all() nothing reaches the wiki at all.
OSW_SEEDS = (
    "osw",
    "opensemantic-lab",
    "pyodide-http",
)

#: Resolved before anything else, so a name with several requirers lands on a version
#: that satisfies all of them rather than on whoever happened to ask first.
#:
#: The base agent takes the latest datamodel-code-generator; osw pins it exactly. Without
#: the pin here the walk resolves the base's unpinned ask first and osw's `==0.51.0` is
#: silently unsatisfied in the built site. isort follows from it: 0.51.0 requires isort<8,
#: while the latest is 9.x, and the resolver reports that too rather than shipping a pair
#: that cannot import together.
PINS = (
    "datamodel-code-generator==0.51.0",
    "isort<8",
)

#: Declared by osw but unusable in the browser, and excluded rather than vendored.
#:
#: dask has no pure-python-only install path worth 30 MB of scheduler for a runtime with
#: one thread, and every use of it in osw.utils.util is already behind MODE == "dask"
#: with a ThreadPoolExecutor alternative. Requires the lazy-import fix in osw-python;
#: until that lands, `import osw` fails in the kernel and this list is why.
EXCLUDED = {"dask"}


def resolve(pyodide_version: str, cache: Path, bundled: list[str], capabilities: list[str] | None):
    in_pyodide = pyodide_packages(pyodide_version, cache)
    prebundled = bundled_wheels(bundled)

    queue: list[tuple[str, str]] = [
        *((r, "osw-flavour pin") for r in PINS),
        *((r, "seed") for r in BUNDLED_SEEDS),
        *((r, "seed") for r in EXTRA_SEEDS),
        *((r, "agent") for r in agent_seeds(None, capabilities)),
        *((r, "osw-flavour") for r in OSW_SEEDS),
    ]
    seen: set[str] = set()
    resolved: dict[str, str] = {}
    from_pyodide: dict[str, str] = {}
    skipped: set[str] = set()
    # Every specifier seen for a name, and who asked for it. The walk resolves a name
    # once, first-come, and ignores specifiers after that - so two requirers wanting
    # different versions would silently yield a site that satisfies only the first. osw
    # pins datamodel-code-generator exactly while the base agent takes the latest, which
    # is exactly that case, so constraints are collected and checked once the walk ends.
    constraints: dict[str, list[tuple[str, str]]] = {}

    def enqueue(dep, requirer: str) -> None:
        queue.append((f"{dep.name}{dep.specifier}" if dep.specifier else dep.name, requirer))

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        while queue:
            requirement, requirer = queue.pop(0)
            req = Requirement(requirement)
            key = normalize(req.name)
            if req.specifier:
                constraints.setdefault(key, []).append((str(req.specifier), requirer))
            if key in seen:
                continue
            seen.add(key)
            if key in EXCLUDED:
                skipped.add(key)
                continue
            # Shipped inside the pyodide-kernel labextension: present at runtime, and
            # vendoring it again would stage a second copy of the same wheel.
            if key in prebundled:
                _, _, requires = wheel_metadata(prebundled[key][1])
                for dep in runtime_requires(requires):
                    enqueue(dep, key)
                continue
            if key in in_pyodide:
                from_pyodide[key] = in_pyodide[key]
                continue
            try:
                wheels = pip_download(sys.executable, requirement, dest)
            except subprocess.CalledProcessError:
                print(f"no pure-python wheel for {requirement}", file=sys.stderr)
                raise SystemExit(1) from None
            if not wheels:
                continue
            name, version, requires = wheel_metadata(wheels[0])
            resolved[normalize(name)] = version
            for dep in runtime_requires(requires):
                enqueue(dep, key)

    conflicts = check_constraints(resolved, from_pyodide, constraints)
    return resolved, from_pyodide, skipped, conflicts


def check_constraints(
    resolved: dict[str, str],
    from_pyodide: dict[str, str],
    constraints: dict[str, list[tuple[str, str]]],
) -> list[str]:
    """Every specifier anyone asked for, against the single version we will ship."""
    problems: list[str] = []
    chosen = {**from_pyodide, **resolved}
    for key, asks in sorted(constraints.items()):
        version = chosen.get(key)
        if version is None:
            continue
        for spec, requirer in asks:
            if not SpecifierSet(spec).contains(version, prereleases=True):
                where = "pyodide distribution" if key in from_pyodide else "vendored"
                problems.append(
                    f"{key}=={version} ({where}) does not satisfy {key}{spec} required by {requirer}"
                )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pyodide-version", default=None)
    ap.add_argument("--cache", type=Path, default=Path(".cache"))
    ap.add_argument("--bundled", action="append", default=None)
    ap.add_argument("--capability", action="append", dest="capabilities", default=None)
    ap.add_argument(
        "--config",
        type=Path,
        default=Path("oold-lite.toml"),
        help="read [agent] capabilities from here when --capability is not given",
    )
    args = ap.parse_args()

    capabilities = args.capabilities
    if capabilities is None and args.config.is_file():
        # Read rather than repeated: a capability list that disagrees with the one the
        # site is built from vendors wheels the agent never uses, or - worse - omits ones
        # it does and sends micropip to PyPI on the first cold start.
        import tomllib

        capabilities = (
            tomllib.loads(args.config.read_text(encoding="utf-8"))
            .get("agent", {})
            .get("capabilities")
        )

    version = args.pyodide_version
    if version is None:
        from jupyterlite_pyodide_kernel.constants import PYODIDE_VERSION

        version = PYODIDE_VERSION

    bundled = args.bundled
    if bundled is None:
        default = (
            Path(sys.prefix)
            / "share/jupyter/labextensions/@jupyterlite/pyodide-kernel-extension/static/pypi"
        )
        bundled = [str(default)] if default.is_dir() else []

    resolved, from_pyodide, skipped, conflicts = resolve(
        version, args.cache, bundled, capabilities
    )

    if conflicts:
        # Fail rather than print a list that would build a site where one of the two
        # requirers is quietly unsatisfied at runtime.
        print("version conflicts in the closure:", file=sys.stderr)
        for problem in conflicts:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\nPin the name in OSW_SEEDS so it is resolved to a version that satisfies "
            "everyone, or relax the requirement upstream.",
            file=sys.stderr,
        )
        return 1

    print(f"# Resolved against pyodide {version}. Regenerate with scripts/resolve_wheels.py.")
    print("#")
    print("# Anything the Pyodide distribution already carries is deliberately absent:")
    for key, ver in sorted(from_pyodide.items()):
        print(f"#   {key} {ver}")
    if skipped:
        print("#")
        print("# Excluded, see EXCLUDED in scripts/resolve_wheels.py:")
        for key in sorted(skipped):
            print(f"#   {key}")
    print()
    for key, ver in sorted(resolved.items()):
        print(f"{key}=={ver}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
