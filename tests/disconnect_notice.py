"""When the backend goes away, does the chat say so?

The reported symptom: the websocket drops, the user types and presses Enter, and the
message simply disappears. The input box is cleared by the widget's own JavaScript, which
needs no server, while the message itself never reaches one - so the chat looks like it
read the message and chose to ignore it.

Panel has always produced a `disconnect_notification` on websocket close, but
`pn.config.notifications` defaulted to False, so there was no notification area to render
it into and the message was made and dropped. That is the kind of fix that is easy to
believe without checking: the config reads correctly, and nothing tells you the area was
never mounted.

So this drives the real thing - open the chat, drop the backend, and look for the notice
on screen.

The backend has to be stopped from outside: taking the *browser* offline does not close
an already-open websocket, so Panel never sees a disconnect and the notice never fires.
That is not a quirk of the test - it is the boundary of the fix. Panel reacts to a close
from the server; a connection that dies silently, with no close frame, stays unnoticed
until a ping times out.

The run prints READY once the chat is up, then waits:

    python tests/disconnect_notice.py --wiki wiki-dev.open-semantic-lab.org \\
        --login-user SandboxTestBot --login-password-file /tmp/sbpw &
    # wait for READY, then
    docker stop osw-chat-dev && sleep 25 && docker start osw-chat-dev
"""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

#: Enough of the configured message to be unambiguous, short enough to survive rewording
#: of the rest of the sentence.
NOTICE = "Connection to the assistant lost"


def log(message: str) -> None:
    print(f"  {message}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--login-user", required=True)
    ap.add_argument("--login-password-file", required=True)
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()
    password = open(args.login_password_file).read().strip()

    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            ignore_https_errors=True, viewport={"width": 1500, "height": 1000}
        )
        page = context.new_page()
        page.set_default_timeout(args.timeout * 1000)

        page.goto(
            f"https://{args.wiki}/w/index.php?title=Special:UserLogin&returnto=Main+Page",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.fill("#wpName1", args.login_user)
        page.fill("#wpPassword1", password)
        page.click("#wpLoginAttempt")
        page.wait_for_load_state("domcontentloaded", timeout=60_000)

        page.goto(f"https://{args.wiki}/wiki/Main_Page",
                  wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector(".floating-chat", timeout=args.timeout * 1000)
        page.click(".floating-chat")

        frame = page.frame_locator("#chatbot_iframe")
        frame.locator("input.bk-input, textarea.bk-input").first.wait_for(
            state="visible", timeout=args.timeout * 1000
        )
        # The greeting arrives after the input appears; cutting the line before the app
        # has finished its first render would test a different failure.
        page.wait_for_timeout(6000)
        log("chat is open and the backend answered")

        print("READY", flush=True)
        log("waiting for the backend to be stopped")

        shown = ""
        for _ in range(args.timeout // 2):
            for candidate in page.frames:
                if "chatbot" not in candidate.url and "main" not in candidate.url:
                    continue
                try:
                    body = candidate.evaluate("() => document.body.innerText")
                except Exception:  # noqa: BLE001 - a frame can detach mid-poll
                    continue
                if NOTICE.lower() in (body or "").lower():
                    shown = body
                    break
            if shown:
                break
            page.wait_for_timeout(2000)

        browser.close()

    if shown:
        line = next((l for l in shown.splitlines() if NOTICE.lower() in l.lower()), "")
        log(f"notice shown: {line.strip()[:120]}")
    else:
        failures.append(
            "no disconnect notice appeared - a dropped backend is still silent, and a "
            "message typed now would vanish with no explanation"
        )

    if failures:
        print("\nFAIL:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("\nPASS: a dropped connection tells the user instead of swallowing the message.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
