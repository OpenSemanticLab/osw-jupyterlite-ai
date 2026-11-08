# Wiki content

This flavour defines no wiki content. Skills are ordinary wiki pages, built from
vocabulary the wiki already has.

## Skills are ordinary wiki pages

| | |
| --- | --- |
| `Category:Article` `OSW92cc6b1a2e6b4bb7bad470dfdcfdaf26` | what a skill page is an instance of. Deliberately broad - `Tutorial` and `LLM-Skill` both sit under it. |
| `Item:OSW1dd1c2ee927343ca9097a45a0af21678` | the **AI Skill** keyword. Attached with `Property:HasKeyword`, which the wiki already used. |
| `Item:OSWe0cbdc9c1e0e4a53b21bd83e6c6d99ad` | "OpenSemanticLab for developers", referenced by `part_of`, itself part of the OpenSemanticLab Manual. |

**The keyword is what makes a page a skill**, not the category. That is why discovery is an
SMW ask over `[[HasKeyword::…]]` rather than a category listing: a keyword can be put on a
Tutorial, an LLM-Skill or a plain Article without moving the page.

`Property:HasRequiredPredecessor` chains prerequisites, and real content already uses it -
*OSW Python Package* requires *JSON-SCHEMA Tutorial*.

## Installing the vocabulary

A wiki needs the `world.opensemantic.base` and `world.opensemantic.meta.docs` page
packages for any of the above to exist. Without them `save_skill` refuses rather than
writing a page typed against a category this wiki does not have, and the defaults that
name missing pages are dropped instead of written as dangling references.

Both `DEFAULT_SKILL_QUERY` in `extension/src/skills.ts` and `SKILL_DISCOVERY_QUERY` in
osw-chatbot's `config.py` default to the same query. A deployment can narrow either on its
own; they are separate settings so the sandbox and the chat assistant can differ.
