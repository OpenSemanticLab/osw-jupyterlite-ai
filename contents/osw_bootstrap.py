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



def _quieten() -> None:
    """Keep library chatter out of the notebook.

    Jupyter paints anything on stderr red, so a cell that merely *worked* looks like it
    failed. Everything silenced here is noise a sandbox user can do nothing about:

    * osw's own INFO banner, which explains how to turn itself off;
    * the ``osw[wikitext]`` hint - mwparserfromhell ships no pure-python wheel and is
      absent from this Pyodide, so the extra genuinely cannot be installed here;
    * pydantic V1 deprecation warnings raised while oold and opensemantic define their
      models, which are about those libraries' internals, not the user's code.

    Deliberately narrow: filters are scoped to those modules and that category, so a
    DeprecationWarning from the user's own code still shows. Real errors are untouched.
    """
    import logging
    import warnings

    logging.getLogger("osw").setLevel(logging.WARNING)
    logging.getLogger("osw.wiki_tools").setLevel(logging.ERROR)
    # The Pyodide kernel's own warning, raised whenever a cell uses top-level await -
    # which the setup cell does. It fires while the kernel decides how to run the cell,
    # before any of its code executes, so a filter set inside that cell is too late for
    # it. Installing this at *import* time is what lets the notebook's first cell be a
    # plain `import osw_bootstrap` and the awaiting cell come after it, quiet.
    warnings.filterwarnings(
        "ignore", message=".*should_run_async.*", category=DeprecationWarning
    )
    for module in (r"oold\..*", r"opensemantic.*", r"osw\..*"):
        warnings.filterwarnings("ignore", category=DeprecationWarning, module=module)


async def setup(verbose: bool = True) -> None:
    """Install the stack and route `requests` through the browser.

    Safe to call more than once: already-present packages are skipped, and ``patch_all``
    is idempotent.

    Uses ``piplite``, not ``micropip``, and the difference is not cosmetic. piplite knows
    the site's own wheel index; micropip does not, and resolves against pypi.org instead.
    That matters here because this site ships a *patched* osw carrying the same version
    number as the released one - so with micropip the released copy wins, dragging back
    the dask dependency the patch removes and making a cold start depend on the network.
    The site also sets ``disablePyPIFallback``, so this now fails loudly rather than
    quietly fetching the wrong thing.
    """
    import piplite

    for step in STEPS:
        if verbose:
            print(f"installing {step} ...")
        await piplite.install(step, keep_going=True)

    # After the installs, not before: patching is cheap but doing it here keeps the whole
    # "make the kernel able to talk to the wiki" story in one call. Pyodide has no
    # sockets, so without this `requests` cannot reach anything at all.
    import pyodide_http

    pyodide_http.patch_all()

    # Imported here, under the filters, rather than left to the user's next cell. The
    # model registration this triggers prints a page of library chatter exactly once;
    # doing it inside the step the user already knows is slow keeps it out of the cell
    # where they are trying to read their own output.
    import contextlib
    import io

    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        import osw  # noqa: F401
        import osw.core  # noqa: F401
        import osw.wtsite  # noqa: F401

    # Again, because importing osw configures logging and resets the warning filters -
    # its own banner says as much ("Configuring logging yourself ... takes the output
    # over automatically"). Without this the filters installed at import time are gone by
    # the time the *next* cell runs, and the kernel's await warning reappears there,
    # under whichever cell the user happens to run next.
    _quieten()

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

    Note the absence of ``do_init=False``, which osw's own ``WtSite`` passes. osw gets
    away with it because it calls ``site.login()`` straight afterwards and mwclient's
    ``login()`` runs ``site_init()`` as a side effect. There is no login on this path, so
    skipping the init would leave ``site.rights`` empty - and ``Page.can('read')`` tests
    membership of exactly that list, so **every** ``page.text()`` would raise
    ``InsufficientPermission``. osw's retry loop then swallows it five times and returns
    an empty page list, which surfaces as an ``IndexError`` nowhere near the cause.
    """
    import js
    import mwclient
    from mwclient.errors import APIError
    from osw.wtsite import WtSite

    domain = domain or js.location.hostname
    try:
        site = mwclient.Site(domain, path="/w/", scheme="https")
    except APIError as error:
        if error.code in ("readapidenied", "mustbeloggedin"):
            # Worth catching rather than letting mwclient's error through: on a wiki that
            # requires login this is the single most likely first failure, and "you need
            # read permission" does not suggest the fix.
            raise PermissionError(
                f"{domain} does not allow anonymous access. Log in to the wiki in this "
                "browser - the sandbox uses that session - and re-run this cell."
            ) from error
        raise

    # WtSiteLegacyConfig warns that WtSiteConfig is preferred, but WtSiteConfig is the
    # credential path - it takes an iri and a CredentialManager and would prompt. Passing
    # an already-built mwclient.Site is only possible through the legacy config, so the
    # warning is expected here and would only confuse someone reading their cell output.
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return WtSite(WtSite.WtSiteLegacyConfig(site=site))


# Run on import, not on setup(): see the should_run_async note in _quieten(). It also
# means a user who only imports this module gets the quiet behaviour without having to
# know it exists.
_quieten()
