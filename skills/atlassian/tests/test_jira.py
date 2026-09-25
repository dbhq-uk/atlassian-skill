"""jira-issues.sh, end to end, against a fake curl.

The fake stands in for the Jira API. Each test gives it a list of routes: a
method, a piece of the URL to match, and the status, body and headers to
answer with. Every request is logged and never sent anywhere. Every test
runs with HOME pointed at a throwaway credential, so nothing here can reach
a real site.
"""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest

SKILL = pathlib.Path(__file__).resolve().parents[1]
ISSUES = SKILL / "scripts" / "jira-issues.sh"

FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, re, sys
args = sys.argv[1:]
conf = {}
for line in open(args[args.index("-K") + 1], encoding="utf-8"):
    m = re.match(r'^(\S+) = "(.*)"$', line.strip())
    if m:
        conf.setdefault(m.group(1), m.group(2))
body = sys.stdin.read() if "data-binary" in conf else ""
method, url = conf.get("request", "GET"), conf["url"]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"method": method, "url": url, "body": body}) + "\n")
routes = json.load(open(os.environ["FAKE_ROUTES"], encoding="utf-8"))
state_path = os.environ["FAKE_ROUTES"] + ".used"
used = json.load(open(state_path)) if os.path.exists(state_path) else {}
for i, route in enumerate(routes):
    if route.get("method", "GET") != method or route["match"] not in url:
        continue
    if "times" in route and used.get(str(i), 0) >= route["times"]:
        continue
    used[str(i)] = used.get(str(i), 0) + 1
    json.dump(used, open(state_path, "w"))
    if "dump-header" in conf:
        with open(conf["dump-header"], "w") as h:
            for k, v in route.get("headers", {}).items():
                h.write(f"{k}: {v}\r\n")
    sys.stdout.write(route.get("body", "{}") + "\n" + str(route.get("status", 200)))
    sys.exit(0)
sys.stdout.write('{"errorMessages":["no fake route for this request"]}\n599')
'''


def text(value):
    return {"type": "text", "text": value}


def para(*content):
    return {"type": "paragraph", "content": list(content)}


# The review's fixture: a table, a nested list and a mention. The jq
# renderer that used to stand in jira get turned the table into one run of
# words, the nested list into "parentchild", and dropped the mention.
DESCRIPTION = {"type": "doc", "version": 1, "content": [
    {"type": "table", "content": [
        {"type": "tableRow", "content": [
            {"type": "tableHeader", "attrs": {}, "content": [para(text("Port"))]},
            {"type": "tableHeader", "attrs": {}, "content": [para(text("Source"))]}]},
        {"type": "tableRow", "content": [
            {"type": "tableCell", "attrs": {}, "content": [para(text("1433"))]},
            {"type": "tableCell", "attrs": {}, "content": [para(text("agent pool"))]}]}]},
    {"type": "bulletList", "content": [
        {"type": "listItem", "content": [
            para(text("parent")),
            {"type": "bulletList", "content": [
                {"type": "listItem", "content": [para(text("child"))]}]}]}]},
    para(text("ask "),
         {"type": "mention", "attrs": {"id": "acc-1", "text": "@Sam"}},
         text(" please")),
]}

ISSUE = {"key": "PAY-12", "fields": {
    "summary": "Open the port", "issuetype": {"name": "Task"},
    "status": {"name": "To Do"}, "assignee": None, "priority": {"name": "High"},
    "labels": [], "updated": "2026-09-20T10:00:00.000+0000",
    "description": DESCRIPTION}}


def comment(n):
    return {"id": str(n), "author": {"displayName": f"Person {n}"},
            "created": f"2026-09-2{n}T10:00:00.000+0000",
            "body": {"type": "doc", "version": 1,
                     "content": [para(text(f"Comment number {n}"))]}}


class _Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        (self.bin / "curl").write_text(FAKE_CURL)
        (self.bin / "curl").chmod(0o755)
        home = self.tmp / "home"
        (home / ".dbhq" / "atlassian").mkdir(parents=True)
        (home / ".dbhq" / "atlassian" / "config.json").write_text(
            '{"site":"https://example.atlassian.net","email":"e@x.com","token":"secret"}')
        self.log = self.tmp / "requests.jsonl"
        self.routes_file = self.tmp / "routes.json"
        self.env = {"HOME": str(home),
                    "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
                    "FAKE_LOG": str(self.log),
                    "FAKE_ROUTES": str(self.routes_file)}
        self.routes([])

    def routes(self, routes):
        self.routes_file.write_text(json.dumps(routes))

    def run_issues(self, *args):
        return subprocess.run(["bash", str(ISSUES), *args], capture_output=True,
                              text=True, env=self.env, cwd=self.tmp, timeout=60)

    def requests(self, method=None):
        if not self.log.exists():
            return []
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [c for c in calls if method is None or c["method"] == method]


class TestGet(_Harness):
    def _get(self, *extra, comments=None):
        comments = [] if comments is None else comments
        self.routes([
            {"match": "/rest/api/3/issue/PAY-12/comment",
             "body": json.dumps({"total": len(comments),
                                 "comments": list(reversed(comments))[:5]})},
            {"match": "/rest/api/3/issue/PAY-12", "body": json.dumps(ISSUE)},
        ])
        result = self.run_issues("get", "PAY-12", *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_a_table_renders_as_a_table(self):
        out = self._get()
        self.assertIn("| Port | Source |", out)
        self.assertIn("| 1433 | agent pool |", out)
        self.assertNotIn("PortSource", out)

    def test_a_nested_list_keeps_its_nesting(self):
        out = self._get()
        self.assertIn("- parent\n  - child", out)
        self.assertNotIn("parentchild", out)

    def test_a_mention_is_kept(self):
        self.assertIn("ask @Sam please", self._get())

    def test_the_last_comments_are_shown_oldest_first_with_author_and_date(self):
        out = self._get(comments=[comment(n) for n in range(1, 8)])
        self.assertIn("the last 5 of 7", out)
        self.assertNotIn("Comment number 2", out)
        for n in range(3, 8):
            self.assertIn(f"Comment number {n}", out)
            self.assertIn(f"Person {n}, 2026-09-2{n}T10:00", out)
        self.assertLess(out.index("Comment number 3"), out.index("Comment number 7"))

    def test_comments_are_requested_newest_first(self):
        self._get()
        urls = [c["url"] for c in self.requests("GET")]
        self.assertTrue(any("/comment?orderBy=-created&maxResults=5" in u
                            for u in urls), urls)

    def test_no_comments_says_so(self):
        self.assertIn("Comments: none", self._get())

    def test_comments_0_skips_the_comment_request(self):
        self._get("--comments", "0")
        self.assertFalse(any("/comment" in c["url"] for c in self.requests()))

    def test_an_empty_description_says_so(self):
        issue = json.loads(json.dumps(ISSUE))
        issue["fields"]["description"] = None
        self.routes([
            {"match": "/comment", "body": '{"total":0,"comments":[]}'},
            {"match": "/rest/api/3/issue/PAY-12", "body": json.dumps(issue)},
        ])
        result = self.run_issues("get", "PAY-12")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Description:\n  (empty)", result.stdout)


if __name__ == "__main__":
    unittest.main()
