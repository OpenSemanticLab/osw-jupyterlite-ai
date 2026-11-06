"""The battery domain models and the view extra, imported in a real kernel.

`scripts/resolve_wheels.py` proves the closure *resolves*; it cannot prove the wheels
*import*. The two differ here in a specific way worth testing: Pyodide 0.29.3 ships
bokeh 3.6.3 and the distribution copy always wins, so panel is pinned to a version that
accepts it. A resolver check only compares specifiers - whether panel 1.7.x actually runs
against that bokeh is a question only the kernel answers.

Separate from `osw_in_kernel.py` on purpose: these packages are deliberately *not* in
`contents/osw_bootstrap.py`'s STEPS, so no normal session pays for them. panel's wheel is
29 MB, most of the site index - keeping it pay-on-use is the design, and a test that
installed it as part of the default path would hide a regression in that.

    python tests/battery_stack_in_kernel.py --wiki wiki-dev.open-semantic-lab.org
"""

from __future__ import annotations

import argparse
import re
import sys
import time

from playwright.sync_api import sync_playwright

CELL = """
import piplite, sys

# The models are pydantic[email], and micropip resolves no extras - so this is
# needed before the import, exactly as contents/osw_bootstrap.py's STEPS and
# OSW_SEEDS both spell it out. Installing opensemantic-batteries alone resolves
# fine and then raises ImportError on `import`, which is what this line prevents
# and why the ordering is part of what the test asserts.
await piplite.install("email-validator", keep_going=True)

# keep_going so one bad wheel does not mask the rest - the markers below say
# which parts actually arrived.
await piplite.install("opensemantic-batteries", keep_going=True)
import opensemantic.batteries as bat
print("BAT_IMPORT ok", getattr(bat, "__version__", "(no __version__)"))

# panelini pulls panel, which pulls param and pyviz-comms; bokeh must come from
# the Pyodide distribution rather than the site index.
await piplite.install("panelini", keep_going=True)

import bokeh
print("BOKEH ok", bokeh.__version__)
import panel
print("PANEL ok", panel.__version__)
import panelini
print("PANELINI ok", getattr(panelini, "__version__", "(no __version__)"))

# The pin exists because panel checks the bokeh it got. Importing panel.io is
# where that reconciliation actually happens, so do it rather than trusting the
# top-level import.
import panel.io
print("PANEL_IO ok")

# bokeh is vendored nowhere: if this says anything but 3.6.x the distribution copy
# was shadowed and the pin is reasoning about the wrong version.
print("BOKEH_SOURCE", "pyodide" if "/lib/python" in bokeh.__file__ else bokeh.__file__)

print("BAT_DONE")
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--base", default="/w/extensions/JupyterLite/dist/")
    # Opened only to get a notebook on screen; its cells are never run. Without
    # ?path= the lab shows the launcher, .jp-Notebook never appears, and the run
    # sits until the timeout with nothing to say about the packages.
    ap.add_argument("--notebook", default="start.ipynb")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    lab = f"https://{args.wiki}{args.base}lab/index.html?path={args.notebook}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        page.on("console", lambda m: print(f"  [console] {m.text}"[:160]))

        print(f"opening {lab}")
        page.goto(lab, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector(".jp-Notebook", timeout=args.timeout * 1000)

        print("installing and importing the battery stack (cold) ...")
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
                ".some(o => o.innerText.includes('BAT_DONE') || o.innerText.includes('Error'))",
                timeout=args.timeout * 1000,
            )
        except Exception:  # noqa: BLE001 - report what the cell managed first
            print("timed out waiting for the cell", file=sys.stderr)
        elapsed = time.monotonic() - started

        output = "\n".join(
            o.inner_text() for o in page.query_selector_all(".jp-OutputArea-output")
        )
        browser.close()

    print(f"\ninstall + import of the battery/view stack: {elapsed:.1f}s")
    print("--- markers ---")
    for line in output.splitlines():
        if line.startswith(("BAT_", "PANEL", "BOKEH")):
            print(f"  {line.strip()}")
    noise = [ln for ln in output.splitlines() if "Traceback" in ln or "Error" in ln]
    if noise:
        print("--- errors in the cell ---")
        for line in noise[:10]:
            print(f"  {line.strip()[:160]}")

    required = [
        "BAT_IMPORT ok",
        "BOKEH ok",
        "PANEL ok",
        "PANELINI ok",
        "PANEL_IO ok",
        "BAT_DONE",
    ]
    missing = [marker for marker in required if marker not in output]
    if missing:
        print(f"\nFAIL: missing {missing}", file=sys.stderr)
        return 1
    if re.search(r"Traceback|ModuleNotFoundError", output):
        print("\nFAIL: the cell raised", file=sys.stderr)
        return 1
    if not re.search(r"BOKEH ok 3\.6\.", output):
        print(
            "\nFAIL: bokeh is not the 3.6.x the Pyodide distribution ships, so the "
            "panel pin is reasoning about a version that is not the one in use",
            file=sys.stderr,
        )
        return 1

    print("\nPASS: the battery models and the view extra install and import.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
