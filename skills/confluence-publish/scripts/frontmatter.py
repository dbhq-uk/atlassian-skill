#!/usr/bin/env python3
"""Read and write the Confluence binding in a markdown file's frontmatter.

The file is the master and the file states where it renders, so the page id
lives with the content rather than in a separate mapping that drifts. A move
or a rename cannot break the link, and a diff shows the binding.

Standard library only. The frontmatter subset handled here is exactly what the
binding needs - a top-level `confluence:` key with string values - and every
other key is copied through untouched rather than reserialised, because a YAML
round trip through a hand-rolled parser would lose formatting nobody asked it
to change.

That "copied through untouched" promise extends to the bytes making up a
line, not only its key and value: every line this module is not editing keeps
its own original line terminator, whichever it was. An earlier version of
this file read and wrote text in Python's default universal-newline mode,
which translates every \\r\\n to \\n on the way in and back to the platform
default on the way out - silently flipping a whole CRLF-authored file to LF
on the first edit, even though only one line's content actually changed.
Reading and writing here works on raw bytes instead (see _read/_write), and
the split/reassemble logic works line by line with each line's terminator
(or lack of one, on a file with no trailing newline) still attached.

Two more properties the write path holds, both found by review rather than
anticipated up front:

- A leading UTF-8 byte-order mark (ordinary output from Windows editors) is
  stripped before parsing and put back on write, rather than being left on
  line 0 where it silently defeats the "---" check and makes a file that
  demonstrably has frontmatter look like it has none.
- The write itself is atomic: text is written to a sibling temporary file
  and moved into place with os.replace(), never opened for truncation in
  place. This is the destructive half of confluence-publish - it rewrites a
  file that may hold uncommitted work - and a process killed mid-write must
  never be able to leave it half-written.
"""

import argparse
import os
import pathlib
import re
import sys
import tempfile

DELIM = "---"
KEYS = ("space", "parent", "page_id")
BOM = b"\xef\xbb\xbf"


def _read(path):
    """(text, had_bom) - the file's exact text, decoded from raw bytes.

    Reading bytes and decoding by hand, rather than any of Python's text
    modes, buys two things at once: no universal-newline translation (a
    plain bytes.decode() never touches \\r\\n, so there is no newline= to
    remember to pass, and nothing for an older pathlib to lack), and a
    UTF-8 BOM can be detected and stripped explicitly instead of silently
    surviving as the first three bytes of "line 0" - which is exactly what
    broke _split before this fix: a BOM in front of "---" meant line 0 was
    never equal to DELIM, so a file that genuinely had frontmatter was read
    as if it had none, and write_page_id's no-frontmatter branch then
    prepended a second, competing "confluence:" block ahead of the real one,
    permanently hiding its space and parent behind body text that looked
    like content.
    """
    with open(path, "rb") as f:
        raw = f.read()
    had_bom = raw.startswith(BOM)
    if had_bom:
        raw = raw[len(BOM):]
    return raw.decode("utf-8"), had_bom


def _write(path, text, had_bom=False):
    """Atomically replace path's contents with text (BOM re-added if had_bom).

    Writes to a sibling temporary file in path's own directory, fsyncs it,
    then os.replace()s it onto path - the standard crash-safe pattern.
    os.replace() is atomic only within a single filesystem, which is why the
    temp file is created next to path rather than under the platform's
    default temp directory (routinely a separate filesystem, e.g. a tmpfs
    /tmp on the same host as an ext4 home directory) - a temp file on a
    different filesystem would make the final move a copy, not a rename,
    and a copy can still be interrupted partway through.

    A plain open(path, "w") followed by write() - what an earlier version of
    this function did - truncates the file the instant it opens, before a
    single byte of the new content lands. A process killed, a full disk, or
    a lost connection between those two steps leaves whatever was already
    on disk gone and nothing coherent in its place: not the old file, not
    the new one, not even a readable partial one. This is the one function
    in this module that overwrites a file the user may have uncommitted
    changes in, so it is the one place a partial write is not an acceptable
    risk.

    The temporary file's mode is set to match the original file's before
    the rename, rather than left at tempfile's default (0600) - otherwise
    every file this module ever edits would silently have its permissions
    tightened on first use.
    """
    path = pathlib.Path(path)
    data = (BOM if had_bom else b"") + text.encode("utf-8")
    mode = path.stat().st_mode if path.exists() else None

    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent) or ".", prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _line_ending(line):
    """The line terminator line ends with, or "" if it has none.

    "none" is the last line of a file with no trailing newline - real and
    common, not a malformed edge case, and the body-preservation guarantee
    below depends on this being distinguished from "\n" rather than defaulted
    to it.
    """
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _sniff_newline(text):
    """The newline convention already used in text, "\n" if none is found.

    Only consulted when there is no existing frontmatter line to copy a line
    ending from - inserting a brand new frontmatter block into a file that
    had none - so the new block matches whatever convention the rest of the
    file already uses rather than always guessing "\n".
    """
    index = text.find("\n")
    if index > 0 and text[index - 1] == "\r":
        return "\r\n"
    return "\n"


def _split(text):
    """Return (frontmatter_lines, body) or (None, text).

    frontmatter_lines is the whole block - the opening and closing "---"
    delimiter lines included - as a list from splitlines(keepends=True), so
    each line still carries its own original terminator. body is everything
    after the closing delimiter, sliced out whole and never decoded into
    lines: a CRLF body, a body with no trailing newline, and a body that
    itself contains a line that looks like a second frontmatter block all
    survive exactly as they arrived, because nothing here re-parses them.

    Matching is anchored to line 0 for the opening delimiter and stops at the
    first line that is exactly "---" after that, so a "---" inside the body
    is never mistaken for a second frontmatter block - only a document that
    opens with one can have one at all. Callers pass text with any BOM
    already stripped (see _read) - this function has no BOM awareness of its
    own, deliberately, so strip_frontmatter(text) keeps working on a plain
    in-memory string exactly as it always has.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != DELIM:
        return None, text
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == DELIM:
            return lines[:i + 1], "".join(lines[i + 1:])
    return None, text


def read_binding(path):
    """Return {"space":, "parent":, "page_id":}, each a string or None."""
    text, _ = _read(path)
    block, _ = _split(text)
    out = {key: None for key in KEYS}
    if block is None:
        return out
    in_block = False
    for raw in block[1:-1]:
        line = raw.rstrip("\r\n")
        if re.match(r"^confluence:\s*$", line):
            in_block = True
            continue
        if in_block and line and not line[0].isspace():
            in_block = False
        if not in_block:
            continue
        m = re.match(r'^\s+(\w+):\s*"?([^"\s][^"]*?)"?\s*$', line)
        if m and m.group(1) in KEYS:
            out[m.group(1)] = m.group(2)
    return out


def strip_frontmatter(text):
    """Return the body with any frontmatter block removed."""
    _, body = _split(text)
    return body


def write_page_id(path, page_id):
    """Set confluence.page_id in place, preserving everything else.

    Idempotent: writing the id a file already carries rewrites nothing, not
    even the file's line endings or its BOM - the early return below fires
    before any read-modify-write, so a no-op call never touches the file at
    all.

    Every frontmatter line this call is not changing is copied through with
    the exact line terminator it already had, rather than the whole block
    being rebuilt with one newline convention picked for the file - a source
    file that mixes \\r\\n and \\n, or a rare one with none on its last line,
    keeps doing so on every line this function does not itself write.
    """
    path = pathlib.Path(path)
    text, had_bom = _read(path)
    if read_binding(path)["page_id"] == page_id:
        return

    block, body = _split(text)
    if block is None:
        newline = _sniff_newline(text)
        new_block = (
            f"{DELIM}{newline}confluence:{newline}"
            f'  page_id: "{page_id}"{newline}{DELIM}{newline}{newline}'
        )
        _write(path, new_block + text, had_bom)
        return

    opening, inner, closing = block[0], block[1:-1], block[-1]
    newline = _line_ending(opening) or _sniff_newline(text)

    out, done, in_block = [], False, False
    for line in inner:
        stripped = line.rstrip("\r\n")
        ending = _line_ending(line) or newline
        if re.match(r"^confluence:\s*$", stripped):
            in_block = True
            out.append(line)
            continue
        if in_block:
            if re.match(r"^\s+page_id:", stripped):
                out.append(f'  page_id: "{page_id}"{ending}')
                done = True
                continue
            if stripped and not stripped[0].isspace():
                if not done:
                    out.append(f'  page_id: "{page_id}"{newline}')
                    done = True
                in_block = False
        out.append(line)
    if in_block and not done:
        out.append(f'  page_id: "{page_id}"{newline}')
        done = True
    if not done:
        out.append(f"confluence:{newline}")
        out.append(f'  page_id: "{page_id}"{newline}')

    new_text = opening + "".join(out) + closing + body
    _write(path, new_text, had_bom)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["read", "body", "set-page-id"])
    parser.add_argument("path")
    parser.add_argument("value", nargs="?")
    args = parser.parse_args(argv)

    # publish.sh pipes `body`'s stdout straight into md_to_htmlplus.py and
    # captures `set-page-id`'s stderr to report a failure - neither expects
    # a multi-line Python traceback as the answer. A file that is not valid
    # UTF-8, missing, or unreadable (a read-only directory on set-page-id,
    # proven live) all raised uncaught before this existed; caught here the
    # same way htmlplus.py's own CLI catches a conversion failure, so the
    # caller gets one line naming what went wrong instead of a stack trace.
    try:
        if args.command == "read":
            binding = read_binding(args.path)
            for key in KEYS:
                print(f"{key}={binding[key] or ''}")
        elif args.command == "body":
            text, _ = _read(args.path)
            sys.stdout.write(strip_frontmatter(text))
        else:
            if not args.value:
                print("Error: set-page-id needs a page id.", file=sys.stderr)
                return 1
            write_page_id(args.path, args.value)
    except Exception as exc:
        print(
            f"Error: {args.path} could not be read or written "
            f"({type(exc).__name__}: {exc}).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
