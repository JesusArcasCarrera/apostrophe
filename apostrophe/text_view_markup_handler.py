# Copyright (C) 2022, Manuel Genovés <manuel.genoves@gmail.com>
#               2019, Gonçalo Silva
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3, as published
# by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranties of
# MERCHANTABILITY, SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR
# PURPOSE.  See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program.  If not, see <http://www.gnu.org/licenses/>.
# END LICENSE

from multiprocessing import Pipe, Process

import gi
from gi.repository import GLib, Gtk, Pango

from apostrophe import helpers
from apostrophe.markup_worker import run_markup_worker
from apostrophe.rich_markdown import active_block_ranges, span_touches_ranges

gi.require_version('Gtk', '4.0')


class MarkupHandler:
    TAG_NAME_ITALIC = 'italic'
    TAG_NAME_BOLD = 'bold'
    TAG_NAME_BOLD_ITALIC = 'bold_italic'
    TAG_NAME_STRIKETHROUGH = 'strikethrough'
    TAG_NAME_CENTER = 'center'
    TAG_NAME_WRAP_NONE = 'wrap_none'
    TAG_NAME_PLAIN_TEXT = 'plain_text'
    TAG_NAME_GRAY_TEXT = 'gray_text'
    TAG_NAME_LINK_COLOR_TEXT = 'link_color_text'
    TAG_NAME_CODE_TEXT = 'code_text'
    TAG_NAME_CODE_BLOCK = 'code_block'
    TAG_NAME_UNFOCUSED_TEXT = 'unfocused_text'
    TAG_NAME_MARGIN_INDENT = 'margin_indent'
    TAG_NAME_RICH_SYNTAX = 'rich_syntax'
    TAG_NAME_RICH_HEADING = 'rich_heading'
    TAG_NAME_RICH_LINK = 'rich_link'
    TAG_NAME_RICH_QUOTE = 'rich_quote'

    def __init__(self, textview):
        self.textview = textview
        self.text_buffer = self.textview.get_buffer()
        self.marked_up_text = None
        self.last_result = []

        # Tags.
        buffer = self.text_buffer

        self.tag_italic = buffer.create_tag(self.TAG_NAME_ITALIC,
                                            weight=Pango.Weight.NORMAL,
                                            style=Pango.Style.ITALIC)

        self.tag_bold = buffer.create_tag(self.TAG_NAME_BOLD,
                                          weight=Pango.Weight.BOLD,
                                          style=Pango.Style.NORMAL)

        self.tag_bold_italic = buffer.create_tag(self.TAG_NAME_BOLD_ITALIC,
                                                 weight=Pango.Weight.BOLD,
                                                 style=Pango.Style.ITALIC)

        self.tag_strikethrough = buffer.create_tag(self.TAG_NAME_STRIKETHROUGH,
                                                   strikethrough=True)

        self.tag_center = buffer.create_tag(self.TAG_NAME_CENTER,
                                            justification=Gtk.Justification.CENTER)

        self.tag_wrap_none = buffer.create_tag(self.TAG_NAME_WRAP_NONE,
                                               wrap_mode=Gtk.WrapMode.NONE,
                                               pixels_above_lines=0,
                                               pixels_below_lines=0)

        self.tag_plain_text = buffer.create_tag(self.TAG_NAME_PLAIN_TEXT,
                                                weight=Pango.Weight.NORMAL,
                                                style=Pango.Style.NORMAL,
                                                strikethrough=False,
                                                justification=Gtk.Justification.LEFT)

        self.tag_gray_text = buffer.create_tag(self.TAG_NAME_GRAY_TEXT,
                                               foreground='gray',
                                               weight=Pango.Weight.NORMAL,
                                               style=Pango.Style.NORMAL)

        self.tag_link_color_text = buffer.create_tag(self.TAG_NAME_LINK_COLOR_TEXT,
                                               foreground='lightblue',
                                               weight=Pango.Weight.NORMAL,
                                               style=Pango.Style.ITALIC)

        self.tag_code_text = buffer.create_tag(self.TAG_NAME_CODE_TEXT,
                                               weight=Pango.Weight.NORMAL,
                                               style=Pango.Style.NORMAL,
                                               strikethrough=False)

        self.tag_code_block = buffer.create_tag(self.TAG_NAME_CODE_BLOCK,
                                                weight=Pango.Weight.NORMAL,
                                                style=Pango.Style.NORMAL,
                                                strikethrough=False,
                                                indent=self.get_margin_indent(0, 1)[1])

        self.tag_rich_syntax_hidden = buffer.create_tag(
            'rich_syntax_hidden', invisible=True)
        self.tag_rich_syntax_revealed = buffer.create_tag(
            'rich_syntax_revealed', foreground='gray')
        self.tag_rich_link = buffer.create_tag(
            self.TAG_NAME_RICH_LINK, underline=Pango.Underline.SINGLE)
        self.tag_rich_quote = buffer.create_tag(
            self.TAG_NAME_RICH_QUOTE, style=Pango.Style.ITALIC)
        self.tags_rich_headings = {}

        self.tags_markup = {
            self.TAG_NAME_ITALIC: lambda args: self.tag_italic,
            self.TAG_NAME_BOLD: lambda args: self.tag_bold,
            self.TAG_NAME_BOLD_ITALIC: lambda args: self.tag_bold_italic,
            self.TAG_NAME_STRIKETHROUGH: lambda args: self.tag_strikethrough,
            self.TAG_NAME_CENTER: lambda args: self.tag_center,
            self.TAG_NAME_WRAP_NONE: lambda args: self.tag_wrap_none,
            self.TAG_NAME_PLAIN_TEXT: lambda args: self.tag_plain_text,
            self.TAG_NAME_GRAY_TEXT: lambda args: self.tag_gray_text,
            self.TAG_NAME_LINK_COLOR_TEXT: lambda args: self.tag_link_color_text,
            self.TAG_NAME_CODE_TEXT: lambda args: self.tag_code_text,
            self.TAG_NAME_CODE_BLOCK: lambda args: self.tag_code_block,
            self.TAG_NAME_MARGIN_INDENT: lambda args: self.get_margin_indent_tag(
                *args)
        }

        # Focus mode.
        self.tag_unfocused_text = buffer.create_tag(self.TAG_NAME_UNFOCUSED_TEXT,
                                                    foreground='gray',
                                                    weight=Pango.Weight.NORMAL,
                                                    style=Pango.Style.NORMAL)

        # Margin and indents.
        # A baseline margin is set to allow negative offsets for formatting
        # headers, lists, etc.
        self.tags_margins_indents = {}
        self.baseline_margin = 0
        self.char_width = 0
        self.update_margins_indents()

        # Style.
        self.on_style_updated()

        # Worker process to handle parsing.
        self.parsing = False
        self.apply_pending = False
        self.parent_conn, child_conn = Pipe()
        Process(target=run_markup_worker,
                args=(child_conn,), daemon=True).start()
        GLib.io_add_watch(
            self.parent_conn.fileno(),
            GLib.PRIORITY_DEFAULT,
            GLib.IO_IN,
            self.on_parsed)

    def on_style_updated(self, *_):
        style_context = self.textview.get_style_context()
        (found, color) = style_context.lookup_color('code_bg_color')
        if not found:
            (_, color) = style_context.lookup_color('background_color')
        self.tag_code_text.set_property("background", color.to_string())
        self.tag_code_block.set_property(
            "paragraph-background", color.to_string())
        (found, color) = style_context.lookup_color('link_fg_color')
        if not found:
            (_, color) = style_context.lookup_color('lightblue')
        self.tag_link_color_text.set_property("foreground", color.to_string())
        self.tag_rich_link.set_property("foreground", color.to_string())

    def apply(self):
        """Applies markup, parsing it in a worker process
        if the text has changed.

        In case parsing is already running, it will re-apply once it finishes.
        This ensure that the pipe doesn't fill (and block) if multiple requests
        are made in quick succession."""

        if not self.parsing:
            self.parsing = True
            self.apply_pending = False

            text = self.text_buffer.get_slice(
                self.text_buffer.get_start_iter(),
                self.text_buffer.get_end_iter(),
                True)
            if text != self.marked_up_text:
                self.parent_conn.send(text)
            else:
                self.do_apply(text)
                # No worker reply will arrive to clear the fast-path state.
                self.parsing = False
        else:
            self.apply_pending = True

    def on_parsed(self, _source, _condition):
        """Reads the parsing result from the pipe
        and triggers any pending apply."""

        try:
            if self.parent_conn.poll():
                self.do_apply(*self.parent_conn.recv())
            return True
        except EOFError:
            return False
        finally:
            self.parsing = False
            if self.apply_pending:
                self.apply()


    def do_apply(self, original_text, result=None):
        """Applies the result of parsing if the current text
        matches the original text."""

        buffer = self.text_buffer
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        text = self.text_buffer.get_slice(start, end, True)

        if result is None:
            result = self.last_result

        # Apply markup tags. Presentation syntax is reapplied even when only
        # the cursor moved, because the active block must reveal its source.
        if text == original_text:
            buffer.remove_tag(self.tag_italic, start, end)
            buffer.remove_tag(self.tag_bold, start, end)
            buffer.remove_tag(self.tag_bold_italic, start, end)
            buffer.remove_tag(self.tag_strikethrough, start, end)
            buffer.remove_tag(self.tag_center, start, end)
            buffer.remove_tag(self.tag_plain_text, start, end)
            buffer.remove_tag(self.tag_gray_text, start, end)
            buffer.remove_tag(self.tag_code_text, start, end)
            buffer.remove_tag(self.tag_code_block, start, end)
            buffer.remove_tag(self.tag_wrap_none, start, end)
            buffer.remove_tag(self.tag_rich_syntax_hidden, start, end)
            buffer.remove_tag(self.tag_rich_syntax_revealed, start, end)
            buffer.remove_tag(self.tag_rich_link, start, end)
            buffer.remove_tag(self.tag_rich_quote, start, end)
            for tag in self.tags_rich_headings.values():
                buffer.remove_tag(tag, start, end)
            for tag in self.tags_margins_indents.values():
                buffer.remove_tag(tag, start, end)

            selection_start, selection_end = self._selection_offsets()
            active_ranges = active_block_ranges(
                text, selection_start, selection_end)

            for tag_name, tag_args, tag_start, tag_end in result:
                if tag_name == self.TAG_NAME_RICH_SYNTAX:
                    if not self.textview.rich_editing:
                        continue
                    tag = (
                        self.tag_rich_syntax_revealed
                        if span_touches_ranges(tag_start, tag_end, active_ranges)
                        else self.tag_rich_syntax_hidden
                    )
                elif tag_name == self.TAG_NAME_RICH_HEADING:
                    if not self.textview.rich_editing:
                        continue
                    tag = self.get_rich_heading_tag(*tag_args)
                elif tag_name == self.TAG_NAME_RICH_LINK:
                    if not self.textview.rich_editing:
                        continue
                    tag = self.tag_rich_link
                elif tag_name == self.TAG_NAME_RICH_QUOTE:
                    if not self.textview.rich_editing:
                        continue
                    tag = self.tag_rich_quote
                else:
                    tag = self.tags_markup[tag_name](tag_args)
                buffer.apply_tag(
                    tag,
                    buffer.get_iter_at_offset(tag_start),
                    buffer.get_iter_at_offset(tag_end))

            self.marked_up_text = text
            self.last_result = result

        # Apply focus mode tag (grey out before/after current sentence).
        buffer.remove_tag(self.tag_unfocused_text, start, end)
        if self.textview.focus_mode:
            start_sentence, end_sentence = buffer.get_current_sentence_bounds()
            buffer.apply_tag(self.tag_unfocused_text, start, start_sentence)
            buffer.apply_tag(self.tag_unfocused_text, end_sentence, end)

    # Margin and indent are cumulative. They differ in two ways:
    # * Margin is always in the beginning,
    # which means it effectively only affects the first line
    # of multi-line text. Indent is applied to every line.
    # * Margin level can be negative, as a baseline margin exists
    # from which it can be subtracted.
    # Indent is always positive, or 0.
    def get_margin_indent_tag(self, margin_level, indent_level):
        level = (margin_level, indent_level)
        if level not in self.tags_margins_indents:
            margin, indent = self.get_margin_indent(margin_level, indent_level)
            tag = self.text_buffer.create_tag(
                "margin_indent_{}_{}".format(margin_level, indent_level),
                left_margin=margin, indent=indent)
            self.tags_margins_indents[level] = tag
            return tag
        else:
            return self.tags_margins_indents[level]

    def get_rich_heading_tag(self, level):
        if level not in self.tags_rich_headings:
            scales = (1.55, 1.35, 1.2, 1.1, 1.0, 0.95)
            self.tags_rich_headings[level] = self.text_buffer.create_tag(
                "rich_heading_{}".format(level),
                scale=scales[max(1, min(level, 6)) - 1],
                weight=Pango.Weight.BOLD,
                pixels_above_lines=8 if level <= 2 else 4,
                pixels_below_lines=4)
        return self.tags_rich_headings[level]

    def _selection_offsets(self):
        if self.text_buffer.get_has_selection():
            start, end = self.text_buffer.get_selection_bounds()
        else:
            start = self.text_buffer.get_iter_at_mark(
                self.text_buffer.get_insert())
            end = start
        return start.get_offset(), end.get_offset()

    def get_margin_indent(self, margin_level, indent_level,
                          baseline_margin=None, char_width=None):
        if baseline_margin is None:
            baseline_margin = self.textview.get_left_margin()
        if char_width is None:
            char_width = helpers.get_char_width(self.textview)
        margin = max(baseline_margin + char_width * margin_level, 0)
        indent = char_width * indent_level
        return margin, indent

    def update_margins_indents(self):
        baseline_margin = self.textview.get_left_margin()
        char_width = helpers.get_char_width(self.textview)

        # Bail out if neither the baseline margin nor character width change
        if baseline_margin == self.baseline_margin and char_width == self.char_width:
            return
        self.baseline_margin = baseline_margin
        self.char_width = char_width

        # Adjust tab size
        tab_array = Pango.TabArray.new(1, True)
        tab_array.set_tab(0, Pango.TabAlign.LEFT, 4 * char_width)
        self.textview.set_tabs(tab_array)

        # Adjust margins and indents
        for level, tag in self.tags_margins_indents.items():
            margin, indent = self.get_margin_indent(
                *level, baseline_margin, char_width)
            tag.set_properties(left_margin=margin, indent=indent)

    def stop(self, *_):
        self.parent_conn.close()
