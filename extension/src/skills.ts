/**
 * Make the wiki's skill pages available to the agent as skills.
 *
 * Jupyternaut's skills are normally folders of `SKILL.md` on the contents drive, which in
 * a static site means they are fixed at build time. Instances of `Category:Skill` are
 * ordinary wiki pages instead: authored in the wiki editor, versioned, discussed, and
 * subject to the wiki's own rights. A skill written by one person is immediately
 * available to everyone else's sandbox, and to the wiki chat assistant, without a rebuild.
 *
 * The mapping is close to identity, which is why the category stores it this way:
 *
 *     SKILL.md frontmatter `name`  <- jsondata.skill_name
 *     SKILL.md frontmatter `description` <- jsondata.description
 *     SKILL.md body                <- the page's main slot
 *
 * `ISkillRegistration.instructions` is a plain string, so every body is fetched once at
 * startup rather than on demand. That is fine, and not a betrayal of the registry's
 * progressive disclosure: what the design keeps small is the *prompt*, where only names
 * and descriptions appear until the agent calls `load_skill`. Bodies are fetched in the
 * same batched query as the metadata, so fifty skills cost two requests, not fifty.
 */

import { ISkillRegistry } from '@jupyternaut/agent';

import { apiGet, apiPost, scriptPath } from './wiki';

/**
 * How skills are found. A skill is an ordinary wiki page carrying the "AI Skill"
 * keyword, so a Tutorial, an LLM-Skill or a plain Article can all be one without being
 * moved.
 *
 * The chat assistant has its own setting with the same default, so a deployment can
 * narrow one surface without disturbing the other. Both defaults are the same string on
 * purpose; change them together.
 */
export const AI_SKILL_KEYWORD = 'Item:OSW1dd1c2ee927343ca9097a45a0af21678';

export const DEFAULT_SKILL_QUERY = `[[HasKeyword::${AI_SKILL_KEYWORD}]]`;

/** "OpenSemanticLab for developers", itself part of the OpenSemanticLab Manual. */
export const DEVELOPERS_ARTICLE = 'Item:OSWe0cbdc9c1e0e4a53b21bd83e6c6d99ad';

/** Category a skill page is an instance of. Broad: the keyword is the discriminator. */
export const DEFAULT_SKILL_CATEGORY =
  'Category:OSW92cc6b1a2e6b4bb7bad470dfdcfdaf26';

/** Which assistant we are, matched against a skill's `applies_to`. */
export const AUDIENCE = 'jupyternaut';

interface ISkillPage {
  title: string;
  name: string;
  description: string;
  instructions: string;
  resources: string[];
}

function textOf(value: unknown): string {
  // OSW stores labels and descriptions as a list of {text, lang}. English when present,
  // otherwise whatever there is - a German-only description is far better than none.
  if (typeof value === 'string') {
    return value;
  }
  if (Array.isArray(value)) {
    const english = value.find(entry => entry?.lang === 'en');
    return (english ?? value[0])?.text ?? '';
  }
  return '';
}

/** Every page the discovery query matches, with the fields needed for the index. */
export async function listSkills(
  query: string = DEFAULT_SKILL_QUERY
): Promise<ISkillPage[]> {
  // An SMW ask, not a category listing: membership of a category is not what makes a
  // page a skill - the keyword is, and a keyword can be put on a Tutorial, an LLM-Skill
  // or a plain Article without moving it.
  const listing = await apiGet({
    action: 'ask',
    query: `${query}|limit=500`
  });
  const members: { title: string }[] = Object.keys(
    listing?.query?.results ?? {}
  ).map(title => ({ title }));
  if (members.length === 0) {
    return [];
  }

  const skills: ISkillPage[] = [];
  // Batched by 50, the API's own limit for titles. One request per skill would be dozens
  // of round trips before the agent has said anything.
  for (let i = 0; i < members.length; i += 50) {
    const batch = members.slice(i, i + 50).map(m => m.title);
    const body = await apiGet({
      action: 'query',
      prop: 'revisions',
      rvprop: 'content',
      // Both slots in one request: the registry wants the instructions as a string, so
      // fetching metadata now and bodies later would double the round trips for nothing.
      rvslots: 'jsondata|main',
      titles: batch.join('|')
    });
    for (const page of body?.query?.pages ?? []) {
      const slots = page?.revisions?.[0]?.slots ?? {};
      const raw = slots.jsondata?.content;
      if (!raw) {
        continue;
      }
      let data: any;
      try {
        data = JSON.parse(raw);
      } catch {
        console.warn(`osw-jupyternaut: ${page.title} has unparseable jsondata, skipping`);
        continue;
      }
      const applies: string[] = Array.isArray(data.applies_to) ? data.applies_to : [];
      if (applies.length > 0 && !applies.includes(AUDIENCE)) {
        continue;
      }
      const name = data.skill_name || data.name;
      if (!name) {
        continue;
      }
      skills.push({
        title: page.title,
        name,
        description: textOf(data.description) || textOf(data.label),
        instructions: slots.main?.content ?? '',
        resources: Array.isArray(data.resources) ? data.resources : []
      });
    }
  }
  return skills;
}

/**
 * Create or update a skill page.
 *
 * Runs as the logged-in user over the same session everything else uses, so it cannot
 * write anything that user could not write by hand - and it is attributed to them in the
 * page history, which is the point of putting skills in the wiki at all.
 */
export async function saveSkill(options: {
  title?: string;
  name: string;
  description: string;
  instructions: string;
  summary?: string;
}): Promise<string> {
  const title = options.title ?? `Item:OSW${crypto.randomUUID().replace(/-/g, '')}`;
  const jsondata = {
    type: [DEFAULT_SKILL_CATEGORY],
    // Without the keyword the page is a plain Article and the discovery query - which
    // matches on the keyword, not the category - would never return what we just wrote.
    keywords: [AI_SKILL_KEYWORD],
    part_of: [DEVELOPERS_ARTICLE],
    skill_name: options.name,
    label: [{ text: options.name, lang: 'en' }],
    description: [{ text: options.description, lang: 'en' }]
  };

  // Two edits, because MediaWiki's action=edit writes one slot per request. jsondata
  // first: a page that exists with its data but no body reads as an empty skill, whereas
  // a body with no data is not in the category at all and would vanish from discovery.
  await apiPost({
    action: 'edit',
    title,
    slot: 'jsondata',
    text: JSON.stringify(jsondata, null, 2),
    summary: options.summary ?? `Skill ${options.name} (assistant)`
  });
  await apiPost({
    action: 'edit',
    title,
    slot: 'main',
    text: options.instructions,
    summary: options.summary ?? `Skill ${options.name} (assistant)`
  });
  return title;
}

/** Register every wiki skill with the agent. Never throws: a broken wiki must not stop the app. */
export async function registerWikiSkills(
  registry: ISkillRegistry,
  query?: string
): Promise<number> {
  let skills: ISkillPage[];
  try {
    // Skills found here are *added* to whatever Jupyternaut already holds - the registry
    // is never cleared. The chat assistant replaces its bundled set instead; the sandbox
    // extends, because its built-ins are about using notebooks rather than about this
    // wiki's conventions and both are worth having.
    skills = await listSkills(query ?? DEFAULT_SKILL_QUERY);
  } catch (reason) {
    console.info('osw-jupyternaut: no wiki skills available', reason);
    return 0;
  }

  for (const skill of skills) {
    try {
      registry.registerSkill({
        name: skill.name,
        description: skill.description,
        instructions: skill.instructions,
        resources: skill.resources,
        loadResource: async (resource: string) => {
          const url = `${scriptPath()}/index.php?title=${encodeURIComponent(resource)}&action=raw`;
          const response = await fetch(url, { credentials: 'same-origin' });
          if (!response.ok) {
            // `error` rather than a throw: the agent shows it to the user and carries on
            // with the rest of the skill, which beats losing the skill over one file.
            return {
              name: skill.name,
              resource,
              error: `${resource} responded ${response.status}`
            };
          }
          return { name: skill.name, resource, content: await response.text() };
        }
      });
    } catch (reason) {
      console.warn(`osw-jupyternaut: could not register skill ${skill.name}`, reason);
    }
  }
  if (skills.length > 0) {
    console.info(
      `osw-jupyternaut: registered ${skills.length} wiki skill(s): ` +
        skills.map(s => s.name).join(', ')
    );
  }
  return skills.length;
}
