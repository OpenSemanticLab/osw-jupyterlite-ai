"""Install the OSW stack into the browser kernel, in an order wheel metadata cannot express.

Two things make a plain ``micropip.install("osw")`` unreliable here, and both are
properties of micropip rather than of our wheels:

* **Pinned transitive dependencies.** ``osw`` requires
  ``datamodel-code-generator==0.51.0``. Installed on its own that resolves instantly from
  the site's own index; reached as a dependency of ``osw`` in the same transaction,
  micropip reports "Can't find a pure Python 3 wheel". Installing the pin first and
  ``osw`` second works every time. Verified against the deployed site, not inferred.
* **Extras are not resolved.** ``osw`` asks for ``pydantic[email]``; micropip ignores the
  ``[email]`` part, so ``email-validator`` has to be named here or it is simply absent
  until something imports it and fails.

Every wheel named here is vendored into the site, so this reaches no index on the
network. If you add one, add it to ``scripts/resolve_wheels.py`` too - otherwise it works
in development, where PyPI is reachable, and fails on a cold start behind a firewall.

Usage in a notebook::

    import osw_bootstrap
    await osw_bootstrap.setup()
"""

from __future__ import annotations

#: Installed one at a time, in this order. Not a set, not sorted - the order is the point.
STEPS: tuple[str, ...] = (
    # Installed rather than imported, even though the Pyodide distribution carries it.
    # The kernel auto-loads a distribution package only when the literal import appears
    # in the *cell* it is about to run; an import inside this module is invisible to
    # that, so `import pyodide_http` here raises ModuleNotFoundError unless it has been
    # installed first.
    "pyodide-http",
    # Must precede osw; see the module docstring.
    "datamodel-code-generator==0.51.0",
    # Same problem, reached through opensemantic.characteristics.quantitative. Only
    # 0.7.1 is vendored, because later releases want pandas>=3.0 and this Pyodide ships
    # 2.3.3 - but micropip resolves the unpinned transitive requirement to the newest
    # release it can see and then fails on pandas. Naming the version here settles it
    # before anything asks for it.
    "pint-pandas==0.7.1",
    # The [email] extra of pydantic, which micropip drops.
    "email-validator",
    # Pulls oold, opensemantic, opensemantic.core and opensemantic.base with it.
    "osw",
    # Nothing requires it, but it is the lab/ELN model layer an OSW notebook wants.
    "opensemantic-lab",
)


async def setup(verbose: bool = True) -> None:
    """Install the stack and route `requests` through the browser.

    Safe to call more than once: micropip skips what is already present, and
    ``patch_all`` is idempotent.
    """
    import micropip

    for step in STEPS:
        if verbose:
            print(f"installing {step} ...")
        await micropip.install(step, keep_going=True)

    # After the installs, not before: patching is cheap but doing it here keeps the whole
    # "make the kernel able to talk to the wiki" story in one call. Pyodide has no
    # sockets, so without this `requests` cannot reach anything at all.
    import pyodide_http

    pyodide_http.patch_all()

    if verbose:
        print("ready - osw-python can now reach this wiki as the logged-in user")


def wtsite(domain: str | None = None):
    """A :class:`WtSite` bound to the wiki this notebook was served from.

    Takes the credential path out of the picture rather than filling it in: the
    ``mwclient.Site`` is built here and handed to ``WtSiteLegacyConfig``, which skips
    osw's credential handling entirely. The browser already holds the session cookie for
    this origin, so the requests are authenticated without anything being asked for or
    stored.

    Everything done through the returned object happens **as the logged-in user** and
    cannot exceed their rights.
    """
    import js
    import mwclient
    from osw.wtsite import WtSite

    domain = domain or js.location.hostname
    site = mwclient.Site(domain, path="/w/", scheme="https", do_init=False)
    return WtSite(WtSite.WtSiteLegacyConfig(site=site))
