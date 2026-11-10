/**
 * The order the two halves of a notebook save are written in, and what happens when the
 * second half fails.
 *
 * Neither order is atomic - `editslots` writes every slot in one revision, but the upload
 * is necessarily a second request, so a failure can always land between them. What
 * differs is the wreckage, and this pins the choice:
 *
 *   upload first  -> a file page holding a notebook and no jsondata: an instance of
 *                    nothing, found by no query, showing its OSW-ID. That is
 *                    `File:Emmc_events.ipynb`, and it is invisible until someone looks.
 *   entity first  -> a well-formed WikiFile whose file is missing. It shows up as what it
 *                    is, and a retry uploads into a page that already says which notebook
 *                    belongs there.
 *
 * The second is what `mwjson.api.updatePage` does (MwJson_api.js:438-453), so this tests
 * that the lab agrees with the wiki rather than inventing its own sequence.
 *
 *     node tests/save_order.cjs
 */

const assert = require('assert');
const fs = require('fs');

const BRIDGE_JS = process.env.BRIDGE_JS || '/tmp/wiki-unit/bridge.js';
const MWJSON_UTIL =
  process.env.MWJSON_UTIL ||
  '/usr/local/docker-container/osw-dev/mediawiki/extensions/MwJson/' +
    'modules/ext.MwJson.util/MwJson_util.js';

const results = [];
async function check(name, fn) {
  try {
    await fn();
    results.push([name, true, '']);
  } catch (reason) {
    results.push([name, false, String((reason && reason.message) || reason)]);
  }
}

/** Every wiki request, in order, as a short tag: 'editslots' or 'upload'. */
let calls = [];

function installBrowser({ uploadFails = false, slotsFail = false } = {}) {
  calls = [];
  global.window = {
    location: {
      href: 'https://wiki.example.org/w/extensions/JupyterLite/dist/lab/index.html',
      pathname: '/w/extensions/JupyterLite/dist/lab/index.html',
      origin: 'https://wiki.example.org'
    }
  };
  global.document = {
    head: {
      appendChild(script) {
        new Function('mwjson', fs.readFileSync(MWJSON_UTIL, 'utf8'))(global.window.mwjson);
        setTimeout(() => script.onload(), 0);
      }
    },
    createElement: () => ({})
  };
  global.FormData = FormData;
  global.fetch = async (url, options = {}) => {
    const href = String(url);
    const json = body => ({ ok: true, status: 200, json: async () => body });
    if (href.includes('meta=tokens')) {
      return json({ query: { tokens: { csrftoken: 'TOKEN+\\' } } });
    }
    if (options.body instanceof FormData) {
      calls.push('upload');
      return uploadFails
        ? json({ error: { code: 'fileexists-no-change', info: 'upload refused' } })
        : json({ upload: { result: 'Success', filename: 'n.ipynb',
                           imageinfo: { url: 'https://wiki.example.org/img/n.ipynb' } } });
    }
    calls.push('editslots');
    return slotsFail
      ? json({ error: { code: 'protectedpage', info: 'slots refused' } })
      : json({ editslots: { result: 'Success' } });
  };
}

/** A JupyterLab app, to the extent saveNotebookToWiki uses one. */
function fakeApp(notebook = 'work/analysis.ipynb') {
  return {
    commands: {
      execute: async (command) => {
        if (command.endsWith('get-notebook-info')) {
          return { notebookPath: notebook };
        }
        return {};
      }
    },
    serviceManager: {
      contents: {
        get: async () => ({ content: { cells: [], metadata: {} } })
      }
    }
  };
}

async function main() {
  if (!fs.existsSync(BRIDGE_JS)) {
    console.error(`\n  compiled module not found at ${BRIDGE_JS} - compile src first\n`);
    return 1;
  }
  // The wiki's own naming helpers, loaded the way the extension loads them. Not
  // optional here: every save mints an OSW-ID through them, so without the file there
  // is nothing to test. Said plainly rather than failing inside a fake browser.
  if (!fs.existsSync(MWJSON_UTIL)) {
    console.error(
      `\n  ${MWJSON_UTIL} not found.` +
        '\n  Set MWJSON_UTIL to MwJson_util.js from a checkout of' +
        ' OpenSemanticLab/mediawiki-extensions-MwJson.\n'
    );
    return 1;
  }
  const load = (opts) => {
    installBrowser(opts);
    for (const key of Object.keys(require.cache)) {
      if (key.includes('/wiki-unit/')) {
        delete require.cache[key];
      }
    }
    return require(BRIDGE_JS);
  };

  await check('the entity is written before the bytes', async () => {
    const bridge = load();
    const result = await bridge.saveNotebookToWiki(fakeApp(), '', 'My analysis', 'c');
    assert.ok(result.ok, `save failed: ${JSON.stringify(result)}`);
    assert.deepStrictEqual(calls, ['editslots', 'upload'],
      'an upload before its jsondata is how a file page ends up an entity-less blob');
  });

  await check('the page is named by OSW-ID, not by the label', async () => {
    const bridge = load();
    const result = await bridge.saveNotebookToWiki(fakeApp(), '', 'My analysis', 'c');
    assert.match(result.title, /^File:OSW[0-9a-f]{32}\.ipynb$/,
      `got ${result.title}`);
    assert.strictEqual(result.label, 'My analysis', 'the readable name is kept');
  });

  await check('a failed upload is reported, not returned as success', async () => {
    const bridge = load({ uploadFails: true });
    const result = await bridge.saveNotebookToWiki(fakeApp(), '', 'My analysis', 'c');
    assert.ok(!result.ok, 'a failed upload must not report ok');
    assert.ok(result.error, 'it must carry an error');
    // The page exists by now. Saying which one is the difference between a recoverable
    // state and a stray artefact nobody knows about.
    assert.match(result.error, /File:OSW[0-9a-f]{32}\.ipynb/,
      `the error should name the page that was created: ${result.error}`);
    assert.match(result.error, /do not call the save successful/i,
      'the model asserts this result to the user, so the refusal has to be explicit');
  });

  await check('a failed entity write leaves no upload behind', async () => {
    const bridge = load({ slotsFail: true });
    await bridge.saveNotebookToWiki(fakeApp(), '', 'My analysis', 'c')
      .catch(reason => ({ error: String(reason) }));
    assert.ok(!calls.includes('upload'),
      'nothing should be uploaded once the entity write has failed');
  });

  await check('the jsondata carries what makes it a WikiFile', async () => {
    const bridge = load();
    const bodies = [];
    const realFetch = global.fetch;
    global.fetch = async (url, options = {}) => {
      if (options.body && !(options.body instanceof FormData)) {
        bodies.push(new URLSearchParams(String(options.body)));
      }
      return realFetch(url, options);
    };
    await bridge.saveNotebookToWiki(fakeApp(), '', 'My analysis', 'c');
    const slots = bodies.find(b => b.get('slot_jsondata'));
    assert.ok(slots, 'jsondata should have been written');
    const data = JSON.parse(slots.get('slot_jsondata'));
    assert.deepStrictEqual(data.type, ['Category:OSW11a53cdfbdc24524bf8ac435cbf65d9d']);
    assert.match(data.uuid, /^[0-9a-f-]{36}$/);
    assert.strictEqual(data.label[0].text, 'My analysis');
    // Derived by the wiki's own rule; without it the entity is one the editor would
    // have filled in.
    assert.strictEqual(data.name, 'MyAnalysis');
    assert.strictEqual(data.file, slots.get('title'));
    // header/footer always carry the default invokes and nothing else.
    assert.strictEqual(slots.get('slot_header'), '{{#invoke:Entity|header}}');
    assert.strictEqual(slots.get('slot_footer'), '{{#invoke:Entity|footer}}');
  });

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
