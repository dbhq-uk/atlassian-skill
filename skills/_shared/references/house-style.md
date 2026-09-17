# Authoring a Confluence page

Confluence is a working reference, not a document dump. A page that uses the
platform's own components is quicker to scan, survives editing by other
people, and reports its own state without a reader having to parse prose.
These are the conventions.

Two things this reference assumes, because they are where generated pages
usually come unstuck:

- The body is authored as **Confluence HTML+**, not Markdown. Markdown cannot
  express a panel, a status lozenge, a task list or a set of column widths, so
  a Markdown pipeline silently flattens all of them into bold text and even
  columns.
- **`html-patterns.md` beside this file is the reference for that format**, and
  it is worth reading before authoring rather than after a rejected publish.
  `htmlplus.py` converts HTML+ to the ADF the REST API takes, and rejects
  invalid nesting locally with the offending element named.

---

## 1 · Use the native components, always

Never hand-build an imitation of something Confluence already provides. A bold
word is not a status lozenge; a line beginning with a dash and a name in
brackets is not a task; ALL CAPS is not a warning panel; a bare URL is not a
smart link.

| Instead of | Use | Pattern |
| --- | --- | --- |
| `**WARNING: ...**` or ALL CAPS | Warning panel | `<div data-type="panel-warning">` |
| A bold aside | Info / note / success / error panel | `<div data-type="panel-info">` |
| `**Built**`, `**Switched off**` in a cell | Status lozenge | `<span data-type="status" data-color="green">Built</span>` |
| `- [ ] Do the thing (Owner)` | Task list | `<ul data-type="task-list">` |
| "We agreed X on this date" | Decision list | `<ul data-type="decision-list">` |
| A long appendix nobody scrolls past | Expand | `<details><summary>…</summary>` |
| A two-column Version / Status / Source table | Page properties | bodied `details` macro |
| A raw `<a href>` to another Confluence page | Inline smart link | `<a href="URL" data-card-appearance="inline"></a>` |
| `2026-09-08` as plain text | Date node | `<time datetime="2026-09-08">8 September 2026</time>` |

**This is HTML+, not storage format.** Storage format is Confluence's other
body representation and uses a different vocabulary (`<ac:structured-macro>`,
`<ri:page>`, CDATA macro bodies). Do not mix the two: `htmlplus.py` converts
HTML+ to ADF, and a storage-format element in an HTML+ body is rejected as an
unknown element.

### Status lozenge colours

Pick the colour from the state, and use the same colour for the same state
across every page in a set.

| State | Colour |
| --- | --- |
| Done, built, live, agreed | `green` |
| In progress, partial, built but switched off | `yellow` |
| Blocked, failed, rejected | `red` |
| Not started, not built, out of scope | `neutral` |
| Under review, proposed, awaiting a decision | `blue` or `purple` |

---

## 2 · Page titles

Concise, Title Case, and `&` rather than "and".

- **"Quarterly Review Agenda & Record"**, not "Quarterly review agenda and record".
- Concise means the shortest phrase that still identifies the page. Trim the
  explainer, keep the identity.
- Prefer a short distinct name over a long prefixed one. A parent page already
  supplies the context, so a child does not need to repeat it in full.
- Avoid ASCII decoration in a title (`<-->`, `|`, `=>`). If two systems need
  naming, name them: **"Orders to Billing Integration"**.

---

## 3 · Tables

Tables carry most of the information on an architecture page, so they get
sized deliberately rather than left to default.

**A table with a prose column runs the full page width.** Put
`data-width="1800"` on the `<table>`, and set `data-colwidth` on every cell of
a column to match the length of the data that column will hold. The widths sum
to roughly the table width.

Measure the widths rather than guessing them: open a page whose table already
reads well, and copy its `data-colwidth` values. The method is what transfers,
not any particular number - a column of dates, counts or short statuses gets a
narrow width, and the prose column absorbs the slack.

**Where every column carries similar weight**, set `data-width` and leave the
columns even. Per-column sizing is for tables with a prose column, not a rule
to apply mechanically.

**A table with few columns and narrow data is not stretched.** A small number,
a short status, a date: it stays at its natural size and sits left, with
`data-layout="default"` and a `data-width` that fits the content. The
per-column rule still applies within it.

**Two narrow tables in sequence go side by side**, in a two-column layout
section scoped to just those two:

```html
<section data-type="layout-two-equal">
  <div data-type="column"><table …>…</table></div>
  <div data-type="column"><table …>…</table></div>
</section>
```

The layout wraps only that pair, never the surrounding page.

**When editing an existing table, copy every cell's `data-colwidth` verbatim.**
Dropping it resets the table to evenly distributed columns, which reads as a
formatting regression to everyone who sees the diff.

---

## 4 · House style for the words

The default tone is **professional, plain and direct**. It reads as a capable
colleague writing to another one, and it is never sales-heavy or hyperbolic.

- **British English**, always. Organise, recognise, behaviour, licence (noun),
  practise (verb).
- Clarity and precision over jargon, but correct industry terminology where it
  adds value.
- Never state a figure without a verified source.
- Dates as `<time>` nodes. In prose, write **17 September 2026**, not 09/17/26,
  which two readers will read two ways.

**Your own conventions go in a file, not in this one.** If
`~/.dbhq/atlassian/house-style.md` exists, the skills read it and apply it on
top of everything here. That is where an organisation's tone, its real
measured column widths, its canonical site host and its own naming rules
belong. Absent that file, this reference is the whole style.

### The register question

A wire contract and a design narrative are different jobs and can take
different registers. Where a page is a contract that another team builds
against, a constrained register such as ASD-STE100 Simplified Technical
English is a legitimate choice, and precision beats elegance in that job.

What the register does **not** change is the formatting. STE's convention of
setting a warning in capitals is a print convention from a world with no
panels. On Confluence, a CAUTION goes in a
`<div data-type="panel-warning">` with the text in sentence case. The panel
does the shouting; the words do the explaining, and a page of capitals stops
being read after the third one.

State the register once at the top of a page that uses one, so a reader knows
the flatness is deliberate.

---

## 5 · Cut these before publishing

Generated prose has recognisable habits. They read as filler to a human
reader, and they are the difference between a page that sounds like a
colleague wrote it and one that sounds like a language model. Cut all of
them:

- **Telling the reader what to conclude.** State the facts and stop. Not "so X
  is really Y", "worth holding that distinction", "this is the important one".
- **Narrating your own process.** Nobody needs to know what an earlier draft
  got wrong, that you verified something, or how much work it took.
- **Hedging twice.** Mark a claim as inference or unverified once, where it is
  made. Never explain why the hedge is there and never repeat it at the other
  end of the paragraph.
- **Justifying a request.** State it. Not "because it is free to ask".
- **Scaffolding headings and preambles.** If an opening line explains the
  section instead of being the section, delete it.
- **Self-praising qualifiers.** "Two honest caveats" is "two caveats". Same for
  careful, rigorous, exhaustive, thorough describing your own output.
- **Framing nobody used.** Never invent a category, positioning or status for
  someone else's work. If it cannot be cited it cannot be asserted; a
  plausible characterisation reads as established and gets repeated.
- **Reassurance and flattery.** "Good question", "your instinct was right".
- **Editorialising adjectives on evidence.** "Real work", "the good news is".
- **Em dashes.** Use a comma, colon, semicolon or parentheses.

Then reread and delete every sentence that does one of the above. Expect to
lose roughly a fifth. If deleting a sentence costs no information, it was one
of these.

---

## 6 · Updating a page that already exists

**An update replaces the whole body.** There is no partial edit, so the safe
route is always fetch, splice, verify:

1. **Fetch the current body immediately before writing**, in the same format
   you intend to publish. Not a copy read earlier in the session: another
   person or job may have landed a version in between, and building on a stale
   copy destroys their work as surely as deleting it.
2. **Splice your change into what came back**, preserving everything you are
   not deliberately changing: local IDs, `data-colwidth` values, inline
   comment anchors (`data-annotation-id`), media IDs and collections.
3. **Verify by re-fetching** and reading the section you changed.

Never invent an opaque ID (`data-id`, `data-collection`, `data-media-id`,
`data-resource-id`, `data-annotation-id`). Copy them from the fetched content
or from an upload step's output.

### Where a page is generated from a repository

If a page is published from a file under version control, the file is the
master and Confluence is the rendering. Say so on the page, name the file
path, and warn that a direct edit in Confluence is lost at the next publish.

Then make the alternative work: **a reader who cannot edit needs somewhere to
put a correction.** Point them at a page comment, and make sure comments are
actually read into the source. A warning without a route just tells people
their feedback has nowhere to go.

---

## 7 · Before you publish

The full checklist is in `checklist.md` beside this file. The four that catch
the most:

- Every status, warning, task and decision is a native component, not bold
  text.
- Every table has been sized deliberately.
- Every cross-page link is a smart link, and it points at the canonical
  `*.atlassian.net` URL. **A link copied out of a browser may carry a
  corporate security proxy's rewritten host.** Those work for the person who
  copied them and break or misroute for others. Strip the proxy host back to
  the real one.
- Every in-page jump uses the heading-text anchor form
  (`<a href="#Open-items">`), not a Markdown slug (`#open-items`). Confluence
  will not resolve the slug and the link renders dead.
