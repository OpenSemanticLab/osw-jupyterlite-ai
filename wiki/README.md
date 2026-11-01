# Wiki content

Definitions that have to exist **on** the wiki, not in the site build.

## `Category:Skill`

`Category:OSWc4358938e1f54d8da084c028dfb8c22f`, a subclass of `Category:Tutorial`
(`Category:OSW494f660e6a714a1a9681c517bbb975da`). It makes an assistant skill an ordinary
wiki entity: authored in the editor, versioned, discussed, and subject to the same rights
as anything else.

Subclassing Tutorial is not decoration. A skill *is* an instruction in written form, which
is what Tutorial means here (`instance_rdf_type: schema:HowTo`), and it inherits
Tutorial's `required_predecessor` / `recommended_successor` properties - a sensible way to
say "read that skill first". The consequence to remember when querying: the dev wiki has
50 Tutorials that are **not** Skills, so `discover_skills` has to ask for the subclass,
never for Tutorial.

### Shape

| where | what |
| --- | --- |
| `main` slot (page body) | the instructions - the markdown an assistant reads |
| `jsondata.skill_name` | the slug the assistant calls it by, e.g. `osl-page-editing` |
| `jsondata.label` / `description` | name and one-line summary, from the base entity schema |
| `jsondata.applies_to` | `osw-chatbot`, `jupyternaut`, or empty for both |
| `jsondata.resources` | files the instructions refer to |

The body holds the instructions rather than a JSON property, so the mapping to the
`SKILL.md` format both assistants already speak is close to identity - frontmatter from
`jsondata`, body from the page - and so that editing a skill produces a readable diff
instead of one very long changed line.

`label` and `description` are the entity's own, inherited from the base schema; only the
slug, the audience and the resource list are new. `description` is what every assistant
sees for every skill on every turn, so keep it to one line that says *when* to reach for
it.

### Installing it

Needs an account with rights to create a Category, so it is not done by the build. The
sandbox is the easiest place to run it, because there it runs as you with no credentials
to configure:

```python
import osw_bootstrap
await osw_bootstrap.setup()
wtsite = osw_bootstrap.wtsite()

import json, requests
base = "https://raw.githubusercontent.com/OpenSemanticLab/osw-jupyterlite-ai/main/wiki/Category_Skill"
page = wtsite.get_page(
    wtsite.GetPageParam(titles=["Category:OSWc4358938e1f54d8da084c028dfb8c22f"])
).pages[0]
for slot in ("jsondata", "jsonschema"):
    page.set_slot_content(slot, requests.get(f"{base}/slot_{slot}.json").json())
page.edit("Create Category:Skill")
```

Verify afterwards that the category page renders a form - if the `allOf` `$ref` to Tutorial
cannot be resolved, the editor shows a bare object rather than an error.
