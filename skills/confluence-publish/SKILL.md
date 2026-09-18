---
name: confluence-publish
description: Publish a repository's markdown files to Confluence Cloud pages, idempotently, with the page id stored in each file's frontmatter. Trigger on phrases like "publish this to confluence", "push the docs to the wiki", "sync these docs to confluence", "publish the spec".
---

# Publishing markdown to Confluence

Turns a markdown file in a repository into a Confluence page, and keeps it that way. The file is the master; Confluence is the rendering.

This skill **creates and updates only**. It has no delete verb anywhere - not for a page, not for an attachment. Removing something is a human job in the Confluence UI.

## Prerequisites

Credentials are shared with `jira` and `confluence`. If `~/.dbhq/atlassian/config.json` does not exist, run `${CLAUDE_SKILL_DIR}/../_shared/scripts/atlassian-setup.sh`.

## The binding

Each file carries its own page id in YAML frontmatter:

```yaml
---
confluence:
  space: "98765"
  parent: "1234567"
  page_id: "8901234"
---
```

`page_id` is written back on the first publish. **Commit that change** - without it the next run creates a second page instead of updating the first.

Get the space id from `${CLAUDE_SKILL_DIR}/../confluence/scripts/confluence-search.sh spaces`. It is the numeric id, not the key.

## Publishing

```bash
${CLAUDE_SKILL_DIR}/scripts/publish.sh docs/payment-correlation.md --dry-run
${CLAUDE_SKILL_DIR}/scripts/publish.sh docs/payment-correlation.md
```

**Always dry-run first.** It converts the whole body and reports what would happen. A `--dry-run` against a file with no `page_id` yet is pure local conversion and needs no credentials at all. A `--dry-run` against a file that already carries a `page_id` also makes one read-only call to the live page, to preview the round-trip gate below before you run for real - see [§ Updating an existing page](#updating-an-existing-page). Neither ever writes.

The page title is the file's first `# ` heading, or the filename if there is none.

## Updating an existing page

`publish.sh` updates through `confluence-pages.sh update`, which carries two protections you do not have to think about but should know are there, because both can stop a publish with nothing sent.

**`update` always needs `--base-version <n>` - `publish.sh` supplies it for you, read fresh immediately before the write.** The file is the master, so there is normally nothing to "base" an edit on the way a human splicing a change into a fetched copy would - but the guard still catches a real case: someone editing the live page directly at the exact moment `publish.sh` runs. If that happens, `update` refuses rather than silently discarding their in-flight change, and reports it as the page having moved on since the version `publish.sh` just read. **The fix is simply to run `publish.sh` again** - there is nothing to splice, because the file on disk already is the whole intended content, unlike the human workflow the `confluence` skill documents.

**`update` also runs a round-trip gate, and it refuses often - across a real set of 289 pages, roughly 60% of the time.** It refuses to overwrite a page whose current content this converter cannot read back unchanged - typically because someone edited that page directly in the Confluence editor at some point, and the editor wrote a node, an attribute, or a mark this converter has no HTML+ form for. `UPDATE REPLACES THE WHOLE BODY`, so writing over content like that would silently drop whatever this converter cannot carry through, not just the part `publish.sh` meant to change. This is expected, ordinary behaviour on a page with any editing history outside this pipeline, not a sign anything is broken:

```
Publish stopped: docs/payment-correlation.md was not sent to page 8901234. Nothing changed. See the error above.
If the page moved on since this run started, just run publish.sh again - the file is the master, so there is nothing to splice by hand.
If the converter refused the round trip, there is no --force: resolve it directly in the Confluence UI, then re-run.
```

**There is no `--force` anywhere in this skill family, and `publish.sh` adds none.** A round-trip refusal needs a human decision in the Confluence UI, not a flag. Run `--dry-run` first and it previews this exact check against the page as it stands right now, so the refusal is not a surprise on the real run - though the page can still change between the preview and the write, so treat the preview as "likely", not certain.

## What markdown can and cannot express

Headings, paragraphs, lists, tables, links, inline marks and fenced code blocks convert cleanly. **GFM task lists (`- [ ]`) become real Confluence task lists**, which Confluence indexes and reports on.

A panel, a status lozenge, a decision list, a layout and a column width have **no markdown syntax at all**. Write them as raw HTML+ inline in the source file, which markdown permits and this skill passes through untouched:

```markdown
Ordinary prose in markdown.

<div data-type="panel-warning"><p>The egress address is not yet confirmed.</p></div>

More prose.
```

Read `${CLAUDE_SKILL_DIR}/../_shared/references/html-patterns.md` for every pattern, and `house-style.md` for when to use which.

**Nested markdown lists are refused, not mangled.** `- one` with `  - nested` indented under it stops the conversion outright, naming the line, rather than silently splitting into two lists with the marker left as stray text:

```
Error: Line 2 is an indented list item: '- nested'. Nested lists are not supported - flatten it to a top-level item, or write the nesting directly in HTML+ instead (<ul><li><p>...</p><ul>...</ul></li></ul>).
```

Nothing was sent - this fires while converting, before the dry-run report or the real write. Flatten the list, or write the nesting directly as HTML+, the same way a panel or a layout is written.

## Images

An image must be a page attachment before it can appear on the page - conversion does not upload files, and a local path will not resolve. `publish.sh` does not do this step for you; upload separately and reference the result in the source file.

```bash
${CLAUDE_SKILL_DIR}/scripts/attachments.sh upload 8901234 diagrams/context.png
```

It prints the media id and the collection, tab separated. Put both into a figure in the source file:

```html
<figure data-type="media-single" data-layout="center" data-width="80">
  <div data-type="media" data-media-type="file" data-id="MEDIA_ID"
       data-collection="COLLECTION" data-alt="context.png"></div>
  <figcaption>System context, as built</figcaption>
</figure>
```

**Never invent a media id or a collection.** The converter refuses a figure that is missing either. Re-uploading the same file replaces it in place on the page (same attachment, version incremented) but issues a **new** media id every time - re-run the upload and splice the new id into the figure; the old figure does not update itself.

## The banner

Every published page gets an info panel naming the source file, saying a direct Confluence edit is lost at the next publish, and pointing readers at page comments. It is added automatically and is not optional: a warning without a route just tells people their feedback has nowhere to go, so somebody has to actually read those comments back into the source.

## Constraints

- **No delete.** Removing a page or an attachment is a human job in the UI.
- **Dry-run before a first publish**, always.
- **Commit the written-back `page_id`** or the next run creates a duplicate.
- **Never invent an opaque id.**
- **No `--force`.** A round-trip refusal on `update` needs a human fix in the Confluence UI - see [§ Updating an existing page](#updating-an-existing-page).
- **`--base-version` is handled for you** - `publish.sh` reads it fresh immediately before every update; you never pass it yourself.
