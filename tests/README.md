# Tests

These drive a **deployed** site with a real browser. There is no unit-test layer here on
purpose: every question they answer - does the kernel inherit the wiki session, does
`import osw` work under Pyodide, how long does a cold start take - is only answerable in
a browser against a live wiki.

Run them from a Playwright image pinned to the browsers it ships:

Build the image once - `tests/Dockerfile` pins both halves, because the python package
and the browsers in the base image have to match or you get a confusing "Executable
doesn't exist":

```bash
docker build -t osw-pw:dev tests/
```

Then:

```bash
docker run --rm --network host -v "$PWD":/w -w /w osw-pw:dev \
    python tests/<name>.py --wiki <host>
```

| | |
| --- | --- |
| `same_origin_session.py` | the kernel's requests carry the wiki origin's cookies. Needs `fixtures/probe.php` deployed. |
| `osw_in_kernel.py` | osw-python imports and builds a `WtSite`; reports the cold start. |
| `cookie_control.py` | a control, not a test: shows that Playwright cannot observe Cookie headers. |
| `quiet_start.py` | the starter cells produce no stderr, so a working cell does not look like a failed one. |
| `live_chat.py` | the agent's resolved configuration, and a real call through the real proxy to the real model. |
| `run_cell.py` | a debugging tool: run one cell in the deployed sandbox and print the output. |

## fixtures/probe.php

`same_origin_session.py` asks the *server* what it received, because the browser side
cannot be observed: `request.all_headers()` reports no Cookie header even for a page
`fetch()` that demonstrably sends one. `cookie_control.py` exists to demonstrate that, so
the next person does not spend an afternoon rediscovering it.

Deploy it only while testing and remove it afterwards:

```bash
cp tests/fixtures/probe.php <wiki>/mediawiki/extensions/JupyterLite/probe.php
# ... run the test ...
rm <wiki>/mediawiki/extensions/JupyterLite/probe.php
```

It reports cookie *names* only, never values, but it is still an undocumented endpoint on
the wiki's origin and does not belong in a deployment.

## live_chat.py, and why it needs a token

This is the test that would have caught the three bugs a settings test missed:
`useSecretsManager` swallowing the token, a `baseURL` without the `/v1` the AI SDK
depends on, and a CORS allow-list that rejected headers the SDK sends.

It needs a **signed** wiki token, and the reason is worth understanding rather than
working around. The proxy authenticates before it forwards, so an unsigned token is
rejected with 401 whatever the url is - a 401 therefore proves nothing about the path. A
wrong `baseURL` only shows up as a 404 *from the vendor*, which means the request has to
get that far.

Mint one inside the proxy container, where the shared secret already lives, and hand the
test the file. The token is short-lived and scoped; the secret never leaves the container
and the token never needs to be read by a human:

```bash
docker cp tests/fixtures/mint_token.py osw-llm-proxy:/tmp/mint.py
docker exec osw-llm-proxy sh -c 'cd /app && uv run --no-sync python /tmp/mint.py /tmp/t'
docker cp osw-llm-proxy:/tmp/t /tmp/livetoken

docker run --rm --network host -v "$PWD":/w -v /tmp/livetoken:/tmp/livetoken:ro -w /w \
    osw-pw:dev python tests/live_chat.py \
        --wiki wiki-dev.open-semantic-lab.org \
        --proxy https://llm.osw-chatbot.demo.open-semantic-lab.org/v1 \
        --token-file /tmp/livetoken

rm -f /tmp/livetoken
```

### A run with nothing stubbed

With `--login-user` the test logs into the wiki first, and then *nothing* is stubbed: the
wiki issues its own agent configuration and its own token, and the token is fetched by
the page rather than handed in, exactly as the extension does it.

```bash
docker run --rm --network host -v "$PWD":/w -v /tmp/sbpw:/tmp/sbpw:ro -w /w \
    osw-pw:dev sh -c "python tests/live_chat.py \
        --wiki wiki-dev.open-semantic-lab.org \
        --proxy https://llm.osw-chatbot.demo.open-semantic-lab.org/v1 \
        --login-user SandboxTestBot --login-password-file /tmp/sbpw"
```

`SandboxTestBot` is a plain account on the dev wiki, created for this. It holds no
special rights - it only needs to be *a* logged-in user, since that is the condition the
stubs were standing in for.

Without `--token-file` or `--login-user` it still runs and still checks the settings and
CORS, but it says so in its own output rather than claiming more than it verified.

It has been checked against a deliberately broken url - dropping the `/v1` makes it fail
with exactly the diagnosis above - because a regression test nobody has seen fail is a
regression test nobody should trust.
