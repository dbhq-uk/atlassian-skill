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


if __name__ == "__main__":
    unittest.main()
