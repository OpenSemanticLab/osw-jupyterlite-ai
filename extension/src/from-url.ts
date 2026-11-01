/**
 * Keep `?fromURL=` pointed at this wiki.
 *
 * `jupyterlab-open-url-parameter` fetches whatever url it is given and drops the result
 * on the contents drive. In a sandbox whose kernel holds the reader's live wiki session,
 * an unrestricted fetch sink on our own origin is a one-click write primitive: send
 * someone a link, their browser opens a notebook you wrote, and anything it does is done
 * as them.
 *
 * So a url is accepted only when it is same-origin **and** under `img_auth.php`, which is
 * how uploaded files are served here ($wgUploadPath). Anything else is stripped from the
 * query string before the open-url extension ever sees it.
 *
 * The MediaWiki launcher applies the same rule when building a link. That check is a
 * courtesy; this one is the control, because a crafted link never passes through the
 * launcher. Keep the two in step.
 *
 * Stripping rather than refusing: an empty sandbox is a better outcome than a blank page,
 * and the console says what was dropped.
 */

import { scriptPath } from './wiki';

export const PARAM = 'fromURL';

export function isAllowed(candidate: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(candidate, window.location.href);
  } catch {
    return false;
  }
  if (parsed.origin !== window.location.origin) {
    return false;
  }
  return parsed.pathname.startsWith(`${scriptPath()}/img_auth.php/`);
}

/**
 * Remove disallowed `fromURL` values from the current url.
 *
 * Must run before jupyterlab-open-url-parameter reads them, which is why the plugin that
 * calls this requires nothing and is activated as early as the application allows.
 * `history.replaceState` rather than a reload: a reload would re-enter this with the same
 * query string if the rewrite ever failed, and a redirect loop in the sandbox launcher is
 * far worse than one dropped file.
 */
export function sanitizeLocation(): string[] {
  const url = new URL(window.location.href);
  const values = url.searchParams.getAll(PARAM);
  if (values.length === 0) {
    return [];
  }

  const allowed = values.filter(isAllowed);
  const rejected = values.filter(value => !isAllowed(value));
  if (rejected.length === 0) {
    return [];
  }

  url.searchParams.delete(PARAM);
  for (const value of allowed) {
    url.searchParams.append(PARAM, value);
  }
  window.history.replaceState(null, '', url.toString());

  for (const value of rejected) {
    console.warn(
      `osw-jupyternaut: refused to load ${value} - only this wiki's img_auth.php files ` +
        'may be opened in the sandbox'
    );
  }
  return rejected;
}
