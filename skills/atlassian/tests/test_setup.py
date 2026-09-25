"""atlassian-setup.sh, end to end, against a fake curl.

An agent that finds no credential is told to run setup, and an agent's shell
has no terminal. Setup used to hit end of input on its first prompt and exit
1 with no message. These tests run it the way an agent would, and in its
scripted form, with HOME pointed at a throwaway directory and a fake curl
that answers from a fixture, so nothing here can reach a real site.
"""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest

SKILL = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
SETUP = SCRIPTS / "atlassian-setup.sh"
REPO = SKILL.parents[1]

TOKEN = "fake-token-value-123"

# Logs each request's URL, its credential line and its own argv, then
# answers FAKE_STATUS for Jira and 200 for Confluence.
FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, re, sys
args = sys.argv[1:]
conf = {}
for line in open(args[args.index("-K") + 1], encoding="utf-8"):
    m = re.match(r'^(\S+) = "(.*)"$', line.strip())
    if m:
        conf.setdefault(m.group(1), m.group(2))
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"url": conf["url"], "user": conf.get("user", ""),
                          "argv": sys.argv}) + "\n")
if "/rest/api/3/myself" in conf["url"]:
    status = os.environ.get("FAKE_STATUS", "200")
    sys.stdout.write('{"displayName":"Test User","accountId":"acc-1"}\n' + status)
elif "/wiki/api/v2/spaces" in conf["url"]:
    sys.stdout.write('{"results":[]}\n200')
else:
    sys.stdout.write('{"projects":[],"values":[],"isLast":true}\n200')
'''


class _Harness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        (self.bin / "curl").write_text(FAKE_CURL)
        (self.bin / "curl").chmod(0o755)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.config = self.home / ".dbhq" / "atlassian" / "config.json"
        self.log = self.tmp / "requests.jsonl"
        self.env = {"HOME": str(self.home),
                    "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
                    "FAKE_LOG": str(self.log)}

    def run_script(self, script, *args, stdin=None, env=None):
        full_env = dict(self.env, **(env or {}))
        return subprocess.run(
            ["bash", str(script), *args], capture_output=True, text=True,
            input=stdin if stdin is not None else "",
            env=full_env, cwd=self.tmp, timeout=60)

    def requests(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def scripted(self, *extra, token=TOKEN + "\n", env=None):
        return self.run_script(
            SETUP, "--site", "example.atlassian.net/", "--email", "e@example.com",
            *extra, stdin=token, env=env)


class TestNoTerminal(_Harness):
    def test_it_says_where_to_run_it_and_exits_non_zero(self):
        # Exactly what an agent's shell gives it: stdin at end of input, and
        # a HOME with no credential. It used to exit 1 with no message.
        result = self.run_script(SETUP, stdin="")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("run this in your own terminal:", result.stderr)
        self.assertIn(f"  {SETUP}\n", result.stderr)
        self.assertTrue(pathlib.Path(SETUP).is_absolute())
        self.assertIn("--site", result.stderr)
        self.assertFalse(self.config.exists())
        self.assertEqual(self.requests(), [])

    def test_a_script_with_no_credential_prints_the_full_setup_path(self):
        result = self.run_script(SCRIPTS / "jira-issues.sh", "get", "PAY-12")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("own terminal", result.stderr)
        self.assertIn(f"  {SETUP}\n", result.stderr)


class TestScripted(_Harness):
    def test_the_token_on_stdin_stores_a_working_credential(self):
        result = self.scripted()
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(self.config.read_text())
        self.assertEqual(saved, {"site": "https://example.atlassian.net",
                                 "email": "e@example.com", "token": TOKEN,
                                 "confluence": True, "scoped": False,
                                 "cloud_id": "",
                                 "jira_base": "https://example.atlassian.net",
                                 "confluence_base": "https://example.atlassian.net"})
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.config.parent.stat().st_mode & 0o777, 0o700)

        # Working: the next script authenticates with exactly what was saved.
        self.log.unlink()
        result = self.run_script(SCRIPTS / "jira-meta.sh", "projects")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.requests()
        self.assertTrue(calls)
        self.assertTrue(calls[0]["url"].startswith("https://example.atlassian.net/rest/api/3/"))
        self.assertEqual(calls[0]["user"], f"e@example.com:{TOKEN}")

    def test_the_token_never_reaches_curls_argv(self):
        self.assertEqual(self.scripted().returncode, 0)
        for call in self.requests():
            self.assertNotIn(TOKEN, " ".join(call["argv"]))
            # The cloud id lookup is public and is sent no credential.
            expected = "" if call["url"].endswith("/_edge/tenant_info") else f"e@example.com:{TOKEN}"
            self.assertEqual(call["user"], expected)

    def test_a_token_file_with_no_final_newline_still_works(self):
        result = self.scripted(token=TOKEN)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.config.read_text())["token"], TOKEN)

    def test_an_empty_token_is_refused_and_nothing_is_saved(self):
        result = self.scripted(token="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the token is required", result.stderr)
        self.assertFalse(self.config.exists())
        self.assertEqual(self.requests(), [])

    def test_a_rejected_token_saves_nothing(self):
        result = self.scripted(env={"FAKE_STATUS": "401"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Nothing was saved", result.stderr)
        self.assertFalse(self.config.exists())

    def test_site_without_email_is_refused(self):
        result = self.run_script(SETUP, "--site", "https://example.atlassian.net",
                                 stdin=TOKEN)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--site and --email go together", result.stderr)
        self.assertEqual(self.requests(), [])

    def test_an_unknown_option_is_refused(self):
        result = self.run_script(SETUP, "--token", TOKEN)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown option '--token'", result.stderr)
        self.assertEqual(self.requests(), [])

    def test_an_existing_credential_is_kept_without_overwrite(self):
        self.config.parent.mkdir(parents=True)
        existing = '{"site":"https://old.atlassian.net","email":"o@x.com","token":"old"}'
        self.config.write_text(existing)
        result = self.scripted()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--overwrite", result.stderr)
        self.assertEqual(self.config.read_text(), existing)
        self.assertEqual(self.requests(), [])

    def test_overwrite_replaces_it_once_the_new_one_verifies(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{"site":"https://old.atlassian.net","email":"o@x.com","token":"old"}')
        result = self.scripted("--overwrite")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.config.read_text())["token"], TOKEN)


class TestSetupPathsInTheDocs(unittest.TestCase):
    """Every setup path the README and SKILL.md give exists for its install."""

    # Where each install method puts the skill folder, mapped back to this
    # repository. The plugin cache holds the whole repository per commit.
    PREFIXES = {
        "${CLAUDE_SKILL_DIR}/": SKILL,
        "~/.claude/skills/atlassian/": SKILL,
        "~/.codex/skills/atlassian/": SKILL,
        "~/.claude/plugins/cache/dbhq/atlassian/<commit>/": REPO,
        "<skill-folder>/": SKILL,
        "skills/atlassian/": SKILL,  # the README's table of repository files
    }

    def _paths(self, text):
        import re
        return re.findall(r"[^\s`(|]*atlassian-setup\.sh", text)

    def test_every_setup_path_resolves_to_the_script(self):
        docs = [REPO / "README.md", SKILL / "SKILL.md",
                *sorted((SKILL / "references").glob("*.md"))]
        seen = 0
        for doc in docs:
            for path in self._paths(doc.read_text(encoding="utf-8")):
                if "/" not in path:
                    continue  # a bare script name in prose, not a path
                seen += 1
                for prefix, root in self.PREFIXES.items():
                    if path.startswith(prefix):
                        target = root / path[len(prefix):]
                        self.assertTrue(target.is_file(),
                                        f"{doc.name}: {path} -> {target} does not exist")
                        break
                else:
                    self.fail(f"{doc.name}: {path} is not under any install's skill folder")
        self.assertGreaterEqual(seen, 5)

    def test_the_skill_tells_the_agent_not_to_run_setup(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Do not run setup yourself", text)
        self.assertIn("own terminal", text)


if __name__ == "__main__":
    unittest.main()
