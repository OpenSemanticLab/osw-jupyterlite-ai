# Tests

These drive a **deployed** site with a real browser. There is no unit-test layer here on
purpose: every question they answer - does the kernel inherit the wiki session, does
`import osw` work under Pyodide, how long does a cold start take - is only answerable in
a browser against a live wiki.

Run them from a Playwright image pinned to the browsers it ships:

```bash
docker run --rm --network host -v "$PWD":/w -w /w \
    mcr.microsoft.com/playwright/python:v1.49.0-jammy \
    sh -c "pip install -q 'playwright==1.49.0'; python tests/<name>.py --wiki <host>"
```

The `playwright==1.49.0` pin matters: installing the latest pulls a version expecting
browser builds the image does not have, and it fails with a confusing "Executable doesn't
exist".

| | |
| --- | --- |
| `same_origin_session.py` | the kernel's requests carry the wiki origin's cookies. Needs `fixtures/probe.php` deployed. |
| `osw_in_kernel.py` | osw-python imports and builds a `WtSite`; reports the cold start. |
| `cookie_control.py` | a control, not a test: shows that Playwright cannot observe Cookie headers. |
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
