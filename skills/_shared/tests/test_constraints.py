"""Repo-wide constraints. These hold the promises the README makes."""

import pathlib
import re
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parents[3]
REFS = REPO / "skills" / "_shared" / "references"

# This file is excluded from its own scan. It has to spell out the strings it
# forbids in order to look for them, so scanning itself would always fail and
# would take CI down with it.
SELF = pathlib.Path(__file__).resolve()

# The set of files this test suite treats as "the public repository" is every
# file git tracks - nothing more, nothing less. Two narrower approaches were
# tried and rejected:
#
# - A fixed extension allow-list ({".md", ".py", ".sh", ".json", ".yml"})
#   undercounts. A .yaml, .txt, .html, .toml, .csv or .svg file, or a
#   tracked extensionless file such as LICENSE, would carry client material
#   straight past every check here with no warning.
# - Walking the filesystem with a denied-extension list (skip images, fonts,
#   archives, compiled output) overcounts instead: it would sweep in this
#   working tree's gitignored scratch content too - .superpowers/sdd/*.diff
#   review artefacts, __pycache__/*.pyc - none of which ships. That is scope
#   creep away from the actual question, which is "what reaches the public
#   repository", not "what exists on this machine right now".
#
# `git ls-files` answers the actual question directly: exactly the files
# that are tracked, which is exactly the files a clone or a release carries.
# It also means no extension is ever silently missing from the scan again -
# there is nothing left to list.
def _tracked_files():
    result = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True, check=True,
    )
    return [REPO / p for p in result.stdout.decode("utf-8").split("\0") if p]


TEXT_FILES = [p for p in _tracked_files() if p.is_file() and p.resolve() != SELF]

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)

# .github/workflows/validate.yml legitimately embeds a literal em dash as the
# search pattern its own em-dash-detection CI step greps for ($'—',
# bash ANSI-C-quoted). That is not a violation of the "no em dash" rule; it
# is the tool that enforces the rule elsewhere needing the character it is
# looking for. It is excluded from the em-dash assertion only, below, and
# nowhere else: it still goes through TestNothingClientSpecific like every
# other tracked file, and so does everything else under .github/. A
# directory-wide exclusion would have been a hole, not a fix - a second file
# at .github/workflows/leak.yml could carry the client's name, their
# hostname, a ticket id and an IP address and nothing in this suite, or in
# the real CI job's own grep, would ever see it.
_VALIDATE_WORKFLOW = REPO / ".github" / "workflows" / "validate.yml"


class TestNothingClientSpecific(unittest.TestCase):
    """A public repository carries no client's name and no client's data."""

    # Built from fragments, deliberately. The client's name and hostname
    # must never appear contiguously anywhere in this public repository,
    # including here, in the file whose job is to detect them - the brief's
    # original "com" + "pre" concatenation had exactly the right idea, and a
    # better regex still has to keep that property. Every fixture below that
    # needs to represent the real name or host is built the same way, not
    # spelled out.
    _NAME = "com" + "pre"
    _HOST = _NAME + "services" + ".atlassian.net"

    FORBIDDEN = [
        (
            re.compile(r"\b" + _NAME + r"\b|" + _NAME + r"-?group|" + _NAME + "services", re.I),
            "a client name",
        ),
        (re.compile(r"\bmcas\.ms\b"), "a specific security proxy host"),
        (re.compile(r"\b(?:[A-Z]{3,}-[0-9]{3,})\b"), "a real-looking ticket id"),
        (re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"), "an IP address"),
    ]

    def test_no_client_material(self):
        for path in TEXT_FILES:
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern, what in self.FORBIDDEN:
                match = pattern.search(text)
                self.assertIsNone(
                    match,
                    f"{path.relative_to(REPO)} contains {what}: "
                    f"{match.group(0) if match else ''!r}",
                )

    def test_the_ordinary_word_is_not_a_false_positive(self):
        """"comprehensive" and "comprehension" must never trip the client-name check."""
        pattern, _what = self.FORBIDDEN[0]
        self.assertIsNone(pattern.search("a comprehensive guide"))
        self.assertIsNone(pattern.search("beyond human comprehension"))
        self.assertIsNotNone(pattern.search("a " + "Com" + "pre" + " skill"))
        self.assertIsNotNone(pattern.search(self._HOST))


class TestNoSourceProperNouns(unittest.TestCase):
    """The shipped references carry none of the source material's real names.

    TestNothingClientSpecific catches shapes: a client-name pattern, a
    hostname, a ticket id, an IP address. It cannot catch a real page title
    or a real system name carried over verbatim, because none of those match
    any of those shapes - "Quarterly Review Agenda & Record",
    "Orders to Billing Integration" and "The expenses destination" all sailed
    through that scan untouched. This test names them directly instead.

    Built from fragments for the same reason the client-name pattern above
    is: the source's proper nouns do not get to appear contiguously in this
    public file either, including in the list of things it forbids.
    """

    _SAF = "S" + "A" + "F"
    _TMS = "T" + "M" + "S"
    _CONCUR = "Con" + "cur"

    FORBIDDEN_NOUNS = [_SAF, _TMS, _CONCUR]

    def test_shipped_references_carry_no_source_proper_nouns(self):
        for name in ("house-style.md", "html-patterns.md", "checklist.md"):
            text = (REFS / name).read_text(encoding="utf-8")
            for noun in self.FORBIDDEN_NOUNS:
                self.assertNotIn(
                    noun,
                    text,
                    f"{name} still carries {noun!r}, a proper noun from the source",
                )


class TestReferencesExist(unittest.TestCase):
    REFS = REFS

    def test_the_three_references_are_present(self):
        for name in ("house-style.md", "html-patterns.md", "checklist.md"):
            self.assertTrue((self.REFS / name).is_file(), f"missing {name}")

    def test_house_style_does_not_teach_storage_format(self):
        # The storage-format warning - and the file's one sanctioned mention
        # of `<ac:structured-macro>`, quoted in backticks purely to tell
        # authors what to avoid - lives in house-style.md, not
        # html-patterns.md. html-patterns.md never mentions storage format at
        # all, so asserting against it here would pass regardless of what the
        # file said: the substring being stripped can never be found there,
        # which makes the assertion vacuous rather than a real check. Reading
        # house-style.md instead exercises the strip-then-assert logic for
        # real: the single backticked mention is removed, and nothing else
        # teaches the syntax.
        text = (self.REFS / "house-style.md").read_text(encoding="utf-8")
        self.assertNotIn("<ac:structured-macro", text.replace("`<ac:structured-macro>`", ""))

    def test_house_style_names_the_override_file(self):
        text = (self.REFS / "house-style.md").read_text(encoding="utf-8")
        self.assertIn("~/.dbhq/atlassian/house-style.md", text)


class TestNoDestructiveVerb(unittest.TestCase):
    """Create and read. The absence of a delete is the safety property."""

    def test_no_script_issues_a_delete(self):
        for path in REPO.rglob("skills/**/*.sh"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn('request = "DELETE"', text, str(path))
            self.assertNotIn("-X DELETE", text, str(path))
            self.assertNotIn("api DELETE", text, str(path))


class TestNoEmDash(unittest.TestCase):
    def test_house_style_forbids_them_and_the_repo_obeys(self):
        for path in TEXT_FILES:
            if path == _VALIDATE_WORKFLOW:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(EM_DASH, text, f"em dash in {path.relative_to(REPO)}")
            self.assertNotIn(EN_DASH, text, f"en dash in {path.relative_to(REPO)}")


if __name__ == "__main__":
    unittest.main()
