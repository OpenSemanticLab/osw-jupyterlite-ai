"""Prove the claim the whole deployment shape rests on.

The sandbox is served from the wiki's own origin so that the in-browser kernel inherits
the reader's MediaWiki session and `osw-python` needs no credential. That is a claim about
how Pyodide's HTTP shim behaves inside a Web Worker, not something the Python side can
check, so it is checked here: a cookie is planted on the wiki origin, a notebook cell is
run, and the *server* is asked what it received.

Asking the server is the only honest way to do it. Watching the request from Playwright
does not work - `request.all_headers()` reports no Cookie header even for a plain page
`fetch()` that demonstrably sends one, so an observer outside the browser cannot tell the
two cases apart. `cookie_control.py` exists to demonstrate exactly that.

Needs no wiki account. A real login would prove the same thing, but a test that creates a
user on a shared wiki is a test nobody wants to run twice.

Requires `probe.php` deployed in the JupyterLite extension directory; it reports cookie
*names* only, never values. Remove it afterwards - it is a verification aid, not an
endpoint.

    python tests/same_origin_session.py --wiki wiki-dev.open-semantic-lab.org
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

from playwright.sync_api import sync_playwright

PROBE_NAME = "osw_sandbox_probe"
PROBE_VALUE = "session-carried"

# Run in the kernel, not in page JS: page JS would prove only that the *page* sends
# cookies, which was never in doubt. The question is the worker the kernel runs in.
CELL = """
import pyodide_http; pyodide_http.patch_all()
import requests, js
r = requests.get(f"https://{js.location.hostname}/w/extensions/JupyterLite/probe.php")
print("PROBE_RESULT", r.status_code, r.text[:300])
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True, help="wiki hostname, no scheme")
    ap.add_argument("--base", default="/w/extensions/JupyterLite/dist/")
    ap.add_argument("--timeout", type=int, default=240, help="seconds to wait for a kernel")
    ap.add_argument("--notebook", default="start.ipynb")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    lab = f"https://{args.wiki}{args.base}lab/index.html"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        context = browser.new_context(ignore_https_errors=True)
        # Stands in for the MediaWiki session cookie: same origin, same path scope, so
        # whatever carries this would carry the real one.
        context.add_cookies(
            [{"name": PROBE_NAME, "value": PROBE_VALUE, "domain": args.wiki, "path": "/"}]
        )
        page = context.new_page()
        page.on("console", lambda m: print(f"  [console] {m.type}: {m.text}"[:240]))

        # Straight to the notebook rather than via the launcher: the launcher's cards are
        # matched by visible text that also appears in the help panel, and a test that
        # depends on which renders first fails randomly.
        url = f"{lab}?path={args.notebook}"
        print(f"opening {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)

        print("waiting for the notebook ...")
        page.wait_for_selector(".jp-Notebook", timeout=args.timeout * 1000)

        print("running the probe cell ...")
        page.click(".jp-Notebook")
        page.keyboard.press("Escape")
        page.keyboard.press("b")  # below, so the notebook's own cells stay intact
        page.keyboard.press("Enter")
        page.keyboard.insert_text(CELL.strip())

        # This is the cold start the UX has to budget for: Pyodide boots and this cell's
        # wheels load before a line of it runs. Reported so a regression in the wheel set
        # shows up as a number rather than as a vague feeling.
        started = time.monotonic()
        page.keyboard.press("Shift+Enter")
        page.wait_for_function(
            "() => [...document.querySelectorAll('.jp-OutputArea-output')]"
            ".some(o => o.innerText.includes('PROBE_RESULT'))",
            timeout=args.timeout * 1000,
        )
        elapsed = time.monotonic() - started

        outputs = page.query_selector_all(".jp-OutputArea-output")
        output = "\n".join(o.inner_text() for o in outputs)
        browser.close()

    print(f"\ncold start + request: {elapsed:.1f}s")
    match = re.search(r"PROBE_RESULT (\d+) (\{.*)", output, re.S)
    if not match:
        print("FAIL: the cell produced no probe result:\n" + output[:600], file=sys.stderr)
        return 1

    status, body = match.group(1), match.group(2).strip()
    print(f"probe responded {status}: {body[:200]}")
    if status != "200":
        print(f"\nFAIL: probe.php returned {status} - is it deployed?", file=sys.stderr)
        return 1

    try:
        reported = json.loads(body[: body.rindex("}") + 1])
    except ValueError:
        print(f"\nFAIL: probe response was not json: {body[:200]}", file=sys.stderr)
        return 1

    if not reported.get("has_probe"):
        print(
            "\nFAIL: the kernel reached the wiki without the origin's cookies.\n"
            "The same-origin session assumption does not hold, and osw-python would need\n"
            f"a credential after all. Server saw: {reported.get('cookie_names')}",
            file=sys.stderr,
        )
        return 1

    print(
        f"\nPASS: the kernel's request carried the origin's cookies "
        f"(sec-fetch-dest={reported.get('ua_is_worker')!r}, so it came from the worker, "
        "not a navigation)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
