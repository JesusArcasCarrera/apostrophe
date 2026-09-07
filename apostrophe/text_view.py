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

import math
import urllib
from gettext import gettext as _
from os.path import basename

import gi

from apostrophe.helpers import user_action
from apostrophe.inline_preview import InlinePreview
from apostrophe.settings import Settings
from apostrophe.text_view_format_inserter import FormatInserter
from apostrophe.text_view_markup_handler import MarkupHandler
from apostrophe.text_view_scroller import TextViewScroller
from apostrophe.text_buffer import ApostropheTextBuffer
from apostrophe.theme_switcher import Theme

gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
gi.require_version('Spelling', '1')

import logging

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, GtkSource, Spelling

LOGGER = logging.getLogger('apostrophe')

DEFAULT_DPI = 98304

@Gtk.Template(resource_path='/org/gnome/gitlab/somas/Apostrophe/ui/TextView.ui')
class ApostropheTextView(GtkSource.View):
    """ApostropheTextView encapsulates all the features around the editor.

    It combines the following:
    - Undo / redo (via TextBufferUndoRedoHandler)
    - Markup (via TextBufferMarkupHandler)
    - Preview popover (via TextBufferMarkupHandler)
    - Drag and drop
    - Scrolling (via TextViewScroller)
    - The various modes supported by Apostrophe (e. Focus Mode, Hemingway Mode)
    """

    __gtype_name__ = "ApostropheTextView"

    bigger_text = GObject.Property(type=bool, default=False)
    focus_mode = GObject.Property(type=bool, default=False)
    font_size = GObject.Property(type=int, default=16)
    line_chars = GObject.Property(type=int, default=66)
    rich_editing = GObject.Property(type=bool, default=True)
    spellcheck = GObject.Property(type=bool, default=True)
    typewriter_sounds = GObject.Property(type=bool, default=False)

    spelling_adapter = None
    spelling_checker = None
    spelling_provider = None

    scroller = None

    _scroll_scale = 0

    @GObject.Property(type=float, default=0)
    def scroll_scale(self):
        return self.scroller.get_scroll_scale_() if self.scroller else 0

    @scroll_scale.setter
    def scroll_scale(self, scale):
        if self.scroller and not math.isclose(scale, self._scroll_scale, rel_tol=1e-5):
            self._scroll_scale = scale
            self.scroller.set_scroll_scale_(scale)

    gesture_controller = Gtk.Template.Child()

    def __init__(self):
        super().__init__()

        self.settings = Settings.new()

        self.buffer = self.get_buffer()
        self.typewriter_sound_players = []
        self.next_typewriter_sound_player = 0

        # Spell checking
        self.spelling_provider = Spelling.Provider.get_default()
        self.spelling_checker = Spelling.Checker.new(self.spelling_provider, self.spelling_provider.get_default_code())
        self.spelling_adapter = Spelling.TextBufferAdapter.new(self.buffer, self.spelling_checker)
        spellchecking_menu = self.spelling_adapter.get_menu_model()

        self.spelling_checker.connect("notify::language", self._on_spelling_language_changed)

        self.insert_action_group('spelling', self.spelling_adapter)

        self.settings.bind("spellcheck", self.spelling_adapter,
                           "enabled", Gio.SettingsBindFlags.DEFAULT)
        
        # Setting up text size
        self.settings.bind("bigger-text", self,
                           "bigger_text", Gio.SettingsBindFlags.GET)

        self.settings.bind("characters-per-line", self,
                           "line_chars", Gio.SettingsBindFlags.GET)
        self.settings.bind("rich-editing", self,
                           "rich_editing", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("typewriter-sounds", self,
                           "typewriter_sounds", Gio.SettingsBindFlags.GET)
        self.connect("notify::typewriter-sounds",
                     self._on_typewriter_sounds_update)
        self._on_typewriter_sounds_update()
        
        # Preview popover
        self.preview_popover = InlinePreview(self)
        action = Gio.SimpleAction.new("open_inline_preview")
        action.connect("activate", self.preview_popover.open_popover)

        group = Gio.SimpleActionGroup.new()
        group.add_action(action)

        self.insert_action_group("text", group)
        
        peek_menu = Gio.Menu()
        peek_item = Gio.MenuItem.new("Peek", 'text.open_inline_preview')
        peek_menu.append_item(peek_item)

        # Context menu
        extra_menu = Gio.Menu()
        extra_menu.append_section(None, peek_menu)
        extra_menu.append_section(None, spellchecking_menu)

        self.set_extra_menu(extra_menu)

        key = Gtk.EventControllerKey.new()
        key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key.connect_after('key-pressed', self._on_key_pressed)
        self.add_controller(key)

        # Markup
        self.markup = MarkupHandler(self)
        self.connect('destroy', self.markup.stop)

        # Drag and drop
        formats = Gdk.ContentFormatsBuilder.new()
        formats.add_gtype(Gio.File)
        formats.add_gtype(GObject.TYPE_STRING)
        drop_target = Gtk.DropTarget(formats=formats.to_formats(), actions=Gdk.DragAction.COPY)
        drop_target.connect('drop', self.on_drop)
        self.add_controller(drop_target)

        # When pasting b-trees, we may get more than one insertion. 
        # We want to disable autocompletion until the paste is done
        self.connect("paste-clipboard", self._on_clipboard_paste_started)

        self.update_vertical_margin()

        # color scheme
        theme = Theme.get_current().name
        scheme_manager = GtkSource.StyleSchemeManager.get_default()
        self.buffer.set_style_scheme(scheme_manager.get_scheme(theme))

        # scroller
        self.scroller = None

        # update font size on next cycle once we have a proper measure
        GLib.idle_add(self.update_font_size)

    def _on_key_pressed(self, controller, key, keycode, state):
        if self.typewriter_sounds and self._is_typing_key(key, state):
            self._play_typewriter_sound()

        if ((key == Gdk.KEY_Tab or key == Gdk.KEY_KP_Tab or key == Gdk.KEY_ISO_Left_Tab) and state == Gdk.ModifierType.SHIFT_MASK):
            self.buffer._unindent()
            return Gdk.EVENT_STOP

    @staticmethod
    def _is_typing_key(key, state):
        shortcut_modifiers = (Gdk.ModifierType.CONTROL_MASK |
                              Gdk.ModifierType.ALT_MASK |
                              Gdk.ModifierType.SUPER_MASK)
        if state & shortcut_modifiers:
            return False

        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_BackSpace,
                   Gdk.KEY_Delete, Gdk.KEY_Tab, Gdk.KEY_KP_Tab):
            return True

        character = Gdk.keyval_to_unicode(key)
        return bool(character and chr(character).isprintable())

    def _play_typewriter_sound(self):
        if not self.typewriter_sound_players:
            self._prepare_typewriter_sounds()

        player = self.typewriter_sound_players[
            self.next_typewriter_sound_player
        ]
        self.next_typewriter_sound_player = (
            self.next_typewriter_sound_player + 1
        ) % len(self.typewriter_sound_players)
        player.pause()
        player.set_timestamp(0)
        player.play()

    def _on_typewriter_sounds_update(self, *_):
        if self.typewriter_sounds and not self.typewriter_sound_players:
            self._prepare_typewriter_sounds()

    def _prepare_typewriter_sounds(self):
        # A small pool lets quick successive clicks overlap naturally.
        self.typewriter_sound_players = [
            Gtk.MediaFile.new_for_resource(
                "/org/gnome/gitlab/somas/Apostrophe/sounds/typewriter-key.wav"
            )
            for _ in range(4)
        ]
        for player in self.typewriter_sound_players:
            player.set_volume(0.45)

    def on_drop(self, drop_target, content, _x, _y):
        # check if a file was dropped
        try:
            # if we got a file, get its uri
            if isinstance(content, Gio.File):
                content = content.get_uri()
            GLib.Uri.is_valid(content, GLib.UriFlags.NONE)
            uri = GLib.Uri.parse(content, GLib.UriFlags.NONE)

            # we could try to use glib utils for handling uris but it's not that
            # worth it
            if content.startswith("file://"):
                # remove trailing newlines
                content = content.strip()

                name = basename(urllib.parse.unquote_plus(content))
                mime = Gio.content_type_guess(content)

                # stripe "file://"
                content = content[7:]

                if mime[0] is not None and mime[0].startswith('image/'):
                    basepath = self.get_root().current.base_path
                    basepath = urllib.parse.quote(basepath)


                    # for handling local URIs we need to substract the basepath
                    # except when it is "/" (document not saved)
                    # TODO: use g_uri_resolve_relative
                    if content.startswith(basepath) and basepath != "/":
                        content = content[len(basepath) + 1:]

                    content = "![{}]({})".format(name, content)
                    limit_left = 2
                    limit_right = len(name)
                else:
                    content = "[{}]({})".format(name, content)
                    limit_left = 1
                    limit_right = len(name)
            elif content.startswith(("https", "http")):
                content = "[{}]({})".format(_("web page"), content)
                limit_left = 1
                limit_right = len(_("web page"))
        # otherwise we just received text
        except Exception as e:
            # delete automatically added DnD text
            insert_mark = self.buffer.get_insert()
            cursor_iter_l, cursor_iter_r = self.buffer.get_selection_bounds()
            self.buffer.delete(cursor_iter_l, cursor_iter_r)

            limit_left = 0
            limit_right = 0

        self.buffer.place_cursor(self.buffer.get_iter_at_mark(
            self.buffer.get_mark('gtk_drag_target')))
        self.buffer.insert_at_cursor(content)
        insert_mark = self.buffer.get_insert()
        selection_bound = self.buffer.get_selection_bound()
        cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
        cursor_iter.backward_chars(len(content) - limit_left)
        self.buffer.move_mark(insert_mark, cursor_iter)
        cursor_iter.forward_chars(limit_right)
        self.buffer.move_mark(selection_bound, cursor_iter)

    def get_text(self):
        start_iter = self.buffer.get_start_iter()
        end_iter = self.buffer.get_end_iter()
        return self.buffer.get_text(start_iter, end_iter, True)

    def set_text(self, text):
        """Set text and clear undo history"""

        self.buffer.begin_irreversible_action()
        with user_action(self.buffer):
            self.buffer.set_text(text)
        self.buffer.end_irreversible_action()

    def update_vertical_margin(self, top_margin=0):
        if self.focus_mode:
            height = self.get_height() + top_margin 
            target_margin = height / 2 
            if self.get_top_margin() != target_margin + top_margin:
                self.set_top_margin(target_margin + top_margin)
            if self.get_bottom_margin() != target_margin:
                self.set_bottom_margin(target_margin)
        else:
            # we want 80 top margin when only the headerbar is shown, 
            # top_margin is 46 in that case
            # sync that value with the webview's one
            self.set_top_margin(34 + top_margin)
            self.set_bottom_margin(64)

    def clear(self):
        """Clear text and undo history"""

        self.set_text('')

    def smooth_scroll_to(self, mark=None):
        """Scrolls if needed to ensure mark is visible.

        If mark is unspecified, the cursor is used."""

        if self.scroller is None:
            return
        if mark is None:
            mark = self.buffer.get_insert()
        GLib.idle_add(self.scroller.smooth_scroll_to_mark,
                      mark, self.focus_mode)

    @Gtk.Template.Callback()
    def _on_button_pressed_event(self, gesture, n_press, x, y):
        event = gesture.get_current_event()
        buffer_x, buffer_y = self.window_to_buffer_coords(Gtk.TextWindowType.TEXT, x, y)
        found, iter = self.get_iter_at_location(buffer_x, buffer_y)
        if found and not self.buffer.get_has_selection():
            self.buffer.place_cursor(iter)

        if event.get_modifier_state() == Gdk.ModifierType.CONTROL_MASK and not event.triggers_context_menu():
            self.preview_popover.open_popover()

        return False

    @Gtk.Template.Callback()
    def _on_button_release_event(self, *args, **kwargs):
        if self.focus_mode or self.rich_editing:
            self.markup.apply()
        if self.focus_mode:
            self.smooth_scroll_to()
        return False

    @Gtk.Template.Callback()
    def _on_focus_mode_update(self, *args, **kwargs):
        self.update_vertical_margin()
        self.markup.apply()
        self.smooth_scroll_to()
        self._on_spellcheck_update()
        self.grab_focus()

    @Gtk.Template.Callback()
    def _on_rich_editing_update(self, *_):
        if hasattr(self, "markup"):
            self.markup.apply()

    @Gtk.Template.Callback()
    def _on_mark_set(self, _text_buffer, _location, mark, _data=None):
        # only scroll if the cursor was moved by keyboard.
        # otherwise check _on_button_release_event
        if mark.get_name() == 'selection_bound' and not self.gesture_controller.is_active():
            self.markup.apply()
            if not self.buffer.get_has_selection():
                self.smooth_scroll_to(mark)
        elif mark.get_name() == 'gtk_drag_target':
            self.smooth_scroll_to(mark)
        return True

    @Gtk.Template.Callback()
    def _on_paste_done(self, *_):
        self.smooth_scroll_to()

    def _on_size_allocate(self, *_):
        self._update_horizontal_margin()
        self.markup.update_margins_indents()
        self.queue_draw()

        self.preview_popover.popover.present()

    @Gtk.Template.Callback()
    def _on_spellcheck_update(self, *args, **kwargs):
        self.spelling_adapter.set_enabled(self.spellcheck and not self.focus_mode)

    @Gtk.Template.Callback()
    def _on_text_changed(self, *_):
        self.markup.apply()
        self.smooth_scroll_to()

    def _on_vadjustment_changed(self, *_):
        if self.scroller.can_scroll():
            self.notify("scroll_scale")

    def _on_spelling_language_changed(self, _obj, _spec):
        self.spelling_adapter.invalidate_all()

    def _on_clipboard_paste_started(self, *args, **kwargs):
        self.buffer.paste_ongoing = True


    @Gtk.Template.Callback()
    def update_font_size(self, *args, **kwargs):
        width = self.get_width()
        # Ensure the appropriate font size is being used
        for font_size in self._get_font_sizes():
            if width >= self.get_min_width(font_size):
                if font_size != self.font_size:
                    self.font_size = font_size
                    for size_class in filter(lambda style_class: style_class.startswith("size"), self.get_css_classes()):
                        self.remove_css_class(size_class)
                    self.add_css_class("size{}".format(font_size))
                    self.queue_draw()
                    self._update_horizontal_margin()
                break
        else:
            return

    def _update_horizontal_margin(self, *args, **kwargs):
        width = self.get_width()

        # Apply margin with the remaining space to allow for markup
        line_width = round((self.line_chars*self._get_char_width(self.font_size)))
        horizontal_margin = (width - line_width) // 2
        self.set_left_margin(horizontal_margin)
        self.set_right_margin(width - line_width - horizontal_margin)

    def _get_font_sizes(self):
        font_sizes_list = [20, 18, 17, 16, 15, 14]
        if self.bigger_text:
            font_sizes_list[:0] = [24, 22]

        return font_sizes_list

    def get_min_width(self, font_size=None, line_chars=None):
        """Returns the minimum width of this text view."""

        if font_size is None:
            font_size = self._get_font_sizes()[-1]

        if line_chars is None:
            line_chars = self.line_chars

        return round((line_chars + self._get_pad_chars(font_size)) * self._get_char_width(font_size))

    def _get_pad_chars(self, font_size):
        """Returns the amount of character padding for font_size.
        Markup can use up to 7 in normal conditions, so the minimum is 14"""

        if self.bigger_text:
            return max(14, 8 * int((1 + (font_size - self._get_font_sizes()[-1])/3)))
        else:
            return max(14, 8 * (1 + font_size - self._get_font_sizes()[-1]))

    @staticmethod
    def _get_char_width(font_size):
        """Returns the font width for a given size.
        Note: specific to Fira Mono!"""

        # We scale by the text scale factor system-wide setting.
        # In the css the units are in em so they're automatically scaled
        # 14.666px == 1em

        settings = Gtk.Settings.get_default()
        scale_factor = settings.props.gtk_xft_dpi / DEFAULT_DPI

        return scale_factor * font_size * 0.6

    def do_size_allocate(self, width, height, baseline):
        self._on_size_allocate()
        Gtk.TextView.do_size_allocate(self,width, height, baseline)
