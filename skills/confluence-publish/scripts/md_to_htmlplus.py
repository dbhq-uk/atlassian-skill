#!/usr/bin/env python3
"""Markdown to Confluence HTML+.

A published page is only as rich as its source, and markdown has a ceiling:
there is no syntax for a panel, a status lozenge, a decision list or a column
width. Inventing one would be a private dialect nothing else reads, so a
source file carries raw HTML+ inline where it needs a native component, and
this converter passes any line starting with `<` straight through.

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

Standard library only.
"""

import argparse
import html
import re
import sys

INLINE = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)"), r"<em>\1</em>"),
    (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r'<a href="\2">\1</a>'),
]

TASK = re.compile(r"^- \[([ xX])\]\s+(.*)$")
BULLET = re.compile(r"^[-*]\s+(.*)$")
ORDERED = re.compile(r"^\d+\.\s+(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE = re.compile(r"^```(\w*)\s*$")
TABLE_SEP = re.compile(r"^\|[\s:|-]+\|$")


def _inline(text):
    out = html.escape(text, quote=False)
    for pattern, repl in INLINE:
        out = pattern.sub(repl, out)
    # Un-escape the tags the substitutions just produced.
    for tag in ("code", "strong", "em"):
        out = out.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        out = out.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    out = re.sub(r"&lt;a href=&quot;([^&]+)&quot;&gt;", r'<a href="\1">', out)
    out = out.replace("&lt;/a&gt;", "</a>")
    return out


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

        if line.startswith("|") and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]):
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
                    and not lines[i].startswith("|"):
                para.append(lines[i])
                i += 1
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            continue

    return "".join(out)


def main(argv=None):
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    sys.stdout.write(md_to_htmlplus(sys.stdin.read()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
