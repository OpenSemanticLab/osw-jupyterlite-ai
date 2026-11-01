"""Run one cell in the deployed sandbox and print what it produced.

A debugging tool, not a test. The kernel is the only place some questions can be answered
- micropip's resolver behaves differently there than pip does anywhere else - and poking
at it through a notebook by hand is slow.

    python tests/run_cell.py --wiki wiki-dev.open-semantic-lab.org --file cell.py
    echo 'print(1+1)' | python tests/run_cell.py --wiki ...
"""

from __future__ import annotations

import argparse
import sys
import time

from playwright.sync_api import sync_playwright

SENTINEL = "__CELL_DONE__"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--base", default="/w/extensions/JupyterLite/dist/")
    ap.add_argument("--notebook", default="start.ipynb")
    ap.add_argument("--file", help="read the cell from here instead of stdin")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    source = open(args.file).read() if args.file else sys.stdin.read()
    # Appended so there is always something to wait for, including when the cell raises:
    # a bare traceback has no marker of its own and the wait would just time out.
    cell = f"{source.strip()}\nprint({SENTINEL!r})"

    url = f"https://{args.wiki}{args.base}lab/index.html?path={args.notebook}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(ignore_https_errors=True).new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector(".jp-Notebook", timeout=args.timeout * 1000)
        page.click(".jp-Notebook")
        page.keyboard.press("Escape")
        page.keyboard.press("b")
        page.keyboard.press("Enter")
        page.keyboard.insert_text(cell)

        started = time.monotonic()
        page.keyboard.press("Shift+Enter")
        try:
            page.wait_for_function(
                "(s) => [...document.querySelectorAll('.jp-OutputArea-output')]"
                ".some(o => o.innerText.includes(s) || o.innerText.includes('Traceback'))",
                arg=SENTINEL,
                timeout=args.timeout * 1000,
            )
        except Exception:  # noqa: BLE001 - print whatever it managed first
            print("(timed out)", file=sys.stderr)
        elapsed = time.monotonic() - started

        out = "\n".join(
            o.inner_text() for o in page.query_selector_all(".jp-OutputArea-output")
        )
        browser.close()

    print(out.replace(SENTINEL, "").strip())
    print(f"\n[{elapsed:.1f}s]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
