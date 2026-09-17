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

    # --- helpers ---

    def _open(self, node):
        node.setdefault("content", [])
        self.blocks[-1]["content"].append(node)
        self.blocks.append(node)

    def _close(self):
        if len(self.blocks) > 1:
            self.blocks.pop()

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
            self.blocks[-1]["content"].append(self._pending_status)

        elif tag == "ul" and dtype == "task-list":
            self._open({"type": "taskList", "attrs": {"localId": ""}})
        elif tag == "li" and dtype == "task-item":
            self._open({"type": "taskItem",
                        "attrs": {"localId": "", "state": "TODO"}})
        elif tag == "input":
            # The checkbox carries the state of the task item it sits in.
            if "checked" in a:
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
            self.blocks[-1]["content"].append({
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
                node = {"type": CARD_TYPES[appearance], "attrs": {"url": href}}
                if appearance == "inline":
                    self.blocks[-1]["content"].append(node)
                else:
                    self.doc["content"].append(node)
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
            self.blocks[-1]["content"].append({"type": "rule"})

        elif tag in SIMPLE_BLOCKS:
            self._open({"type": SIMPLE_BLOCKS[tag]})

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
        node = {"type": "text", "text": data}
        marks = self._current_marks()
        if marks:
            node["marks"] = marks
        self.blocks[-1]["content"].append(node)


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["to-adf"])
    args = parser.parse_args(argv)
    try:
        if args.command == "to-adf":
            doc = html_to_adf(sys.stdin.read())
            json.dump(doc, sys.stdout, separators=(",", ":"))
    except ConversionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
