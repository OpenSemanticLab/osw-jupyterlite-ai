# osw-jupyternaut

Wires the in-browser Jupyternaut agent to the OpenSemanticLab wiki the sandbox is served
from.

| plugin | what it does |
| --- | --- |
| `osw-jupyternaut:from-url-guard` | restricts `?fromURL=` to this wiki's `img_auth.php` files |
| `osw-jupyternaut:wiki-config` | applies the wiki's default provider/model from `action=jupyterliteconfig` |

It registers no AI provider of its own: Jupyternaut already ships a
**Generic (OpenAI-compatible)** provider that takes a `baseURL`, which is all a
wiki-hosted LLM proxy needs.

## Build

Driven by the flavour build (`scripts/build.sh`), which runs `npm install && npm run
build` here and then `pip install --no-deps .`. There is no pip build hook on purpose -
see the comment in `pyproject.toml`.

## `@jupyternaut/agent` must stay a shared singleton

`package.json` declares it `{"bundled": false, "singleton": true}`. Bundling a second
copy would give this extension different token objects from the ones Jupyternaut
registers against, and every registration would silently do nothing.
