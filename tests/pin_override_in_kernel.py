"""Can a user deviate from the vendored pins, and does micropip resolve extras?

Two questions the flavour's design rests on, neither answerable by reading code:

1. The site index pins a closure that is *proven* against this Pyodide - notably
   panel 1.7.5, because 1.10 needs a bokeh newer than the distribution ships. If
   asking for a different version simply failed, the pin would be a cage: nobody
   could install anything the build did not foresee. `disablePyPIFallback` is
   false precisely so it is not, but the interesting case is a name that IS in
   the local index at a DIFFERENT version - does piplite fall back to PyPI for
   it, or refuse because its own index cannot satisfy the specifier?

2. Whether micropip resolves extras. `scripts/resolve_wheels.py` hand-lists
   email-validator because its own build-time dependency walk does not follow
   them, and `contents/osw_bootstrap.py` installs it explicitly. Those are two
   different resolvers, and the comments in both have been reading as if they
   were one. If micropip does handle extras at runtime, the bootstrap step is
   redundant even though the build-time seed is not.

    python tests/pin_override_in_kernel.py --wiki wiki-dev.open-semantic-lab.org
"""

from __future__ import annotations

import argparse
import re
import sys
import time

from playwright.sync_api import sync_playwright

CELL = r"""
import micropip, piplite, importlib.metadata as md

def ver(name):
    try:
        return md.version(name)
    except Exception:
        return None

# (1a) piplite, a name in the local index at a version it does not carry.
try:
    await piplite.install("jsonpath-ng==1.16.0", keep_going=True)
    print("PIPLITE_NEWER", ver("jsonpath-ng"))
except Exception as e:
    print("PIPLITE_NEWER failed:", type(e).__name__, str(e)[:160])

# (1b) the same through micropip, which talks to PyPI rather than the site index.
# This is the question that matters: can a user deviate from the pin at all?
try:
    await micropip.install("jsonpath-ng==1.16.0")
    print("MICROPIP_NEWER", ver("jsonpath-ng"))
except Exception as e:
    print("MICROPIP_NEWER failed:", type(e).__name__, str(e)[:160])

# (1c) a package absent from the local index entirely - the plain fallback path.
try:
    await micropip.install("humanize==4.11.0")
    print("MICROPIP_ABSENT", ver("humanize"))
except Exception as e:
    print("MICROPIP_ABSENT failed:", type(e).__name__, str(e)[:160])

# (2) extras on a package micropip actually installs, rather than on pydantic,
# which the distribution already satisfies and may therefore short-circuit.
try:
    await micropip.install("requests[socks]")
    print("EXTRA_PURE", "pysocks=", ver("pysocks"))
except Exception as e:
    print("EXTRA_PURE failed:", type(e).__name__, str(e)[:160])

print("PIN_DONE")
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--base", default="/w/extensions/JupyterLite/dist/")
    ap.add_argument("--notebook", default="start.ipynb")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    lab = f"https://{args.wiki}{args.base}lab/index.html?path={args.notebook}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(ignore_https_errors=True).new_page()
        print(f"opening {lab}")
        page.goto(lab, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector(".jp-Notebook", timeout=args.timeout * 1000)

        page.click(".jp-Notebook")
        page.keyboard.press("Escape")
        page.keyboard.press("b")
        page.keyboard.press("Enter")
        page.keyboard.insert_text(CELL.strip())

        started = time.monotonic()
        page.keyboard.press("Shift+Enter")
        try:
            page.wait_for_function(
                "() => [...document.querySelectorAll('.jp-OutputArea-output')]"
                ".some(o => o.innerText.includes('PIN_DONE'))",
                timeout=args.timeout * 1000,
            )
        except Exception:  # noqa: BLE001
            print("timed out", file=sys.stderr)
        elapsed = time.monotonic() - started
        output = "\n".join(
            o.inner_text() for o in page.query_selector_all(".jp-OutputArea-output")
        )
        browser.close()

    print(f"\nelapsed: {elapsed:.1f}s")
    print("--- answers ---")
    for line in output.splitlines():
        if re.match(r"^(PIPLITE_|MICROPIP_|EXTRA_|PIN_DONE)", line):
            print(f"  {line.strip()}")
    return 0 if "PIN_DONE" in output else 1


if __name__ == "__main__":
    raise SystemExit(main())
