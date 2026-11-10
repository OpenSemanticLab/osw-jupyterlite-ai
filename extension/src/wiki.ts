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

function apiUrl(): string {
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
async function csrfToken(): Promise<string> {
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

/**
 * What a wiki file may be called: an OSW-ID and an extension, nothing else.
 *
 * Lowercase hex only, which is exactly what `oswId()` produces from
 * `crypto.randomUUID()` - the rule is as tight as the one sanctioned way to mint a name,
 * so anything else is a caller that built a name by hand.
 */
const OSW_FILENAME = /^OSW[0-9a-f]{32}\.[A-Za-z0-9]+$/;

/**
 * Upload a file to the wiki as the logged-in user.
 *
 * Multipart rather than urlencoded, because `action=upload` wants the bytes as a file
 * part. Same-origin with `credentials`, so the session cookie carries the identity and
 * nothing here holds a credential of its own.
 *
 * The name is checked here rather than in each caller. Every upload the lab can make
 * passes through this function - the bridge action, the save-notebook-to-wiki command and
 * `mw.osw.sandbox.saveNotebookToWiki` - and a caller that named a file after its contents
 * is how `File:Emmc_events.ipynb` came to exist: a page under a clear name, outside the
 * convention, that no query can find.
 */
export async function uploadFile(
  filename: string,
  contents: string | Blob,
  comment: string,
  mime = 'application/x-ipynb+json'
): Promise<any> {
  // Thrown, not returned: a returned error can be read past, and the next caller to get
  // this wrong will be code rather than a person.
  if (!OSW_FILENAME.test(filename)) {
    throw new Error(
      `refusing to upload '${filename}': wiki files are named OSW<uuid>.<ext> - ` +
        'mint the name with oswId() and put the readable name in jsondata.label'
    );
  }

  const form = new FormData();
  form.set('action', 'upload');
  form.set('format', 'json');
  form.set('formatversion', '2');
  form.set('filename', filename);
  form.set('comment', comment);
  // Overwriting a file of the same name is the point when a notebook is saved twice;
  // without this the second save fails on the "exists" warning.
  form.set('ignorewarnings', '1');
  form.set('token', await csrfToken());
  // A Blob already carries its bytes; wrapping a string is what turns text into one.
  // Images and pdfs must not take the string path - that would re-encode them as UTF-8
  // and store something that is no longer the file.
  form.set(
    'file',
    contents instanceof Blob ? contents : new Blob([contents], { type: mime }),
    filename
  );

  const response = await fetch(apiUrl(), {
    method: 'POST',
    credentials: 'same-origin',
    body: form
  });
  if (!response.ok) {
    throw new Error(`api.php responded ${response.status}`);
  }
  const body = await response.json();
  if (body.error) {
    throw new Error(`${body.error.code}: ${body.error.info}`);
  }
  return body.upload;
}

/** Where `mwjson.util` lives, relative to the wiki's script path. */
const MWJSON_UTIL = '/extensions/MwJson/modules/ext.MwJson.util/MwJson_util.js';

let utilCache: any | undefined;

/**
 * The wiki's own `mwjson.util`, loaded into the lab.
 *
 * Normally `mwjson` arrives through ResourceLoader, which exists only in a MediaWiki page
 * view - and the lab is a static document served out of the extension directory, with no
 * `mw` and no loader. But the file itself is an ordinary same-origin script whose only
 * top-level statement is `mwjson.util = class {...}`, so it can simply be loaded: seed
 * the namespace it assigns onto, add a script tag, and read the class back off it.
 *
 * Loaded rather than reimplemented, and with no local fallback on purpose. The values
 * these helpers produce go into `jsondata`, where the wiki compares them against values
 * minted by its own editor - so a second implementation would be correct the day it was
 * written and quietly diverge afterwards. A fallback would also hide the one failure
 * worth seeing: if this file ever moves, that should surface here, where it can be fixed,
 * rather than as pages that are subtly wrong. Nothing is lost by being strict, either -
 * every other part of this save (`action=editslots`, the WikiFile category, the jsondata
 * slot) already requires the OSW stack this file belongs to.
 */
async function mwjsonUtil(): Promise<any> {
  if (utilCache !== undefined) {
    return utilCache;
  }
  utilCache = await new Promise<any>((resolve, reject) => {
    const host = window as any;
    if (host.mwjson?.util) {
      resolve(host.mwjson.util);
      return;
    }
    // The file assigns onto `mwjson`; it does not create it.
    host.mwjson = host.mwjson ?? {};
    const url = `${scriptPath()}${MWJSON_UTIL}`;
    const script = document.createElement('script');
    script.src = url;
    script.onload = () =>
      host.mwjson?.util
        ? resolve(host.mwjson.util)
        : reject(new Error(`${url} loaded but did not define mwjson.util`));
    script.onerror = () => reject(new Error(`could not load ${url}`));
    document.head.appendChild(script);
  }).catch(reason => {
    utilCache = undefined; // a transient network failure should not be cached
    throw reason;
  });
  return utilCache;
}

/** `mwjson.util.toPascalCase`, as the wiki itself defines it. */
export async function toPascalCase(input: string): Promise<string> {
  return String((await mwjsonUtil()).toPascalCase(input));
}


/**
 * An OSW-ID: the wiki's canonical name for an entity page.
 *
 * Delegated to `mwjson.util.OswId` for the same reason as toPascalCase - the convention
 * belongs to the wiki, and a second copy of it here would be a second thing to keep
 * right.
 */
export async function oswId(uuid?: string): Promise<string> {
  return String((await mwjsonUtil()).OswId(uuid));
}

/**
 * Write slots on a page through `action=editslots`.
 *
 * The slot content goes in `slot_<name>` parameters, not `<name>` - a plain `jsondata`
 * is accepted and ignored, which looks like a successful write and stores nothing.
 */
export async function editSlots(
  title: string,
  slots: Record<string, string>,
  summary: string
): Promise<any> {
  const form = new URLSearchParams();
  form.set('action', 'editslots');
  form.set('format', 'json');
  form.set('formatversion', '2');
  form.set('title', title);
  form.set('summary', summary);
  for (const [name, content] of Object.entries(slots)) {
    form.set(`slot_${name}`, content);
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
  return body.editslots;
}
