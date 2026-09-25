# Confluence pages

Search, read, create and update Confluence Cloud pages through the v2 REST API. Credentials, setup and the rules are in `SKILL.md`.

## Before you write anything

Read `${CLAUDE_SKILL_DIR}/references/house-style.md` and `${CLAUDE_SKILL_DIR}/references/html-patterns.md` every time, then `~/.dbhq/atlassian/house-style.md` if it exists. The user's file wins. Work through `${CLAUDE_SKILL_DIR}/references/checklist.md` against the body before you send it.

## Finding a page

```bash
${CLAUDE_SKILL_DIR}/scripts/confluence-search.sh spaces
${CLAUDE_SKILL_DIR}/scripts/confluence-search.sh text 'egress address'
${CLAUDE_SKILL_DIR}/scripts/confluence-search.sh cql 'space = DOCS and lastmodified >= now("-7d")'
```

CQL is the leverage. `space =`, `title ~`, `text ~`, `ancestor =`, `lastmodified >=`.

Every search here fetches one page, up to its `[limit]` (`spaces` defaults to 50, `text`/`cql` to 25). Confluence's own pagination is cursor-based, not a total count, but the cursor's presence still says when there is more - and when it does, the command says so rather than let a truncated result set pass as complete. Raise the limit or narrow the query.

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

## Editing one block

**To change one part of a page, use `edit`, not `update`.** It converts only your fragment and splices it into the live page by local id. Every other node on the page is carried through as it came, without passing through the converter - a macro, an inline comment, anything this converter has no HTML+ for.

1. **Read the page** in `--format html`. Note the version from the header, and the `data-local-id` of the block you want to change, or the block your new content goes after.
2. **Write the fragment** - the new block or blocks, as HTML+.
3. **Dry-run it**, then run it for real with the same `--base-version`.

```bash
${CLAUDE_SKILL_DIR}/scripts/confluence-pages.sh read 1234567 > /tmp/current.html
# note the version and the data-local-id of the block to change
${CLAUDE_SKILL_DIR}/scripts/confluence-pages.sh edit 1234567 --base-version 14 \
  --replace 5f1c2a9e --body-file /tmp/fragment.html --dry-run
${CLAUDE_SKILL_DIR}/scripts/confluence-pages.sh edit 1234567 --base-version 14 \
  --replace 5f1c2a9e --body-file /tmp/fragment.html --message "Corrected the egress address"
```

- `--replace <local-id>` puts the fragment in place of that block.
- `--insert-after <local-id>` puts it straight after that block, in the same container.
- `--append` adds it at the end of the page.

The fragment is checked where it will land: a table after a list item is refused, because a list item cannot hold one. An expand after a block inside a table cell becomes a nested expand.

`edit` targets a block. A local id on an inline node - a lozenge, a date, an inline card - is refused; use the id of the paragraph it sits in. So is a table row or cell, or a layout column; target a block inside it, or the whole table or layout.

**The round-trip gate only checks the block being replaced**, so `edit` works on a page `update` refuses. `--insert-after` and `--append` replace nothing, so they check nothing. `--base-version` works exactly as it does for `update`, below.

`--dry-run` sends nothing. It prints the top-level block count before and after, and every node the write would remove: each node whose local id would be gone, and each node with no named HTML+ form that would no longer be there.

## Updating a page

**An update replaces the whole body, and `update` requires `--base-version <n>` - the version number your edit was composed against.** Use it to rewrite a page; use `edit` above to change part of one. `--dry-run` works the same way as `edit`'s: it sends nothing and names what the new body would remove. The `--base-version` rule is not a formality:

- Re-reading the page right before you write does **not**, by itself, protect anything. It only makes the version number correct at the instant of the write - which is exactly the check that a stale write needs to fail.
- `--base-version` is what makes the refusal possible. `update` re-reads the live page itself immediately before writing, and if the page's current version does not match the `--base-version` you passed, it refuses instead of overwriting whatever landed in between.
- Without it: you read version 5, spend ten minutes composing the change, someone else saves version 6 in the meantime, and you write version 7 - their edit is gone, with no error.

So:

1. **Read the page**, in `--format html`, and note the version number from the header (`... pass --base-version N to update`). The header prints on stderr, so it still shows on your terminal even while `> /tmp/current.html` sends the body itself to the file - the redirect captures a clean fragment, not the header lines ahead of it.
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
Fix: nothing was sent. This page carries something this converter cannot round-trip exactly - editing it here risks silently losing part of it that your change never touched. To change one block, use edit with that block's local id.
```

This means the page has a node type, or an attribute on one, that this converter cannot carry through unchanged - writing would silently alter part of the page your edit never touched, not just the part you meant to change. **Re-reading will not fix this, and there is no bypass and no `--force`.** If your change is to one block, use `edit` - it only checks that block. Otherwise make the change in the Confluence UI.

To see how often this refuses on the user's site, run `audit` (below). It reads, and writes nothing.

**Never invent an opaque id.** `data-id` and `data-collection` on a media node come from a fetch or from an upload step's output. A made-up one produces a broken node on a live page. An inline comment anchor - Confluence's annotation mark - and anything else this converter has no named HTML+ for arrive as `adf-opaque`/`adf-opaque-mark` (see `html-patterns.md`); copy that element through unchanged rather than inventing one.

## Measuring the gate

```bash
${CLAUDE_SKILL_DIR}/scripts/confluence-pages.sh audit --cql 'space = DOCS and type = page' --limit 100
```

`audit` runs the round-trip gate over the pages a CQL query finds, up to `--limit` (default 25). It reports how many pages would pass, the pass rate for each node type, and each page it would refuse, with the node nearest the difference. It only reads. Use it to find out whether `update` or `edit` suits the pages in a space.

## What the converter does for you

`htmlplus.py` converts HTML+ to the ADF the API takes, and **rejects invalid nesting before anything is sent**, naming the element and its parent. If you see:

```
Error: A panel cannot contain a table. Close the panel and put the table after it as a sibling.
```

that is the converter, not the server. Nothing was sent. Fix the body and retry.

The nesting rules are Atlassian's own published ADF schema, which ships with the skill. An attribute an element does not take - a misspelt `data-colour`, say - is refused by name rather than dropped.

It also rejects a `data-colwidth` that is not a plain number, and a column where some cells carry the attribute and others do not - Confluence silently resets that whole table to even columns, which reads as a formatting regression to everyone who sees the diff.
