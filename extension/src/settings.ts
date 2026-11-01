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
 * The one entry this module owns is re-asserted on every start, so changing the model or
 * the proxy url on the wiki reaches browsers that have already used the sandbox. Anything
 * else in the settings - providers the user added, which one they made default - is left
 * alone.
 */

import { ISettingRegistry } from '@jupyterlab/settingregistry';

import { apiGet } from './wiki';

/** Jupyternaut's settings plugin. Moves between releases; pinned here deliberately. */
export const SETTINGS_MODEL = '@jupyternaut/persona:settings-model';

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

/**
 * A short-lived token identifying this user to the LLM proxy.
 *
 * Minted by the Chatbot extension's `action=chatbottoken`: HMAC-signed with the wiki's
 * shared secret, carrying the username, an expiry and the action list. Reused rather than
 * invented - the proxy already verifies exactly this, and a second token format would be
 * a second thing to get wrong.
 *
 * Null when the wiki issues none: the extension may be absent, the secret unset, or the
 * reader may lack `chatbot-use`. The agent then has no working provider, which is the
 * correct outcome and shows up as a 401 rather than as silence.
 */
export async function fetchAgentToken(): Promise<string | null> {
  try {
    const body = await apiGet({ action: 'chatbottoken' });
    return body?.chatbottoken?.token ?? null;
  } catch (reason) {
    console.info('osw-jupyternaut: the wiki issued no agent token', reason);
    return null;
  }
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

  // Jupyternaut reads a provider's apiKey from jupyter-secrets-manager while this is on -
  // which it is by default - so the token written into settings is ignored, the model is
  // never built, and the chat reports "configure your AI settings first" even though the
  // provider is plainly listed in the picker.
  if (settings.get('useSecretsManager').composite !== false) {
    await settings.set('useSecretsManager', false as any);
    console.info('osw-jupyternaut: disabled the secrets manager so the wiki token is used');
  }

  // The wiki mints a short-lived token identifying this user; the proxy named by baseURL
  // verifies it and adds the real vendor key on the far side. No key reaches the browser,
  // which is what makes a static site safe to configure this way.
  const token = await fetchAgentToken();

  const providers = (settings.get('providers').composite as any[]) ?? [];
  const existing = providers.find(entry => entry?.id === PROVIDER_ID);

  const entry = {
    id: PROVIDER_ID,
    name: config.name ?? 'OSW',
    provider: config.provider,
    model: config.model,
    // A placeholder when the wiki issues no token: the AI SDK refuses an empty key, and
    // failing at the provider with a clear 401 beats failing here with a config error.
    apiKey: token ?? 'no-wiki-token',
    ...(config.baseURL ? { baseURL: config.baseURL } : {}),
    ...(config.parameters ? { parameters: config.parameters } : {})
  };

  // This entry belongs to the wiki, and is re-asserted on every start rather than
  // written once.
  //
  // It used to be written once and then left alone, guarded by a version marker in
  // localStorage, on the reasoning that a user may have edited it and their choice
  // should win. That was wrong twice over: a browser that had taken an earlier version
  // kept a stale baseURL and a stale token and simply stopped working, and the only way
  // out was for the user to find and clear a setting they never set. Changing the model
  // on the wiki has to reach people who have already used the sandbox - they are exactly
  // the people who will notice.
  //
  // What a user *does* keep: providers they added themselves, and their choice of
  // default. Only this one entry is owned here.
  const unchanged =
    existing && Object.entries(entry).every(([k, v]) => (existing as any)[k] === v);

  if (!unchanged) {
    const next = existing
      ? providers.map(p => (p?.id === PROVIDER_ID ? { ...p, ...entry } : p))
      : [...providers, entry];
    await settings.set('providers', next as any);
  }

  // Only steer the default when the user has not chosen one, or already has ours.
  // Overwriting a deliberate choice on every page load would be the most annoying
  // possible behaviour.
  const currentDefault = settings.get('defaultProvider').composite as string;
  if (!currentDefault || currentDefault === PROVIDER_ID) {
    if (currentDefault !== PROVIDER_ID) {
      await settings.set('defaultProvider', PROVIDER_ID as any);
    }
  }

  // Everything that decides whether the agent can start, in one line. When a user reports
  // "configure your AI settings first" this says which part is missing without asking
  // them to go digging in a settings panel.
  console.info(
    `osw-jupyternaut: applied the wiki's agent defaults (${config.provider}/${config.model})`,
    {
      baseURL: config.baseURL ?? '(none)',
      token: token ? 'present' : 'MISSING - the wiki issued none',
      secretsManager: settings.get('useSecretsManager').composite,
      defaultProvider: settings.get('defaultProvider').composite,
      changed: !unchanged
    }
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
