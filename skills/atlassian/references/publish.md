# Publishing markdown to Confluence

Turns a markdown file in a repository into a Confluence page, and keeps it that way. The file is the master; Confluence is the rendering.

Credentials, setup and the rules are in `SKILL.md`.

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

**Always dry-run first.** It converts the whole body and reports what would happen. A `--dry-run` against a file with no `page_id` yet is pure local conversion and needs no credentials at all. A `--dry-run` against a file that already carries a `page_id` also makes one read-only call to the live page, to say whether it was edited in Confluence since the last publish, and whether the file changes it - see [§ Updating an existing page](#updating-an-existing-page). Neither ever writes.

The page title is the file's first `# ` heading, or the filename if there is none.

## Updating an existing page

**Publish replaces the page body with the file.** That is the design - the file is the master - so what publish guards against is not the converter, but a person having edited the page in Confluence since the last publish. Their edit would be lost.

Every publish writes its version with the message `Published from <file>`, so the page's latest version says who wrote last. Before it writes, `publish.sh` reads the live page. If the latest version is anything else, it refuses, naming that version, its author and its date:

```
Error: page 8901234 was edited in Confluence since docs/payment-correlation.md was last published.
Cause: version 9, by 5b10ac8d82e05b22cc7d4ef5 on 2026-09-20T10:00:00Z, has the message "", not "Published from docs/payment-correlation.md". Publishing would overwrite it.
Fix: nothing was sent. Read the page (confluence-pages.sh read 8901234 --format markdown), bring that edit into docs/payment-correlation.md, then publish with --base-version 9 to confirm you have.
```

Do exactly that. Read the page, bring the edit into the file (or decide it should go), then run again with `--base-version` set to that version. `--base-version` is not a `--force`: it confirms one exact version, and if another edit lands after it, publish refuses again. A version 1 with no message is what a create leaves, and counts as publish's own.

**A publish that changes nothing sends nothing.** The page's current body is compared with what the file would write, ignoring what Confluence fills in on save - local ids, and the measured size of an image. If they match, publish says `Unchanged` and writes no new version, so re-running an unchanged file does not fill the page history.

If someone saves the page in the moment between publish reading it and writing, the write is refused as a conflict and nothing changes. Run `publish.sh` again.

**There is no `--force` anywhere in this skill, and `publish.sh` adds none.** Run `--dry-run` first: against a file that carries a `page_id`, it reads the live page and says whether the publish would be refused, and whether the file changes the page at all.

## What markdown can and cannot express

These convert:

- Headings, `#` or underlined with `===` / `---`
- Paragraphs, with a hard line break from two trailing spaces or a trailing backslash
- Bullet lists (`-`, `*`, `+`), and numbered lists, which keep their start number (`3.` starts at 3)
- **GFM task lists (`- [ ]`), which become real Confluence task lists** that Confluence indexes and reports on
- Tables
- Blockquotes (`>`), holding paragraphs, lists or code
- Fenced code blocks, with ```` ``` ```` or `~~~`, and their language
- A thematic break (`---`, `***` or `___` on a line of its own)
- Links, `<https://...>` autolinks and `<name@example.com>`
- `**bold**`, `__bold__`, `*italic*`, `_italic_`, `~~strike~~` and `` `code` ``, with backslash escapes (`\*`)
- An image, on a line of its own: at an absolute URL, or a local file, which publish uploads - see [§ Images](#images)

Everything else is refused by name rather than published as literal markdown: an image inside a sentence, a nested list, a reference-style link or footnote, and an indented code block. A heading or a nested quote inside a blockquote is refused by the HTML+ converter, which knows what a blockquote may hold.

A panel, a status lozenge, a decision list, a layout and a column width have **no markdown syntax at all**. Write them as raw HTML+ on its own line in the source file, which markdown permits and this skill passes through untouched:

```markdown
Ordinary prose in markdown.

<div data-type="panel-warning"><p>The egress address is not yet confirmed.</p></div>

More prose.
```

**On its own line, not mid-sentence.** A line that holds only an inline component - a lozenge, a date, an inline card - becomes a paragraph of its own, because a page cannot hold an inline node outside one. The same markup embedded inside running prose - `assignee is <span data-type="status" ...>unset</span> today` - is not detected as a tag and is escaped to inert, visible text instead of passed through as live HTML+. This converter cannot yet tell a real inline tag apart from a stray `<` a human never meant as markup, and guessing wrong there mis-renders prose silently - exactly the failure this whole converter exists to avoid. Give an inline component its own line, even a short one.

A fenced code block's language token is passed straight into a CSS class name (`language-<token>`) and accepted as written - any token, including one with punctuation in it (`` ```c++ ``, `` ```objective-c ``) - not restricted to a plain word.

**A relative link to another file - `[guide](guide.md#heading)` - is refused, not published pointing nowhere.** This converter reads one file at a time; it has no way to know whether `guide.md` has even been published yet, let alone at what Confluence URL. Link that page's absolute Confluence URL once it exists, or point at an absolute external URL. An in-page anchor (`[above](#section)`) and a `mailto:` link both still work - neither is a path to another file.

Before you write the file, read `${CLAUDE_SKILL_DIR}/references/house-style.md` (when to reach for raw HTML+ instead of plain markdown) and `${CLAUDE_SKILL_DIR}/references/html-patterns.md` (every pattern markdown has no syntax for), then `~/.dbhq/atlassian/house-style.md` if it exists. The user's file wins.

**Nested markdown lists are refused, not mangled.** `- one` with `  - nested` indented under it stops the conversion outright, naming the line, rather than silently splitting into two lists with the marker left as stray text:

```
Error: Line 2 is an indented list item: '- nested'. Nested lists are not supported - flatten it to a top-level item, or write the nesting directly in HTML+ instead (<ul><li><p>...</p><ul>...</ul></li></ul>).
```

Nothing was sent - this fires while converting, before the dry-run report or the real write. Flatten the list, or write the nesting directly as HTML+, the same way a panel or a layout is written.

## Images

An image at an absolute URL, `![alt](https://example.com/diagram.png)` on a line of its own, becomes a figure showing that image. Confluence fetches it from that address, so it must stay reachable.

**A local image, `![alt](diagrams/context.png)` on a line of its own, is uploaded by `publish.sh` as a page attachment**, and the figure points at it. The path is read relative to the folder the markdown file is in. A missing file is refused before anything is sent, and so are two different images with the same file name, because a page attachment is named by its file name.

The upload carries the image's sha256 hash in its comment. On the next publish, an attachment of the same name with the same hash is reused rather than uploaded again - a re-upload would issue a new media id and make every publish look like a change. A changed image is uploaded as a new version of the same attachment.

On a first publish the page does not exist yet, so there is nothing to attach to. Publish creates it with a placeholder where each image goes, uploads the images, then writes the full page as version 2.

To place an image by hand instead - with a caption, or a width - upload it and write the figure yourself:

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
