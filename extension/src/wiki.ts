/**
 * Talking to the MediaWiki that served this page.
 *
 * Every call here is same-origin and sends the browser's cookies, so it runs as whoever
 * is logged into the wiki - there is no credential to configure and no token to hold.
 * That is the whole reason the sandbox is served from the wiki's own origin.
 *
 * It also means every write goes out in that user's name and under their rights. Nothing
 * in this module can exceed what the user could do by hand, which is the property worth
 * keeping: treat a permission error as the answer, never as something to route around.
 */

/** Where the wiki's entry points live, derived from where this page was served from. */
export function scriptPath(): string {
  // The lab is served from <script path>/extensions/JupyterLite/dist/lab/, so the wiki's
  // script path is whatever precedes /extensions/. Derived rather than configured: a
  // hardcoded "/w" would break the moment someone deploys at a different $wgScriptPath,
  // and the failure would look like "the agent cannot see the wiki" rather than a path bug.
  const marker = '/extensions/';
  const path = window.location.pathname;
  const index = path.indexOf(marker);
  return index === -1 ? '/w' : path.slice(0, index);
}

export function apiUrl(): string {
  return `${scriptPath()}/api.php`;
}

export interface IApiParams {
  [key: string]: string | number | boolean | undefined;
}

/** A GET against the wiki API. Throws on an API-level error, not just on HTTP failure. */
export async function apiGet<T = any>(params: IApiParams): Promise<T> {
  const url = new URL(apiUrl(), window.location.href);
  url.searchParams.set('format', 'json');
  url.searchParams.set('formatversion', '2');
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) {
      url.searchParams.set(key, String(value));
    }
  }
  const response = await fetch(url.toString(), {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' }
  });
  if (!response.ok) {
    throw new Error(`${url.pathname} responded ${response.status}`);
  }
  const body = await response.json();
  if (body.error) {
    // MediaWiki answers 200 with an error object, so a bare response.ok check would let
    // readapidenied through as success and the caller would parse an error as data.
    throw new Error(`${body.error.code}: ${body.error.info}`);
  }
  return body;
}

/**
 * A CSRF token for the current session.
 *
 * Not cached: tokens are tied to the session, and a session that changed under us (logout
 * in another tab, expiry) would otherwise produce a confusing `badtoken` much later.
 */
export async function csrfToken(): Promise<string> {
  const body = await apiGet({ action: 'query', meta: 'tokens', type: 'csrf' });
  const token = body?.query?.tokens?.csrftoken;
  if (!token || token === '+\\') {
    // '+\\' is the anonymous token: present, well-formed and useless for writing.
    throw new Error('not logged in - the wiki issued an anonymous edit token');
  }
  return token;
}

/** A POST against the wiki API, with the CSRF token attached. */
export async function apiPost<T = any>(params: IApiParams): Promise<T> {
  const form = new URLSearchParams();
  form.set('format', 'json');
  form.set('formatversion', '2');
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) {
      form.set(key, String(value));
    }
  }
  form.set('token', await csrfToken());

  const response = await fetch(apiUrl(), {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: form
  });
  if (!response.ok) {
    throw new Error(`api.php responded ${response.status}`);
  }
  const body = await response.json();
  if (body.error) {
    throw new Error(`${body.error.code}: ${body.error.info}`);
  }
  return body;
}

/** Who the wiki thinks we are. `null` when nobody is logged in. */
export async function currentUser(): Promise<string | null> {
  try {
    const body = await apiGet({ action: 'query', meta: 'userinfo' });
    const info = body?.query?.userinfo;
    return info && !info.anon ? info.name : null;
  } catch (reason) {
    console.warn('osw-jupyternaut: could not read the current user', reason);
    return null;
  }
}
