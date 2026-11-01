"""The wiki page's half of the chatbot's sandbox tools.

The assistant's sandbox tools are a chain: a langchain tool in the Panel backend, a
postMessage to the wiki page, `sandbox.js` there, a window holding the lab, and the
bridge plugin inside it. The backend end is covered by unit checks - the tools appear
only when `code-sandbox` is in the signed action list - and the lab end by
`tests/bridge.py`. This covers the middle, on a real wiki page, as a real logged-in user,
because that is where the pieces actually have to meet.

Driven through `mw.osw.sandbox` directly rather than through a chat message. The backend
is the one part that cannot be exercised here without standing up a second Panel instance
and repointing the wiki at it; what is asserted instead is that every call the backend
makes has something real to land on, and that it behaves.

    python tests/wiki_sandbox_tools.py --wiki wiki-dev.open-semantic-lab.org \\
        --login-user SandboxTestBot --login-password-file /tmp/sbpw
"""

from __future__ import annotations

import argparse
import json
import sys

from playwright.sync_api import sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--login-user", required=True)
    ap.add_argument("--login-password-file", required=True)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    password = open(args.login_password_file).read().strip()
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            ignore_https_errors=True, viewport={"width": 1600, "height": 1000}
        )
        page = context.new_page()
        console: list[str] = []
        page.on("console", lambda m: console.append(m.text))

        print(f"logging in as {args.login_user} ...")
        page.goto(
            f"https://{args.wiki}/w/index.php?title=Special:UserLogin&returnto=Main+Page",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.fill("#wpName1", args.login_user)
        page.fill("#wpPassword1", password)
        page.click("#wpLoginAttempt")
        page.wait_for_load_state("domcontentloaded", timeout=60_000)

        print("opening a wiki page ...")
        page.goto(
            f"https://{args.wiki}/wiki/Main_Page",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_timeout(6000)

        state = page.evaluate(
            """() => {
                const s = window.mw && mw.osw && mw.osw.sandbox;
                const j = window.mw && mw.osw && mw.osw.jupyterlite;
                return {
                    sandboxModule: !!s,
                    jupyterliteModule: !!j,
                    enabled: s ? s.isEnabled() : null,
                    basePath: s ? s.basePath() : null,
                    actions: window.mw ? mw.config.get('wgChatbotActions') : null,
                    toolboxLink: !!document.querySelector('#t-jupyterlite')
                };
            }"""
        )
        print("\n--- what the wiki page exposes ---")
        print(f"  {json.dumps(state)[:400]}")

        if not state["sandboxModule"]:
            failures.append(
                "mw.osw.sandbox is absent - sandbox.js did not load, so every sandbox "
                "tool the assistant calls would fail at the browser"
            )
        if not state["jupyterliteModule"]:
            failures.append("mw.osw.jupyterlite is absent - the launcher did not load")
        if state["enabled"] is not True:
            failures.append(
                f"the sandbox reports itself disabled (basePath={state['basePath']!r}); "
                "wgJupyterLiteBasePath is not exposed to this user"
            )
        if not state["toolboxLink"]:
            failures.append("no toolbox link - users have no way in from the wiki")
        actions = state["actions"] or []
        if "code-sandbox" not in actions:
            failures.append(
                f"code-sandbox is not in wgChatbotActions ({actions}), so the backend "
                "would never offer the sandbox tools"
            )

        # The notebook argument has to resolve to a wiki file, and anything else must be
        # refused - the sandbox runs with this user's rights, so an arbitrary url here
        # would be a way to have them edit the wiki without noticing.
        print("\nchecking the notebook allowlist ...")
        allow = page.evaluate(
            """async () => {
                const out = {};
                try { await mw.osw.sandbox.notebookUrl('https://evil.example/x.ipynb');
                      out.offOrigin = 'ACCEPTED'; }
                catch (e) { out.offOrigin = 'refused'; }
                try { await mw.osw.sandbox.notebookUrl('Main_Page');
                      out.notAFile = 'ACCEPTED'; }
                catch (e) { out.notAFile = 'refused'; }
                out.empty = await mw.osw.sandbox.notebookUrl('');
                return out;
            }"""
        )
        print(f"  {json.dumps(allow)}")
        if allow.get("offOrigin") != "refused":
            failures.append("an off-origin notebook url was accepted")
        if allow.get("notAFile") != "refused":
            failures.append("a non-File: page was accepted as a notebook")
        if allow.get("empty") is not None:
            failures.append("an empty notebook argument should mean 'empty sandbox'")

        # The hidden iframe: created on demand, revealed on demand, and the kernel kept
        # alive behind it. Driven through the api rather than the chat, because what is
        # being checked here is the page's half of the contract.
        print("\nchecking the hidden-iframe lifecycle ...")
        lifecycle = page.evaluate(
            """() => {
                const s = mw.osw.sandbox;
                const out = {};
                s.ensure();
                const el = document.getElementById('osw-sandbox-panel');
                const f = el && el.querySelector('iframe');
                out.createdHidden = !!el && el.classList.contains('osw-sandbox-hidden');
                out.pointsAtLab = !!f && (f.getAttribute('src') || '').includes('lab/index.html');
                s.show();
                out.visibleAfterShow = s.isVisible();
                s.hide();
                out.hiddenAfterHide = !s.isVisible();
                // Still there after hiding - that is the point, the kernel keeps running.
                out.framePersists = !!document.querySelector('#osw-sandbox-frame');
                out.poppedOut = s.isPoppedOut();
                return out;
            }"""
        )
        print(f"  {json.dumps(lifecycle)}")
        if not lifecycle.get("createdHidden"):
            failures.append("ensure() did not create the sandbox hidden")
        if not lifecycle.get("pointsAtLab"):
            failures.append("the sandbox iframe does not point at the lab")
        if not lifecycle.get("visibleAfterShow"):
            failures.append("show() did not reveal the sandbox")
        if not lifecycle.get("hiddenAfterHide"):
            failures.append("hide() did not hide the sandbox")
        if not lifecycle.get("framePersists"):
            failures.append(
                "hiding removed the iframe - the kernel would die and the cold start "
                "would be paid again on the next reveal"
            )

        browser.close()

    if failures:
        print("\nFAIL:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(
        "\nPASS: the wiki page exposes the sandbox client, the launcher and the "
        "code-sandbox action, and refuses notebooks that are not wiki files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
