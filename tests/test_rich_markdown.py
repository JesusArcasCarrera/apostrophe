#!/usr/bin/env python

import unittest

from apostrophe.rich_markdown import (
    active_block_ranges,
    parse_presentation,
    span_touches_ranges,
)
from apostrophe.markup_worker import parse_markup


class RichMarkdownPresentationTest(unittest.TestCase):
    def test_core_inline_markup_preserves_content_ranges(self):
        text = "A **bold** and *soft* [link](https://example.com)."
        presentation = parse_presentation(text)

        hidden = [text[span.start:span.end] for span in presentation.syntax]
        content = {
            span.kind: text[span.start:span.end]
            for span in presentation.content
        }
        self.assertEqual(hidden, ["**", "**", "*", "*", "[", "](https://example.com)"])
        self.assertEqual(content["bold"], "bold")
        self.assertEqual(content["italic"], "soft")
        self.assertEqual(content["link"], "link")

    def test_heading_and_quote_prefixes_are_separate_from_content(self):
        text = "## Heading\n\n> A quote"
        presentation = parse_presentation(text)

        hidden = [(span.kind, text[span.start:span.end])
                  for span in presentation.syntax]
        content = [(span.kind, span.level, text[span.start:span.end])
                   for span in presentation.content]
        self.assertEqual(hidden, [("heading", "## "), ("blockquote", "> ")])
        self.assertEqual(content, [
            ("heading", 2, "Heading"),
            ("blockquote", 0, "A quote"),
        ])

    def test_code_block_contents_are_not_interpreted_as_inline_markup(self):
        text = "```markdown\n**literal**\n```\n\n**rendered**"
        presentation = parse_presentation(text)

        bold = [span for span in presentation.content if span.kind == "bold"]
        self.assertEqual(len(bold), 1)
        self.assertEqual(text[bold[0].start:bold[0].end], "rendered")

    def test_active_blocks_follow_cursor_and_multiblock_selection(self):
        text = "First **block**\ncontinues\n\nSecond *block*"
        first = active_block_ranges(text, 8, 8)
        second = active_block_ranges(text, len(text), len(text))
        both = active_block_ranges(text, 8, len(text))
        separator = text.index("\n\n")
        second_start = separator + 2

        self.assertEqual(first, ((0, separator + 1),))
        self.assertEqual(second, ((second_start, len(text)),))
        self.assertEqual(both, ((0, separator + 1), (second_start, len(text))))
        self.assertTrue(span_touches_ranges(6, 15, first))
        self.assertFalse(span_touches_ranges(34, 41, first))

    def test_worker_parser_has_no_graphical_dependency(self):
        text = "# Title\n\nA **bold** [link](https://example.com)."
        result = parse_markup(text)
        tag_names = {item[0] for item in result}

        self.assertIn("rich_heading", tag_names)
        self.assertIn("rich_syntax", tag_names)
        self.assertIn("rich_link", tag_names)


if __name__ == "__main__":
    unittest.main()
