# The image the browser suites in this directory run in.
#
# Written down because it was not: the image was built by hand on one machine, so the
# only record of how to run these tests was a shell history. Anyone cloning the repo got
# "ModuleNotFoundError: playwright" and no way to tell which version was meant.
#
# The base ships the browsers; it does not ship the python package, so that is installed
# here - at the matching version, because playwright refuses to drive browsers built for
# a different release.
#
#   docker build -t osw-pw:dev tests/
#   docker run --rm --network host -v "$PWD":/w -v /tmp/sbpw:/tmp/sbpw:ro -w /w osw-pw:dev \
#       python tests/notebook_persistence.py --wiki wiki-dev.open-semantic-lab.org \
#           --login-user SandboxTestBot --login-password-file /tmp/sbpw
#
# These suites drive a *deployed* wiki and sandbox, so they are not part of CI: they need
# a reachable wiki, an account and a password file. `extension/tests/` holds the checks
# that run without any of that.
#
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

# Must match the browsers in the base image above. Bump both together.
RUN pip install --no-cache-dir playwright==1.49.0

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
