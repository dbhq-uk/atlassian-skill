import os
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import frontmatter  # noqa: E402  (module import, needed to patch frontmatter.os.replace)
from frontmatter import read_binding, strip_frontmatter, write_page_id  # noqa: E402
from md_to_htmlplus import ConversionError, md_to_htmlplus  # noqa: E402

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
    """Cases the fixtures above do not exercise.

    Those were written before this converter's write path had to think
    about anything other than a clean, LF, single-block source file.
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
        # BOUND's own title: key is a fixture detail the tests above never
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
        # The idempotency test above only exercises the short-circuit
        # on a file that already carried the id when it was written. This
        # exercises the write path itself first, then checks a second call
        # with the same id changes nothing further.
        path = self._write(UNBOUND)
        write_page_id(path, "5555555")
        after_first = path.read_text()
        write_page_id(path, "5555555")
        self.assertEqual(path.read_text(), after_first)

    def test_write_page_id_on_a_bom_file_finds_and_preserves_the_real_binding(self):
        # Before the fix: a leading UTF-8 BOM sat on line 0 ahead of "---",
        # so _split's line-0 check never matched, the file read as having no
        # frontmatter at all, and write_page_id's no-frontmatter branch
        # prepended a brand new confluence: block ahead of the real one -
        # permanently demoting the original space/parent into what the
        # module now treats as body text. This proves the real binding is
        # found (not just "some binding"), and that only one frontmatter
        # block exists after the write, not two competing ones.
        path = self._write(UNBOUND)
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

        self.assertEqual(read_binding(path)["space"], "98765")
        self.assertEqual(read_binding(path)["parent"], "1234567")

        write_page_id(path, "5555555")

        after = path.read_bytes()
        self.assertTrue(after.startswith(b"\xef\xbb\xbf"), "BOM was dropped")
        self.assertEqual(
            after.count(b"---"), 2,
            "expected exactly one frontmatter block (2 delimiter lines), "
            "not a second one prepended ahead of the real one",
        )
        self.assertEqual(read_binding(path)["page_id"], "5555555")
        self.assertEqual(read_binding(path)["space"], "98765")
        self.assertEqual(read_binding(path)["parent"], "1234567")

    def test_write_page_id_on_a_bom_file_with_no_frontmatter_keeps_the_bom_first(self):
        path = self._write("Just a body.\n")
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
        write_page_id(path, "8901234")
        after = path.read_bytes()
        self.assertTrue(after.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(read_binding(path)["page_id"], "8901234")

    def test_write_page_id_leaves_the_file_intact_if_the_write_is_interrupted(self):
        # Simulates a crash between "temp file written" and "moved into
        # place": os.replace is the exact call that makes the swap atomic,
        # so making it raise is the sharpest way to prove a failure there
        # cannot leave the real file truncated or half-written - the plain
        # open(path, "w") + write() this replaced could not make that
        # promise at all.
        path = self._write(UNBOUND)
        before = path.read_bytes()

        with mock.patch.object(
            frontmatter.os, "replace", side_effect=OSError("simulated crash")
        ):
            with self.assertRaises(OSError):
                write_page_id(path, "5555555")

        self.assertEqual(path.read_bytes(), before, "the real file was touched")
        leftovers = [
            p for p in path.parent.iterdir()
            if p.name.startswith(f".{path.name}.") and p.name.endswith(".tmp")
        ]
        self.assertEqual(leftovers, [], "a temp file was left behind uncleaned")

    def test_write_page_id_preserves_the_file_mode(self):
        path = self._write(UNBOUND)
        os.chmod(path, 0o640)
        write_page_id(path, "5555555")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)


class TestFrontmatterCli(unittest.TestCase):
    """frontmatter.py's own CLI, not the library functions - publish.sh
    pipes `body`'s stdout onward and captures `set-page-id`'s stderr, and
    neither expects a multi-line Python traceback as the answer.
    """

    def _run(self, *args):
        import subprocess
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "frontmatter.py"), *args],
            capture_output=True, text=True,
        )

    def test_body_on_a_non_utf8_file_gives_one_line_not_a_traceback(self):
        f = tempfile.NamedTemporaryFile(suffix=".md", delete=False)
        f.write(b"---\nconfluence:\n  space: \"1\"\n---\n\n\xff\xfe not utf-8\n")
        f.close()
        result = self._run("body", f.name)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("Error:"), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_read_on_a_missing_file_gives_one_line_not_a_traceback(self):
        result = self._run("read", "/nonexistent/path/does-not-exist.md")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("Error:"), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_an_ordinary_file_still_works_through_the_cli(self):
        path = pathlib.Path(
            tempfile.NamedTemporaryFile("w", suffix=".md", delete=False).name
        )
        path.write_text(BOUND)
        result = self._run("read", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("page_id=8901234", result.stdout)


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

    def test_indented_bullet_under_a_list_is_refused_not_mangled(self):
        # Exactly the review's repro: unhandled, this fell through into the
        # paragraph catch-all - the marker survived as literal text, the
        # indentation collapsed, and the outer list split into two <ul>
        # blocks either side of the wreckage, with no error at all. The fix
        # is a refusal, not a silent conversion, so this only asserts the
        # error - there is no "correct HTML+" for this input to compare to.
        src = "- one\n  - nested one\n  - nested two\n- two\n"
        with self.assertRaises(ConversionError) as ctx:
            md_to_htmlplus(src)
        self.assertIn("nested one", str(ctx.exception))

    def test_indented_bullet_after_a_paragraph_is_also_refused(self):
        # A different code path from the case above: here the indented line
        # would have been swallowed by the paragraph-continuation loop
        # rather than met fresh at the top of the line dispatch, since no
        # list was open beforehand. Both paths must refuse.
        src = "Some intro text.\n  - nested bullet\n"
        with self.assertRaises(ConversionError):
            md_to_htmlplus(src)

    def test_indented_numbered_item_is_refused(self):
        src = "1. one\n   2. nested\n"
        with self.assertRaises(ConversionError):
            md_to_htmlplus(src)

    def test_indented_task_item_is_refused(self):
        src = "- [ ] one\n  - [ ] nested\n"
        with self.assertRaises(ConversionError):
            md_to_htmlplus(src)

    def test_ordinary_indented_prose_is_not_mistaken_for_a_list(self):
        # A continuation line that happens to start with whitespace but is
        # not a list marker (no "-", "*" or "N." after the indent) is
        # ordinary prose, not this converter's business to refuse.
        out = md_to_htmlplus("Some intro text.\n  still the same paragraph.\n")
        self.assertEqual(
            out, "<p>Some intro text.   still the same paragraph.</p>"
        )

    # -- Independent review, MAJOR 6

    def test_a_bare_pipe_line_that_is_not_a_table_does_not_hang(self):
        # The reviewer's own repro: a line starting with "|" that does not
        # open a real table (no separator row follows) used to loop
        # forever without advancing - the table branch's own check
        # required a separator row, but the paragraph loop's stop
        # condition only checked "starts with |", so this line failed both
        # and made zero progress either way. Bounded by the test runner's
        # own timeout rather than an explicit one here - if this ever
        # regresses, the whole suite hangs rather than this test failing
        # cleanly, which is a worse outcome deliberately avoided by fixing
        # the loop rather than only detecting it.
        out = md_to_htmlplus("| not a real table\nmore text on the next line\n")
        self.assertEqual(out, "<p>| not a real table more text on the next line</p>")

    def test_a_bare_pipe_line_at_the_end_of_input_does_not_hang(self):
        # The same shape with nothing following it at all - i + 1 <
        # len(lines) is false, so _looks_like_table_start never even reads
        # lines[i + 1].
        out = md_to_htmlplus("Some text.\n|\n")
        self.assertEqual(out, "<p>Some text. |</p>")

    def test_inline_code_containing_literal_asterisks_is_not_reprocessed(self):
        # Finding: **literal** typed inside a code span used to lose its
        # backticks' protection - the bold pattern re-scanned the whole
        # string after the code substitution ran, including the <code>
        # tag's own freshly-inserted content, and picked it up as if it
        # were real bold markup.
        out = md_to_htmlplus("Use `**bold**` literally in code.\n")
        self.assertEqual(
            out, "<p>Use <code>**bold**</code> literally in code.</p>"
        )

    def test_a_literal_html_tag_typed_as_prose_is_not_live_html(self):
        # Found applying the same fix, not one of the reviewer's named
        # bullets: the dead "un-escape the tags the substitutions just
        # produced" step this replaced did a blanket string replace of
        # &lt;strong&gt; -> <strong> across the WHOLE output, with no way
        # to tell "produced by the bold pattern just now" apart from
        # "typed by the author as literal prose" - so documentation that
        # mentions the tag name <strong> by name silently became live
        # formatting instead of the visible tag name it was meant to be.
        out = md_to_htmlplus(
            "Type <strong>literally</strong> to mean the word, not markup.\n"
        )
        self.assertEqual(
            out,
            "<p>Type &lt;strong&gt;literally&lt;/strong&gt; to mean the "
            "word, not markup.</p>",
        )

    def test_a_cpp_fence_is_recognised(self):
        # \w* refused this outright - + is not a word character - so the
        # whole fence fell through to the paragraph catch-all instead of
        # opening a code block.
        out = md_to_htmlplus("```c++\nint x = 1;\n```\n")
        self.assertEqual(out, '<pre><code class="language-c++">int x = 1;</code></pre>')

    def test_a_relative_link_is_refused_not_published_dead(self):
        with self.assertRaises(ConversionError) as ctx:
            md_to_htmlplus("See the [guide](guide.md#heading).\n")
        self.assertIn("guide.md#heading", str(ctx.exception))

    def test_an_in_page_anchor_link_still_works(self):
        # Not a relative link to another file - a same-page anchor, which
        # this converter cannot resolve any better than a relative path,
        # but does not need to: the fragment alone is already correct
        # wherever this page ends up.
        out = md_to_htmlplus("See [above](#introduction).\n")
        self.assertEqual(out, '<p>See <a href="#introduction">above</a>.</p>')

    def test_a_mailto_link_still_works(self):
        # Absolute by RFC 3986's scheme grammar even though it carries no
        # "//" - confirms the check is not accidentally requiring one.
        out = md_to_htmlplus("Email [support](mailto:help@example.com).\n")
        self.assertEqual(
            out,
            '<p>Email <a href="mailto:help@example.com">support</a>.</p>',
        )

    def test_a_quote_in_a_link_href_does_not_break_the_html_attribute(self):
        # href is the LINK regex's own [^)]+ group, so a literal ) in the
        # target would end the capture early regardless of this fix -
        # tested here with a plain quote instead, the shape that actually
        # reaches _link with the attribute-breakout risk intact.
        out = md_to_htmlplus('[x](https://example.com/"widget)\n')
        self.assertEqual(
            out,
            '<p><a href="https://example.com/&quot;widget">x</a></p>',
        )


if __name__ == "__main__":
    unittest.main()
