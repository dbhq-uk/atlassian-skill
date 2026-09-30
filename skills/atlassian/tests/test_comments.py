"""confluence-comments.sh, end to end, against the fake curl test_jira.py uses.

Every request is logged and never sent anywhere, and every test runs with
HOME pointed at a throwaway credential, so nothing here can reach a real
site.
"""

import json
import subprocess
import unittest

from test_jira import SKILL, _Harness, para, text

COMMENTS = SKILL / "scripts" / "confluence-comments.sh"


def doc(*blocks):
    return {"type": "doc", "version": 1, "content": list(blocks)}


def adf_value(words):
    return json.dumps(doc(para(text(words))))


def a_comment(cid, words, author="acc-me", version=1, **extra):
    return {"id": cid, "pageId": "1234567", "status": "current",
            "version": {"number": version, "authorId": author,
                        "createdAt": "2026-09-29T17:00:00.000Z"},
            "body": {"atlas_doc_format": {"value": adf_value(words)}},
            "_links": {"base": "https://example.atlassian.net/wiki",
                       "webui": f"/spaces/D/pages/1234567?focusedCommentId={cid}"},
            **extra}


PAGE = {"id": "1234567", "title": "Egress", "status": "current",
        "version": {"number": 3},
        "body": {"atlas_doc_format": {"value": json.dumps(doc(
            para(text("Open the egress address.")),
            para(text("The egress "), text("rule", ), text(" is new.")),
            para(text("egr"), {"type": "mention", "attrs": {"id": "a"}}, text("ess"))))}}}

ME = {"match": "/rest/api/3/myself", "body": json.dumps({"accountId": "acc-me"})}


class _Comments(_Harness):
    def run_comments(self, *args):
        return subprocess.run(["bash", str(COMMENTS), *args], capture_output=True,
                              text=True, env=self.env, cwd=self.tmp, timeout=60)

    def footer(self, comment, creator=None, replies=()):
        """Routes for one footer comment: it, its first version and its replies."""
        cid = comment["id"]
        return [
            {"match": f"/footer-comments/{cid}/versions/1",
             "body": json.dumps({"number": 1, "authorId": creator or comment["version"]["authorId"]})},
            {"match": f"/footer-comments/{cid}/children",
             "body": json.dumps({"results": list(replies), "_links": {}})},
            {"match": f"/footer-comments/{cid}?", "body": json.dumps(comment)},
        ]

    def posted(self, method="POST"):
        sent = self.requests(method)
        self.assertEqual(len(sent), 1, sent)
        return sent[0]


class TestCreate(_Comments):
    def setUp(self):
        super().setUp()
        self.routes([
            {"match": "/wiki/api/v2/pages/1234567?", "body": json.dumps(PAGE)},
            {"method": "POST", "match": "-comments", "status": 201,
             "body": json.dumps(a_comment("900", "x"))},
        ])

    def test_a_footer_comment_is_posted_as_an_adf_string(self):
        result = self.run_comments("create", "1234567", "Looks good.")
        self.assertEqual(result.returncode, 0, result.stderr)
        sent = self.posted()
        self.assertTrue(sent["url"].endswith("/wiki/api/v2/footer-comments"))
        payload = json.loads(sent["body"])
        self.assertEqual(payload["pageId"], "1234567")
        self.assertIsInstance(payload["body"]["value"], str)
        self.assertEqual(json.loads(payload["body"]["value"])["content"][0]["content"][0]["text"],
                         "Looks good.")
        self.assertIn("focusedCommentId=900", result.stdout)

    def test_an_inline_comment_states_the_count_read_from_the_live_page(self):
        result = self.run_comments("create", "1234567", "Which one?", "--inline", "egress",
                                   "--match", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        sent = self.posted()
        self.assertTrue(sent["url"].endswith("/wiki/api/v2/inline-comments"))
        self.assertEqual(json.loads(sent["body"])["inlineCommentProperties"],
                         {"textSelection": "egress", "textSelectionMatchCount": 2,
                          "textSelectionMatchIndex": 1})

    def test_text_on_the_page_twice_needs_a_match(self):
        result = self.run_comments("create", "1234567", "x", "--inline", "egress")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("2 times", result.stderr)
        self.assertEqual(self.requests("POST"), [])

    def test_text_not_on_the_page_or_a_match_past_the_last_is_refused(self):
        for args in (("--inline", "no such words"), ("--inline", "egress", "--match", "3"),
                     ("--inline", "egress", "--match", "0")):
            with self.subTest(args=args):
                result = self.run_comments("create", "1234567", "x", *args)
                self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.requests("POST"), [])

    def test_dry_run_and_bad_input_send_nothing(self):
        result = self.run_comments("create", "1234567", "x", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Nothing was sent.", result.stdout)
        for args in (("create", "12x", "hi"), ("create", "1234567", "   "),
                     ("create", "1234567"), ("create", "1234567", "hi", "--match", "1")):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_comments(*args).returncode, 0)
        self.assertEqual(self.requests("POST"), [])


class TestReply(_Comments):
    def test_a_reply_goes_to_the_parents_own_kind(self):
        inline = a_comment("700", "Why?", author="acc-other")
        self.routes([
            {"match": "/footer-comments/700", "status": 404, "body": '{"errors":[]}'},
            {"match": "/inline-comments/700?", "body": json.dumps(inline)},
            {"method": "POST", "match": "/inline-comments", "status": 201,
             "body": json.dumps(a_comment("701", "Because."))},
        ])
        result = self.run_comments("reply", "700", "Because.")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(self.posted()["body"])
        self.assertEqual(payload["parentCommentId"], "700")
        self.assertNotIn("pageId", payload)


class TestUpdate(_Comments):
    def given(self, author="acc-me", version=2):
        self.routes([ME, *self.footer(a_comment("555", "Old.", author=author, version=version)),
                     {"method": "PUT", "match": "/footer-comments/555",
                      "body": json.dumps(a_comment("555", "New.", version=version + 1))}])

    def test_update_writes_the_next_version_of_your_own_comment(self):
        self.given()
        result = self.run_comments("update", "555", "New.", "--base-version", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(self.posted("PUT")["body"])
        self.assertEqual(payload["version"]["number"], 3)
        self.assertIn("New.", payload["body"]["value"])

    def test_update_needs_a_base_version_and_refuses_a_stale_one(self):
        self.given()
        self.assertNotEqual(self.run_comments("update", "555", "New.").returncode, 0)
        result = self.run_comments("update", "555", "New.", "--base-version", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("moved on", result.stderr)
        self.assertEqual(self.requests("PUT"), [])

    def test_someone_elses_comment_is_refused(self):
        self.given(author="acc-other")
        result = self.run_comments("update", "555", "Mine now.", "--base-version", "2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("written by someone else", result.stderr)
        self.assertEqual(self.requests("PUT"), [])

    def test_the_writer_is_the_author_of_version_1(self):
        # The latest version is ours, but somebody else wrote the comment.
        self.routes([ME, *self.footer(a_comment("555", "Old.", version=2), creator="acc-other")])
        result = self.run_comments("update", "555", "x", "--base-version", "2")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.requests("PUT"), [])


class TestResolve(_Comments):
    def test_resolve_sends_the_body_back_unchanged(self):
        inline = a_comment("700", "Why?", author="acc-other", resolutionStatus="open")
        self.routes([
            {"match": "/footer-comments/700", "status": 404, "body": '{"errors":[]}'},
            {"match": "/inline-comments/700?", "body": json.dumps(inline)},
            {"method": "PUT", "match": "/inline-comments/700", "body": json.dumps(inline)},
        ])
        result = self.run_comments("resolve", "700")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(self.posted("PUT")["body"])
        self.assertIs(payload["resolved"], True)
        self.assertEqual(payload["body"]["value"], adf_value("Why?"))
        self.assertEqual(payload["version"]["number"], 2)

    def test_a_footer_comment_cannot_be_resolved(self):
        self.routes(self.footer(a_comment("555", "Hi.")))
        result = self.run_comments("resolve", "555")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.requests("PUT"), [])


class TestDelete(_Comments):
    def test_your_own_comment_with_no_replies_is_deleted(self):
        self.routes([ME, *self.footer(a_comment("555", "Typo.")),
                     {"method": "DELETE", "match": "/footer-comments/555", "status": 204, "body": ""}])
        result = self.run_comments("delete", "555")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.posted("DELETE")["url"].endswith("/wiki/api/v2/footer-comments/555"))
        self.assertIn("Typo.", result.stdout)

    def test_a_comment_with_replies_is_refused(self):
        self.routes([ME, *self.footer(a_comment("555", "Typo."),
                                      replies=[a_comment("556", "Reply", author="acc-other")])])
        result = self.run_comments("delete", "555")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("has replies", result.stderr)
        self.assertEqual(self.requests("DELETE"), [])

    def test_someone_elses_comment_is_refused(self):
        self.routes([ME, *self.footer(a_comment("555", "Theirs.", author="acc-other"))])
        result = self.run_comments("delete", "555")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("written by someone else", result.stderr)
        self.assertEqual(self.requests("DELETE"), [])

    def test_dry_run_sends_nothing(self):
        self.routes([ME, *self.footer(a_comment("555", "Typo."))])
        result = self.run_comments("delete", "555", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Nothing was sent.", result.stdout)
        self.assertEqual({r["method"] for r in self.requests()}, {"GET"})

    def test_an_id_that_is_not_a_number_is_refused_before_a_request(self):
        for cid in ("555/../../pages/1", "abc", ""):
            with self.subTest(cid=cid):
                self.assertNotEqual(self.run_comments("delete", cid).returncode, 0)
        self.assertEqual(self.requests(), [])


class TestListAndRead(_Comments):
    def test_list_shows_threads_with_replies_nested(self):
        root = a_comment("555", "Root words.", author="acc-other")
        reply = a_comment("556", "Reply words.")
        self.routes([
            ME,
            {"match": "/footer-comments/555/children", "body": json.dumps({"results": [reply]})},
            {"match": "/footer-comments/556/children", "body": json.dumps({"results": []})},
            {"match": "/pages/1234567/footer-comments", "body": json.dumps({"results": [root]})},
            {"match": "/pages/1234567/inline-comments", "body": json.dumps({"results": []})},
        ])
        result = self.run_comments("list", "1234567")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn("-- footer comment 555, version 1, acc-other,", out)
        self.assertIn("    -- footer comment 556, version 1, acc-me (you),", out)
        self.assertLess(out.index("Root words."), out.index("Reply words."))

    def test_read_prints_the_version_to_update_against(self):
        self.routes(self.footer(a_comment("555", "Hello.", version=4)))
        result = self.run_comments("read", "555")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pass --base-version 4 to update", result.stderr)
        self.assertIn("Hello.", result.stdout)
        self.assertNotIn("base-version", result.stdout)

    def test_an_id_in_neither_collection_says_so(self):
        self.routes([{"match": "-comments/", "status": 404, "body": '{"errors":[]}'}])
        result = self.run_comments("read", "555")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no comment 555", result.stderr)


if __name__ == "__main__":
    unittest.main()
