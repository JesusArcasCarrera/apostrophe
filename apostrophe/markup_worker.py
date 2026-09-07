# Copyright (C) 2026 Jesus Arcas
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3, as published
# by the Free Software Foundation.

"""Markdown parsing that is safe to import in a worker process."""

import regex as re

from apostrophe import markup_regex
from apostrophe.markup_regex import (BLOCK_QUOTE, BOLD, BOLD_ITALIC, CODE,
                                     HEADER, HEADER_UNDER, HORIZONTAL_RULE,
                                     IMAGE, ITALIC_ASTERISK, ITALIC_UNDERSCORE,
                                     LINK, LINK_ALT, MATH, STRIKETHROUGH, TABLE)
from apostrophe.rich_markdown import parse_presentation


def parse_markup(text):
    """Return markup tag ranges without importing GTK or application state."""
    result = []

    code_blocks = []
    for match in re.finditer(markup_regex.CODE_BLOCK, text):
        start, end = match.start("block"), match.end("block")
        result.append(("code_block", (), start, end))
        code_blocks.append((start, end))

    def inside_code_blocks(start, end):
        return any(block_start < start < block_end or
                   block_start < end < block_end
                   for block_start, block_end in code_blocks)

    def match_inside_code_blocks(match):
        return inside_code_blocks(match.start(), match.end())

    regexps = (
        (ITALIC_ASTERISK, "italic"),
        (ITALIC_UNDERSCORE, "italic"),
        (BOLD, "bold"),
        (BOLD_ITALIC, "bold_italic"),
        (STRIKETHROUGH, "strikethrough"),
        (CODE, "code_text"),
        (MATH, "code_text"),
        (TABLE, "wrap_none"),
    )
    for regexp, tag_name in regexps:
        for match in re.finditer(regexp, text):
            if not match_inside_code_blocks(match):
                result.append((tag_name, (), match.start(), match.end()))

    for regexp, tag_name in (
            (LINK, "link_color_text"),
            (IMAGE, "gray_text")):
        for match in re.finditer(regexp, text):
            if match_inside_code_blocks(match):
                continue
            result.append(
                (tag_name, (), match.start(), match.start("text")))
            result.append((tag_name, (), match.end("text"), match.end()))

    for match in re.finditer(LINK_ALT, text):
        if not match_inside_code_blocks(match):
            result.append(("gray_text", (),
                           match.start("text"), match.end("text")))

    for match in re.finditer(HORIZONTAL_RULE, text):
        if not match_inside_code_blocks(match):
            result.append(("center", (),
                           match.start("symbols"), match.end("symbols")))

    for match in re.finditer(BLOCK_QUOTE, text):
        if not match_inside_code_blocks(match):
            result.append(("margin_indent", (2, -2),
                           match.start(), match.end()))

    for match in re.finditer(HEADER, text):
        if match_inside_code_blocks(match):
            continue
        margin = -len(match.group("level")) - 1
        result.append(("margin_indent", (margin, 0),
                       match.start(), match.end()))
        result.append(("bold", (), match.start(), match.end()))

    presentation = parse_presentation(text)
    for span in presentation.syntax:
        result.append(("rich_syntax", (span.kind,), span.start, span.end))
    for span in presentation.content:
        if span.kind == "heading":
            result.append(("rich_heading", (span.level,),
                           span.start, span.end))
        elif span.kind == "link":
            result.append(("rich_link", (), span.start, span.end))
        elif span.kind == "blockquote":
            result.append(("rich_quote", (), span.start, span.end))

    for match in re.finditer(HEADER_UNDER, text):
        if match_inside_code_blocks(match):
            continue
        frontmatter = re.search(markup_regex.FRONTMATTER, text)
        if frontmatter and match.start() <= frontmatter.end():
            continue
        result.append(("bold", (), match.start(), match.end()))

    return result


def run_markup_worker(child_conn):
    """Parse the newest text received over a multiprocessing connection."""
    while True:
        while True:
            try:
                text = child_conn.recv()
                if not child_conn.poll():
                    break
            except EOFError:
                child_conn.close()
                return

        child_conn.send((text, parse_markup(text)))
