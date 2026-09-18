---
name: confluence
description: Search, read, create and update Confluence Cloud pages via the REST API. Trigger on phrases like "confluence", "the wiki", "what does the wiki say", "create a confluence page", "update that page", "CQL", "publish this to confluence".
---

# Confluence pages

Search, read, create and update Confluence Cloud pages through the v2 REST API, authenticated with an API token. No MCP server.

This skill **reads, creates and updates**. It has no delete, no space administration and no permission change. Deleting a page is a human job in the Confluence UI.

## Prerequisites

Credentials are shared with `jira`. If `~/.dbhq/atlassian/config.json` does not exist, run:

```bash
${CLAUDE_SKILL_DIR}/../_shared/scripts/atlassian-setup.sh
```

It asks for the site URL, the account email and an API token from <https://id.atlassian.com/manage-profile/security/api-tokens>, verifies Jira access, then checks Confluence access (a warning, not a blocker - a token can be valid for Jira with no Confluence licence), and saves only after both checks have run.

## Before you write anything

**Read these two files first. Every time.** They are the difference between a page that uses the platform and a page that is a wall of bold text:

1. `${CLAUDE_SKILL_DIR}/../_shared/references/house-style.md` - the conventions
2. `${CLAUDE_SKILL_DIR}/../_shared/references/html-patterns.md` - the HTML+ patterns

And if it exists, read `~/.dbhq/atlassian/house-style.md` too. That is the user's own tone, column widths and site conventions, and it wins over anything in the shipped reference.

Work through `${CLAUDE_SKILL_DIR}/../_shared/references/checklist.md` against the body before you send it.

## Finding a page

```bash
${CLAUDE_SKILL_DIR}/scripts/confluence-search.sh spaces
${CLAUDE_SKILL_DIR}/scripts/confluence-search.sh text 'egress address'
${CLAUDE_SKILL_DIR}/scripts/confluence-search.sh cql 'space = DOCS and lastmodified >= now("-7d")'
```

CQL is the leverage. `space =`, `title ~`, `text ~`, `ancestor =`, `lastmodified >=`.

## Reading a page

A page id is always numeric - `read` refuses anything else (a URL, a title) with a clear error rather than a confusing one from deeper in the call chain.

```bash
${CLAUDE_SKILL_DIR}/scripts/confluence-pages.sh read 1234567                      # HTML+
${CLAUDE_SKILL_DIR}/scripts/confluence-pages.sh read 1234567 --format markdown    # to understand it
${CLAUDE_SKILL_DIR}/scripts/confluence-pages.sh read 1234567 --format adf         # the raw ADF, for when the converter and the API disagree
```

**`--format markdown` is one way.** A panel becomes a blockquote and a lozenge becomes bracketed text, because markdown has no syntax for either. Never edit that markdown and write it back - it would destroy every native component on the page. Editing goes through `--format html` (the default).

Every `read` prints a header naming the page's current version and the exact flag an update needs:

```
# page id 1234567, version 14 - pass --base-version 14 to update
```

That number is what you carry into `update` below.

## Creating a page

Write the body to a file as an HTML+ fragment, then:

```bash
${CLAUDE_SKILL_DIR}/scripts/confluence-pages.sh create \
  --space 98765 --title "Payment Correlation" \
  --parent 1234567 --body-file /tmp/body.html
```

`--space` takes the numeric space **id** from `confluence-search.sh spaces`, not the key. `--parent` is a page id and is validated the same way `read` and `update` validate theirs - anything non-numeric is refused before a request is built. A zero-byte or otherwise empty body file is refused before anything is sent - it would otherwise convert to a valid, empty document.

## Updating a page

**An update replaces the whole body. There is no partial edit, and `update` requires `--base-version <n>` - the version number your edit was composed against.** This is not a formality:

- Re-reading the page right before you write does **not**, by itself, protect anything. It only makes the version number correct at the instant of the write - which is exactly the check that a stale write needs to fail.
- `--base-version` is what makes the refusal possible. `update` re-reads the live page itself immediately before writing, and if the page's current version does not match the `--base-version` you passed, it refuses instead of overwriting whatever landed in between.
- Without it: you read version 5, spend ten minutes composing the change, someone else saves version 6 in the meantime, and you write version 7 - their edit is gone, with no error.

So:

1. **Read the page**, in `--format html`, and note the version number from the header (`... pass --base-version N to update`).
2. **Splice your change into what came back.** Preserve everything you are not deliberately changing: `data-colwidth` values, inline comment anchors, media ids and collections.
3. **Update, passing that same `--base-version`.**
4. **Verify by reading it again** and checking the section you changed.

```bash
${CLAUDE_SKILL_DIR}/scripts/confluence-pages.sh read 1234567 > /tmp/current.html
# note the version from the header, splice your change into /tmp/current.html
${CLAUDE_SKILL_DIR}/scripts/confluence-pages.sh update 1234567 \
  --body-file /tmp/current.html --base-version 14 --message "Added the egress address"
${CLAUDE_SKILL_DIR}/scripts/confluence-pages.sh read 1234567 | less
```

If the page moved on since your base version, `update` refuses:

```
Error: page 1234567 has moved on since your base version.
Cause: --base-version was 14; the page is now at version 15.
Fix: read the page again, splice your change into what comes back, and pass --base-version 15.
```

Do exactly that - read again, re-splice, update with the new version. Do not re-run the same command with the same body file; it was refused for a reason and nothing was sent.

If the page carries something this converter cannot reproduce exactly, `update` refuses the same way, before sending anything:

```
Error: page 1234567 ("Payment Correlation") cannot be safely edited through this skill.
Cause: a "table" node does not survive converting to HTML+ and back to ADF unchanged.
Fix: nothing was sent. This page carries something this converter cannot round-trip exactly - editing it here risks silently losing part of it that your change never touched.
```

This means the page has a node type, or an attribute on one, that this converter cannot carry through unchanged - writing would silently alter part of the page your edit never touched, not just the part you meant to change. **Re-reading will not fix this, and there is no bypass and no `--force`.** Stop, and make this edit directly in the Confluence UI instead.

**Never invent an opaque id.** `data-id`, `data-collection`, `data-media-id`, `data-resource-id` and inline-comment `data-annotation-id` all come from a fetch or from an upload step's output. A made-up one produces a broken node on a live page.

## What the converter does for you

`htmlplus.py` converts HTML+ to the ADF the API takes, and **rejects invalid nesting before anything is sent**, naming the element and its parent. If you see:

```
Error: A panel cannot contain a table. Close the panel and put the table after it as a sibling.
```

that is the converter, not the server. Nothing was sent. Fix the body and retry.

It also rejects a `data-colwidth` that is not a plain number, and a column where some cells carry the attribute and others do not - Confluence silently resets that whole table to even columns, which reads as a formatting regression to everyone who sees the diff.

## Constraints

- **No delete.** Not for a page, not for a space, not for an attachment.
- **`update` always needs `--base-version`.** Read the page, take the version from its header, splice, update with that version - see [§ Updating a page](#updating-a-page).
- **HTML+ in, never markdown and never storage format.** Markdown flattens panels, lozenges, tasks and column widths into bold text.
- **Never invent an opaque id.**
- **A page id is numeric.** Anything else is refused before a request is built.
