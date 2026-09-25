#!/usr/bin/env python3
"""Markdown to Confluence HTML+.

A published page is only as rich as its source, and markdown has a ceiling:
there is no syntax for a panel, a status lozenge, a decision list or a column
width. Inventing one would be a private dialect nothing else reads, so a
source file carries raw HTML+ inline where it needs a native component, and
this converter passes any line starting with `<` straight through.

That passthrough is line-granular, not text-granular: a status lozenge or
similar span written as its own line works, but the same markup embedded
mid-sentence in running prose - "assignee is <span data-type=...>unset</span>
today" - is not detected as a tag at all. `_inline` runs `html.escape` over
every paragraph and heading first, so an embedded tag there is escaped to
inert, visible text (`&lt;span...&gt;`) rather than passed through as live
HTML+, on the same logic that escapes a genuine stray `<` safely rather than
risk treating it as markup it never meant to be. A general "does this look
like a real tag" detector inside running text was judged too large a change
to make safely alongside the fixes below - a wrong call there silently
mis-renders prose, the same class of failure this converter exists to avoid,
just moved rather than removed. Known limit, not a silent one: write a
component that needs raw HTML+ on its own line.

GFM task lists are the one exception. `- [ ]` maps exactly onto a Confluence
task list, which Confluence indexes and reports on, so it is converted rather
than passed through as a bullet with a bracket in it.

Every other common construct either converts or is refused by name - never
published as literal markdown for a reader to see. It converts: ATX and setext
headings, paragraphs with hard breaks, bullet (-, *, +) and numbered lists
with their start number, task lists, tables, blockquotes, backtick and tilde
fences, thematic breaks, links, autolinks, bold, italic and strikethrough in
both the asterisk and the underscore form, inline code, backslash escapes,
and an image at an absolute URL on its own line. It refuses: a relative image,
an image inside a line of text, an image title, reference-style links,
footnotes, an indented code block and a nested list.

Every list item and table cell this converter emits wraps its text in a
`<p>`, not bare text - htmlplus.py's html_to_adf refuses a bare text node
directly inside a listItem, tableCell or tableHeader (BLOCK_ONLY_PARENTS: its
content model is block children only), so `<li>one</li>` is rejected and
`<li><p>one</p></li>` is what has to be emitted. A task-list item is the one
exception on the list side: taskItem takes inline content directly, the same
as a paragraph or a heading, so its text is left bare.

Indented list markers (a bullet, numbered item or task under another item)
are refused with a ConversionError rather than converted, and rather than
silently absorbed into whatever paragraph or list happens to be open when
they are seen. This converter does not support nested lists - review found
that, unsupported, an indented "  - nested" line matches none of BULLET,
ORDERED or TASK (all anchored at column 0), so it fell through into the
paragraph catch-all: its marker survived as literal text, its indentation
collapsed to whatever join the paragraph loop used, and the list around it
split into two separate <ul> blocks with no error at all. That is silent
corruption of exactly the kind this whole converter exists to avoid -
htmlplus.py raises ConversionError by name rather than drop or mangle
anything it cannot represent, and a markdown source deserves the same
treatment rather than a lesser one just because the input is friendlier.
Refusing was chosen over implementing one level of nesting because a clear,
early error that names the line is a better outcome for a publishing tool
than a converter that silently supports depth 1 and silently mangles depth
2 - and because ADF's own nesting (a listItem's block content includes
another bulletList/orderedList) is already reachable for anyone who needs
it, by writing that nesting directly in raw HTML+, the same way a panel or
a layout is.

Standard library only.
"""

import argparse
import html
import re
import sys

# Code is deliberately not in this list - see _inline's own comment for why
# it is protected from these rather than run through them in turn.
CODE = re.compile(r"`([^`]+)`")
# The two asterisk patterns are the originals. The underscore forms follow
# CommonMark's rule that an underscore inside a word (snake_case_name) never
# opens or closes emphasis, so identifiers in prose are left alone.
INLINE = [
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)"), r"<strong>\1</strong>"),
    (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), r"<s>\1</s>"),
    (re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)"), r"<em>\1</em>"),
    (re.compile(r"(?<!\w)_(?=[^\s_])([^_]+?)(?<=[^\s_])_(?!\w)"), r"<em>\1</em>"),
]
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]*)\)")
# <https://example.com> or <help@example.com>, matched after html.escape has
# turned the angle brackets into entities.
AUTOLINK = re.compile(r"&lt;([^\s<>]+?)&gt;")
EMAIL = re.compile(r"^[^@\s/]+@[^@\s/]+\.[^@\s/]+$")
# CommonMark's backslash escapes: a backslash before ASCII punctuation makes
# that character literal.
ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
# RFC 3986's own scheme grammar (scheme = ALPHA *(ALPHA / DIGIT / "+" / "-"
# / ".") ":"), not a hand-maintained list of "http/https/mailto and
# whatever else we happen to have seen" - so a scheme this converter has
# never encountered is still recognised as absolute, rather than being
# mistaken for a bare relative path because it is unfamiliar.
_URL_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

TASK = re.compile(r"^[-*+] \[([ xX])\]\s+(.*)$")
BULLET = re.compile(r"^[-*+]\s+(.*)$")
ORDERED = re.compile(r"^(\d{1,9})[.)]\s+(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
# A fence opens with three or more backticks or tildes, and closes with at
# least as many of the same character. The language token is \S*, not \w*:
# it is passed straight into a CSS class name (language-<token>), not
# validated against an identifier shape, so \w* refused a real, common token
# like c++ outright.
FENCE = re.compile(r"^(`{3,}|~{3,})\s*(\S*)\s*$")
TABLE_SEP = re.compile(r"^\|[\s:|-]+\|$")
THEMATIC_BREAK = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
BLOCKQUOTE = re.compile(r"^ {0,3}>[ ]?(.*)$")
IMAGE_LINE = re.compile(r"^!\[([^\]]*)\]\(([^)]*)\)\s*$")
LINK_DEFINITION = re.compile(r"^ {0,3}\[([^\]]+)\]:\s*\S")
INDENTED_CODE = re.compile(r"^(?: {4}|\t)")
# A line of raw HTML+: a tag name straight after the "<", a closing tag, or a
# comment. "<https://..." is an autolink and "<= 5" is prose, and neither is
# raw HTML.
RAW_HTML = re.compile(r"^\s*<(?:[a-zA-Z][a-zA-Z0-9-]*(?=[\s/>])|/[a-zA-Z]|!--)")
# A bullet, numbered or task marker preceded by at least one space or tab -
# i.e. any of BULLET/ORDERED/TASK's own marker shapes, but indented rather
# than at column 0. Deliberately not "any indented line": ordinary indented
# prose (a continuation line, a quoted snippet) is not a list marker and is
# not this converter's business to refuse.
INDENTED_LIST = re.compile(r"^[ \t]+(?:[-*+]\s+|\d+[.)]\s+)")
# A raw HTML+ line that opens with an inline element - a status lozenge, a
# date, a <br>, an inline mark, a link or an inline card. ADF has no room for
# an inline node straight under the document, so such a line is wrapped in a
# <p> of its own. A block or embed card is a block and is left alone.
INLINE_RAW = re.compile(
    r"^\s*<(?:span|time|br|strong|b|em|i|code|s|del|u|sub|sup)\b"
    r"|^\s*<a\b(?![^>]*data-card-appearance=[\"']?(?:block|embed))",
    re.I,
)
# A line ending in two or more spaces, or in a backslash, is a hard break.
HARD_BREAK = "\x01"


class ConversionError(Exception):
    """Raised when the markdown source cannot be safely converted.

    Named and used the same way as htmlplus.py's ConversionError: a message
    that names the specific line or construct that could not be handled,
    raised in preference to converting it wrong or dropping it silently.
    """


def _link(match, stash):
    """The <a> a markdown link becomes, or a ConversionError naming it.

    A relative path to another local file - [guide](guide.md#heading) -
    passes through to href unchanged as far as this function's caller is
    concerned, but it points nowhere once this page is the thing living at
    a Confluence URL: this converter has no way to know that other file's
    own published page id, or whether it has even been published at all.
    Publishing a dead link silently is worse than refusing outright, so an
    href that is neither an in-page anchor nor an absolute URL (any real
    scheme - http, https, mailto and anything this converter has never
    seen, per _URL_SCHEME) is refused, naming the exact link, rather than
    resolved to a guess or left to point nowhere.
    """
    text, href = match.group(1), match.group(2)
    if not (href.startswith("#") or _URL_SCHEME.match(href)):
        raise ConversionError(
            f"Relative link: [{text}]({href}). This converter cannot "
            f"resolve a relative path to another file's published "
            f"Confluence URL - it never reads that file, so it does not "
            f"know whether it has even been published, let alone at what "
            f"page id. Fix: link that page's absolute Confluence URL once "
            f"it is published, or point this at an absolute external URL "
            f"instead."
        )
    # href, unlike the surrounding text, was never meant to have quote=False
    # html.escape run over it for this purpose - the whole line's own pass
    # at the top of _inline already handled &, < and > for both, but left "
    # alone (quote=False), and href becomes a double-quoted HTML attribute
    # value here. A literal " in a URL is rare but real (copy-pasted from
    # somewhere already percent-encoding-averse), and left unescaped it
    # closes the attribute early - the same malformed-markup shape as
    # htmlplus.py's own alt-text finding, just in the file upstream of it.
    href = href.replace('"', "&quot;")
    return stash(f'<a href="{href}">{_marks(text)}</a>')


def _autolink(match, stash):
    """<https://...> or <name@example.com> as a link, or the text unchanged."""
    target = match.group(1)
    if EMAIL.match(target):
        href = "mailto:" + target
    elif _URL_SCHEME.match(target):
        href = target
    else:
        return match.group(0)
    return stash(f'<a href="{href.replace(chr(34), "&quot;")}">{target}</a>')


def _marks(text):
    for pattern, repl in INLINE:
        text = pattern.sub(repl, text)
    return text


def _inline(text):
    """Inline markdown to HTML+. Text is escaped; tags come only from here.

    Code spans, backslash escapes, links and autolinks are each turned into
    their final HTML first and parked behind a placeholder, so no later
    pattern can reach inside them - **literal** typed in a code span used to
    pick up <strong> that way, and an underscore in a URL would pick up <em>.
    The emphasis patterns then run over what is left, and the placeholders
    go back last. \x00 never occurs in real text, so a placeholder cannot
    collide with anything on either side of this round trip.
    """
    parked = []

    def stash(value):
        parked.append(value)
        return f"\x00{len(parked) - 1}\x00"

    text = CODE.sub(lambda m: stash(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text)
    text = ESCAPE.sub(lambda m: stash(html.escape(m.group(1), quote=False)), text)
    out = html.escape(text, quote=False)
    image = IMAGE.search(out)
    if image:
        raise ConversionError(
            f"Image inside a line of text: {html.unescape(image.group(0))}. "
            f"A page can only show an image as a block of its own. Fix: put "
            f"the image on a line by itself, with a blank line either side."
        )
    out = AUTOLINK.sub(lambda m: _autolink(m, stash), out)
    out = LINK.sub(lambda m: _link(m, stash), out)
    out = _marks(out)
    for _ in range(len(parked) + 1):
        if "\x00" not in out:
            break
        out = re.sub(r"\x00(\d+)\x00", lambda m: parked[int(m.group(1))], out)
    return out.replace(HARD_BREAK, "<br>")


def _image(line):
    """A line holding only ![alt](url), as a figure, or a ConversionError."""
    alt, target = IMAGE_LINE.match(line).groups()
    if re.search(r"\s", target.strip()):
        raise ConversionError(
            f"Image with a title: {line.strip()}. A Confluence image has no "
            f"title. Fix: remove the quoted title and keep the alt text."
        )
    target = target.strip()
    if not _URL_SCHEME.match(target):
        raise ConversionError(
            f"Relative image: {line.strip()}. This converter does not upload "
            f"files, and a local path does not resolve on a Confluence page. "
            f"Fix: upload it with attachments.sh upload <page-id> <file> and "
            f"write a <figure> with the media id it prints (see publish.md, "
            f"Images), or use an absolute https URL."
        )
    if not target.lower().startswith(("https://", "http://")):
        raise ConversionError(
            f"Image URL is not http or https: {line.strip()}. Confluence "
            f"shows an image from a web address only."
        )
    bits = ['data-media-type="external"', f'data-url="{html.escape(target)}"']
    if alt:
        bits.append(f'data-alt="{html.escape(alt)}"')
    return ('<figure data-type="media-single" data-layout="center">'
            f'<div data-type="media" {" ".join(bits)}></div></figure>')


def _looks_like_table_start(lines, i):
    """Whether lines[i] genuinely opens a table - a header row followed by
    a real separator row, not merely a line that happens to start with
    "|".

    Used both to decide whether to start consuming a table (the main loop)
    and to decide whether to stop consuming a paragraph (the paragraph
    branch below). Sharing one check between the two closed an infinite
    loop: the paragraph branch used to stop on any line starting with "|"
    without confirming it was actually a table, so a bare "|" line that
    failed this same test at the top of the loop fell through to the
    paragraph branch, which then refused to consume it either - the while
    loop's own condition was false on its very first line, nothing was
    appended, and i never advanced. Reproduced with a bounded run: fifty
    iterations in, i was still sitting on the same line.
    """
    return (lines[i].startswith("|") and i + 1 < len(lines)
            and TABLE_SEP.match(lines[i + 1]))


def _table(rows):
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    head, body = cells[0], cells[2:]
    out = ["<table><thead><tr>"]
    out += [f"<th><p>{_inline(c)}</p></th>" for c in head]
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>")
        out += [f"<td><p>{_inline(c)}</p></td>" for c in row]
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _starts_a_block(lines, i):
    """Whether lines[i] opens a block of its own and so ends a paragraph."""
    line = lines[i]
    return bool(
        RAW_HTML.match(line) or HEADING.match(line) or FENCE.match(line)
        or THEMATIC_BREAK.match(line) or BLOCKQUOTE.match(line)
        or IMAGE_LINE.match(line) or BULLET.match(line) or ORDERED.match(line)
        or INDENTED_LIST.match(line) or _looks_like_table_start(lines, i)
    )


def md_to_htmlplus(markdown):
    """Convert markdown to a Confluence HTML+ fragment."""
    lines = markdown.replace("\r\n", "\n").split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        if INDENTED_LIST.match(line):
            # Checked before anything else so it fires whether the line is
            # met fresh (the first line of what would otherwise become a
            # paragraph) or was about to be swallowed as a paragraph
            # continuation - the stopping condition added to that loop
            # below hands control back here rather than consuming it there.
            raise ConversionError(
                f"Line {i + 1} is an indented list item: {line.strip()!r}. "
                f"Nested lists are not supported - flatten it to a "
                f"top-level item, or write the nesting directly in HTML+ "
                f'instead (<ul><li><p>...</p><ul>...</ul></li></ul>).'
            )

        fence = FENCE.match(line)
        if fence:
            marker, lang = fence.group(1), fence.group(2) or "plaintext"
            i += 1
            code = []
            while i < len(lines):
                close = FENCE.match(lines[i])
                if close and not close.group(2) and close.group(1)[0] == marker[0] \
                        and len(close.group(1)) >= len(marker):
                    break
                code.append(lines[i])
                i += 1
            i += 1
            body = html.escape("\n".join(code), quote=False)
            out.append(f'<pre><code class="language-{lang}">{body}</code></pre>')
            continue

        # Raw HTML+ passes straight through, which is how a source file
        # expresses a panel, a lozenge or a layout. A line that opens with an
        # inline element gets a <p> of its own (see INLINE_RAW).
        if RAW_HTML.match(line):
            block = []
            while i < len(lines) and lines[i].strip():
                block.append(lines[i])
                i += 1
            raw = "".join(block)
            out.append(f"<p>{raw}</p>" if INLINE_RAW.match(raw) else raw)
            continue

        if INDENTED_CODE.match(line):
            raise ConversionError(
                f"Line {i + 1} is indented by four spaces after a blank line: "
                f"{line.strip()!r}. In markdown that is an indented code "
                f"block, or a second paragraph of a list item, and neither "
                f"is supported. Fix: use a fenced code block (```), or remove "
                f"the indent."
            )

        definition = LINK_DEFINITION.match(line)
        if definition:
            what = ("Footnote" if definition.group(1).startswith("^")
                    else "Link reference definition")
            raise ConversionError(
                f"{what} on line {i + 1}: {line.strip()!r}. Reference-style "
                f"links and footnotes are not supported. Fix: write the link "
                f"inline as [text](https://...), or the note as text."
            )

        if THEMATIC_BREAK.match(line):
            out.append("<hr>")
            i += 1
            continue

        if BLOCKQUOTE.match(line):
            # The quoted lines are markdown in their own right, so they go
            # through this same converter. htmlplus.py then checks what a
            # blockquote may hold - a heading or a nested quote is refused
            # there, by name.
            inner = []
            while i < len(lines) and BLOCKQUOTE.match(lines[i]):
                inner.append(BLOCKQUOTE.match(lines[i]).group(1))
                i += 1
            out.append(f"<blockquote>{md_to_htmlplus(chr(10).join(inner))}</blockquote>")
            continue

        if IMAGE_LINE.match(line):
            out.append(_image(line))
            i += 1
            continue

        heading = HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1
            continue

        if _looks_like_table_start(lines, i):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(_table(rows))
            continue

        if TASK.match(line):
            items = []
            while i < len(lines) and TASK.match(lines[i]):
                state, text = TASK.match(lines[i]).groups()
                checked = " checked" if state.lower() == "x" else ""
                items.append(
                    f'<li data-type="task-item"><input type="checkbox"{checked}> '
                    f"{_inline(text)}</li>"
                )
                i += 1
            out.append('<ul data-type="task-list">' + "".join(items) + "</ul>")
            continue

        if BULLET.match(line):
            items = []
            while i < len(lines) and BULLET.match(lines[i]) \
                    and not TASK.match(lines[i]) \
                    and not THEMATIC_BREAK.match(lines[i]):
                items.append(f"<li><p>{_inline(BULLET.match(lines[i]).group(1))}</p></li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue

        ordered = ORDERED.match(line)
        if ordered:
            # The first number is where the list starts counting, as in
            # CommonMark. 1 is the default and is left off.
            start = int(ordered.group(1))
            items = []
            while i < len(lines) and ORDERED.match(lines[i]):
                items.append(f"<li><p>{_inline(ORDERED.match(lines[i]).group(2))}</p></li>")
                i += 1
            open_tag = "<ol>" if start == 1 else f'<ol start="{start}">'
            out.append(open_tag + "".join(items) + "</ol>")
            continue

        # A paragraph: every line up to a blank one or the start of another
        # block. The first line always belongs to it - everything that could
        # have claimed it has already said no above.
        para = [lines[i]]
        i += 1
        level = 0
        while i < len(lines) and lines[i].strip():
            setext = SETEXT.match(lines[i])
            if setext:
                # "Title" then "=====" (or "-----") is a heading.
                level = 1 if setext.group(1)[0] == "=" else 2
                i += 1
                break
            if _starts_a_block(lines, i):
                break
            para.append(lines[i])
            i += 1
        text = ""
        for index, part in enumerate(para):
            last = index == len(para) - 1
            if not last and (part.endswith("  ") or part.endswith("\\")):
                text += part.rstrip(" ").removesuffix("\\") + HARD_BREAK
            else:
                text += part + ("" if last else " ")
        if level:
            out.append(f"<h{level}>{_inline(text.strip())}</h{level}>")
        else:
            out.append(f"<p>{_inline(text)}</p>")

    return "".join(out)


def main(argv=None):
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    # ConversionError is caught here, not left to propagate, for the same
    # reason htmlplus.py's own CLI catches it: this runs at the front of
    # publish.sh's pipeline, piped straight into md_to_htmlplus.py, and an
    # uncaught exception there is a multi-line Python traceback on stderr -
    # accurate, but not what "surface it clearly" means for a publishing
    # tool. A one-line "Error: ..." naming the offending line, and exit 1,
    # is what the caller can actually act on.
    try:
        sys.stdout.write(md_to_htmlplus(sys.stdin.read()))
    except ConversionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
