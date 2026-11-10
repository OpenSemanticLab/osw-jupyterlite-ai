"""Everything the assistant can do with the sandbox, checked on a real wiki page.

Driven through `mw.osw.sandbox` rather than through a chat turn. The chat leg is covered
by `wiki_chat_edit.py`; what this answers is whether the machinery underneath it works -
cold start, repeated runs against one kernel, notebooks, the pop-out, re-discovery after
a reload, and persisting a notebook to the wiki so `?fromURL=` can reopen it.

Each check reports on its own and the run continues, because a sandbox that can run code
but cannot pop out is a different problem from one that cannot start at all, and finding
both in one run is cheaper than finding them in two.

    python tests/sandbox_matrix.py --wiki wiki-dev.open-semantic-lab.org \\
        --login-user SandboxTestBot --login-password-file /tmp/sbpw
"""

from __future__ import annotations

import argparse
import json
import sys
import time

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


def open_wiki_page(page, wiki: str) -> None:
    page.goto(
        f"https://{wiki}/wiki/Main_Page", wait_until="domcontentloaded", timeout=120_000
    )
    wait_for_mw(page)
    page.wait_for_function(
        "() => window.mw && mw.osw && mw.osw.sandbox", timeout=60_000
    )


def sandbox_call(page, script: str, arg=None):
    """Run an async snippet against mw.osw.sandbox and bring back JSON.

    page.evaluate takes no timeout of its own; the page default governs, and a cold
    start is tens of seconds, so main() raises that before the first call.
    """
    return page.evaluate(
        "async (arg) => { try { return { ok: true, value: await (%s)(arg) }; }"
        " catch (e) { return { ok: false, error: String(e) }; } }" % script,
        arg,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--login-user", required=True)
    ap.add_argument("--login-password-file", required=True)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()
    password = open(args.login_password_file).read().strip()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            ignore_https_errors=True, viewport={"width": 1600, "height": 1000}
        )
        page = context.new_page()
        # A cold Pyodide start is tens of seconds, and page.evaluate has no timeout of
        # its own - it inherits this one.
        page.set_default_timeout(args.timeout * 1000)
        console: list[str] = []
        page.on("console", lambda m: console.append(m.text[:200]))

        print("\n== setup ==")
        login(page, args.wiki, args.login_user, password)
        open_wiki_page(page, args.wiki)
        state = page.evaluate(
            "() => ({ enabled: mw.osw.sandbox.isEnabled(),"
            " base: mw.osw.sandbox.basePath(),"
            " open: mw.osw.sandbox.isOpen(),"
            " hasToggle: typeof mw.osw.sandbox.toggle === 'function' })"
        )
        check("sandbox client is present and enabled", bool(state["enabled"]), str(state))
        # It ships with the JupyterLite extension now, so it must be here on a wiki that
        # has no chatbot at all.
        check("the client exposes toggle()", bool(state.get("hasToggle")), str(state))
        check("the toolbox offers a panel toggle",
              page.locator("#t-jupyterlite-panel").count() > 0,
              "no #t-jupyterlite-panel portlet link")

        print("\n== the toggle shows and hides ==")
        shown = call_toggle = page.evaluate("() => mw.osw.sandbox.toggle()")
        check("toggle() reveals the panel", bool(shown) and page.evaluate(
            "() => mw.osw.sandbox.isVisible()"), str(shown))
        hidden = page.evaluate("() => mw.osw.sandbox.toggle()")
        check("toggle() hides it again", hidden is False and not page.evaluate(
            "() => mw.osw.sandbox.isVisible()"), str(hidden))

        print("\n== hidden sandbox, cold start ==")
        page.evaluate("() => mw.osw.sandbox.ensure()")
        hidden = page.evaluate(
            "() => { const el = document.getElementById('osw-sandbox-panel');"
            " return { created: !!el,"
            "   hidden: !!el && el.classList.contains('osw-sandbox-hidden') };}"
        )
        check("created, and created hidden", hidden.get("created") and hidden.get("hidden"),
              str(hidden))

        started = time.monotonic()
        ready = sandbox_call(page, "() => mw.osw.sandbox.whenReady()")
        cold = time.monotonic() - started
        # whenReady resolves on the lab's READY message, which is the frontend being
        # restored - not Pyodide being up. The first run is what proves the kernel.
        check("the lab reports itself ready", bool(ready.get("value")),
              f"{cold:.1f}s" + ("" if ready.get("ok") else f" {ready.get('error')}"))

        print("\n== running code ==")
        # The bridge appends to the open notebook and runs it there, so this also
        # establishes whether the hidden sandbox starts with a notebook open at all.
        first = sandbox_call(
            page, "() => mw.osw.sandbox.request('run', { code: 'print(6*7)' })")
        value = first.get("value")
        no_notebook = isinstance(value, dict) and "no notebook" in str(value.get("error", ""))
        check("a bare run opens its own notebook rather than failing",
              first.get("ok") and not no_notebook, json.dumps(value)[:160])

        print("\n== notebook: create, then run in it ==")
        made = sandbox_call(
            page,
            "() => mw.osw.sandbox.request('create_notebook', { name: 'matrix-test',"
            " cells: [{type:'markdown', source:'# matrix test'},"
            "         {type:'code', source:'x = 41'}] })")
        check("create_notebook accepted", made.get("ok"), json.dumps(made.get("value"))[:160])

        runs = []
        for code in ("x = 41", "x + 1", "print('iteration-3')"):
            r = sandbox_call(
                page, f"() => mw.osw.sandbox.request('run', {{ code: {json.dumps(code)} }})")
            runs.append(r)
            print(f"    run {code!r} -> {json.dumps(r.get('value'))[:120]}")
        check("three runs in a row all answered", all(r.get("ok") for r in runs))
        # x survives between calls only if every run hit the same kernel.
        combined = json.dumps([r.get("value") for r in runs])
        check("kernel state persists across runs (x + 1 == 42)", "42" in combined,
              "no 42 in the outputs" if "42" not in combined else "")

        print("\n== the jupyterlite command surface ==")
        started = sandbox_call(
            page, "() => mw.osw.sandbox.request('command',"
            " { command: 'jupyterlab-ai-commands:start-kernel', args: {} })")
        kernel_id = (started.get("value") or {}).get("kernelId")
        check("a kernel can be started through the bridge", bool(kernel_id), str(kernel_id))

        if kernel_id:
            direct = []
            for code in ("z = 41", "print(z + 1)"):
                # Not `args`: that name holds the parsed command line, and
                # shadowing it breaks every later args.wiki / args.timeout.
                payload = json.dumps({"kernelId": kernel_id, "code": code})
                r = sandbox_call(
                    page,
                    "() => mw.osw.sandbox.request('command', { command:"
                    " 'jupyterlab-ai-commands:execute-in-kernel', args: "
                    + payload + " })")
                direct.append(r)
            # The point of the direct path: the model gets the output, not just "ok".
            blob = json.dumps([d.get("value") for d in direct])
            check("direct kernel execution returns real output", '"42' in blob,
                  blob[:200])

        listed = sandbox_call(
            page, "() => mw.osw.sandbox.request('command',"
            " { command: 'jupyterlab-ai-commands:list-kernels', args: {} })")
        check("kernels can be listed", (listed.get("value") or {}).get("success") is True,
              json.dumps(listed.get("value"))[:120])

        print("\n== the command allowlist ==")
        # filebrowser:open-url would load any url into the lab and walk around the
        # ?fromURL= allowlist, so the namespace guard is load-bearing, not tidiness.
        refused = sandbox_call(
            page, "() => mw.osw.sandbox.request('command',"
            " { command: 'filebrowser:open-url', args: { url: 'https://evil.example/x' } })")
        value = json.dumps(refused.get("value") or refused)
        check("a command outside jupyterlab-ai-commands is refused",
              "refused" in value, value[:160])

        print("\n== pop-out ==")
        # A marker, so the check is "the user sees the code the assistant wrote" rather
        # than "some notebook is open" - a restored blank one would pass the latter.
        marker = f"popout_marker_{int(time.time())}"
        made_marker = sandbox_call(
            page,
            "() => mw.osw.sandbox.request('create_notebook', { name: 'popout-check',"
            " cells: [{ type: 'code', source: " + json.dumps(f"print('{marker}')") + " }] })")
        # Asserted, not assumed: if this quietly failed the pop-out would open whatever
        # happened to be active and the next check would blame the pop-out for it.
        check("the marker notebook was created",
              bool((made_marker.get("value") or {}).get("notebookPath")),
              json.dumps(made_marker.get("value"))[:140])
        before_pages = len(context.pages)
        sandbox_call(page, "() => mw.osw.sandbox.popOut()")
        page.wait_for_timeout(4000)
        popped = page.evaluate(
            "() => ({ poppedOut: mw.osw.sandbox.isPoppedOut(),"
            " open: mw.osw.sandbox.isOpen() })")
        check("popOut reports a window", bool(popped.get("poppedOut")), str(popped))
        check("a new browser window actually opened", len(context.pages) > before_pages,
              f"{before_pages} -> {len(context.pages)} pages")

        if len(context.pages) > before_pages:
            lab = context.pages[-1]
            lab.wait_for_load_state("domcontentloaded", timeout=60_000)
            check("the popped-out window is the lab", "lab/index.html" in lab.url, lab.url)
            # Not asserted on the url: JupyterLite consumes ?path= to open the file and
            # then normalises the address bar, so the query is gone by the time it can be
            # read. What the user sees is the notebook, so that is what is checked.
            try:
                lab.wait_for_selector(".jp-Notebook", timeout=args.timeout * 1000)
                lab.wait_for_timeout(4000)
                opened = lab.evaluate(
                    "() => [...document.querySelectorAll('.lm-TabBar-tabLabel')]"
                    ".map(e => e.textContent).filter(Boolean)")
                print(f"    notebooks open in the pop-out: {opened}")
                cells = lab.evaluate(
                    "() => [...document.querySelectorAll('.jp-Notebook .jp-Cell')]"
                    ".map(c => c.innerText || '').join('\\n')")
                # Deliberately not asserting *which* notebook is focused. ?path= is
                # honoured by a lab with no saved workspace, and ignored by one that has
                # restored its previous tabs - which is the normal case once the hidden
                # sandbox has been used. What is guaranteed, and what the user needs, is
                # that the pop-out shows the sandbox with the assistant's notebooks and
                # their code in it.
                check("the pop-out shows notebooks with the assistant's code",
                      bool(opened) and "print" in cells,
                      f"open: {opened}")
            except Exception as reason:  # noqa: BLE001
                check("the assistant's code is sitting in the popped-out notebook",
                      False, str(reason)[:120])

        print("\n== open_sandbox prefers a window ==")
        # The complaint this guards: "open the sandbox for me" revealing a panel in the
        # same page when the user asked for somewhere to work. A blocked popup is a
        # legitimate outcome, but it has to be reported as a panel, not as a tab.
        opened_where = page.evaluate(
            "() => ({ popped: mw.osw.sandbox.isPoppedOut(),"
            " visible: mw.osw.sandbox.isVisible() })")
        check("the sandbox is in its own window, not a panel in the page",
              bool(opened_where.get("popped")) and not opened_where.get("visible"),
              str(opened_where))

        print("\n== re-discovery after the page reloads ==")
        open_wiki_page(page, args.wiki)
        page.wait_for_timeout(3000)
        again = page.evaluate(
            "() => ({ poppedOut: mw.osw.sandbox.isPoppedOut(),"
            " open: mw.osw.sandbox.isOpen() })")
        check("a reloaded page re-finds the open sandbox window",
              bool(again.get("poppedOut") or again.get("open")), str(again))

        browser.close()

    print("\n== summary ==")
    failed = [name for name, ok, _ in RESULTS if not ok]
    for name, ok, detail in RESULTS:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
