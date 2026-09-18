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
its own original line terminator, whichever it was. A first version of this
file read and wrote text in Python's default universal-newline mode, which
translates every \\r\\n to \\n on the way in and back to the platform default
on the way out - silently flipping a whole CRLF-authored file to LF on the
first edit, even though only one line's content actually changed. Every read
and write here therefore uses newline="" (no translation), and the split/
reassemble logic below works line by line with each line's terminator (or
lack of one, on a file with no trailing newline) still attached.
"""

import argparse
import pathlib
import re
import sys

DELIM = "---"
KEYS = ("space", "parent", "page_id")


def _read(path):
    """The file's exact text, with no newline translation.

    pathlib.Path.read_text() only grew a newline= parameter in Python 3.13;
    this repo targets 3.11 (see .github/workflows/validate.yml), so the
    translation has to be disabled the old way, via the builtin open().
    Universal-newline mode (the default) would silently turn every \\r\\n in
    the file into \\n before this module ever saw it - see the module
    docstring for why that matters on the write path.
    """
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _write(path, text):
    """Write text back with no newline translation - the write side of
    _read's guarantee. Without newline="", a plain "\\n" written on Linux
    stays "\\n" (os.linesep is already "\\n" here), which would mask this
    exact bug in local testing while still mis-writing on Windows - disabling
    translation unconditionally is what makes the guarantee hold everywhere,
    not just on this platform.
    """
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


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
    opens with one can have one at all.
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
    text = _read(path)
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
    even the file's line endings - the early return below fires before any
    read-modify-write, so a no-op call never touches the file at all.

    Every frontmatter line this call is not changing is copied through with
    the exact line terminator it already had, rather than the whole block
    being rebuilt with one newline convention picked for the file - a source
    file that mixes \\r\\n and \\n, or a rare one with none on its last line,
    keeps doing so on every line this function does not itself write.
    """
    path = pathlib.Path(path)
    text = _read(path)
    if read_binding(path)["page_id"] == page_id:
        return

    block, body = _split(text)
    if block is None:
        newline = _sniff_newline(text)
        new_block = (
            f"{DELIM}{newline}confluence:{newline}"
            f'  page_id: "{page_id}"{newline}{DELIM}{newline}{newline}'
        )
        _write(path, new_block + text)
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
    _write(path, new_text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["read", "body", "set-page-id"])
    parser.add_argument("path")
    parser.add_argument("value", nargs="?")
    args = parser.parse_args(argv)

    if args.command == "read":
        binding = read_binding(args.path)
        for key in KEYS:
            print(f"{key}={binding[key] or ''}")
    elif args.command == "body":
        sys.stdout.write(strip_frontmatter(_read(args.path)))
    else:
        if not args.value:
            print("Error: set-page-id needs a page id.", file=sys.stderr)
            return 1
        write_page_id(args.path, args.value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
