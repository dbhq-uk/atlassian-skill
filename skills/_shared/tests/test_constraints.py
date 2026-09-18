"""Repo-wide constraints. These hold the promises the README makes."""

import os
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


class TestPublishVersionParserTracksConfluencePagesWording(unittest.TestCase):
    """publish.sh scrapes a plain-text line confluence-pages.sh prints, with
    nothing else asserting the two agree - a coupling across two files that
    a wording change on either side breaks silently, not loudly:
    publish.sh's BASE_VERSION just comes back empty and a later, more
    confusing error fires instead of this one. This runs the real sed
    command lifted from publish.sh against a sample of the real header line
    lifted from confluence-pages.sh, so a future edit to either file's
    wording fails this test directly rather than being caught by hand.
    """

    PAGES_SH = (REPO / "skills" / "confluence" / "scripts"
                / "confluence-pages.sh")
    PUBLISH_SH = (REPO / "skills" / "confluence-publish" / "scripts"
                  / "publish.sh")

    def test_publish_sh_sed_extracts_confluence_pages_sh_header(self):
        pages_text = self.PAGES_SH.read_text(encoding="utf-8")
        publish_text = self.PUBLISH_SH.read_text(encoding="utf-8")

        header = re.search(
            r'echo "(# page id \$PAGE_ID, version \$VERSION[^"]*)"', pages_text
        )
        self.assertIsNotNone(
            header,
            "confluence-pages.sh no longer prints the header publish.sh "
            "parses - update the sed below (or this pattern) to match.",
        )
        sample_line = (
            header.group(1)
            .replace("$PAGE_ID", "1234567")
            .replace("$VERSION", "14")
        )

        sed_script = re.search(
            r"sed -n '(s/\^# page id.*?/p)'", publish_text, re.S
        )
        self.assertIsNotNone(
            sed_script,
            "publish.sh no longer parses the version with this sed command "
            "- update this test to match whatever replaced it.",
        )

        result = subprocess.run(
            ["sed", "-n", sed_script.group(1)],
            input=sample_line + "\n",
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(
            result.stdout.strip(), "14",
            "publish.sh's version sed no longer extracts the version number "
            "from confluence-pages.sh's actual header line.",
        )


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

    This is why the house style is a reference rather than a fourth skill: a
    skill has to trigger on its own description and can silently not fire,
    where a SKILL.md instruction to read a file before writing cannot.
    """

    WRITERS = ("confluence", "confluence-publish", "jira")

    def test_every_write_path_reads_the_house_style(self):
        for name in self.WRITERS:
            skill = REPO / "skills" / name / "SKILL.md"
            text = skill.read_text(encoding="utf-8")
            self.assertIn("_shared/references/house-style.md", text, name)
            self.assertIn("_shared/references/html-patterns.md", text, name)

    def test_every_write_path_names_the_user_override(self):
        for name in self.WRITERS:
            text = (REPO / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("~/.dbhq/atlassian/house-style.md", text, name)


class TestCredentialMove(unittest.TestCase):
    """An existing install moves once, and a fresh one does not move at all."""

    def _run_common(self, home):
        import subprocess
        return subprocess.run(
            ["bash", "-c",
             f'. "{REPO}/skills/_shared/scripts/_common.sh"; echo "$CONFIG_DIR"'],
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
    """A second publish of an unchanged file must update, never create."""

    def test_publish_dry_run_reports_update_when_a_page_id_is_bound(self):
        import subprocess
        import tempfile
        script = REPO / "skills" / "confluence-publish" / "scripts" / "publish.sh"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(
                '---\nconfluence:\n  space: "1"\n  page_id: "8901234"\n---\n\n'
                "# Title\n\nBody.\n"
            )
            path = f.name
        result = subprocess.run(
            ["bash", str(script), path, "--dry-run"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("UPDATE page 8901234", result.stdout)
        self.assertIn("Nothing was sent.", result.stdout)

    def test_publish_dry_run_reports_create_when_no_page_id_is_bound(self):
        import subprocess
        import tempfile
        script = REPO / "skills" / "confluence-publish" / "scripts" / "publish.sh"
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write('---\nconfluence:\n  space: "1"\n---\n\n# Title\n\nBody.\n')
            path = f.name
        result = subprocess.run(
            ["bash", str(script), path, "--dry-run"],
            capture_output=True, text=True,
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

    SCRIPT = (REPO / "skills" / "confluence-publish" / "scripts"
              / "publish.sh")
    FRONTMATTER_PY = (REPO / "skills" / "confluence-publish" / "scripts"
                       / "frontmatter.py")
    MD_TO_HTMLPLUS_PY = (REPO / "skills" / "confluence-publish" / "scripts"
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

    SETUP_SH = REPO / "skills" / "_shared" / "scripts" / "atlassian-setup.sh"

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

    def _wait_for_new_file(self, directory, before, timeout=5):
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = set(os.listdir(directory)) - before
            if found:
                return directory / next(iter(found))
            time.sleep(0.05)
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
                cfg = self._wait_for_new_file(tmp, before)
                self.assertIsNotNone(cfg, "the curl config file never appeared")
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=15)
                deadline = time.time() + 5
                while cfg.exists() and time.time() < deadline:
                    time.sleep(0.05)
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
            common_sh = REPO / "skills" / "_shared" / "scripts" / "_common.sh"
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

            script = (REPO / "skills" / "confluence-publish" / "scripts"
                      / "attachments.sh")
            self._assert_cleans_up_on_term(
                ["bash", str(script), "upload", "1234567", str(upload_file)],
                {"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
                 "HOME": str(home)},
            )


if __name__ == "__main__":
    unittest.main()
