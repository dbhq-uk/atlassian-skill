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

# "custom" joined the fixed five after a live-site measurement found real
# panels with panelType="custom" - Confluence's own emoji-and-colour panel,
# carrying panelIconId/panelIcon/panelIconText/panelColor alongside it. Before
# this, a fetched page with one raised inside check_roundtrip's own internal
# conversion (adf_to_html wrote data-type="panel-custom" verbatim with no
# validation on the way out; html_to_adf then refused it on the way back in,
# since "custom" was not a member here) rather than returning the ordinary
# (False, "panel") the gate is meant to hand back for anything it cannot
# carry - the one shape of failure that reached a caller as a raw
# ConversionError out of check_roundtrip instead of a refusal it could act on.
PANEL_TYPES = {"info", "note", "success", "warning", "error", "custom"}

# The extra attrs a custom panel carries, beyond the panelType every panel
# has. Confluence assigns panelIconId/panelIcon/panelColor together as a set
# (an editor pick: the emoji and the background colour); panelIconText is
# optional even then - a live-site measurement found it on some custom
# panels and not others; see the panel branches in _Builder.handle_starttag
# and _node_to_html.
_CUSTOM_PANEL_ATTRS = {"panelIconId", "panelIcon", "panelIconText", "panelColor"}
STATUS_COLOURS = {"neutral", "purple", "blue", "red", "yellow", "green"}
DECISION_STATES = {"DECIDED", "UNDECIDED"}
CARD_TYPES = {"inline": "inlineCard", "block": "blockCard", "embed": "embedCard"}

# Opaque passthrough. An ADF node or mark this converter does not know by
# name is carried through untouched rather than refused (a block or inline
# node) or silently dropped (a mark) - see _opaque_to_html and
# _opaque_mark_to_html below, and the _Builder branches that read the two
# data-type values back. Real Confluence pages carry far more node and mark
# types than this converter has named support for (media, extension,
# textColor, alignment, breakout and more, measured against a live site) -
# passthrough is the floor that lets any page be read and edited around the
# parts this converter cannot yet render, not a replacement for adding named
# support where it is worth having (as it was for mediaSingle/media).
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
    # and media belong in this set for the same reason: a figure is a
    # block, exactly like a table or a panel, and cannot sit inside a
    # paragraph either.
    "paragraph": {"blockCard", "embedCard", "table", "panel", "expand",
                  "layoutSection", "heading", "rule", "mediaSingle", "media"},
    # Inline-content-only containers: any block child at all is a violation.
    "taskItem": None,
    "decisionItem": None,
    "heading": None,
    # A caption's content model is inline text only (the same shape as a
    # heading or a task item), not the codeBlock TEXT_ONLY sentinel below
    # it - a caption can still hold, say, a status lozenge.
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
# Widened from the original five (listItem, tableCell, tableHeader, panel,
# blockquote) to every other container whose content model is exactly one
# kind of block child and nothing else - taskList
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
# mediaSingle belongs in this set for the same reason: its content is a
# media leaf plus an optional caption, block-only the same way a listItem's
# is, and every real published figure this converter was checked against,
# in a live-site round-trip measurement, is written pretty-printed - a
# newline and indentation between <div data-type="media"> and
# <figcaption>. Without this entry that whitespace is content, not
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
    whole-number rule is right for colspan/rowspan/an ordered list's start
    number/a paragraph's indent level - counts, never genuinely fractional,
    whoever writes them. A pixel dimension the editor itself computed is
    different: mediaSingle/media width and height, a table's own width, a
    table cell's colwidth, and a breakout's width are all genuinely
    float-valued in real ADF - a live-site measurement of a
    different, older population of real pages found column widths like
    230.4 and 253.44, not just whole-pixel values, and every one of those
    pages' own values had always been server-computed, never typed by an
    author. _parse_plain_number rejecting them was this converter refusing
    its own honest output: correctly built to catch someone hand-writing
    242px or 50%, wrongly applied to a value nobody typed. So this parses
    the wider "number" shape rather than isdecimal()'s integer-only one,
    while still refusing a unit or anything else int()/float() cannot read
    with a named ConversionError rather than a raw traceback - the
    authoring guard is unchanged, only where it runs.
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

    Shared by colspan, rowspan, an ordered list's start number and a
    paragraph's indent level - every one a count, not a dimension, so a
    genuine fraction is never valid for any of them, only ever a sign of a
    hand-written mistake (242px, 50%) or a unit Confluence would drop
    silently. data-width and data-colwidth used to be here too; moved to
    _parse_float once a live-site measurement found real, server-computed
    fractional pixel widths on both (230.4, 253.44) - this helper's job is
    catching an authoring mistake, and a value nobody typed by hand is
    never that, whichever attribute it turns up on.

    colspan and rowspan went straight to a bare int(a[key]) with nothing
    catching a malformed value at first, so "colspan=2.0" - exactly the
    shape a real page's own JSON float produces once rendered by
    _format_number's counterpart - escaped html_to_adf's ConversionError
    handling as a raw Python ValueError traceback, all the way out through
    main().

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


def _breakout_marks(a):
    """A one-element marks list for a node's optional breakout mode/width,
    read back from data-breakout-mode/data-breakout-width - or [] when the
    element carries neither, so a caller can splice this straight onto a
    fresh node's "marks" key without inventing one nothing needs.

    breakout is a live-site-measured node mark (not an attrs entry) found on
    codeBlock, expand and layoutSection alike - the editor's "make this wide"
    toggle. Shared by all three callers rather than three separate
    near-duplicates, since the shape (mode, an optional pixel width) is the
    same wherever it appears.
    """
    if "data-breakout-mode" not in a:
        return []
    mark = {"type": "breakout", "attrs": {"mode": a["data-breakout-mode"]}}
    if "data-breakout-width" in a:
        # A pixel width the editor computed on resize, the same shape as a
        # table's own width or a colwidth - _parse_float, not
        # _parse_plain_number, so a genuine fraction is not refused as if
        # it were a hand-authoring mistake.
        mark["attrs"]["width"] = _parse_float(
            a["data-breakout-width"], "data-breakout-width"
        )
    return [mark]


def _paragraph_marks(a):
    """A paragraph's optional alignment/indentation node marks, read back
    from data-align/data-indent-level - the editor's centre/indent toggles,
    a live-site measurement found on a real minority of paragraphs (roughly
    one in fifty, against one in three for localId alone). Returns [] when
    the element carries neither.
    """
    marks = []
    if "data-align" in a:
        marks.append({"type": "alignment", "attrs": {"align": a["data-align"]}})
    if "data-indent-level" in a:
        marks.append({
            "type": "indentation",
            "attrs": {"level": _parse_plain_number(
                a["data-indent-level"], "data-indent-level"
            )},
        })
    return marks


def _set_local_id(attrs_out, a):
    """Copy localId from data-local-id/data-local-id-null into attrs_out,
    preserving all three states real ADF actually has - not the two an
    "in a" check alone can tell apart.

    A live-site measurement, re-run after the fact against a page this
    converter had already been declared to pass, found the third: a real
    taskItem with localId: null (a JSON null Confluence itself sent, not
    a missing key and not an empty string) round-tripped to localId: ""
    instead - html_to_adf had never read a value back distinctly from
    "attribute absent", so the only two encodings available were "the key
    is missing" and "the key is a string", and a JSON null had nowhere
    left to go but the empty string.

    So the key is absent (this function is a no-op - attrs_out is left
    exactly as the caller built it, gaining no "localId" key at all), the
    key is present holding a real value (the ordinary data-local-id="..."
    case), or the key is present holding null - and null gets its own
    valueless boolean attribute, data-local-id-null, checked first, rather
    than folding into data-local-id's own value: a value of the literal
    four-character string "None" (or "null") would be indistinguishable
    from the real null this exists to carry. See _local_id_attr, this
    function's mirror on the way out (ADF to HTML+), for the same
    three-way split from the other direction. Every one of the fourteen
    node types that carry localId reads it back through this one function,
    so the fix and its reasoning live in exactly one place rather than
    fourteen near-identical copies of the same three-line "if" this
    replaced.
    """
    if "data-local-id-null" in a:
        attrs_out["localId"] = None
    elif "data-local-id" in a:
        attrs_out["localId"] = a["data-local-id"]


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
        # {"next_col": int, "occupied": {column index: rows still covered},
        # "carried_over": {column indices occupied before the open row},
        # "widths": {column index: set of widths seen}}, where None in a
        # widths set means "no attribute" - see the "table"/"tr"/"th"/"td"
        # branches of handle_starttag and handle_endtag for how next_col and
        # occupied track the real grid column under colspan/rowspan, not
        # just the count of <td>/<th> tags seen. Scoped per table rather
        # than one flat set of attributes, so a table nested inside another
        # table's cell tracks its own columns without corrupting or being
        # corrupted by the columns of the table it sits in.
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
        # the same shape as _opaque_stack and for the same reason.
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
            # 40-page live sample: a childless node omits the key
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
            # localId, alignment and indentation are all round-trip
            # bookkeeping-or-formatting a live-site measurement found on a
            # third of real paragraphs (localId) and a smaller but real
            # share (the two marks, the editor's centre/indent toggles) -
            # carried the same way media's localId already is, so a page
            # with either is still a real, editable <p> rather than an
            # opaque blob. None is invented when hand-authoring a fresh
            # paragraph: leave them off and either Confluence assigns its
            # own id, or there is simply no alignment/indent to have.
            node = {"type": "paragraph"}
            attrs = {}
            _set_local_id(attrs, a)
            if attrs:
                node["attrs"] = attrs
            marks = _paragraph_marks(a)
            if marks:
                node["marks"] = marks
            self._open(node)
        elif tag in HEADINGS:
            attrs = {"level": HEADINGS[tag]}
            _set_local_id(attrs, a)
            self._open({"type": "heading", "attrs": attrs})
        elif tag == "code" and self.blocks[-1]["type"] == "codeBlock":
            # Inside a <pre>, <code> carries the language rather than an
            # inline mark - checked ahead of the generic INLINE_MARKS branch
            # below, which would otherwise claim "code" first every time.
            # setdefault, not a bare [...] assignment: <pre> no longer
            # always creates an "attrs" key (see its own branch below), so
            # a codeBlock with a language class but no localId/breakout
            # reaches here with none yet.
            for cls in a.get("class", "").split():
                if cls.startswith("language-"):
                    self.blocks[-1].setdefault("attrs", {})["language"] = (
                        cls[len("language-"):]
                    )
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
            attrs = {"panelType": kind}
            _set_local_id(attrs, a)
            # The custom-panel attrs (icon id, icon, icon text, colour) are
            # an editor pick, round-trip bookkeeping the same as a media id -
            # not something to invent when hand-authoring a plain panel, so
            # each is carried only when the source actually has it.
            if "data-panel-icon-id" in a:
                attrs["panelIconId"] = a["data-panel-icon-id"]
            if "data-panel-icon" in a:
                attrs["panelIcon"] = a["data-panel-icon"]
            if "data-panel-icon-text" in a:
                attrs["panelIconText"] = a["data-panel-icon-text"]
            if "data-panel-color" in a:
                attrs["panelColor"] = a["data-panel-color"]
            self._open({"type": "panel", "attrs": attrs})

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
            _set_local_id(self._pending_status["attrs"], a)
            self._append(self._pending_status)

        elif tag == "ul" and dtype == "task-list":
            # localId round-trips through data-local-id when present - see
            # the taskItem branch below for why this matters: proven live,
            # not assumed from the media/caption precedent alone. The key
            # is omitted rather than defaulted to "" when absent - a node
            # authored fresh, with no data-local-id at all, used to gain
            # attrs: {"localId": ""} anyway, which is not what the source
            # had and fails check-roundtrip against anything that
            # genuinely carries no localId key (a hand-authored ADF
            # fixture; conceivably a future API response). caption below
            # already gets this right - no attrs key at all when it has
            # nothing to hold - and taskList has no other attrs, so the
            # same omission applies to the whole attrs key, not just the
            # value inside it.
            node = {"type": "taskList"}
            attrs = {}
            _set_local_id(attrs, a)
            if attrs:
                node["attrs"] = attrs
            self._open(node)
        elif tag == "li" and dtype == "task-item":
            # Confluence assigns every taskItem a real localId on save,
            # even when the create request sent none (the enclosing
            # taskList's own localId is left empty instead - the two do
            # not behave the same way). Hardcoding "" here, as an earlier
            # version of this converter did, meant a fetched page's
            # taskItem could never survive check-roundtrip: html_to_adf
            # regenerated "" in place of the id Confluence had assigned,
            # and update's round-trip gate refused every page with a task
            # list on it - proven live against a real site, not assumed.
            # Unlike taskList, taskItem's attrs always exists (it holds
            # state too), so only the localId key inside it is conditional.
            attrs = {"state": "TODO"}
            _set_local_id(attrs, a)
            self._open({"type": "taskItem", "attrs": attrs})
        elif tag == "input":
            # The checkbox carries the state of the task item it sits in -
            # checked or not, it must be inside one. Only the checked case
            # validated this at first: an unchecked <input type="checkbox">
            # outside a task-list item was silently dropped instead of
            # refused - the one shape of this element that reached no
            # error and no output at all, rather than either a task or a
            # clear refusal.
            if self.blocks[-1].get("type") != "taskItem":
                raise ConversionError(
                    "<input type=\"checkbox\"> found outside a task-list "
                    'item. A checkbox belongs in a task-list item: <li '
                    'data-type="task-item">.'
                )
            if "checked" in a:
                self.blocks[-1]["attrs"]["state"] = "DONE"

        elif tag == "ul" and dtype == "decision-list":
            # Same treatment as taskList above, for the same reason.
            node = {"type": "decisionList"}
            attrs = {}
            _set_local_id(attrs, a)
            if attrs:
                node["attrs"] = attrs
            self._open(node)
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
            attrs = {"state": state}
            _set_local_id(attrs, a)
            self._open({"type": "decisionItem", "attrs": attrs})

        elif tag == "details":
            attrs = {"title": ""}
            _set_local_id(attrs, a)
            node = {"type": "expand", "attrs": attrs}
            marks = _breakout_marks(a)
            if marks:
                node["marks"] = marks
            self._open(node)
        elif tag == "summary":
            self._in_summary = True

        elif tag == "pre":
            # No "language" default here any more - a live-site measurement
            # found real codeBlocks never carry one at all (0 of 49 sampled;
            # 28 had no "attrs" key whatsoever). The <code class=
            # "language-..."> branch above fills it in when a language is
            # actually given; without one, this omits the key exactly the
            # way Confluence's own editor does for an unlabelled block,
            # rather than inventing "plaintext" as if it had been chosen.
            node = {"type": "codeBlock"}
            attrs = {}
            _set_local_id(attrs, a)
            if attrs:
                node["attrs"] = attrs
            marks = _breakout_marks(a)
            if marks:
                node["marks"] = marks
            self._open(node)

        elif tag == "time":
            attrs = {"timestamp": _date_to_timestamp(a.get("datetime", ""))}
            _set_local_id(attrs, a)
            self._append({"type": "date", "attrs": attrs})
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
                attrs = {"url": href}
                _set_local_id(attrs, a)
                node = {"type": CARD_TYPES[appearance], "attrs": attrs}
                self._append(node)
                self._in_card = True
            else:
                self.marks.append({"type": "link", "attrs": {"href": href}})

        elif tag == "section" and dtype in LAYOUTS:
            node = {"type": "layoutSection", "_expected": LAYOUTS[dtype],
                    "_layout": dtype}
            marks = _breakout_marks(a)
            if marks:
                node["marks"] = marks
            self._open(node)
        elif tag == "div" and dtype == "column":
            # An even split is the fresh-authoring default (the shape this
            # converter has always produced with nothing else to go on),
            # but a real column's width is not always even - a live-site
            # measurement found columns dragged to 66.66/33.33, not just
            # 50/50 - so an explicit data-width is read back verbatim
            # rather than the even split always overriding whatever the
            # source actually had. width is the "content" a reader drags
            # the column divider to set, exactly the shape an ordered
            # list's start number or a code block's language already are:
            # worth carrying by hand, not just round-trip bookkeeping.
            parent = self.blocks[-1]
            if "data-width" in a:
                width = _parse_float(a["data-width"], "data-width")
            else:
                width = round(100.0 / parent.get("_expected", 1), 2)
            self._open({"type": "layoutColumn", "attrs": {"width": width}})

        elif tag == "hr":
            node = {"type": "rule"}
            attrs = {}
            _set_local_id(attrs, a)
            if attrs:
                node["attrs"] = attrs
            self._append(node)

        elif tag in SIMPLE_BLOCKS:
            # bulletList, orderedList, listItem and blockquote all carry a
            # localId on a fetched page (a live-site measurement: common on
            # every one of the four), so all four take it the same
            # conditional way as taskList/decisionList already do. order is
            # orderedList's own: the number the list starts counting from,
            # read back from the plain HTML "start" attribute rather than a
            # data-* one, since it is exactly HTML's own start="N" and an
            # author might reasonably set it by hand - unlike a localId,
            # nobody hand-authors.
            node = {"type": SIMPLE_BLOCKS[tag]}
            attrs = {}
            _set_local_id(attrs, a)
            if tag == "ol" and "start" in a:
                attrs["order"] = _parse_plain_number(a["start"], "start")
            if attrs:
                node["attrs"] = attrs
            self._open(node)

        elif tag == "table":
            # attrs is omitted entirely, not left as an empty {}, when the
            # table has none of the below - a bare <table> with no
            # data-width/layout/localId etc. is a real, live-measured
            # shape (most tables on at least one real page carried none at
            # all), and an unconditional attrs_out here, as an earlier
            # version of this converter had, produced "attrs": {} for
            # every one of them: present-but-empty and absent are
            # different ADF, the same distinction _close already applies
            # to "content" and every other optional-attrs branch in this
            # file already applies to its own attrs dict.
            attrs_out = {}
            if "data-width" in a:
                # A table's own pixel width, editor-computed like colwidth
                # below - _parse_float, not _parse_plain_number, for the
                # same reason (see _parse_plain_number's own docstring).
                attrs_out["width"] = _parse_float(a["data-width"], "data-width")
            if "data-layout" in a:
                attrs_out["layout"] = a["data-layout"]
            if a.get("data-number-column") == "true":
                attrs_out["isNumberColumnEnabled"] = True
            if a.get("data-display-mode"):
                attrs_out["displayMode"] = a["data-display-mode"]
            _set_local_id(attrs_out, a)
            node = {"type": "table"}
            if attrs_out:
                node["attrs"] = attrs_out
            self._open(node)
            # next_col: the column cursor for the row currently being
            # parsed, reset at each <tr>. occupied: column index -> number
            # of further rows, beyond the row a rowspan started in, that
            # column is still covered - the grid-tracking state a naive
            # per-<td> counter does not have, and needs to, because a
            # rowspan removes a cell from every row after the one it opened
            # in. carried_over: the subset of occupied's keys that predate
            # the row currently open - see the <tr> open/close handling
            # below for why only that subset gets decremented once the row
            # ends, not every column occupied[col] happened to gain during
            # it.
            self._table_stack.append({
                "next_col": 0, "occupied": {}, "carried_over": set(),
                "widths": {},
            })
        elif tag in ("thead", "tbody", "tfoot"):
            # Not ADF nodes. Rows sit directly on the table.
            pass
        elif tag == "tr":
            if not self._table_stack:
                raise ConversionError(
                    "<tr> found outside a <table>. A row must sit inside a "
                    "table."
                )
            node = {"type": "tableRow"}
            attrs = {}
            _set_local_id(attrs, a)
            if attrs:
                node["attrs"] = attrs
            self._open(node)
            frame = self._table_stack[-1]
            frame["next_col"] = 0
            # Snapshot which columns are occupied by a rowspan that started
            # in an earlier row, before this row's own cells can add any
            # more - see the </tr> handling in handle_endtag, which
            # decrements only these, not the whole occupied dict.
            frame["carried_over"] = set(frame["occupied"])
        elif tag in ("th", "td"):
            if not self._table_stack:
                raise ConversionError(
                    f"<{tag}> found outside a <table>. A cell must sit "
                    f"inside a table."
                )
            node_type = "tableHeader" if tag == "th" else "tableCell"
            frame = self._table_stack[-1]

            cell_attrs = {}
            colspan = 1
            if "colspan" in a:
                colspan = _parse_plain_number(a["colspan"], "colspan")
                cell_attrs["colspan"] = colspan
            rowspan = 1
            if "rowspan" in a:
                rowspan = _parse_plain_number(a["rowspan"], "rowspan")
                cell_attrs["rowspan"] = rowspan

            # The real grid column this cell starts at - skipping past any
            # column an earlier row's rowspan still covers. A naive count of
            # <td>/<th> tags actually seen in this row (what an earlier
            # version of this converter did) puts the wrong column index on
            # every cell after the first gap a rowspan leaves, and that
            # misattribution is what made _check_column_widths see two
            # different widths on what was really one consistent column -
            # a real table with a vertically merged cell, refused with a
            # false "two different data-colwidth values" - proven against a
            # live site, not a hypothetical.
            while frame["occupied"].get(frame["next_col"], 0) > 0:
                frame["next_col"] += 1
            col_index = frame["next_col"]

            # colwidth is one value per column the cell spans, comma
            # separated when colspan > 1 - real ADF carries an array here,
            # one entry per spanned column, not a single number; rendering
            # only the first (an earlier version of this converter did)
            # silently truncated every wider table's merged-cell columns on
            # the way out, failing the round-trip gate on the way back in.
            raw = a.get("data-colwidth")
            raw_values = raw.split(",") if raw is not None else None
            if raw_values is not None:
                # A pixel width the editor computed, not a count - a live
                # site measurement of an older, otherwise-untouched
                # population of real pages found genuinely fractional
                # values here (230.4, 253.44), which _parse_plain_number's
                # whole-number-only rule refused outright. _parse_float,
                # like table's own width and a breakout's width above, for
                # the same reason: see _parse_plain_number's own docstring.
                # 242px and 50% are still refused, with the same message,
                # because float() cannot parse either any more than int()
                # could - only the genuine fraction a hand-typed value can
                # never honestly be is now allowed through.
                cell_attrs["colwidth"] = [
                    _parse_float(v, "data-colwidth") for v in raw_values
                ]
            for i in range(colspan):
                value = raw_values[i] if raw_values and i < len(raw_values) else None
                self._record_width(col_index + i, value)

            if rowspan > 1:
                for i in range(colspan):
                    frame["occupied"][col_index + i] = rowspan - 1
            frame["next_col"] = col_index + colspan

            _set_local_id(cell_attrs, a)
            if "data-background" in a:
                cell_attrs["background"] = a["data-background"]
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
            # carries all three on nearly every media node (a live-site
            # measurement found width and height on 222/222 sampled
            # mediaSingle images, localId on 136/232), and dropping them
            # silently would fail the round-trip gate on every one.
            if "data-width" in a:
                node_attrs["width"] = _parse_float(a["data-width"], "data-width")
            if "data-height" in a:
                node_attrs["height"] = _parse_float(a["data-height"], "data-height")
            _set_local_id(node_attrs, a)
            # A leaf, like hardBreak/rule/status - appended through _append
            # (not a raw list.append) so it is checked against
            # _check_nesting like everything else that reaches the tree,
            # and pushed onto _media_stack so its own </div> is swallowed
            # rather than closing whatever real block happens to be open
            # (see _media_stack's own comment).
            self._append({"type": "media", "attrs": node_attrs})
            self._media_stack.append(tag)

        elif tag == "figcaption":
            # A live-site measurement found 19 of 35 real captions carry
            # an attrs.localId Confluence assigned; the rest carry no attrs
            # key at all. Both shapes are preserved - an omitted key here,
            # not an empty {} placeholder, matching how the rest of this
            # file already treats an absent optional attribute (see
            # _close's own note on omitting rather than emitting empty).
            node = {"type": "caption"}
            attrs = {}
            _set_local_id(attrs, a)
            if attrs:
                node["attrs"] = attrs
            self._open(node)

        elif tag in ("div", "span") and dtype == ADF_OPAQUE:
            # Rule 3: the nesting validator does not inspect an
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
            if tag == "tr" and self._table_stack:
                # One row consumed: every column that was already occupied
                # before this row's own cells were placed (carried_over, set
                # at <tr> open) has one fewer future row left to cover - not
                # every column in occupied, which by now may also hold
                # entries a rowspan starting in THIS row just added, and
                # those must survive untouched into the row after this one,
                # not be consumed by the row that created them.
                frame = self._table_stack[-1]
                for col in frame["carried_over"]:
                    remaining = frame["occupied"].get(col)
                    if remaining is not None:
                        if remaining <= 1:
                            del frame["occupied"][col]
                        else:
                            frame["occupied"][col] = remaining - 1
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
        # apart. Dropping it (this method's earlier behaviour, via a blanket
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
        # The same problem again, one level down, for the media leaf:
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
# _node_to_html's own fallback to tell "a known inline type
# reached in block position" - rare, arguably unreachable in a well-formed
# tree, but the pre-existing behaviour this file already had - apart from
# "a genuinely unrecognised type reached in block position", which now gets
# the opaque <div> form rather than being handed to _inline_to_html, where
# it would come back as a <span> and violate the block/inline distinction
# opaque passthrough is supposed to preserve.
_INLINE_LEAF_TYPES = {"text", "status", "date", "inlineCard", "hardBreak"}

# The attrs keys the mediaSingle/media/caption renderers each know how
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


def _fully_modelled(node, known_attrs, known_marks=frozenset()):
    """Whether every attrs key and every node-level mark on this node is one
    its named HTML+ renderer actually represents.

    Generalises the completeness check above (built for
    mediaSingle/media/caption alone, after the occurrenceKey finding) to
    every other named node type this converter renders: a live-site
    measurement of a real Confluence site found localId alone on eleven more
    node types than the six that already carried it, plus background on
    table cells, order on orderedList, and a breakout node mark on
    codeBlock/expand/layoutSection - none representable before this. Rather
    than enumerate each one's presence or absence as it comes up, every
    named renderer now states what it models and defers to the same rule:
    known attrs and marks render named; anything else - today's gap or a
    future ADF revision's - degrades to the opaque blob a wholly
    unrecognised type already gets, never a silent, partial drop. See
    _NODE_ATTRS_MARKS and _INLINE_ATTRS_MARKS, its two callers.
    """
    a = node.get("attrs", {})
    if not set(a) <= known_attrs:
        return False
    for mark in node.get("marks") or ():
        if mark.get("type") not in known_marks:
            return False
    return True


# Per named block-level (or block-position leaf) node type, the attrs keys
# and node-level marks _node_to_html's matching branch actually renders.
# Read by _node_to_html's own dispatch, ahead of every branch below it: a
# node whose type is a key here but whose attrs or marks are not a subset of
# its entry is not fully representable, so it renders as opaque instead of
# through its named branch, which would otherwise silently drop whatever it
# does not model. A type with no entry here is unconditionally opaque -
# there being no named branch for it at all is exactly the state this
# converter is already in for mention, emoji, extension and the rest.
_NODE_ATTRS_MARKS = {
    "paragraph": ({"localId"}, {"alignment", "indentation"}),
    "heading": ({"level", "localId"}, frozenset()),
    "panel": ({"panelType", "localId"} | _CUSTOM_PANEL_ATTRS, frozenset()),
    "expand": ({"title", "localId"}, {"breakout"}),
    "codeBlock": ({"language", "localId"}, {"breakout"}),
    "taskList": ({"localId"}, frozenset()),
    "taskItem": ({"state", "localId"}, frozenset()),
    "decisionList": ({"localId"}, frozenset()),
    "decisionItem": ({"state", "localId"}, frozenset()),
    "bulletList": ({"localId"}, frozenset()),
    "orderedList": ({"localId", "order"}, frozenset()),
    "listItem": ({"localId"}, frozenset()),
    "blockquote": ({"localId"}, frozenset()),
    "rule": ({"localId"}, frozenset()),
    "table": ({"width", "layout", "isNumberColumnEnabled", "displayMode",
               "localId"}, frozenset()),
    "tableRow": ({"localId"}, frozenset()),
    "tableCell": ({"colwidth", "colspan", "rowspan", "localId", "background"},
                  frozenset()),
    "tableHeader": ({"colwidth", "colspan", "rowspan", "localId", "background"},
                     frozenset()),
    "layoutSection": (frozenset(), {"breakout"}),
    "layoutColumn": ({"width"}, frozenset()),
    "blockCard": ({"url", "localId"}, frozenset()),
    "embedCard": ({"url", "localId"}, frozenset()),
    "mediaSingle": (_MEDIA_SINGLE_ATTRS, frozenset()),
    "media": (_MEDIA_ATTRS, frozenset()),
    "caption": (_CAPTION_ATTRS, frozenset()),
}

# The same table for the inline-position leaf types _inline_to_html renders
# by name. text is not here: its marks are the pre-existing per-mark
# opaque-passthrough mechanism (every recognised mark renders named, every
# other wraps opaquely - see the "else" branch inside _inline_to_html's own
# marks loop), a different and already-general mechanism from the
# whole-node fallback this dict feeds.
_INLINE_ATTRS_MARKS = {
    "status": ({"text", "color", "localId"}, frozenset()),
    "date": ({"timestamp", "localId"}, frozenset()),
    "inlineCard": ({"url", "localId"}, frozenset()),
    "hardBreak": (frozenset(), frozenset()),
}


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
    """An unrecognised ADF node, carried through untouched.

    tag is "div" for a node reached in block position - a direct child of a
    block-content list - and "span" for one reached in inline position,
    inside a paragraph, heading or similar. The position is what the caller
    already knows; the node's type gives no clue either way, since it is by
    definition one this converter does not recognise.
    """
    return f'<{tag} data-type="{ADF_OPAQUE}" data-adf="{_encode_adf(node)}"></{tag}>'


def _format_number(value):
    """A JSON number as a plain integer string, where that is safe.

    Found measuring this converter against a live Confluence instance,
    unrelated to opaque passthrough itself but blocking the same
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


def _local_id_attr(a):
    """The data-local-id (or data-local-id-null) HTML+ attribute fragment
    for a node's optional "localId" attrs key - "" when the key is absent
    entirely, ready to splice straight after a tag name. Mirrors
    _set_local_id, the read side, and carries the same three-state
    reasoning: absent (nothing to say - "" here), present as a real JSON
    null (a live-measured, real shape - Confluence assigns the id slot on
    some nodes without a value), or present as a string (the ordinary
    case). null gets its own valueless boolean attribute rather than a
    value of data-local-id itself, or the string "None" would be
    indistinguishable from a genuine localId that happened to read "None".
    """
    if "localId" not in a:
        return ""
    if a["localId"] is None:
        return " data-local-id-null"
    return f' data-local-id="{a["localId"]}"'


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
                # more, measured against a live site. Previously
                # silently dropped here: none of the branches above matched,
                # so the mark simply never got applied and the run of text
                # lost its formatting on every fetch, with no warning. Now
                # wrapped instead, the same as a known mark, just opaquely.
                out = _opaque_mark_to_html(mark, out)
        return out
    known = _INLINE_ATTRS_MARKS.get(t)
    if known is not None and not _fully_modelled(node, *known):
        return _opaque_to_html(node, tag="span")
    if t == "status":
        a = node["attrs"]
        local_id = _local_id_attr(a)
        return (f'<span data-type="status" data-color="{a["color"]}"{local_id}>'
                f'{_escape(a["text"])}</span>')
    if t == "date":
        a = node["attrs"]
        iso = _timestamp_to_iso(a["timestamp"])
        local_id = _local_id_attr(a)
        return f'<time datetime="{iso}"{local_id}>{iso}</time>'
    if t == "inlineCard":
        a = node["attrs"]
        local_id = _local_id_attr(a)
        return f'<a href="{a["url"]}" data-card-appearance="inline"{local_id}></a>'
    if t == "hardBreak":
        return "<br>"
    # Reached for any inline ADF node type this converter does not know how
    # to render. Earlier versions of this converter raised here: a real
    # fetched Confluence page can carry node types this converter has no
    # HTML+ for (media, mention, emoji, extension, nestedExpand and more,
    # measured against a live site), and refusing rather than silently
    # dropping the node was the right call while the only alternative was
    # dropping it. Passthrough is
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


def _node_marks_html(node):
    """A node's own breakout mark, if it has one, as an HTML+ attribute
    fragment - "" when it has none. Shared by codeBlock, expand and
    layoutSection, the three named block renderers that can carry one.
    """
    marks = node.get("marks") or []
    for mark in marks:
        if mark["type"] == "breakout":
            bits = [f'data-breakout-mode="{mark["attrs"]["mode"]}"']
            if "width" in mark["attrs"]:
                bits.append(
                    f'data-breakout-width="{_format_number(mark["attrs"]["width"])}"'
                )
            return " " + " ".join(bits)
    return ""


def _node_to_html(node):
    t = node.get("type")
    a = node.get("attrs", {})
    known = _NODE_ATTRS_MARKS.get(t)
    if known is not None and not _fully_modelled(node, *known):
        return _opaque_to_html(node)
    if t == "paragraph":
        local_id = _local_id_attr(a)
        align = indent = ""
        for mark in node.get("marks") or []:
            if mark["type"] == "alignment":
                align = f' data-align="{mark["attrs"]["align"]}"'
            elif mark["type"] == "indentation":
                indent = f' data-indent-level="{_format_number(mark["attrs"]["level"])}"'
        return f"<p{local_id}{align}{indent}>{_inline_html(node)}</p>"
    if t == "heading":
        level = a["level"]
        local_id = _local_id_attr(a)
        return f"<h{level}{local_id}>{_inline_html(node)}</h{level}>"
    if t == "panel":
        bits = []
        local_id_bit = _local_id_attr(a).strip()
        if local_id_bit:
            bits.append(local_id_bit)
        if "panelIconId" in a:
            bits.append(f'data-panel-icon-id="{a["panelIconId"]}"')
        if "panelIcon" in a:
            bits.append(f'data-panel-icon="{a["panelIcon"]}"')
        if "panelIconText" in a:
            bits.append(f'data-panel-icon-text="{a["panelIconText"]}"')
        if "panelColor" in a:
            bits.append(f'data-panel-color="{a["panelColor"]}"')
        extra = "" if not bits else " " + " ".join(bits)
        return (f'<div data-type="panel-{a["panelType"]}"{extra}>'
                f"{_children_html(node)}</div>")
    if t == "expand":
        local_id = _local_id_attr(a)
        breakout = _node_marks_html(node)
        return (f'<details{local_id}{breakout}>'
                f'<summary>{_escape(a.get("title", ""))}</summary>'
                f"{_children_html(node)}</details>")
    if t == "codeBlock":
        text = "".join(c.get("text", "") for c in node.get("content", []))
        local_id = _local_id_attr(a)
        breakout = _node_marks_html(node)
        # No class at all when the node carries no "language" - the real
        # shape of an unlabelled block (see the html_to_adf side of this
        # same fix). Defaulting to "language-plaintext" here, as an earlier
        # version of this converter did, rendered every one of those as if
        # "plaintext" had been chosen, which round-tripped back in as an
        # attrs key the fetched page never had.
        code_open = f'<code class="language-{a["language"]}">' if "language" in a \
            else "<code>"
        return f'<pre{local_id}{breakout}>{code_open}{_escape(text)}</code></pre>'
    if t == "taskList":
        # localId round-trips through data-local-id - see the html_to_adf
        # side for why dropping it used to fail check-roundtrip on every
        # fetched page with a task list. Emitted only when the "localId"
        # key is actually present (matching html_to_adf's own now-omitted-
        # rather-than-defaulted key, and the caption pattern below) -
        # present-but-empty and absent are different ADF, and only "in a"
        # tells them apart; a.get(..., "") would print data-local-id=""
        # for a node that never had the key at all, and html_to_adf would
        # then read that back as a present-but-empty key, still not what
        # the source had.
        local_id = _local_id_attr(a)
        return (f'<ul data-type="task-list"{local_id}>'
                f"{_children_html(node)}</ul>")
    if t == "taskItem":
        checked = " checked" if a.get("state") == "DONE" else ""
        local_id = _local_id_attr(a)
        return (f'<li data-type="task-item"{local_id}>'
                f'<input type="checkbox"{checked}>{_inline_html(node)}</li>')
    if t == "decisionList":
        local_id = _local_id_attr(a)
        return (f'<ul data-type="decision-list"{local_id}>'
                f"{_children_html(node)}</ul>")
    if t == "decisionItem":
        local_id = _local_id_attr(a)
        return (f'<li data-type="decision-item" data-state="{a.get("state", "DECIDED")}"'
                f"{local_id}>"
                f"{_inline_html(node)}</li>")
    if t == "bulletList":
        local_id = _local_id_attr(a)
        return f"<ul{local_id}>{_children_html(node)}</ul>"
    if t == "orderedList":
        local_id = _local_id_attr(a)
        # order is HTML's own start="N" - the number the list starts
        # counting from - not a data-* attribute, since it is exactly what
        # start already means and an author might reasonably set it by hand.
        start = f' start="{_format_number(a["order"])}"' if "order" in a else ""
        return f"<ol{local_id}{start}>{_children_html(node)}</ol>"
    if t == "listItem":
        local_id = _local_id_attr(a)
        return f"<li{local_id}>{_children_html(node)}</li>"
    if t == "blockquote":
        local_id = _local_id_attr(a)
        return f"<blockquote{local_id}>{_children_html(node)}</blockquote>"
    if t == "rule":
        local_id = _local_id_attr(a)
        return f"<hr{local_id}>"
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
        local_id_bit = _local_id_attr(a).strip()
        if local_id_bit:
            bits.append(local_id_bit)
        open_tag = "<table" + ("" if not bits else " " + " ".join(bits)) + ">"
        return f"{open_tag}<tbody>{_children_html(node)}</tbody></table>"
    if t == "tableRow":
        local_id = _local_id_attr(a)
        return f"<tr{local_id}>{_children_html(node)}</tr>"
    if t in ("tableCell", "tableHeader"):
        # colwidth's values are checked for shape, not just its key's
        # presence in _NODE_ATTRS_MARKS above (which only inspects attrs
        # keys, not what is inside them): real ADF allows null entries for
        # an indeterminate column inside a colspanned cell, never seen on a
        # live-measured site but not something a plain-number HTML
        # attribute can carry either. Falls back to opaque rather than
        # writing the Python string "None" into data-colwidth, which
        # _parse_plain_number could never read back.
        colwidth = a.get("colwidth")
        if colwidth is not None and not all(
            isinstance(w, (int, float)) and not isinstance(w, bool)
            for w in colwidth
        ):
            return _opaque_to_html(node)
        tag = "td" if t == "tableCell" else "th"
        bits = []
        if colwidth is not None:
            # One value per spanned column, comma separated - see the
            # matching split in handle_starttag's "th"/"td" branch. A
            # colspan=1 cell (the overwhelming majority) still round-trips
            # through the same one-element-list shape it always has.
            bits.append(
                'data-colwidth="' + ",".join(_format_number(w) for w in colwidth) + '"'
            )
        for key in ("colspan", "rowspan"):
            if key in a:
                bits.append(f'{key}="{_format_number(a[key])}"')
        if "background" in a:
            bits.append(f'data-background="{a["background"]}"')
        local_id_bit = _local_id_attr(a).strip()
        if local_id_bit:
            bits.append(local_id_bit)
        open_tag = f"<{tag}" + ("" if not bits else " " + " ".join(bits)) + ">"
        return f"{open_tag}{_children_html(node)}</{tag}>"
    if t == "layoutSection":
        count = len(node.get("content", []))
        layout = _LAYOUT_BY_COUNT.get(count, "layout-two-equal")
        breakout = _node_marks_html(node)
        return (f'<section data-type="{layout}"{breakout}>'
                f"{_children_html(node)}</section>")
    if t == "layoutColumn":
        # "width" is always present - html_to_adf never builds a
        # layoutColumn without computing or reading one - so this is
        # unconditional, not the usual "in a" guard the rest of this file
        # uses for a genuinely optional key.
        return (f'<div data-type="column" data-width="{_format_number(a["width"])}">'
                f"{_children_html(node)}</div>")
    if t in ("blockCard", "embedCard"):
        appearance = "block" if t == "blockCard" else "embed"
        local_id = _local_id_attr(a)
        return f'<a href="{a["url"]}" data-card-appearance="{appearance}"{local_id}></a>'
    if t == "mediaSingle":
        bits = [f'data-layout="{a.get("layout", "center")}"']
        if "width" in a:
            bits.append(f'data-width="{_format_number(a["width"])}"')
        if "widthType" in a:
            bits.append(f'data-width-type="{a["widthType"]}"')
        return (f'<figure data-type="media-single" {" ".join(bits)}>'
                f"{_children_html(node)}</figure>")
    if t == "media" and a.get("type", "file") == "file" \
            and "id" in a and "collection" in a:
        # Named support only for the shape this converter can represent
        # completely: a file attachment with an id and a collection - what
        # attachments.sh upload actually produces. The attrs-completeness
        # half of that ("no key outside _MEDIA_ATTRS") is now the shared
        # gate at the top of this function; what is left here is the
        # required-keys-present check that gate does not do, since a media
        # node can carry only attrs this converter models (type, id,
        # collection - all in _MEDIA_ATTRS) and still not be the file shape
        # this branch builds, if the id or collection Confluence sends
        # simply is not there. A media node of any other shape (external
        # type with no id/collection) falls through to the opaque branch at
        # the end of this function instead - never a KeyError on a["id"].
        bits = [f'data-media-type="{a.get("type", "file")}"',
                f'data-id="{a["id"]}"', f'data-collection="{a["collection"]}"']
        if "alt" in a:
            bits.append(f'data-alt="{a["alt"]}"')
        if "width" in a:
            bits.append(f'data-width="{_format_number(a["width"])}"')
        if "height" in a:
            bits.append(f'data-height="{_format_number(a["height"])}"')
        local_id_bit = _local_id_attr(a).strip()
        if local_id_bit:
            bits.append(local_id_bit)
        return f'<div data-type="media" {" ".join(bits)}></div>'
    if t == "caption":
        bits = _local_id_attr(a)
        return f"<figcaption{bits}>{_inline_html(node)}</figcaption>"
    if t in _INLINE_LEAF_TYPES:
        # A known inline leaf type reached in block position. Not something
        # a well-formed ADF tree produces - text/status/date/inlineCard/
        # hardBreak only ever sit inside a paragraph, heading or similar,
        # never as a direct child of a block-content list - but this is the
        # pre-existing fallback for it, kept rather than removed.
        return _inline_to_html(node)
    # A genuinely unrecognised type in block position - opaque passthrough,
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
        # below), and a live-site measurement found a real media
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
# is what makes it safe against opaque passthrough for a NODE, with no
# extra handling. An opaque HTML+ element's data-adf is decoded by
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
                f"{a_or_an(t).capitalize()} {t} is Confluence-only and "
                f"Jira does not render it. The API would accept the "
                f"description and show nothing. Use a panel, a table, a "
                f"code block or a task list instead."
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
    round-trip fidelity on the page actually being replaced. Before opaque
    passthrough existed, a page carrying an unrecognised node or mark
    simply refused to convert at all - loud, but safe, since nothing was
    ever written. Opaque passthrough lets those pages convert now; for the
    ones that still are not identical under this comparison (a known type
    dropping an attr or a mark this converter has no HTML+ for), that
    refusal has to be reproduced deliberately here, or reading now
    succeeds where it used to fail and writing silently drops whatever
    this converter could not carry through - the same failure closed for
    an outright-unsupported node, reappearing one level down for a
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
            # confluence-pages.sh read pipes a live page's body straight
            # into this with no chance to validate it first - a null body,
            # a truncated response, or ADF this build has never seen the
            # shape of all reach json.load or adf_to_html here. Only
            # ConversionError is a message this CLI already explains;
            # anything else, uncaught, is a Python traceback as the whole
            # answer to "what does the wiki say about X" - not what "read"
            # should hand back for a page it cannot render, the same reason
            # check-roundtrip's own try/except below exists.
            try:
                sys.stdout.write(adf_to_html(json.load(sys.stdin)))
            except ConversionError:
                raise
            except Exception as exc:
                raise ConversionError(
                    f"the page body could not be read as HTML+ "
                    f"({type(exc).__name__}: {exc})."
                )
        elif args.command == "to-markdown":
            try:
                sys.stdout.write(adf_to_markdown(json.load(sys.stdin)))
            except ConversionError:
                raise
            except Exception as exc:
                raise ConversionError(
                    f"the page body could not be read as markdown "
                    f"({type(exc).__name__}: {exc})."
                )
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
