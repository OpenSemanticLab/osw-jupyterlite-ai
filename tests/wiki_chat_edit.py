"""Drive a real chat on a real wiki and watch it write a page.

Every other test here checks a piece: the gate resolves, the refusal is phrased right,
the history round trips. None of them exercise the loop that matters - the model asks to
write, is refused, reads the skill, and tries again - because that loop only exists when
a real model is reading real tool observations in a real browser session.

    python tests/wiki_chat_edit.py --wiki wiki-dev.open-semantic-lab.org \\
        --login-user SandboxTestBot --login-password-file /tmp/sbpw \\
        --backend https://osw-chatbot-dev.demo.open-semantic-lab.org/main

Writes a page and does not clean up - the test account has no delete right, and a run
that silently removed its evidence would be harder to diagnose when it fails. Point it at
a scratch wiki, or expect an `Item:` labelled "Chat edit test <hex>" per run.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import uuid

from playwright.sync_api import sync_playwright

#: Message containers, for counting that a send landed.
BUBBLES = "div.message, .bk-panel-models-chat-ChatMessage"

#: Where the text actually is. Panel renders each bubble into a shadow root, so
#: inner_text on the container returns nothing while a locator for the elements inside
#: pierces the boundary and reads fine.
#:
#: `pre` and `code` matter as much as `p`: the assistant puts command output and source
#: in code blocks, so a paragraph-only selector reads the sentence introducing a result
#: and silently drops the result itself.
BUBBLE_TEXT = (
    "div.message p, div.message pre, div.message code, div.message li,"
    " .bk-panel-models-chat-ChatMessage p, .bk-panel-models-chat-ChatMessage pre,"
    " .bk-panel-models-chat-ChatMessage code, .bk-panel-models-chat-ChatMessage li"
)


def log(message: str) -> None:
    print(f"  {message}", flush=True)


def wait_for_mw(page, timeout: int = 60) -> None:
    """ResourceLoader finishes after domcontentloaded; mw.loader appears only then."""
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
    name = page.evaluate("() => mw.config.get('wgUserName')")
    if name != user:
        raise RuntimeError(f"logged in as {name!r}, expected {user!r}")
    log(f"logged in as {name}")


def point_at_backend(page, backend: str) -> None:
    """Set the per-user backend preference, so the test drives the build under test.

    The wiki's own `iframe_src` names whichever backend the deployment uses; this one is
    the sandbox-capable build. `$wgChatbotAllowedBackendOrigins` gates which origins the
    preference will accept, so an unlisted one fails here rather than silently loading
    the wrong assistant.
    """
    result = page.evaluate(
        """async (backend) => {
            // mediawiki.api is not loaded on an ordinary page view.
            await mw.loader.using('mediawiki.api');
            const api = new mw.Api();
            await api.postWithToken('csrf', {
                action: 'options',
                change: 'chatbot-custom-backend-iframe-src=' + backend
            });
            return mw.user.options.get('chatbot-custom-backend-iframe-src');
        }""",
        backend,
    )
    log(f"backend preference set (reads back as {result!r})")


def open_chat(page, timeout: int):
    page.wait_for_selector(".floating-chat", timeout=timeout * 1000)
    page.click(".floating-chat")
    frame = page.frame_locator("#chatbot_iframe")
    frame.locator("input.bk-input, textarea.bk-input").first.wait_for(
        state="visible", timeout=timeout * 1000
    )
    # The greeting arrives after the input appears; sending into that gap is swallowed.
    page.wait_for_timeout(5000)
    log("chat opened and the backend answered")
    return frame


def send(page, frame, text: str, timeout: int, attempts: int = 4) -> None:
    """Type a prompt and confirm it actually left the box.

    The ChatInterface reports its input visible and enabled a moment before it is ready
    again, and a send in that window is swallowed - no bubble, no callback, nothing to
    wait for. A new bubble is the only proof it landed, so retry until one appears.
    """
    bubbles = frame.locator(BUBBLES)
    for attempt in range(attempts):
        before = bubbles.count()
        box = frame.locator("input.bk-input, textarea.bk-input").first
        box.wait_for(state="visible", timeout=timeout * 1000)
        box.click()
        box.fill(text)
        box.press("Enter")
        for _ in range(40):
            if bubbles.count() > before:
                log(f"sent (attempt {attempt + 1}): {text[:60]}")
                return
            page.wait_for_timeout(250)
        page.wait_for_timeout(3000)
    raise RuntimeError(f"the chat never accepted the prompt after {attempts} attempts")


def accept_confirmations(page) -> int:
    """Click through any OO.ui.confirm the assistant raises. Returns how many."""
    accepted = 0
    button = page.locator(
        ".oo-ui-messageDialog-actions .oo-ui-flaggedElement-progressive"
    ).first
    for _ in range(3):
        try:
            button.wait_for(state="visible", timeout=4000)
        except Exception:  # noqa: BLE001 - no dialog is the normal case
            break
        button.click()
        accepted += 1
        page.wait_for_timeout(500)
    return accepted


def transcript(frame) -> str:
    return "\n".join(
        t.strip() for t in frame.locator(BUBBLE_TEXT).all_inner_texts() if t.strip()
    )


def settle(page, frame, timeout: int, baseline: int = 0) -> str:
    """Wait for the assistant to answer, then for the answer to stop growing.

    Stability alone is not enough: the transcript already holds the greeting and the
    user's own message, so a turn that has not started yet looks exactly like one that
    has finished. `baseline` is the bubble count before sending, and nothing counts as
    an answer until the count exceeds it - which matters most for a sandbox turn, where
    the first forty seconds are a cold kernel and nothing is on screen.
    """
    deadline = time.monotonic() + timeout
    bubbles = frame.locator(BUBBLES)
    last, stable, answered = "", 0, False

    while time.monotonic() < deadline:
        accept_confirmations(page)
        if not answered:
            answered = bubbles.count() > baseline
            if not answered:
                page.wait_for_timeout(1000)
                continue
        now = transcript(frame)
        if now == last and now:
            stable += 1
            if stable >= 8:
                return now
        else:
            stable = 0
        last = now
        page.wait_for_timeout(1000)

    if not answered:
        log("the assistant never answered within the timeout")
    return last


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--login-user", required=True)
    ap.add_argument("--login-password-file", required=True)
    ap.add_argument("--backend", default="")
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument(
        "--sandbox", action="store_true",
        help="drive a sandbox tool instead of a page edit: the result then travels "
             "tool -> wiki page -> chat iframe -> Bokeh, which the direct bridge tests "
             "never exercise")
    args = ap.parse_args()

    password = open(args.login_password_file).read().strip()
    marker = uuid.uuid4().hex[:8]
    label = f"Chat edit test {marker}"
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            ignore_https_errors=True, viewport={"width": 1500, "height": 1000}
        ).new_page()
        console: list[str] = []
        page.on("console", lambda m: console.append(m.text[:300]))

        login(page, args.wiki, args.login_user, password)
        if args.backend:
            point_at_backend(page, args.backend)

        page.goto(
            f"https://{args.wiki}/wiki/Main_Page",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        frame = open_chat(page, args.timeout)

        if args.sandbox:
            baseline = frame.locator(BUBBLES).count()
            send(
                page,
                frame,
                "Run this in the code sandbox and tell me the output: "
                f"print('{marker}' * 2). Use the sandbox, not your own arithmetic.",
                args.timeout,
            )
            text = settle(page, frame, args.timeout, baseline)
            print("\n--- transcript ---")
            for line in text.splitlines():
                if line.strip():
                    print(f"  {line[:200]}")
            browser.close()

            print("\n--- what the transcript shows ---")
            bokeh = [c for c in console if "not serializable" in c]
            log(f"bokeh serialisation errors: {len(bokeh)}")
            if bokeh:
                failures.append(
                    "a tool result reached Bokeh with an undefined in it - the reply "
                    f"never arrives and the model waits out its timeout: {bokeh[0][:120]}"
                )
            if marker * 2 not in text.replace(" ", ""):
                failures.append(
                    f"the sandbox output {marker * 2!r} never came back into the chat"
                )
            if failures:
                print("\nFAIL:", file=sys.stderr)
                for failure in failures:
                    print(f"  {failure}", file=sys.stderr)
                return 1
            print("\nPASS: a sandbox tool ran and its output reached the chat.")
            return 0

        send(
            page,
            frame,
            f"Create a new wiki page: an Item that is an instance of "
            f"Category:OSW494f660e6a714a1a9681c517bbb975da, with the label "
            f"'{label}'. Do it now - do not ask me to confirm the details first.",
            args.timeout,
        )
        text = settle(page, frame, args.timeout)

        print("\n--- transcript ---")
        for line in text.splitlines():
            if line.strip():
                print(f"  {line[:200]}")

        browser.close()

    print("\n--- what the transcript shows ---")
    gated = "osl-page-editing" in text
    unreachable = "browser could not be reached" in text
    log(f"the skill gate was mentioned      : {gated}")
    log(f"'browser could not be reached'    : {unreachable}")

    if unreachable:
        failures.append(
            "a refusal reached the model dressed as an unreachable browser - the gate's "
            "own instruction is contradicted by the advice appended to it"
        )

    # The page is the point. Anything the model says about having written it is a claim.
    #
    # Checked through usercontribs, not list=search: the search index is asynchronous and
    # had not caught up seconds after the edit, which reads as "nothing was written" when
    # the page is already there. Contributions are written in the same transaction as the
    # revision.
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(ignore_https_errors=True).new_page()
        login(page, args.wiki, args.login_user, password)
        written = page.evaluate(
            """async (user) => {
                await mw.loader.using('mediawiki.api');
                const api = new mw.Api();
                const r = await api.get({ action: 'query', list: 'usercontribs',
                                          ucuser: user, uclimit: 5, ucprop: 'title|timestamp' });
                const titles = (r.query.usercontribs || []).map(c => c.title);
                const out = [];
                for (const title of titles) {
                    const raw = await api.get({ action: 'query', prop: 'revisions',
                                                titles, rvprop: 'content', rvslots: 'jsondata' });
                    out.push(title);
                }
                return out;
            }""",
            args.login_user,
        )
        log(f"most recent edits by {args.login_user}: {written}")

        found = []
        for title in written:
            data = page.evaluate(
                """async (title) => {
                    const r = await fetch(mw.config.get('wgScriptPath') +
                        '/index.php?title=' + encodeURIComponent(title) +
                        '&action=raw&slot=jsondata');
                    return r.ok ? await r.text() : '';
                }""",
                title,
            )
            if marker in data:
                found.append(title)
        log(f"pages whose label carries {marker}: {found}")
        if not found:
            failures.append(
                f"no page carrying {marker!r} among the latest edits - whatever the "
                "assistant said, nothing was written"
            )
        browser.close()

    if failures:
        print("\nFAIL:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("\nPASS: the assistant wrote the page, and no refusal was mislabelled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
