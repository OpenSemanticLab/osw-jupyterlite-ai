"""Check the two things osw-jupyternaut is responsible for, in a real browser.

1. `?fromURL=` is restricted to this wiki's own uploaded files. A security control, not a
   nicety: the kernel holds the reader's wiki session, so a notebook someone can be lured
   into opening can write to the wiki as them.
2. The wiki's `$wgJupyterLiteSettings` becomes the agent's default provider, so a
   deployment can retarget the model without rebuilding the static site.

The second is checked against a stubbed `action=jupyterliteconfig` response. That is
deliberate: this test is about the plugin's behaviour, and on a wiki that denies anonymous
API reads - osw-dev does - an unauthenticated run could only ever observe the plugin
giving up. The wiki-side gating is verified separately, by the unstubbed run reporting
`readapidenied`.

    python tests/labextension.py --wiki wiki-dev.open-semantic-lab.org
"""

from __future__ import annotations

import argparse
import json
import sys

from playwright.sync_api import sync_playwright

EVIL = "https://example.invalid/evil.ipynb"

STUB = {
    "jupyterliteconfig": {
        "basePath": "/w/extensions/JupyterLite/dist/",
        "settings": {
            "provider": "generic",
            "model": "stub-model",
            "name": "OSW (stub)",
            "baseURL": "https://proxy.invalid/llm/v1",
        },
    }
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--base", default="/w/extensions/JupyterLite/dist/")
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    lab = f"https://{args.wiki}{args.base}lab/index.html"
    messages: list[str] = []
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)

        # fetch is patched in the page rather than intercepted with context.route:
        # JupyterLite registers a service worker, and requests that go through it are not
        # reliably visible to Playwright's router. Patching in an init script runs before
        # any extension activates and needs no cooperation from the network layer.
        context.add_init_script(
            f"""
            (() => {{
              const stub = {json.dumps(STUB)};
              const real = window.fetch;
              window.fetch = async (input, init) => {{
                const href = typeof input === 'string' ? input : (input.url ?? String(input));
                if (href.includes('action=jupyterliteconfig')) {{
                  return new Response(JSON.stringify(stub), {{
                    status: 200,
                    headers: {{ 'Content-Type': 'application/json' }}
                  }});
                }}
                return real(input, init);
              }};
            }})();
            """
        )

        page = context.new_page()
        page.on("console", lambda m: messages.append(m.text))

        # One allowed url and one that is not. Testing only the rejection would not catch
        # a guard that threw everything away.
        good = f"https://{args.wiki}/w/img_auth.php/0/00/example.ipynb"
        print("opening with one allowed and one disallowed fromURL")
        page.goto(
            f"{lab}?fromURL={good}&fromURL={EVIL}",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        page.wait_for_selector(".jp-LabShell, .jp-Notebook", timeout=args.timeout * 1000)
        page.wait_for_timeout(6000)
        browser.close()

    ours = [m for m in messages if "osw-jupyternaut" in m]
    print("\n--- osw-jupyternaut console ---")
    for message in ours:
        print(f"  {message.splitlines()[0][:150]}")

    # Asserted from the log rather than from the query string: jupyterlab-open-url-parameter
    # consumes the parameter once it has handled it, so an empty `fromURL` afterwards says
    # nothing about which values survived the guard.
    refused = [m for m in ours if "refused to load" in m]
    if not any(EVIL in m for m in refused):
        failures.append("the off-origin fromURL was not refused - the guard did not run")
    if any(good in m for m in refused):
        failures.append("the allowed img_auth.php url was refused - the guard is too strict")

    if not any("applied the wiki's agent defaults" in m for m in ours):
        failures.append(
            "the stubbed wiki agent defaults were not applied; "
            f"saw: {[m.splitlines()[0][:90] for m in ours]}"
        )

    if failures:
        print("\nFAIL:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("\nPASS: fromURL is restricted to wiki files and the wiki's defaults are applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
