"""Real browser, real proxy: check the two things that actually broke.

`labextension.py` stubs the wiki API and asserts that settings get *written*. They did,
every time, while the chat stayed broken for three separate reasons - all of them past
the point a settings test looks:

1. `useSecretsManager` was left on, so Jupyternaut read the apiKey from the secrets
   manager and ignored the token in settings. The provider appeared in the picker and the
   agent refused to start.
2. `baseURL` lacked the `/v1` the AI SDK depends on: it appends only `/messages`, and
   Anthropic lives at `<base>/v1/messages`.
3. The proxy's CORS allow-list missed headers the SDK sends (`user-agent`,
   `x-stainless-*`), so the preflight 400'd and the browser reported an opaque CORS error
   with nothing in the proxy's log.

So this asserts the *resolved* settings - read back out of JupyterLab's own settings
manager, not from a log line - and then makes the request the SDK would make, from the
page, to the real proxy.

The token is deliberately unsigned, so the proxy must answer 401. That is the whole
assertion: a 401 means the agent's configuration resolved, the url was right, and the
preflight passed. Anything else is one of the three bugs above:

    CORS / network error  -> preflight or origin wrong
    404                   -> baseURL path wrong
    no call at all        -> settings never resolved

The seam this does not cross is a real browser obtaining a real wiki token, which needs a
login there is no test account for. The hop past it is covered by the proxy's own
end-to-end test, which signs a token inside the container and gets a real completion.

    python tests/live_chat.py --wiki wiki-dev.open-semantic-lab.org \\
        --proxy https://llm.osw-chatbot.demo.open-semantic-lab.org/v1
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time

from playwright.sync_api import sync_playwright

DESCRIBE_COMMAND = "osw-jupyternaut:describe"
PROVIDER_ID = "osw"


def fake_token() -> str:
    """Well-formed and deliberately unsigned, so the proxy rejects the signature.

    Garbage would also be rejected, but for the wrong reason - and would pass this test
    while hiding a real problem.
    """
    payload = {
        "u": "live-chat-test",
        "w": "mediawiki",
        "iat": time.time(),
        "exp": time.time() + 600,
        "n": "live",
        "a": ["code-sandbox"],
        "ca": [],
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{encoded}.not-a-real-signature"


# Exactly what @ai-sdk/anthropic sends, including the headers that broke the preflight.
CALL_PROXY = """
async ({ baseURL, model, token }) => {
  try {
    const res = await fetch(baseURL.replace(/\\/$/, '') + '/messages', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-api-key': token,
        'anthropic-version': '2023-06-01',
        'anthropic-dangerous-direct-browser-access': 'true',
        'x-stainless-retry-count': '0'
      },
      body: JSON.stringify({
        model, max_tokens: 16,
        messages: [{ role: 'user', content: 'ping' }]
      })
    });
    return { status: res.status, body: (await res.text()).slice(0, 300) };
  } catch (e) {
    return { status: 0, body: String(e) };
  }
}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--proxy", required=True, help="baseURL exactly as the wiki serves it")
    ap.add_argument("--base", default="/w/extensions/JupyterLite/dist/")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument(
        "--token-file",
        help="file holding a wiki-signed token. With it the call must return a real "
        "completion, which is the only way a wrong baseURL path shows up - an unsigned "
        "token is rejected before the proxy ever forwards, so a 401 proves nothing "
        "about the url.",
    )
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    signed = None
    if args.token_file:
        signed = open(args.token_file).read().strip()

    stub = {
        "jupyterliteconfig": {
            "basePath": args.base,
            "settings": {
                "provider": "anthropic",
                "model": args.model,
                "name": "OSW (live test)",
                "baseURL": args.proxy,
            },
        }
    }
    token = signed or fake_token()
    token_stub = {"chatbottoken": {"token": token, "expires": time.time() + 600}}

    console: list[str] = []
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            ignore_https_errors=True, viewport={"width": 1600, "height": 1000}
        )
        # Patched in the page rather than intercepted: JupyterLite registers a service
        # worker, which makes context.route unreliable, and this has to be in place
        # before any extension activates.
        context.add_init_script(
            f"""
            (() => {{
              const config = {json.dumps(stub)};
              const token = {json.dumps(token_stub)};
              const json = (b) => new Response(JSON.stringify(b), {{
                status: 200, headers: {{ 'Content-Type': 'application/json' }} }});
              const real = window.fetch;
              window.fetch = async (input, init) => {{
                const href = typeof input === 'string' ? input : (input.url ?? String(input));
                if (href.includes('action=jupyterliteconfig')) return json(config);
                if (href.includes('action=chatbottoken')) return json(token);
                return real(input, init);
              }};
            }})();
            """
        )
        page = context.new_page()
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))

        url = f"https://{args.wiki}{args.base}lab/index.html"
        print(f"opening {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector(".jp-LabShell", timeout=args.timeout * 1000)
        page.wait_for_timeout(6000)

        # The resolved settings, read back from JupyterLab's own settings manager. A log
        # line only says what the code believed it did; this is what Jupyternaut will act
        # on when it builds the agent.
        # Through the extension's own describe command. serviceManager.settings.fetch
        # reads a connector that does not see JupyterLite's IndexedDB-backed user
        # settings - it returns an empty composite - so the only way to observe the
        # *resolved* configuration is from inside the application.
        resolved = page.evaluate(
            "(cmd) => (window.jupyterapp ?? window.jupyterlab).commands.execute(cmd)",
            DESCRIBE_COMMAND,
        )

        print("\n--- resolved agent settings ---")
        print(f"  useSecretsManager: {resolved.get('useSecretsManager')}")
        print(f"  defaultProvider:   {resolved.get('defaultProvider')}")
        entry = resolved.get("provider")
        print(f"  provider entry:    {json.dumps(entry)[:200] if entry else '(missing)'}")

        if resolved.get("useSecretsManager") is not False:
            failures.append(
                "useSecretsManager is not false - Jupyternaut will read the apiKey from "
                "the secrets manager and ignore the wiki token, and the agent will refuse "
                "to start while still listing the provider"
            )
        if entry is None:
            failures.append("the wiki's provider entry was never written into settings")
        else:
            if entry.get("baseURL") != args.proxy:
                failures.append(
                    f"baseURL is {entry.get('baseURL')!r}, expected {args.proxy!r}"
                )
            if entry.get("apiKey") != "present":
                failures.append("the provider has no apiKey - the wiki token was not stored")
        if resolved.get("defaultProvider") != PROVIDER_ID:
            failures.append(
                f"defaultProvider is {resolved.get('defaultProvider')!r}, not {PROVIDER_ID!r}"
            )

        print("\ncalling the proxy the way the AI SDK does ...")
        result = page.evaluate(
            CALL_PROXY,
            {
                "baseURL": (entry or {}).get("baseURL") or args.proxy,
                "model": args.model,
                "token": token,
            },
        )
        browser.close()

    print(f"  -> status {result['status']}: {result['body'][:180]}")

    if result["status"] == 0:
        failures.append(
            "the request never completed - a CORS preflight rejection or a blocked "
            f"origin: {result['body'][:200]}"
        )
    elif signed:
        # With a real token the request goes all the way to the model, so the url has to
        # be right end to end. This is the assertion an unsigned token cannot make.
        if result["status"] == 404:
            failures.append(
                "404 from the vendor - the baseURL path is wrong. The SDK appends only "
                "'/messages', so the base must end in '/v1'."
            )
        elif result["status"] != 200:
            failures.append(
                f"expected a completion, got {result['status']}: {result['body'][:220]}"
            )
        elif '"content"' not in result["body"]:
            failures.append(f"200 but no completion content: {result['body'][:220]}")
    elif result["status"] == 404:
        failures.append("404 - the baseURL path is wrong")
    elif result["status"] not in (401, 403):
        failures.append(
            f"expected 401/403 for an unsigned token, got {result['status']}: "
            f"{result['body'][:200]}"
        )

    noisy = [m for m in console if "CORS" in m or "Cross-Origin" in m or "NetworkError" in m]
    if noisy:
        failures.append(f"the browser reported a CORS/network error: {noisy[0][:200]}")

    if failures:
        print("\nFAIL:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    if signed:
        print(
            "\nPASS: settings resolved as the agent needs them, and the SDK-shaped "
            "request reached the model through the proxy and came back with a completion."
        )
    else:
        print(
            "\nPASS: settings resolved and the request reached the proxy over CORS. "
            "Without --token-file the url is only checked as far as the proxy's own "
            "auth, which rejects before forwarding - pass a signed token to check it "
            "end to end."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
