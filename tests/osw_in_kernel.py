"""Does osw-python actually import and run in the browser kernel?

This is the Phase 1 milestone. Everything else in the project assumes it, and two things
had to change upstream before it could be true: osw capped itself at Python <3.14 while
Pyodide is 3.14, and it imported dask at module scope from a module that core, wtsite and
wiki_tools all import unconditionally.

Also reports how long the cold start takes, because that is a product constraint here -
every user pays it in their own browser, and the chatbot's client-tool budget is 60s.

    python tests/osw_in_kernel.py --wiki wiki-dev.open-semantic-lab.org
"""

from __future__ import annotations

import argparse
import re
import sys
import time

from playwright.sync_api import sync_playwright

# One cell, because each cell pays its own wheel-loading latency and the point is to
# measure the whole thing once. `mwclient.Site` is built by hand and handed to
# WtSiteLegacyConfig: that is the path which skips osw's credential handling entirely,
# which is what "no credentials on the same instance" means in practice.
CELL = """
# Through the bootstrap, not a bare micropip.install("osw"): micropip cannot resolve
# osw's pinned datamodel-code-generator as a transitive dependency, and drops the
# [email] extra of pydantic. osw_bootstrap exists to order around both.
import osw_bootstrap
await osw_bootstrap.setup(verbose=False)

import osw, osw.core, osw.wtsite, osw.wiki_tools, osw.utils.util
print("OSW_IMPORT ok")

wtsite = osw_bootstrap.wtsite()
print("OSW_WTSITE ok", type(wtsite).__name__)

import opensemantic, opensemantic.core, opensemantic.base
print("OSW_MODELS ok")

try:
    import dask
    print("OSW_DASK present (unexpected, but harmless)")
except ImportError:
    print("OSW_DASK absent as intended")
print("OSW_DONE")
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--base", default="/w/extensions/JupyterLite/dist/")
    ap.add_argument("--notebook", default="start.ipynb")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    lab = f"https://{args.wiki}{args.base}lab/index.html?path={args.notebook}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        page.on("console", lambda m: print(f"  [console] {m.text}"[:200]))

        print(f"opening {lab}")
        page.goto(lab, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector(".jp-Notebook", timeout=args.timeout * 1000)

        print("running the import cell (this is the cold start) ...")
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
                ".some(o => o.innerText.includes('OSW_DONE') || o.innerText.includes('Error'))",
                timeout=args.timeout * 1000,
            )
        except Exception:  # noqa: BLE001 - report what the cell managed before timing out
            print("timed out waiting for the cell", file=sys.stderr)
        elapsed = time.monotonic() - started

        output = "\n".join(
            o.inner_text() for o in page.query_selector_all(".jp-OutputArea-output")
        )
        browser.close()

    print(f"\ncold start + full osw import: {elapsed:.1f}s")
    print("--- cell output ---")
    print(output.strip()[:2000])

    required = ["OSW_IMPORT ok", "OSW_WTSITE ok", "OSW_MODELS ok", "OSW_DONE"]
    missing = [marker for marker in required if marker not in output]
    if missing:
        print(f"\nFAIL: missing {missing}", file=sys.stderr)
        return 1
    if re.search(r"Traceback|ModuleNotFoundError", output):
        print("\nFAIL: the cell raised", file=sys.stderr)
        return 1

    print("\nPASS: osw-python imports and builds a WtSite in the browser, no credentials.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
