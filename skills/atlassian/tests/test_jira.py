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
import re
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
    if "body_contains" in route and route["body_contains"] not in body:
        continue
    if "times" in route and used.get(str(i), 0) >= route["times"]:
        continue
    used[str(i)] = used.get(str(i), 0) + 1
    json.dump(used, open(state_path, "w"))
    if route.get("curl_exit"):
        sys.stderr.write("curl: (7) Failed to connect\n")
        sys.exit(route["curl_exit"])
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
        # sleep is logged, not slept, so a test can see every pause and a
        # run with a Retry-After does not take real seconds.
        self.sleeps = self.tmp / "sleeps.log"
        (self.bin / "sleep").write_text(f'#!/bin/sh\necho "$1" >> "{self.sleeps}"\n')
        (self.bin / "sleep").chmod(0o755)
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


def created(key):
    return {"method": "POST", "match": "/rest/api/3/issue", "status": 201,
            "body": json.dumps({"key": key})}


class TestBulk(_Harness):
    ENTRIES = [
        {"summary": "First", "type": "Task"},
        {"summary": "Second", "labels": ["a"], "note": "kept as written"},
        {"summary": "Third"},
    ]

    def write(self, entries, name="tickets.json"):
        path = self.tmp / name
        path.write_text(json.dumps(entries))
        return path

    def posts(self):
        return [json.loads(c["body"]) for c in self.requests("POST")]

    def slept(self):
        return self.sleeps.read_text().split() if self.sleeps.exists() else []

    def test_a_429_is_waited_out_and_the_item_retried(self):
        self.routes([
            {"method": "POST", "match": "/rest/api/3/issue", "times": 1,
             "status": 429, "headers": {"Retry-After": "3"}, "body": "{}"},
            created("PAY-1"),
        ])
        path = self.write([{"summary": "First"}])
        result = self.run_issues("bulk", "PAY", str(path))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.posts()), 2)
        self.assertIn("PAY-1", result.stdout)
        self.assertIn("Created: 1   Failed: 0", result.stdout)
        self.assertIn("3", self.slept())  # the wait Retry-After asked for
        self.assertFalse((self.tmp / "tickets.remaining.json").exists())

    def test_a_partial_failure_writes_only_the_failed_entries(self):
        self.routes([
            {"method": "POST", "match": "/rest/api/3/issue", "body_contains": "Second",
             "status": 400, "body": json.dumps({"errors": {"labels": "bad label"}})},
            created("PAY-1"),
        ])
        path = self.write(self.ENTRIES)
        result = self.run_issues("bulk", "PAY", str(path))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Created: 2   Failed: 1", result.stdout)
        self.assertIn("bad label", result.stderr)
        remaining = self.tmp / "tickets.remaining.json"
        self.assertIn(str(remaining), result.stdout)
        self.assertEqual(json.loads(remaining.read_text()), [self.ENTRIES[1]])
        self.assertEqual(json.loads(path.read_text()), self.ENTRIES)

    def test_a_network_failure_fails_the_item_not_the_run(self):
        self.routes([
            {"method": "POST", "match": "/rest/api/3/issue", "body_contains": "First",
             "curl_exit": 7},
            created("PAY-2"),
        ])
        path = self.write(self.ENTRIES)
        result = self.run_issues("bulk", "PAY", str(path))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Created: 2   Failed: 1", result.stdout)
        remaining = json.loads((self.tmp / "tickets.remaining.json").read_text())
        self.assertEqual(remaining, [self.ENTRIES[0]])

    def test_a_limit_that_does_not_clear_stops_the_run(self):
        self.routes([
            {"method": "POST", "match": "/rest/api/3/issue", "body_contains": "First",
             "body": json.dumps({"key": "PAY-1"}), "status": 201},
            {"method": "POST", "match": "/rest/api/3/issue", "status": 429,
             "headers": {"Retry-After": "1"}, "body": "{}"},
        ])
        path = self.write(self.ENTRIES)
        result = self.run_issues("bulk", "PAY", str(path))
        self.assertEqual(result.returncode, 1)
        # First created; Second tried twice; Third never sent.
        self.assertEqual([p["fields"]["summary"] for p in self.posts()],
                         ["First", "Second", "Second"])
        self.assertIn("Stopping", result.stderr)
        remaining = json.loads((self.tmp / "tickets.remaining.json").read_text())
        self.assertEqual(remaining, self.ENTRIES[1:])

    def test_a_remaining_file_that_all_goes_through_is_emptied(self):
        self.routes([created("PAY-3")])
        path = self.write([self.ENTRIES[2]], name="tickets.remaining.json")
        result = self.run_issues("bulk", "PAY", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(path.read_text()), [])

    def test_a_dry_run_does_not_sleep_or_send(self):
        path = self.write(self.ENTRIES * 5)
        result = self.run_issues("bulk", "PAY", str(path), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.slept(), [])
        self.assertEqual(self.requests(), [])
        self.assertIn("Would create: 15", result.stdout)

    def test_a_fields_object_reaches_the_payload(self):
        self.routes([created("PAY-1")])
        path = self.write([{"summary": "First", "fields": {
            "customfield_10050": "Ops", "components": [{"name": "Backend"}]}}])
        result = self.run_issues("bulk", "PAY", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        fields = self.posts()[0]["fields"]
        self.assertEqual(fields["customfield_10050"], "Ops")
        self.assertEqual(fields["components"], [{"name": "Backend"}])
        self.assertEqual(fields["summary"], "First")

    def test_fields_cannot_set_a_field_that_has_its_own_key(self):
        path = self.write([{"summary": "First", "fields": {"summary": "Other"}},
                           {"summary": "Second", "fields": ["not", "an", "object"]}])
        result = self.run_issues("bulk", "PAY", str(path), "--dry-run")
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot set summary", result.stderr)
        self.assertIn("must be an object", result.stderr)
        self.assertEqual(self.requests(), [])


class TestNoUnsourcedRequestRate(unittest.TestCase):
    """"Roughly 60 requests a minute" was stated in four places with no
    source. Atlassian publishes per-second burst limits and an hourly
    points quota instead. A request rate may appear only beside the link."""

    RATE = re.compile(r"\b\d+\s*(?:authenticated\s+)?(?:requests?|calls?)\s*(?:a|an|per|/)\s*(?:second|minute|hour)", re.I)

    def test_no_file_states_a_rate_without_a_source(self):
        repo = SKILL.parents[1]
        files = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True,
                               text=True, check=True).stdout.split()
        offenders = []
        for name in files:
            if "/tests/" in name or not name.endswith((".md", ".sh", ".py", ".json")):
                continue
            for n, line in enumerate((repo / name).read_text(encoding="utf-8").splitlines(), 1):
                if self.RATE.search(line) and "developer.atlassian.com" not in line:
                    offenders.append(f"{name}:{n}: {line.strip()}")
        self.assertEqual(offenders, [])

    def test_the_old_claim_would_be_caught(self):
        self.assertTrue(self.RATE.search("roughly 60 requests a minute"))
        self.assertTrue(self.RATE.search("(roughly 60 requests/minute)"))


if __name__ == "__main__":
    unittest.main()
