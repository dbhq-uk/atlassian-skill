import json
import pathlib
import subprocess
import sys
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from htmlplus import ConversionError, html_to_adf  # noqa: E402


class TestDocumentEnvelope(unittest.TestCase):
    def test_empty_fragment_is_an_empty_doc(self):
        self.assertEqual(
            html_to_adf(""),
            {"type": "doc", "version": 1, "content": []},
        )

    def test_paragraph(self):
        self.assertEqual(
            html_to_adf("<p>Hello.</p>"),
            {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "Hello."}],
                    }
                ],
            },
        )

    def test_headings_carry_their_level(self):
        doc = html_to_adf("<h2>Open items</h2><h4>Detail</h4>")
        self.assertEqual(doc["content"][0]["type"], "heading")
        self.assertEqual(doc["content"][0]["attrs"], {"level": 2})
        self.assertEqual(doc["content"][1]["attrs"], {"level": 4})

    def test_html_wrapper_is_rejected(self):
        # A body is a fragment. A wrapper means somebody pasted a whole page.
        with self.assertRaises(ConversionError) as cm:
            html_to_adf("<html><body><p>x</p></body></html>")
        self.assertIn("fragment", str(cm.exception))

    def test_markdown_code_fence_is_rejected(self):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf("```html\n<p>x</p>\n```")
        self.assertIn("code fence", str(cm.exception))


class TestInlineMarks(unittest.TestCase):
    def test_strong_and_em(self):
        doc = html_to_adf("<p><strong>bold</strong> and <em>italic</em></p>")
        content = doc["content"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "bold",
                                      "marks": [{"type": "strong"}]})
        self.assertEqual(content[2], {"type": "text", "text": "italic",
                                      "marks": [{"type": "em"}]})

    def test_inline_code(self):
        doc = html_to_adf("<p>the <code>reference</code> field</p>")
        self.assertEqual(
            doc["content"][0]["content"][1],
            {"type": "text", "text": "reference", "marks": [{"type": "code"}]},
        )

    def test_nested_marks_accumulate(self):
        doc = html_to_adf("<p><strong><em>both</em></strong></p>")
        marks = doc["content"][0]["content"][0]["marks"]
        self.assertEqual(
            sorted(m["type"] for m in marks), ["em", "strong"]
        )


class TestCli(unittest.TestCase):
    def _run(self, args, stdin):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "htmlplus.py")] + args,
            input=stdin, capture_output=True, text=True,
        )

    def test_to_adf_on_stdout(self):
        r = self._run(["to-adf"], "<p>Hi.</p>")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["content"][0]["type"], "paragraph")

    def test_conversion_error_exits_one_with_a_message(self):
        r = self._run(["to-adf"], "<html><p>x</p></html>")
        self.assertEqual(r.returncode, 1)
        self.assertIn("fragment", r.stderr)
        self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
