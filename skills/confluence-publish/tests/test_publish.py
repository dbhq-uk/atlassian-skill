import pathlib
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from frontmatter import read_binding, strip_frontmatter, write_page_id  # noqa: E402
from md_to_htmlplus import md_to_htmlplus  # noqa: E402

BOUND = """\
---
title: Payment Correlation
confluence:
  space: "98765"
  parent: "1234567"
  page_id: "8901234"
---

# Payment Correlation

Body text.
"""

UNBOUND = """\
---
confluence:
  space: "98765"
  parent: "1234567"
---

Body text.
"""


class TestFrontmatter(unittest.TestCase):
    def _write(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
        f.write(text)
        f.close()
        return pathlib.Path(f.name)

    def test_reads_a_full_binding(self):
        self.assertEqual(
            read_binding(self._write(BOUND)),
            {"space": "98765", "parent": "1234567", "page_id": "8901234"},
        )

    def test_a_missing_page_id_is_none_not_an_error(self):
        # An unpublished file is the normal first state, not a failure.
        self.assertEqual(read_binding(self._write(UNBOUND))["page_id"], None)

    def test_a_file_with_no_frontmatter_at_all(self):
        self.assertEqual(
            read_binding(self._write("Just a body.\n")),
            {"space": None, "parent": None, "page_id": None},
        )

    def test_write_page_id_preserves_the_other_keys_and_the_body(self):
        path = self._write(UNBOUND)
        write_page_id(path, "5555555")
        text = path.read_text()
        self.assertEqual(read_binding(path)["page_id"], "5555555")
        self.assertEqual(read_binding(path)["space"], "98765")
        self.assertEqual(read_binding(path)["parent"], "1234567")
        self.assertTrue(text.rstrip().endswith("Body text."))

    def test_write_page_id_is_idempotent(self):
        path = self._write(BOUND)
        before = path.read_text()
        write_page_id(path, "8901234")
        self.assertEqual(path.read_text(), before)

    def test_strip_frontmatter_leaves_the_body(self):
        self.assertEqual(strip_frontmatter(BOUND).strip(),
                         "# Payment Correlation\n\nBody text.")


class TestFrontmatterEdgeCases(unittest.TestCase):
    """Cases the brief's own fixtures do not exercise.

    Task 13's brief was written before this converter's write path had to
    think about anything other than a clean, LF, single-block source file.
    Each test here is a way a real file on disk differs from that: no
    frontmatter, a sibling key the writer must not touch, no confluence key
    at all yet, a confluence block that is not the last thing in the
    frontmatter, CRLF line endings, a missing trailing newline, and a body
    that itself contains something that looks like a second frontmatter
    block.
    """

    def _write(self, text, newline=""):
        # newline="" so the exact bytes given are what lands on disk - the
        # thing under test is what write_page_id/read_binding do with a
        # specific line-ending convention, and letting tempfile's own text
        # mode normalise it away before the test even starts would defeat
        # the point of the CRLF cases below.
        f = tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False, newline=newline
        )
        f.write(text)
        f.close()
        return pathlib.Path(f.name)

    def test_write_page_id_on_a_file_with_no_frontmatter_creates_one(self):
        path = self._write("Just a body.\n")
        write_page_id(path, "8901234")
        self.assertEqual(read_binding(path)["page_id"], "8901234")
        text = path.read_text()
        self.assertTrue(text.startswith("---\n"))
        self.assertTrue(text.endswith("Just a body.\n"))

    def test_write_page_id_preserves_a_sibling_key_it_never_reads(self):
        # BOUND's own title: key is a fixture detail the brief's tests never
        # actually assert on - only space/parent/page_id are checked there.
        path = self._write(BOUND)
        write_page_id(path, "5555555")
        text = path.read_text()
        self.assertIn("title: Payment Correlation", text)
        self.assertEqual(read_binding(path)["page_id"], "5555555")

    def test_write_page_id_with_no_confluence_key_yet_adds_one(self):
        src = (
            "---\n"
            "title: Standalone Note\n"
            "---\n"
            "\n"
            "Body text.\n"
        )
        path = self._write(src)
        write_page_id(path, "8901234")
        self.assertEqual(
            read_binding(path),
            {"space": None, "parent": None, "page_id": "8901234"},
        )
        text = path.read_text()
        self.assertIn("title: Standalone Note", text)
        self.assertTrue(text.rstrip().endswith("Body text."))

    def test_write_page_id_when_a_key_follows_the_confluence_block(self):
        # confluence: is not the last key in the frontmatter, so the writer
        # has to stop copying-through-unchanged at the right line rather
        # than at the end of the block.
        src = (
            "---\n"
            "confluence:\n"
            '  space: "98765"\n'
            "title: After The Block\n"
            "---\n"
            "\n"
            "Body text.\n"
        )
        path = self._write(src)
        write_page_id(path, "8901234")
        self.assertEqual(read_binding(path)["page_id"], "8901234")
        self.assertEqual(read_binding(path)["space"], "98765")
        text = path.read_text()
        self.assertIn("title: After The Block", text)
        # The inserted line belongs inside the confluence: block, not after
        # the sibling key that follows it.
        self.assertLess(
            text.index('page_id: "8901234"'), text.index("title: After The Block")
        )

    def test_write_page_id_on_a_crlf_file_keeps_crlf(self):
        src = UNBOUND.replace("\n", "\r\n")
        path = self._write(src, newline="")
        write_page_id(path, "5555555")
        raw = path.read_bytes()
        self.assertNotIn(b"\r\n\n", raw)  # no mangled mixed line endings
        self.assertIn(b"\r\n", raw)
        self.assertNotRegex(
            raw, rb"(?<!\r)\n",
            msg="a plain LF crept into a file that was CRLF throughout",
        )
        self.assertEqual(read_binding(path)["page_id"], "5555555")
        self.assertEqual(read_binding(path)["space"], "98765")

    def test_write_page_id_preserves_a_missing_trailing_newline(self):
        src = UNBOUND.rstrip("\n")  # "Body text." with no final newline
        path = self._write(src)
        before_body_tail = path.read_text()[-len("Body text."):]
        write_page_id(path, "5555555")
        text = path.read_text()
        self.assertFalse(text.endswith("\n"))
        self.assertEqual(text[-len("Body text."):], before_body_tail)
        self.assertEqual(read_binding(path)["page_id"], "5555555")

    def test_body_containing_a_frontmatter_look_alike_is_left_alone(self):
        src = (
            "---\n"
            "confluence:\n"
            '  space: "98765"\n'
            '  parent: "1234567"\n'
            "---\n"
            "\n"
            "Body intro.\n"
            "\n"
            "---\n"
            "title: not real frontmatter\n"
            "---\n"
            "\n"
            "More body.\n"
        )
        path = self._write(src)
        before = path.read_text()
        body_only_before = strip_frontmatter(before)
        write_page_id(path, "8901234")
        after = path.read_text()
        # Everything from the real closing delimiter onward - including the
        # look-alike block - is untouched.
        self.assertEqual(strip_frontmatter(after), body_only_before)
        self.assertEqual(read_binding(path)["page_id"], "8901234")
        self.assertEqual(read_binding(path)["space"], "98765")

    def test_write_page_id_is_idempotent_on_a_second_call(self):
        # The brief's own idempotency test only exercises the short-circuit
        # on a file that already carried the id when it was written. This
        # exercises the write path itself first, then checks a second call
        # with the same id changes nothing further.
        path = self._write(UNBOUND)
        write_page_id(path, "5555555")
        after_first = path.read_text()
        write_page_id(path, "5555555")
        self.assertEqual(path.read_text(), after_first)


class TestMarkdownToHtmlPlus(unittest.TestCase):
    def test_headings_and_paragraphs(self):
        self.assertEqual(
            md_to_htmlplus("# Title\n\nBody.\n"),
            "<h1>Title</h1><p>Body.</p>",
        )

    def test_inline_marks(self):
        self.assertEqual(
            md_to_htmlplus("**bold** and *italic* and `code`\n"),
            "<p><strong>bold</strong> and <em>italic</em> and <code>code</code></p>",
        )

    def test_link(self):
        self.assertEqual(
            md_to_htmlplus("See [the docs](https://example.com/x).\n"),
            '<p>See <a href="https://example.com/x">the docs</a>.</p>',
        )

    def test_fenced_code_keeps_its_language(self):
        self.assertEqual(
            md_to_htmlplus('```json\n{"a": 1}\n```\n'),
            '<pre><code class="language-json">{"a": 1}</code></pre>',
        )

    def test_gfm_task_list_becomes_a_real_task_list(self):
        # The one markdown syntax that maps exactly onto a native component.
        out = md_to_htmlplus("- [ ] Confirm it\n- [x] Publish it\n")
        self.assertIn('<ul data-type="task-list">', out)
        self.assertIn('<input type="checkbox"> Confirm it', out)
        self.assertIn('<input type="checkbox" checked> Publish it', out)

    def test_plain_bullet_list_stays_a_bullet_list(self):
        out = md_to_htmlplus("- one\n- two\n")
        self.assertEqual(out, "<ul><li><p>one</p></li><li><p>two</p></li></ul>")

    def test_table(self):
        out = md_to_htmlplus("| A | B |\n| --- | --- |\n| 1 | 2 |\n")
        self.assertIn("<table>", out)
        self.assertIn("<th><p>A</p></th>", out)
        self.assertIn("<td><p>1</p></td>", out)

    def test_raw_html_passes_through_untouched(self):
        # This is how a source file expresses a panel, which markdown cannot.
        src = 'Before.\n\n<div data-type="panel-warning"><p>Careful.</p></div>\n\nAfter.\n'
        out = md_to_htmlplus(src)
        self.assertIn('<div data-type="panel-warning"><p>Careful.</p></div>', out)
        self.assertIn("<p>Before.</p>", out)
        self.assertIn("<p>After.</p>", out)

    def test_output_converts_cleanly_to_adf(self):
        sys.path.insert(0, str(SCRIPTS.parents[1] / "_shared" / "scripts"))
        from htmlplus import html_to_adf

        src = (
            "# Title\n\nBody with **bold**.\n\n"
            '<div data-type="panel-info"><p>Context.</p></div>\n\n'
            "- [ ] A task\n\n```bash\nls\n```\n"
        )
        doc = html_to_adf(md_to_htmlplus(src))
        self.assertEqual(
            [n["type"] for n in doc["content"]],
            ["heading", "paragraph", "panel", "taskList", "codeBlock"],
        )


if __name__ == "__main__":
    unittest.main()
