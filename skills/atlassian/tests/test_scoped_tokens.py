"""Scoped API tokens, end to end, against a fake curl.

A classic API token calls the site. A scoped token must call Atlassian's
gateway, api.atlassian.com/ex/<product>/<cloud id>, and the site answers it
with 401. These tests check that setup tells the two apart and that every
request then goes to the right base. HOME points at a throwaway directory
and curl is a fake that answers from fixtures, so nothing here can reach a
real site.
"""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest

SKILL = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"

SITE = "https://example.atlassian.net"
CLOUD_ID = "11111111-2222-3333-4444-555555555555"
JIRA_GATEWAY = f"https://api.atlassian.com/ex/jira/{CLOUD_ID}"
CONFLUENCE_GATEWAY = f"https://api.atlassian.com/ex/confluence/{CLOUD_ID}"
TOKEN = "fake-scoped-token-123"

# Logs each request's URL and credential line. The site answers /myself
# with FAKE_SITE_STATUS and the gateway with FAKE_GATEWAY_STATUS; everything
# else gets a small fixture that is enough for the script under test.
FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, re, sys
args = sys.argv[1:]
conf = {}
for line in open(args[args.index("-K") + 1], encoding="utf-8"):
    m = re.match(r'^(\S+) = "(.*)"$', line.strip())
    if m:
        conf.setdefault(m.group(1), m.group(2))
if "dump-header" in conf:
    open(conf["dump-header"], "w").close()
if "data-binary" in conf:
    sys.stdin.read()
url = conf["url"]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"url": url, "user": conf.get("user")}) + "\n")
gateway = url.startswith("https://api.atlassian.com/")
adf = json.dumps({"type": "doc", "version": 1, "content": [
    {"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]}]})
if url.endswith("/_edge/tenant_info"):
    status, body = 200, json.dumps({"cloudId": os.environ.get("FAKE_CLOUD_ID", "")})
elif "/rest/api/3/myself" in url:
    status = os.environ.get("FAKE_GATEWAY_STATUS" if gateway else "FAKE_SITE_STATUS", "200")
    body = '{"displayName":"Test User","accountId":"acc-1"}'
elif "/rest/api/3/issue/PAY-12" in url:
    status = os.environ.get("FAKE_ISSUE_STATUS", "200")
    body = json.dumps({"key": "PAY-12", "fields": {
        "summary": "S", "issuetype": {"name": "Task"}, "status": {"name": "To Do"},
        "assignee": None, "priority": {"name": "High"}, "labels": [],
        "updated": "2026-09-20T10:00:00.000+0000", "description": None}})
elif "/attachments?" in url:
    status, body = 200, '{"results":[]}'
elif "/child/attachment" in url:
    status, body = 200, '{"results":[{"extensions":{"fileId":"f-1","collectionName":"contentId-1234567"}}]}'
elif "/wiki/api/v2/pages/" in url:
    status, body = 200, json.dumps({"id": "1234567", "title": "T", "status": "current",
        "version": {"number": 3}, "body": {"atlas_doc_format": {"value": adf}}})
else:
    status, body = 200, '{"results":[],"values":[],"isLast":true}'
sys.stdout.write(body + "\n" + str(status))
'''


class _Harness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = pathlib.Path(self._tmp.name)
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
                    "FAKE_LOG": str(self.log), "FAKE_CLOUD_ID": CLOUD_ID}

    def run_script(self, name, *args, stdin="", **env):
        return subprocess.run(
            ["bash", str(SCRIPTS / name), *args], capture_output=True, text=True,
            input=stdin, env={**self.env, **env}, cwd=self.tmp, timeout=60)

    def urls(self):
        if not self.log.exists():
            return []
        return [json.loads(line)["url"] for line in self.log.read_text().splitlines()]

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def setup(self, **env):
        return self.run_script("atlassian-setup.sh", "--site", SITE,
                               "--email", "e@example.com", stdin=TOKEN + "\n", **env)

    def save_config(self, **extra):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps(
            {"site": SITE, "email": "e@example.com", "token": TOKEN, **extra}))


class TestSetupTellsTheTokensApart(_Harness):
    def test_a_token_the_site_refuses_but_the_gateway_takes_is_saved_as_scoped(self):
        result = self.setup(FAKE_SITE_STATUS="401")
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(self.config.read_text())
        self.assertTrue(saved["scoped"])
        self.assertEqual(saved["cloud_id"], CLOUD_ID)
        self.assertEqual(saved["jira_base"], JIRA_GATEWAY)
        self.assertEqual(saved["confluence_base"], CONFLUENCE_GATEWAY)
        self.assertTrue(saved["confluence"])
        urls = self.urls()
        self.assertIn(f"{JIRA_GATEWAY}/rest/api/3/myself", urls)
        self.assertIn(f"{CONFLUENCE_GATEWAY}/wiki/api/v2/spaces?limit=1", urls)
        self.assertIn("Scoped token: yes", result.stdout)

    def test_a_classic_token_calls_the_site_and_never_the_gateway(self):
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(self.config.read_text())
        self.assertFalse(saved["scoped"])
        self.assertEqual(saved["jira_base"], SITE)
        self.assertEqual(saved["confluence_base"], SITE)
        self.assertFalse(any("api.atlassian.com" in u for u in self.urls()))

    def test_the_cloud_id_is_read_without_sending_the_credential(self):
        self.setup()
        tenant = [c for c in self.calls() if c["url"].endswith("/_edge/tenant_info")]
        self.assertEqual(len(tenant), 1)
        self.assertIsNone(tenant[0]["user"])

    def test_refused_at_both_saves_nothing_and_mentions_expiry(self):
        result = self.setup(FAKE_SITE_STATUS="401", FAKE_GATEWAY_STATUS="401")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expired", result.stderr)
        self.assertIn("one year", result.stderr)
        self.assertFalse(self.config.exists())

    def test_a_cloud_id_that_is_not_a_uuid_is_never_put_into_a_url(self):
        result = self.setup(FAKE_SITE_STATUS="401", FAKE_CLOUD_ID='x"\nurl = "https://evil')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("api.atlassian.com" in u for u in self.urls()))
        self.assertFalse(self.config.exists())


class TestRequestsGoToTheRightBase(_Harness):
    def scoped(self):
        self.save_config(scoped=True, cloud_id=CLOUD_ID,
                         jira_base=JIRA_GATEWAY, confluence_base=CONFLUENCE_GATEWAY)

    def test_jira_calls_the_jira_gateway(self):
        self.scoped()
        result = self.run_script("jira-issues.sh", "get", "PAY-12", "--comments", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.urls()[0].split("?")[0],
                         f"{JIRA_GATEWAY}/rest/api/3/issue/PAY-12")
        # The link a person clicks is still the site, not the gateway.
        self.assertIn(f"{SITE}/browse/PAY-12", result.stdout)

    def test_confluence_v2_calls_the_confluence_gateway(self):
        self.scoped()
        result = self.run_script("confluence-pages.sh", "read", "1234567")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.urls()[0].split("?")[0],
                         f"{CONFLUENCE_GATEWAY}/wiki/api/v2/pages/1234567")

    def test_confluence_v1_search_calls_the_confluence_gateway(self):
        self.scoped()
        result = self.run_script("confluence-search.sh", "cql", "type = page")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.urls()[0].startswith(
            f"{CONFLUENCE_GATEWAY}/wiki/rest/api/search?"), self.urls())

    def test_the_attachment_upload_calls_the_confluence_gateway(self):
        self.scoped()
        image = self.tmp / "diagram.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        result = self.run_script("attachments.sh", "upload", "1234567", str(image))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{CONFLUENCE_GATEWAY}/wiki/rest/api/content/1234567/child/attachment",
                      self.urls())

    def test_a_credential_saved_before_scoped_tokens_still_calls_the_site(self):
        self.save_config()
        result = self.run_script("jira-issues.sh", "get", "PAY-12", "--comments", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.urls()[0].startswith(f"{SITE}/rest/api/3/issue/PAY-12"))
        result = self.run_script("confluence-pages.sh", "read", "1234567")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.urls()[-1].startswith(f"{SITE}/wiki/api/v2/pages/1234567"))

    def test_a_base_that_is_not_a_plain_https_url_is_refused(self):
        self.save_config(jira_base='http://example.test"\nurl = "https://evil')
        result = self.run_script("jira-issues.sh", "get", "PAY-12")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a plain https URL", result.stderr)
        self.assertEqual(self.urls(), [])


class TestA401MentionsExpiry(_Harness):
    def test_a_refused_token_says_it_may_have_expired(self):
        self.save_config()
        result = self.run_script("jira-issues.sh", "get", "PAY-12", FAKE_ISSUE_STATUS="401")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expired", result.stderr)
        self.assertIn("one year", result.stderr)
        self.assertIn("scope", result.stderr)
        self.assertIn(str(SCRIPTS / "atlassian-setup.sh"), result.stderr)


class TestSecurityDocDescribesScopedTokens(unittest.TestCase):
    TEXT = (SKILL.parents[1] / "SECURITY.md").read_text(encoding="utf-8")

    def test_it_no_longer_says_there_is_no_scoped_token(self):
        self.assertNotIn("does not offer", self.TEXT)
        self.assertIn("api.atlassian.com/ex/jira/", self.TEXT)
        self.assertIn("api.atlassian.com/ex/confluence/", self.TEXT)

    def test_the_recommended_scopes_hold_no_delete_scope(self):
        import re
        section = self.TEXT.split("### Scope of the token", 1)[1].split("\n## ", 1)[0]
        scopes = re.findall(r"`((?:read|write|delete|manage|search):[a-z.:-]+)`", section)
        self.assertIn("write:issue:jira", scopes)
        self.assertIn("write:page:confluence", scopes)
        recommended = [s for s in scopes if s.endswith((":jira", ":confluence"))]
        self.assertFalse([s for s in recommended if s.startswith("delete:")], recommended)


if __name__ == "__main__":
    unittest.main()
