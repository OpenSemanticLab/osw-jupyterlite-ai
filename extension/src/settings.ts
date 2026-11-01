/**
 * Apply the wiki's own agent configuration, as defaults.
 *
 * The site is a static build, so the model it talks to would otherwise be fixed at build
 * time. `action=jupyterliteconfig` lets a deployment set it in `CustomSettings.php`
 * instead - the same place every other per-wiki setting lives - and this applies what it
 * says the first time a browser starts the agent.
 *
 * **Defaults, not policy.** JupyterLite keeps user settings in the browser and they win
 * over anything shipped with the site, so a user who has configured their own provider
 * keeps it. That is deliberate; if a deployment ever needs to *force* a provider, this is
 * the wrong mechanism and the provider registry has to refuse the others.
 *
 * A consequence worth knowing before it bites: once this has written a value, later
 * changes on the wiki do not reach that browser. `SETTINGS_VERSION` is the escape hatch -
 * raise it and every browser takes the new defaults once more.
 */

import { ISettingRegistry } from '@jupyterlab/settingregistry';

import { apiGet } from './wiki';

/** Jupyternaut's settings plugin. Moves between releases; pinned here deliberately. */
export const SETTINGS_MODEL = '@jupyternaut/persona:settings-model';

/**
 * Bump to re-apply defaults in browsers that already took an older set.
 *
 * Without this, a deployment that changes its model is invisible to exactly the users who
 * have used the sandbox before - the ones most likely to notice and least likely to think
 * to reset their settings.
 */
export const SETTINGS_VERSION = 1;

const VERSION_KEY = 'osw-jupyternaut:applied-settings-version';

export interface IWikiAgentConfig {
  /** Provider id understood by Jupyternaut, e.g. 'generic' for an OpenAI-compatible API. */
  provider?: string;
  model?: string;
  baseURL?: string;
  /** Extra per-provider options, merged verbatim into the provider entry. */
  parameters?: Record<string, unknown>;
  /** Shown in the provider picker. */
  name?: string;
  /** Written through as-is; everything the schema allows is permitted here. */
  [key: string]: unknown;
}

export interface IWikiConfigResponse {
  basePath?: string;
  settings?: IWikiAgentConfig;
}

/** Ask the wiki what it wants. Returns null when the module is absent or we may not use it. */
export async function fetchWikiConfig(): Promise<IWikiConfigResponse | null> {
  try {
    const body = await apiGet({ action: 'jupyterliteconfig' });
    return body?.jupyterliteconfig ?? null;
  } catch (reason) {
    // Not fatal, and not even unusual: the extension may be disabled, or the reader may
    // lack jupyterlite-use. The agent still works, the user just configures it by hand.
    console.info('osw-jupyternaut: no wiki agent configuration', reason);
    return null;
  }
}

/** The provider entry id this module owns. Stable, so re-applying updates rather than adds. */
const PROVIDER_ID = 'osw';

export async function applyWikiDefaults(
  registry: ISettingRegistry,
  config: IWikiAgentConfig
): Promise<void> {
  if (!config.provider || !config.model) {
    console.info('osw-jupyternaut: wiki config names no provider/model, leaving settings alone');
    return;
  }

  const settings = await registry.load(SETTINGS_MODEL);

  // The marker lives in localStorage rather than in the settings registry because the
  // registry validates against Jupyternaut's schema and rejects keys it does not declare.
  // Same browser, same origin, same lifetime as the user settings it guards.
  const appliedVersion = Number(window.localStorage.getItem(VERSION_KEY) ?? 0);

  const providers = (settings.get('providers').composite as any[]) ?? [];
  const existing = providers.find(entry => entry?.id === PROVIDER_ID);

  if (existing && appliedVersion >= SETTINGS_VERSION) {
    // Applied already, and the user may have edited it since; theirs wins.
    return;
  }

  const entry = {
    id: PROVIDER_ID,
    name: config.name ?? 'OSW',
    provider: config.provider,
    model: config.model,
    // The AI SDK rejects an empty key for some providers even when the endpoint needs
    // none. The proxy named by baseURL authenticates by the wiki session and ignores
    // this, and no real key is ever sent to the browser.
    apiKey: 'wiki-session',
    ...(config.baseURL ? { baseURL: config.baseURL } : {}),
    ...(config.parameters ? { parameters: config.parameters } : {})
  };

  const next = existing
    ? providers.map(p => (p?.id === PROVIDER_ID ? { ...p, ...entry } : p))
    : [...providers, entry];

  await settings.set('providers', next as any);

  // Only steer the default when the user has not chosen one. Overwriting a deliberate
  // choice on every page load would be the most annoying possible behaviour.
  const currentDefault = settings.get('defaultProvider').composite as string;
  if (!currentDefault || (existing && currentDefault === PROVIDER_ID)) {
    await settings.set('defaultProvider', PROVIDER_ID as any);
  }

  window.localStorage.setItem(VERSION_KEY, String(SETTINGS_VERSION));

  console.info(
    `osw-jupyternaut: applied the wiki's agent defaults (${config.provider}/${config.model})`
  );
}

/**
 * Make sure a command is on the agent's approval list.
 *
 * The agent checks `commandsRequiringApproval` before running a command, so this is what
 * puts a human in front of an action the model would otherwise take on its own. Added
 * rather than replaced: the defaults cover the notebook execution commands and dropping
 * them to add ours would quietly widen what the agent may do unattended.
 */
export async function requireApproval(
  registry: ISettingRegistry,
  command: string
): Promise<void> {
  const settings = await registry.load(SETTINGS_MODEL);
  const current = (settings.get('commandsRequiringApproval').composite as string[]) ?? [];
  if (current.includes(command)) {
    return;
  }
  await settings.set('commandsRequiringApproval', [...current, command] as any);
}
