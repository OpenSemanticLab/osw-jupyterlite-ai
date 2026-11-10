/**
 * Unit tests for the lab's wiki layer - the parts that never reach a browser.
 *
 * The browser suites in ../../tests drive a real wiki and a real lab, which is the only
 * way to test most of this extension. But three things decided here are pure logic, cheap
 * to get wrong, and expensive to notice: what a file may be called, where `mwjson.util`
 * comes from, and the order the two halves of a save are written in. All three produced
 * real defects (`File:Emmc_events.ipynb` was both a clear name and an entity-less blob),
 * and none had any coverage.
 *
 * No test framework and no dependencies: the extension has neither, and adding jest to
 * assert on six functions would be the larger change.
 *
 *     node tests/wiki_unit.cjs           # after compiling src/wiki.ts (see below)
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const WIKI_JS = process.env.WIKI_JS || '/tmp/wiki-unit/wiki.js';

/** The wiki's real `mwjson.util`, so delegation is tested against the actual file. */
const MWJSON_UTIL =
  process.env.MWJSON_UTIL ||
  '/usr/local/docker-container/osw-dev/mediawiki/extensions/MwJson/' +
    'modules/ext.MwJson.util/MwJson_util.js';

const results = [];
function check(name, fn) {
  try {
    fn();
    results.push([name, true, '']);
  } catch (reason) {
    results.push([name, false, String((reason && reason.message) || reason)]);
  }
}
async function checkAsync(name, fn) {
  try {
    await fn();
    results.push([name, true, '']);
  } catch (reason) {
    results.push([name, false, String((reason && reason.message) || reason)]);
  }
}

// ---------------------------------------------------------------------------
// A browser, to the extent this module uses one.
// ---------------------------------------------------------------------------

/** Requests the module made, so a test can assert what was sent and in what order. */
let sent = [];

let scriptLoads = 0;

function installBrowser({ utilAvailable = true } = {}) {
  sent = [];
  scriptLoads = 0;

  global.window = {
    // The lab is served from <script path>/extensions/JupyterLite/dist/lab/, which is
    // what scriptPath() parses. A non-"/w" path on purpose: a hardcoded "/w" would pass
    // a test that used "/w".
    location: {
      href: 'https://wiki.example.org/mw/extensions/JupyterLite/dist/lab/index.html',
      pathname: '/mw/extensions/JupyterLite/dist/lab/index.html',
      // Real browsers supply this; leaving it out made every same-origin check fail
      // closed and looked like the code refusing its own wiki.
      origin: 'https://wiki.example.org'
    }
  };

  global.document = {
    head: {
      appendChild(script) {
        scriptLoads += 1;
        // Stand in for the browser fetching and executing the script tag.
        if (!utilAvailable) {
          setTimeout(() => script.onerror(new Error('404')), 0);
          return;
        }
        const source = fs.readFileSync(MWJSON_UTIL, 'utf8');
        new Function('mwjson', source)(global.window.mwjson);
        setTimeout(() => script.onload(), 0);
      }
    },
    createElement: () => ({ set src(v) { this._src = v; }, get src() { return this._src; } })
  };

  global.fetch = async (url, options = {}) => {
    sent.push({ url: String(url), options });
    const href = String(url);
    if (href.includes('meta=tokens')) {
      return jsonResponse({ query: { tokens: { csrftoken: 'TOKEN+\\' } } });
    }
    if (options.body instanceof global.FormData) {
      return jsonResponse({ upload: { result: 'Success', filename: 'x.ipynb' } });
    }
    return jsonResponse({ editslots: { result: 'Success' } });
  };

  function jsonResponse(body) {
    return { ok: true, status: 200, json: async () => body };
  }
}

// FormData/Blob exist in node 18+, but make the dependency explicit.
assert.ok(typeof FormData === 'function', 'this test needs node >= 18 for FormData');
global.FormData = FormData;

// ---------------------------------------------------------------------------

async function main() {
  if (!fs.existsSync(WIKI_JS)) {
    console.error(`\n  compiled module not found at ${WIKI_JS}` +
      '\n  compile it first:  npx tsc src/wiki.ts --outDir /tmp/wiki-unit ' +
      '--module commonjs --target es2020 --skipLibCheck --lib es2020,dom\n');
    return 1;
  }
  const haveUtil = fs.existsSync(MWJSON_UTIL);
  if (!haveUtil) {
    console.error(`\n  SKIPPING the mwjson.util tests: ${MWJSON_UTIL} not found.` +
      '\n  Set MWJSON_UTIL to a checkout of the MwJson extension.\n');
  }

  // Each group reloads the module so the one-shot mwjson.util cache starts empty.
  const load = (opts) => {
    installBrowser(opts);
    delete require.cache[require.resolve(WIKI_JS)];
    return require(WIKI_JS);
  };

  // -- the name guard ------------------------------------------------------
  //
  // The defect this exists for: a caller naming a file after its contents, which put
  // `File:Emmc_events.ipynb` on the wiki - outside the convention, found by no query.
  let wiki = load();
  for (const bad of [
    'notes.ipynb',                       // a clear name, the actual defect
    'Emmc_events.ipynb',
    'OSW123.ipynb',                      // too short to be an OSW-ID
    'OSW' + 'A'.repeat(32) + '.ipynb',   // uppercase: not what oswId() emits
    'OSW' + 'a'.repeat(32),              // no extension
    'OSW' + 'a'.repeat(32) + '.',        // empty extension
    'OSW' + 'a'.repeat(33) + '.ipynb',   // one character too many
    '',
    'subdir/OSW' + 'a'.repeat(32) + '.ipynb'
  ]) {
    await checkAsync(`uploadFile refuses ${JSON.stringify(bad)}`, async () => {
      await assert.rejects(
        () => wiki.uploadFile(bad, '{}', 'c'),
        /refusing to upload/,
        'a name outside the convention must be refused'
      );
    });
  }

  await checkAsync('the refusal happens before anything is sent', async () => {
    sent = [];
    await assert.rejects(() => wiki.uploadFile('notes.ipynb', '{}', 'c'));
    assert.deepStrictEqual(sent, [], 'no request should leave for a refused name');
  });

  await checkAsync('uploadFile accepts a well-formed OSW name', async () => {
    const name = 'OSW' + 'ab12'.repeat(8) + '.ipynb';
    const result = await wiki.uploadFile(name, '{}', 'comment');
    assert.strictEqual(result.result, 'Success');
    const upload = sent.find(r => r.options.body instanceof FormData);
    assert.ok(upload, 'an upload request should have been sent');
    assert.strictEqual(upload.options.body.get('filename'), name);
    // Without this a second save fails on the "exists" warning instead of making a
    // new revision.
    assert.strictEqual(upload.options.body.get('ignorewarnings'), '1');
  });

  await checkAsync('the api path is derived from where the lab is served, not hardcoded',
    async () => {
      assert.strictEqual(wiki.scriptPath(), '/mw');
      const tokenCall = sent.find(r => r.url.includes('meta=tokens'));
      assert.ok(tokenCall.url.startsWith('https://wiki.example.org/mw/api.php'),
        `unexpected api url: ${tokenCall && tokenCall.url}`);
    });

  // -- the img_auth allowlist ----------------------------------------------
  //
  // One implementation, in from-url.ts. There were four, in three spellings: two in
  // bridge.ts testing `pathname.includes('/img_auth.php/')` while a comment claimed
  // they were "the same rule as the ?fromURL= guard" - which anchors on the script
  // path instead, and so refuses things `includes` lets through.
  const guard = (() => {
    installBrowser();
    const path = require.resolve(WIKI_JS).replace(/wiki\.js$/, 'from-url.js');
    delete require.cache[path];
    return require(path).isAllowed;
  })();

  for (const [url, ok, why] of [
    ['https://wiki.example.org/mw/img_auth.php/2/23/OSWaaaa.ipynb', true, 'a wiki file'],
    ['/mw/img_auth.php/2/23/OSWaaaa.ipynb', true, 'relative, same wiki'],
    ['https://evil.example.com/mw/img_auth.php/x.ipynb', false, 'another origin'],
    ['https://wiki.example.org/mw/index.php?title=X', false, 'not an uploaded file'],
    // The case the loose `includes` spelling let through: path traversal that merely
    // contains the segment somewhere.
    ['https://wiki.example.org/evil/img_auth.php/x.ipynb', false, 'wrong script path'],
    ['not a url at all', false, 'unparseable']
  ]) {
    check(`allowlist ${ok ? 'accepts' : 'refuses'} ${why}`, () => {
      assert.strictEqual(guard(url), ok, url);
    });
  }

  // -- editSlots -----------------------------------------------------------
  await checkAsync('editSlots sends slot_<name>, not <name>', async () => {
    wiki = load();
    await wiki.editSlots('File:X', { jsondata: '{"a":1}' }, 'summary');
    const call = sent.find(r => r.options.body && !(r.options.body instanceof FormData));
    const body = new URLSearchParams(String(call.options.body));
    // A plain `jsondata` parameter is accepted and ignored by the API: the write looks
    // like it succeeded and stores nothing.
    assert.strictEqual(body.get('slot_jsondata'), '{"a":1}');
    assert.strictEqual(body.get('jsondata'), null);
    assert.strictEqual(body.get('action'), 'editslots');
  });

  // -- mwjson.util delegation ----------------------------------------------
  if (haveUtil) {
    await checkAsync('toPascalCase matches the wiki\'s own implementation', async () => {
      wiki = load();
      const mwjson = {};
      new Function('mwjson', fs.readFileSync(MWJSON_UTIL, 'utf8'))(mwjson);
      const cases = [
        'Persistence test 1a2b3c', 'battery cycling analysis', 'Über alles', 'Grüße',
        'EMMC events', 'my-notebook_v2', '  leading space', 'ÄÖÜäöüß', 'a', 'A',
        '123 start with digits', 'already PascalCase', 'mixed Case-with_punct!',
        'Messung Größe 5', 'tensile test (sample 3)', 'foo.bar.baz', 'UPPER CASE',
        'Notebook 2026-11-08', 'éclair café', 'multiple   spaces'
      ];
      for (const input of cases) {
        assert.strictEqual(
          await wiki.toPascalCase(input),
          mwjson.util.toPascalCase(input),
          `diverged on ${JSON.stringify(input)}`
        );
      }
    });

    await checkAsync('oswId matches the wiki\'s own implementation', async () => {
      wiki = load();
      const mwjson = {};
      new Function('mwjson', fs.readFileSync(MWJSON_UTIL, 'utf8'))(mwjson);
      const uuid = '0190b3f2-1c4d-7e8a-9b0c-1d2e3f4a5b6c';
      assert.strictEqual(await wiki.oswId(uuid), mwjson.util.OswId(uuid));
      const minted = await wiki.oswId();
      assert.match(minted, /^OSW[0-9a-f]{32}$/,
        'a minted id must satisfy the upload name guard');
    });

    await checkAsync('the module is fetched once, not per call', async () => {
      wiki = load();
      assert.strictEqual(scriptLoads, 0, 'nothing should load before it is needed');
      await wiki.toPascalCase('one');
      assert.strictEqual(scriptLoads, 1);
      await wiki.toPascalCase('two');
      await wiki.oswId();
      // One script tag for the whole module lifetime: a save does several of these and
      // refetching 30 kB each time would be visible.
      assert.strictEqual(scriptLoads, 1, `loaded ${scriptLoads} times, expected 1`);
    });
  }

  await checkAsync('a missing mwjson.util fails loudly rather than guessing', async () => {
    wiki = load({ utilAvailable: false });
    // No local fallback on purpose: a second implementation of the wiki's naming rules
    // would be correct when written and diverge silently afterwards, and these values go
    // into jsondata where the wiki compares them against its own editor's output.
    await assert.rejects(() => wiki.toPascalCase('x'), /could not load/);
  });

  // -----------------------------------------------------------------------
  const failed = results.filter(([, ok]) => !ok);
  for (const [name, ok, detail] of results) {
    console.log(`  ${ok ? 'ok  ' : 'FAIL'}  ${name}${detail ? ' - ' + detail : ''}`);
  }
  console.log(`\n  ${results.length - failed.length}/${results.length} checks passed`);
  return failed.length ? 1 : 0;
}

main().then(code => process.exit(code), reason => {
  console.error(reason);
  process.exit(1);
});
