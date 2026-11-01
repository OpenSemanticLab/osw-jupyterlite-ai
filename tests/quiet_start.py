"""The starter notebook should run without looking like it failed.

Jupyter paints anything a cell writes to stderr on a red background, so library chatter -
an INFO banner, a deprecation warning from a dependency's internals - reads as an error to
anyone who did not write the library. A first-run sandbox that greets a user with a wall
of red is a sandbox they do not trust.

So this asserts the absence of stderr output, which is the thing a user actually sees,
rather than the absence of any particular message. A new noisy dependency fails it without
anyone having to predict which message it will print.

    python tests/quiet_start.py --wiki wiki-dev.open-semantic-lab.org
"""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

# Same shape as the starter notebook's first cells. Written here rather than driving
# start.ipynb so the test says exactly what it exercises.
# Split exactly as the notebook does: the import installs the warning filters, and only
# the cell after it uses await. Combined in one cell, the kernel's own deprecation warning
# fires before any filter can be in place.
#
# Each carries its own marker so the runner can wait for *that* cell to finish before
# typing the next. A fixed sleep is not enough: the install cell takes ~40s on a cold
# kernel, and keystrokes sent meanwhile land in the wrong cell and fail at random.
CELLS = [
    ("import osw_bootstrap\nprint('STEP1')", "STEP1"),
    ("await osw_bootstrap.setup()\nprint('STEP2')", "STEP2"),
    ("wtsite = osw_bootstrap.wtsite()\nprint('WTSITE READY')", "WTSITE READY"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--base", default="/w/extensions/JupyterLite/dist/")
    ap.add_argument("--notebook", default="start.ipynb")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    url = f"https://{args.wiki}{args.base}lab/index.html?path={args.notebook}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(ignore_https_errors=True).new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector(".jp-Notebook", timeout=args.timeout * 1000)

        page.click(".jp-Notebook")
        page.keyboard.press("Escape")
        # One cell at a time, each run as it is added. Selecting all and running would
        # also run the notebook's own cells, whose output is not what this is about.
        for source, marker in CELLS:
            page.keyboard.press("b")
            page.keyboard.press("Enter")
            page.keyboard.insert_text(source)
            page.keyboard.press("Shift+Enter")
            page.wait_for_function(
                "m => [...document.querySelectorAll('.jp-OutputArea-output')]"
                ".some(o => o.innerText.includes(m) || o.innerText.includes('Traceback'))",
                arg=marker,
                timeout=args.timeout * 1000,
            )
        page.wait_for_timeout(1500)

        # `.jp-OutputArea-output[data-mime-type]` covers every output; the stderr ones
        # carry jp-RenderedText with a data-mime-type of application/vnd.jupyter.stderr.
        stderr_blocks = page.eval_on_selector_all(
            ".jp-OutputArea-output",
            """nodes => nodes
                .filter(n => (n.getAttribute('data-mime-type') || '')
                    .includes('vnd.jupyter.stderr'))
                .map(n => n.innerText.trim())""",
        )
        all_output = page.eval_on_selector_all(
            ".jp-OutputArea-output", "nodes => nodes.map(n => n.innerText.trim())"
        )
        browser.close()

    print("--- cell output ---")
    for block in all_output:
        for line in block.splitlines()[:6]:
            print(f"  {line[:150]}")

    if not any("WTSITE READY" in block for block in all_output):
        print("\nFAIL: the cells did not run to completion", file=sys.stderr)
        return 1

    if stderr_blocks:
        print("\nFAIL: the starter cells wrote to stderr, which renders red:", file=sys.stderr)
        for block in stderr_blocks:
            for line in block.splitlines()[:12]:
                print(f"  {line[:160]}", file=sys.stderr)
        return 1

    print("\nPASS: the starter cells produced no stderr output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
