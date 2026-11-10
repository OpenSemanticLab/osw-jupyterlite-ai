"""Ask the wiki assistant to open the sandbox, and see whether it does.

The last untested link. `wiki_sandbox_tools.py` proves the page exposes what the backend
calls; `bridge.py` proves the lab answers. This drives the whole chain the way a user
does: type a sentence into the chat on a wiki page, and watch for a sandbox window.

The backend has to be one carrying the sandbox tools. It is reached through the
"Custom backend for iframe" preference rather than by repointing the wiki, so the
instance everybody else uses is untouched - see $wgChatbotAllowedBackendOrigins.

What is asserted is the observable outcome - a new window on the sandbox url - not the
model's wording, which is not ours to pin. A model that declines, or answers in prose
without calling the tool, fails this: the point is that the tool is reachable and works,
and if the prompt is not getting the model there, that is a real finding too.

    python tests/chatbot_sandbox.py --wiki wiki-dev.open-semantic-lab.org \\
        --login-user SandboxTestBot --login-password-file /tmp/sbpw
"""

from __future__ import annotations

import argparse
import json
import sys

from playwright.sync_api import sync_playwright

ASK = "Open the Python sandbox for me, please. Just open it, nothing else."

# The sandbox is an iframe on the page now, not a tab, so the observable outcome is a
# visible panel rather than a window event. That is also the more honest assertion: a
# window handle says a popup was requested, this says the user can see it.
PANEL = "#osw-sandbox-panel"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--login-user", required=True)
    ap.add_argument("--login-password-file", required=True)
    ap.add_argument("--page", default="Main_Page")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    password = open(args.login_password_file).read().strip()
    failures: list[str] = []
    opened: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            ignore_https_errors=True, viewport={"width": 1600, "height": 1000}
        )
        # The tool opens the sandbox with window.open; catching the new page is how we
        # observe that it actually happened rather than trusting the chat transcript.
        def on_page(pg):
            # The url at creation time is always about:blank - the navigation has not
            # happened yet - so wait for it to settle before recording it. Reading it
            # immediately made a working open_sandbox look like a failure.
            try:
                pg.wait_for_load_state("domcontentloaded", timeout=30_000)
            except Exception:  # noqa: BLE001
                pass
            opened.append(pg.url)

        context.on("page", on_page)

        page = context.new_page()
        page.goto(
            f"https://{args.wiki}/w/index.php?title=Special:UserLogin&returnto=Main+Page",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.fill("#wpName1", args.login_user)
        page.fill("#wpPassword1", password)
        page.click("#wpLoginAttempt")
        page.wait_for_load_state("domcontentloaded", timeout=60_000)

        print(f"opening {args.page} ...")
        page.goto(
            f"https://{args.wiki}/wiki/{args.page}",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_timeout(5000)

        backend = page.evaluate(
            """() => {
                const el = document.getElementById('chatbot_iframe');
                return el ? el.getAttribute('src') : null;
            }"""
        )
        print(f"  chatbot backend: {str(backend)[:90]}")
        if not backend:
            failures.append("no chatbot iframe on the page")
        elif "osw-chatbot-dev" not in backend:
            failures.append(
                f"the page is using {backend[:80]} - the preference pointing this user "
                "at the instance carrying the sandbox tools did not take"
            )

        if failures:
            browser.close()
            print("\nFAIL:", file=sys.stderr)
            for failure in failures:
                print(f"  {failure}", file=sys.stderr)
            return 1

        print("opening the chat and asking ...")
        page.click(".floating-chat")
        # Waited for rather than slept on: the popup expands with a transition, and a
        # fixed delay is the kind of flake that makes a suite untrustworthy.
        try:
            page.wait_for_selector(
                ".floating-chat .text-box", state="visible", timeout=30_000
            )
        except Exception:  # noqa: BLE001 - fall through to the diagnostic below
            pass

        # The chat itself lives inside the backend's iframe, cross-origin, so it has to
        # be driven through the frame rather than the page. `.text-box` on the wiki page
        # is vestigial markup and never receives anything.
        frame = page.frame_locator("#chatbot_iframe")
        try:
            frame.locator("textarea").first.wait_for(state="visible", timeout=120_000)
        except Exception:  # noqa: BLE001
            inner = next(
                (f.url for f in page.frames if "chatbot" in f.url), "(no chat frame)"
            )
            browser.close()
            print(
                f"\nFAIL: the chat never rendered an input. Frame: {inner[:90]}\n"
                "  A blank chat iframe usually means Panel refused the websocket - check "
                "BOKEH_ALLOW_WS_ORIGIN against the host it is served from.",
                file=sys.stderr,
            )
            return 1

        box = frame.locator("textarea").first
        box.click()
        box.type(ASK)
        page.keyboard.press("Enter")

        # The backend has to reach a model and come back, then the browser opens a
        # window; generous, because a cold Panel session plus a completion is not fast.
        try:
            page.wait_for_function(
                "() => window.__oswOpened === true",
                timeout=5000,
            )
        except Exception:  # noqa: BLE001 - the real signal is the window event below
            pass
        shown = False
        for _ in range(args.timeout // 5):
            shown = page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el || el.classList.contains('osw-sandbox-hidden')) return false;
                    const f = el.querySelector('iframe');
                    return !!f && (f.getAttribute('src') || '').includes('lab/index.html');
                }""",
                PANEL,
            )
            if shown:
                break
            page.wait_for_timeout(5000)

        transcript = ""
        for f in page.frames:
            if "chatbot" in f.url:
                try:
                    transcript = f.evaluate(
                        "() => document.body.innerText.slice(-1500)"
                    )
                except Exception:  # noqa: BLE001
                    transcript = "(could not read the chat frame)"
        browser.close()

    # Either surface counts. `open_sandbox` asks for a window first and only falls back
    # to the in-page panel when the browser blocks the popup, so requiring the panel
    # would fail precisely when the preferred path worked.
    in_window = any("JupyterLite" in url or "lab/index.html" in url for url in opened)
    shown = shown or in_window
    print(f"\n--- sandbox visible: {shown} "
          f"({'window' if in_window else 'panel' if shown else 'nowhere'}) ---")
    if opened:
        print(f"  (windows opened: {[u[:60] for u in opened]})")
    print("\n--- end of the chat ---")
    for line in [ln for ln in transcript.splitlines() if ln.strip()][-12:]:
        print(f"  {line[:150]}")

    if not shown:
        failures.append(
            "the sandbox never became visible, in a window or a panel - the "
            "assistant did not reach "
            "open_sandbox, or the browser side refused it"
        )

    if failures:
        print("\nFAIL:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("\nPASS: asking the assistant for the sandbox opened it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
