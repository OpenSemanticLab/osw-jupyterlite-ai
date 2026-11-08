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

import { uploadFile } from './wiki';

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
  // Same rule as the launcher and the ?fromURL= guard: only this wiki's own uploaded
  // files. A notebook opened here runs with the reader's rights.
  const parsed = new URL(url, window.location.href);
  const allowed =
    parsed.origin === window.location.origin &&
    parsed.pathname.includes('/img_auth.php/');
  if (!allowed) {
    return { error: `refused ${url} - only this wiki's uploaded files may be opened` };
  }
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

/**
 * Save the open notebook to the wiki as an uploaded file.
 *
 * Done here rather than by shipping the notebook back over the bridge: the sandbox is
 * same-origin with the wiki and already carries the user's session, so the bytes never
 * leave the browser and the upload is attributed to them.
 *
 * The notebook is saved to the sandbox's own storage first - its cells live in an
 * in-memory model until something writes them, and uploading before that stores an
 * empty file that looks like a successful save.
 */
async function saveNotebookToWiki(
  app: JupyterFrontEnd,
  notebookPath: string,
  filename: string,
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

  const model = await app.serviceManager.contents.get(path, { content: true });
  const contents =
    typeof model.content === 'string'
      ? model.content
      : JSON.stringify(model.content, null, 1);

  const name = (filename || path).replace(/[^A-Za-z0-9._-]/g, '_');
  const target = name.endsWith('.ipynb') ? name : `${name}.ipynb`;
  const upload = await uploadFile(
    target,
    contents,
    comment || `Notebook saved from the sandbox: ${path}`
  );

  // The title is what reopens it: ?fromURL= takes a wiki file url, and open_sandbox
  // takes the File: page title.
  return {
    ok: upload?.result === 'Success' || !!upload?.filename,
    title: upload?.filename ? `File:${upload.filename}` : null,
    url: upload?.imageinfo?.url ?? null,
    notebookPath: path
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
          case 'save_notebook_to_wiki':
            reply({
              result: await saveNotebookToWiki(
                app,
                String(data.notebookPath ?? ''),
                String(data.filename ?? ''),
                String(data.comment ?? '')
              )
            });
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
