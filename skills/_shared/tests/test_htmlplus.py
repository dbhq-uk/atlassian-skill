import json
import pathlib
import subprocess
import sys
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from htmlplus import ConversionError, html_to_adf  # noqa: E402
from htmlplus import adf_to_html, adf_to_markdown  # noqa: E402


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

    # -- Finding 3 (review, Task 7): a block/embed card nested inside a
    # paragraph had no FORBIDDEN_CHILDREN rule, so html_to_adf accepted it,
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
        # Finding 2 (review, Task 7): the emitter applied marks[0]
        # innermost, the opposite of how html_to_adf stores them (outer to
        # inner, in tag-open order), so bold-inside-em became em-inside-bold
        # on every pass. Asserting the exact string, not just round-trip
        # stability, so a future regression fails with a readable diff
        # rather than a bare ADF inequality.
        doc = html_to_adf("<p><strong><em>both</em></strong></p>")
        self.assertEqual(adf_to_html(doc), "<p><strong><em>both</em></strong></p>")


class TestUnsupportedAdfNode(unittest.TestCase):
    """Finding 1 (review, Task 7): a node type this converter cannot render.

    A real fetched Confluence page can carry node types this converter has
    no HTML+ for - media, mention, emoji, extension and more. There is no
    HTML+ syntax to construct one through html_to_adf, so these ADF
    documents are built directly, the way a page fetched from the API would
    arrive.
    """

    UNSUPPORTED_DOC = {
        "type": "doc", "version": 1,
        "content": [
            {"type": "paragraph",
             "content": [{"type": "text", "text": "Before."}]},
            {"type": "mediaSingle", "attrs": {"layout": "center"},
             "content": [{"type": "media",
                          "attrs": {"type": "file", "id": "abc123",
                                    "collection": "x"}}]},
            {"type": "paragraph",
             "content": [{"type": "text", "text": "After."}]},
        ],
    }

    def test_adf_to_html_refuses_rather_than_drop_the_node(self):
        # It feeds the write path (fetch, splice, verify, write back), so
        # silently dropping content there is the one outcome worse than a
        # hard failure - refusing means the page is never overwritten with
        # data missing.
        with self.assertRaises(ConversionError) as cm:
            adf_to_html(self.UNSUPPORTED_DOC)
        self.assertIn("mediaSingle", str(cm.exception))

    def test_adf_to_markdown_marks_the_gap_instead_of_raising(self):
        # Documented one-way and read-only, never written back, so there is
        # no unsafe overwrite to guard against - a visible placeholder beats
        # a hard failure for an agent that is only trying to read the page.
        md = adf_to_markdown(self.UNSUPPORTED_DOC)
        self.assertIn("Before.", md)
        self.assertIn("[unsupported node: mediaSingle]", md)
        self.assertIn("After.", md)


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


if __name__ == "__main__":
    unittest.main()
