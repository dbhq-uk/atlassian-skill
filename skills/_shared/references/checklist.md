# Before you publish

Run this against the body you are about to send. It takes a couple of minutes
and catches the things a reader notices first.

## Format

- [ ] The body is Confluence HTML+, not Markdown, and not storage-format XML
      (`<ac:structured-macro>` and friends).
- [ ] It is a fragment: no `<html>`, `<body>` or `<head>` wrapper, no Markdown
      code fences around it.
- [ ] Every warning, caution and note is a panel. Nothing is shouting in
      capitals to make up for a missing one.
- [ ] Every state word (built, blocked, switched off, not started) is a status
      lozenge, and the same state uses the same colour on every page in the set.
- [ ] Every action with an owner is a task-list item, not a dash and a name.
- [ ] Every agreed position is a decision-list item.
- [ ] Long reference material a reader does not need in their way sits in an
      expand.
- [ ] The Version / Status / Source block is a two-column table with a
      header column - the page-properties macro is not supported yet, see
      html-patterns.md.

## Tables

- [ ] Every table has been sized deliberately rather than left at the default.
- [ ] A table with a prose column is full width, `data-width="1800"`, with
      `data-colwidth` on every cell of every column and the widths summing to
      roughly the table width.
- [ ] A table of short values sits left at its natural size,
      `data-layout="default"` with a `data-width` that fits.
- [ ] Two narrow tables in sequence are side by side in a two-column layout
      section scoped to just that pair.
- [ ] Widths are plain numbers. No `px`, no `%`.
- [ ] On an edited table, every existing `data-colwidth` was copied through
      verbatim.

## Links

- [ ] Cross-page references are inline smart links with empty anchor text.
- [ ] Prose links keep their anchor text only where it has to read as part of
      the sentence.
- [ ] Every URL is on the canonical `*.atlassian.net` host, with no corporate
      security-proxy rewrite left in.
- [ ] In-page jumps use the heading-text anchor form (`#Open-items`), not a
      Markdown slug (`#open-items`), and every one has been clicked.
- [ ] No `§`, "see section 9", or invented cross-reference number pretending to
      be a link.

## Words

- [ ] British English throughout.
- [ ] Dates are `<time>` nodes; prose dates are written out (17 September 2026).
- [ ] Any convention from `~/.dbhq/atlassian/house-style.md` has been applied.
- [ ] No em dashes.
- [ ] No figure appears without a verified source.
- [ ] The page states its register if it uses a constrained one.
- [ ] A read-through has removed: conclusions drawn for the reader, process
      narration, doubled hedges, justified requests, scaffolding preambles,
      self-praising qualifiers, invented framing for someone else's work,
      reassurance, and editorialising adjectives on evidence.
- [ ] Roughly a fifth of the first draft is gone. If nothing was cut, the pass
      did not happen.

## Title and placement

- [ ] Title is concise, Title Case, `&` rather than "and".
- [ ] No ASCII decoration in the title (`<-->`, `|`, `=>`).
- [ ] The title does not repeat what the parent page already establishes.
- [ ] The page sits under the right parent, and any page it supersedes carries
      a banner naming what replaced it.

## Updating an existing page

- [ ] The current body was fetched **immediately before** writing, not earlier
      in the session.
- [ ] The change was spliced into what came back, not into an older copy.
- [ ] Local IDs, `data-colwidth` values, inline comment anchors, media IDs and
      collections all survived unchanged - except a table's or a heading's
      own local id, which this converter does not read on the way in and so
      cannot preserve even when copied through verbatim.
- [ ] No opaque ID was invented.
- [ ] The result was verified by re-fetching and reading the changed section.

## If the page is generated from a repository

- [ ] The page names the source file path and says a direct Confluence edit is
      lost at the next publish.
- [ ] There is a working route for a reader's correction, and somebody reads
      it back into the source.
