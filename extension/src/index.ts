import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { ISettingRegistry } from '@jupyterlab/settingregistry';
import { ISkillRegistry } from '@jupyternaut/agent';

import { bridge, saveNotebookToWiki } from './bridge';
import { sanitizeLocation } from './from-url';
import {
  SETTINGS_MODEL,
  applyWikiDefaults,
  fetchWikiConfig,
  requireApproval
} from './settings';
import { listSkills, registerWikiSkills, saveSkill } from './skills';
import { currentUser } from './wiki';

export const SAVE_SKILL_COMMAND = 'osw-jupyternaut:save-skill';

export const SAVE_NOTEBOOK_COMMAND = 'osw-jupyternaut:save-notebook-to-wiki';
export const DESCRIBE_COMMAND = 'osw-jupyternaut:describe';
export const REFRESH_SKILLS_COMMAND = 'osw-jupyternaut:refresh-skills';

/**
 * Strip disallowed `?fromURL=` values before anything acts on them.
 *
 * Separate from the configuration plugin, and requiring nothing, so it activates as early
 * as the application allows: jupyterlab-open-url-parameter reads the query string on its
 * own activation, and a sanitiser that ran afterwards would be decoration.
 */
const fromUrlGuard: JupyterFrontEndPlugin<void> = {
  id: 'osw-jupyternaut:from-url-guard',
  description: "Restrict ?fromURL= to this wiki's own uploaded files",
  autoStart: true,
  activate: () => {
    const rejected = sanitizeLocation();
    if (rejected.length > 0) {
      console.warn(
        `osw-jupyternaut: dropped ${rejected.length} disallowed fromURL parameter(s)`
      );
    }
  }
};

/**
 * Take the agent's provider configuration from the wiki rather than from the build.
 *
 * Optional dependency on the setting registry: without it the agent still runs and the
 * user configures the provider themselves, which beats refusing to start.
 */
const wikiConfig: JupyterFrontEndPlugin<void> = {
  id: 'osw-jupyternaut:wiki-config',
  description: "Apply the wiki's default agent configuration",
  autoStart: true,
  optional: [ISettingRegistry],
  activate: async (app: JupyterFrontEnd, settingRegistry: ISettingRegistry | null) => {
    const user = await currentUser();
    if (user) {
      console.info(`osw-jupyternaut: the wiki sees you as ${user}`);
    } else {
      // Worth saying out loud: everything here runs as the wiki user, so "nobody"
      // explains in advance why reads and writes are about to be refused.
      console.info('osw-jupyternaut: not logged in - the kernel has no wiki rights');
    }

    if (!settingRegistry) {
      return;
    }
    try {
      // A write to the wiki should not happen because a model decided it should. The
      // agent checks this list before running a command, so adding ours here is what
      // puts a human in front of every skill edit.
      await requireApproval(settingRegistry, SAVE_SKILL_COMMAND);
    } catch (reason) {
      console.warn('osw-jupyternaut: could not mark save-skill as needing approval', reason);
    }

    // Everything that decides whether the agent can start, readable from one place.
    //
    // Not only for tests: JupyterLite persists user settings to IndexedDB through a
    // connector that `serviceManager.settings.fetch` does not read, so there is no way
    // to see the *resolved* configuration from outside the application. Without this, a
    // user reporting "configure your AI settings first" can only be asked to describe a
    // settings panel. The base project ships the same thing for the same reason.
    app.commands.addCommand(DESCRIBE_COMMAND, {
      label: 'Describe the OSW agent configuration',
      caption:
        'The resolved provider, model, proxy url and token state the agent will use.',
      execute: async () => {
        const settings = await settingRegistry.load(SETTINGS_MODEL);
        const providers = (settings.get('providers').composite as any[]) ?? [];
        const entry = providers.find(p => p?.id === 'osw');
        return {
          useSecretsManager: settings.get('useSecretsManager').composite,
          defaultProvider: settings.get('defaultProvider').composite,
          provider: entry
            ? {
                id: entry.id,
                provider: entry.provider,
                model: entry.model,
                baseURL: entry.baseURL,
                // Never the token itself - this is pasted into bug reports.
                apiKey: entry.apiKey ? 'present' : 'missing'
              }
            : null,
          providerCount: providers.length
        };
      }
    });

    const config = await fetchWikiConfig();
    if (!config?.settings) {
      return;
    }
    try {
      await applyWikiDefaults(settingRegistry, config.settings);
    } catch (reason) {
      console.warn('osw-jupyternaut: could not apply the wiki agent defaults', reason);
    }
  }
};

/**
 * Make `Category:Skill` pages available to the agent, and let it write them back.
 *
 * Writing is a JupyterLab *command* rather than a tool registered on IToolRegistry.
 * Jupyternaut already exposes commands to the model through `discover_commands` and
 * `execute_command`, so this is one registration instead of a dependency on the AI SDK's
 * tool types - and bundling a second copy of that SDK is exactly the kind of duplicate
 * that makes shared singletons stop matching.
 */
const wikiSkills: JupyterFrontEndPlugin<void> = {
  id: 'osw-jupyternaut:wiki-skills',
  description: 'Expose Category:Skill pages as agent skills',
  autoStart: true,
  requires: [ISkillRegistry],
  activate: async (app: JupyterFrontEnd, skills: ISkillRegistry) => {
    // The wiki may narrow which pages count as skills here without affecting the chat
    // assistant, which has its own setting. Both default to the same query.
    const config = await fetchWikiConfig();
    const raw = config?.settings?.skillDiscoveryQuery;
    const query = typeof raw === "string" && raw.trim() ? raw.trim() : undefined;
    await registerWikiSkills(skills, query);

    app.commands.addCommand(REFRESH_SKILLS_COMMAND, {
      label: 'Reload skills from the wiki',
      caption:
        'Re-read Category:Skill from the wiki. Use after writing or editing a skill so ' +
        'it is available without reloading the page.',
      execute: async () => {
        const count = await registerWikiSkills(skills, query);
        return { registered: count };
      }
    });

    app.commands.addCommand(SAVE_SKILL_COMMAND, {
      label: 'Save a skill to the wiki',
      caption:
        'Create or update an instance of Category:Skill. Writes to the wiki as the ' +
        'logged-in user. Arguments: name (slug), description (one line), instructions ' +
        '(markdown), and optionally title to update an existing page.',
      execute: async args => {
        const name = String(args?.name ?? '').trim();
        const description = String(args?.description ?? '').trim();
        const instructions = String(args?.instructions ?? '').trim();
        if (!name || !description || !instructions) {
          // Returned rather than thrown: the agent reads this back and can correct
          // itself, where an exception would surface as a tool failure with no guidance.
          return {
            error:
              'name, description and instructions are all required. name is a slug like ' +
              "'osl-page-editing'; description is the single line every assistant sees " +
              'for this skill on every turn, so say when to reach for it.'
          };
        }
        if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(name)) {
          return { error: `'${name}' is not a slug - use lower case and hyphens` };
        }
        try {
          const title = await saveSkill({
            title: args?.title ? String(args.title) : undefined,
            name,
            description,
            instructions
          });
          // Re-registered immediately so the skill the agent just wrote is usable in the
          // same conversation rather than after a reload.
          await registerWikiSkills(skills);
          return { title, name };
        } catch (reason) {
          return { error: String(reason) };
        }
      }
    });

    // A command, not an IToolRegistry tool, for the same reason as the skill writers:
    // Jupyternaut reaches commands through discover_commands/execute_command, so this
    // one registration serves the in-lab agent, and the host bridge calls the same
    // function for the chat assistant.
    app.commands.addCommand(SAVE_NOTEBOOK_COMMAND, {
      label: 'Save this notebook to the wiki',
      caption:
        'Upload the open notebook as a WikiFile so it outlives this browser. The ' +
        'sandbox keeps notebooks in browser storage, which does not survive clearing ' +
        'site data. Pass a readable label; the page itself is named by OSW-ID.',
      execute: async (commandArgs: any) =>
        saveNotebookToWiki(
          app,
          String(commandArgs?.notebookPath ?? ''),
          String(commandArgs?.label ?? ''),
          String(commandArgs?.comment ?? '')
        )
    });

    app.commands.addCommand('osw-jupyternaut:list-skills', {
      label: 'List the wiki skills',
      caption: 'Names and descriptions of every Category:Skill page this wiki holds.',
      execute: async () => ({ skills: await listSkills() })
    });
  }
};

export default [fromUrlGuard, wikiConfig, wikiSkills, bridge];
