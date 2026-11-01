"""Does the sandbox answer the wiki, and can it actually build a notebook?

The chat assistant drives the sandbox over postMessage, through the bridge plugin. This
exercises that contract directly - request in, reply out - and then the part most likely
to be wrong: the notebook commands, whose argument shapes come from reading
jupyterlab-ai-commands rather than from a published contract.

Messages are posted from the lab page to itself. The bridge accepts same-origin messages
and replies to `event.source`, so a self-post is answered the same way a post from the
wiki window would be, without needing a second document.

    python tests/bridge.py --wiki wiki-dev.open-semantic-lab.org
"""

from __future__ import annotations

import argparse
import json
import sys

from playwright.sync_api import sync_playwright

# Resolves when the reply for this id arrives, so a slow notebook build is waited for
# rather than guessed at.
ASK = """
({ action, payload, timeoutMs }) => new Promise(resolve => {
  const id = 'test-' + Math.random().toString(36).slice(2);
  const timer = setTimeout(() => {
    window.removeEventListener('message', onMessage);
    resolve({ error: 'timed out waiting for the bridge' });
  }, timeoutMs);
  function onMessage(event) {
    const d = event.data;
    if (!d || d.type !== 'osw-sandbox-reply' || d.id !== id) return;
    clearTimeout(timer);
    window.removeEventListener('message', onMessage);
    resolve(d);
  }
  window.addEventListener('message', onMessage);
  window.postMessage(
    Object.assign({ type: 'osw-sandbox-request', id, action }, payload),
    window.location.origin
  );
})
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--base", default="/w/extensions/JupyterLite/dist/")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    lab = f"https://{args.wiki}{args.base}lab/index.html"
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(ignore_https_errors=True).new_page()
        page.on("console", lambda m: print(f"  [console] {m.text}"[:180]))

        print(f"opening {lab}")
        page.goto(lab, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector(".jp-LabShell", timeout=args.timeout * 1000)
        page.wait_for_timeout(5000)

        print("asking the bridge for status ...")
        status = page.evaluate(ASK, {"action": "status", "payload": {}, "timeoutMs": 30_000})
        print(f"  {json.dumps(status)[:200]}")
        if not status.get("result", {}).get("ready"):
            failures.append(f"status did not report ready: {status}")

        print("asking it to build a notebook ...")
        built = page.evaluate(
            ASK,
            {
                "action": "create_notebook",
                "payload": {
                    "name": "bridge-test",
                    "cells": [
                        {"type": "markdown", "source": "# Bridge test"},
                        {"type": "code", "source": "print('hello from the bridge')"},
                    ],
                },
                "timeoutMs": args.timeout * 1000,
            },
        )
        print(f"  {json.dumps(built)[:300]}")
        if built.get("error"):
            failures.append(f"create_notebook failed: {built['error']}")
        elif not built.get("result", {}).get("notebookPath"):
            failures.append(f"create_notebook returned no path: {built}")
        else:
            # Attached, not visible: the panel is real but a headless window never
            # gives it focus, so a visibility check would fail on a notebook that is
            # perfectly well open. What matters is that a command reporting success
            # actually left a notebook behind.
            page.wait_for_selector(".jp-Notebook", state="attached", timeout=60_000)
            cells = page.eval_on_selector_all(
                ".jp-Notebook .jp-Cell", "nodes => nodes.length"
            )
            print(f"  notebook open with {cells} cell(s)")
            if cells < 2:
                failures.append(f"expected 2 cells in the notebook, found {cells}")

        browser.close()

    if failures:
        print("\nFAIL:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("\nPASS: the bridge answers and builds a notebook.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
