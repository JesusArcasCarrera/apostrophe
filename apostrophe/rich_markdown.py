# Copyright (C) 2026 Jesus Arcas
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3, as published
# by the Free Software Foundation.

"""Pure helpers for Apostrophe's rich Markdown editing presentation."""

from dataclasses import dataclass

import regex as re

from apostrophe import markup_regex


@dataclass(frozen=True)
class SyntaxSpan:
    """A Markdown delimiter that can be hidden without changing the source."""

    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class ContentSpan:
    """Semantic content that receives richer presentation in the editor."""

    start: int
    end: int
    kind: str
    level: int = 0


@dataclass(frozen=True)
class RichMarkdownPresentation:
    syntax: tuple[SyntaxSpan, ...]
    content: tuple[ContentSpan, ...]


def parse_presentation(text: str) -> RichMarkdownPresentation:
    """Return source-preserving syntax and semantic ranges for core Markdown."""
    syntax: list[SyntaxSpan] = []
    content: list[ContentSpan] = []
    occupied: list[tuple[int, int]] = []
    code_blocks = [
        (match.start("block"), match.end("block"))
        for match in re.finditer(markup_regex.CODE_BLOCK, text)
    ]

    def inside_code_block(start: int, end: int) -> bool:
        return any(block_start <= start and end <= block_end
                   for block_start, block_end in code_blocks)

    def overlaps_occupied(start: int, end: int) -> bool:
        return any(start < other_end and other_start < end
                   for other_start, other_end in occupied)

    def add_inline(pattern, kind: str) -> None:
        for match in re.finditer(pattern, text):
            start, end = match.span()
            if inside_code_block(start, end) or overlaps_occupied(start, end):
                continue
            text_start, text_end = match.span("text")
            syntax.extend((
                SyntaxSpan(start, text_start, kind),
                SyntaxSpan(text_end, end, kind),
            ))
            content.append(ContentSpan(text_start, text_end, kind))
            occupied.append((start, end))

    # Links and images own their complete range so emphasis inside their label
    # can be added later without accidentally hiding URL punctuation twice.
    for pattern, kind in (
            (markup_regex.IMAGE, "image"),
            (markup_regex.LINK, "link")):
        add_inline(pattern, kind)

    # Prefer the longest delimiter forms before their shorter variants.
    for pattern, kind in (
            (markup_regex.BOLD_ITALIC, "bold-italic"),
            (markup_regex.BOLD, "bold"),
            (markup_regex.STRIKETHROUGH, "strikethrough"),
            (markup_regex.ITALIC_ASTERISK, "italic"),
            (markup_regex.ITALIC_UNDERSCORE, "italic"),
            (markup_regex.CODE, "code"),
            (markup_regex.MATH, "math")):
        add_inline(pattern, kind)

    for match in re.finditer(markup_regex.HEADER, text):
        if inside_code_block(*match.span()):
            continue
        text_start, text_end = match.span("text")
        syntax.append(SyntaxSpan(match.start(), text_start, "heading"))
        content.append(ContentSpan(
            text_start,
            text_end,
            "heading",
            level=len(match.group("level")),
        ))

    for match in re.finditer(markup_regex.BLOCK_QUOTE, text):
        if inside_code_block(*match.span()):
            continue
        text_start, text_end = match.span("text")
        syntax.append(SyntaxSpan(match.start(), text_start, "blockquote"))
        content.append(ContentSpan(text_start, text_end, "blockquote"))

    syntax = [span for span in syntax if span.start < span.end]
    content = [span for span in content if span.start < span.end]
    syntax.sort(key=lambda span: (span.start, span.end, span.kind))
    content.sort(key=lambda span: (span.start, span.end, span.kind))
    return RichMarkdownPresentation(tuple(syntax), tuple(content))


def active_block_ranges(text: str, selection_start: int,
                        selection_end: int) -> tuple[tuple[int, int], ...]:
    """Return non-empty Markdown blocks touched by the cursor or selection."""
    text_length = len(text)
    start = max(0, min(selection_start, text_length))
    end = max(0, min(selection_end, text_length))
    if start > end:
        start, end = end, start

    blocks = tuple(_nonempty_blocks(text))
    if not blocks:
        return ((0, text_length),)

    # A collapsed cursor at a block boundary belongs to the following block,
    # unless it sits at the very end of the document.
    cursor_end = end if end > start else min(start + 1, text_length)
    active = tuple(
        block for block in blocks
        if start < block[1] and cursor_end > block[0]
    )
    if active:
        return active

    nearest = min(blocks, key=lambda block: min(
        abs(start - block[0]), abs(start - block[1])))
    return (nearest,)


def span_touches_ranges(start: int, end: int,
                        ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(start < range_end and range_start < end
               for range_start, range_end in ranges)


def _nonempty_blocks(text: str):
    block_start = None
    offset = 0
    for line in text.splitlines(keepends=True):
        line_end = offset + len(line)
        if line.strip():
            if block_start is None:
                block_start = offset
        elif block_start is not None:
            yield block_start, offset
            block_start = None
        offset = line_end

    if block_start is not None:
        yield block_start, len(text)
