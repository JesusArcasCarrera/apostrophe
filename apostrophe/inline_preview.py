# Copyright (C) 2022, Manuel Genovés <manuel.genoves@gmail.com>
#               2019, Wolf Vollprecht <w.vollprecht@gmail.com>
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

import os
import regex as re
import socket
import threading
from gettext import gettext as _
from urllib.parse import unquote

import gi

gi.require_version("Gtk", "4.0")
gi.require_version('WebKit', '6.0')
from gi.repository import Gdk, GObject, GLib, Gtk, WebKit, Adw

from apostrophe import latex_to_PNG, markup_regex
from apostrophe.settings import Settings


class DictAccessor:
    reEndResponse = re.compile(br"^[2-5][0-58][0-9] .*\r\n$", re.DOTALL +
                               re.MULTILINE)
    reDefinition = re.compile(br"^151(.*?)^\.", re.DOTALL + re.MULTILINE)

    def __init__(self, host="dict.dict.org", port=2628, timeout=60):
        self.socket = socket.create_connection((host, port), timeout)
        self.socket_file = self.socket.makefile("rb")
        self.login_response = self._read_response()

    def _read_response(self):
        response = b""
        while True:
            line = self.socket_file.readline()
            response += line
            if self.reEndResponse.search(response):
                break
        return response

    def run_command(self, cmd):
        self.socket.sendall(cmd.encode("utf-8") + b"\r\n")
        return self._read_response()

    def get_matches(self, database, strategy, word):
        if database in ["", "all"]:
            d = "*"
        else:
            d = database
        if strategy in ["", "default"]:
            s = "."
        else:
            s = strategy
        w = word.replace("\"", r"\\\"")
        tsplit = self.run_command(
            "MATCH {} {} \"{}\"".format(
                d, s, w)).splitlines()
        mlist = list()
        if tsplit[-1].startswith(b"250 ok") and tsplit[0].startswith(b"1"):
            mlines = tsplit[1:-2]
            for line in mlines:
                lsplit = line.strip().split()
                db = lsplit[0]
                word = unquote(" ".join(lsplit[1:]))
                mlist.append((db, word))
        return mlist

    def get_definition(self, database, word):
        if database in ["", "all"]:
            d = "*"
        else:
            d = database
        w = word.replace("\"", r"\\\"")
        dsplit = self.run_command(
            "DEFINE {} \"{}\"".format(
                d, w)).splitlines(True)

        dlist = list()
        if dsplit[-1].startswith(b"250 ok") and dsplit[0].startswith(b"1"):
            dlines = dsplit[1:-1]
            dtext = b"".join(dlines)
            dlist = [dtext]
        return dlist

    def close(self):
        t = self.run_command("QUIT")
        self.socket.close()
        return t

    def parse_wordnet(self, response):
        # consisting of group (n,v,adj,adv)
        # number, description, examples, synonyms, antonyms

        lines = response.splitlines()
        lines = lines[2:]
        lines = " ".join(lines)
        lines = re.sub(r"\s+", " ", lines).strip()
        lines = re.split(r"( adv | adj | n | v |^adv |^adj |^n |^v )", lines)
        res = []
        act_res = {"defs": [], "class": "none", "num": "None"}
        for l in lines:
            l = l.strip()
            if not l:
                continue
            if l in ["adv", "adj", "n", "v"]:
                if act_res:
                    res.append(act_res.copy())
                act_res = {"defs": [], "class": l}
            else:
                ll = re.split(r"(?: |^)(\d): ", l)
                act_def = {}
                for lll in ll:
                    if lll.strip().isdigit() or not lll.strip():
                        if "description" in act_def and act_def["description"]:
                            act_res["defs"].append(act_def.copy())
                        act_def = {"num": lll}
                        continue
                    a = re.findall(r"(\[(syn|ant): (.+?)\] ??)+", lll)
                    for n in a:
                        if n[1] == "syn":
                            act_def["syn"] = re.findall(r"{(.*?)}.*?", n[2])
                        else:
                            act_def["ant"] = re.findall(r"{(.*?)}.*?", n[2])
                    tbr = re.search(r"\[.+\]", lll)
                    if tbr:
                        lll = lll[:tbr.start()]
                    lll = lll.split(";")
                    act_def["examples"] = []
                    act_def["description"] = []
                    for llll in lll:
                        llll = llll.strip()
                        if llll.strip().startswith("\""):
                            act_def["examples"].append(llll)
                        else:
                            act_def["description"].append(llll)
                if act_def and "description" in act_def:
                    act_res["defs"].append(act_def.copy())

        res.append(act_res.copy())
        return res


def get_dictionary(term):
    da = DictAccessor()
    output = da.get_definition("wn", term)
    if output:
        output = output[0]
    else:
        return None
    return da.parse_wordnet(output.decode(encoding="UTF-8"))


class FixedWidthContainer(Adw.Bin):
    __gtype_name__ = "FixedWidthContainer"

    content_ = None

    @GObject.Property(type=Gtk.Widget)
    def content(self):
        return self.content_

    @content.setter
    def content(self, value):
        self.set_child(value)
        self.content_ = value

    def __init__(self):
        super().__init__()

        self.queue_allocate()
        self.queue_resize()
        self.set_layout_manager(None)

    def do_size_allocate(self, width, height, baseline):
        self.content.allocate(width, height, baseline)

    def do_measure(self, orientation, for_size):
        content_horizontal_natural = self.content.measure(Gtk.Orientation.HORIZONTAL, -1).natural
        if orientation == Gtk.Orientation.HORIZONTAL:
            min = natural = content_horizontal_natural
        else:
            min, natural, _, _ = self.content.measure(orientation, content_horizontal_natural)

        min_baseline = -1
        nat_baseline = -1

        return (min, natural, min_baseline, nat_baseline)

@Gtk.Template(resource_path='/org/gnome/gitlab/somas/Apostrophe/ui/InlinePreviewPopover.ui')
class InlinePreviewPopover(Gtk.Popover):
    __gtype_name__ = "InlinePreviewPopover"

    stack = Gtk.Template.Child()
    spinner = Gtk.Template.Child()
    empty = Gtk.Template.Child()
    error = Gtk.Template.Child()
    is_loaded = GObject.property(type=bool, default=False)
    preview_ = None

    @GObject.Property(type=Gtk.Widget)
    def preview(self):
        return self.preview_

    @preview.setter
    def preview(self, value):
        self.remove_preview()
        self.stack.add_named(value, "view")
        self.stack.set_visible_child(value)
        self.preview_ = value
        self.is_loaded = True

    def __init__(self):
        super().__init__()
        self.connect("closed", self._on_popover_closed)
        self.stack.set_visible_child(self.empty)

    def set_loading(self):
        GLib.timeout_add(15, self.set_spinner, None, 0)

    def set_spinner(self, *args, **kwargs):
        if not self.is_loaded:
            self.stack.set_visible_child(self.spinner)
        return False

    def remove_preview(self):
        if preview:= self.stack.get_child_by_name("view"):
            self.stack.remove(preview)
        self.is_loaded = False
        self.stack.set_visible_child(self.empty)

    def set_not_found(self):
        self.is_loaded = True
        GLib.idle_add(self.stack.set_visible_child, self.error)

    def _on_popover_closed(self, *args, **kwargs):
        self.remove_preview()

class InlinePreview(GObject.Object):
    WIDTH = 400
    HEIGHT = 300

    def __init__(self, text_view):

        self.settings = Settings.new()

        self.text_view = text_view
        self.text_buffer = text_view.get_buffer()

        self.latex_converter = latex_to_PNG.LatexToPNG()
        self.characters_per_line = self.settings.get_int("characters-per-line")

        self.popover = InlinePreviewPopover()
        self.popover.set_parent(text_view)

        self.popover.connect("closed", self._on_popover_closed)

        self.current_match = None

        self.preview_fns = {
            markup_regex.MATH: self.get_view_for_math,
            markup_regex.IMAGE: self.get_view_for_image,
            markup_regex.LINK: self.get_view_for_link,
            markup_regex.LINK_ALT: self.get_view_for_link,
            markup_regex.FOOTNOTE_ID: self.get_view_for_footnote,
            re.compile(r"(?P<text>\w+)"): self.get_view_for_lexikon
        }

    def get_view_for_math(self, match):
        success, temp_result = self.latex_converter.generatepng(match.group("text"))
        if success:
            result = Gdk.Texture.new_from_filename(temp_result.name)
            temp_result.close()
        else:
            result = temp_result

        if match == self.current_match:
            GLib.idle_add(self.get_view_for_math_finish, success, result)

    def get_view_for_math_finish(self, success, result):
        if success:
            image = Gtk.Picture()
            image.set_paintable(result)
            width, height = result.get_intrinsic_width(), result.get_intrinsic_height()
            ratio = max(width/self.WIDTH, height/self.HEIGHT, 1)
            width, height = width/ratio, height/ratio
            view = Gtk.ScrolledWindow.new()
            view.set_min_content_width(width)
            view.set_min_content_height(height)
            view.set_child(image)
        else:
            if result == 2:
                error = _("LaTeX not found")
            else:
                error = _("Formula looks incorrect:")
                error += "\n\n“{}”".format(result)
            view = Gtk.Label(label=error)
        view.add_css_class("formula")
        self.popover.preview = view
        return False

    def get_view_for_image(self, match):
        path = match.group("url")
        window = self.text_view.get_root()
        basepath = window.current.base_path

        if path.startswith(("https://", "http://", "www.")):
            self.get_view_for_link(match)
            return
        if path.startswith(("file://")):
            path = path[7:]
        if not path.startswith(("/", "file://")) and basepath != "/":
            path = os.path.join(basepath, path)
        path = unquote(path)

        if match == self.current_match:
            try:
                texture = Gdk.Texture.new_from_filename(path)
                GLib.idle_add(self.get_view_for_image_finish, texture)
            except GLib.GError as error:
                self.popover.set_not_found()

    def get_view_for_image_finish(self, texture):
        image = Gtk.Picture()
        image.set_paintable(texture)
        width, height = texture.get_intrinsic_width(), texture.get_intrinsic_height()
        ratio = max(width/self.WIDTH, height/self.HEIGHT, 1)
        width, height = width/ratio, height/ratio
        view = Gtk.ScrolledWindow.new()
        view.set_min_content_width(width)
        view.set_min_content_height(height)
        view.set_child(image)
        self.popover.preview = view
        return False

    def get_view_for_link(self, match):
        if match == self.current_match:
            GLib.idle_add(self.get_view_for_link_finish, match)

    def get_view_for_link_finish(self, match):
        url = match.group("url")
        web_view = WebKit.WebView(zoom_level=0.3125)  # ~1280x960
        web_view.set_size_request(self.WIDTH, self.HEIGHT)
        if GLib.uri_parse_scheme(url) is None:
            url = "http://{}".format(url)
        web_view.load_uri(url)
        self.popover.preview = web_view
        return False

    def get_view_for_footnote(self, match):
        footnote_id = match.group("id")
        fn_matches = re.finditer(
            markup_regex.FOOTNOTE,
            self.text_buffer.get_text(
                *self.text_buffer.get_bounds(),
                True
            ))
        for fn_match in fn_matches:
            if fn_match.group("id") == footnote_id:
                if fn_match:
                    footnote = re.sub("\n[\t ]+", "\n", fn_match.group("text"))
                else:
                    footnote = _("No matching footnote found")
                if match == self.current_match:
                    GLib.idle_add(self.get_view_for_footnote_finish, footnote)
                return False

    def get_view_for_footnote_finish(self, footnote):

        label = Gtk.Label(label=footnote)
        label.set_max_width_chars(self.characters_per_line)
        label.set_wrap(True)
        view = FixedWidthContainer()
        view.add_css_class("footnote")
        view.content = label
        self.popover.preview = view
        return False

    def get_view_for_lexikon(self, match):
        term = match.group("text")
        lexikon_dict = get_dictionary(term)
        if match == self.current_match:
            GLib.idle_add(self.get_view_for_lexikon_finish, term, lexikon_dict)

    def get_view_for_lexikon_finish(self, term, lexikon_dict):
        if lexikon_dict:
            grid = Gtk.Grid.new()
            grid.add_css_class("lexikon")
            grid.set_row_spacing(2)
            grid.set_column_spacing(4)
            i = 0
            for entry in lexikon_dict:
                if not entry["defs"]:
                    continue
                elif entry["class"].startswith("n"):
                    word_type = _("noun")
                elif entry["class"].startswith("v"):
                    word_type = _("verb")
                elif entry["class"].startswith("adj"):
                    word_type = _("adjective")
                elif entry["class"].startswith("adv"):
                    word_type = _("adverb")
                else:
                    continue

                vocab_label = Gtk.Label.new(term + " ~ " + word_type)
                vocab_label.add_css_class("header")
                if i == 0:
                    vocab_label.add_css_class("first")
                vocab_label.set_halign(Gtk.Align.START)
                vocab_label.set_justify(Gtk.Justification.LEFT)
                grid.attach(vocab_label, 0, i, 3, 1)

                for definition in entry["defs"]:
                    i = i + 1
                    num_label = Gtk.Label.new(definition["num"] + ".")
                    num_label.add_css_class("number")
                    num_label.set_valign(Gtk.Align.START)
                    grid.attach(num_label, 0, i, 1, 1)

                    def_label = Gtk.Label(
                        label=" ".join(
                            definition["description"]))
                    def_label.add_css_class("description")
                    def_label.set_halign(Gtk.Align.START)
                    def_label.set_max_width_chars(self.characters_per_line)
                    def_label.set_wrap(True)
                    def_label.set_justify(Gtk.Justification.FILL)
                    grid.attach(def_label, 1, i, 1, 1)
                i = i + 1
            if i > 0:
                view = Gtk.ScrolledWindow.new()
                view.set_max_content_height(self.HEIGHT)
                view.set_propagate_natural_width(True)
                view.set_propagate_natural_height(True)
                fixed_container = FixedWidthContainer()
                fixed_container.content = grid
                view.set_child(fixed_container)
                self.popover.preview = view
        else:
            self.popover.set_not_found()
        return False

    def open_popover(self, *args, **kwargs):
        rect = self.text_view.get_iter_location(
                            self.text_buffer.get_iter_at_mark(self.text_buffer.get_insert()))
        rect.x, rect.y = self.text_view.buffer_to_window_coords(
            Gtk.TextWindowType.TEXT, rect.x, rect.y)
        self.popover.set_pointing_to(rect)

        self.popover.popup()
        self.popover.set_loading()

        self.populate_popover()

    def populate_popover(self, *args, **kwargs):
        start_iter = self.text_buffer.get_iter_at_mark(self.text_buffer.get_insert())
        line_offset = start_iter.get_line_offset()
        end_iter = start_iter.copy()
        start_iter.set_line_offset(0)
        end_iter.forward_to_line_end()
        text = self.text_buffer.get_text(start_iter, end_iter, True)

        for regex, get_view_fn in self.preview_fns.items():
            matches = re.finditer(regex, text)
            for match in matches:
                if match.start() <= line_offset <= match.end():
                    self.current_match = match
                    threading.Thread(target=get_view_fn, args=(match,)).start()
                    return

        self.popover.set_not_found()

    def _on_popover_closed(self, *args, **kwargs):
        self.current_match = None
