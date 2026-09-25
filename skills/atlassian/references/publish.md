# Publishing markdown to Confluence

Turns a markdown file in a repository into a Confluence page, and keeps it that way. The file is the master; Confluence is the rendering.

Publishing **creates and updates only**. There is no delete verb anywhere - not for a page, not for an attachment. Removing something is a human job in the Confluence UI.

Credentials and setup are in `SKILL.md`. The rules there apply here too.

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

Get the space id from `${CLAUDE_SKILL_DIR}/scripts/confluence-search.sh spaces`. It is the numeric id, not the key.

## Publishing

```bash
${CLAUDE_SKILL_DIR}/scripts/publish.sh docs/payment-correlation.md --dry-run
${CLAUDE_SKILL_DIR}/scripts/publish.sh docs/payment-correlation.md
```

**Always dry-run first.** It converts the whole body and reports what would happen. A `--dry-run` against a file with no `page_id` yet is pure local conversion and needs no credentials at all. A `--dry-run` against a file that already carries a `page_id` also makes one read-only call to the live page, to preview the round-trip gate below before you run for real - see [§ Updating an existing page](#updating-an-existing-page). Neither ever writes.

The page title is the file's first `# ` heading, or the filename if there is none.

## Updating an existing page

`publish.sh` updates through `confluence-pages.sh update`, which carries two protections you do not have to think about but should know are there, because both can stop a publish with nothing sent.

**`update` always needs `--base-version <n>` - `publish.sh` supplies it for you, read fresh immediately before the write.** The file is the master, so there is normally nothing to "base" an edit on the way a human splicing a change into a fetched copy would - but the guard still catches a real case: someone editing the live page directly at the exact moment `publish.sh` runs. If that happens, `update` refuses rather than silently discarding their in-flight change, and reports it as the page having moved on since the version `publish.sh` just read. **The fix is simply to run `publish.sh` again** - there is nothing to splice, because the file on disk already is the whole intended content, unlike the human workflow `confluence.md` documents.

**`update` also runs a round-trip gate.** It refuses to overwrite a page whose current content this converter cannot read back unchanged. A live-site measurement once found this on roughly half of real pages - almost always an ordinary node's own attribute or mark (a local id, a colspanned cell's per-column widths, a list's start number) that this converter dropped rather than carried through, not an exotic node type. Generalising the carry-through-or-opaque-passthrough rule that already covered media to every named node type closed that gap, and the same measurement now passes cleanly; what still refuses is narrower - a node type or attribute this converter has never modelled at all, or a table a person left genuinely inconsistent. `UPDATE REPLACES THE WHOLE BODY`, so writing over content like that would silently drop whatever this converter cannot carry through, not just the part `publish.sh` meant to change. This is expected, ordinary behaviour on a page with any editing history outside this pipeline, not a sign anything is broken:

```
Publish stopped: docs/payment-correlation.md was not sent to page 8901234. Nothing changed. See the error above.
If the page moved on since this run started, just run publish.sh again - the file is the master, so there is nothing to splice by hand.
If the converter refused the round trip, there is no --force: resolve it directly in the Confluence UI, then re-run.
```

**There is no `--force` anywhere in this skill, and `publish.sh` adds none.** A round-trip refusal needs a human decision in the Confluence UI, not a flag. Run `--dry-run` first and it previews this exact check against the page as it stands right now, so the refusal is not a surprise on the real run - though the page can still change between the preview and the write, so treat the preview as "likely", not certain.

## What markdown can and cannot express

Headings, paragraphs, lists, tables, links, inline marks and fenced code blocks convert cleanly. **GFM task lists (`- [ ]`) become real Confluence task lists**, which Confluence indexes and reports on.

A panel, a status lozenge, a decision list, a layout and a column width have **no markdown syntax at all**. Write them as raw HTML+ on its own line in the source file, which markdown permits and this skill passes through untouched:

```markdown
Ordinary prose in markdown.

<div data-type="panel-warning"><p>The egress address is not yet confirmed.</p></div>

More prose.
```

**On its own line, not mid-sentence.** A line that holds only an inline component - a lozenge, a date, an inline card - becomes a paragraph of its own, because a page cannot hold an inline node outside one. The same markup embedded inside running prose - `assignee is <span data-type="status" ...>unset</span> today` - is not detected as a tag and is escaped to inert, visible text instead of passed through as live HTML+. This converter cannot yet tell a real inline tag apart from a stray `<` a human never meant as markup, and guessing wrong there mis-renders prose silently - exactly the failure this whole converter exists to avoid. Give an inline component its own line, even a short one.

A fenced code block's language token is passed straight into a CSS class name (`language-<token>`) and accepted as written - any token, including one with punctuation in it (`` ```c++ ``, `` ```objective-c ``) - not restricted to a plain word.

**A relative link to another file - `[guide](guide.md#heading)` - is refused, not published pointing nowhere.** This converter reads one file at a time; it has no way to know whether `guide.md` has even been published yet, let alone at what Confluence URL. Link that page's absolute Confluence URL once it exists, or point at an absolute external URL. An in-page anchor (`[above](#section)`) and a `mailto:` link both still work - neither is a path to another file.

**Read these two files first. Every time.** They are the difference between a page that uses the platform and a page that is a wall of bold text:

1. `${CLAUDE_SKILL_DIR}/references/house-style.md` - the conventions, and when to reach for a raw HTML+ pattern instead of plain markdown
2. `${CLAUDE_SKILL_DIR}/references/html-patterns.md` - every HTML+ pattern markdown has no syntax for

And if it exists, read `~/.dbhq/atlassian/house-style.md` too. That is the user's own tone and conventions, and it wins over anything in the shipped reference.

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
