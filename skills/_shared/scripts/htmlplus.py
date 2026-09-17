#!/usr/bin/env python3
"""Confluence HTML+ to Atlassian Document Format, and back.

HTML+ is the format the Atlassian editor and the Atlassian MCP server accept.
It is not a REST body representation, so this converter is what lets a skill
author in HTML+ and still publish over the plain REST API: HTML+ in, ADF out,
POSTed as body.representation=atlas_doc_format.

The same ADF is what a Jira v3 issue description takes, so one converter
serves both products.

Standard library only. No packages, no venv.
"""

import argparse
import json
import sys
from html.parser import HTMLParser

# Marks that wrap inline text, keyed by tag.
INLINE_MARKS = {
    "strong": "strong",
    "b": "strong",
    "em": "em",
    "i": "em",
    "code": "code",
    "s": "strike",
    "del": "strike",
    "u": "underline",
    "sub": "subsup",
    "sup": "subsup",
}

HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

# A body is a fragment. These tags mean somebody handed over a whole document.
FORBIDDEN_WRAPPERS = {"html", "head", "body"}

PANEL_TYPES = {"info", "note", "success", "warning", "error"}
STATUS_COLOURS = {"neutral", "purple", "blue", "red", "yellow", "green"}
DECISION_STATES = {"DECIDED", "UNDECIDED"}
CARD_TYPES = {"inline": "inlineCard", "block": "blockCard", "embed": "embedCard"}

# Column counts per layout. The number of <div data-type="column"> children
# must match, or Confluence rejects the document.
LAYOUTS = {
    "layout-two-equal": 2,
    "layout-two-left-sidebar": 2,
    "layout-two-right-sidebar": 2,
    "layout-three-equal": 3,
    "layout-three-with-sidebars": 3,
    "layout-section": 1,
}

SIMPLE_BLOCKS = {
    "ul": "bulletList",
    "ol": "orderedList",
    "li": "listItem",
    "blockquote": "blockquote",
}

# A sentinel for FORBIDDEN_CHILDREN entries whose content model is plain
# text and nothing else - not even the other inline nodes (status, date, a
# link card) that a heading or a task item can legally hold. codeBlock is
# the only one: real ADF code blocks carry marks-free text, full stop. It
# is kept distinct from the shared "inline content only" None entries below
# rather than folded into their BLOCK_TYPES check, because that check does
# not treat status/date/card children as a violation, and inside a
# codeBlock they are one.
TEXT_ONLY = "text-only"

# What each container cannot directly contain, keyed by ADF node type.
# Straight from the nesting table in references/html-patterns.md, which is in
# turn ADF's own rules. Confluence rejects a violation with a descriptive
# error after the call; this is what rejects it before one.
FORBIDDEN_CHILDREN = {
    "listItem": {"heading", "table", "blockquote", "panel", "expand",
                 "layoutSection", "rule"},
    "panel": {"table", "expand", "blockquote", "embedCard", "panel",
              "layoutSection"},
    "expand": {"expand", "layoutSection", "bodiedExtension"},
    "tableCell": {"table", "layoutSection", "bodiedExtension"},
    "tableHeader": {"table", "layoutSection", "bodiedExtension"},
    # ADF blockquote content is paragraphs, lists and code blocks only -
    # not headings, tables, panels, expands or layout sections either.
    "blockquote": {"blockquote", "heading", "table", "panel", "expand",
                   "layoutSection"},
    "table": {"table"},
    # Inline-content-only containers: any block child at all is a violation.
    "taskItem": None,
    "decisionItem": None,
    "heading": None,
    "codeBlock": TEXT_ONLY,
}

# Every block node type this converter emits. Used for the inline-only check.
BLOCK_TYPES = {
    "paragraph", "heading", "table", "tableRow", "tableCell", "tableHeader",
    "panel", "expand", "blockquote", "bulletList", "orderedList", "listItem",
    "taskList", "taskItem", "decisionList", "decisionItem", "codeBlock",
    "layoutSection", "layoutColumn", "rule", "blockCard", "embedCard",
}

# Containers whose ADF content model is blocks only - a bare text node sitting
# straight inside one of these is invalid, even though the parser is happy to
# hand it over. Confluence's own v2 API only rejects this for panel, and does
# so with a bare 500 and a null detail; the rest fail just as surely, only
# without telling anyone - the editor cannot represent bare text there and
# silently repairs or mangles it on the next human edit.
BLOCK_ONLY_PARENTS = {"listItem", "tableCell", "tableHeader", "panel",
                       "blockquote"}


def a_or_an(word):
    """"a" or "an" before word, by the crude vowel-sound test.

    Good enough for the ADF node type names this converter ever puts in an
    error message - real words, not abbreviations that read differently
    ("an SQL", "a UUID") which the sound test would get wrong.
    """
    return "an" if word[:1].lower() in "aeiou" else "a"


class ConversionError(Exception):
    """Raised with a message naming the element that could not be converted."""


def _date_to_timestamp(value):
    """ISO yyyy-mm-dd to the epoch-millisecond string ADF wants."""
    import calendar
    import datetime

    try:
        day = datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ConversionError(
            f'datetime="{value}" is not an ISO date. Use yyyy-mm-dd.'
        )
    epoch = calendar.timegm(day.timetuple())
    return str(epoch * 1000)


class _Builder(HTMLParser):
    """Walks HTML+ and builds an ADF content list.

    Two stacks. `blocks` holds the open block nodes, innermost last, with the
    ADF document at the bottom. `marks` holds the inline marks currently in
    scope, so text picks up every mark wrapping it rather than only the nearest.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.doc = {"type": "doc", "version": 1, "content": []}
        self.blocks = [self.doc]
        self.marks = []
        self._pending_status = None
        self._in_summary = False
        self._in_time = False
        self._in_card = False
        # One frame per currently-open <table>, innermost last. Each frame is
        # {"cell_index": int, "widths": {column index: set of widths seen}},
        # where None in a widths set means "no attribute". Scoped per table
        # rather than one flat pair of attributes, so a table nested inside
        # another table's cell tracks its own columns without corrupting or
        # being corrupted by the columns of the table it sits in.
        self._table_stack = []

    # --- helpers ---

    def _open(self, node):
        self._check_nesting(node["type"])
        node.setdefault("content", [])
        self.blocks[-1]["content"].append(node)
        self.blocks.append(node)

    def _append(self, node):
        """Append a leaf node - no children, no stack push - after checking
        it against the currently open ancestors.

        Everything that opens and stays open goes through _open, which pushes
        onto the block stack and so gets checked there. A leaf - plain text,
        a rule, a status lozenge, a date, a link card - never gets pushed,
        but it still needs to clear the same nesting rules a block does, or
        it walks straight past the validator this whole task exists to add:
        a <li>hello</li> or an <h2><hr></h2> would otherwise never reach
        _check_nesting at all.
        """
        self._check_nesting(node["type"])
        self.blocks[-1]["content"].append(node)

    def _check_nesting(self, child_type):
        """Reject an invalid parent/child pair, naming both.

        Walks the whole stack of open blocks, innermost first, not only the
        immediate parent. A table nested inside an expand nested inside a
        table cell is still a table inside a table cell as far as ADF is
        concerned - the expand itself is a perfectly legal home for a table,
        so a check that stopped at the nearest ancestor would let that
        violation through undetected (Confluence would still reject it, just
        after the call, which is the exact failure mode this function exists
        to catch first). So every open ancestor that has a rule in
        FORBIDDEN_CHILDREN gets checked in turn; the walk only stops early
        when it finds a violation to raise.
        """
        for ancestor in reversed(self.blocks):
            parent_type = ancestor.get("type")
            if parent_type not in FORBIDDEN_CHILDREN:
                continue
            forbidden = FORBIDDEN_CHILDREN[parent_type]
            if forbidden is None:
                if child_type in BLOCK_TYPES:
                    raise ConversionError(
                        f"{a_or_an(parent_type).capitalize()} {parent_type} "
                        f"takes inline content only, so it cannot contain "
                        f"{a_or_an(child_type)} {child_type}. Close the "
                        f"{parent_type} and put the {child_type} after it."
                    )
                continue
            if forbidden is TEXT_ONLY:
                if child_type != "text":
                    raise ConversionError(
                        f"{a_or_an(parent_type).capitalize()} {parent_type} "
                        f"takes plain text only, so it cannot contain "
                        f"{a_or_an(child_type)} {child_type}. Close the "
                        f"{parent_type} and put the {child_type} after it."
                    )
                continue
            if child_type in forbidden:
                raise ConversionError(
                    f"{a_or_an(parent_type).capitalize()} {parent_type} "
                    f"cannot contain {a_or_an(child_type)} {child_type}. "
                    f"Close the {parent_type} and put the {child_type} "
                    f"after it as a sibling."
                )

    def _close(self):
        if len(self.blocks) > 1:
            self.blocks.pop()

    def _record_width(self, index, raw):
        frame = self._table_stack[-1]
        frame["widths"].setdefault(index, set()).add(raw)

    def _check_column_widths(self, widths):
        """Every cell of a column carries the same data-colwidth, or none does.

        Confluence silently resets a table to evenly distributed columns when
        one cell in a column is missing the attribute, and that reads as a
        formatting regression to everyone who sees the diff. So it is caught
        here, before the call.
        """
        for index, seen_widths in sorted(widths.items()):
            if len(seen_widths) == 1:
                continue
            if None in seen_widths:
                raise ConversionError(
                    f"Table column {index + 1}: data-colwidth is on some cells "
                    f"and not others. Put it on every cell of the column, "
                    f"header and body alike, with the same value."
                )
            seen = ", ".join(sorted(w for w in seen_widths if w is not None))
            raise ConversionError(
                f"Table column {index + 1}: two different data-colwidth "
                f"values ({seen}). Every cell of a column takes the same one."
            )

    def _current_marks(self):
        # A list of dicts, deduplicated by type, in the order they were opened.
        out, seen = [], set()
        for m in self.marks:
            if m["type"] not in seen:
                seen.add(m["type"])
                out.append(m)
        return out

    # --- parser callbacks ---

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        dtype = a.get("data-type", "")

        if tag in FORBIDDEN_WRAPPERS:
            raise ConversionError(
                f"<{tag}> found. A Confluence body is a fragment: no <html>, "
                f"<head> or <body> wrapper."
            )

        if tag == "p":
            self._open({"type": "paragraph"})
        elif tag in HEADINGS:
            self._open({"type": "heading", "attrs": {"level": HEADINGS[tag]}})
        elif tag == "code" and self.blocks[-1]["type"] == "codeBlock":
            # Inside a <pre>, <code> carries the language rather than an
            # inline mark - checked ahead of the generic INLINE_MARKS branch
            # below, which would otherwise claim "code" first every time.
            for cls in a.get("class", "").split():
                if cls.startswith("language-"):
                    self.blocks[-1]["attrs"]["language"] = cls[len("language-"):]
        elif tag in INLINE_MARKS:
            mark = {"type": INLINE_MARKS[tag]}
            if tag in ("sub", "sup"):
                mark["attrs"] = {"type": tag}
            self.marks.append(mark)

        elif tag == "div" and dtype.startswith("panel-"):
            kind = dtype[len("panel-"):]
            if kind not in PANEL_TYPES:
                raise ConversionError(
                    f'data-type="{dtype}" is not a panel. '
                    f"Use one of: {', '.join(sorted(PANEL_TYPES))}."
                )
            self._open({"type": "panel", "attrs": {"panelType": kind}})

        elif tag == "span" and dtype == "status":
            colour = a.get("data-color", "neutral")
            if colour not in STATUS_COLOURS:
                raise ConversionError(
                    f'data-color="{colour}" is not a status colour. '
                    f"Use one of: {', '.join(sorted(STATUS_COLOURS))}."
                )
            # Text arrives in handle_data; park the node and fill it there.
            self._pending_status = {
                "type": "status",
                "attrs": {"text": "", "color": colour},
            }
            self._append(self._pending_status)

        elif tag == "ul" and dtype == "task-list":
            self._open({"type": "taskList", "attrs": {"localId": ""}})
        elif tag == "li" and dtype == "task-item":
            self._open({"type": "taskItem",
                        "attrs": {"localId": "", "state": "TODO"}})
        elif tag == "input":
            # The checkbox carries the state of the task item it sits in.
            if "checked" in a:
                if self.blocks[-1].get("type") != "taskItem":
                    raise ConversionError(
                        "<input checked> found outside a task-list item. A "
                        'checkbox belongs in a task-list item: <li '
                        'data-type="task-item">.'
                    )
                self.blocks[-1]["attrs"]["state"] = "DONE"

        elif tag == "ul" and dtype == "decision-list":
            self._open({"type": "decisionList", "attrs": {"localId": ""}})
        elif tag == "li" and dtype == "decision-item":
            state = a.get("data-state", "DECIDED")
            if state not in DECISION_STATES:
                raise ConversionError(
                    f'data-state="{state}" is not a decision state. '
                    f"Use DECIDED or UNDECIDED."
                )
            self._open({"type": "decisionItem",
                        "attrs": {"localId": "", "state": state}})

        elif tag == "details":
            self._open({"type": "expand", "attrs": {"title": ""}})
        elif tag == "summary":
            self._in_summary = True

        elif tag == "pre":
            self._open({"type": "codeBlock", "attrs": {"language": "plaintext"}})

        elif tag == "time":
            self._append({
                "type": "date",
                "attrs": {"timestamp": _date_to_timestamp(a.get("datetime", ""))},
            })
            self._in_time = True

        elif tag == "a":
            href = a.get("href", "")
            appearance = a.get("data-card-appearance")
            if appearance:
                if appearance not in CARD_TYPES:
                    raise ConversionError(
                        f'data-card-appearance="{appearance}" is not a card. '
                        f"Use inline, block or embed."
                    )
                # Appended to the currently open block, whatever it is - not
                # forced to the document root. A card left where its author
                # put it is what _check_nesting can actually rule on; moving
                # it to the root silently relocates their content instead.
                node = {"type": CARD_TYPES[appearance], "attrs": {"url": href}}
                self._append(node)
                self._in_card = True
            else:
                self.marks.append({"type": "link", "attrs": {"href": href}})

        elif tag == "section" and dtype in LAYOUTS:
            self._open({"type": "layoutSection", "_expected": LAYOUTS[dtype],
                        "_layout": dtype})
        elif tag == "div" and dtype == "column":
            parent = self.blocks[-1]
            width = round(100.0 / parent.get("_expected", 1), 2)
            self._open({"type": "layoutColumn", "attrs": {"width": width}})

        elif tag == "hr":
            self._append({"type": "rule"})

        elif tag in SIMPLE_BLOCKS:
            self._open({"type": SIMPLE_BLOCKS[tag]})

        elif tag == "table":
            attrs_out = {}
            if "data-width" in a:
                attrs_out["width"] = int(a["data-width"])
            if "data-layout" in a:
                attrs_out["layout"] = a["data-layout"]
            if a.get("data-number-column") == "true":
                attrs_out["isNumberColumnEnabled"] = True
            if a.get("data-display-mode"):
                attrs_out["displayMode"] = a["data-display-mode"]
            self._open({"type": "table", "attrs": attrs_out})
            self._table_stack.append({"cell_index": 0, "widths": {}})
        elif tag in ("thead", "tbody", "tfoot"):
            # Not ADF nodes. Rows sit directly on the table.
            pass
        elif tag == "tr":
            if not self._table_stack:
                raise ConversionError(
                    "<tr> found outside a <table>. A row must sit inside a "
                    "table."
                )
            self._open({"type": "tableRow"})
            self._table_stack[-1]["cell_index"] = 0
        elif tag in ("th", "td"):
            if not self._table_stack:
                raise ConversionError(
                    f"<{tag}> found outside a <table>. A cell must sit "
                    f"inside a table."
                )
            node_type = "tableHeader" if tag == "th" else "tableCell"
            cell_attrs = {}
            raw = a.get("data-colwidth")
            if raw is not None:
                if not raw.isdigit():
                    raise ConversionError(
                        f'data-colwidth="{raw}" is not a plain number. '
                        f"Confluence drops anything else rather than coercing "
                        f"it. Write 242, never 242px and never 50%."
                    )
                cell_attrs["colwidth"] = [int(raw)]
            for key, attr in (("colspan", "colspan"), ("rowspan", "rowspan")):
                if key in a:
                    cell_attrs[attr] = int(a[key])
            frame = self._table_stack[-1]
            self._record_width(frame["cell_index"], raw)
            frame["cell_index"] += 1
            self._open({"type": node_type, "attrs": cell_attrs})

        else:
            raise ConversionError(
                f"<{tag}{' data-type=' + dtype if dtype else ''}> is not a "
                f"known HTML+ element."
            )

    def handle_endtag(self, tag):
        if tag in INLINE_MARKS and self.marks:
            for i in range(len(self.marks) - 1, -1, -1):
                if self.marks[i]["type"] == INLINE_MARKS[tag]:
                    self.marks.pop(i)
                    break
        elif tag == "span" and self._pending_status is not None:
            # Strip once here, now every run has been accumulated, rather
            # than on each handle_data call - that was overwriting instead
            # of accumulating and losing every run but the last.
            self._pending_status["attrs"]["text"] = (
                self._pending_status["attrs"]["text"].strip()
            )
            self._pending_status = None
        elif tag == "summary":
            self.blocks[-1]["attrs"]["title"] = (
                self.blocks[-1]["attrs"]["title"].strip()
            )
            self._in_summary = False
        elif tag == "time":
            self._in_time = False
        elif tag == "a":
            if self._in_card:
                self._in_card = False
            elif self.marks and self.marks[-1]["type"] == "link":
                self.marks.pop()
        elif tag == "input":
            pass
        elif tag == "section":
            node = self.blocks[-1]
            if node.get("type") == "layoutSection":
                expected = node.pop("_expected", None)
                layout = node.pop("_layout", "")
                actual = len(node["content"])
                if expected is not None and actual != expected:
                    raise ConversionError(
                        f'data-type="{layout}" needs {expected} columns, '
                        f"found {actual}."
                    )
            self._close()
        elif tag in ("p", "div", "details", "pre", "ol", "blockquote") \
                or tag in HEADINGS or tag in ("ul", "li"):
            self._close()
        elif tag == "table":
            if not self._table_stack:
                # A </table> with no matching open <table> - the parser
                # accepts an unmatched close tag rather than rejecting it,
                # so this is guarded rather than left to crash on pop().
                raise ConversionError(
                    "</table> found with no matching <table> open."
                )
            frame = self._table_stack.pop()
            self._check_column_widths(frame["widths"])
            self._close()
        elif tag in ("thead", "tbody", "tfoot"):
            pass
        elif tag in ("tr", "th", "td"):
            self._close()

    def handle_data(self, data):
        if not data.strip():
            return
        if self._in_card:
            # A smart link renders its anchor text from the target, so any
            # text inside the element is discarded rather than emitted.
            return
        if self._in_time:
            return
        if self._pending_status is not None:
            # Accumulate every run rather than overwriting, so inline markup
            # inside the status (e.g. an <em>) does not drop the runs either
            # side of it. Stripped once when the <span> closes.
            self._pending_status["attrs"]["text"] += data
            return
        if self._in_summary:
            # Same accumulate-then-strip as status, above.
            self.blocks[-1]["attrs"]["title"] += data
            return
        if self.blocks[-1] is self.doc:
            raise ConversionError(
                f"Loose text outside any block: {data.strip()[:40]!r}. "
                f"Wrap it in a <p>."
            )
        parent_type = self.blocks[-1].get("type")
        if parent_type in BLOCK_ONLY_PARENTS:
            raise ConversionError(
                f"{a_or_an(parent_type).capitalize()} {parent_type} takes "
                f"block content only, so it cannot hold bare text: "
                f"{data.strip()[:40]!r}. Wrap it in a <p>."
            )
        node = {"type": "text", "text": data}
        marks = self._current_marks()
        if marks:
            node["marks"] = marks
        self._append(node)


def html_to_adf(fragment):
    """Convert an HTML+ fragment to an ADF document dict."""
    if "```" in fragment:
        raise ConversionError(
            "Markdown code fence found. Send the HTML+ body on its own, with "
            "no code fence around it."
        )
    builder = _Builder()
    builder.feed(fragment)
    builder.close()
    if len(builder.blocks) > 1:
        # Anything left on the stack besides the document itself never saw
        # its closing tag. Emitting it anyway would hand Confluence a node
        # still carrying private bookkeeping keys (or simply malformed
        # nesting) - fail the whole conversion instead, generically, rather
        # than special-casing any one element.
        unclosed = builder.blocks[-1]["type"]
        raise ConversionError(
            f'Unclosed "{unclosed}" element: the fragment ended before it '
            f"was closed. Every element that opens a block needs a "
            f"matching closing tag."
        )
    return builder.doc


# --- ADF back to HTML+ ---

_MARK_TAGS = {"strong": "strong", "em": "em", "code": "code",
              "strike": "s", "underline": "u"}
_PANEL_LABELS = {"info": "Info", "note": "Note", "success": "Success",
                 "warning": "Warning", "error": "Error"}
_LAYOUT_BY_COUNT = {1: "layout-section", 2: "layout-two-equal",
                    3: "layout-three-equal"}


def _escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))


def _timestamp_to_iso(ms):
    import datetime
    return datetime.datetime.fromtimestamp(
        int(ms) / 1000, tz=datetime.timezone.utc
    ).strftime("%Y-%m-%d")


def _inline_to_html(node):
    t = node.get("type")
    if t == "text":
        out = _escape(node["text"])
        for mark in node.get("marks", []):
            mt = mark["type"]
            if mt == "link":
                out = f'<a href="{mark["attrs"]["href"]}">{out}</a>'
            elif mt == "subsup":
                tag = mark["attrs"]["type"]
                out = f"<{tag}>{out}</{tag}>"
            elif mt in _MARK_TAGS:
                tag = _MARK_TAGS[mt]
                out = f"<{tag}>{out}</{tag}>"
        return out
    if t == "status":
        a = node["attrs"]
        return (f'<span data-type="status" data-color="{a["color"]}">'
                f'{_escape(a["text"])}</span>')
    if t == "date":
        iso = _timestamp_to_iso(node["attrs"]["timestamp"])
        return f'<time datetime="{iso}">{iso}</time>'
    if t == "inlineCard":
        return f'<a href="{node["attrs"]["url"]}" data-card-appearance="inline"></a>'
    if t == "hardBreak":
        return "<br>"
    return ""


def _children_html(node):
    return "".join(_node_to_html(c) for c in node.get("content", []))


def _inline_html(node):
    return "".join(_inline_to_html(c) for c in node.get("content", []))


def _node_to_html(node):
    t = node.get("type")
    a = node.get("attrs", {})
    if t == "paragraph":
        return f"<p>{_inline_html(node)}</p>"
    if t == "heading":
        level = a["level"]
        return f"<h{level}>{_inline_html(node)}</h{level}>"
    if t == "panel":
        return f'<div data-type="panel-{a["panelType"]}">{_children_html(node)}</div>'
    if t == "expand":
        return (f'<details><summary>{_escape(a.get("title", ""))}</summary>'
                f"{_children_html(node)}</details>")
    if t == "codeBlock":
        text = "".join(c.get("text", "") for c in node.get("content", []))
        lang = a.get("language", "plaintext")
        return f'<pre><code class="language-{lang}">{_escape(text)}</code></pre>'
    if t == "taskList":
        return f'<ul data-type="task-list">{_children_html(node)}</ul>'
    if t == "taskItem":
        checked = " checked" if a.get("state") == "DONE" else ""
        return (f'<li data-type="task-item"><input type="checkbox"{checked}>'
                f"{_inline_html(node)}</li>")
    if t == "decisionList":
        return f'<ul data-type="decision-list">{_children_html(node)}</ul>'
    if t == "decisionItem":
        return (f'<li data-type="decision-item" data-state="{a.get("state", "DECIDED")}">'
                f"{_inline_html(node)}</li>")
    if t == "bulletList":
        return f"<ul>{_children_html(node)}</ul>"
    if t == "orderedList":
        return f"<ol>{_children_html(node)}</ol>"
    if t == "listItem":
        return f"<li>{_children_html(node)}</li>"
    if t == "blockquote":
        return f"<blockquote>{_children_html(node)}</blockquote>"
    if t == "rule":
        return "<hr>"
    if t == "table":
        bits = []
        if "width" in a:
            bits.append(f'data-width="{a["width"]}"')
        if "layout" in a:
            bits.append(f'data-layout="{a["layout"]}"')
        if a.get("isNumberColumnEnabled"):
            bits.append('data-number-column="true"')
        if "displayMode" in a:
            bits.append(f'data-display-mode="{a["displayMode"]}"')
        open_tag = "<table" + ("" if not bits else " " + " ".join(bits)) + ">"
        return f"{open_tag}<tbody>{_children_html(node)}</tbody></table>"
    if t == "tableRow":
        return f"<tr>{_children_html(node)}</tr>"
    if t in ("tableCell", "tableHeader"):
        tag = "td" if t == "tableCell" else "th"
        bits = []
        if "colwidth" in a:
            bits.append(f'data-colwidth="{a["colwidth"][0]}"')
        for key in ("colspan", "rowspan"):
            if key in a:
                bits.append(f'{key}="{a[key]}"')
        open_tag = f"<{tag}" + ("" if not bits else " " + " ".join(bits)) + ">"
        return f"{open_tag}{_children_html(node)}</{tag}>"
    if t == "layoutSection":
        count = len(node.get("content", []))
        layout = _LAYOUT_BY_COUNT.get(count, "layout-two-equal")
        return f'<section data-type="{layout}">{_children_html(node)}</section>'
    if t == "layoutColumn":
        return f'<div data-type="column">{_children_html(node)}</div>'
    if t in ("blockCard", "embedCard"):
        appearance = "block" if t == "blockCard" else "embed"
        return f'<a href="{a["url"]}" data-card-appearance="{appearance}"></a>'
    return _inline_to_html(node)


def adf_to_html(doc):
    """Render an ADF document as an HTML+ fragment."""
    return "".join(_node_to_html(n) for n in doc.get("content", []))


# --- ADF to markdown, one way only ---

def _inline_md(node):
    out = []
    for child in node.get("content", []):
        t = child.get("type")
        if t == "text":
            text = child["text"]
            for mark in child.get("marks", []):
                mt = mark["type"]
                if mt == "strong":
                    text = f"**{text}**"
                elif mt == "em":
                    text = f"*{text}*"
                elif mt == "code":
                    text = f"`{text}`"
                elif mt == "link":
                    text = f'[{text}]({mark["attrs"]["href"]})'
            out.append(text)
        elif t == "status":
            out.append(f'[{child["attrs"]["text"]}]')
        elif t == "date":
            out.append(_timestamp_to_iso(child["attrs"]["timestamp"]))
        elif t == "inlineCard":
            out.append(child["attrs"]["url"])
    return "".join(out)


def _node_to_md(node, depth=0):
    t = node.get("type")
    a = node.get("attrs", {})
    if t == "paragraph":
        return _inline_md(node)
    if t == "heading":
        return "#" * a["level"] + " " + _inline_md(node)
    if t == "panel":
        label = _PANEL_LABELS.get(a.get("panelType", "info"), "Note")
        inner = "\n".join(_node_to_md(c) for c in node.get("content", []))
        return "\n".join(f"> **{label}:** {line}" if i == 0 else f"> {line}"
                         for i, line in enumerate(inner.splitlines()))
    if t == "codeBlock":
        text = "".join(c.get("text", "") for c in node.get("content", []))
        return f'```{a.get("language", "")}\n{text}\n```'
    if t == "taskList":
        return "\n".join(_node_to_md(c) for c in node.get("content", []))
    if t == "taskItem":
        box = "x" if a.get("state") == "DONE" else " "
        return f"- [{box}] {_inline_md(node).strip()}"
    if t == "decisionList":
        return "\n".join(_node_to_md(c) for c in node.get("content", []))
    if t == "decisionItem":
        return f'- ({a.get("state", "DECIDED").lower()}) {_inline_md(node).strip()}'
    if t in ("bulletList", "orderedList"):
        marker = "-" if t == "bulletList" else "1."
        return "\n".join(
            f"{'  ' * depth}{marker} "
            + "\n".join(_node_to_md(g, depth + 1)
                        for g in c.get("content", [])).strip()
            for c in node.get("content", [])
        )
    if t == "expand":
        inner = "\n\n".join(_node_to_md(c) for c in node.get("content", []))
        return f'**{a.get("title", "")}**\n\n{inner}'
    if t == "blockquote":
        inner = "\n".join(_node_to_md(c) for c in node.get("content", []))
        return "\n".join(f"> {line}" for line in inner.splitlines())
    if t == "rule":
        return "---"
    if t == "table":
        rows = []
        for row in node.get("content", []):
            cells = [" ".join(_node_to_md(c) for c in cell.get("content", []))
                     for cell in row.get("content", [])]
            rows.append("| " + " | ".join(cells) + " |")
            if len(rows) == 1:
                rows.append("|" + "|".join([" --- "] * len(cells)) + "|")
        return "\n".join(rows)
    if t in ("layoutSection", "layoutColumn"):
        return "\n\n".join(_node_to_md(c) for c in node.get("content", []))
    if t in ("blockCard", "embedCard"):
        return a.get("url", "")
    return ""


def adf_to_markdown(doc):
    """Render an ADF document as lossy markdown, for reading only.

    A panel becomes a labelled blockquote and a lozenge becomes bracketed
    text, because markdown has no syntax for either. Converting this back and
    writing it to the page destroys every native component on it, which is why
    the skills never do that: editing goes through HTML+.
    """
    return "\n\n".join(
        block for block in (_node_to_md(n) for n in doc.get("content", []))
        if block
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["to-adf", "to-html", "to-markdown"])
    args = parser.parse_args(argv)
    try:
        if args.command == "to-adf":
            doc = html_to_adf(sys.stdin.read())
            json.dump(doc, sys.stdout, separators=(",", ":"))
        elif args.command == "to-html":
            sys.stdout.write(adf_to_html(json.load(sys.stdin)))
        elif args.command == "to-markdown":
            sys.stdout.write(adf_to_markdown(json.load(sys.stdin)))
    except ConversionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
