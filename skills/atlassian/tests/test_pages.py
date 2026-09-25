"""confluence-pages.sh update and edit, end to end, against a fake curl.

The fake stands in for the Confluence API: a GET returns a page fixture, and
a PUT is recorded, never sent anywhere. Every test runs with HOME pointed at
a throwaway credential, so nothing here can reach a real site.
"""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest

SKILL = pathlib.Path(__file__).resolve().parents[1]
PAGES = SKILL / "scripts" / "confluence-pages.sh"

FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, re, sys
args = sys.argv[1:]
conf = {}
for line in open(args[args.index("-K") + 1], encoding="utf-8"):
    m = re.match(r'^(\S+) = "(.*)"$', line.strip())
    if m:
        conf.setdefault(m.group(1), m.group(2))
open(conf["dump-header"], "w").close()
body = sys.stdin.read() if "data-binary" in conf else ""
method = conf.get("request", "GET")
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"method": method, "url": conf["url"], "body": body}) + "\n")
if method == "GET":
    sys.stdout.write(open(os.environ["FAKE_PAGE"], encoding="utf-8").read() + "\n200")
else:
    sys.stdout.write(os.environ.get("FAKE_PUT_BODY", '{"id":"1234567"}') + "\n"
                     + os.environ.get("FAKE_PUT_STATUS", "200"))
'''

# A macro this converter has no HTML+ for. edit must carry it through
# byte for byte, never through the converter.
MACRO = {"type": "extension", "attrs": {
    "extensionType": "com.atlassian.confluence.macro.core",
    "extensionKey": "toc", "parameters": {"macroParams": {"maxLevel": {"value": "2"}}},
    "localId": "m1"}}


def _para(local_id, text):
    return {"type": "paragraph", "attrs": {"localId": local_id},
            "content": [{"type": "text", "text": text}]}


PAGE_ADF = {"type": "doc", "version": 1, "content": [
    _para("p1", "First"), MACRO, _para("p2", "Second")]}

# A table whose first column has a width on one cell and not the other. The
# round-trip gate cannot carry that through, so update must refuse it.
UNROUNDTRIPPABLE_ADF = {"type": "doc", "version": 1, "content": [
    {"type": "table", "content": [
        {"type": "tableRow", "content": [
            {"type": "tableCell", "attrs": {"colwidth": [200]},
             "content": [_para("c1", "a")]}]},
        {"type": "tableRow", "content": [
            {"type": "tableCell", "attrs": {},
             "content": [_para("c2", "b")]}]}]}]}


def _compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


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
        self.env = {"HOME": str(home), "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
                    "FAKE_LOG": str(self.log), "FAKE_PAGE": str(self.tmp / "page.json")}
        self.page(PAGE_ADF)

    def page(self, adf, version=5):
        (self.tmp / "page.json").write_text(json.dumps({
            "id": "1234567", "title": "Test Page", "status": "current",
            "version": {"number": version},
            "body": {"atlas_doc_format": {"value": _compact(adf)}}}))

    def body(self, html):
        path = self.tmp / "body.html"
        path.write_text(html)
        return str(path)

    def run_pages(self, *args, **env):
        return subprocess.run(["bash", str(PAGES), *args], capture_output=True,
                              text=True, env={**self.env, **env})

    def requests(self, method=None):
        if not self.log.exists():
            return []
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [c for c in calls if method is None or c["method"] == method]

    def put_payload(self):
        puts = self.requests("PUT")
        self.assertEqual(len(puts), 1, puts)
        return json.loads(puts[0]["body"])


class TestEdit(_Harness):
    def test_replace_changes_only_that_node_and_the_macro_is_byte_identical(self):
        result = self.run_pages("edit", "1234567", "--base-version", "5",
                                "--replace", "p2", "--body-file",
                                self.body("<p>Changed</p>"))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.put_payload()
        self.assertEqual(payload["version"]["number"], 6)
        value = payload["body"]["value"]
        self.assertIn(_compact(MACRO), value)
        self.assertIn(_compact(_para("p1", "First")), value)
        after = json.loads(value)
        self.assertEqual(after["content"][:2], PAGE_ADF["content"][:2])
        self.assertEqual(after["content"][2]["content"][0]["text"], "Changed")
        self.assertEqual(len(after["content"]), 3)

    def test_insert_after_and_append(self):
        self.run_pages("edit", "1234567", "--base-version", "5", "--insert-after",
                       "p1", "--body-file", self.body("<h2>New</h2>"))
        after = json.loads(self.put_payload()["body"]["value"])
        self.assertEqual([n["type"] for n in after["content"]],
                         ["paragraph", "heading", "extension", "paragraph"])
        self.log.unlink()
        self.run_pages("edit", "1234567", "--base-version", "5", "--append",
                       "--body-file", self.body("<p>End</p>"))
        after = json.loads(self.put_payload()["body"]["value"])
        self.assertEqual(after["content"][-1]["content"][0]["text"], "End")

    def test_a_stale_base_version_is_refused_and_nothing_is_sent(self):
        result = self.run_pages("edit", "1234567", "--base-version", "4",
                                "--replace", "p2", "--body-file", self.body("<p>x</p>"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("has moved on since your base version", result.stderr)
        self.assertIn("--base-version 5", result.stderr)
        self.assertEqual(self.requests("PUT"), [])

    def test_dry_run_sends_nothing_and_says_what_would_go(self):
        result = self.run_pages("edit", "1234567", "--base-version", "5",
                                "--replace", "p2", "--body-file",
                                self.body("<p>Changed</p>"), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests("PUT"), [])
        self.assertIn("paragraph p2", result.stdout)
        self.assertIn("Nothing was sent.", result.stdout)

    def test_replacing_the_macro_is_reported_as_removing_it(self):
        result = self.run_pages("edit", "1234567", "--base-version", "5",
                                "--replace", "m1", "--body-file",
                                self.body("<p>No more contents</p>"), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("extension m1", result.stdout)

    def test_an_unknown_local_id_is_refused(self):
        result = self.run_pages("edit", "1234567", "--base-version", "5",
                                "--replace", "nope", "--body-file", self.body("<p>x</p>"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('local id "nope"', result.stderr)
        self.assertEqual(self.requests("PUT"), [])

    def test_the_fragment_is_checked_where_it_lands(self):
        # A table is fine at the top level but a list item cannot hold one.
        self.page({"type": "doc", "version": 1, "content": [
            {"type": "bulletList", "content": [
                {"type": "listItem", "content": [_para("li1", "item")]}]}]})
        result = self.run_pages(
            "edit", "1234567", "--base-version", "5", "--insert-after", "li1",
            "--body-file", self.body("<table><tbody><tr><td><p>x</p></td></tr></tbody></table>"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("listItem", result.stderr)
        self.assertEqual(self.requests("PUT"), [])

    def test_edit_works_on_a_page_update_refuses(self):
        # The gate only checks the node being replaced, so a page with an
        # inconsistent table elsewhere can still be edited.
        adf = json.loads(json.dumps(UNROUNDTRIPPABLE_ADF))
        adf["content"].append(_para("p9", "Tail"))
        self.page(adf)
        result = self.run_pages("edit", "1234567", "--base-version", "5",
                                "--replace", "p9", "--body-file", self.body("<p>New tail</p>"))
        self.assertEqual(result.returncode, 0, result.stderr)
        after = json.loads(self.put_payload()["body"]["value"])
        self.assertEqual(after["content"][0], adf["content"][0])


class TestSpliceRules(unittest.TestCase):
    """adf_edit.splice's refusals, called directly."""

    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(SKILL / "scripts"))
        import adf_edit
        import htmlplus
        cls.splice = staticmethod(adf_edit.splice)
        cls.Error = htmlplus.ConversionError

    def _doc(self, *content):
        return json.loads(json.dumps({"type": "doc", "version": 1, "content": list(content)}))

    def test_a_duplicate_local_id_is_refused(self):
        with self.assertRaises(self.Error) as cm:
            self.splice(self._doc(_para("x", "a"), _para("x", "b")), "replace", "x", "<p>c</p>")
        self.assertIn("share local id", str(cm.exception))

    def test_an_inline_node_is_not_a_target(self):
        doc = self._doc({"type": "paragraph", "content": [
            {"type": "status", "attrs": {"text": "ok", "color": "green", "localId": "s1"}}]})
        with self.assertRaises(self.Error) as cm:
            self.splice(doc, "replace", "s1", "<p>c</p>")
        self.assertIn("block it sits in", str(cm.exception))

    def test_a_table_cell_is_not_a_target(self):
        doc = self._doc({"type": "table", "content": [{"type": "tableRow", "content": [
            {"type": "tableCell", "attrs": {"localId": "td1"}, "content": [_para("q", "a")]}]}]})
        with self.assertRaises(self.Error):
            self.splice(doc, "replace", "td1", "<td><p>b</p></td>")
        # A block inside the cell is fine, and an expand there is nested.
        out = self.splice(doc, "insert-after", "q", "<details><summary>s</summary><p>x</p></details>")
        cell = out["content"][0]["content"][0]["content"][0]
        self.assertEqual(cell["content"][1]["type"], "nestedExpand")


class TestUpdate(_Harness):
    def test_the_write_is_version_plus_one_with_the_message(self):
        result = self.run_pages("update", "1234567", "--base-version", "5",
                                "--body-file", self.body("<p>Whole new body</p>"),
                                "--message", "Rewrote it")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.put_payload()
        self.assertEqual(payload["version"], {"number": 6, "message": "Rewrote it"})
        self.assertEqual(payload["title"], "Test Page")
        self.assertEqual(payload["status"], "current")
        self.assertEqual(json.loads(payload["body"]["value"])["content"][0]["type"],
                         "paragraph")

    def test_a_stale_base_version_is_refused_and_nothing_is_sent(self):
        result = self.run_pages("update", "1234567", "--base-version", "3",
                                "--body-file", self.body("<p>x</p>"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--base-version was 3; the page is now at version 5", result.stderr)
        self.assertEqual(self.requests("PUT"), [])

    def test_the_round_trip_gate_refuses_and_nothing_is_sent(self):
        self.page(UNROUNDTRIPPABLE_ADF)
        result = self.run_pages("update", "1234567", "--base-version", "5",
                                "--body-file", self.body("<p>x</p>"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be safely edited through this skill", result.stderr)
        self.assertIn("use edit", result.stderr)
        self.assertEqual(self.requests("PUT"), [])

    def test_a_conflict_on_the_write_is_mapped_for_409_412_and_a_version_400(self):
        for status, body in (("409", "{}"), ("412", "{}"),
                             ("400", '{"errors":[{"title":"Version must be incremented"}]}')):
            with self.subTest(status=status):
                result = self.run_pages("update", "1234567", "--base-version", "5",
                                        "--body-file", self.body("<p>x</p>"),
                                        FAKE_PUT_STATUS=status, FAKE_PUT_BODY=body)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("changed while you were working on it", result.stderr)
                self.assertIn("do not re-run this command", result.stderr)

    def test_a_400_that_is_not_about_the_version_is_an_ordinary_error(self):
        result = self.run_pages("update", "1234567", "--base-version", "5",
                                "--body-file", self.body("<p>x</p>"),
                                FAKE_PUT_STATUS="400",
                                FAKE_PUT_BODY='{"message":"Invalid body"}')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("changed while you were working on it", result.stderr)
        self.assertIn("HTTP 400", result.stderr)

    def test_dry_run_sends_nothing_and_names_what_the_body_drops(self):
        result = self.run_pages("update", "1234567", "--base-version", "5",
                                "--body-file", self.body('<p data-local-id="p1">First</p>'),
                                "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests("PUT"), [])
        self.assertIn("extension m1", result.stdout)
        self.assertIn("paragraph p2", result.stdout)
        self.assertNotIn("paragraph p1", result.stdout)
        self.assertIn("Nothing was sent.", result.stdout)


if __name__ == "__main__":
    unittest.main()
