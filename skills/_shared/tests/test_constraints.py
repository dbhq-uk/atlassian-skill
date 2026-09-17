"""Repo-wide constraints. These hold the promises the README makes."""

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[3]

# This file is excluded from its own scan. It has to spell out the strings it
# forbids in order to look for them, so scanning itself would always fail and
# would take CI down with it.
#
# .github/ is excluded the same way the real CI job excludes it:
# .github/workflows/validate.yml's own "nothing client-specific" grep step
# runs with --exclude-dir=.github, and its em-dash step scopes to *.md/*.py
# only, so it never scans itself either. Both dodge the same problem this
# test hit without the exclusion: that workflow's own em-dash-detection step
# greps for the literal em-dash character as its search pattern (bash
# ANSI-C-quoted), and scanning *.yml repo-wide would flag that literal as a
# violation. It is not one; it is the tool that finds em dashes elsewhere
# needing the character it is looking for.
SELF = pathlib.Path(__file__).resolve()
TEXT_FILES = [
    p for p in REPO.rglob("*")
    if p.is_file()
    and p.suffix in {".md", ".py", ".sh", ".json", ".yml"}
    and ".git/" not in str(p)
    and ".github/" not in str(p)
    and p.resolve() != SELF
]

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)


class TestNothingClientSpecific(unittest.TestCase):
    """A public repository carries no client's name and no client's data."""

    # The original "com" + "pre" pattern matched the ordinary English word
    # "comprehensive", which would fail CI for anyone who ever writes that
    # word in a README. This pattern matches the standalone client name and
    # its hostnames, not any English word that happens to start the same way.
    FORBIDDEN = [
        (re.compile(r"\bacme\b|acme-?group|acmeservices", re.I), "a client name"),
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
        self.assertIsNotNone(pattern.search("a Acme skill"))
        self.assertIsNotNone(pattern.search("mycompany.atlassian.net"))


class TestReferencesExist(unittest.TestCase):
    REFS = REPO / "skills" / "_shared" / "references"

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
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(EM_DASH, text, f"em dash in {path.relative_to(REPO)}")
            self.assertNotIn(EN_DASH, text, f"en dash in {path.relative_to(REPO)}")


if __name__ == "__main__":
    unittest.main()
