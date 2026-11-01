"""Control for same_origin_session.py.

A "no cookie" result there has two possible causes: the worker really does not send
them, or Playwright simply does not report the Cookie header. This separates the two by
making the *same* request three ways - page fetch, page XHR, and the Pyodide kernel -
and comparing what the observer sees for each. A page fetch that also shows no cookie
means the observer is the problem, not the kernel.
"""

from __future__ import annotations

import argparse

from playwright.sync_api import sync_playwright

NAME = "osw_sandbox_probe"
VALUE = "session-carried"

KERNEL_CELL = """
import pyodide_http; pyodide_http.patch_all()
import requests, js
r = requests.get(f"https://{js.location.hostname}/w/api.php?action=query&meta=siteinfo&format=json")
print("KERNEL", r.status_code)
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--base", default="/w/extensions/JupyterLite/dist/")
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    seen: list[tuple[str, str]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)
        context.add_cookies(
            [{"name": NAME, "value": VALUE, "domain": args.wiki, "path": "/"}]
        )

        def on_request(request) -> None:
            if "/w/api.php" not in request.url:
                return
            try:
                cookie = request.all_headers().get("cookie", "")
            except Exception:  # noqa: BLE001
                cookie = "<unreadable>"
            tag = "page" if "siteinfo" not in request.url else "?"
            seen.append((tag, cookie))

        context.on("request", on_request)
        page = context.new_page()
        page.on("console", lambda m: print(f"  [console] {m.text}"[:200]))

        page.goto(
            f"https://{args.wiki}{args.base}lab/index.html?path=start.ipynb",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_selector(".jp-Notebook", timeout=args.timeout * 1000)

        print("cookies the context holds for this origin:")
        for c in context.cookies(f"https://{args.wiki}/"):
            print(f"  {c['name']}={c['value'][:20]} domain={c['domain']} path={c['path']}")

        print("\n-- page fetch (credentials: same-origin, the browser default) --")
        res = page.evaluate(
            """async () => {
                const r = await fetch('/w/api.php?action=query&meta=userinfo&format=json');
                return { status: r.status, body: (await r.text()).slice(0, 120) };
            }"""
        )
        print(f"  status={res['status']} body={res['body']}")

        print("\n-- does the page itself see the cookie in document.cookie? --")
        print("  document.cookie =", page.evaluate("() => document.cookie"))

        print("\n-- kernel request --")
        page.click(".jp-Notebook")
        page.keyboard.press("Escape")
        page.keyboard.press("b")
        page.keyboard.press("Enter")
        page.keyboard.insert_text(KERNEL_CELL.strip())
        page.keyboard.press("Shift+Enter")
        page.wait_for_function(
            "() => [...document.querySelectorAll('.jp-OutputArea-output')]"
            ".some(o => o.innerText.includes('KERNEL'))",
            timeout=args.timeout * 1000,
        )

        browser.close()

    print("\n-- observed Cookie headers on /w/api.php requests --")
    for tag, cookie in seen:
        print(f"  cookie={cookie!r}")
    print(
        "\nIf every entry is empty, including the page fetch, the observer cannot see\n"
        "Cookie headers and this proves nothing either way."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
