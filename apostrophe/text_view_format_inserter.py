import regex as re
import urllib
from gettext import gettext as _

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk

from apostrophe.helpers import user_action
from apostrophe.markup_regex import CHECKLIST, HEADER, LIST, ORDERED_LIST, URL
from apostrophe.table_input import TableInput
from apostrophe.settings import Settings


class FormatInserter:
    """Manages insertion of formatting.

    Methods can be called directly, as well as be used as signal callbacks."""

    def insert_italic(self, action, data, text_view):
        """Insert italic or mark a selection as bold"""

        self.__wrap(text_view, "_", _("italic text"))

    def insert_bold(self, action, data, text_view):
        """Insert bold or mark a selection as bold"""

        self.__wrap(text_view, "**", _("bold text"))

    def insert_strikethrough(self, action, data, text_view):
        """Insert strikethrough or mark a selection as strikethrough"""

        self.__wrap(text_view, "~~", _("strikethrough text"))

    def insert_horizontal_rule(self, action, data, text_view):
        """Insert horizontal rule"""

        text_buffer = text_view.get_buffer()
        with user_action(text_buffer):
            text_buffer.insert_at_cursor("\n\n---\n")
        text_view.scroll_mark_onscreen(text_buffer.get_insert())

    def insert_list_item(self, action, data, text_view):
        """Insert list item or mark a selection as list item"""

        text_buffer = text_view.get_buffer()

        helptext = _("Item")

        cursor_mark = text_buffer.get_insert()
        cursor_iter = text_buffer.get_iter_at_mark(cursor_mark)

        with user_action(text_buffer):
            selection_length = 0
            empty_line = False

            # get text of the current line
            (start, end) = text_buffer.get_current_line_bounds()
            text = text_buffer.get_text(start, end, True)

            if text_buffer.get_has_selection():
                # if the current line has a list item we'll treat it entirely no matter what's really selected
                if match:=re.match(LIST, text):
                    selection_length = len(match.group("text") or "")
                else:
                    # otherwise use the current selection
                    (start, end) = text_buffer.get_selection_bounds()
                    text = text_buffer.get_text(start, end, True)
                    selection_length = len(text)
                text_buffer.move_mark(cursor_mark, end)

            # let's check if it's an empty line
            if cursor_iter.starts_line() and cursor_iter.ends_line():
                empty_line = True
                selection_length = len(helptext)

            # are we in the beginning of a line?
            if start.starts_line():
                
                text = text_buffer.get_text(start, end, True)
                # check whether we have a list already to remove it
                if match:=re.match(LIST, text):
                    delete_end = start.copy()
                    delete_end.forward_chars(len(match.group("content")) - len(match.group("text") or ""))
                    text_buffer.delete(start, delete_end)
                else:
                    # check whether the previous line has items
                    start_previous_line = start.copy()
                    start_previous_line.backward_lines(1)
                    previous_line = text_buffer.get_text(start_previous_line, start, True)
                    match = re.match(LIST, previous_line)

                    symbol = "-"
                    indent = ""
                    if match:
                        symbol = match.group("symbol")
                        indent = match.group("indent")
                    else:
                        # otherwise check if the second previous line is blank or not
                        start_previous_line.backward_lines(1)
                        previous_line = text_buffer.get_text(start_previous_line, start, True)
                        if not previous_line:
                            indent = "\n"
                    text_buffer.insert(start, f"{indent}{symbol} {helptext if empty_line else ''}")
            # if we're not at the beginning of the line we just insert a newline and the item symbol
            else:
                text_buffer.insert(start, "\n\n- ")

        self.__select_text(text_view, 0, selection_length)

    def insert_checklist_item(self, action, data, text_view):
        """Insert checklist item or mark a selection as checklist item"""

        text_buffer = text_view.get_buffer()

        helptext = _("Item")

        cursor_mark = text_buffer.get_insert()
        cursor_iter = text_buffer.get_iter_at_mark(cursor_mark)

        with user_action(text_buffer):
            selection_length = 0
            empty_line = False

            # get text of the current line
            (start, end) = text_buffer.get_current_line_bounds()
            text = text_buffer.get_text(start, end, True)

            if text_buffer.get_has_selection():
                # if the current line has a checklist item we'll treat it entirely no matter what's really selected
                if match:=re.match(CHECKLIST, text):
                    selection_length = len(match.group("text") or "")
                else:
                    # otherwise use the current selection
                    (start, end) = text_buffer.get_selection_bounds()
                    text = text_buffer.get_text(start, end, True)
                    selection_length = len(text)
                text_buffer.move_mark(cursor_mark, end)

            # let's check if it's an empty line
            if cursor_iter.starts_line() and cursor_iter.ends_line():
                empty_line = True
                selection_length = len(helptext)

            # are we in the beginning of a line?
            if start.starts_line():
                
                text = text_buffer.get_text(start, end, True)
                value = " "
                # check whether we have a list already to toggle it
                if match:=re.match(CHECKLIST, text):
                    delete_end = start.copy()
                    delete_end.forward_chars(len(match.group("content")) - len(match.group("text") or ""))
                    value = "x" if match.group("check") == " " else " "
                    text_buffer.delete(start, delete_end)

                # check whether the previous line has items
                start_previous_line = start.copy()
                start_previous_line.backward_lines(1)
                previous_line = text_buffer.get_text(start_previous_line, start, True)
                match = re.match(CHECKLIST, previous_line)

                symbol = "-"
                indent = ""
                if match:
                    symbol = match.group("symbol")
                    indent = match.group("indent")
                else:
                    # otherwise check if the second previous line is blank or not
                    start_previous_line.backward_lines(1)
                    previous_line = text_buffer.get_text(start_previous_line, start, True)
                    if not previous_line:
                        indent = "\n"
                text_buffer.insert(start, f"{indent}{symbol} [{value}] {helptext if empty_line else ''}")
            # if we're not at the beginning of the line we just insert a newline and the item symbol
            else:
                text_buffer.insert(start, "\n\n- [ ] ")

        self.__select_text(text_view, 0, selection_length)

    def insert_ordered_list_item(self, action, data, text_view):
        """Insert ordered item or mark a selection as an ordered list item"""

        text_buffer = text_view.get_buffer()

        helptext = _("Item")

        cursor_mark = text_buffer.get_insert()
        cursor_iter = text_buffer.get_iter_at_mark(cursor_mark)

        with user_action(text_buffer):
            selection_length = 0
            empty_line = False

            # get text of the current line
            (start, end) = text_buffer.get_current_line_bounds()
            text = text_buffer.get_text(start, end, True)

            if text_buffer.get_has_selection():
                # if the current line has a list item we'll treat it entirely no matter what's really selected
                if match:=re.match(ORDERED_LIST, text):
                    selection_length = len(match.group("text") or "")
                else:
                    # otherwise use the current selection
                    (start, end) = text_buffer.get_selection_bounds()
                    text = text_buffer.get_text(start, end, True)
                    selection_length = len(text)
                text_buffer.move_mark(cursor_mark, end)

            # let's check if it's an empty line
            if cursor_iter.starts_line() and cursor_iter.ends_line():
                empty_line = True
                selection_length = len(helptext)

            # are we in the beginning of a line?
            if start.starts_line():
                
                text = text_buffer.get_text(start, end, True)
                # check whether we have a numeration already to remove it
                if match:=re.match(ORDERED_LIST, text):
                    delete_end = start.copy()
                    delete_end.forward_chars(len(match.group("content")) - len(match.group("text") or ""))
                    text_buffer.delete(start, delete_end)
                else:
                    # check whether the previous line is numbered
                    start_previous_line = start.copy()
                    start_previous_line.backward_lines(1)
                    previous_line = text_buffer.get_text(start_previous_line, start, True)
                    match = re.match(ORDERED_LIST, previous_line)

                    number = 1
                    delimiter = "."
                    indent = ""
                    if match:
                        number = int(match.group("number")) + 1
                        delimiter = match.group("delimiter")
                        indent = match.group("indent")
                    else:
                        # otherwise check if the second previous line is blank or not
                        start_previous_line.backward_lines(1)
                        previous_line = text_buffer.get_text(start_previous_line, start, True)
                        if not previous_line:
                            indent = "\n"
                    text_buffer.insert(start, f"{indent}{number}{delimiter} {helptext if empty_line else ''}")
            # if we're not at the beginning of the line we just insert a newline and the numbering
            else:
                text_buffer.insert(start, "\n\n1. ")

        self.__select_text(text_view, 0, selection_length)

    def insert_header(self, action, level, text_view):
        """Insert header or mark a selection as a list header"""
        level = 1 if not level else level.get_int32()

        text_buffer = text_view.get_buffer()

        helptext = _("Header")

        cursor_mark = text_buffer.get_insert()
        cursor_iter = text_buffer.get_iter_at_mark(cursor_mark)

        with user_action(text_buffer):
            selection_length = 0
            indent= ""
            next_level = 1
            empty_line = False

            # get text of the current line
            (start, end) = text_buffer.get_current_line_bounds()
            text = text_buffer.get_text(start, end, True)

            if text_buffer.get_has_selection():
                # if the current line has a heading we'll treat it entirely no matter what's really selected
                if match:=re.match(HEADER, text):
                    selection_length = len(match.group("text") or "")
                else:
                    # otherwise we'll just use the selected text
                    (start, end) = text_buffer.get_selection_bounds()
                    text = text_buffer.get_text(start, end, True)
                    selection_length = len(text)
                text_buffer.move_mark(cursor_mark, end)

            # let's check if it's an empty line
            if cursor_iter.starts_line() and cursor_iter.ends_line():
                empty_line = True
                selection_length = len(helptext)

            # are we in the beginning of a line?
            if start.starts_line():
                text = text_buffer.get_text(start, end, True)
                # check whether we have a header already
                if match:=re.match(HEADER, text):
                    next_level = len(match.group("level"))

                    delete_end = start.copy()
                    delete_end.forward_chars(next_level + 1)

                    text_buffer.delete(start, delete_end)
                    if next_level == level:
                        return

                    # cycle between levels 1, 2 and 3. If we reach 0, we remove the heading
                    next_level = (next_level + 1) % 4

                # check whether the previous line has items
                start_previous_line = start.copy()
                start_previous_line.backward_lines(1)
                previous_line = text_buffer.get_text(start_previous_line, start, True)
                if previous_line not in [None, "\n", ""]:
                    indent = "\n"

            # if we're not at the beginning of the line we just insert a newline
            else:
                indent = "\n\n"

            # only apply next level if we're coming from the toolbar
            level = next_level if level == -1 else level
            text_buffer.insert(start, indent + "#"*level + f" {helptext if empty_line else ''}")

        self.__select_text(text_view, 0, selection_length)

    def insert_blockquote(self, action, data, text_view):
        """Insert quote or mark a selection as a quote"""
        text_buffer = text_view.get_buffer()
        cursor_mark = text_buffer.get_insert()
        cursor_iter = text_buffer.get_iter_at_mark(cursor_mark)

        helptext = _("Quote")

        with user_action(text_buffer):
            selection_length = 0
            empty_line = False

            if text_buffer.get_has_selection():
                (start, end) = text_buffer.get_selection_bounds()
                text_buffer.move_mark(cursor_mark, end)
                selection_length = len(text_buffer.get_text(start, end, True))
            else:
                (start, end) = text_buffer.get_current_line_bounds()

            if start.starts_line() :
                text = text_buffer.get_text(start, end, True)

                if text.startswith("> "):
                    delete_end = start.copy()
                    delete_end.forward_chars(2)
                    text_buffer.delete(start, delete_end)
                else:
                    start_previous_line = start.copy()
                    start_previous_line.backward_lines(1)
                    previous_line = text_buffer.get_text(start_previous_line, start, True)

                    indent = "\n" if not previous_line else ""

                    if cursor_iter.starts_line() and cursor_iter.ends_line():
                        empty_line = True
                        selection_length = len(helptext)

                    text_buffer.insert(start, f"{indent}> {helptext if empty_line else ''}")
            else:
                text_buffer.insert(start, "\n\n> ")

        self.__select_text(text_view, 0, selection_length)

    def insert_codeblock(self, action, data, text_view):
        """Insert codeblock or mark a selection as an inline codeblock"""

        text_buffer = text_view.get_buffer()

        helptext = _("code")

        cursor_mark = text_buffer.get_insert()
        cursor_iter = text_buffer.get_iter_at_mark(cursor_mark)

        with user_action(text_buffer):

            # let's check if it's an empty line to use block markup
            if cursor_iter.starts_line() and cursor_iter.ends_line():
                self.__wrap(text_view, "```", f"\n{helptext}\n")
            # otherise we use inline markup
            else:
                self.__wrap(text_view, "`", helptext)

    def insert_link(self, action, data, text_view):
        """Insert weblink"""
        selection_length = 0

        # 0 for text, 1 for URL
        selected_part = 1

        text_buffer = text_view.get_buffer()

        url_helptext = _("https://www.example.com")
        helptext = _("link text")

        cursor_mark = text_buffer.get_insert()
        cursor_iter = text_buffer.get_iter_at_mark(cursor_mark)

        with user_action(text_buffer):
            selection_length = len(url_helptext)

            if text_buffer.get_has_selection():
                (start, end) = text_buffer.get_selection_bounds()
                text_buffer.move_mark(cursor_mark, end)
                text = text_buffer.get_text(start, end, True)
                text_buffer.delete(start, end)

                # is it selecting a url?
                if re.match(URL, text):
                    url_helptext = text
                    selection_length = len(helptext)
                    selected_part = 0
                else:
                    helptext = text
            else:
                start = cursor_iter.copy()

            text_buffer.insert(start, f"[{helptext}]({url_helptext})")

            self.__select_text(text_view, 1 if selected_part == 1 else len(url_helptext)+3, selection_length)

    def insert_image(self, action, data, text_view):
        """Open a filechooser to grab a image uri and insert it properly formatted"""
        selection_length = 0

        text_buffer = text_view.get_buffer()

        helptext = _("image caption")

        window = text_view.get_root()

        filefilter = Gtk.FileFilter.new()
        filefilter.add_mime_type('image/*')
        filefilter.set_name(_('Image'))

        filefilters = Gio.ListStore.new(Gtk.FileFilter)
        filefilters.append(filefilter)

        filechooser = Gtk.FileDialog.new()
        filechooser.set_title(_("Select an image"))
        filechooser.set_filters(filefilters)
        filechooser.set_modal(True)

        def filechooser_cb(dialog, result):
            try:
                image = dialog.open_finish(result)
            except GLib.GError as error:
                # we don't care much about what happened, just that we didn't
                # get an uri
                text_view.grab_focus()
                return
            basepath = window.current.base_path
            if basepath != "/":
                basepath = Gio.File.new_for_path(basepath)
                relative_path = basepath.get_relative_path(image)
                if relative_path:
                    path = urllib.parse.quote(relative_path)
                else:
                    path = urllib.parse.quote(image.get_path())
            else:
                path = urllib.parse.quote(image.get_path())

            helptext = _("image caption")

            cursor_mark = text_buffer.get_insert()
            cursor_iter = text_buffer.get_iter_at_mark(cursor_mark)

            with user_action(text_buffer):

                if text_buffer.get_has_selection():
                    (start, end) = text_buffer.get_selection_bounds()
                    text_buffer.move_mark(cursor_mark, end)
                    text = text_buffer.get_text(start, end, True)
                    text_buffer.delete(start, end)
                    helptext = text
                else:
                    start = cursor_iter.copy()

                text_buffer.insert(start, f"![{helptext}]({path})")

                selection_length = len(helptext)
                self.__select_text(text_view, len(path)+3, selection_length)


        filechooser.open(window, None, filechooser_cb)

    def insert_table(self, action, data, text_view):
        def generate_line(cells, width, content=" "):
            content = content*width if content else " "*width
            return "|" + (content+"|")*columns + "\n"

        text_buffer = text_view.get_buffer()
        cursor_iter = text_buffer.get_iter_at_mark(text_buffer.get_insert())

        [rows, columns] = data

        self.settings = Settings.new()
        characters_per_line = self.settings.get_int('characters-per-line')

        max_width = 20
        possible_width = (characters_per_line - columns - 1)//columns
        width = min(max_width, possible_width)

        table = ""
        for i in range(rows+2):
            table += generate_line(columns, width, "-" if i==1 else " ")

        text_buffer.insert(cursor_iter, table)

    @staticmethod
    def __wrap(text_view, wrap, helptext=""):
        """Inserts wrap format to the selected text
        (helper text when nothing selected)"""
        text_buffer = text_view.get_buffer()
        with user_action(text_buffer):
            if text_buffer.get_has_selection():
                # Find current highlighting
                (start, end) = text_buffer.get_selection_bounds()
                moved = False
                if (start.get_offset() >= len(wrap) and
                        end.get_offset() <= text_buffer.get_char_count() - len(wrap)):
                    moved = True
                    ext_start = start.copy()
                    ext_start.backward_chars(len(wrap))
                    ext_end = end.copy()
                    ext_end.forward_chars(len(wrap))
                    text = text_buffer.get_text(ext_start, ext_end, True)
                else:
                    text = text_buffer.get_text(start, end, True)

                if moved and text.startswith(wrap) and text.endswith(wrap):
                    text = text[len(wrap):-len(wrap)]
                    new_text = text
                    text_buffer.delete(ext_start, ext_end)
                    move_back = 0
                else:
                    if moved:
                        text = text[len(wrap):-len(wrap)]
                    new_text = text.lstrip().rstrip()
                    text = text.replace(new_text, wrap + new_text + wrap)

                    text_buffer.delete(start, end)
                    move_back = len(wrap)

                text_buffer.insert_at_cursor(text)
                text_length = len(new_text)

            else:
                text_buffer.insert_at_cursor(wrap + helptext + wrap)
                # we offset things by 1 char if we're inserting a codeblock
                text_length = len(helptext.strip("\n"))
                move_back = len(wrap) + (1 if helptext == _("\ncode\n") else 0)

        cursor_mark = text_buffer.get_insert()
        cursor_iter = text_buffer.get_iter_at_mark(cursor_mark)
        cursor_iter.backward_chars(move_back)
        text_buffer.move_mark_by_name('selection_bound', cursor_iter)
        cursor_iter.backward_chars(text_length)
        text_buffer.move_mark_by_name('insert', cursor_iter)
        text_view.grab_focus()

    @staticmethod
    def __select_text(text_view, offset, length):
        """Selects text starting at the current cursor
        minus offset, length characters."""

        text_buffer = text_view.get_buffer()
        cursor_iter = text_buffer.get_iter_at_mark(text_buffer.get_insert())
        cursor_iter.backward_chars(offset)
        text_buffer.move_mark_by_name('selection_bound', cursor_iter)
        cursor_iter.backward_chars(length)
        text_buffer.move_mark_by_name('insert', cursor_iter)
        text_view.grab_focus()
