/**
 * Let the wiki's chat assistant drive this sandbox.
 *
 * The assistant runs in an iframe on a wiki page and has no access to the lab; the lab is
 * a separate document the wiki opened. This listens for postMessage requests from that
 * opener and answers them, so "write me a notebook that lists all tensile tests" can end
 * with a notebook open in front of the user instead of a code block in a chat bubble.
 *
 * Everything goes through `jupyterlab-ai-commands`, which Jupyternaut already uses for
 * the same operations. Working at the notebook level rather than firing code at a kernel
 * is deliberate: what the user is left holding is a notebook they can read, change and
 * re-run, not an invisible side effect.
 *
 * ## Trust
 *
 * Only same-origin messages are accepted. That is not much of a check on its own - the
 * wiki and the sandbox share an origin by design - so it is a sanity bound, not the
 * security boundary. The real control is that running code requires the user's
 * confirmation on the wiki side, and that nothing here can exceed the rights of whoever
 * is logged in.
 *
 * Replies always carry the request's `id`. The wiki opened this window and may have
 * several calls outstanding; answering without saying which one is being answered would
 * be worse than not answering.
 */

import { JupyterFrontEnd, JupyterFrontEndPlugin } from '@jupyterlab/application';

import { isAllowed } from './from-url';
import { apiGet, editSlots, oswId, toPascalCase, uploadFile } from './wiki';

const REQUEST = 'osw-sandbox-request';
const REPLY = 'osw-sandbox-reply';
const READY = 'osw-sandbox-ready';

interface ICell {
  type?: string;
  source?: string;
}

async function createNotebook(
  app: JupyterFrontEnd,
  name: string,
  cells: ICell[]
): Promise<any> {
  const notebookName = name.endsWith('.ipynb') ? name : `${name}.ipynb`;
  const created: any = await app.commands.execute(
    'jupyterlab-ai-commands:create-notebook',
    { name: notebookName, language: 'python' }
  );
  const notebookPath = created?.notebookPath ?? notebookName;

  // Plain appends, no bookkeeping: add-cell drops the single empty cell a new notebook
  // starts with, and makes each cell it adds the active one, so the next call's default
  // position of 'below' lands exactly where it should. ('bottom' is not a position -
  // the only values are above and below, relative to the active cell.)
  for (const cell of cells) {
    await app.commands.execute('jupyterlab-ai-commands:add-cell', {
      notebookPath,
      content: cell.source ?? '',
      cellType: cell.type === 'markdown' ? 'markdown' : 'code'
    });
  }

  await app.commands.execute('jupyterlab-ai-commands:save-notebook', { notebookPath });
  return { notebookPath, cells: cells.length };
}

async function openUrl(app: JupyterFrontEnd, url: string): Promise<any> {
  if (!url) {
    return { error: 'no url given' };
  }
  // The ?fromURL= guard itself, not a restatement of it. This used to be its own
  // copy testing `pathname.includes('/img_auth.php/')` while claiming to be "the same
  // rule" - looser than the real one, which anchors on the wiki's script path.
  if (!isAllowed(url)) {
    return { error: `refused ${url} - only this wiki's uploaded files may be opened` };
  }
  const parsed = new URL(url, window.location.href);
  await app.commands.execute('filebrowser:open-url', { url: parsed.href });
  return { opened: parsed.href };
}

/**
 * Commands the host may invoke. Everything jupyterlab-ai-commands exposes, and nothing
 * else: `app.commands` also holds `filebrowser:open-url`, which would load an arbitrary
 * url into the lab and walk straight around the `?fromURL=` allowlist that exists to stop
 * exactly that. A prefix test is the whole guard, so it is written once, here.
 */
const COMMAND_PREFIX = 'jupyterlab-ai-commands:';

/**
 * Every command the host may call, with the argument schema the lab itself declares.
 *
 * Read from the live registry rather than listed here. A hand-kept list is a second
 * source of truth that silently falls behind: the deployed build offers 23 commands and
 * the chat assistant could reach 11 of them, because the rest were never wrapped. This
 * cannot drift - whatever the installed version provides is what gets reported.
 *
 * `describedBy` is the same JSON schema `@jupyterlite/ai` turns into tools for the in-lab
 * agent, so both agents end up describing these commands identically.
 */
async function listLabCommands(app: JupyterFrontEnd): Promise<any> {
  const commands = [];
  for (const id of app.commands.listCommands()) {
    if (!id.startsWith(COMMAND_PREFIX)) {
      continue;
    }
    let args: any = null;
    try {
      args = (await app.commands.describedBy(id))?.args ?? null;
    } catch {
      // A command that cannot describe itself is still callable.
      args = null;
    }
    commands.push({
      command: id,
      label: app.commands.label(id) || id.slice(COMMAND_PREFIX.length),
      caption: app.commands.caption(id) || '',
      args
    });
  }
  return { commands, count: commands.length };
}

async function runLabCommand(
  app: JupyterFrontEnd,
  command: string,
  args: any
): Promise<any> {
  if (!command.startsWith(COMMAND_PREFIX)) {
    return {
      error: `refused: only ${COMMAND_PREFIX}* commands may be called from the host`
    };
  }
  if (!app.commands.hasCommand(command)) {
    return { error: `unknown command ${command}` };
  }
  return await app.commands.execute(command, args);
}

/** The WikiFile category every uploaded file is an instance of. */
const WIKIFILE_CATEGORY = 'Category:OSW11a53cdfbdc24524bf8ac435cbf65d9d';

/**
 * Save the open notebook to the wiki as a WikiFile entity.
 *
 * Done here rather than by shipping the notebook to the chat backend: the sandbox is
 * same-origin with the wiki and already carries the user's session, so the upload is
 * attributed to them. Registered as a command too, so the in-lab agent can call it.
 *
 * The page is named by OSW-ID, not by the label. That is the wiki's convention for an
 * entity - a readable title is a property of the thing, not its identifier - and the
 * label rides in the jsondata slot where Display_title_of picks it up.
 *
 * Saved to the sandbox's own storage first: cells live in an in-memory model until
 * something writes them, and uploading before that stores an empty notebook that looks
 * like a successful save.
 */
export async function saveNotebookToWiki(
  app: JupyterFrontEnd,
  notebookPath: string,
  label: string,
  comment: string
): Promise<any> {
  let path = notebookPath;
  if (!path) {
    const info: any = await app.commands
      .execute('jupyterlab-ai-commands:get-notebook-info', {})
      .catch(() => null);
    path = info?.notebookPath;
  }
  if (!path) {
    return { error: 'no notebook is open to save' };
  }

  await app.commands
    .execute('jupyterlab-ai-commands:save-notebook', { notebookPath: path })
    .catch(() => undefined);

  return await saveFileToWiki(app, path, label, comment);
}

/** Mime types the wiki cares about; `uploadFile` only uses this as a hint. */
const MIME_BY_EXTENSION: Record<string, string> = {
  ipynb: 'application/x-ipynb+json',
  json: 'application/json',
  csv: 'text/csv',
  txt: 'text/plain',
  md: 'text/markdown',
  py: 'text/x-python',
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  svg: 'image/svg+xml',
  pdf: 'application/pdf'
};

function mimeFor(extension: string): string {
  return MIME_BY_EXTENSION[extension] ?? 'application/octet-stream';
}

function base64ToBlob(base64: string, type = 'application/octet-stream'): Blob {
  const binary = atob(base64.replace(/\s/g, ''));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Blob([bytes], { type });
}

/**
 * Copy a file from the wiki into the sandbox, so code can read it.
 *
 * The other half of saveFileToWiki. Without it the only way to get wiki data into the
 * kernel is to write Python that fetches it - which works, but puts the burden of
 * knowing the API on the model, and reaches the wiki as the user with nobody checking
 * what was asked for.
 *
 * Only this wiki's own uploaded files, the same rule `?fromURL=` and `open_url` follow.
 */
export async function loadFileFromWiki(
  app: JupyterFrontEnd,
  title: string,
  targetPath: string
): Promise<any> {
  if (!title) {
    return { error: 'no file page was named' };
  }
  const full = /^File:/i.test(title) ? title : `File:${title}`;
  let info: any;
  try {
    info = await apiGet({
      action: 'query',
      prop: 'imageinfo',
      iiprop: 'url|size|mime',
      titles: full,
      formatversion: 2
    });
  } catch (reason) {
    return { error: `${full} could not be looked up: ${String(reason)}` };
  }
  const page = info?.query?.pages?.[0];
  const image = page?.imageinfo?.[0];
  if (!page || page.missing || !image?.url) {
    return { error: `${full} is not an uploaded file on this wiki` };
  }

  const url = new URL(image.url, window.location.href);
  if (!isAllowed(url.href)) {
    return { error: `refused ${url.href} - only this wiki's own uploaded files` };
  }

  const response = await fetch(url.href, { credentials: 'same-origin' });
  if (!response.ok) {
    return { error: `${full} could not be downloaded: ${response.status}` };
  }

  // Named after the wiki page unless the caller said otherwise, so a file keeps the
  // identity it has on the wiki rather than acquiring a second one here.
  const path = targetPath || url.pathname.split('/').pop() || 'download.bin';
  const text = (image.mime || '').startsWith('text/') ||
    (image.mime || '').includes('json') ||
    /\.(ipynb|txt|csv|md|py|json)$/i.test(path);

  if (text) {
    await app.serviceManager.contents.save(path, {
      type: 'file',
      format: 'text',
      content: await response.text()
    });
  } else {
    const buffer = await response.arrayBuffer();
    let binary = '';
    const bytes = new Uint8Array(buffer);
    for (let i = 0; i < bytes.length; i += 1) {
      binary += String.fromCharCode(bytes[i]);
    }
    await app.serviceManager.contents.save(path, {
      type: 'file',
      format: 'base64',
      content: btoa(binary)
    });
  }

  return { ok: true, path, title: full, size: image.size ?? null, mime: image.mime ?? null };
}

/**
 * Upload any file from the sandbox to the wiki as a WikiFile entity.
 *
 * The same path a notebook takes, because the rules are the same: the page is named by
 * OSW-ID, the readable name lives in `jsondata.label`, and the entity is written before
 * the bytes. Doing this from kernel code with `osw-python` instead would go around the
 * one guard that enforces any of it.
 */
export async function saveFileToWiki(
  app: JupyterFrontEnd,
  filePath: string,
  label: string,
  comment: string
): Promise<any> {
  const path = filePath;
  if (!path) {
    return { error: 'no file path was given' };
  }

  // `format: null` lets the server pick: text comes back as a string, anything else as
  // base64. Asking for text unconditionally corrupts an image.
  let model: any;
  try {
    model = await app.serviceManager.contents.get(path, { content: true });
  } catch (reason) {
    return { error: `${path} could not be read from the sandbox: ${String(reason)}` };
  }

  const extension = path.includes('.') ? path.split('.').pop()!.toLowerCase() : 'bin';
  const contents: string | Blob =
    model.format === 'base64'
      ? base64ToBlob(String(model.content))
      : typeof model.content === 'string'
        ? model.content
        : JSON.stringify(model.content, null, 1);

  const uuid = crypto.randomUUID();
  const target = `${await oswId(uuid)}.${extension}`;
  const title = `File:${target}`;
  const readable = label || path.replace(/\.[^.]+$/, '');
  // Derived with the wiki's own rule - see toPascalCase - rather than a local guess.
  const name = await toPascalCase(readable);

  // Entity first, bytes second - the order `mwjson.api.updatePage` uses, and for its
  // reason. Neither sequence is atomic: `editslots` writes every slot in one revision,
  // but the upload is necessarily a second request, so a failure can always land between
  // them. What differs is the wreckage.
  //
  // Uploading first and describing after leaves a file page carrying a notebook and no
  // jsondata - not an instance of anything, found by no query, displaying its OSW-ID.
  // That is `File:Emmc_events.ipynb`, and it is invisible until somebody goes looking.
  // Describing first leaves the opposite: a well-formed WikiFile whose file is missing.
  // It shows up as what it is, and the retry is clean, because the page already says
  // which notebook belongs there.
  //
  // `editslots` creates the page when it does not exist, so this needs no prior write.
  await editSlots(
    title,
    {
      jsondata: JSON.stringify({
        type: [WIKIFILE_CATEGORY],
        uuid,
        // `name` is what `osl.util.postProcessPage` derives from the label before any
        // wiki-side save (`PageBot.js:476-492`). Leaving it out produced an entity the
        // editor would have filled in - a difference that only surfaces in whatever
        // happens to read `name`.
        name,
        file: title,
        label: [{ text: readable, lang: 'en' }]
      }),
      header: '{{#invoke:Entity|header}}',
      footer: '{{#invoke:Entity|footer}}'
    },
    comment || `Notebook saved from the sandbox: ${readable}`
  );

  let upload: any;
  try {
    upload = await uploadFile(
      target,
      contents,
      comment || `From the sandbox: ${readable}`,
      mimeFor(extension)
    );
  } catch (reason) {
    return {
      error:
        `${title} was created to hold the notebook, but the upload itself failed: ` +
        `${String(reason)}. The page exists and has no file yet. Report this - do not ` +
        'call the save successful.'
    };
  }
  if (!upload?.filename && upload?.result !== 'Success') {
    return {
      error:
        `${title} was created to hold the notebook, but the upload did not complete: ` +
        `${JSON.stringify(upload)}. Report this - do not call the save successful.`
    };
  }

  return {
    ok: true,
    title,
    label: readable,
    url: upload?.imageinfo?.url ?? null,
    path
  };
}

async function runCode(app: JupyterFrontEnd, code: string): Promise<any> {
  // Appended to the open notebook and run there, rather than executed straight against
  // a kernel: the user sees what ran and keeps it, and the result is reproducible by
  // pressing Shift+Enter rather than only by asking the assistant again.
  // get-notebook-info throws when nothing is open rather than returning an empty path,
  // so the absence has to be caught, not tested for.
  const openNotebook = async (): Promise<string | undefined> => {
    try {
      const info: any = await app.commands.execute(
        'jupyterlab-ai-commands:get-notebook-info',
        {}
      );
      return info?.notebookPath;
    } catch {
      return undefined;
    }
  };

  let notebookPath = await openNotebook();
  if (!notebookPath) {
    // A hidden sandbox starts with nothing open, so the first run has nowhere to go.
    // Making one here keeps "code always lands in a notebook" true without requiring
    // the caller to know the order - and it is what makes the pop-out worth offering,
    // since there is always something to show.
    await createNotebook(app, 'sandbox', []);
    notebookPath = await openNotebook();
  }
  if (!notebookPath) {
    return { error: 'no notebook is open in the sandbox and one could not be created' };
  }
  await app.commands.execute('jupyterlab-ai-commands:add-cell', {
    notebookPath,
    content: code,
    cellType: 'code'
  });
  // add-cell makes the new cell active but does not report its id, so it is read back
  // rather than guessed. run-cell needs one and silently running the wrong cell would be
  // the worst available failure.
  const after: any = await app.commands.execute(
    'jupyterlab-ai-commands:get-notebook-info',
    { notebookPath }
  );
  const cellId = after?.activeCellId;
  if (!cellId) {
    return { error: 'could not add a cell to the notebook' };
  }
  const result: any = await app.commands.execute('jupyterlab-ai-commands:run-cell', {
    notebookPath,
    cellId
  });
  return {
    cellId,
    ok: result?.success !== false,
    status: result?.status,
    message: result?.message,
    output: result?.outputs ?? []
  };
}

export const bridge: JupyterFrontEndPlugin<void> = {
  id: 'osw-jupyternaut:wiki-bridge',
  description: "Let the wiki's chat assistant open notebooks and run cells here",
  autoStart: true,
  activate: (app: JupyterFrontEnd) => {
    window.addEventListener('message', async (event: MessageEvent) => {
      if (event.origin !== window.location.origin) {
        return;
      }
      const data = event.data;
      if (!data || data.type !== REQUEST || !data.id) {
        return;
      }

      // Replies go to the window that asked, at its own origin - never '*', which would
      // broadcast notebook contents to whatever else happens to be listening.
      const reply = (payload: any) =>
        (event.source as Window | null)?.postMessage(
          { type: REPLY, id: data.id, ...payload },
          event.origin
        );

      try {
        switch (data.action) {
          case 'status':
            reply({ result: { open: true, ready: true } });
            break;
          case 'open_url':
            // Load a wiki notebook into a sandbox that is already running, instead of
            // reloading the frame - which would throw away a warm kernel and make the
            // user wait out the cold start again.
            reply({ result: await openUrl(app, String(data.url ?? '')) });
            break;
          case 'create_notebook':
            reply({
              result: await createNotebook(app, String(data.name ?? 'notebook'), data.cells ?? [])
            });
            break;
          case 'run':
            reply({ result: await runCode(app, String(data.code ?? '')) });
            break;
          case 'save_file_to_wiki':
            reply({
              result: await saveFileToWiki(
                app,
                String(data.path ?? ''),
                String(data.label ?? ''),
                String(data.comment ?? '')
              )
            });
            break;
          case 'load_file_from_wiki':
            reply({
              result: await loadFileFromWiki(
                app,
                String(data.title ?? ''),
                String(data.path ?? '')
              )
            });
            break;
          case 'save_notebook_to_wiki':
            reply({
              result: await saveNotebookToWiki(
                app,
                String(data.notebookPath ?? ''),
                String(data.label ?? data.filename ?? ''),
                String(data.comment ?? '')
              )
            });
            break;
          case 'list_commands':
            reply({ result: await listLabCommands(app) });
            break;
          case 'command':
            // The whole jupyterlab-ai-commands surface, so the chat assistant can do
            // what the in-lab agent can: kernels, cells, notebooks.
            reply({
              result: await runLabCommand(
                app,
                String(data.command ?? ''),
                data.args ?? {}
              )
            });
            break;
          default:
            reply({ error: `unknown action ${data.action}` });
        }
      } catch (reason) {
        reply({ error: String(reason) });
      }
    });

    // Whoever opened us cannot tell when the lab has finished starting, and a cold
    // start is tens of seconds. Announcing readiness lets them wait for this instead of
    // polling, and say "still starting" rather than appearing to hang.
    //
    // Both opener and parent: the sandbox is normally an iframe on the wiki page, where
    // the host is `window.parent`, but it also works in its own tab, where it is
    // `window.opener`. Checking only one left the other waiting out its full timeout.
    app.restored.then(() => {
      const hosts = [window.opener, window.parent !== window ? window.parent : null];
      for (const host of hosts) {
        if (host) {
          (host as Window).postMessage({ type: READY }, window.location.origin);
        }
      }
    });
  }
};
