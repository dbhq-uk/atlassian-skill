#!/usr/bin/env python3
"""Edit one node of a Confluence page's ADF by its local id, and say what a
write would remove.

`update` re-emits the whole page: every node goes through the converter and
back, and the round-trip gate has to pass for all of it, even when the edit
touches one paragraph. `splice` instead converts only the fragment and puts
it into the live ADF beside, or in place of, the node carrying a local id.
Every other node is copied through as parsed JSON and never reaches the
converter, so an extension, a macro or anything else this converter has no
HTML+ for leaves exactly as it arrived.

`diff` compares the live document with the one about to be written and
names what would go: every node whose local id is missing afterwards, and
every node the converter has no named form for that no longer appears.

`same` says whether a page already holds what publish.sh would write, so an
unchanged file does not add an empty version to the page history.

Standard library only. Reads files, writes JSON to stdout, sends nothing.
"""

import argparse
import json
import sys

from htmlplus import (
    INLINE_TYPES,
    ConversionError,
    _INLINE_ATTRS_MARKS,
    _NODE_ATTRS_MARKS,
    _fully_modelled,
    _node_to_html,
    html_to_adf,
    normalise,
)

# A fragment cannot be converted on its own inside these: a row needs its
# table around it for the column bookkeeping, and a column needs its
# section for the width.
_NO_SPLICE_PARENTS = {"table", "tableRow", "layoutSection"}


def _walk(node, parent=None):
    """Yield (node, parent) for node and every node under it."""
    yield node, parent
    for child in node.get("content", []) or []:
        if isinstance(child, dict):
            yield from _walk(child, node)


def _local_id(node):
    attrs = node.get("attrs")
    return attrs.get("localId") if isinstance(attrs, dict) else None


def _find(doc, local_id):
    """(node, parent) for the one node carrying local_id, or ConversionError."""
    found = [(n, p) for n, p in _walk(doc) if _local_id(n) == local_id]
    if not found:
        raise ConversionError(
            f'no node on this page has local id "{local_id}". Read the page '
            f"with --format html and copy a data-local-id value from there."
        )
    if len(found) > 1:
        raise ConversionError(
            f'{len(found)} nodes on this page share local id "{local_id}", so '
            f"it does not say which one to edit. Use update for this page."
        )
    node, parent = found[0]
    if parent is None:
        raise ConversionError("the local id is on the document itself.")
    if node.get("type") in INLINE_TYPES:
        raise ConversionError(
            f'local id "{local_id}" is on {node.get("type")}, which sits inside '
            f"a {parent.get('type')}. edit replaces or adds whole blocks - use "
            f"the local id of the block it sits in."
        )
    if parent.get("type") in _NO_SPLICE_PARENTS:
        raise ConversionError(
            f'local id "{local_id}" is on a {node.get("type")} directly inside '
            f"a {parent.get('type')}, which edit cannot rebuild on its own. "
            f"Target a block inside it, or the whole {parent.get('type')}."
        )
    return node, parent


def _fragment_nodes(fragment, parent_type):
    """The fragment's nodes, converted and checked as parent_type's children."""
    nodes = html_to_adf(fragment, parent=parent_type).get("content", [])
    if not nodes:
        raise ConversionError(
            "the fragment converted to nothing. Check the body file has content."
        )
    return nodes


def _survives(node, parent_type):
    """Whether node converts to HTML+ and back unchanged where it sits."""
    try:
        back = html_to_adf(_node_to_html(node, parent_type), parent=parent_type)
    except ConversionError:
        return False
    return normalise(back.get("content") or []) == normalise([node])


def splice(doc, op, local_id, fragment):
    """doc with fragment spliced in. doc is changed in place and returned."""
    if op == "append":
        doc.setdefault("content", []).extend(_fragment_nodes(fragment, "doc"))
        return doc
    target, parent = _find(doc, local_id)
    parent_type = parent.get("type")
    if op == "replace" and not _survives(target, parent_type):
        raise ConversionError(
            f'the {target.get("type")} with local id "{local_id}" does not '
            f"survive converting to HTML+ and back unchanged, so a replacement "
            f"written from its HTML+ could drop part of it. Make this edit in "
            f"the Confluence UI."
        )
    nodes = _fragment_nodes(fragment, parent_type)
    siblings = parent["content"]
    index = next(i for i, n in enumerate(siblings) if n is target)
    if op == "replace":
        siblings[index:index + 1] = nodes
    else:
        siblings[index + 1:index + 1] = nodes
    return doc


def _unnamed(node):
    """Whether this node has no complete named HTML+ form."""
    t = node.get("type")
    if t == "text":
        return False
    known = _NODE_ATTRS_MARKS.get(t) or _INLINE_ATTRS_MARKS.get(t)
    return known is None or not _fully_modelled(node, *known)


def _canonical(node):
    return json.dumps(node, sort_keys=True, separators=(",", ":"))


def removed(before, after):
    """The nodes of before that after no longer has, as (type, local id)."""
    after_ids = {_local_id(n) for n, _ in _walk(after)} - {None}
    after_unnamed = {_canonical(n) for n, _ in _walk(after) if _unnamed(n)}
    gone = []
    skip = set()
    for node, parent in _walk(before):
        if parent is None:
            continue
        if id(parent) in skip:
            skip.add(id(node))
            continue
        local_id = _local_id(node)
        if local_id is not None and local_id not in after_ids:
            gone.append((node.get("type"), local_id))
            skip.add(id(node))
        elif local_id is None and _unnamed(node) and _canonical(node) not in after_unnamed:
            gone.append((node.get("type"), None))
            skip.add(id(node))
    return gone


def describe(before, after):
    """The dry-run report: block counts and what the write would remove."""
    lines = [
        f"  Top-level blocks: {len(before.get('content', []))} now, "
        f"{len(after.get('content', []))} after."
    ]
    gone = removed(before, after)
    if not gone:
        lines.append("  Removes nothing that carries a local id, and no node "
                     "without a named HTML+ form.")
    else:
        lines.append(f"  Removes {len(gone)}:")
        for node_type, local_id in gone:
            if local_id is None:
                lines.append(f"    {node_type} (no named HTML+ form)")
            else:
                lines.append(f"    {node_type} {local_id}")
    return "\n".join(lines)


# Attributes Confluence fills in when it saves a page, which a file never
# states. A local id on any node; the pixel size of an image it measured.
_ASSIGNED_ON_SAVE = {"localId"}
_ASSIGNED_ON_SAVE_BY_TYPE = {"media": {"width", "height"},
                             "mediaSingle": {"width", "widthType"}}


def same(new, live):
    """Whether live already holds new, ignoring what Confluence assigns on
    save and new never states, and the differences normalise removes.
    Anything else that differs counts."""
    return _same(normalise(new), normalise(live))


def _same(new, live):
    if isinstance(new, dict) and isinstance(live, dict) and isinstance(new.get("type"), str):
        if set(new) - {"attrs"} != set(live) - {"attrs"}:
            return False
        ignorable = _ASSIGNED_ON_SAVE | _ASSIGNED_ON_SAVE_BY_TYPE.get(new["type"], set())
        new_attrs, live_attrs = new.get("attrs") or {}, live.get("attrs") or {}
        if any(k not in live_attrs or live_attrs[k] != v for k, v in new_attrs.items()):
            return False
        if any(k not in new_attrs and k not in ignorable for k in live_attrs):
            return False
        return all(_same(new[k], live[k]) for k in new if k != "attrs")
    if isinstance(new, list) and isinstance(live, list):
        return len(new) == len(live) and all(_same(a, b) for a, b in zip(new, live))
    return new == live


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("splice", help="print the page ADF with a fragment spliced in")
    s.add_argument("--page", required=True, help="the live page ADF, as JSON")
    s.add_argument("--fragment", required=True, help="the HTML+ fragment")
    s.add_argument("--op", required=True, choices=["replace", "insert-after", "append"])
    s.add_argument("--local-id", default=None)
    d = sub.add_parser("diff", help="say what writing the second ADF over the first removes")
    d.add_argument("before")
    d.add_argument("after")
    m = sub.add_parser("same", help="exit 0 if the live ADF already holds the new one")
    m.add_argument("new")
    m.add_argument("live")
    args = parser.parse_args(argv)
    try:
        if args.command == "splice":
            if (args.op == "append") != (args.local_id is None):
                raise ConversionError("--append takes no local id; --replace "
                                      "and --insert-after need one.")
            with open(args.page, encoding="utf-8") as f:
                doc = json.load(f)
            with open(args.fragment, encoding="utf-8") as f:
                fragment = f.read()
            json.dump(splice(doc, args.op, args.local_id, fragment), sys.stdout,
                      ensure_ascii=False, separators=(",", ":"))
        elif args.command == "same":
            with open(args.new, encoding="utf-8") as f:
                new = json.load(f)
            with open(args.live, encoding="utf-8") as f:
                live = json.load(f)
            return 0 if same(new, live) else 3
        else:
            with open(args.before, encoding="utf-8") as f:
                before = json.load(f)
            with open(args.after, encoding="utf-8") as f:
                after = json.load(f)
            print(describe(before, after))
    except ConversionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"Error: could not read the page or fragment ({exc}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
