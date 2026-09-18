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
import base64
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

# Task 11b: opaque passthrough. An ADF node or mark this converter does not
# know by name is carried through untouched rather than refused (a block or
# inline node) or silently dropped (a mark) - see _opaque_to_html and
# _opaque_mark_to_html below, and the _Builder branches that read the two
# data-type values back. Real Confluence pages carry far more node and mark
# types than this converter has named support for (media, extension,
# textColor, alignment, breakout and more, measured against a live site) -
# passthrough is the floor that lets any page be read and edited around the
# parts this converter cannot yet render, not a replacement for adding named
# support where it is worth having (Task 14 for mediaSingle/media).
ADF_OPAQUE = "adf-opaque"
ADF_OPAQUE_MARK = "adf-opaque-mark"

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
    # A paragraph is not covered by the shared None/inline-only sentinel
    # below, because it legitimately holds non-block inline content that
    # sentinel would reject too (status, date, inlineCard) - it only needs
    # to reject the block-shaped things a <p> can end up wrapping through
    # this parser, notably a block/embed card that was written nested
    # inside a paragraph rather than left as a sibling of it. mediaSingle
    # and media joined this set in Task 14, for the same reason: a figure
    # is a block, exactly like a table or a panel, and cannot sit inside a
    # paragraph either.
    "paragraph": {"blockCard", "embedCard", "table", "panel", "expand",
                  "layoutSection", "heading", "rule", "mediaSingle", "media"},
    # Inline-content-only containers: any block child at all is a violation.
    "taskItem": None,
    "decisionItem": None,
    "heading": None,
    # Task 14. A caption's content model is inline text only (the same
    # shape as a heading or a task item), not the codeBlock TEXT_ONLY
    # sentinel below it - a caption can still hold, say, a status lozenge.
    "caption": None,
    "codeBlock": TEXT_ONLY,
}

# Every block node type this converter emits. Used for the inline-only check.
BLOCK_TYPES = {
    "paragraph", "heading", "table", "tableRow", "tableCell", "tableHeader",
    "panel", "expand", "blockquote", "bulletList", "orderedList", "listItem",
    "taskList", "taskItem", "decisionList", "decisionItem", "codeBlock",
    "layoutSection", "layoutColumn", "rule", "blockCard", "embedCard",
    "mediaSingle", "media",
}

# Containers whose ADF content model is blocks only - a bare text node sitting
# straight inside one of these is invalid, even though the parser is happy to
# hand it over. Confluence's own v2 API only rejects this for panel, and does
# so with a bare 500 and a null detail; the rest fail just as surely, only
# without telling anyone - the editor cannot represent bare text there and
# silently repairs or mangles it on the next human edit.
#
# Task 7 widened this from the original five (listItem, tableCell,
# tableHeader, panel, blockquote) to every other container whose content
# model is exactly one kind of block child and nothing else - taskList
# (taskItem+), decisionList (decisionItem+), bulletList/orderedList
# (listItem+), table (tableRow+), tableRow (tableCell|tableHeader+),
# layoutSection (layoutColumn+), layoutColumn (block+) and expand (block+
# past its title). codeBlock is deliberately not here even though it looks
# block-only shaped: its content model is plain text, not blocks, so it
# needs the opposite treatment and handle_data never checks it against
# this set.
#
# This set now does double duty: handle_data also reads it to decide
# whether whitespace-only text is inter-tag formatting to discard, or
# content to keep. Without the wider set, the newline between two <li>
# (parent bulletList at that point, not listItem, which already closed)
# would have been kept as a stray text node - the very regression the
# fix for the mark-order space-eating bug had to avoid reintroducing.
#
# mediaSingle joined this set in Task 14, for the same reason: its content
# is a media leaf plus an optional caption, block-only the same way a
# listItem's is, and every real published figure this converter was
# checked against (Task 14's live-site round-trip re-measure) is written
# pretty-printed - a newline and indentation between <div data-type="media">
# and <figcaption>. Without this entry that whitespace is content, not
# formatting: it lands as a stray text node wedged between the media and
# the caption, which is exactly the class of bug BLOCK_ONLY_PARENTS exists
# to prevent for every other block-only container.
BLOCK_ONLY_PARENTS = {"listItem", "tableCell", "tableHeader", "panel",
                      "blockquote", "taskList", "decisionList",
                      "bulletList", "orderedList", "table", "tableRow",
                      "layoutSection", "layoutColumn", "expand",
                      "mediaSingle"}


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


def _parse_float(raw, attr_name):
    """A dimension attribute (mediaSingle.width, media.width, media.height)
    as a float - ConversionError, not a bare traceback, for anything
    float() cannot parse.

    Deliberately separate from _parse_plain_number: that helper's plain
    whole-number rule is right for data-colwidth/colspan/rowspan/table
    width, which Confluence drops outright unless they are a plain integer
    with no unit. A mediaSingle or media dimension is different - it is
    genuinely float-valued in ADF (measured against a live site, Task 14:
    every real mediaSingle.width is a JSON number, and a resize in the
    Confluence editor can leave a non-integer pixel value) - so this parses
    the wider "number" shape rather than isdecimal()'s integer-only one,
    while still refusing a unit or anything else int()/float() cannot read
    with a named ConversionError rather than a raw traceback.
    """
    try:
        return float(raw)
    except ValueError:
        raise ConversionError(
            f'{attr_name}="{raw}" is not a number. Confluence drops '
            f"anything else rather than coercing it. Write a plain number, "
            f"never a unit."
        )


def _parse_plain_number(raw, attr_name):
    """A "plain number" HTML attribute as an int - ConversionError, not a
    bare traceback, for anything int() cannot parse.

    Shared by data-width, data-colwidth, colspan and rowspan. Only
    data-colwidth had this check before Task 11b; colspan and rowspan went
    straight to a bare int(a[key]) with nothing catching a malformed value,
    so "colspan=2.0" - exactly the shape a real page's own JSON float
    produces once rendered by _format_number's counterpart before this fix
    existed - escaped html_to_adf's ConversionError handling as a raw
    Python ValueError traceback, all the way out through main().

    isdecimal(), not isdigit(): isdigit() is true for characters int()
    still rejects - a superscript or subscript digit ("2"-superscript,
    "5"-subscript) reads as a digit to str.isdigit() but int() raises
    ValueError on it regardless, which would have reopened the exact
    bare-traceback gap this helper exists to close. isdecimal() is true
    only for characters that can actually form a decimal integer.
    """
    if not raw.isdecimal():
        raise ConversionError(
            f'{attr_name}="{raw}" is not a plain number. Confluence drops '
            f"anything else rather than coercing it. Write a plain whole "
            f"number, never a unit and never a decimal point."
        )
    return int(raw)


def _decode_adf(payload):
    """The exact node or mark an opaque HTML+ element's data-adf carried.

    Raises ConversionError rather than letting a malformed attribute reach
    the caller as a bare Python traceback - malformed or truncated base64,
    non-ASCII characters, or base64 that decodes to something other than
    JSON all take this path. Valid-but-wrong JSON is rejected too: every
    ADF node and mark this converter ever writes is a JSON object with a
    "type", so a bare number, a list or null did not come from _encode_adf
    and is not safe to build into the document as if it had.

    The mirror of _encode_adf, below the ADF-to-HTML+ direction. Kept next
    to _date_to_timestamp rather than its mirror, matching how this file
    already splits _date_to_timestamp (here) from _timestamp_to_iso
    (there) - each helper lives with the parsing direction that calls it.
    """
    try:
        raw = base64.b64decode(payload.encode("ascii"), validate=True)
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ConversionError(
            f"data-adf is not valid base64-encoded ADF JSON ({exc}). An "
            f"opaque element's data-adf attribute must be exactly what "
            f"_encode_adf produced - hand-editing it is not supported."
        )
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise ConversionError(
            'data-adf decoded to something other than an ADF node or mark '
            '(a JSON object with a "type"). Hand-editing an opaque '
            "element's data-adf attribute is not supported."
        )
    return value


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
        # Tag names of currently-open opaque (non-mark) elements, innermost
        # last. Everything the node carries already travelled in its
        # data-adf attribute, so nothing between the open and close tag is
        # meaningful - handle_data ignores it and handle_endtag swallows the
        # matching close tag rather than running it through the normal
        # per-tag handling (which would otherwise pop the real open block,
        # since "div" ordinarily closes one).
        self._opaque_stack = []
        # Mark objects pushed by an open <span data-type="adf-opaque-mark">,
        # matched back out of self.marks by identity (not by type, the way
        # INLINE_MARKS closes) when its </span> arrives - see handle_endtag.
        self._opaque_mark_stack = []
        # Tag names of currently-open media leaf elements, innermost last -
        # the same shape as _opaque_stack and for the same reason (Task 14).
        # <div data-type="media"> is a leaf: it never opens a real block, so
        # its own </div> must not fall into the generic
        # "div closes whatever _open pushed" handling in handle_endtag,
        # which would pop the mediaSingle (or whatever else) actually open
        # at that point instead. Also read by handle_data, which ignores
        # anything written between a media element's open and close tag -
        # the node is fully described by its attributes, so stray text or
        # pretty-printing whitespace there is not content.
        self._media_stack = []

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
            node = self.blocks.pop()
            # _open always sets content=[] on the way in, so every closing
            # block has the key - but a real fetched ADF document never
            # carries an empty content list on any node, checked across a
            # 40-page live sample (Task 11b): a childless node omits the key
            # entirely rather than keeping it empty. Emitting <p></p> for an
            # empty paragraph is correct HTML+; parsing that back in with an
            # empty content=[] still attached, where the original had no
            # content key at all, is not - it is the single most common
            # cause of a real page failing to round-trip byte-identical
            # (blank lines are ordinary paragraphs with nothing in them).
            if not node["content"]:
                del node["content"]

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
        # A list of dicts, deduplicated by (type, attrs), in the order they
        # were opened. Deduping by type alone merged two different opaque
        # marks of the same underlying ADF type into one text node's marks
        # list, silently losing every one but the first - harmless while an
        # opaque mark never reached self.marks, but overlapping inline
        # comments are exactly this shape: two "annotation" marks, same
        # type, different attrs (different comment ids), on the same run.
        out, seen = [], set()
        for m in self.marks:
            key = (m["type"], json.dumps(m.get("attrs"), sort_keys=True))
            if key not in seen:
                seen.add(key)
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

        elif tag == "br":
            # A hard break - what shift-enter produces in the Confluence
            # editor, so a genuinely common element on a real page, not an
            # edge case. adf_to_html already emits <br> for a hardBreak
            # node; without this branch the reverse direction had no way
            # back in, so any fetched page containing one became
            # uneditable through this skill - splicing a change and
            # converting back to ADF would reject the very HTML+ the
            # converter itself had just produced.
            self._append({"type": "hardBreak"})

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
            # localId defaults to "" (never authored by hand) but is read
            # back from data-local-id when present - see the taskItem
            # branch below for why this matters: proven live (Task 15),
            # not assumed from the media/caption precedent alone.
            self._open({"type": "taskList",
                        "attrs": {"localId": a.get("data-local-id", "")}})
        elif tag == "li" and dtype == "task-item":
            # Confluence assigns every taskItem a real localId on save,
            # even when the create request sent none (the enclosing
            # taskList's own localId is left empty instead - the two do
            # not behave the same way). Hardcoding "" here, as the brief
            # this converter came from did, meant a fetched page's taskItem
            # could never survive check-roundtrip: html_to_adf regenerated
            # "" in place of the id Confluence had assigned, and update's
            # round-trip gate refused every page with a task list on it -
            # proven live against a real site, not assumed (Task 15).
            self._open({"type": "taskItem",
                        "attrs": {"localId": a.get("data-local-id", ""),
                                  "state": "TODO"}})
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
            self._open({"type": "decisionList",
                        "attrs": {"localId": a.get("data-local-id", "")}})
        elif tag == "li" and dtype == "decision-item":
            state = a.get("data-state", "DECIDED")
            if state not in DECISION_STATES:
                raise ConversionError(
                    f'data-state="{state}" is not a decision state. '
                    f"Use DECIDED or UNDECIDED."
                )
            # Same localId treatment as taskItem above, and for the same
            # reason: decisionItem is Confluence's other server-assigned-id
            # list type, sibling to taskItem in every way that matters here.
            self._open({"type": "decisionItem",
                        "attrs": {"localId": a.get("data-local-id", ""),
                                  "state": state}})

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
                attrs_out["width"] = _parse_plain_number(a["data-width"], "data-width")
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
                cell_attrs["colwidth"] = [_parse_plain_number(raw, "data-colwidth")]
            for key, attr in (("colspan", "colspan"), ("rowspan", "rowspan")):
                if key in a:
                    cell_attrs[attr] = _parse_plain_number(a[key], key)
            frame = self._table_stack[-1]
            self._record_width(frame["cell_index"], raw)
            frame["cell_index"] += 1
            self._open({"type": node_type, "attrs": cell_attrs})

        elif tag == "figure" and dtype == "media-single":
            attrs_out = {"layout": a.get("data-layout", "center")}
            if "data-width" in a:
                attrs_out["width"] = _parse_float(a["data-width"], "data-width")
            if "data-width-type" in a:
                attrs_out["widthType"] = a["data-width-type"]
            self._open({"type": "mediaSingle", "attrs": attrs_out})

        elif tag == "div" and dtype == "media":
            media_id = a.get("data-id", "")
            collection = a.get("data-collection", "")
            if not media_id or not collection:
                raise ConversionError(
                    "A media node needs both data-id and data-collection. "
                    "Both come from attachments.sh upload - never invent one."
                )
            node_attrs = {
                "id": media_id,
                "type": a.get("data-media-type", "file"),
                "collection": collection,
            }
            if "data-alt" in a:
                node_attrs["alt"] = a["data-alt"]
            # width/height/localId are optional and round-trip only - there
            # is no reason to author them by hand, but a real fetched page
            # carries all three on nearly every media node (Task 14's
            # live-site measurement: width and height on 222/222 sampled
            # mediaSingle images, localId on 136/232), and dropping them
            # silently would fail the round-trip gate on every one.
            if "data-width" in a:
                node_attrs["width"] = _parse_float(a["data-width"], "data-width")
            if "data-height" in a:
                node_attrs["height"] = _parse_float(a["data-height"], "data-height")
            if "data-local-id" in a:
                node_attrs["localId"] = a["data-local-id"]
            # A leaf, like hardBreak/rule/status - appended through _append
            # (not the raw list.append the brief this came from used) so it
            # is checked against _check_nesting like everything else that
            # reaches the tree, and pushed onto _media_stack so its own
            # </div> is swallowed rather than closing whatever real block
            # happens to be open (see _media_stack's own comment).
            self._append({"type": "media", "attrs": node_attrs})
            self._media_stack.append(tag)

        elif tag == "figcaption":
            # Task 14 live-site measurement: 19 of 35 real captions carry
            # an attrs.localId Confluence assigned; the rest carry no attrs
            # key at all. Both shapes are preserved - an omitted key here,
            # not an empty {} placeholder, matching how the rest of this
            # file already treats an absent optional attribute (see
            # _close's own note on omitting rather than emitting empty).
            node = {"type": "caption"}
            if "data-local-id" in a:
                node["attrs"] = {"localId": a["data-local-id"]}
            self._open(node)

        elif tag in ("div", "span") and dtype == ADF_OPAQUE:
            # Rule 3 (Task 11b): the nesting validator does not inspect an
            # opaque node's contents and does not reject it for its position
            # - it came from a real page, so it was already valid where it
            # was. Appended straight to the open block's content rather than
            # through _open/_append, which would run it past
            # _check_nesting.
            node = _decode_adf(a.get("data-adf", ""))
            self.blocks[-1]["content"].append(node)
            self._opaque_stack.append(tag)

        elif tag == "span" and dtype == ADF_OPAQUE_MARK:
            # An opaque mark wraps ordinary, editable HTML+ - only the mark
            # itself is unrecognised, not the content it applies to (rule 5).
            # Reuse the normal open-marks stack so wrapped text picks it up
            # exactly the way a <strong> or <em> would.
            mark = _decode_adf(a.get("data-adf", ""))
            self.marks.append(mark)
            self._opaque_mark_stack.append(mark)

        else:
            raise ConversionError(
                f"<{tag}{' data-type=' + dtype if dtype else ''}> is not a "
                f"known HTML+ element."
            )

    def handle_endtag(self, tag):
        if self._opaque_stack and self._opaque_stack[-1] == tag:
            # The matching close of an opaque node's open tag - see the
            # ADF_OPAQUE branch in handle_starttag. Swallowed rather than
            # processed: an ordinary "div"/"span" close here would otherwise
            # run through the generic handling below and pop the real open
            # block, since this element never pushed one.
            self._opaque_stack.pop()
            return
        if self._media_stack and self._media_stack[-1] == tag:
            # The matching close of a media leaf's open tag - see the
            # "div"+"media" branch in handle_starttag. Swallowed for the
            # same reason as the opaque case just above: <div data-type=
            # "media"> never pushed a real block, so its own </div> must
            # not fall into the generic div-close handling below, which
            # would pop whatever block (the enclosing mediaSingle, most of
            # the time) actually is open.
            self._media_stack.pop()
            return
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
        elif tag == "span" and self._opaque_mark_stack:
            # Matched by identity, not by type the way INLINE_MARKS closes -
            # every opaque mark closes on the same tag ("span"), whatever its
            # underlying ADF type, so there is no type-to-tag lookup to use.
            mark = self._opaque_mark_stack.pop()
            for i in range(len(self.marks) - 1, -1, -1):
                if self.marks[i] is mark:
                    self.marks.pop(i)
                    break
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
        elif tag in ("figure", "figcaption"):
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
        if self._opaque_stack:
            # Nothing between an opaque node's open and close tag is
            # meaningful - its content already travelled in data-adf.
            return
        if self._media_stack:
            # A media element is fully described by its attributes and is
            # always written empty (<div ... ></div>); stray text or
            # pretty-printing whitespace between its open and close tag is
            # not content, the same as an opaque node just above.
            return
        if self._in_card:
            # A smart link renders its anchor text from the target, so any
            # text inside the element is discarded rather than emitted.
            return
        if self._in_time:
            return
        if self._pending_status is not None:
            # Accumulate every run rather than overwriting, so inline markup
            # inside the status (e.g. an <em>) does not drop the runs, or the
            # space between two of them, either side of it. Checked ahead of
            # the whitespace-only guard below rather than after it, or a
            # lone space between two marked runs inside the status would be
            # silently eaten before ever reaching this accumulation. Stripped
            # once when the <span> closes.
            self._pending_status["attrs"]["text"] += data
            return
        if self._in_summary:
            # Same accumulate-then-strip as status, above - and checked
            # ahead of the whitespace guard for the same reason.
            self.blocks[-1]["attrs"]["title"] += data
            return
        if self.blocks[-1] is self.doc:
            if not data.strip():
                # Whitespace between top-level blocks - formatting, not
                # content.
                return
            raise ConversionError(
                f"Loose text outside any block: {data.strip()[:40]!r}. "
                f"Wrap it in a <p>."
            )
        parent_type = self.blocks[-1].get("type")
        if parent_type in BLOCK_ONLY_PARENTS:
            if not data.strip():
                # Whitespace between block siblings inside a block-only
                # container - the newline between two <li>, <tr>, <td> or
                # <div data-type="column"> elements, say - is formatting
                # the same way it is at the document root, and has to
                # vanish the same way: keeping it would append a stray
                # text node straight into a container whose ADF content
                # model has no room for one.
                return
            raise ConversionError(
                f"{a_or_an(parent_type).capitalize()} {parent_type} takes "
                f"block content only, so it cannot hold bare text: "
                f"{data.strip()[:40]!r}. Wrap it in a <p>."
            )
        # Every other open block already holds inline content directly - a
        # paragraph, heading, task item, decision item or code block - so
        # whitespace here is not formatting, it is content: the gap between
        # "<strong>a</strong>" and "<em>b</em>" that keeps the two words
        # apart. Dropping it (the pre-Task-7-review behaviour, via a blanket
        # "if not data.strip(): return" at the top of this method) ran them
        # together on write-back.
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
    unclosed = None
    if len(builder.blocks) > 1:
        # Anything left on the stack besides the document itself never saw
        # its closing tag. Emitting it anyway would hand Confluence a node
        # still carrying private bookkeeping keys (or simply malformed
        # nesting) - fail the whole conversion instead, generically, rather
        # than special-casing any one element.
        unclosed = builder.blocks[-1]["type"]
    elif builder._opaque_stack:
        # The same problem, one level down: an opaque node was never pushed
        # onto builder.blocks (rule 6 - it has no editable children), so an
        # unclosed one is invisible to the check above. Left uncaught,
        # handle_data's "ignore everything while an opaque element is open"
        # guard keeps swallowing text for the rest of the fragment - every
        # paragraph after the break vanishes, silently, with a clean exit.
        unclosed = ADF_OPAQUE
    elif builder._opaque_mark_stack:
        unclosed = ADF_OPAQUE_MARK
    elif builder._media_stack:
        # The same problem again, one level down, for Task 14's media leaf:
        # a <div data-type="media"> never pushes onto builder.blocks either
        # (it is a leaf, like the opaque case above), so an unclosed one is
        # just as invisible to the first check, and handle_data's matching
        # "ignore everything while a media element is open" guard would
        # otherwise swallow every paragraph after the break the same way.
        unclosed = "media"
    if unclosed:
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

# The inline leaf types _inline_to_html already renders by name. Read by
# _node_to_html's own fallback (Task 11b) to tell "a known inline type
# reached in block position" - rare, arguably unreachable in a well-formed
# tree, but the pre-existing behaviour this file already had - apart from
# "a genuinely unrecognised type reached in block position", which now gets
# the opaque <div> form rather than being handed to _inline_to_html, where
# it would come back as a <span> and violate the block/inline distinction
# opaque passthrough is supposed to preserve.
_INLINE_LEAF_TYPES = {"text", "status", "date", "inlineCard", "hardBreak"}

# The attrs keys Task 14's mediaSingle/media/caption renderers each know how
# to write out. Read by _node_to_html to decide, per node, whether named
# rendering can represent this exact node completely - not just this node's
# type and required fields, but every attrs key it actually carries.
#
# Review finding on this task: occurrenceKey is a real, schema-documented
# ADF media attribute this model does not cover. A live site's media nodes
# happened not to carry it in the 289-page sample this task was measured
# against, so the gap did not show up there - but a media node that does
# carry it rendered through the named branch anyway before this set existed,
# silently dropping the attribute: round-tripping true under full opaque
# passthrough and false under named support that could not fully represent
# it. The fix is general, not "add occurrenceKey to the model" - that closes
# this one instance and leaves the same shape of bug waiting for the next
# attribute Atlassian ships. Instead: any attrs key outside the set below,
# on any of the three types, falls back to the existing opaque passthrough,
# the same as a type this converter has no named support for at all. That
# makes a guarantee worth stating rather than just hoping for: named support
# is never worse than opaque. A node the model fully understands gets a
# readable, authorable figure; a node carrying anything else - today or in
# some future ADF revision - degrades to a lossless blob rather than a lossy
# render, and check_roundtrip catches it as "safe to read, not safe to
# rewrite" if it ever somehow didn't.
_MEDIA_SINGLE_ATTRS = {"layout", "width", "widthType"}
_MEDIA_ATTRS = {"id", "type", "collection", "alt", "width", "height", "localId"}
_CAPTION_ATTRS = {"localId"}


def _escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))


def _timestamp_to_iso(ms):
    import datetime
    return datetime.datetime.fromtimestamp(
        int(ms) / 1000, tz=datetime.timezone.utc
    ).strftime("%Y-%m-%d")


def _encode_adf(value):
    """A node or mark's complete ADF JSON, base64-encoded for an HTML+ attribute.

    Base64 rather than escaped JSON, deliberately: ADF JSON carries a double
    quote on every key, and an HTML attribute containing quotes is fragile
    through any parser or editor that touches it. Base64 has no character
    that means anything to HTML - and being unreadable is the right signal
    that this is not something to hand-edit.
    """
    return base64.b64encode(
        json.dumps(value, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def _opaque_to_html(node, tag="div"):
    """An unrecognised ADF node, carried through untouched (Task 11b).

    tag is "div" for a node reached in block position - a direct child of a
    block-content list - and "span" for one reached in inline position,
    inside a paragraph, heading or similar. The position is what the caller
    already knows; the node's type gives no clue either way, since it is by
    definition one this converter does not recognise.
    """
    return f'<{tag} data-type="{ADF_OPAQUE}" data-adf="{_encode_adf(node)}"></{tag}>'


def _format_number(value):
    """A JSON number as a plain integer string, where that is safe.

    Found measuring this converter against a live Confluence instance
    (Task 11b), unrelated to opaque passthrough itself but blocking the same
    acceptance bar: real pages return table width, column width, colspan and
    rowspan as a JSON float even for whole values (1800.0, 200.0, 2.0) -
    never what html_to_adf itself writes, since it always stores
    _parse_plain_number(raw)'s int, but exactly what a fetched page carries
    on the way in. Rendered verbatim, that put "1800.0" or "2.0" in an
    attribute that is supposed to be a plain number; the parser's own
    _parse_plain_number two hundred lines away cannot read that back - so a
    table on a real page converted to HTML+ and then failed to convert back,
    breaking exactly the round trip this converter exists to guarantee. A
    whole-number float renders as a plain integer; anything else is left
    alone rather than guessed at. Covers all four numeric attributes this
    converter emits: width, colwidth, colspan, rowspan.
    """
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _opaque_mark_to_html(mark, inner):
    """An unrecognised mark, wrapping content that stays normal and editable.

    Only the mark is opaque (rule 5) - inner is whatever _inline_to_html
    already rendered for the node this mark sits on, marks already applied
    inside it, so wrapping it here is no different from wrapping it in a
    <strong> or <em> tag.
    """
    return f'<span data-type="{ADF_OPAQUE_MARK}" data-adf="{_encode_adf(mark)}">{inner}</span>'


def _inline_to_html(node):
    t = node.get("type")
    if t == "text":
        out = _escape(node["text"])
        # Marks are stored outer-to-inner, in the order the source tags
        # opened (html_to_adf pushes onto self.marks as each tag opens, so
        # the first mark in the list is the outermost). Applying them in
        # that same order here would wrap innermost-first instead - the
        # opposite sense - and reverse the nesting on every pass. Walking
        # the list in reverse re-applies the innermost mark first, which
        # rebuilds the original nesting.
        for mark in reversed(node.get("marks", [])):
            mt = mark["type"]
            if mt == "link":
                out = f'<a href="{mark["attrs"]["href"]}">{out}</a>'
            elif mt == "subsup":
                tag = mark["attrs"]["type"]
                out = f"<{tag}>{out}</{tag}>"
            elif mt in _MARK_TAGS:
                tag = _MARK_TAGS[mt]
                out = f"<{tag}>{out}</{tag}>"
            else:
                # An unrecognised mark - textColor, alignment, breakout and
                # more, measured against a live site (Task 11b). Previously
                # silently dropped here: none of the branches above matched,
                # so the mark simply never got applied and the run of text
                # lost its formatting on every fetch, with no warning. Now
                # wrapped instead, the same as a known mark, just opaquely.
                out = _opaque_mark_to_html(mark, out)
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
    # Reached for any inline ADF node type this converter does not know how
    # to render. Until Task 11b this raised: a real fetched Confluence page
    # can carry node types this converter has no HTML+ for (media, mention,
    # emoji, extension, nestedExpand and more, measured against a live
    # site), and refusing rather than silently dropping the node was the
    # right call while the only alternative was dropping it. Passthrough is
    # strictly better than both: the node is carried through untouched, so
    # reading is never refused and nothing is lost on the write-back path
    # either. Its own content is not reachable through this converter in
    # this version - the whole node is one opaque blob (rule 6) - which is a
    # known limit, not worked around here: a bodiedExtension holding
    # editable prose cannot be edited through this converter yet.
    return _opaque_to_html(node, tag="span")


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
        # localId round-trips through data-local-id - see the html_to_adf
        # side for why dropping it used to fail check-roundtrip on every
        # fetched page with a task list. attrs["localId"] is always present
        # coming from html_to_adf (possibly ""), so this always emits the
        # attribute rather than only when truthy - an absent attribute and
        # a present-but-empty one are different HTML, and only one of them
        # is what html_to_adf's own a.get("data-local-id", "") expects back.
        return (f'<ul data-type="task-list" data-local-id="{a.get("localId", "")}">'
                f"{_children_html(node)}</ul>")
    if t == "taskItem":
        checked = " checked" if a.get("state") == "DONE" else ""
        return (f'<li data-type="task-item" data-local-id="{a.get("localId", "")}">'
                f'<input type="checkbox"{checked}>{_inline_html(node)}</li>')
    if t == "decisionList":
        return (f'<ul data-type="decision-list" data-local-id="{a.get("localId", "")}">'
                f"{_children_html(node)}</ul>")
    if t == "decisionItem":
        return (f'<li data-type="decision-item" data-state="{a.get("state", "DECIDED")}" '
                f'data-local-id="{a.get("localId", "")}">'
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
            bits.append(f'data-width="{_format_number(a["width"])}"')
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
            bits.append(f'data-colwidth="{_format_number(a["colwidth"][0])}"')
        for key in ("colspan", "rowspan"):
            if key in a:
                bits.append(f'{key}="{_format_number(a[key])}"')
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
    if t == "mediaSingle" and set(a) <= _MEDIA_SINGLE_ATTRS:
        bits = [f'data-layout="{a.get("layout", "center")}"']
        if "width" in a:
            bits.append(f'data-width="{_format_number(a["width"])}"')
        if "widthType" in a:
            bits.append(f'data-width-type="{a["widthType"]}"')
        return (f'<figure data-type="media-single" {" ".join(bits)}>'
                f"{_children_html(node)}</figure>")
    if t == "media" and a.get("type", "file") == "file" \
            and "id" in a and "collection" in a and set(a) <= _MEDIA_ATTRS:
        # Named support only for the shape this converter can represent
        # completely: a file attachment with an id and a collection - what
        # attachments.sh upload actually produces - and no attrs key
        # outside _MEDIA_ATTRS (review finding on this task: a media node
        # carrying occurrenceKey, a real ADF attribute this model does not
        # cover, used to render named anyway and silently drop it). A media
        # node of any other shape (external type with no id/collection, or
        # any node carrying an attribute this model does not know) falls
        # through to the opaque branch at the end of this function instead,
        # the same as any other node this converter does not fully model -
        # never a KeyError on a["id"], and never a narrower, lossy render of
        # something this converter cannot round-trip completely.
        bits = [f'data-media-type="{a.get("type", "file")}"',
                f'data-id="{a["id"]}"', f'data-collection="{a["collection"]}"']
        if "alt" in a:
            bits.append(f'data-alt="{a["alt"]}"')
        if "width" in a:
            bits.append(f'data-width="{_format_number(a["width"])}"')
        if "height" in a:
            bits.append(f'data-height="{_format_number(a["height"])}"')
        if "localId" in a:
            bits.append(f'data-local-id="{a["localId"]}"')
        return f'<div data-type="media" {" ".join(bits)}></div>'
    if t == "caption" and set(a) <= _CAPTION_ATTRS:
        bits = f' data-local-id="{a["localId"]}"' if "localId" in a else ""
        return f"<figcaption{bits}>{_inline_html(node)}</figcaption>"
    if t in _INLINE_LEAF_TYPES:
        # A known inline leaf type reached in block position. Not something
        # a well-formed ADF tree produces - text/status/date/inlineCard/
        # hardBreak only ever sit inside a paragraph, heading or similar,
        # never as a direct child of a block-content list - but this is the
        # pre-existing fallback for it, kept rather than removed.
        return _inline_to_html(node)
    # A genuinely unrecognised type in block position - Task 11b passthrough,
    # carried through untouched as an opaque <div>. Checked after
    # _INLINE_LEAF_TYPES so a known inline type never gets wrapped as opaque
    # here, and delegates to _inline_to_html's own fallback (a <span>)
    # instead, only when reached that way.
    return _opaque_to_html(node)


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
    if t == "mediaSingle":
        return "\n".join(_node_to_md(c) for c in node.get("content", []))
    if t == "media":
        # .get(), not a["id"] - this rendering never raises (see the note
        # below), and Task 14's live-site measurement found a real media
        # node shape this converter has no named HTML+ for at all
        # ("external": a url, no id or collection). id first because that
        # is what an attachment actually is; url as the fallback for that
        # shape, so reading an external image still names something rather
        # than "attachment:None".
        ref = a.get("id") or a.get("url", "unknown")
        return f'![{a.get("alt", "image")}](attachment:{ref})'
    if t == "caption":
        return f"*{_inline_md(node)}*"
    # Deliberately not a ConversionError, unlike adf_to_html's equivalent
    # fallback. This rendering is documented one-way and read-only, never
    # written back, so there is no unsafe overwrite to guard against - an
    # agent trying to understand a page is better served by a page it can
    # read with the gap marked than by a hard failure over one node it
    # cannot render.
    return f"[unsupported node: {t}]"


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


# --- the Jira profile ---

# Node and mark types Confluence renders and Jira does not. A description
# carrying a Confluence-only node is accepted by the API and then shows as
# nothing on the issue; a Confluence-only mark would lose the formatting on
# the text it wraps. Either way it looks like it worked. Refused here
# instead. No mark type is in this set today - see the note below on why
# _walk still has to be able to reach one.
#
# This check (in html_to_adf_for_jira, below) runs against the tree
# html_to_adf already returned, not against the HTML+ source text - and that
# is what makes it safe against opaque passthrough (Task 11b) for a NODE,
# with no extra handling. An opaque HTML+ element's data-adf is decoded by
# _decode_adf before the node is ever appended to the tree (see the
# ADF_OPAQUE branch in _Builder.handle_starttag), so a status lozenge or a
# decision list smuggled in through
# <div data-type="adf-opaque" data-adf="..."> comes back out with its
# genuine "type" restored - "status", "decisionList" - exactly as if it had
# been written with the native <span data-type="status"> syntax.
#
# That property only protects this function where _walk actually looks -
# and the first version of _walk only descended into a node's "content",
# never into a text node's "marks". _decode_adf restores an opaque mark's
# genuine type the same way it restores an opaque node's (see the
# ADF_OPAQUE_MARK branch in the same method), so a Confluence-only mark
# smuggled in through <span data-type="adf-opaque-mark"> would have reached
# the tree correctly typed and then sailed straight past this check anyway,
# because nothing ever read the marks list it landed in. CONFLUENCE_ONLY
# holds no mark today, so nothing was actually smuggled by this gap - but
# the day a mark is added here, the refusal would have silently never
# fired, with no error to say why. _walk now yields a node's marks too,
# closing that before this set has a real mark member to expose it.
# Proven, not just argued, by TestJiraProfile below - for a node with named
# HTML+ support (status), for a node with none (bodiedExtension, which can
# only ever arrive opaquely), and for a mark forced into this set the same
# way (annotation, which likewise has no named HTML+ support here and so
# can also only ever arrive opaquely).
#
# bodiedExtension belongs in this set for a sharper reason than the other
# six: this converter has no named HTML+ syntax for it at all (unlike
# status/decisionList/decisionItem/expand/layoutSection/layoutColumn, which
# are also Confluence-only but do have one), so the only way a bodiedExtension
# node can ever reach this function is through the opaque wrapper. It is the
# case that actually exercises the passthrough path rather than merely
# being consistent with it.
CONFLUENCE_ONLY = {"status", "decisionList", "decisionItem", "expand",
                   "layoutSection", "layoutColumn", "bodiedExtension"}


def _walk(node):
    """Yield node, then every node reachable through its "content" list,
    and every mark reachable through its "marks" list.

    Walks the parsed ADF tree, which is what makes the opaque-passthrough
    case safe for both a smuggled node and a smuggled mark - see the
    CONFLUENCE_ONLY comment above. The marks branch matters even though a
    mark itself never has content or further marks of its own to recurse
    into: without it, a mark's restored type is never visited at all.
    """
    yield node
    for child in node.get("content", []):
        yield from _walk(child)
    for mark in node.get("marks", []):
        yield from _walk(mark)


def html_to_adf_for_jira(fragment):
    """Convert HTML+ to ADF for a Jira issue field, refusing Confluence nodes.

    Jira's ADF profile is narrower than Confluence's own. A status lozenge
    and a decision list are Confluence nodes; Jira renders neither, and a
    description containing one is accepted by the API and then displays as
    nothing at all - the worst kind of failure, because it looks like it
    worked. This is the same html_to_adf everything else in this file uses,
    with that one further check on the result.
    """
    doc = html_to_adf(fragment)
    for node in _walk(doc):
        t = node.get("type")
        if t in CONFLUENCE_ONLY:
            raise ConversionError(
                f"A {t} is a Confluence node and Jira does not render it. "
                f"The API would accept the description and show nothing. "
                f"Use a panel, a table, a code block or a task list instead."
            )
    return doc


# --- the write-path round-trip gate ---

def _first_roundtrip_difference(original, roundtripped):
    """The ADF "type" of the node closest to where original and
    roundtripped first disagree, or None if they are identical.

    Walks both trees together and returns the type of the innermost real
    ADF node still common to both paths when the walk hits a difference -
    never a value, only the type name, so this is safe to put in a refusal
    message without carrying page content into it.

    Only a dict reached by walking down a "content" or "marks" list is a
    real ADF node whose own "type" is meaningful to report - the review
    round found that adopting a["type"] at any depth, including inside
    "attrs", was wrong: a subsup mark's attrs is {"type": "sub"} or
    {"type": "sup"}, and that "type" is an attribute value, not a node
    type, but the same string either way, so the naive version reported
    "sub" as if it were an ADF node type. is_node tracks whether the
    current dict was reached that way; it is only carried onward through a
    "content" or "marks" key, so a dict found via "attrs" (or anything
    else) never adopts its own "type" field, however it is spelled.

    is_node is AND-ed with the key check, not just set from it: a second
    review pass found that a bare key-name match let a literal "content" or
    "marks" key sitting inside "attrs" re-arm is_node one level down, so a
    node type manufactured inside an attrs value ({"attrs": {"content":
    [{"type": "leak-attempt", ...}]}}) could still surface. Once is_node is
    False - once the walk is inside a real node's attrs - nothing nested
    under it is a real node either, however its keys happen to be spelled,
    so False has to stay False for the rest of that subtree.
    """
    def walk(a, b, nearest_type, is_node=True):
        if isinstance(a, dict) and isinstance(b, dict):
            here = (a.get("type") if is_node and isinstance(a.get("type"), str)
                    else nearest_type)
            if set(a.keys()) != set(b.keys()):
                return here
            for key in a:
                diff = walk(a[key], b[key], here,
                             is_node=(is_node and key in ("content", "marks")))
                if diff is not None:
                    return diff
            return None
        if isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                return nearest_type
            for x, y in zip(a, b):
                diff = walk(x, y, nearest_type, is_node=is_node)
                if diff is not None:
                    return diff
            return None
        if a != b:
            return nearest_type
        return None

    return walk(original, roundtripped, None)


def check_roundtrip(doc):
    """(ok, differing_type) - whether doc survives adf_to_html then
    html_to_adf unchanged, and if not, the ADF type nearest the first
    place it does not.

    "Unchanged" is Python value equality (==), not byte-for-byte string
    identity - deliberately. width: 1800.0 coming back as width: 1800 is
    exactly what _format_number exists to do, and 1800.0 == 1800 is True,
    so it counts as unchanged here, correctly; refusing that would make
    this converter's own acceptance bar unreachable on a real page.

    The write-path gate this exists for: UPDATE REPLACES THE WHOLE BODY, so
    a fetch/splice/verify update is only as safe as this converter's
    round-trip fidelity on the page actually being replaced. Before Task
    11b a page carrying an unrecognised node or mark simply refused to
    convert at all - loud, but safe, since nothing was ever written. Opaque
    passthrough lets those pages convert now; for the ones that still are
    not identical under this comparison (a known type dropping an attr or
    a mark this converter has no HTML+ for), that refusal has to be
    reproduced deliberately here, or reading now succeeds where it used to
    fail and writing silently drops whatever this converter could not
    carry through - the exact failure Task 7 closed for an
    outright-unsupported node, reappearing one level down for a
    partially-supported one. Does not itself decide whether to write; that
    is confluence-pages.sh's call.
    """
    roundtripped = html_to_adf(adf_to_html(doc))
    if roundtripped == doc:
        return True, None
    return False, _first_roundtrip_difference(doc, roundtripped) or "document"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command",
        choices=["to-adf", "to-adf-jira", "to-html", "to-markdown", "check-roundtrip"],
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "to-adf":
            doc = html_to_adf(sys.stdin.read())
            json.dump(doc, sys.stdout, separators=(",", ":"))
        elif args.command == "to-adf-jira":
            doc = html_to_adf_for_jira(sys.stdin.read())
            json.dump(doc, sys.stdout, separators=(",", ":"))
        elif args.command == "to-html":
            sys.stdout.write(adf_to_html(json.load(sys.stdin)))
        elif args.command == "to-markdown":
            sys.stdout.write(adf_to_markdown(json.load(sys.stdin)))
        elif args.command == "check-roundtrip":
            # Reads the page's current ADF (exactly what the API returned)
            # on stdin. Silent on success, so confluence-pages.sh's update
            # gate can tell "safe to proceed" from "refuse" by exit code
            # alone. The inner try/except is this command's own: its caller
            # is a bash gate that prints whatever reaches stderr as a
            # "Cause:" line verbatim, so a raw traceback there is not just a
            # worse error, it is twelve lines of Python in a refusal
            # message someone is expected to read and act on. A
            # ConversionError from the conversion itself (an
            # outright-unsupported node, or the colwidth-consistency check
            # firing on a real page nobody edited) passes through unchanged
            # - the page is not safe to write back either way. Anything
            # else - a null body, a body that is not JSON at all - is not a
            # shape this command explains on its own, so it collapses to
            # one line instead of reaching the caller as a stack trace.
            try:
                ok, differing_type = check_roundtrip(json.load(sys.stdin))
            except ConversionError:
                raise
            except Exception as exc:
                raise ConversionError(
                    f"the page body could not be checked "
                    f"({type(exc).__name__}: {exc})."
                )
            if not ok:
                raise ConversionError(
                    f'a "{differing_type}" node does not survive converting '
                    f"to HTML+ and back to ADF unchanged."
                )
    except ConversionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
