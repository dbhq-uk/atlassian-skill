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
INLINE = [
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)"), r"<em>\1</em>"),
]
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
# RFC 3986's own scheme grammar (scheme = ALPHA *(ALPHA / DIGIT / "+" / "-"
# / ".") ":"), not a hand-maintained list of "http/https/mailto and
# whatever else we happen to have seen" - so a scheme this converter has
# never encountered is still recognised as absolute, rather than being
# mistaken for a bare relative path because it is unfamiliar.
_URL_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

TASK = re.compile(r"^- \[([ xX])\]\s+(.*)$")
BULLET = re.compile(r"^[-*]\s+(.*)$")
ORDERED = re.compile(r"^\d+\.\s+(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# \S*, not \w*: a fence's language token is passed straight into a CSS
# class name (language-<token>), not validated against an identifier
# shape, so \w* refused a real, common token like c++ outright - the line
# matched no branch at all and fell through to the paragraph catch-all,
# with its own ``` markers read as literal text rather than opening a
# fence.
FENCE = re.compile(r"^```(\S*)\s*$")
TABLE_SEP = re.compile(r"^\|[\s:|-]+\|$")
# A bullet, numbered or task marker preceded by at least one space or tab -
# i.e. any of BULLET/ORDERED/TASK's own marker shapes, but indented rather
# than at column 0. Deliberately not "any indented line": ordinary indented
# prose (a continuation line, a quoted snippet) is not a list marker and is
# not this converter's business to refuse.
INDENTED_LIST = re.compile(r"^[ \t]+(?:[-*]\s+|\d+\.\s+)")


class ConversionError(Exception):
    """Raised when the markdown source cannot be safely converted.

    Named and used the same way as htmlplus.py's ConversionError: a message
    that names the specific line or construct that could not be handled,
    raised in preference to converting it wrong or dropping it silently.
    """


def _link(match):
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
    return f'<a href="{href}">{text}</a>'


def _inline(text):
    out = html.escape(text, quote=False)

    # Code spans are protected from every later substitution by pulling
    # them out to a placeholder first and putting them back, verbatim and
    # already escaped, only as the very last step - never run through
    # INLINE or LINK at all. Without this, **literal** typed inside a code
    # span picked up <strong> from the bold pattern below: each pattern
    # re-scans the whole string after the one before it ran, with nothing
    # to tell "text this same function just inserted as a tag" apart from
    # "text that was always there" - a code span's own content is exactly
    # the place that distinction matters, since it promises not to be
    # reinterpreted as markup at all. \x00 is not valid UTF-8 text and
    # HTML.escape/the other patterns never produce it, so it cannot collide
    # with anything real on either side of this round trip.
    codes = []

    def _stash(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    out = CODE.sub(_stash, out)
    for pattern, repl in INLINE:
        out = pattern.sub(repl, out)
    out = LINK.sub(_link, out)
    for index, code in enumerate(codes):
        out = out.replace(f"\x00{index}\x00", f"<code>{code}</code>")
    return out


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
            lang = fence.group(1) or "plaintext"
            i += 1
            code = []
            while i < len(lines) and not FENCE.match(lines[i]):
                code.append(lines[i])
                i += 1
            i += 1
            body = html.escape("\n".join(code), quote=False)
            out.append(f'<pre><code class="language-{lang}">{body}</code></pre>')
            continue

        # Raw HTML+ passes straight through, which is how a source file
        # expresses a panel, a lozenge or a layout.
        if line.lstrip().startswith("<"):
            block = []
            while i < len(lines) and lines[i].strip():
                block.append(lines[i])
                i += 1
            out.append("".join(block))
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

        for pattern, tag in ((BULLET, "ul"), (ORDERED, "ol")):
            if pattern.match(line) and not TASK.match(line):
                items = []
                while i < len(lines) and pattern.match(lines[i]) \
                        and not TASK.match(lines[i]):
                    items.append(
                        f"<li><p>{_inline(pattern.match(lines[i]).group(1))}</p></li>"
                    )
                    i += 1
                out.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
                break
        else:
            para = []
            while i < len(lines) and lines[i].strip() \
                    and not lines[i].lstrip().startswith("<") \
                    and not HEADING.match(lines[i]) \
                    and not FENCE.match(lines[i]) \
                    and not BULLET.match(lines[i]) \
                    and not ORDERED.match(lines[i]) \
                    and not INDENTED_LIST.match(lines[i]) \
                    and not _looks_like_table_start(lines, i):
                para.append(lines[i])
                i += 1
            if not para:
                # Structurally unreachable today: every condition this
                # while loop checks was already checked, and found false,
                # for lines[i] earlier in this same outer iteration - this
                # loop only runs at all once BULLET, ORDERED, HEADING,
                # FENCE, the raw-HTML check and INDENTED_LIST have all
                # already said no to the current line, and now
                # _looks_like_table_start is the exact same function the
                # table branch above already called on it and also got
                # False from. That last equality is what the original bug
                # did not have: the table branch required a real separator
                # row on the following line, but this loop's own stop
                # condition used to be a bare "starts with |" - so a "|"
                # line that was not a genuine table correctly failed the
                # table check, then immediately failed this loop's first
                # condition too, appending nothing and advancing i by
                # zero. The outer while looped on that same line forever.
                # Kept as a backstop rather than removed now that the two
                # checks agree: an infinite loop is the one failure mode
                # here worse than a redundant few lines.
                para.append(lines[i])
                i += 1
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            continue

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
