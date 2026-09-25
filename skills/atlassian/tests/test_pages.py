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
conf, forms = {}, []
for line in open(args[args.index("-K") + 1], encoding="utf-8"):
    m = re.match(r'^(\S+) = "(.*)"$', line.strip())
    if m:
        conf.setdefault(m.group(1), m.group(2))
        if m.group(1) == "form":
            forms.append(m.group(2))
if "dump-header" in conf:
    open(conf["dump-header"], "w").close()
body = sys.stdin.read() if "data-binary" in conf else ""
method, url = conf.get("request", "GET"), conf["url"]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"method": method, "url": url, "body": body, "forms": forms}) + "\n")
if "/attachments?" in url:
    sys.stdout.write(os.environ.get("FAKE_ATTACHMENTS", '{"results":[]}') + "\n200")
elif "/child/attachment" in url:
    sys.stdout.write(os.environ.get(
        "FAKE_UPLOAD",
        '{"results":[{"extensions":{"fileId":"f-new","collectionName":"contentId-1234567"}}]}')
        + "\n200")
elif method == "POST":
    sys.stdout.write('{"id":"1234567","_links":{"base":"https://example.atlassian.net/wiki",'
                     '"webui":"/spaces/D/pages/1234567"}}\n200')
elif method == "GET":
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

    def page(self, adf, version=5, message="", author="acc-1", when="2026-09-20T10:00:00Z"):
        (self.tmp / "page.json").write_text(json.dumps({
            "id": "1234567", "title": "Test Page", "status": "current",
            "version": {"number": version, "message": message,
                        "authorId": author, "createdAt": when},
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


class TestReadMarkdown(_Harness):
    def test_a_mention_survives_read_format_markdown(self):
        self.page({"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "ask "},
                {"type": "mention", "attrs": {"id": "acc-1", "text": "@Sam"}},
                {"type": "text", "text": " please"}]}]})
        result = self.run_pages("read", "1234567", "--format", "markdown")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ask @Sam please", result.stdout)


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



class TestPublish(_Harness):
    """publish.sh against the same fake: it refuses to overwrite an edit made
    in Confluence, skips a write that changes nothing, and uploads a local
    image itself."""

    PUBLISH = SKILL / "scripts" / "publish.sh"

    def setUp(self):
        super().setUp()
        self.doc = self.tmp / "doc.md"
        self.write_doc(page_id="1234567")

    def write_doc(self, page_id=None, body="# Title\n\nBody text.\n"):
        binding = f'  page_id: "{page_id}"\n' if page_id else ""
        self.doc.write_text(f'---\nconfluence:\n  space: "98765"\n{binding}---\n\n{body}')

    def publish(self, *args, **env):
        return subprocess.run(["bash", str(self.PUBLISH), str(self.doc), *args],
                              capture_output=True, text=True, env={**self.env, **env})

    def published(self):
        return f"Published from {self.doc}"

    def test_a_page_edited_in_confluence_is_refused_naming_that_version(self):
        self.page(PAGE_ADF, version=7, message="", author="acc-123",
                  when="2026-09-20T10:00:00Z")
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("edited in Confluence", result.stderr)
        for part in ("version 7", "acc-123", "2026-09-20T10:00:00Z", "--base-version 7"):
            self.assertIn(part, result.stderr)
        self.assertEqual(self.requests("PUT"), [])

    def test_base_version_confirms_that_edit_and_the_publish_goes_ahead(self):
        self.page(PAGE_ADF, version=7, message="Fixed a typo")
        result = self.publish("--base-version", "7")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.put_payload()
        self.assertEqual(payload["version"], {"number": 8, "message": self.published()})

    def test_a_base_version_the_page_has_moved_past_is_refused(self):
        self.page(PAGE_ADF, version=8, message="Another edit")
        result = self.publish("--base-version", "7")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("moved on since version 7", result.stderr)
        self.assertEqual(self.requests("PUT"), [])

    def test_a_page_last_written_by_publish_is_updated(self):
        self.page(PAGE_ADF, version=5, message=self.published())
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.put_payload()["version"]["number"], 6)

    def test_publishing_an_unchanged_file_sends_no_put(self):
        self.page(PAGE_ADF, version=5, message=self.published())
        self.assertEqual(self.publish().returncode, 0)
        written = json.loads(self.put_payload()["body"]["value"])
        # Confluence assigns local ids on save; they must not count as a change.
        for node in written["content"]:
            node.setdefault("attrs", {})["localId"] = "assigned"
        self.page(written, version=6, message=self.published())
        self.log.unlink()
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Unchanged", result.stdout)
        self.assertEqual(self.requests("PUT"), [])

    def test_a_changed_file_is_still_written(self):
        self.page(PAGE_ADF, version=5, message=self.published())
        self.publish()
        written = json.loads(self.put_payload()["body"]["value"])
        self.page(written, version=6, message=self.published())
        self.log.unlink()
        self.write_doc(page_id="1234567", body="# Title\n\nNew body text.\n")
        self.assertEqual(self.publish().returncode, 0)
        self.assertEqual(len(self.requests("PUT")), 1)

    def _with_image(self):
        (self.tmp / "img").mkdir()
        (self.tmp / "img" / "d.png").write_bytes(b"not really a png")
        self.write_doc(page_id="1234567",
                       body="# Title\n\n![Diagram](img/d.png)\n")
        self.page(PAGE_ADF, version=5, message=self.published())

    def test_a_local_image_is_uploaded_and_the_figure_points_at_it(self):
        import hashlib
        self._with_image()
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        uploads = [r for r in self.requests("PUT") if "/child/attachment" in r["url"]]
        self.assertEqual(len(uploads), 1)
        digest = hashlib.sha256(b"not really a png").hexdigest()
        self.assertIn(f"comment=sha256:{digest}", uploads[0]["forms"])
        page_puts = [r for r in self.requests("PUT") if "/wiki/api/v2/pages/" in r["url"]]
        media = json.loads(json.loads(page_puts[0]["body"])["body"]["value"])
        figure = media["content"][-1]
        self.assertEqual(figure["type"], "mediaSingle")
        self.assertEqual(figure["content"][0]["attrs"],
                         {"type": "file", "id": "f-new",
                          "collection": "contentId-1234567", "alt": "Diagram"})

    def test_an_image_already_attached_unchanged_is_not_uploaded_again(self):
        import hashlib
        self._with_image()
        digest = hashlib.sha256(b"not really a png").hexdigest()
        attached = json.dumps({"results": [{"fileId": "f-old", "comment": f"sha256:{digest}"}]})
        result = self.publish(FAKE_ATTACHMENTS=attached)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([r for r in self.requests("PUT") if "/child/attachment" in r["url"]], [])
        page_put = [r for r in self.requests("PUT") if "/wiki/api/v2/pages/" in r["url"]][0]
        self.assertIn("f-old", page_put["body"])

    def test_a_first_publish_with_an_image_creates_then_attaches_then_writes(self):
        (self.tmp / "d.png").write_bytes(b"png")
        self.write_doc(body="# Title\n\n![Diagram](d.png)\n")
        self.page(PAGE_ADF, version=1, message="")
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        methods = [(r["method"], "attachment" in r["url"]) for r in self.requests()
                   if r["method"] != "GET"]
        self.assertEqual(methods, [("POST", False), ("PUT", True), ("PUT", False)])
        self.assertIn('page_id: "1234567"', self.doc.read_text())
        final = json.loads(self.requests("PUT")[-1]["body"])
        self.assertEqual(final["version"], {"number": 2, "message": self.published()})
        self.assertIn("f-new", final["body"]["value"])

    def test_a_missing_image_is_refused_before_anything_is_sent(self):
        self.write_doc(page_id="1234567", body="# Title\n\n![Gone](nowhere.png)\n")
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nowhere.png", result.stderr)
        self.assertEqual(self.requests(), [])

    def test_the_publish_path_never_sends_the_user_to_the_confluence_ui(self):
        # Publish replaces the page by design. The old advice - resolve the
        # round-trip gate "in the Confluence UI" - asked a person to delete
        # content so that publish could overwrite it anyway.
        self.assertNotIn("Confluence UI", self.PUBLISH.read_text())
        self.assertNotIn("check-roundtrip", self.PUBLISH.read_text())

if __name__ == "__main__":
    unittest.main()
