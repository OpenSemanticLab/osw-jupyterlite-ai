import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { ISettingRegistry } from '@jupyterlab/settingregistry';

import { sanitizeLocation } from './from-url';
import { applyWikiDefaults, fetchWikiConfig } from './settings';
import { currentUser } from './wiki';

/**
 * Strip disallowed `?fromURL=` values before anything acts on them.
 *
 * Separate from the configuration plugin, and requiring nothing, so it is activated as
 * early as the application allows: jupyterlab-open-url-parameter reads the query string
 * on its own activation, and a sanitiser that ran afterwards would be decoration.
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
 * Optional dependency on the setting registry: without it the agent still runs, the user
 * just configures the provider themselves, which is better than refusing to start.
 */
const wikiConfig: JupyterFrontEndPlugin<void> = {
  id: 'osw-jupyternaut:wiki-config',
  description: "Apply the wiki's default agent configuration",
  autoStart: true,
  optional: [ISettingRegistry],
  activate: async (_app: JupyterFrontEnd, settingRegistry: ISettingRegistry | null) => {
    const user = await currentUser();
    if (user) {
      console.info(`osw-jupyternaut: the wiki sees you as ${user}`);
    } else {
      // Worth saying out loud: everything osw-python does here runs as the wiki user, so
      // "nobody" explains in advance why reads and writes will be refused.
      console.info('osw-jupyternaut: not logged in - the kernel has no wiki rights');
    }

    if (!settingRegistry) {
      return;
    }
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

export default [fromUrlGuard, wikiConfig];
