# Build environment for the OSW JupyterLite site.
#
# The host has Python 3.10 and no node, but oold_jupyterlite_ai.config uses tomllib
# (3.11+) and the labextension build shells out to npm. Both live here instead.
#
# Node is only needed when [extension] is set in oold-lite.toml; a measurement build
# without it never invokes npm.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git ca-certificates curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Installed from git: neither oold-jupyterlite-ai nor oold-azure-providers is on PyPI.
# Pinned to a commit rather than main so a rebuild is reproducible - bump deliberately.
ARG OOLD_LITE_REF=main
RUN pip install --no-cache-dir \
    "oold-jupyterlite-ai[build] @ git+https://github.com/OO-LD/oold-jupyterlite-ai@${OOLD_LITE_REF}"

# jupyterlab-open-url-parameter is what implements ?fromURL=, not JupyterLite core.
# It restores the URL contract the retired extensions/JupyterLite/dist/ site used.
RUN pip install --no-cache-dir jupyterlab-open-url-parameter

WORKDIR /flavour
