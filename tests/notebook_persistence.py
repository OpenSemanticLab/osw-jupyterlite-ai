"""A notebook saved to the wiki can be reopened from it, in a fresh sandbox.

The sandbox keeps notebooks in the browser's own storage, which does not survive clearing
site data and belongs to one machine. Anything worth keeping has to reach the wiki. This
drives that whole round trip: write a notebook, upload it, confirm the File: page exists,
then open a *new* sandbox on it through `?fromURL=` and check the cells came back.

Deliberately end to end rather than three unit checks. Each half has been fine on its own
before while the join was broken - an upload that stored an empty notebook looks exactly
like a successful save until someone reopens it.

    python tests/notebook_persistence.py --wiki wiki-dev.open-semantic-lab.org \\
        --login-user SandboxTestBot --login-password-file /tmp/sbpw
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid

from playwright.sync_api import sync_playwright

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return ok


def wait_for_mw(page, timeout: int = 60) -> None:
    page.wait_for_function(
        "() => window.mw && mw.loader && typeof mw.loader.using === 'function'",
        timeout=timeout * 1000,
    )


def login(page, wiki: str, user: str, password: str) -> None:
    page.goto(
        f"https://{wiki}/w/index.php?title=Special:UserLogin&returnto=Main+Page",
        wait_until="domcontentloaded",
        timeout=120_000,
    )
    page.fill("#wpName1", user)
    page.fill("#wpPassword1", password)
    page.click("#wpLoginAttempt")
    page.wait_for_load_state("domcontentloaded", timeout=60_000)
    wait_for_mw(page)


def call(page, script: str):
    return page.evaluate(
        "async () => { try { return { ok: true, value: await (%s)() }; }"
        " catch (e) { return { ok: false, error: String(e) }; } }" % script
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--login-user", required=True)
    ap.add_argument("--login-password-file", required=True)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()
    password = open(args.login_password_file).read().strip()

    marker = uuid.uuid4().hex[:8]
    filename = f"persistence_test_{marker}.ipynb"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            ignore_https_errors=True, viewport={"width": 1500, "height": 1000}
        )
        page = context.new_page()
        page.set_default_timeout(args.timeout * 1000)

        print("\n== write a notebook in the sandbox ==")
        login(page, args.wiki, args.login_user, password)
        page.goto(
            f"https://{args.wiki}/wiki/Main_Page",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        wait_for_mw(page)
        page.wait_for_function("() => window.mw && mw.osw && mw.osw.sandbox", timeout=60_000)

        page.evaluate("() => mw.osw.sandbox.ensure()")
        ready = call(page, "() => mw.osw.sandbox.whenReady()")
        check("the sandbox started", bool(ready.get("value")), str(ready)[:120])

        made = call(
            page,
            "() => mw.osw.sandbox.request('create_notebook', { name: 'persistence',"
            " cells: [{ type: 'code', source: " + json.dumps(f"print('{marker}')") + " }] })",
        )
        check("a notebook was written", bool((made.get("value") or {}).get("notebookPath")),
              json.dumps(made.get("value"))[:140])

        print("\n== upload it to the wiki ==")
        saved = call(
            page,
            "() => mw.osw.sandbox.request('save_notebook_to_wiki', { filename: "
            + json.dumps(filename) + ", comment: 'persistence test' })",
        )
        value = saved.get("value") or {}
        title = value.get("title")
        check("the upload reported a file page", bool(title), json.dumps(value)[:200])

        print("\n== the wiki really has it ==")
        if title:
            info = page.evaluate(
                """async (title) => {
                    await mw.loader.using('mediawiki.api');
                    const r = await new mw.Api().get({ action: 'query', prop: 'imageinfo',
                        iiprop: 'url|size', titles: title });
                    const pages = Object.values(r.query.pages || {});
                    const p = pages[0] || {};
                    return { missing: 'missing' in p,
                             url: (p.imageinfo || [{}])[0].url || null,
                             size: (p.imageinfo || [{}])[0].size || 0 };
                }""",
                title,
            )
            check("the file page exists on the wiki", not info.get("missing"), str(info))
            # An empty upload is the failure that looks like success: the cells live in
            # an in-memory model until something saves them.
            check("the uploaded notebook is not empty", (info.get("size") or 0) > 100,
                  f"{info.get('size')} bytes")

            print("\n== reopen it in a fresh sandbox through ?fromURL= ==")
            file_url = info.get("url")
            if file_url:
                lab = context.new_page()
                lab.set_default_timeout(args.timeout * 1000)
                base = f"https://{args.wiki}/w/extensions/JupyterLite/dist/lab/index.html"
                lab.goto(f"{base}?fromURL={file_url}", wait_until="domcontentloaded",
                         timeout=120_000)
                try:
                    lab.wait_for_selector(".jp-Notebook", timeout=args.timeout * 1000)
                    lab.wait_for_timeout(5000)
                    cells = lab.evaluate(
                        "() => [...document.querySelectorAll('.jp-Notebook .jp-Cell')]"
                        ".map(c => c.innerText || '').join('\\n')")
                    check("the notebook came back with its code",
                          marker in cells, (cells or "no cells")[:140])
                except Exception as reason:  # noqa: BLE001
                    check("the notebook came back with its code", False, str(reason)[:140])

        browser.close()

    print("\n== summary ==")
    failed = [name for name, ok, _ in RESULTS if not ok]
    for name, ok, _ in RESULTS:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    print(f"  the uploaded file is {filename} - the test account cannot delete it")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
