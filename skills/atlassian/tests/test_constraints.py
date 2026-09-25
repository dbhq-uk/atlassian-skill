"""Repo-wide constraints. These hold the promises the README makes."""

import os
import pathlib
import re
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parents[3]
REFS = REPO / "skills" / "atlassian" / "references"

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
#   working tree's own gitignored scratch content too - agent working
#   state, __pycache__/*.pyc - none of which ships. That is scope creep
#   away from the actual question, which is "what reaches the public
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
# search pattern its own em-dash-detection CI step greps for (an
# ANSI-C-quoted $'...' holding U+2014 - written as a codepoint here
# rather than as the character, because the CI step below greps THIS
# file too and a comment about the rule is not an exemption from it).
# That is not a violation of the "no em dash" rule; it
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
    # including here, in the file whose job is to detect them - the
    # "com" + "pre" concatenation below keeps that property, and a better
    # regex still has to keep it too. Every fixture below that needs to
    # represent the real name or host is built the same way, not spelled
    # out.
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
    any of those shapes - a real session-notes page title, a real two-system
    integration name, and a real destination system name all sailed through
    that scan untouched. This test names them directly instead.

    Built from fragments for the same reason the client-name pattern above
    is: the source's proper nouns do not get to appear contiguously in this
    public file either, including in the list of things it forbids - and
    that includes in prose describing them, not only in the list itself.
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


class TestNoForceFlag(unittest.TestCase):
    """No script accepts --force. A round-trip or stale-write refusal on
    `confluence-pages.sh update` (or `confluence-publish`'s publish.sh,
    which calls it) needs a human decision in the Confluence UI, never a
    flag - AGENTS.md's constraint 6, echoed across README.md, SECURITY.md,
    CONTRIBUTING.md and every SKILL.md that documents a refusal. This
    checks the property those documents claim actually holds: every
    argument parser in the repo is checked for the same `--flagname)` case
    shape every other flag here uses, not just grepped for the string
    "--force" - which the documentation itself is full of, always saying
    there isn't one.
    """

    def test_no_script_parses_a_force_flag(self):
        for path in REPO.rglob("skills/**/*.sh"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("--force)", text, str(path))
            self.assertNotIn("'--force'", text, str(path))
            self.assertNotIn('"--force"', text, str(path))


class TestCreateFieldEscapeHatch(unittest.TestCase):
    """create had no way to serve a project-specific required field or a
    component with no default - only project, type, summary, description,
    labels, priority and parent. --field KEY=VALUE closes that gap, merged
    into `fields` last. Runs the real script's --dry-run, which never
    touches the network, so no fake curl is needed here.
    """

    SCRIPT = REPO / "skills" / "atlassian" / "scripts" / "jira-issues.sh"

    def _run(self, tmp, *args):
        config_dir = tmp / ".dbhq" / "atlassian"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text(
            '{"site":"https://example.atlassian.net",'
            '"email":"e@x.com","token":"secret"}'
        )
        return subprocess.run(
            ["bash", str(self.SCRIPT), "create", "PAY", "Task", "Summary",
             *args, "--dry-run"],
            capture_output=True, text=True,
            env={"HOME": str(tmp), "PATH": os.environ.get("PATH", "")},
        )

    def test_a_plain_value_is_sent_as_a_string(self):
        import tempfile, json
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(pathlib.Path(tmp), "--field", "customfield_10050=Ops")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["fields"]["customfield_10050"], "Ops")

    def test_a_json_value_is_sent_as_json_not_a_string(self):
        import tempfile, json
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(
                pathlib.Path(tmp), "--field",
                'components=[{"name":"Backend"}]',
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                payload["fields"]["components"], [{"name": "Backend"}]
            )

    def test_it_refuses_to_set_a_field_the_dedicated_options_already_cover(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(pathlib.Path(tmp), "--field", "summary=hijack")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("use the dedicated option", result.stderr)

    def test_a_value_with_no_equals_sign_is_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(pathlib.Path(tmp), "--field", "noequals")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("needs key=value", result.stderr)


class TestBulkRejectsAnUnrecognisedFourthArgument(unittest.TestCase):
    """jira-issues.sh bulk used to accept any fourth argument as a silent
    no-op unless it was exactly "--dry-run" - so `bulk PAY tickets.json
    --dryrun` (a plausible misspelling) ran live instead of refusing. This
    runs the real script, with a throwaway config so it never reaches a
    network call: --dry-run's own branch never calls curl, and an
    unrecognised option is now rejected before the file is even read.
    """

    SCRIPT = REPO / "skills" / "atlassian" / "scripts" / "jira-issues.sh"

    def _home_with_config(self, tmp):
        config_dir = tmp / ".dbhq" / "atlassian"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text(
            '{"site":"https://example.atlassian.net",'
            '"email":"e@x.com","token":"secret"}'
        )
        return tmp

    def _tickets_file(self, tmp):
        path = tmp / "tickets.json"
        path.write_text('[{"summary": "Test one"}]')
        return path

    def test_a_misspelt_dry_run_flag_is_rejected_not_run_live(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            home = self._home_with_config(tmp)
            tickets = self._tickets_file(tmp)
            result = subprocess.run(
                ["bash", str(self.SCRIPT), "bulk", "PAY", str(tickets), "--dryrun"],
                capture_output=True, text=True,
                env={"HOME": str(home), "PATH": os.environ.get("PATH", "")},
            )
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("unknown option", result.stderr)
            # Never reaches the point of reporting any per-issue action -
            # a live run and a rejected one must not look the same.
            self.assertNotIn("Test one", result.stdout)

    def test_the_correct_spelling_still_runs_as_a_dry_run(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            home = self._home_with_config(tmp)
            tickets = self._tickets_file(tmp)
            result = subprocess.run(
                ["bash", str(self.SCRIPT), "bulk", "PAY", str(tickets), "--dry-run"],
                capture_output=True, text=True,
                env={"HOME": str(home), "PATH": os.environ.get("PATH", "")},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("dry run - nothing will be sent", result.stdout)
            self.assertIn("Test one", result.stdout)


class TestJiraMetaFollowsPagination(unittest.TestCase):
    """jira-meta.sh projects used to fetch exactly one page of 100 and
    filter locally - a project living past the first page was invisible to
    both `projects <search>` and, by extension, to an agent discovering the
    right project before creating an issue. This runs the real script
    against a fake two-page curl and checks both pages' projects come back.
    """

    SCRIPT = REPO / "skills" / "atlassian" / "scripts" / "jira-meta.sh"

    FAKE_CURL = """#!/bin/bash
cfg=""
while [ $# -gt 0 ]; do
  case "$1" in
    -K) cfg="$2"; shift 2 ;;
    *) shift ;;
  esac
done
url=$(grep '^url' "$cfg" | sed 's/.*= "\\(.*\\)"/\\1/')
hdrfile=$(grep '^dump-header' "$cfg" | sed 's/.*= "\\(.*\\)"/\\1/')
: > "$hdrfile"
case "$url" in
  *startAt=0*)
    printf '{"values":[{"key":"AAA","name":"Alpha","projectTypeKey":"software"}],"isLast":false,"startAt":0,"maxResults":100,"total":2}'
    printf '\\n200'
    ;;
  *startAt=1*)
    printf '{"values":[{"key":"BBB","name":"Beta","projectTypeKey":"software"}],"isLast":true,"startAt":1,"maxResults":100,"total":2}'
    printf '\\n200'
    ;;
  *)
    printf '{"values":[],"isLast":true}'
    printf '\\n200'
    ;;
esac
"""

    def test_projects_merges_a_second_page_rather_than_stopping_at_the_first(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            fake_curl = bin_dir / "curl"
            fake_curl.write_text(self.FAKE_CURL)
            fake_curl.chmod(0o755)

            home = tmp / "home"
            config_dir = home / ".dbhq" / "atlassian"
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text(
                '{"site":"https://example.atlassian.net",'
                '"email":"e@x.com","token":"secret"}'
            )

            result = subprocess.run(
                ["bash", str(self.SCRIPT), "projects"],
                capture_output=True, text=True,
                env={"HOME": str(home),
                     "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("AAA", result.stdout)
            self.assertIn("BBB", result.stdout)


class TestDiscoveryWarnsWhenTruncated(unittest.TestCase):
    """confluence-search.sh (spaces, cql/text) and jira-issues.sh search
    fetch one page and used to say nothing when more existed - incomplete
    discovery presented as authoritative. Each now checks the API's own
    "there is more" signal (Confluence's `_links.next` cursor, Jira's
    `nextPageToken`) and says so. This runs each real script against a
    fake curl standing in for a truncated result set.
    """

    CONFLUENCE_SEARCH = (REPO / "skills" / "atlassian" / "scripts"
                          / "confluence-search.sh")
    JIRA_ISSUES = REPO / "skills" / "atlassian" / "scripts" / "jira-issues.sh"

    def _fake_curl(self, tmp, body_by_url_substring):
        """A fake curl returning a different canned body depending on what
        substring of the requested URL matches - good enough to tell
        `spaces` from `search` without a real cursor-following mock.
        """
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        fake_curl = bin_dir / "curl"
        cases = "\n".join(
            f'  *{substr}*) printf {body!r}; printf "\\n200" ;;'
            for substr, body in body_by_url_substring.items()
        )
        fake_curl.write_text(
            "#!/bin/bash\n"
            'cfg=""\n'
            'while [ $# -gt 0 ]; do case "$1" in -K) cfg="$2"; shift 2 ;; '
            "*) shift ;; esac; done\n"
            'url=$(grep \'^url\' "$cfg" | sed \'s/.*= "\\(.*\\)"/\\1/\')\n'
            'hdrfile=$(grep \'^dump-header\' "$cfg" | sed \'s/.*= "\\(.*\\)"/\\1/\')\n'
            ': > "$hdrfile"\n'
            f'case "$url" in\n{cases}\nesac\n'
        )
        fake_curl.chmod(0o755)
        return bin_dir

    def _home_with_config(self, tmp):
        home = tmp / "home"
        config_dir = home / ".dbhq" / "atlassian"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text(
            '{"site":"https://example.atlassian.net",'
            '"email":"e@x.com","token":"secret"}'
        )
        return home

    def test_confluence_spaces_warns_on_a_next_cursor(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            bin_dir = self._fake_curl(tmp, {
                "spaces": '{"results":[{"id":"1","key":"DOCS","name":"Docs"}],'
                          '"_links":{"next":"/wiki/api/v2/spaces?cursor=abc"}}',
            })
            home = self._home_with_config(tmp)
            result = subprocess.run(
                ["bash", str(self.CONFLUENCE_SEARCH), "spaces"],
                capture_output=True, text=True,
                env={"HOME": str(home),
                     "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("more exist", result.stdout)

    def test_confluence_text_search_warns_on_a_next_cursor(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            bin_dir = self._fake_curl(tmp, {
                "rest/api/search": '{"results":[{"content":{"id":"111","title":"Egress"}}],'
                                    '"_links":{"next":"/wiki/rest/api/search?cursor=xyz"}}',
            })
            home = self._home_with_config(tmp)
            result = subprocess.run(
                ["bash", str(self.CONFLUENCE_SEARCH), "text", "egress"],
                capture_output=True, text=True,
                env={"HOME": str(home),
                     "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("More results exist", result.stdout)

    def test_confluence_spaces_says_nothing_when_there_is_no_next_cursor(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            bin_dir = self._fake_curl(tmp, {
                "spaces": '{"results":[{"id":"1","key":"DOCS","name":"Docs"}]}',
            })
            home = self._home_with_config(tmp)
            result = subprocess.run(
                ["bash", str(self.CONFLUENCE_SEARCH), "spaces"],
                capture_output=True, text=True,
                env={"HOME": str(home),
                     "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("more exist", result.stdout)

    def test_jira_search_warns_on_a_next_page_token(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            bin_dir = self._fake_curl(tmp, {
                "search/jql": '{"issues":[{"key":"PAY-1","fields":{"status":{"name":"Open"},'
                              '"issuetype":{"name":"Task"},"summary":"One"}}],'
                              '"nextPageToken":"tok123"}',
            })
            home = self._home_with_config(tmp)
            result = subprocess.run(
                ["bash", str(self.JIRA_ISSUES), "search",
                 "assignee = currentUser()", "1"],
                capture_output=True, text=True,
                env={"HOME": str(home),
                     "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("More results exist", result.stdout)


class TestConfluencePagesReadStdoutIsAJustTheBody(unittest.TestCase):
    """The documented workflow is:

        confluence-pages.sh read 1234567 > /tmp/current.html
        # splice your change into /tmp/current.html
        confluence-pages.sh update 1234567 --body-file /tmp/current.html --base-version 14

    `read` used to print its "# page id ..." header on stdout ahead of the
    body, so the redirected file started with two lines of loose text -
    htmlplus.py correctly refused them ("Loose text outside any block").
    The header now goes to stderr (still visible on a terminal, where both
    streams interleave to the same tty; still readable via 2>&1 where a
    caller needs it, as publish.sh's own read calls do). This runs the
    real script against a fake curl, captures ONLY stdout (redirecting
    stderr away, exactly as `> file` does), and proves it round-trips
    through the real converter with no error - for --format html, the
    documented workflow, and --format adf, which the finding also named
    ("does not emit a standalone JSON document, so it cannot be piped to
    jq").
    """

    PAGES_SH = (REPO / "skills" / "atlassian" / "scripts"
                / "confluence-pages.sh")
    HTMLPLUS = REPO / "skills" / "atlassian" / "scripts" / "htmlplus.py"

    FAKE_CURL = """#!/bin/bash
cfg=""
while [ $# -gt 0 ]; do
  case "$1" in
    -K) cfg="$2"; shift 2 ;;
    *) shift ;;
  esac
done
hdrfile=$(grep '^dump-header' "$cfg" | sed 's/.*= "\\(.*\\)"/\\1/')
: > "$hdrfile"
printf '{"id":"1234567","title":"Test Page","status":"current","version":{"number":3},"body":{"atlas_doc_format":{"value":"{\\\\"type\\\\":\\\\"doc\\\\",\\\\"version\\\\":1,\\\\"content\\\\":[{\\\\"type\\\\":\\\\"paragraph\\\\",\\\\"content\\\\":[{\\\\"type\\\\":\\\\"text\\\\",\\\\"text\\\\":\\\\"Hello\\\\"}]}]}"}}}'
printf '\\n200'
"""

    def _bin_and_home(self, tmp):
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        fake_curl = bin_dir / "curl"
        fake_curl.write_text(self.FAKE_CURL)
        fake_curl.chmod(0o755)
        home = tmp / "home"
        config_dir = home / ".dbhq" / "atlassian"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text(
            '{"site":"https://example.atlassian.net",'
            '"email":"e@x.com","token":"secret"}'
        )
        return bin_dir, home

    def test_html_stdout_alone_converts_cleanly_the_documented_workflow(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            bin_dir, home = self._bin_and_home(tmp)
            result = subprocess.run(
                ["bash", str(self.PAGES_SH), "read", "1234567"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                env={"HOME": str(home),
                     "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
            )
            self.assertEqual(result.returncode, 0)
            # The bug's own symptom: no line of the redirected body should
            # be the header comment.
            self.assertNotIn("page id", result.stdout)
            convert = subprocess.run(
                ["python3", str(self.HTMLPLUS), "to-adf"],
                input=result.stdout, capture_output=True, text=True,
            )
            self.assertEqual(
                convert.returncode, 0,
                f"the redirected body did not convert cleanly: {convert.stderr}",
            )

    def test_adf_stdout_alone_is_standalone_json_pipeable_to_jq(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            bin_dir, home = self._bin_and_home(tmp)
            result = subprocess.run(
                ["bash", str(self.PAGES_SH), "read", "1234567", "--format", "adf"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                env={"HOME": str(home),
                     "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
            )
            self.assertEqual(result.returncode, 0)
            jq = subprocess.run(
                ["jq", "empty"], input=result.stdout, capture_output=True, text=True,
            )
            self.assertEqual(
                jq.returncode, 0,
                f"stdout was not a standalone JSON document: {jq.stderr}",
            )

    def test_the_version_header_still_prints_live_on_stderr(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            bin_dir, home = self._bin_and_home(tmp)
            result = subprocess.run(
                ["bash", str(self.PAGES_SH), "read", "1234567"],
                capture_output=True, text=True,
                env={"HOME": str(home),
                     "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("pass --base-version 3 to update", result.stderr)


class TestNoEmDash(unittest.TestCase):
    def test_house_style_forbids_them_and_the_repo_obeys(self):
        for path in TEXT_FILES:
            if path == _VALIDATE_WORKFLOW:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(EM_DASH, text, f"em dash in {path.relative_to(REPO)}")
            self.assertNotIn(EN_DASH, text, f"en dash in {path.relative_to(REPO)}")


class TestHouseStyleIsOnTheWritePath(unittest.TestCase):
    """A reference nothing is required to read is a reference nobody reads.

    This is why the house style is a reference rather than a skill of its
    own: a skill has to trigger on its own description and can silently not
    fire, where a SKILL.md instruction to read a file before writing cannot.
    SKILL.md is always loaded, and each write reference repeats the
    instruction at the point of writing.
    """

    SKILL = REPO / "skills" / "atlassian"
    WRITERS = (
        SKILL / "SKILL.md",
        SKILL / "references" / "jira.md",
        SKILL / "references" / "confluence.md",
        SKILL / "references" / "publish.md",
    )

    def test_every_write_path_reads_the_house_style(self):
        for path in self.WRITERS:
            text = path.read_text(encoding="utf-8")
            self.assertIn("${CLAUDE_SKILL_DIR}/references/house-style.md", text, path.name)
            self.assertIn("${CLAUDE_SKILL_DIR}/references/html-patterns.md", text, path.name)

    def test_every_write_path_names_the_user_override(self):
        for path in self.WRITERS:
            text = path.read_text(encoding="utf-8")
            self.assertIn("~/.dbhq/atlassian/house-style.md", text, path.name)


def _skill_dirs():
    return sorted(p.parent for p in (REPO / "skills").glob("*/SKILL.md"))


def _frontmatter(skill_md):
    text = skill_md.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---", text, re.S)
    fields = {}
    for line in match.group(1).splitlines() if match else []:
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


class TestTheSkillStandsAlone(unittest.TestCase):
    """`npx skills add` copies only a folder that holds a SKILL.md.

    Until 25 Sep 2026 this repository shipped three skills that all reached
    a fourth folder, `_shared`, with `../`. That folder had no SKILL.md, so
    skills.sh never copied it, and every script failed on its first line.
    One self-contained folder is the fix; these tests hold it.
    """

    def test_nothing_in_a_skill_reaches_outside_its_own_folder(self):
        found = []
        for skill in _skill_dirs():
            for path in sorted(skill.rglob("*")):
                if not path.is_file() or "tests" in path.relative_to(skill).parts:
                    continue
                for number, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                ):
                    if "../" in line:
                        found.append(f"{path.relative_to(REPO)}:{number}: {line.strip()}")
        self.assertEqual(found, [], "a skill reaches outside its own folder:\n" + "\n".join(found))

    FAKE_CURL = """#!/bin/bash
cfg=""
while [ $# -gt 0 ]; do
  case "$1" in
    -K) cfg="$2"; shift 2 ;;
    *) shift ;;
  esac
done
url=$(grep '^url' "$cfg" | sed 's/.*= "\\(.*\\)"/\\1/')
hdrfile=$(grep '^dump-header' "$cfg" | sed 's/.*= "\\(.*\\)"/\\1/')
: > "$hdrfile"
case "$url" in
  */rest/api/3/myself)
    printf '{"displayName":"Test User","accountId":"abc","timeZone":"Europe/London"}' ;;
  */wiki/api/v2/pages/*)
    printf '{"id":"1234567","title":"Test Page","status":"current","version":{"number":3},"body":{"atlas_doc_format":{"value":"{\\\\"type\\\\":\\\\"doc\\\\",\\\\"version\\\\":1,\\\\"content\\\\":[{\\\\"type\\\\":\\\\"paragraph\\\\",\\\\"content\\\\":[{\\\\"type\\\\":\\\\"text\\\\",\\\\"text\\\\":\\\\"Hello\\\\"}]}]}"}}}' ;;
esac
printf '\\n200'
"""

    def test_a_copy_of_the_skill_folder_alone_runs(self):
        """Copy exactly what skills.sh copies - the folder with the SKILL.md
        and nothing beside it - and run each entry point from the copy."""
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            installed = tmp / "agent" / "skills" / "atlassian"
            shutil.copytree(REPO / "skills" / "atlassian", installed,
                            ignore=shutil.ignore_patterns("tests", "__pycache__"))
            scripts = installed / "scripts"

            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            (bin_dir / "curl").write_text(self.FAKE_CURL)
            (bin_dir / "curl").chmod(0o755)
            home = tmp / "home"
            (home / ".dbhq" / "atlassian").mkdir(parents=True)
            (home / ".dbhq" / "atlassian" / "config.json").write_text(
                '{"site":"https://example.atlassian.net",'
                '"email":"e@x.com","token":"secret"}'
            )
            env = {"HOME": str(home),
                   "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}
            doc = tmp / "doc.md"
            doc.write_text('---\nconfluence:\n  space: "1"\n---\n\n# Title\n\nBody.\n')

            runs = {
                "jira-meta.sh whoami":
                    ["bash", str(scripts / "jira-meta.sh"), "whoami"],
                "confluence-pages.sh read":
                    ["bash", str(scripts / "confluence-pages.sh"), "read", "1234567"],
                "publish.sh --dry-run":
                    ["bash", str(scripts / "publish.sh"), str(doc), "--dry-run"],
            }
            for name, argv in runs.items():
                result = subprocess.run(argv, capture_output=True, text=True, env=env)
                self.assertNotIn("No such file", result.stderr, name)
                self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")

            result = subprocess.run(
                ["python3", str(scripts / "htmlplus.py"), "to-adf"],
                input="<p>Hello</p>", capture_output=True, text=True, env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"paragraph"', result.stdout)


class TestTriggerPhrases(unittest.TestCase):
    """The host picks a skill by its description. A trigger phrase in two
    descriptions leaves it guessing - "publish this to confluence" used to
    sit in two of them."""

    def _phrases(self):
        phrases = {}
        for skill in _skill_dirs():
            description = _frontmatter(skill / "SKILL.md").get("description", "")
            phrases[skill.name] = [p.lower() for p in re.findall(r'"([^"]+)"', description)]
        return phrases

    def test_every_skill_has_trigger_phrases(self):
        for name, phrases in self._phrases().items():
            self.assertTrue(phrases, f"{name} has no quoted trigger phrases")

    def test_no_trigger_phrase_is_in_two_skills_or_listed_twice(self):
        owner = {}
        for name, phrases in self._phrases().items():
            self.assertEqual(len(phrases), len(set(phrases)), f"{name} repeats a phrase")
            for phrase in phrases:
                self.assertNotIn(phrase, owner, f"{phrase!r} is in {owner.get(phrase)} and {name}")
                owner[phrase] = name

    def test_the_generic_ticket_phrases_are_tied_to_jira(self):
        # "create a ticket" or "what's assigned to me" on their own would
        # fire for GitHub, Trello or any other tracker.
        for name, phrases in self._phrases().items():
            for phrase in ("create a ticket", "raise a ticket", "what's assigned to me"):
                self.assertNotIn(phrase, phrases, name)

    def test_the_description_fits_the_agent_skills_limit(self):
        for skill in _skill_dirs():
            description = _frontmatter(skill / "SKILL.md").get("description", "")
            self.assertLessEqual(len(description), 1024, skill.name)

    def test_the_skill_is_named_after_its_folder(self):
        for skill in _skill_dirs():
            self.assertEqual(_frontmatter(skill / "SKILL.md").get("name"), skill.name)


class TestInstallersRetireTheOldLayout(unittest.TestCase):
    """install.sh and install-codex.sh put one `atlassian` skill in place, and
    remove what an earlier run of the same installer left for the four old
    folders - jira, confluence, confluence-publish and _shared. Those point
    into folders that no longer exist, so left alone they are dangling
    duplicates an agent may still match. Anything the installer did not make
    - a real folder, or a link somewhere else - is left alone, because
    `jira` and `confluence` are ordinary names a user may have used."""

    def _run(self, script, home):
        return subprocess.run(
            ["bash", str(REPO / script)], capture_output=True, text=True,
            env={"HOME": str(home), "PATH": os.environ.get("PATH", "")},
        )

    def test_install_sh(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            root = home / ".claude" / "skills"
            root.mkdir(parents=True)
            (root / "jira").symlink_to("/old/checkout/skills/jira")
            (root / "_shared").symlink_to("/old/checkout/skills/_shared")
            (root / "confluence").mkdir()
            (root / "confluence" / "SKILL.md").write_text("mine")
            (root / "confluence-publish").symlink_to("/somewhere/else")

            result = self._run("install.sh", home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                os.readlink(root / "atlassian"), str(REPO / "skills" / "atlassian"))
            self.assertFalse(os.path.lexists(root / "jira"))
            self.assertFalse(os.path.lexists(root / "_shared"))
            self.assertEqual((root / "confluence" / "SKILL.md").read_text(), "mine")
            self.assertEqual(os.readlink(root / "confluence-publish"), "/somewhere/else")
            self.assertIn("atlassian/scripts/atlassian-setup.sh", result.stdout)

    def test_install_codex_sh(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            root = home / ".codex" / "skills"
            root.mkdir(parents=True)
            old = root / "jira"
            old.mkdir()
            (old / "SKILL.md").write_text("---\nname: jira\n---\n")
            (old / "scripts").symlink_to("/old/checkout/skills/jira/scripts")
            (old / "references").symlink_to("/old/checkout/skills/jira/references")
            (root / "_shared").symlink_to("/old/checkout/skills/_shared")
            mine = root / "confluence"
            mine.mkdir()
            (mine / "SKILL.md").write_text("mine")
            (mine / "scripts").symlink_to("/old/checkout/skills/confluence/scripts")
            (mine / "notes.txt").write_text("not the installer's")

            result = self._run("install-codex.sh", home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(os.path.lexists(old))
            self.assertFalse(os.path.lexists(root / "_shared"))
            self.assertTrue((mine / "notes.txt").is_file())

            skill_md = (root / "atlassian" / "SKILL.md").read_text()
            self.assertNotIn("CLAUDE_SKILL_DIR}", skill_md)
            self.assertIn(str(root / "atlassian") + "/scripts/atlassian-setup.sh", skill_md)
            for sub in ("scripts", "references"):
                self.assertEqual(
                    os.readlink(root / "atlassian" / sub),
                    str(REPO / "skills" / "atlassian" / sub))


class TestCredentialMove(unittest.TestCase):
    """An existing install moves once, and a fresh one does not move at all."""

    def _run_common(self, home):
        import subprocess
        return subprocess.run(
            ["bash", "-c",
             f'. "{REPO}/skills/atlassian/scripts/_common.sh"; echo "$CONFIG_DIR"'],
            capture_output=True, text=True,
            env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        )

    def test_a_dbhq_jira_install_moves_to_atlassian(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            home = pathlib.Path(home)
            old = home / ".dbhq" / "jira"
            old.mkdir(parents=True)
            (old / "config.json").write_text(json.dumps({"site": "x"}))
            self._run_common(home)
            self.assertFalse(old.exists(), "the old directory was left behind")
            new = home / ".dbhq" / "atlassian" / "config.json"
            self.assertTrue(new.is_file(), "the credential did not arrive")
            self.assertEqual(json.loads(new.read_text())["site"], "x")

    def test_a_legacy_dot_jira_install_also_moves(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            home = pathlib.Path(home)
            old = home / ".jira"
            old.mkdir(parents=True)
            (old / "config.json").write_text(json.dumps({"site": "x"}))
            self._run_common(home)
            self.assertFalse(old.exists())
            self.assertTrue((home / ".dbhq" / "atlassian" / "config.json").is_file())

    def test_a_fresh_install_moves_nothing_and_does_not_fail(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            home = pathlib.Path(home)
            result = self._run_common(home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((home / ".dbhq" / "atlassian" / "config.json").exists())

    def test_it_does_not_move_twice(self):
        # An install already at ~/.dbhq/atlassian keeps what it has, even if a
        # stale ~/.dbhq/jira is still lying around.
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            home = pathlib.Path(home)
            current = home / ".dbhq" / "atlassian"
            current.mkdir(parents=True)
            (current / "config.json").write_text(json.dumps({"site": "keep"}))
            stale = home / ".dbhq" / "jira"
            stale.mkdir(parents=True)
            (stale / "config.json").write_text(json.dumps({"site": "stale"}))
            self._run_common(home)
            self.assertEqual(
                json.loads((current / "config.json").read_text())["site"], "keep"
            )
            self.assertTrue(stale.exists(), "a stale directory is left alone, not deleted")


class TestPublishIdempotency(unittest.TestCase):
    """A second publish of an unchanged file must update, never create.

    Both tests run with HOME pointed at a fresh empty directory, the same
    isolation confluence-publish/tests/test_attachments.py uses. Without
    it the bound-page_id case below is not a local test at all: publish.sh
    previews the round-trip gate by calling `confluence-pages.sh read` for
    real, which reads ~/.dbhq/atlassian/config.json and, on any machine
    where the skill is actually set up, sends a credentialed HTTPS request
    to that person's live Atlassian site. Proven with strace: one curl and
    one connection to port 443 with a real config present, none with an
    empty HOME. The assertions never depended on the response - publish.sh
    prints "UPDATE page ..." and "Nothing was sent." either way - so the
    call bought nothing and the suite passed identically without it.
    """

    def _run_dry_run(self, frontmatter_body):
        import subprocess
        import tempfile
        script = REPO / "skills" / "atlassian" / "scripts" / "publish.sh"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(frontmatter_body)
            path = f.name
        return subprocess.run(
            ["bash", str(script), path, "--dry-run"],
            capture_output=True, text=True,
            env={"HOME": tempfile.mkdtemp(), "PATH": os.environ.get("PATH", "")},
        )

    def test_publish_dry_run_reports_update_when_a_page_id_is_bound(self):
        result = self._run_dry_run(
            '---\nconfluence:\n  space: "1"\n  page_id: "8901234"\n---\n\n'
            "# Title\n\nBody.\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("UPDATE page 8901234", result.stdout)
        self.assertIn("Nothing was sent.", result.stdout)

    def test_publish_dry_run_reports_create_when_no_page_id_is_bound(self):
        result = self._run_dry_run(
            '---\nconfluence:\n  space: "1"\n---\n\n# Title\n\nBody.\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CREATE", result.stdout)


class TestPublishRefusesAnEmptyBody(unittest.TestCase):
    """publish.sh must never replace a page's content with the banner alone.

    Both routes to an empty $BODY are covered: a file that converts to
    nothing on its own merits, and a pipeline failure inside the
    frontmatter.py | md_to_htmlplus.py step that `set -o pipefail` now turns
    into a stopped script rather than a silently empty $BODY.
    """

    SCRIPT = (REPO / "skills" / "atlassian" / "scripts"
              / "publish.sh")
    FRONTMATTER_PY = (REPO / "skills" / "atlassian" / "scripts"
                       / "frontmatter.py")
    MD_TO_HTMLPLUS_PY = (REPO / "skills" / "atlassian" / "scripts"
                          / "md_to_htmlplus.py")

    def _write(self, content, mode="w", **kwargs):
        import tempfile
        f = tempfile.NamedTemporaryFile(mode, suffix=".md", delete=False, **kwargs)
        f.write(content)
        f.close()
        return f.name

    def test_a_file_that_is_only_frontmatter_is_refused_not_published(self):
        path = self._write('---\nconfluence:\n  space: "1"\n---\n')
        result = subprocess.run(
            ["bash", str(self.SCRIPT), path, "--dry-run"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Error:", result.stderr)
        self.assertIn("empty", result.stderr)
        # Never reaches the point of reporting a dry-run action - nothing
        # about this file should look like a normal, previewable publish.
        self.assertNotIn("CREATE", result.stdout)
        self.assertNotIn("UPDATE", result.stdout)

    def test_a_body_that_is_only_an_html_comment_is_also_refused(self):
        # Non-empty bytes, but htmlplus.py's HTMLParser silently drops a
        # comment, so this converts to {"content":[]} the same as a
        # zero-byte body - proving the guard checks the converted ADF, not
        # merely whether $BODY has any bytes in it.
        path = self._write(
            '---\nconfluence:\n  space: "1"\n---\n\n<!-- nothing but a comment -->\n'
        )
        result = subprocess.run(
            ["bash", str(self.SCRIPT), path, "--dry-run"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Error:", result.stderr)

    def test_publish_sh_enables_pipefail(self):
        text = self.SCRIPT.read_text(encoding="utf-8")
        self.assertIn("set -o pipefail", text)

    def test_pipefail_catches_a_crashed_first_stage_of_the_conversion_pipe(self):
        # Reproduces the underlying failure directly: a file frontmatter.py
        # cannot decode makes it crash, and without pipefail the pipe's exit
        # status is md_to_htmlplus.py's alone - which succeeds, on the empty
        # input it received because the first stage never wrote anything.
        # publish.sh now runs this exact shape of pipeline under
        # `set -o pipefail`, so the pipe as a whole must fail.
        bad = self._write(b"\xff\xfe not valid utf-8 in the body\n", mode="wb")
        with_pipefail = subprocess.run(
            ["bash", "-c",
             f'set -e -o pipefail; '
             f'python3 "{self.FRONTMATTER_PY}" body "{bad}" '
             f'| python3 "{self.MD_TO_HTMLPLUS_PY}" > /dev/null'],
        )
        self.assertNotEqual(
            with_pipefail.returncode, 0,
            "set -o pipefail did not catch the crashed first stage of the pipe",
        )


class TestTokenNeverReachesProcessArgv(unittest.TestCase):
    """atlassian-setup.sh writes the token through jq's environment, not
    --arg. jq's own argv is a separate process's command line, and
    /proc/<pid>/cmdline is world-readable - the exact leak the "token never
    reaches a command line" rule elsewhere in this repository exists to
    close for curl, and had been left open here for jq.
    """

    SETUP_SH = REPO / "skills" / "atlassian" / "scripts" / "atlassian-setup.sh"

    def test_the_saved_token_never_appears_in_jqs_own_argv(self):
        import json
        import os
        import shutil
        import tempfile

        text = self.SETUP_SH.read_text(encoding="utf-8")
        match = re.search(
            r'UMASK_OLD=\$\(umask\).*?mv "\$TMP_CONFIG" "\$CONFIG_FILE"\n',
            text, re.S,
        )
        self.assertIsNotNone(
            match,
            "atlassian-setup.sh's credential-write block has changed shape "
            "- update this test's extraction pattern to match.",
        )
        write_block = match.group(0)

        real_jq = shutil.which("jq")
        self.assertIsNotNone(real_jq, "jq must be on PATH to run this test")

        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            config_dir = tmp / "config"
            config_dir.mkdir()
            config_file = config_dir / "config.json"
            argv_log = tmp / "jq-argv.log"

            # A fake jq on PATH ahead of the real one, so the write block
            # under test calls this instead - it logs its own argv, exactly
            # what /proc/<pid>/cmdline would have carried, then delegates to
            # the real jq so the write block's own behaviour is unaffected.
            fake_bin = tmp / "bin"
            fake_bin.mkdir()
            fake_jq = fake_bin / "jq"
            fake_jq.write_text(
                "#!/bin/bash\n"
                f'printf \'%s\\n\' "$*" >> "{argv_log}"\n'
                f'exec "{real_jq}" "$@"\n'
            )
            fake_jq.chmod(0o755)

            secret = "super-secret-token-value"
            harness = (
                'CONFIG_DIR="$1"; CONFIG_FILE="$2"; SITE="$3"; EMAIL="$4"; '
                'TOKEN="$5"; CONFLUENCE="$6"\n' + write_block
            )
            result = subprocess.run(
                ["bash", "-c", harness, "bash",
                 str(config_dir), str(config_file),
                 "https://example.atlassian.net", "dan@example.com",
                 secret, "yes"],
                capture_output=True, text=True,
                env={"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(config_file.is_file(), result.stderr)
            saved = json.loads(config_file.read_text())
            self.assertEqual(saved["token"], secret)

            argv_text = argv_log.read_text() if argv_log.exists() else ""
            self.assertNotIn(
                secret, argv_text,
                "the token reached jq's own argv - /proc/<pid>/cmdline "
                "would have carried it in plain text",
            )

    def test_a_jq_failure_does_not_destroy_an_existing_credential(self):
        import tempfile

        text = self.SETUP_SH.read_text(encoding="utf-8")
        match = re.search(
            r'UMASK_OLD=\$\(umask\).*?mv "\$TMP_CONFIG" "\$CONFIG_FILE"\n',
            text, re.S,
        )
        self.assertIsNotNone(match)
        write_block = match.group(0)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            config_dir = tmp / "config"
            config_dir.mkdir()
            config_file = config_dir / "config.json"
            existing = (
                '{"site":"https://old.atlassian.net","email":"old@x.com",'
                '"token":"old-token"}'
            )
            config_file.write_text(existing)

            # A jq that always fails - a bad value, a crash, anything short
            # of this process being killed outright (a signal is I2's
            # trap's job, not this one's). Writing straight to $CONFIG_FILE
            # with `jq ... > "$CONFIG_FILE"` truncates it the instant the
            # shell opens that redirection, before jq runs at all - so a
            # failure here used to destroy a working credential and leave
            # nothing in its place.
            fake_bin = tmp / "bin"
            fake_bin.mkdir()
            fake_jq = fake_bin / "jq"
            fake_jq.write_text("#!/bin/bash\nexit 1\n")
            fake_jq.chmod(0o755)

            harness = (
                'CONFIG_DIR="$1"; CONFIG_FILE="$2"; SITE="$3"; EMAIL="$4"; '
                'TOKEN="$5"; CONFLUENCE="$6"\n' + write_block
            )
            result = subprocess.run(
                ["bash", "-c", harness, "bash",
                 str(config_dir), str(config_file),
                 "https://new.atlassian.net", "new@example.com",
                 "new-token", "yes"],
                capture_output=True, text=True,
                env={"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
            )
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(
                config_file.read_text(), existing,
                "the existing credential was destroyed by a failed write",
            )
            leftovers = [p for p in config_dir.iterdir() if p != config_file]
            self.assertEqual(
                leftovers, [], f"leftover temp file(s): {leftovers}"
            )


class TestTokenTempFileCleanupOnSignal(unittest.TestCase):
    """A curl config file naming the token must not survive a signal.

    The `rm -f` at the end of each of api(), attachments.sh and
    atlassian-setup.sh's verify() only runs on a normal return. A signal -
    Ctrl-C while curl is mid-request, a killed parent - skips straight past
    it unless a trap on EXIT/INT/TERM/HUP cleans up independently of how the
    function was left. This exercises that directly: a fake, slow curl gives
    the test a window to send a real signal while the config file exists,
    then checks it is gone.
    """

    def _wait_for_written_config(self, directory, before, timeout=20):
        """The new temp file, once it actually holds the token.

        Waiting for the file to merely *exist* is a race, and it is the
        race that made these two tests flake: every one of the three
        scripts does `cfg=$(mktemp)`, then `chmod 600`, then installs the
        trap, and only then writes the `user = "email:TOKEN"` line. A
        signal landing in the gap between mktemp and trap kills bash at
        its default disposition and leaves the file behind - correctly,
        and harmlessly, because at that instant the file is still empty.
        The property under test is "a file NAMING THE TOKEN must not
        survive a signal", so wait until the file is non-empty: by then
        the token is in it and the trap is necessarily installed, which
        is exactly the window the trap is there to cover.
        """
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            for name in set(os.listdir(directory)) - before:
                path = directory / name
                try:
                    if path.stat().st_size > 0:
                        return path
                except OSError:
                    pass
            time.sleep(0.02)
        return None

    def _assert_cleans_up_on_term(self, argv, env):
        import signal
        import tempfile
        import time
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            env = dict(env)
            env["TMPDIR"] = str(tmp)
            before = set(os.listdir(tmp))
            # A real Ctrl-C delivers SIGINT to the whole foreground process
            # group at once - the script AND the curl (here, faked sleep)
            # it is waiting on - which is what actually unblocks bash's
            # wait() promptly: the trap is deferred until the child bash is
            # waiting on exits, and only a process-group-wide signal makes
            # that child exit right away rather than after its own full
            # duration. start_new_session=True gives this process its own
            # group so the signal can be aimed at exactly it (and nothing
            # belonging to the test runner itself), and killpg reproduces
            # that whole-group delivery rather than sending to the script's
            # own PID alone, which would leave it still blocked on its
            # child.
            proc = subprocess.Popen(
                argv, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                cfg = self._wait_for_written_config(tmp, before)
                self.assertIsNotNone(
                    cfg, "the curl config file never appeared with content in it"
                )
                self.assertIn(
                    "secret", cfg.read_text(),
                    "the file this test signals over does not name the token - "
                    "it is not the file the trap exists to remove",
                )
                # Signal until the file goes, not once - and wait on the
                # FILE, never on the process. Both details are what an
                # earlier version of this test got wrong, and each caused
                # its own flake:
                #
                # - Signalling once races the fork. The script writes the
                #   config file and only then starts curl, so a group-wide
                #   signal aimed the instant the file appears can land
                #   before curl is forked - and a process forked after the
                #   signal never receives it. Confirmed by listing the
                #   group afterwards: bash, curl and sleep all still there,
                #   none of them signalled. bash defers a trapped signal
                #   until the running foreground command returns, so the
                #   pending TERM then waits out curl's full sleep. Pressing
                #   Ctrl-C again is exactly what a real user does when the
                #   first one appears to do nothing.
                # - Requiring the process to EXIT asserts something bash
                #   does not do: it runs a trapped signal's handler and
                #   carries on. The file being gone is the property; the
                #   process is killed in the finally block either way.
                pgid = os.getpgid(proc.pid)
                deadline = time.time() + 15
                while cfg.exists() and time.time() < deadline:
                    try:
                        os.killpg(pgid, signal.SIGTERM)
                    except ProcessLookupError:
                        break
                    time.sleep(0.1)
                self.assertFalse(
                    cfg.exists(),
                    f"{cfg} still exists after SIGTERM - the trap did not "
                    f"clean it up",
                )
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()

    def test_common_sh_api_cleans_up_its_config_file_on_sigterm(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as bin_dir, \
             tempfile.TemporaryDirectory() as home:
            bin_dir = pathlib.Path(bin_dir)
            fake_curl = bin_dir / "curl"
            fake_curl.write_text("#!/bin/bash\nsleep 30\n")
            fake_curl.chmod(0o755)
            common_sh = REPO / "skills" / "atlassian" / "scripts" / "_common.sh"
            harness = (
                f'. "{common_sh}"; SITE=https://example.atlassian.net; '
                f'EMAIL=e@x.com; TOKEN=secret; api GET /rest/api/3/myself'
            )
            self._assert_cleans_up_on_term(
                ["bash", "-c", harness],
                {"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
                 "HOME": home},
            )

    def test_attachments_sh_cleans_up_its_config_file_on_sigterm(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as bin_dir, \
             tempfile.TemporaryDirectory() as home:
            bin_dir = pathlib.Path(bin_dir)
            fake_curl = bin_dir / "curl"
            fake_curl.write_text("#!/bin/bash\nsleep 30\n")
            fake_curl.chmod(0o755)

            home = pathlib.Path(home)
            config_dir = home / ".dbhq" / "atlassian"
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text(
                '{"site":"https://example.atlassian.net",'
                '"email":"e@x.com","token":"secret"}'
            )
            upload_file = home / "diagram.png"
            upload_file.write_text("data")

            script = (REPO / "skills" / "atlassian" / "scripts"
                      / "attachments.sh")
            self._assert_cleans_up_on_term(
                ["bash", str(script), "upload", "1234567", str(upload_file)],
                {"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
                 "HOME": str(home)},
            )


class TestTheDocsMatchTheCode(unittest.TestCase):
    """Small claims in the public docs that had drifted from the code."""

    def test_the_manifest_does_not_call_an_updating_skill_read_only(self):
        import json
        manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
        description = manifest["description"].lower()
        self.assertIn("update", description)
        self.assertNotIn("read only", description)
        self.assertNotIn("create and read only", description)

    def test_security_names_every_v1_endpoint_the_scripts_call(self):
        scripts = REPO / "skills" / "atlassian" / "scripts"
        called = set()
        for script in scripts.glob("*.sh"):
            text = script.read_text(encoding="utf-8")
            if "/wiki/rest/api/search" in text:
                called.add("CQL search")
            if "/child/attachment" in text:
                called.add("attachment upload")
        self.assertEqual(called, {"CQL search", "attachment upload"})
        security = " ".join((REPO / "SECURITY.md").read_text(encoding="utf-8").split())
        for name in called:
            self.assertIn(name, security)
        self.assertNotIn("the one place", security)

    def test_the_gate_refusal_figures_are_stated_once(self):
        # They drifted apart when four files each kept their own copy:
        # "roughly half", "roughly 60% of 289", and a present-tense claim in
        # a script. The README states them, with dates. Everything else
        # points there, or at the audit command.
        figures = re.compile(r"\b289\b|60%|roughly half")
        holders = sorted(
            str(path.relative_to(REPO)) for path in TEXT_FILES
            if path.suffix in (".md", ".sh", ".json")
            and figures.search(path.read_text(encoding="utf-8", errors="replace"))
        )
        self.assertEqual(holders, ["README.md"])

    def test_the_confluence_reference_documents_the_audit(self):
        text = (REFS / "confluence.md").read_text(encoding="utf-8")
        self.assertIn("confluence-pages.sh audit --cql", text)

    def test_the_readme_lists_each_sibling_skill_once(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        section = readme.split("## Also from DBHQ", 1)[1].split("\n## ", 1)[0]
        links = re.findall(r"\(https://skills\.dbhq\.uk/([a-z]+)/\)", section)
        self.assertTrue(links)
        repeated = sorted({name for name in links if links.count(name) > 1})
        self.assertEqual(repeated, [])


if __name__ == "__main__":
    unittest.main()
