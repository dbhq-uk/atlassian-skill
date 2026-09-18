import base64
import json
import pathlib
import subprocess
import sys
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from htmlplus import ConversionError, html_to_adf  # noqa: E402
from htmlplus import adf_to_html, adf_to_markdown  # noqa: E402
from htmlplus import _opaque_to_html, _opaque_mark_to_html  # noqa: E402
from htmlplus import check_roundtrip, _first_roundtrip_difference  # noqa: E402
from htmlplus import html_to_adf_for_jira, CONFLUENCE_ONLY, _walk  # noqa: E402


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


class TestPanels(unittest.TestCase):
    def test_warning_panel(self):
        doc = html_to_adf(
            '<div data-type="panel-warning"><p>It will bite.</p></div>'
        )
        node = doc["content"][0]
        self.assertEqual(node["type"], "panel")
        self.assertEqual(node["attrs"], {"panelType": "warning"})
        self.assertEqual(node["content"][0]["type"], "paragraph")

    def test_every_panel_type(self):
        for kind in ("info", "note", "success", "warning", "error"):
            doc = html_to_adf(f'<div data-type="panel-{kind}"><p>x</p></div>')
            self.assertEqual(doc["content"][0]["attrs"]["panelType"], kind)

    def test_unknown_panel_type_is_rejected(self):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf('<div data-type="panel-danger"><p>x</p></div>')
        self.assertIn("panel-danger", str(cm.exception))


class TestStatus(unittest.TestCase):
    def test_lozenge(self):
        doc = html_to_adf(
            '<p><span data-type="status" data-color="green">Built</span></p>'
        )
        self.assertEqual(
            doc["content"][0]["content"][0],
            {"type": "status", "attrs": {"text": "Built", "color": "green"}},
        )

    def test_unknown_colour_is_rejected(self):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf('<p><span data-type="status" data-color="orange">x</span></p>')
        self.assertIn("orange", str(cm.exception))


class TestTasksAndDecisions(unittest.TestCase):
    def test_task_list(self):
        doc = html_to_adf(
            '<ul data-type="task-list">'
            '<li data-type="task-item"><input type="checkbox"> Confirm it</li>'
            '<li data-type="task-item"><input type="checkbox" checked> Publish it</li>'
            "</ul>"
        )
        node = doc["content"][0]
        self.assertEqual(node["type"], "taskList")
        self.assertEqual(node["content"][0]["attrs"]["state"], "TODO")
        self.assertEqual(node["content"][1]["attrs"]["state"], "DONE")
        self.assertEqual(
            node["content"][0]["content"][0]["text"].strip(), "Confirm it"
        )

    def test_decision_list(self):
        doc = html_to_adf(
            '<ul data-type="decision-list">'
            '<li data-type="decision-item" data-state="DECIDED">Correlate on reference</li>'
            "</ul>"
        )
        node = doc["content"][0]
        self.assertEqual(node["type"], "decisionList")
        self.assertEqual(node["content"][0]["attrs"]["state"], "DECIDED")


class TestExpandAndCode(unittest.TestCase):
    def test_expand(self):
        doc = html_to_adf(
            "<details><summary>Full change log</summary><p>x</p></details>"
        )
        node = doc["content"][0]
        self.assertEqual(node["type"], "expand")
        self.assertEqual(node["attrs"], {"title": "Full change log"})
        self.assertEqual(node["content"][0]["type"], "paragraph")

    def test_code_block_keeps_its_language(self):
        doc = html_to_adf(
            '<pre><code class="language-json">{"a": 1}</code></pre>'
        )
        self.assertEqual(
            doc["content"][0],
            {
                "type": "codeBlock",
                "attrs": {"language": "json"},
                "content": [{"type": "text", "text": '{"a": 1}'}],
            },
        )

    def test_code_block_without_a_language_is_plaintext(self):
        doc = html_to_adf("<pre><code>ls -la</code></pre>")
        self.assertEqual(doc["content"][0]["attrs"]["language"], "plaintext")


class TestDatesAndLinks(unittest.TestCase):
    def test_time_node(self):
        doc = html_to_adf(
            '<p>On <time datetime="2026-09-17">17 September 2026</time>.</p>'
        )
        self.assertEqual(doc["content"][0]["content"][1]["type"], "date")
        # ADF dates are epoch milliseconds as a string.
        self.assertEqual(
            doc["content"][0]["content"][1]["attrs"]["timestamp"], "1789603200000"
        )

    def test_empty_anchor_is_an_inline_smart_card(self):
        doc = html_to_adf(
            '<p><a href="https://mycompany.atlassian.net/wiki/x" '
            'data-card-appearance="inline"></a></p>'
        )
        self.assertEqual(
            doc["content"][0]["content"][0],
            {
                "type": "inlineCard",
                "attrs": {"url": "https://mycompany.atlassian.net/wiki/x"},
            },
        )

    def test_block_and_embed_appearances(self):
        for appearance, node_type in (("block", "blockCard"), ("embed", "embedCard")):
            doc = html_to_adf(
                f'<a href="https://example.com/x" data-card-appearance="{appearance}"></a>'
            )
            self.assertEqual(doc["content"][0]["type"], node_type)

    def test_anchor_with_text_is_a_link_mark(self):
        doc = html_to_adf('<p>the <a href="https://example.com/x">contract page</a></p>')
        text = doc["content"][0]["content"][1]
        self.assertEqual(text["text"], "contract page")
        self.assertEqual(
            text["marks"], [{"type": "link", "attrs": {"href": "https://example.com/x"}}]
        )


class TestListsAndLayouts(unittest.TestCase):
    def test_bullet_list(self):
        doc = html_to_adf("<ul><li><p>one</p></li><li><p>two</p></li></ul>")
        self.assertEqual(doc["content"][0]["type"], "bulletList")
        self.assertEqual(doc["content"][0]["content"][0]["type"], "listItem")

    def test_ordered_list(self):
        doc = html_to_adf("<ol><li><p>one</p></li></ol>")
        self.assertEqual(doc["content"][0]["type"], "orderedList")

    def test_two_equal_layout(self):
        doc = html_to_adf(
            '<section data-type="layout-two-equal">'
            '<div data-type="column"><p>L</p></div>'
            '<div data-type="column"><p>R</p></div>'
            "</section>"
        )
        node = doc["content"][0]
        self.assertEqual(node["type"], "layoutSection")
        self.assertEqual(len(node["content"]), 2)
        self.assertEqual(node["content"][0]["type"], "layoutColumn")
        self.assertEqual(node["content"][0]["attrs"], {"width": 50.0})

    def test_column_count_must_match_the_layout(self):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf(
                '<section data-type="layout-three-equal">'
                '<div data-type="column"><p>L</p></div>'
                "</section>"
            )
        self.assertIn("3 columns", str(cm.exception))


class TestUnclosedElementsAreRejected(unittest.TestCase):
    def test_unclosed_section_is_rejected(self):
        # An unclosed layout would otherwise emit successfully with its
        # private _expected/_layout bookkeeping keys still on the node -
        # invalid ADF, POSTed straight to Confluence.
        with self.assertRaises(ConversionError) as cm:
            html_to_adf(
                '<section data-type="layout-two-equal">'
                '<div data-type="column"><p>L</p></div>'
            )
        self.assertIn("layoutSection", str(cm.exception))


class TestParkedTextAccumulates(unittest.TestCase):
    def test_summary_accumulates_text_around_inline_marks(self):
        # Inline markup inside <summary> used to split the title into
        # separate handle_data runs, and each run overwrote the last -
        # only "log" survived. Every run must accumulate instead.
        doc = html_to_adf(
            "<details><summary>Full <em>change</em> log</summary><p>x</p></details>"
        )
        node = doc["content"][0]
        self.assertEqual(node["attrs"], {"title": "Full change log"})


TABLE = (
    '<table data-width="1800">'
    "<thead><tr>"
    '<th data-colwidth="242"><p>Item</p></th>'
    '<th data-colwidth="1478"><p>What it means</p></th>'
    "</tr></thead>"
    "<tbody><tr>"
    '<td data-colwidth="242"><p>A</p></td>'
    '<td data-colwidth="1478"><p>B</p></td>'
    "</tr></tbody>"
    "</table>"
)


class TestTables(unittest.TestCase):
    def test_table_shape(self):
        doc = html_to_adf(TABLE)
        node = doc["content"][0]
        self.assertEqual(node["type"], "table")
        self.assertEqual(node["attrs"]["width"], 1800)
        self.assertEqual(len(node["content"]), 2)
        self.assertEqual(node["content"][0]["type"], "tableRow")
        self.assertEqual(node["content"][0]["content"][0]["type"], "tableHeader")
        self.assertEqual(node["content"][1]["content"][0]["type"], "tableCell")

    def test_colwidth_becomes_a_list_of_ints(self):
        doc = html_to_adf(TABLE)
        cell = doc["content"][0]["content"][0]["content"][0]
        self.assertEqual(cell["attrs"]["colwidth"], [242])

    def test_layout_and_number_column_attributes(self):
        doc = html_to_adf(
            '<table data-width="400" data-layout="center" data-number-column="true">'
            '<tbody><tr><td data-colwidth="400"><p>x</p></td></tr></tbody></table>'
        )
        attrs = doc["content"][0]["attrs"]
        self.assertEqual(attrs["layout"], "center")
        self.assertTrue(attrs["isNumberColumnEnabled"])

    def test_a_unit_on_colwidth_is_rejected(self):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf(
                '<table data-width="400"><tbody><tr>'
                '<td data-colwidth="242px"><p>x</p></td></tr></tbody></table>'
            )
        self.assertIn("242px", str(cm.exception))
        self.assertIn("plain number", str(cm.exception))

    def test_a_percentage_on_colwidth_is_rejected(self):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf(
                '<table data-width="400"><tbody><tr>'
                '<td data-colwidth="50%"><p>x</p></td></tr></tbody></table>'
            )
        self.assertIn("50%", str(cm.exception))

    def test_a_column_missing_colwidth_on_one_cell_is_rejected(self):
        # Confluence resets the whole table to even columns, which reads as a
        # formatting regression to everyone who sees the diff.
        with self.assertRaises(ConversionError) as cm:
            html_to_adf(
                '<table data-width="400">'
                '<thead><tr><th data-colwidth="200"><p>A</p></th>'
                '<th data-colwidth="200"><p>B</p></th></tr></thead>'
                "<tbody><tr><td><p>x</p></td>"
                '<td data-colwidth="200"><p>y</p></td></tr></tbody></table>'
            )
        self.assertIn("column 1", str(cm.exception))
        self.assertIn("data-colwidth", str(cm.exception))

    def test_a_column_with_two_different_widths_is_rejected(self):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf(
                '<table data-width="400">'
                '<thead><tr><th data-colwidth="200"><p>A</p></th></tr></thead>'
                '<tbody><tr><td data-colwidth="300"><p>x</p></td></tr></tbody></table>'
            )
        self.assertIn("column 1", str(cm.exception))
        self.assertIn("200", str(cm.exception))
        self.assertIn("300", str(cm.exception))

    def test_a_table_with_no_widths_at_all_is_allowed(self):
        # Sizing is a house-style rule, not an API rule. An unsized table is
        # valid ADF, so the converter passes it and the checklist catches it.
        doc = html_to_adf("<table><tbody><tr><td><p>x</p></td></tr></tbody></table>")
        self.assertEqual(doc["content"][0]["type"], "table")
        self.assertNotIn("colwidth", doc["content"][0]["content"][0]["content"][0]["attrs"])


class TestOrphanTableAndCheckboxTags(unittest.TestCase):
    # Malformed input must always yield a ConversionError, never a raw
    # Python traceback - main() only catches ConversionError, so anything
    # else reaches a CLI caller as an unhandled crash instead of a clean
    # "Error: ..." message.

    def test_orphan_closing_table_tag_is_rejected(self):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf("<p>x</p></table>")
        self.assertIn("table", str(cm.exception))

    def test_orphan_td_outside_a_table_is_rejected(self):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf('<td data-colwidth="1">x</td>')
        self.assertIn("<td>", str(cm.exception))
        self.assertIn("table", str(cm.exception))

    def test_orphan_tr_outside_a_table_is_rejected(self):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf("<tr><td>x</td></tr>")
        self.assertIn("<tr>", str(cm.exception))
        self.assertIn("table", str(cm.exception))

    def test_checked_checkbox_outside_a_task_item_is_rejected(self):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf('<p><input type="checkbox" checked></p>')
        self.assertIn("checkbox", str(cm.exception))
        self.assertIn("task-list item", str(cm.exception))

    def test_unchecked_checkbox_outside_a_task_item_is_also_rejected(self):
        # Previously silently dropped rather than refused - the checked
        # branch above validated placement, the unchecked one did not, so
        # a misplaced <input type="checkbox"> with no checked attribute
        # simply vanished with no error and no output at all.
        with self.assertRaises(ConversionError) as cm:
            html_to_adf('<p><input type="checkbox"></p>')
        self.assertIn("checkbox", str(cm.exception))
        self.assertIn("task-list item", str(cm.exception))


class TestNesting(unittest.TestCase):
    """One case per row of the nesting table in references/html-patterns.md.

    Each of these is rejected by Confluence with a descriptive error after the
    call. The point of the converter is that they are rejected here instead.
    """

    def _rejects(self, fragment, *expected_fragments):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf(fragment)
        message = str(cm.exception)
        for fragment_text in expected_fragments:
            self.assertIn(fragment_text, message)

    def test_list_item_cannot_hold_a_heading(self):
        self._rejects("<ul><li><h2>no</h2></li></ul>", "heading", "listItem")

    def test_list_item_cannot_hold_a_table(self):
        self._rejects(
            "<ul><li><table><tbody><tr><td><p>x</p></td></tr></tbody></table></li></ul>",
            "table", "listItem",
        )

    def test_list_item_cannot_hold_a_panel(self):
        self._rejects(
            '<ul><li><div data-type="panel-info"><p>x</p></div></li></ul>',
            "panel", "listItem",
        )

    def test_panel_cannot_hold_a_table(self):
        self._rejects(
            '<div data-type="panel-info">'
            "<table><tbody><tr><td><p>x</p></td></tr></tbody></table></div>",
            "table", "panel",
        )

    def test_panel_cannot_hold_an_expand(self):
        self._rejects(
            '<div data-type="panel-warning"><details><summary>s</summary>'
            "<p>x</p></details></div>",
            "expand", "panel",
        )

    def test_panel_cannot_nest_in_a_panel(self):
        self._rejects(
            '<div data-type="panel-info"><div data-type="panel-note">'
            "<p>x</p></div></div>",
            "panel", "panel",
        )

    def test_expand_cannot_nest_in_an_expand(self):
        self._rejects(
            "<details><summary>a</summary><details><summary>b</summary>"
            "<p>x</p></details></details>",
            "expand", "expand",
        )

    def test_expand_cannot_hold_a_layout_section(self):
        self._rejects(
            "<details><summary>a</summary>"
            '<section data-type="layout-two-equal">'
            '<div data-type="column"><p>L</p></div>'
            '<div data-type="column"><p>R</p></div>'
            "</section></details>",
            "layoutSection", "expand",
        )

    def test_table_cell_cannot_hold_a_table(self):
        self._rejects(
            "<table><tbody><tr><td>"
            "<table><tbody><tr><td><p>x</p></td></tr></tbody></table>"
            "</td></tr></tbody></table>",
            "table", "tableCell",
        )

    def test_table_cell_cannot_hold_a_layout_section(self):
        self._rejects(
            "<table><tbody><tr><td>"
            '<section data-type="layout-two-equal">'
            '<div data-type="column"><p>L</p></div>'
            '<div data-type="column"><p>R</p></div>'
            "</section></td></tr></tbody></table>",
            "layoutSection", "tableCell",
        )

    def test_task_item_cannot_hold_a_block(self):
        self._rejects(
            '<ul data-type="task-list"><li data-type="task-item">'
            "<p>no</p></li></ul>",
            "paragraph", "taskItem",
        )

    def test_decision_item_cannot_hold_a_block(self):
        self._rejects(
            '<ul data-type="decision-list">'
            '<li data-type="decision-item" data-state="DECIDED">'
            "<p>no</p></li></ul>",
            "paragraph", "decisionItem",
        )

    def test_heading_cannot_hold_a_block(self):
        self._rejects("<h2><p>no</p></h2>", "paragraph", "heading")

    def test_blockquote_cannot_nest_in_a_blockquote(self):
        self._rejects(
            "<blockquote><blockquote><p>x</p></blockquote></blockquote>",
            "blockquote", "blockquote",
        )

    def test_table_cannot_nest_in_a_table_via_an_expand(self):
        # An expand inside a table cell is allowed and becomes a nested expand,
        # so this case must still reject on the table, not on the expand.
        self._rejects(
            "<table><tbody><tr><td><details><summary>s</summary>"
            "<table><tbody><tr><td><p>x</p></td></tr></tbody></table>"
            "</details></td></tr></tbody></table>",
            "table",
        )

    def test_a_panel_after_a_list_is_fine(self):
        # The documented way to attach a panel to a list: close it first.
        doc = html_to_adf(
            "<ul><li><p>one</p></li></ul>"
            '<div data-type="panel-info"><p>note</p></div>'
        )
        self.assertEqual(doc["content"][0]["type"], "bulletList")
        self.assertEqual(doc["content"][1]["type"], "panel")

    def test_an_expand_inside_a_table_cell_is_allowed(self):
        doc = html_to_adf(
            '<table><tbody><tr><td data-colwidth="400">'
            "<details><summary>More</summary><p>x</p></details>"
            "</td></tr></tbody></table>"
        )
        cell = doc["content"][0]["content"][0]["content"][0]
        self.assertEqual(cell["content"][0]["type"], "expand")

    # -- Finding A: nodes appended directly bypassed the validator. A live
    # probe against a real Confluence site showed the v2 API itself only
    # rejects the panel case (a bare 500, no detail) - the others are
    # accepted on the wire. They are still rejected here, because a
    # document the API swallows is not the same as one the editor can
    # represent: an accepted-but-malformed document gets silently repaired
    # or mangled on the next human edit, which is worse than a rejection.

    def test_bare_text_in_a_list_item_is_rejected(self):
        # <li>hello</li> - the most natural list HTML anyone writes, and it
        # was reaching the tree straight through handle_data with no check
        # at all before this fix.
        self._rejects("<ul><li>hello</li></ul>", "listItem")

    def test_a_rule_cannot_sit_directly_in_a_list_item(self):
        # <hr> was appended straight to content, bypassing _check_nesting -
        # so FORBIDDEN_CHILDREN["listItem"] already named "rule" but the
        # rule could never actually fire.
        self._rejects("<ul><li><hr></li></ul>", "rule", "listItem")

    def test_a_rule_cannot_sit_directly_in_a_heading(self):
        self._rejects("<h2><hr></h2>", "rule", "heading")

    def test_status_cannot_sit_in_a_code_block(self):
        # A status lozenge is legal inline content in a heading or a task
        # item, but a codeBlock takes plain text only - not even the other
        # inline nodes a heading can hold - so it needs its own check
        # (TEXT_ONLY), not just BLOCK_TYPES membership.
        self._rejects(
            '<pre><span data-type="status" data-color="green">x</span></pre>',
            "status", "codeBlock",
        )

    # -- Finding B: a block or embed card was silently relocated to the
    # document root instead of staying where its author put it. Silently
    # moving content is worse than refusing it.

    def test_a_block_card_is_appended_to_its_actual_parent_not_the_document_root(self):
        doc = html_to_adf(
            '<div data-type="panel-info"><p>x</p>'
            '<a href="https://example.com" data-card-appearance="block"></a>'
            "</div>"
        )
        self.assertEqual(len(doc["content"]), 1)
        panel = doc["content"][0]
        self.assertEqual(panel["type"], "panel")
        self.assertEqual(panel["content"][1]["type"], "blockCard")

    def test_an_embed_card_cannot_sit_directly_in_a_panel(self):
        # Now that the card is checked in place rather than relocated to
        # the root first, panel's existing "embedCard" forbidden entry can
        # actually fire.
        self._rejects(
            '<div data-type="panel-info"><p>x</p>'
            '<a href="https://example.com" data-card-appearance="embed"></a>'
            "</div>",
            "embedCard", "panel",
        )

    # -- Finding C: blockquote's forbidden set was too narrow. ADF
    # blockquote content is paragraphs, lists and code blocks only.

    def test_blockquote_cannot_hold_a_heading(self):
        self._rejects("<blockquote><h2>no</h2></blockquote>", "heading", "blockquote")

    def test_blockquote_cannot_hold_a_table(self):
        self._rejects(
            "<blockquote><table><tbody><tr><td><p>x</p></td></tr></tbody>"
            "</table></blockquote>",
            "table", "blockquote",
        )

    def test_blockquote_cannot_hold_a_panel(self):
        self._rejects(
            '<blockquote><div data-type="panel-info"><p>x</p></div></blockquote>',
            "panel", "blockquote",
        )

    def test_blockquote_cannot_hold_an_expand(self):
        self._rejects(
            "<blockquote><details><summary>s</summary><p>x</p></details>"
            "</blockquote>",
            "expand", "blockquote",
        )

    def test_blockquote_cannot_hold_a_layout_section(self):
        self._rejects(
            "<blockquote>"
            '<section data-type="layout-two-equal">'
            '<div data-type="column"><p>L</p></div>'
            '<div data-type="column"><p>R</p></div>'
            "</section></blockquote>",
            "layoutSection", "blockquote",
        )

    # -- Finding 3: a block/embed card nested inside a paragraph had no
    # FORBIDDEN_CHILDREN rule, so html_to_adf accepted it,
    # and adf_to_html's inline renderer had no branch for it either - the
    # card silently vanished on the way back to HTML+. Fixed on the forward
    # side: refuse the malformed input at authoring time rather than try to
    # round-trip it.

    def test_paragraph_cannot_hold_a_block_card(self):
        self._rejects(
            '<p><a href="https://example.com/x" '
            'data-card-appearance="block"></a></p>',
            "blockCard", "paragraph",
        )


ROUND_TRIP_CASES = [
    "<p>Hello.</p>",
    "<h2>Open items</h2>",
    "<p><strong>bold</strong></p>",
    "<p><code>reference</code></p>",
    '<div data-type="panel-warning"><p>It will bite.</p></div>',
    '<p><span data-type="status" data-color="green">Built</span></p>',
    '<ul data-type="task-list">'
    '<li data-type="task-item"><input type="checkbox"> Do it</li></ul>',
    '<ul data-type="decision-list">'
    '<li data-type="decision-item" data-state="DECIDED">Agreed</li></ul>',
    "<details><summary>More</summary><p>x</p></details>",
    '<pre><code class="language-json">{"a": 1}</code></pre>',
    '<p>On <time datetime="2026-09-17">17 September 2026</time>.</p>',
    '<p><a href="https://example.com/x" data-card-appearance="inline"></a></p>',
    "<ul><li><p>one</p></li></ul>",
    "<ol><li><p>one</p></li></ol>",
    '<table data-width="400"><tbody><tr>'
    '<td data-colwidth="400"><p>x</p></td></tr></tbody></table>',
    '<section data-type="layout-two-equal">'
    '<div data-type="column"><p>L</p></div>'
    '<div data-type="column"><p>R</p></div></section>',
    # Two or more marks on the same run. Regression case for the mark-order
    # bug found in review: the emitter was applying marks[0] innermost,
    # opposite the sense the forward parser stores them in, so nesting
    # reversed on every pass.
    "<p><strong><em>both</em></strong></p>",
    '<p><a href="https://example.com/x"><strong>link</strong></a></p>',
    # Finding 4: html_to_adf had no <br> branch at all, so
    # a page containing a hard break (shift-enter in the Confluence editor)
    # could not be converted back to ADF after a round trip through
    # adf_to_html, which does emit <br> for a hardBreak node.
    "<p>line one<br>line two</p>",
    # Finding 5: the old blanket "if not data.strip():
    # return" at the top of handle_data ate the space between two marked
    # runs, not just inter-tag formatting whitespace.
    "<p><strong>a</strong> <em>b</em></p>",
]


class TestRoundTrip(unittest.TestCase):
    def test_every_pattern_survives_html_to_adf_to_html_to_adf(self):
        """Stability is the property that matters, not string equality.

        A second pass through the converter must produce the same ADF as the
        first. That is what makes a fetch-splice-verify update safe: the
        untouched parts of a fetched body come back unchanged.
        """
        for fragment in ROUND_TRIP_CASES:
            with self.subTest(fragment=fragment):
                once = html_to_adf(fragment)
                twice = html_to_adf(adf_to_html(once))
                self.assertEqual(once, twice)

    def test_two_marks_keep_their_source_nesting_order(self):
        # Finding 2: the emitter applied marks[0]
        # innermost, the opposite of how html_to_adf stores them (outer to
        # inner, in tag-open order), so bold-inside-em became em-inside-bold
        # on every pass. Asserting the exact string, not just round-trip
        # stability, so a future regression fails with a readable diff
        # rather than a bare ADF inequality.
        doc = html_to_adf("<p><strong><em>both</em></strong></p>")
        self.assertEqual(adf_to_html(doc), "<p><strong><em>both</em></strong></p>")

    def test_hard_break_converts_to_adf(self):
        # Finding 4: html_to_adf had no <br> branch, so a
        # page containing a hard break could be fetched and rendered to
        # HTML+ but never converted back - splicing a change and
        # re-converting raised "<br> is not a known HTML+ element" on the
        # very markup adf_to_html had just produced.
        doc = html_to_adf("<p>line one<br>line two</p>")
        content = doc["content"][0]["content"]
        self.assertEqual(
            [n["type"] for n in content], ["text", "hardBreak", "text"]
        )

    def test_space_between_two_marked_runs_survives(self):
        # Finding 5: a lone space between two inline
        # elements is genuine content in a paragraph, not the inter-tag
        # formatting whitespace the same guard correctly discards at the
        # document root or inside a block-only container.
        doc = html_to_adf("<p><strong>a</strong> <em>b</em></p>")
        texts = [n["text"] for n in doc["content"][0]["content"]]
        self.assertEqual(texts, ["a", " ", "b"])

    def test_whitespace_between_list_items_still_vanishes(self):
        # Guard against the false negative the review explicitly warned
        # against when widening BLOCK_ONLY_PARENTS to fix Finding 5: once
        # a </li> closes, the open block is the list itself (bulletList,
        # orderedList, taskList or decisionList - not listItem, which
        # already popped), so the same whitespace-discard rule has to
        # reach the list container too, or formatting indentation between
        # items would be kept as a stray text node.
        with_whitespace = html_to_adf(
            "<ul>\n  <li><p>one</p></li>\n  <li><p>two</p></li>\n</ul>"
        )
        without_whitespace = html_to_adf(
            "<ul><li><p>one</p></li><li><p>two</p></li></ul>"
        )
        self.assertEqual(with_whitespace, without_whitespace)
        self.assertEqual(len(with_whitespace["content"][0]["content"]), 2)


class TestUnsupportedAdfNode(unittest.TestCase):
    """A node type this converter cannot render by name.

    A real fetched Confluence page can carry node types this converter has
    no HTML+ for - mention, emoji, extension, bodiedExtension and more.
    There is no HTML+ syntax to construct one through html_to_adf, so these
    ADF documents are built directly, the way a page fetched from the API
    would arrive.

    adf_to_html used to raise on a node like this rather than silently drop
    it - correct at the time, because the only other behaviour on offer was
    dropping it. Opaque passthrough replaces that with something strictly
    better: nothing is refused and nothing is lost. The test below asserts
    the passthrough behaviour; an earlier version of this suite asserted
    the raise it replaced.

    UNSUPPORTED_DOC's middle node was mediaSingle/media until this
    converter gained named HTML+ support for it (a file attachment, the
    shape attachments.sh upload produces) - at which point a mediaSingle no
    longer demonstrated "a node this converter cannot render by name", it
    demonstrated the opposite. Swapped for bodiedExtension, which this
    converter has no named HTML+ syntax for at all (see CONFLUENCE_ONLY's
    own note on it, below) and so remains a genuine example of this class's
    premise.
    """

    UNSUPPORTED_DOC = {
        "type": "doc", "version": 1,
        "content": [
            {"type": "paragraph",
             "content": [{"type": "text", "text": "Before."}]},
            {"type": "bodiedExtension",
             "attrs": {"extensionType": "com.atlassian.confluence.macro.core",
                       "extensionKey": "com.example.macro", "parameters": {}},
             "content": [{"type": "paragraph",
                          "content": [{"type": "text", "text": "Inside."}]}]},
            {"type": "paragraph",
             "content": [{"type": "text", "text": "After."}]},
        ],
    }

    def test_adf_to_html_no_longer_raises_carries_the_node_through_opaque(self):
        # This used to raise ConversionError - the exact opposite of the
        # assertion below - because the only alternative on offer at the
        # time was silently dropping the node. Passthrough removes that
        # trade-off: the unrecognised node is carried through as opaque
        # HTML+ instead of being refused, and the two surrounding
        # paragraphs are untouched.
        html = adf_to_html(self.UNSUPPORTED_DOC)
        self.assertIn('data-type="adf-opaque"', html)
        self.assertIn("Before.", html)
        self.assertIn("After.", html)
        # And it reads back to the exact node it came from.
        self.assertEqual(html_to_adf(html), self.UNSUPPORTED_DOC)

    def test_adf_to_markdown_marks_the_gap_instead_of_raising(self):
        # Documented one-way and read-only, never written back, so there is
        # no unsafe overwrite to guard against - a visible placeholder beats
        # a hard failure for an agent that is only trying to read the page.
        md = adf_to_markdown(self.UNSUPPORTED_DOC)
        self.assertIn("Before.", md)
        self.assertIn("[unsupported node: bodiedExtension]", md)
        self.assertIn("After.", md)


class TestOpaquePassthrough(unittest.TestCase):
    """An unrecognised ADF node or mark is carried through untouched rather
    than refused (a node) or silently dropped (a mark).

    Fixtures here are invented, not lifted from any real page - an
    extension always carries extensionKey "com.example.macro", and media
    ids/collections are placeholder strings.
    """

    EXTENSION_NODE = {
        "type": "extension",
        "attrs": {
            "extensionType": "com.atlassian.confluence.macro.core",
            "extensionKey": "com.example.macro",
            "parameters": {"macroParams": {"exampleParam": {"value": "42"}}},
        },
    }

    def test_1_unknown_block_node_round_trips_byte_identical(self):
        doc = {"type": "doc", "version": 1, "content": [self.EXTENSION_NODE]}
        html = adf_to_html(doc)
        self.assertTrue(html.startswith('<div data-type="adf-opaque"'))
        self.assertEqual(html_to_adf(html), doc)

    def test_2_unknown_inline_node_inside_a_paragraph_round_trips(self):
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "Assigned to "},
                    {"type": "mention",
                     "attrs": {"id": "example-user-1", "text": "@Example Person"}},
                    {"type": "text", "text": "."},
                ]},
            ],
        }
        html = adf_to_html(doc)
        self.assertIn('<span data-type="adf-opaque"', html)
        self.assertEqual(html_to_adf(html), doc)

    def test_3_unknown_mark_round_trips_wrapped_text_stays_editable(self):
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "at risk",
                     "marks": [{"type": "textColor", "attrs": {"color": "#ff0000"}}]},
                ]},
            ],
        }
        html = adf_to_html(doc)
        self.assertIn('data-type="adf-opaque-mark"', html)
        # The wrapped text is plain HTML+, not base64 - readable and editable.
        self.assertIn(">at risk<", html)
        round_tripped = html_to_adf(html)
        self.assertEqual(round_tripped, doc)
        text_node = round_tripped["content"][0]["content"][0]
        self.assertEqual(text_node["type"], "text")
        self.assertEqual(text_node["text"], "at risk")

    def test_4_known_type_with_unknown_attrs_is_unaffected(self):
        # Passthrough must trigger on an unrecognised type, never on merely
        # unrecognised attrs of a type this converter already knows how to
        # render - or every panel gains a future attribute this converter
        # has not been taught yet becomes opaque instead of a panel.
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "panel",
                 "attrs": {"panelType": "info", "futureAttr": "not yet named"},
                 "content": [{"type": "paragraph",
                              "content": [{"type": "text", "text": "x"}]}]},
            ],
        }
        html = adf_to_html(doc)
        self.assertNotIn("adf-opaque", html)
        self.assertTrue(html.startswith('<div data-type="panel-info">'))

    def test_5_opaque_node_inside_a_code_block_converts(self):
        # listItem was an earlier fixture for this test, but "extension" is
        # not in FORBIDDEN_CHILDREN["listItem"] - that fixture passed the
        # validator regardless of whether the bypass existed, so it never
        # actually exercised rule 3 (a review finding).
        # codeBlock is TEXT_ONLY: normally *any* non-text child is rejected
        # there (test_status_cannot_sit_in_a_code_block covers a status
        # node), so it is the container where the bypass is actually
        # observable. An opaque node converts inside one; an ordinary
        # ADF node with real nesting rules of its own - a rule - still
        # doesn't, proving this is the opaque bypass and not a general
        # loosening of codeBlock's content model.
        opaque = _opaque_to_html(self.EXTENSION_NODE, tag="div")
        doc = html_to_adf(f"<pre><code>{opaque}</code></pre>")
        self.assertEqual(doc["content"][0]["type"], "codeBlock")
        self.assertEqual(doc["content"][0]["content"][0], self.EXTENSION_NODE)
        with self.assertRaises(ConversionError) as cm:
            html_to_adf("<pre><code><hr></code></pre>")
        self.assertIn("codeBlock", str(cm.exception))

    def test_6_adf_to_markdown_emits_a_placeholder_naming_the_type(self):
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Before."}]},
                self.EXTENSION_NODE,
                {"type": "paragraph", "content": [{"type": "text", "text": "After."}]},
            ],
        }
        md = adf_to_markdown(doc)
        self.assertIn("Before.", md)
        self.assertIn("extension", md)
        self.assertIn("After.", md)

    def test_7_opaque_node_survives_two_full_round_trips(self):
        doc = {"type": "doc", "version": 1, "content": [self.EXTENSION_NODE]}
        once = html_to_adf(adf_to_html(doc))
        twice = html_to_adf(adf_to_html(once))
        self.assertEqual(once, doc)
        self.assertEqual(twice, doc)

    def test_8_realistic_composite_round_trips_byte_identical(self):
        # Paragraphs, a table, an extension and a media node at block
        # position, and an unrecognised mark at inline position, all in one
        # document - the mix a real page actually carries.
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Overview."}]},
                {"type": "table", "attrs": {"width": 400}, "content": [
                    {"type": "tableRow", "content": [
                        {"type": "tableCell", "attrs": {}, "content": [
                            {"type": "paragraph", "content": [
                                {"type": "text", "text": "Status: "},
                                {"type": "text", "text": "at risk",
                                 "marks": [{"type": "textColor",
                                            "attrs": {"color": "#ff0000"}}]},
                            ]},
                        ]},
                    ]},
                ]},
                self.EXTENSION_NODE,
                {"type": "mediaSingle", "attrs": {"layout": "center"}, "content": [
                    {"type": "media", "attrs": {
                        "type": "file",
                        "id": "11111111-1111-1111-1111-111111111111",
                        "collection": "example-collection",
                    }},
                ]},
                {"type": "paragraph", "content": [{"type": "text", "text": "End."}]},
            ],
        }
        html = adf_to_html(doc)
        self.assertEqual(html_to_adf(html), doc)


class TestOpaqueHardening(unittest.TestCase):
    """Review findings on opaque passthrough, verified fresh rather than
    trusted: Critical 1 (silent content loss on an unclosed opaque element)
    and Important 3-5 (data-adf validation, mark dedupe, and the two
    numeric attrs _format_number missed).
    """

    def test_unclosed_opaque_node_raises_rather_than_silently_dropping_content(self):
        # Critical 1. handle_data ignores everything while an opaque
        # element is open (rule 6 - its content already travelled in
        # data-adf). Before this fix, an unclosed opaque tag left that
        # guard on for the rest of the fragment: every real paragraph
        # after the break vanished silently, with a clean exit - and after
        # the empty-content fix elsewhere in this task, the resulting empty
        # paragraph looked exactly like an ordinary blank line.
        opaque_open = _opaque_to_html(
            {"type": "extension",
             "attrs": {"extensionType": "com.atlassian.confluence.macro.core",
                       "extensionKey": "com.example.macro", "parameters": {}}},
            tag="div",
        ).rsplit("</div>", 1)[0]
        fragment = "<p>Keep.</p>" + opaque_open + "<p>This prose must survive.</p>"
        with self.assertRaises(ConversionError) as cm:
            html_to_adf(fragment)
        self.assertIn("adf-opaque", str(cm.exception))

    def test_unclosed_opaque_mark_raises(self):
        # The same gap on the mark side - _opaque_mark_stack, not
        # _opaque_stack - which the block case above cannot reach, since a
        # mark never pushes onto builder.blocks at all.
        mark_open = _opaque_mark_to_html(
            {"type": "textColor", "attrs": {"color": "#ff0000"}}, ""
        ).rsplit("</span>", 1)[0]
        fragment = f"<p>{mark_open}x</p>"
        with self.assertRaises(ConversionError) as cm:
            html_to_adf(fragment)
        self.assertIn("adf-opaque-mark", str(cm.exception))

    def test_malformed_data_adf_raises_conversion_error_not_a_traceback(self):
        # Important 3. Five ways data-adf can be broken, each of which
        # reached the caller as a bare Python traceback (not the "Error:
        # ..." message every other failure in this file produces) before
        # _decode_adf wrapped them.
        good = base64.b64encode(
            json.dumps({"type": "extension", "attrs": {}}).encode("utf-8")
        ).decode("ascii")
        cases = {
            "malformed base64": "not-valid-base64!!!",
            "truncated base64": good[:-4],
            "valid base64, not JSON": base64.b64encode(b"not json").decode("ascii"),
            "non-ASCII in the attribute": "café",
        }
        for label, payload in cases.items():
            with self.subTest(case=label):
                fragment = f'<div data-type="adf-opaque" data-adf="{payload}"></div>'
                with self.assertRaises(ConversionError) as cm:
                    html_to_adf(fragment)
                self.assertIn("data-adf", str(cm.exception))
        with self.subTest(case="missing attribute"):
            with self.assertRaises(ConversionError) as cm:
                html_to_adf('<div data-type="adf-opaque"></div>')
            self.assertIn("data-adf", str(cm.exception))

    def test_valid_json_but_not_an_adf_node_or_mark_is_rejected(self):
        # Important 3. A bare number, a list and null are all valid JSON,
        # but none is "a JSON object with a type" - every node or mark
        # _encode_adf ever produces is. Before this fix all three were
        # accepted and built into the document with a clean exit.
        for value in (123, [], None):
            payload = base64.b64encode(json.dumps(value).encode("utf-8")).decode("ascii")
            fragment = f'<div data-type="adf-opaque" data-adf="{payload}"></div>'
            with self.subTest(value=value):
                with self.assertRaises(ConversionError) as cm:
                    html_to_adf(fragment)
                self.assertIn("data-adf", str(cm.exception))

    def test_two_opaque_marks_of_the_same_type_both_survive(self):
        # Important 4. _current_marks deduped by type alone, harmless while
        # an opaque mark never reached self.marks - but overlapping inline
        # comments are exactly two "annotation" marks of the same type with
        # different attrs (different comment ids) on one run of text, and
        # deduping by type alone silently dropped every one but the first.
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "flagged",
                     "marks": [
                         {"type": "annotation", "attrs": {"id": "comment-1"}},
                         {"type": "annotation", "attrs": {"id": "comment-2"}},
                     ]},
                ]},
            ],
        }
        html = adf_to_html(doc)
        self.assertEqual(html.count('data-type="adf-opaque-mark"'), 2)
        round_tripped = html_to_adf(html)
        self.assertEqual(round_tripped, doc)
        marks = round_tripped["content"][0]["content"][0]["marks"]
        self.assertEqual(len(marks), 2)
        self.assertEqual(
            {m["attrs"]["id"] for m in marks}, {"comment-1", "comment-2"}
        )

    def test_colspan_rowspan_as_json_floats_round_trip(self):
        # Important 5. Same root cause as table width/colwidth - a real
        # page can return colspan/rowspan as a JSON float (2.0) - but
        # colspan/rowspan had no _format_number treatment at all before
        # this fix, so "2.0" reached the reverse parser's bare int(...)
        # unguarded.
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "table", "content": [
                    {"type": "tableRow", "content": [
                        {"type": "tableCell",
                         "attrs": {"colspan": 2.0, "rowspan": 3.0},
                         "content": [{"type": "paragraph",
                                      "content": [{"type": "text", "text": "x"}]}]},
                    ]},
                ]},
            ],
        }
        html = adf_to_html(doc)
        self.assertIn('colspan="2"', html)
        self.assertIn('rowspan="3"', html)
        round_tripped = html_to_adf(html)
        cell = round_tripped["content"][0]["content"][0]["content"][0]
        self.assertEqual(cell["attrs"]["colspan"], 2)
        self.assertEqual(cell["attrs"]["rowspan"], 3)

    def test_non_integer_colspan_raises_conversion_error_not_a_traceback(self):
        # Important 5. Before this fix, int(a["colspan"]) had no guard at
        # all - a malformed value raised a bare ValueError that escaped
        # main()'s ConversionError handling as a Python traceback.
        with self.assertRaises(ConversionError) as cm:
            html_to_adf(
                '<table><tbody><tr><td colspan="2.5"><p>x</p></td></tr>'
                "</tbody></table>"
            )
        self.assertIn("colspan", str(cm.exception))
        self.assertIn("plain number", str(cm.exception))


class TestMedia(unittest.TestCase):
    """Named HTML+ support for mediaSingle/media/caption, so an author can
    write a figure by hand rather than rely on opaque passthrough alone.

    The fixtures and the attrs modelled (width, height, localId on media;
    width, widthType on mediaSingle; localId on caption) come from
    re-measuring this converter against 289 pages of a live Confluence
    site: real pages carry widthType and localId on nearly every figure,
    and modelling a narrower set of attrs would have made every one of
    those real pages fail the round-trip gate that used to pass them
    (opaquely).
    """

    FIGURE = (
        '<figure data-type="media-single" data-layout="center" data-width="80">'
        '<div data-type="media" data-media-type="file" data-id="abc-123" '
        'data-collection="contentId-999" data-alt="diagram.svg"></div>'
        "<figcaption>The diagram</figcaption></figure>"
    )

    def test_figure_becomes_a_media_single(self):
        doc = html_to_adf(self.FIGURE)
        node = doc["content"][0]
        self.assertEqual(node["type"], "mediaSingle")
        self.assertEqual(node["attrs"], {"layout": "center", "width": 80.0})
        self.assertEqual(node["content"][0]["type"], "media")
        self.assertEqual(node["content"][0]["attrs"]["id"], "abc-123")
        self.assertEqual(node["content"][1]["type"], "caption")

    def test_media_round_trips(self):
        once = html_to_adf(self.FIGURE)
        self.assertEqual(once, html_to_adf(adf_to_html(once)))

    def test_an_invented_media_id_is_refused(self):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf(
                '<figure data-type="media-single">'
                '<div data-type="media" data-id="made-up"></div></figure>'
            )
        self.assertIn("never invent one", str(cm.exception))

    def test_pretty_printed_figure_round_trips(self):
        # A review finding: a hand-authored figure is often written across
        # multiple indented lines, and mediaSingle's content model is
        # block-only (a media node plus an optional caption), so without
        # mediaSingle in BLOCK_ONLY_PARENTS the newline and indentation
        # between </div> and <figcaption> is not formatting, it is a stray
        # text node wedged into the mediaSingle's content list. The compact
        # FIGURE constant above (no whitespace between tags) could not have
        # caught this.
        pretty = (
            '<figure data-type="media-single" data-layout="center" data-width="80">\n'
            '  <div data-type="media" data-media-type="file" data-id="abc-123"\n'
            '       data-collection="contentId-999" data-alt="diagram.svg"></div>\n'
            "  <figcaption>The diagram</figcaption>\n"
            "</figure>\n"
        )
        doc = html_to_adf(pretty)
        self.assertEqual(doc, html_to_adf(self.FIGURE))

    def test_real_world_attrs_survive_layout_widthtype_height_localid(self):
        # A live-site measurement: widthType is on 222/222 sampled
        # mediaSingle nodes and localId on 136/232 media nodes - attrs a
        # minimal hand-authored example never mentions. Dropping them would
        # fail check_roundtrip on nearly every real published figure.
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "mediaSingle",
                 "attrs": {"layout": "align-start", "width": 542,
                           "widthType": "pixel"},
                 "content": [
                     {"type": "media", "attrs": {
                         "type": "file", "id": "abc-123",
                         "collection": "contentId-999",
                         "width": 732, "height": 676,
                         "localId": "b8208ef164ce",
                     }},
                     {"type": "caption", "attrs": {"localId": "1ff11c92d9d0"},
                      "content": [{"type": "text", "text": "As built"}]},
                 ]},
            ],
        }
        ok, differing_type = check_roundtrip(doc)
        self.assertTrue(ok, differing_type)
        html = adf_to_html(doc)
        self.assertNotIn("adf-opaque", html)
        self.assertEqual(html_to_adf(html), doc)

    def test_a_caption_with_no_attrs_stays_that_way(self):
        # The other real shape (a live-site measurement found 16/35
        # sampled captions carry no attrs key at all) - proving the
        # localId case above does not make attrs mandatory.
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "mediaSingle", "attrs": {"layout": "center"},
                 "content": [
                     {"type": "media", "attrs": {
                         "type": "file", "id": "abc-123",
                         "collection": "contentId-999"}},
                     {"type": "caption",
                      "content": [{"type": "text", "text": "As built"}]},
                 ]},
            ],
        }
        roundtripped = html_to_adf(adf_to_html(doc))
        self.assertEqual(roundtripped, doc)
        caption = roundtripped["content"][0]["content"][1]
        self.assertNotIn("attrs", caption)

    def test_real_task_item_localid_survives_the_round_trip_gate(self):
        # Live-proven, not assumed: creating a page with a task
        # list through this converter, then reading it straight back,
        # showed Confluence had assigned every taskItem a real localId -
        # "6ffa3fef-674f-4bd7-b1b6-fc8340fc603e" and its sibling below are
        # shaped like the real ones observed, ids changed - while leaving
        # the enclosing taskList's own localId empty. Before this fix,
        # html_to_adf hardcoded localId: "" for taskList AND taskItem, so
        # update's round-trip gate refused every page with a task list on
        # it: the one thing this converter itself creates and Confluence
        # itself immediately makes un-updatable. This is check_roundtrip
        # itself, not just html_to_adf(adf_to_html(doc)) stability, because
        # that is the exact check confluence-pages.sh update runs before
        # every write.
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "taskList", "attrs": {"localId": ""}, "content": [
                    {"type": "taskItem",
                     "attrs": {"state": "TODO",
                               "localId": "6ffa3fef-674f-4bd7-b1b6-fc8340fc603e"},
                     "content": [{"type": "text", "text": " A real task"}]},
                    {"type": "taskItem",
                     "attrs": {"state": "DONE",
                               "localId": "f653ea70-c56a-4678-a03b-2994bf1c3b78"},
                     "content": [{"type": "text", "text": " A done task"}]},
                ]},
            ],
        }
        ok, differing_type = check_roundtrip(doc)
        self.assertTrue(ok, differing_type)
        self.assertEqual(html_to_adf(adf_to_html(doc)), doc)

    def test_a_task_list_with_no_localid_key_stays_that_way(self):
        # Companion to the caption case above, and to the real-taskItem
        # case before it. An earlier fix carried a present localId value
        # through correctly but still defaulted a
        # MISSING key to "" on the way in, so a node that never had a
        # localId at all - not authored by hand, and not what Confluence
        # itself sends either, but the shape check_roundtrip must not
        # invent structure for - gained one (attrs: {"localId": ""})
        # anyway, on every pass. That still fails the round-trip gate the
        # same way a wrong value would: present-but-empty and absent are
        # different ADF. Review finding, fixed the same way caption
        # already was - by omitting the key (and, for taskList, the whole
        # attrs dict, since it holds nothing else) rather than defaulting
        # it.
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "taskList", "content": [
                    {"type": "taskItem", "attrs": {"state": "TODO"},
                     "content": [{"type": "text", "text": "Plain"}]},
                ]},
            ],
        }
        ok, differing_type = check_roundtrip(doc)
        self.assertTrue(ok, differing_type)
        roundtripped = html_to_adf(adf_to_html(doc))
        self.assertEqual(roundtripped, doc)
        self.assertNotIn("attrs", roundtripped["content"][0])
        self.assertNotIn("localId", roundtripped["content"][0]["content"][0]["attrs"])

    def test_a_decision_list_with_no_localid_key_stays_that_way(self):
        # Same case, decisionItem's sibling family - decisionItem always
        # carries state (like taskItem carries state), decisionList never
        # carries anything but localId (like taskList).
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "decisionList", "content": [
                    {"type": "decisionItem", "attrs": {"state": "DECIDED"},
                     "content": [{"type": "text", "text": "Agreed"}]},
                ]},
            ],
        }
        ok, differing_type = check_roundtrip(doc)
        self.assertTrue(ok, differing_type)
        roundtripped = html_to_adf(adf_to_html(doc))
        self.assertEqual(roundtripped, doc)
        self.assertNotIn("attrs", roundtripped["content"][0])
        self.assertNotIn("localId", roundtripped["content"][0]["content"][0]["attrs"])

    def test_a_non_file_media_node_falls_back_to_opaque_not_a_crash(self):
        # A live-site measurement found the other real media shape:
        # type "external" (a bare url, no id or collection - pasting an
        # external image address rather than uploading a file). Named
        # rendering requires id and collection, so this must fall back to
        # the same opaque passthrough an unrecognised node type gets,
        # rather than a KeyError on a["id"].
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "media", "attrs": {
                    "type": "external", "alt": "chart.png",
                    "url": "https://example.atlassian.net/wiki/download/x.png",
                    "width": 721, "height": 293,
                }},
            ],
        }
        html = adf_to_html(doc)
        self.assertIn('data-type="adf-opaque"', html)
        self.assertEqual(html_to_adf(html), doc)

    def test_a_media_node_missing_collection_falls_back_to_opaque(self):
        # The narrower case of the same rule: type "file" but no
        # collection is not a shape this converter's named renderer can
        # represent either (attachments.sh upload always returns both), so
        # it must not crash on a["collection"].
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "media", "attrs": {"type": "file", "id": "abc-123"}},
            ],
        }
        html = adf_to_html(doc)
        self.assertIn('data-type="adf-opaque"', html)
        self.assertEqual(html_to_adf(html), doc)

    def test_a_figure_around_an_unrenderable_media_still_round_trips(self):
        # The composite of the two cases above: a mediaSingle (always safe
        # to render named) wrapping a media node that is not
        # (the external case). The figure renders named; the media inside
        # it renders opaque; the whole thing still reads back unchanged.
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "mediaSingle", "attrs": {"layout": "center"},
                 "content": [
                     {"type": "media", "attrs": {
                         "type": "external", "url": "https://example.com/x.png"}},
                 ]},
            ],
        }
        html = adf_to_html(doc)
        self.assertTrue(html.startswith('<figure data-type="media-single"'))
        self.assertIn('data-type="adf-opaque"', html)
        self.assertEqual(html_to_adf(html), doc)

    def test_a_media_node_with_occurrencekey_falls_back_to_opaque(self):
        # Review finding on this task: occurrenceKey is a real,
        # schema-documented ADF media attribute this converter's model
        # does not cover. Before the fix, a media node carrying it
        # rendered through the named branch anyway and silently dropped
        # the attribute - round-tripping true under the pre-Task-14 (fully
        # opaque) converter and false under a named renderer that could
        # not fully represent it. It did not show up in the 289-page live
        # sample this task was measured against because none of those
        # nodes happened to carry the attribute - this fixture exists so
        # the gap has a permanent regression test rather than depending on
        # a future live sample to notice it again.
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "media", "attrs": {
                    "type": "file", "id": "abc-123", "collection": "contentId-999",
                    "occurrenceKey": "11111111-1111-1111-1111-111111111111",
                }},
            ],
        }
        ok, differing_type = check_roundtrip(doc)
        self.assertTrue(ok, differing_type)
        html = adf_to_html(doc)
        self.assertIn('data-type="adf-opaque"', html)
        self.assertEqual(html_to_adf(html), doc)

    def test_a_fully_modelled_media_node_still_renders_named(self):
        # The other side of the fix above: falling back on an unmodelled
        # attribute must not become falling back on everything. A media
        # node carrying only the attrs this converter models still gets
        # the readable, authorable <figure> form, not the opaque blob -
        # the fallback narrows exactly to what it needs to, it does not
        # quietly swallow the feature this task exists to add.
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "mediaSingle", "attrs": {"layout": "center"},
                 "content": [
                     {"type": "media", "attrs": {
                         "type": "file", "id": "abc-123",
                         "collection": "contentId-999"}},
                 ]},
            ],
        }
        html = adf_to_html(doc)
        self.assertNotIn("adf-opaque", html)
        self.assertTrue(html.startswith('<figure data-type="media-single"'))
        self.assertEqual(html_to_adf(html), doc)

    def test_a_mediasingle_with_an_unmodelled_attr_falls_back_to_opaque(self):
        # The same rule generalised to mediaSingle, not just media -
        # proven here rather than only claimed, so the guarantee ("named
        # support is never worse than opaque") holds for all three types
        # this task added, not only the one the review finding named.
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "mediaSingle",
                 "attrs": {"layout": "center", "futureAttr": "not yet named"},
                 "content": [
                     {"type": "media", "attrs": {
                         "type": "file", "id": "abc-123",
                         "collection": "contentId-999"}},
                 ]},
            ],
        }
        ok, differing_type = check_roundtrip(doc)
        self.assertTrue(ok, differing_type)
        html = adf_to_html(doc)
        self.assertIn('data-type="adf-opaque"', html)
        self.assertEqual(html_to_adf(html), doc)

    def test_a_caption_with_an_unmodelled_attr_falls_back_to_opaque(self):
        # And the third of the three types this task added.
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "mediaSingle", "attrs": {"layout": "center"},
                 "content": [
                     {"type": "media", "attrs": {
                         "type": "file", "id": "abc-123",
                         "collection": "contentId-999"}},
                     {"type": "caption",
                      "attrs": {"futureAttr": "not yet named"},
                      "content": [{"type": "text", "text": "cap"}]},
                 ]},
            ],
        }
        ok, differing_type = check_roundtrip(doc)
        self.assertTrue(ok, differing_type)
        html = adf_to_html(doc)
        self.assertIn('data-type="adf-opaque"', html)
        self.assertEqual(html_to_adf(html), doc)

    def test_paragraph_cannot_hold_a_figure(self):
        # Mirrors test_paragraph_cannot_hold_a_block_card (Finding
        # 3): a figure is a block, exactly like a table or a panel, and a
        # <figure> written inside a <p> is silently accepted here and then
        # either rejected by the API or mis-rendered - refused instead, the
        # same as every other block wrongly nested inside a paragraph.
        with self.assertRaises(ConversionError) as cm:
            html_to_adf(f"<p>{self.FIGURE}</p>")
        message = str(cm.exception)
        self.assertIn("mediaSingle", message)
        self.assertIn("paragraph", message)

    def test_a_caption_cannot_hold_a_block(self):
        # caption is an inline-content-only container, the same shape as
        # heading/taskItem/decisionItem (FORBIDDEN_CHILDREN's None
        # sentinel) - a block nested inside it is refused rather than
        # silently accepted and then mangled on the next human edit.
        with self.assertRaises(ConversionError) as cm:
            html_to_adf(
                '<figure data-type="media-single">'
                '<div data-type="media" data-id="abc-123" '
                'data-collection="contentId-999"></div>'
                "<figcaption><p>no</p></figcaption></figure>"
            )
        self.assertIn("paragraph", str(cm.exception))
        self.assertIn("caption", str(cm.exception))

    def test_unclosed_media_div_is_rejected(self):
        # Mirrors test_unclosed_opaque_node_raises_rather_than_silently_
        # dropping_content (TestOpaqueHardening): <div data-type="media">
        # is a leaf pushed onto _media_stack rather than builder.blocks, so
        # an unclosed one is invisible to the "len(blocks) > 1" check on
        # its own. Here the enclosing <figure> DOES close, which pops
        # mediaSingle off builder.blocks and brings that check back to
        # balanced (len 1) - so this exercises _media_stack's own guard
        # specifically, not the pre-existing block-stack one: without it,
        # the unclosed media div's "ignore text while it is open" state in
        # handle_data would leak past the fragment's end with no error,
        # silently swallowing every paragraph after the break.
        fragment = (
            "<p>Keep.</p>"
            '<figure data-type="media-single">'
            '<div data-type="media" data-id="abc-123" '
            'data-collection="contentId-999">'
            "</figure>"
            "<p>This prose must survive.</p>"
        )
        with self.assertRaises(ConversionError) as cm:
            html_to_adf(fragment)
        self.assertIn("media", str(cm.exception))


class TestAdfToMarkdown(unittest.TestCase):
    def test_paragraph_and_heading(self):
        doc = html_to_adf("<h2>Title</h2><p>Body.</p>")
        self.assertEqual(adf_to_markdown(doc), "## Title\n\nBody.")

    def test_panel_becomes_a_labelled_blockquote(self):
        doc = html_to_adf('<div data-type="panel-warning"><p>Careful.</p></div>')
        self.assertIn("> **Warning:** Careful.", adf_to_markdown(doc))

    def test_status_becomes_bracketed_text(self):
        doc = html_to_adf(
            '<p><span data-type="status" data-color="green">Built</span></p>'
        )
        self.assertEqual(adf_to_markdown(doc), "[Built]")

    def test_task_list_becomes_gfm_checkboxes(self):
        doc = html_to_adf(
            '<ul data-type="task-list">'
            '<li data-type="task-item"><input type="checkbox"> A</li>'
            '<li data-type="task-item"><input type="checkbox" checked> B</li></ul>'
        )
        md = adf_to_markdown(doc)
        self.assertIn("- [ ] A", md)
        self.assertIn("- [x] B", md)

    def test_code_block_keeps_its_language(self):
        doc = html_to_adf('<pre><code class="language-json">{"a": 1}</code></pre>')
        self.assertEqual(adf_to_markdown(doc), '```json\n{"a": 1}\n```')


class TestReverseCli(unittest.TestCase):
    def _run(self, args, stdin):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "htmlplus.py")] + args,
            input=stdin, capture_output=True, text=True,
        )

    def test_to_html(self):
        adf = json.dumps(html_to_adf("<p>Hi.</p>"))
        r = self._run(["to-html"], adf)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "<p>Hi.</p>")

    def test_to_markdown(self):
        adf = json.dumps(html_to_adf("<h2>T</h2>"))
        r = self._run(["to-markdown"], adf)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "## T")

    def test_check_roundtrip_is_silent_and_exits_0_on_a_clean_document(self):
        # Important 2. confluence-pages.sh's update gate tells "safe to
        # write" from "refuse" by exit code alone - it must not have to
        # parse stdout to find out.
        adf = json.dumps(html_to_adf("<p>Hi.</p>"))
        r = self._run(["check-roundtrip"], adf)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_check_roundtrip_exits_1_naming_the_type_on_a_lossy_document(self):
        # A known type (orderedList) whose "order" attr this converter has
        # no HTML+ syntax for - dropped silently by adf_to_html, so the
        # page round-trips to something different from what was fetched.
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "orderedList", "attrs": {"order": 5}, "content": [
                    {"type": "listItem", "content": [
                        {"type": "paragraph",
                         "content": [{"type": "text", "text": "x"}]},
                    ]},
                ]},
            ],
        }
        r = self._run(["check-roundtrip"], json.dumps(doc))
        self.assertEqual(r.returncode, 1)
        self.assertIn("orderedList", r.stderr)
        self.assertTrue(r.stderr.startswith("Error:"))

    def test_to_html_on_a_null_body_gives_one_line_not_a_traceback(self):
        # confluence-pages.sh read pipes a live page's body straight in with
        # no chance to validate first. check-roundtrip already had a
        # catch-all for exactly this shape of failure; to-html and
        # to-markdown did not.
        r = self._run(["to-html"], "null")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.startswith("Error:"), r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_to_markdown_on_malformed_json_gives_one_line_not_a_traceback(self):
        r = self._run(["to-markdown"], "not json at all")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.startswith("Error:"), r.stderr)
        self.assertNotIn("Traceback", r.stderr)

class TestRoundtripGate(unittest.TestCase):
    """check_roundtrip itself (Important 2) - the function
    confluence-pages.sh's update command gates a write on."""

    def test_differing_type_is_a_real_node_type_not_an_attrs_value(self):
        # Minor 1 (review round two). _first_roundtrip_difference used to
        # adopt a["type"] at any depth, including inside "attrs" - and a
        # subsup mark's own attrs is {"type": "sub"} or {"type": "sup"},
        # an attribute value that happens to share the key name "type"
        # with the thing this function is meant to report. The refusal
        # message promises to carry no page content, but "sub"/"sup" here
        # was never a node type at all - it is this mark's own attrs
        # value. The extra "extraAttr" is not carried by
        # adf_to_html's subsup rendering (only the tag name is), so this
        # mark genuinely fails to round-trip; the type reported for that
        # failure must be "subsup" (the mark's real ADF type), not "sub".
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "H2O",
                     "marks": [{"type": "subsup",
                                "attrs": {"type": "sub", "extraAttr": "keep-me"}}]},
                ]},
            ],
        }
        ok, differing_type = check_roundtrip(doc)
        self.assertFalse(ok)
        self.assertEqual(differing_type, "subsup")

    def test_a_content_or_marks_key_inside_attrs_cannot_manufacture_a_type(self):
        # Minor (review round three). The Minor 1 fix above set is_node
        # from a bare key-name match (key in ("content", "marks")), which
        # closed the subsup case but left a second way in: a literal
        # "content" or "marks" key sitting *inside* an "attrs" value
        # re-armed is_node one level down, regardless of the fact that the
        # walk was already inside attrs and had no business trusting a
        # "type" there at all. This exact fixture, confirmed live,
        # used to return "leak-attempt" - the attacker-chosen
        # value of a "type" key buried in attrs["content"][0] - instead of
        # "node", the actual enclosing ADF node's real type. is_node must
        # stay False for the rest of a subtree once it goes False, however
        # the keys underneath happen to be spelled.
        result = _first_roundtrip_difference(
            {"type": "node", "attrs": {"content": [{"type": "leak-attempt", "x": 1}]}},
            {"type": "node", "attrs": {"content": [{"type": "leak-attempt", "x": 2}]}},
        )
        self.assertEqual(result, "node")

    def test_clean_document_is_ok(self):
        doc = html_to_adf("<p><strong>Hi.</strong></p>")
        ok, differing_type = check_roundtrip(doc)
        self.assertTrue(ok)
        self.assertIsNone(differing_type)

    def test_lossy_known_type_is_not_ok_and_names_its_type(self):
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "orderedList", "attrs": {"order": 5}, "content": [
                    {"type": "listItem", "content": [
                        {"type": "paragraph",
                         "content": [{"type": "text", "text": "x"}]},
                    ]},
                ]},
            ],
        }
        ok, differing_type = check_roundtrip(doc)
        self.assertFalse(ok)
        self.assertEqual(differing_type, "orderedList")

    def test_opaque_passthrough_content_is_ok(self):
        # The gate must not flag passthrough content as unsafe - that is
        # the whole point of opaque passthrough: an unrecognised node or
        # mark is exactly the case this converter now carries through
        # exactly.
        doc = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "extension", "attrs": {
                    "extensionType": "com.atlassian.confluence.macro.core",
                    "extensionKey": "com.example.macro", "parameters": {},
                }},
            ],
        }
        ok, differing_type = check_roundtrip(doc)
        self.assertTrue(ok)
        self.assertIsNone(differing_type)


class TestJiraProfile(unittest.TestCase):
    """Jira's ADF profile is narrower than Confluence's.

    A status lozenge and a decision list are Confluence nodes. Jira renders
    neither, and a description containing one is accepted and then displays as
    nothing at all - the worst kind of failure, because it looks like it
    worked.
    """

    def test_paragraphs_panels_and_code_pass(self):
        doc = html_to_adf_for_jira(
            "<p>Steps.</p>"
            '<div data-type="panel-warning"><p>Careful.</p></div>'
            '<pre><code class="language-bash">ls</code></pre>'
        )
        types = [n["type"] for n in doc["content"]]
        self.assertEqual(types, ["paragraph", "panel", "codeBlock"])

    def test_task_lists_and_tables_pass(self):
        doc = html_to_adf_for_jira(
            '<ul data-type="task-list">'
            '<li data-type="task-item"><input type="checkbox"> A</li></ul>'
            '<table data-width="400"><tbody><tr>'
            '<td data-colwidth="400"><p>x</p></td></tr></tbody></table>'
        )
        self.assertEqual(
            [n["type"] for n in doc["content"]], ["taskList", "table"]
        )

    def test_a_status_lozenge_is_refused(self):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf_for_jira(
                '<p><span data-type="status" data-color="green">Built</span></p>'
            )
        self.assertIn("status", str(cm.exception))
        self.assertIn("Jira", str(cm.exception))

    def test_a_decision_list_is_refused(self):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf_for_jira(
                '<ul data-type="decision-list">'
                '<li data-type="decision-item" data-state="DECIDED">x</li></ul>'
            )
        self.assertIn("decisionList", str(cm.exception))

    def test_a_layout_section_is_refused(self):
        with self.assertRaises(ConversionError) as cm:
            html_to_adf_for_jira(
                '<section data-type="layout-two-equal">'
                '<div data-type="column"><p>L</p></div>'
                '<div data-type="column"><p>R</p></div></section>'
            )
        self.assertIn("layoutSection", str(cm.exception))

    def test_a_status_hidden_in_an_opaque_wrapper_is_still_refused(self):
        # This check runs against the parsed tree html_to_adf returns, not
        # against the HTML+ source, checking CONFLUENCE_ONLY membership. That
        # is only safe if a Confluence-only node smuggled in through the
        # generic <span data-type="adf-opaque"> wrapper comes back out with
        # its real type restored rather than staying "adf-opaque" - proved
        # here rather than assumed. _opaque_to_html builds exactly the wire
        # form _node_to_html/_inline_to_html would have produced for this
        # node had it come from a live page with no named support for it.
        opaque_span = _opaque_to_html(
            {"type": "status", "attrs": {"text": "Built", "color": "green"}},
            tag="span",
        )
        with self.assertRaises(ConversionError) as cm:
            html_to_adf_for_jira(f"<p>{opaque_span}</p>")
        self.assertIn("status", str(cm.exception))
        self.assertIn("Jira", str(cm.exception))

    def test_bodied_extension_is_refused_even_though_it_only_ever_arrives_opaque(self):
        # Unlike status/decisionList/decisionItem/expand/layoutSection/
        # layoutColumn, this converter has no native HTML+ syntax for
        # bodiedExtension at all - so this is the one CONFLUENCE_ONLY member
        # that can only ever reach html_to_adf_for_jira through the opaque
        # wrapper, and the case that actually exercises the passthrough
        # path for real rather than merely by construction.
        opaque_div = _opaque_to_html({
            "type": "bodiedExtension",
            "attrs": {"extensionType": "com.atlassian.confluence.macro.core",
                      "extensionKey": "com.example.macro"},
            "content": [{"type": "paragraph",
                        "content": [{"type": "text", "text": "x"}]}],
        })
        with self.assertRaises(ConversionError) as cm:
            html_to_adf_for_jira(opaque_div)
        self.assertIn("bodiedExtension", str(cm.exception))
        self.assertIn("Jira", str(cm.exception))

    def test_walk_visits_a_nodes_marks_not_just_its_content(self):
        # Structural regression test for _walk itself, independent of what
        # CONFLUENCE_ONLY currently holds. The first version of _walk only
        # descended into "content", never into a text node's "marks" - so a
        # mark's restored type (see _decode_adf and the ADF_OPAQUE_MARK
        # branch) was reachable in the tree but invisible to this walk. A
        # future addition to CONFLUENCE_ONLY would have silently never
        # fired for a mark. annotation has no named HTML+ syntax in this
        # converter (no INLINE_MARKS entry, no dedicated handle_starttag
        # branch), so it can only ever arrive through the opaque wrapper -
        # the same property bodiedExtension has among node types.
        opaque_mark_span = _opaque_mark_to_html(
            {"type": "annotation", "attrs": {"id": "1"}}, "flagged",
        )
        doc = html_to_adf_for_jira(f"<p>{opaque_mark_span}</p>")
        types = {n.get("type") for n in _walk(doc)}
        self.assertIn("annotation", types)

    def test_a_mark_declared_confluence_only_is_refused_even_hidden_opaquely(self):
        # CONFLUENCE_ONLY holds no mark type today - a mark only decorates
        # already-visible text, so nothing here has ever produced the
        # invisible-content failure a missing block node does. This proves
        # the defence works end to end for the day one is added, rather
        # than resting on the structural check above alone: with
        # "annotation" declared refused, exactly the way it can only ever
        # arrive (opaquely - see the previous test), it has to be caught.
        CONFLUENCE_ONLY.add("annotation")
        self.addCleanup(CONFLUENCE_ONLY.discard, "annotation")
        opaque_mark_span = _opaque_mark_to_html(
            {"type": "annotation", "attrs": {"id": "1"}}, "flagged",
        )
        with self.assertRaises(ConversionError) as cm:
            html_to_adf_for_jira(f"<p>{opaque_mark_span}</p>")
        self.assertIn("annotation", str(cm.exception))
        self.assertIn("Jira", str(cm.exception))

    def test_an_opaque_type_jira_can_render_still_passes(self):
        # The refusal is scoped to CONFLUENCE_ONLY, not to opaque
        # passthrough in general - an unrecognised node this converter has
        # no named support for, but that is not in CONFLUENCE_ONLY, still
        # converts. Whether Jira itself renders an "extension" node is a
        # question this task does not answer; the point here is narrower:
        # html_to_adf_for_jira does not refuse it just for being opaque.
        opaque_div = _opaque_to_html({
            "type": "extension",
            "attrs": {"extensionType": "com.atlassian.confluence.macro.core",
                      "extensionKey": "com.example.macro", "parameters": {}},
        })
        doc = html_to_adf_for_jira(opaque_div)
        self.assertEqual(doc["content"][0]["type"], "extension")


if __name__ == "__main__":
    unittest.main()
