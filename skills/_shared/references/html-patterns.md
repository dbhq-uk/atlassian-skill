# Confluence HTML+: the working subset

The patterns you actually need to author a page. `htmlplus.py` converts every
one of these to ADF and rejects anything it does not recognise, naming the
element.

The format maps one-to-one onto Atlassian Document Format, so it inherits
ADF's nesting rules. The converter enforces them locally, before the call, so
a violation names the offending element and its parent rather than arriving as
a server error afterwards.

**Emit a body fragment only.** No `<html>`, `<body>` or `<head>` wrapper, and
no Markdown code fences around it.

---

## Panels

```html
<div data-type="panel-info"><p>Context a reader needs before the section.</p></div>
<div data-type="panel-note"><p>An aside that is not a warning.</p></div>
<div data-type="panel-success"><p>Something that is done and proven.</p></div>
<div data-type="panel-warning"><p>Something that will bite.</p></div>
<div data-type="panel-error"><p>Something that is actively broken.</p></div>
```

A panel takes paragraphs, headings, lists, code blocks and media. It cannot
contain a table, an expand, a blockquote or another panel.

Use `panel-warning` for anything that would otherwise be written in capitals.

`panel-custom` is the editor's emoji-and-colour panel, on a page fetched back
from Confluence: `data-panel-icon-id`, `data-panel-icon`, `data-panel-icon-text`
and `data-panel-color`. Round-trip bookkeeping, the same as a media id - not
something to invent when hand-authoring a fresh panel; use one of the five
named colours above instead.

---

## Status lozenges

```html
<span data-type="status" data-color="green">Built</span>
<span data-type="status" data-color="yellow">Built, switched off</span>
<span data-type="status" data-color="red">Blocked</span>
<span data-type="status" data-color="neutral">Not built</span>
```

Colours: `neutral`, `purple`, `blue`, `red`, `yellow`, `green`.

A lozenge is inline, so it sits in a table cell or mid-sentence. A table whose
Status column holds lozenges is scannable at a glance in a way that a column of
bold words is not.

---

## Tasks and decisions

```html
<ul data-type="task-list">
  <li data-type="task-item"><input type="checkbox"> Confirm the egress address with the platform team</li>
  <li data-type="task-item"><input type="checkbox" checked> Publish the as-built contract</li>
</ul>

<ul data-type="decision-list">
  <li data-type="decision-item" data-state="DECIDED">Payments correlate on <code>reference</code>, resolved back to <code>transactionId</code></li>
  <li data-type="decision-item" data-state="UNDECIDED">The expenses-system destination</li>
</ul>
```

Both are inline-content only. No block elements inside an item.

A task list is not decoration: Confluence indexes tasks, so an item written as
a real task appears in the assignee's own task view and in reports across the
space. An item written as a dash and a name does not.

All four elements above also carry a `data-local-id` on a page fetched back
from Confluence - on the `<ul>` for a task list authored directly through this
skill (Confluence leaves it empty), and always on each `<li>` item, task or
decision, which Confluence assigns a real id to the moment it is saved, even
when none was sent. Like the media `data-local-id` above, this is round-trip
bookkeeping, not something to invent when hand-authoring a new list - leave it
off, exactly as the examples above do, and Confluence assigns its own.

**`data-local-id` is not special to media and lists.** A page fetched back
from Confluence carries one on almost every element that can hold content -
paragraphs, headings, plain `<ul>`/`<ol>`/`<li>`, `<blockquote>`, `<hr>`,
`<table>`/`<tr>`/`<td>`/`<th>`, panels, code blocks and expands, status
lozenges, `<time>`, and every card. Every one of them is read back and
preserved when splicing a change into a fetched page - see the checklist -
and none of them is something to invent by hand.

Occasionally a fetched element's local id is a real Confluence-assigned
`null` rather than a value - a task item most often. That round-trips as
`data-local-id-null` (a bare boolean attribute, no value), distinct from
both `data-local-id="..."` (a real value, including an empty string) and no
`data-local-id` attribute at all (the key was never there). Splice it
through unchanged like any other local id; never write it by hand.

An `<ol>` also takes the plain HTML `start` attribute for a list that does not
begin at 1 - `<ol start="5">` - unlike a local id, this one an author might
genuinely set by hand.

---

## Expands

```html
<details><summary>Full change log</summary>
  <p>…</p>
</details>
```

An expand takes almost any block content including tables and panels. It
cannot nest inside another expand. Inside a table cell it is allowed, and
this converter emits it as an ordinary `expand` node there too - not ADF's
separate `nestedExpand` type, which this converter does not write.

`<details>` also takes `data-local-id` and, on a fetched page,
`data-breakout-mode` (see Code blocks above) for an expand stretched wide.

Use one for anything a reader needs available but not in their way: a change
log, a long field map, a superseded position kept for the record.

---

## Page properties

**Not supported yet.** The Version / Status / Source block at the top of a
document page is Confluence's page-properties macro on a live page - a
bodied `details` extension (`data-type="bodied-extension"`) that makes its
values readable by a page-properties report elsewhere in the space.
`htmlplus.py` has no HTML+ authoring form for a bodied extension: it can only
ever arrive on a page opaquely, read back from a fetch, never written fresh.
Authoring one by hand is refused:

    Error: <div data-type=bodied-extension> is not a known HTML+ element.

**Use a plain two-column table instead** - it is the pattern that actually
publishes, not a lesser stand-in for the macro:

```html
<table data-layout="default" data-width="900">
  <tbody>
    <tr><th data-colwidth="200"><p>Version</p></th><td data-colwidth="700"><p>v1.0</p></td></tr>
    <tr><th data-colwidth="200"><p>Status</p></th><td data-colwidth="700"><p><span data-type="status" data-color="green">Built</span></p></td></tr>
    <tr><th data-colwidth="200"><p>Updated</p></th><td data-colwidth="700"><p><time datetime="2026-09-08">8 September 2026</time></p></td></tr>
  </tbody>
</table>
```

The first column is `<th>`, which renders it as a header column.

---

## Opaque content

A real page carries node types and marks this converter has no name for -
the bodied extension above is one, and Atlassian adds more over time. Rather
than refuse the whole page over a part it cannot render, these arrive as:

```html
<div data-type="adf-opaque" data-adf="BASE64_JSON"></div>
<span data-type="adf-opaque-mark" data-adf="BASE64_JSON">…</span>
```

`div` in block position, `span` inline. `data-adf` is that node's or mark's
own ADF, base64-encoded, and converts straight back to exactly what it was.
**An inline comment anchor - Confluence's annotation mark - is the one you
meet most often**: it has no named HTML+ form either, so it always arrives
as `adf-opaque-mark`.

**Never hand-write one, and never edit `data-adf`.** Copy the whole element
through unchanged when splicing a change into a fetched page - it is
round-trip bookkeeping, the same as a media id or a local id, not something
to author.

---

## Tables

```html
<table data-width="1800">
  <thead>
    <tr>
      <th data-colwidth="200"><p>Item</p></th>
      <th data-colwidth="1500"><p>What it means</p></th>
      <th data-colwidth="100"><p>Time</p></th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td data-colwidth="200"><p>…</p></td>
      <td data-colwidth="1500"><p>…</p></td>
      <td data-colwidth="100"><p>40 min</p></td>
    </tr>
  </tbody>
</table>
```

`data-colwidth` goes on **every cell of a column**, header and body alike, with
the same value. Widths are plain numbers with no unit: `242`, never `242px`.
Anything that is not a plain number is dropped rather than coerced.

A cell spanning more than one column with `colspan` carries one width per
spanned column, comma separated: `data-colwidth="200,400"` on a `colspan="2"`
cell. An ordinary single-column cell keeps the plain single value above.

Other table attributes: `data-layout` (`default`, `center`, `wide`,
`full-width`), `data-number-column="true"` for automatic row numbering,
`data-display-mode="fixed"` to lock the widths. A fetched page's `<table>`,
`<tr>`, `<td>` and `<th>` also carry `data-local-id`, and a cell can carry
`data-background` (a colour the editor's cell-shading picker set) - both
round-trip bookkeeping-or-formatting, not something to invent by hand.

Cells accept block content (paragraphs, lists, panels, nested expands), so a
cell that needs a caveat can carry one rather than pushing it into a footnote.

---

## Links

```html
<!-- Reference link, and the default: renders the target's live title and icon -->
<a href="https://mycompany.atlassian.net/wiki/spaces/DOCS/pages/1234567" data-card-appearance="inline"></a>

<!-- Prose link, where the anchor text has to read as part of the sentence -->
<p>The <a href="https://…/pages/1234567">contract page</a> gives the field rules.</p>

<!-- Standalone card the page is highlighting -->
<a href="https://…/pages/1234567" data-card-appearance="block"></a>

<!-- Full view: a recording, a video, a whiteboard -->
<a href="https://www.loom.com/share/VIDEO_ID" data-card-appearance="embed"></a>
```

A smart link renders anchor text from the target, so leave the element empty.
A card in a sources list or a "read these" table shows the real page title and
keeps showing the right one after somebody renames the page.

**Check the host.** A URL copied from a browser may carry a corporate security
proxy's rewritten host rather than the real `*.atlassian.net` one. Those links
work for whoever copied them and break or misroute for other readers. Always
publish the canonical host.

### In-page jumps

```html
<p>See <a href="#Open-items">the open items</a>.</p>
<h2>Open items</h2>
```

The href is `#` plus the heading text with spaces replaced by hyphens, matching
the heading's own case. A Markdown slug (`#open-items`) does not resolve, and a
duplicate heading appends `.1`, `.2`.

Do not write `§`, "section 9", or an invented number. Those are not links.

---

## Dates

```html
<p>Confirmed on <time datetime="2026-09-08">8 September 2026</time>.</p>
```

Always a `<time>` node for a real calendar date. Never wrap a duration, a
version or a non-date value.

---

## Layouts

```html
<section data-type="layout-two-equal">
  <div data-type="column"><p>Left</p></div>
  <div data-type="column"><p>Right</p></div>
</section>
```

Available: `layout-two-equal`, `layout-two-left-sidebar`,
`layout-two-right-sidebar`, `layout-three-equal`, `layout-three-with-sidebars`,
`layout-section`. The number of `column` children must match the layout.

A column takes `data-width` (a percentage, no `%` sign: `66.66`) for a divider
dragged off the even split its name suggests - omit it when hand-authoring a
fresh layout and this converter computes an even split itself. On a fetched
page, `<section>` can also carry `data-breakout-mode` (see Code blocks above)
for a layout section stretched wide.

---

## Code blocks

```html
<pre><code class="language-json">{ "transactionId": 1 }</code></pre>
```

Use the real language class so it highlights, or write `language-plaintext`
for a bare route or a shell line where you want to say so explicitly. Leaving
the class off entirely is also fine and is what a fetched page's own
unlabelled code blocks actually look like - this converter no longer invents
`plaintext` where the source had no language at all.

`<pre>` also takes `data-local-id` (round-trip bookkeeping, as elsewhere) and,
on a page fetched back from Confluence, `data-breakout-mode`
(`wide`/`full-width`) with an optional `data-breakout-width` in pixels for a
`wide` block resized past its default - the editor's own "make this wide"
toggle. `data-wrap` is not a real attribute; this converter has no line-wrap
toggle and silently drops it if written.

---

## Images and attachments

An image must be uploaded as a page attachment first (`attachments.sh upload
<page-id> <file>`); conversion does not upload files, and a local filesystem
path will not resolve. Reference the returned media id and collection:

```html
<figure data-type="media-single" data-layout="center" data-width="80">
  <div data-type="media" data-media-type="file" data-id="MEDIA_ID"
       data-collection="contentId-1234567" data-alt="system-context.png"></div>
  <figcaption>System context, as built.</figcaption>
</figure>
```

`data-width` on the `<figure>` is what the editor shows as the display width;
leave `data-width-type` off to let Confluence pick a sensible pixel size on
first save rather than trying to guess one. Both are round-tripped exactly as
Confluence saved them, so an already-published figure's width and width-type
survive an edit unchanged regardless of which the original author wrote -
copy them through verbatim rather than recomputing either.

Re-uploading a file with the same name replaces the attachment in place (no
duplicate) but issues a new media id every time - splice the new id into any
figure that used the old one; do not assume it stayed the same because the
attachment did.

The inner `<div>` also carries the image's own pixel `data-width`/
`data-height` and a `data-local-id` on a page fetched back from Confluence.
Both are round-trip bookkeeping, not something to invent when hand-authoring
a new figure - leave them off and Confluence assigns its own.

A PDF or zip is a media node, not an `<a href>`; a relative href does not
become an attachment chip.

---

## Nesting rules worth remembering

These are the ones generated HTML trips over most:

| Container | Cannot directly contain |
| --- | --- |
| `<li>` | heading, table, blockquote, panel, expand, layout, rule |
| Panel | table, expand, blockquote, embed card, another panel |
| Expand | another expand, layout section, bodied extension |
| Table cell | table, top-level expand, layout section, bodied extension |
| Task / decision item, heading, caption | any block element; inline only |

To attach a table or a panel to a list, close the list and put the block after
it as a sibling.

Self-nesting is disallowed for expand, blockquote, panel and table. Lists nest
freely.
