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

import logging
import os
from dataclasses import dataclass
from gettext import gettext as _

import chardet
import gi


gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from apostrophe import helpers
from apostrophe.editor import Editor
from apostrophe.export_dialog import AdvancedExportDialog, ExportDialog
from apostrophe.headerbars import BaseHeaderbar
from apostrophe.helpers import App, bind_enum
from apostrophe.panels import ApostrophePanels
from apostrophe.preview_handler import PreviewHandler
from apostrophe.preview_security import PreviewSecurity
from apostrophe.search_and_replace import ApostropheSearchBar
from apostrophe.settings import Settings
from apostrophe.stats_handler import StatsHandler
from apostrophe.text_view import ApostropheTextView
from apostrophe.text_view_format_inserter import FormatInserter
from apostrophe.preview_security import PreviewSecurityHandler
from apostrophe.pride import apply_seasonal_style
from apostrophe.sized_bin import ApostropheSizedBin

LOGGER = logging.getLogger('apostrophe')


@Gtk.Template(resource_path='/org/gnome/gitlab/somas/Apostrophe/ui/Window.ui')
class MainWindow(Adw.ApplicationWindow):

    __gtype_name__ = "ApostropheWindow"


    editor = Gtk.Template.Child()
    save_progressbar = Gtk.Template.Child()
    headerbar_revealer = Gtk.Template.Child()
    headerbar = Gtk.Template.Child()
    headerbar_narrow = Gtk.Template.Child()
    searchbar = Gtk.Template.Child()
    panels = Gtk.Template.Child()
    preview_stack = Gtk.Template.Child()
    security_warning = Gtk.Template.Child()
    discard_infobar = Gtk.Template.Child()
    preview_spinner = Gtk.Template.Child()
    webview_snapshot = Gtk.Template.Child()

    headerbars_breakpointbin = Gtk.Template.Child()
    headerbars_breakpoint = Gtk.Template.Child()

    subtitle = GObject.Property(type=str)
    is_fullscreen = GObject.Property(type=bool, default=False)

    preview = GObject.Property(type=bool, default=False)
    preview_layout = GObject.Property(type=int, default=1)

    did_change = GObject.Property(type=bool, default=False)
    current = GObject.Property(type=GObject.Object, default=None)
    snapshot = GObject.Property(type=Gio.File, default=None)
    snapshot_restored = GObject.Property(type=bool, default=False)
    topbars_height = GObject.Property(type=int, default=0)

    close_anyway = False
    file_monitor = None

    def __init__(self, app):
        """Set up the main window"""

        super().__init__(application=Gio.Application.get_default(),
                         title="Apostrophe Rich")

        os.chdir("/")

        # Preferences
        self.settings = Settings.new()

        # Connect signals that we can't connect on the UI file
        self.connect("notify::is-fullscreen", self._on_fullscreen)

        # Create new, empty file
        # TODO: load last opened file?
        self.current = File()

        # Setup text editor
        self.textview = self.editor.textview
        self.text_changed_handler_id = None
        self.autosave_timer = None

        # save metadata for spellchecking language
        self.textview.spelling_checker.connect("notify::language", self._on_spellchecking_language_changed)

        # Setup save progressbar an its animator
        def hide_progressbar(animation, *args):
            self.save_progressbar.hide()

        fade_target = Adw.PropertyAnimationTarget.new(self.save_progressbar, "opacity")
        self.progressbar_fade_out = Adw.TimedAnimation.new(self.save_progressbar, 1, 0, 500, fade_target)
        self.progressbar_fade_out.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self.progressbar_fade_out.connect("done", hide_progressbar)

        def on_progressbar_value(animation, *args):
            if animation.get_value() > 0.3 and self.did_change:
                animation.pause()
            
        def fade_out_progressbar(animation, *args):
            self.progressbar_fade_out.play()

        fraction_target = Adw.PropertyAnimationTarget.new(self.save_progressbar, "fraction")
        self.progressbar_animation = Adw.TimedAnimation.new(self.save_progressbar, 0, 1, 300, fraction_target)
        self.progressbar_animation.connect("notify::value", on_progressbar_value)
        self.progressbar_animation.connect("done", fade_out_progressbar)

        # Setup pride progressbar
        apply_seasonal_style(self.save_progressbar)

        # Setup preview
        self.preview_handler = PreviewHandler(self, self.textview, self.panels)

        # not really necessary but we'll keep a preview_layout property on the window
        # and bind it both to the switcher and the renderer
        self.bind_property("preview_layout", self.headerbar.preview_layout_switcher, 
                           "preview_layout", GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)

        self.bind_property("preview_layout", self.panels, 
                           "layout", GObject.BindingFlags.SYNC_CREATE)

        self.panels.connect("close-panel-window", self.on_preview_window_close)

        # preview security handler
        self.preview_security_handler = PreviewSecurityHandler(self)

        # Headerbars breakpoint bin minimum size
        self.headerbars_breakpointbin.set_size_request(
            self.headerbar_narrow.measure(Gtk.Orientation.HORIZONTAL, -1).minimum,
            self.headerbar_narrow.measure(Gtk.Orientation.VERTICAL, -1).minimum
            )

        # Search and replace initialization
        self.searchbar.attach(self.textview)

        # Hemingway Toast
        self.hemingway_toast = Adw.Toast.new(_("Text can't be deleted while on Hemingway mode"))
        self.hemingway_toast.set_timeout(3)
        self.hemingway_toast.set_action_name("win.show_hemingway_help")
        self.hemingway_toast.set_button_label(_("Tell me more"))

        # Actions
        action = Gio.PropertyAction.new("find", self.searchbar, "search-mode-enabled")
        self.add_action(action)

        action = Gio.PropertyAction.new("find_replace", self.searchbar, "replace-mode-enabled")
        self.add_action(action)

        action = Gio.PropertyAction.new("focus_mode", self.textview, "focus-mode")
        self.add_action(action)

        action = Gio.PropertyAction.new(
            "rich_editing", self.textview, "rich-editing")
        self.add_action(action)

        action = Gio.PropertyAction.new("hemingway_mode", self.textview.buffer, "hemingway-mode")
        self.add_action(action)
        self.textview.connect("notify::hemingway-mode", self.show_hemingway_toast)

        action = Gio.SimpleAction.new("show_hemingway_toast", None)
        action.connect("activate", self.show_hemingway_toast)
        self.add_action(action)

        action = Gio.SimpleAction.new("show_hemingway_help", None)
        action.connect("activate", self.show_hemingway_help)
        self.add_action(action)

        action = Gio.PropertyAction.new("fullscreen", self, "is-fullscreen")
        self.add_action(action)

        action = Gio.PropertyAction.new("preview", self, "preview")
        self.add_action(action)
        self.connect("notify::preview", self.toggle_preview)

        # currently unused, we rather open a new window
        action = Gio.SimpleAction.new("new", None)
        action.connect("activate", self.new_document)
        self.add_action(action)

        action = Gio.SimpleAction.new("open", None)
        action.connect("activate", self.open_document)
        self.add_action(action)

        action = Gio.SimpleAction.new("open_file", GLib.VariantType("s"))
        action.connect("activate", self.open_from_gvariant)
        self.add_action(action)

        action = Gio.SimpleAction.new("save", None)
        action.connect("activate", self.save_document)
        self.add_action(action)

        action = Gio.SimpleAction.new("save_as", None)
        action.connect("activate", self.save_document_as)
        self.add_action(action)

        action = Gio.SimpleAction.new("export", GLib.VariantType("s"))
        action.connect("activate", self.open_export)
        self.add_action(action)

        action = Gio.SimpleAction.new("advanced_export", None)
        action.connect("activate", self.open_advanced_export)
        self.add_action(action)

        action = Gio.SimpleAction.new("copy_html", None)
        action.connect("activate", self.copy_html_to_clipboard)
        self.add_action(action)

        action = Gio.SimpleAction.new("close", None)
        action.connect("activate", self.do_close_request)
        self.add_action(action)

        action = Gio.SimpleAction.new("insert-bold")
        action.connect_after("activate", FormatInserter().insert_bold, self.textview)
        self.add_action(action)

        action = Gio.SimpleAction.new("insert-italic")
        action.connect_after("activate", FormatInserter().insert_italic, self.textview)
        self.add_action(action)

        action = Gio.SimpleAction.new("insert-strikethrough",)
        action.connect_after("activate", FormatInserter().insert_strikethrough, self.textview)
        self.add_action(action)

        action = Gio.SimpleAction.new("insert-header", GLib.VariantType("i"))
        action.connect_after("activate", FormatInserter().insert_header, self.textview)
        self.add_action(action)

        action = Gio.SimpleAction.new("insert-hrule")
        action.connect_after("activate", FormatInserter().insert_horizontal_rule, self.textview)
        self.add_action(action)

        action = Gio.SimpleAction.new("insert-listitem")
        action.connect_after("activate", FormatInserter().insert_list_item, self.textview)
        self.add_action(action)

        action = Gio.SimpleAction.new("insert-checklist-listitem")
        action.connect_after("activate", FormatInserter().insert_checklist_item, self.textview)
        self.add_action(action)

        action = Gio.SimpleAction.new("insert-ordered-listitem")
        action.connect_after("activate", FormatInserter().insert_ordered_list_item, self.textview)
        self.add_action(action)

        action = Gio.SimpleAction.new("insert-blockquote")
        action.connect_after("activate", FormatInserter().insert_blockquote, self.textview)
        self.add_action(action)

        action = Gio.SimpleAction.new("insert-codeblock")
        action.connect_after("activate", FormatInserter().insert_codeblock, self.textview)
        self.add_action(action)

        action = Gio.SimpleAction.new("insert-link")
        action.connect_after("activate", FormatInserter().insert_link, self.textview)
        self.add_action(action)

        action = Gio.SimpleAction.new("insert-image")
        action.connect_after("activate", FormatInserter().insert_image, self.textview)
        self.add_action(action)

        action = Gio.SimpleAction.new("insert-table", GLib.VariantType("ai"))
        action.connect_after("activate", FormatInserter().insert_table, self.textview)
        self.add_action(action)

        # update textview's top margin on topbar size changes
        self.connect("notify::topbars-height", self.update_textview_margin)

        # Bind gsettings
        self.settings.bind("window-width", self, "default-width",
            Gio.SettingsBindFlags.DEFAULT|Gio.SettingsBindFlags.GET_NO_CHANGES)
        self.settings.bind("window-height", self, "default-height",
            Gio.SettingsBindFlags.DEFAULT|Gio.SettingsBindFlags.GET_NO_CHANGES)
        self.settings.bind("is-maximized", self, "maximized",
            Gio.SettingsBindFlags.DEFAULT|Gio.SettingsBindFlags.GET_NO_CHANGES)
        self.settings.bind("is-fullscreen", self, "fullscreened",
            Gio.SettingsBindFlags.DEFAULT|Gio.SettingsBindFlags.GET_NO_CHANGES)
        bind_enum(self.settings, "preview-mode", self, "preview-layout",
            Gio.SettingsBindFlags.DEFAULT|Gio.SettingsBindFlags.GET_NO_CHANGES)
        self.settings.bind("preview-active", self, "preview",
            Gio.SettingsBindFlags.DEFAULT|Gio.SettingsBindFlags.GET_NO_CHANGES)

        self.new_document()

        if self.get_application()._application_id == 'org.gnome.gitlab.somas.Apostrophe.Devel':
            self.add_css_class('devel')

    def update_textview_margin(self, *args, **kwargs):
        self.textview.update_vertical_margin(self.topbars_height)
        self.textview.queue_resize()

    def on_text_changed(self, *_args):
        """called when the text changes, sets the self.did_change to true and
           updates the title and the counters to reflect that
        """

        if self.did_change is False:
            self.did_change = True

            # Autosaving
            self.autosave_timer = GLib.timeout_add_seconds(self.settings.get_int("autosave-period"), self.autosave_snapshot)

        if self.snapshot_restored is True:
            self.snapshot_restored = False

        self.update_headerbar_title(True, True)
        if self.settings.get_value("autohide-headerbar"):
            self.hide_headerbar_bottombar()

    def _on_fullscreen(self, *args, **kwargs):
        """Puts the application in fullscreen mode and show/hides
        the poller for motion in the top border
        """
        if self.is_fullscreen == True:
            self.fullscreen()
            self.hide_headerbar_bottombar()
        else:
            self.unfullscreen()
            self.reveal_headerbar_bottombar()

        self.textview.grab_focus()

    def on_preview_window_close(self, *args, **kwargs):
        self.preview = False

    def toggle_preview(self, *args, **kwargs):
        """Toggle the preview mode
        """

        if self.preview:
            self.textview.grab_focus()
            self.preview_handler.show()
        else:
            self.preview_handler.hide()
            self.textview.grab_focus()

    @Gtk.Template.Callback()
    def load_restricted_preview(self, *args):
        self.current.security_level = PreviewSecurity.RESTRICTED

    @Gtk.Template.Callback()
    def load_preview(self, *args):
        self.current.security_level = PreviewSecurity.UNRESTRICTED

    def save_document(self, _action=None, _value=None, callback=None):
        """Try to save buffer in the current gfile.
        If the file doesn't exist calls save_document_as
        """

        self.reveal_headerbar_bottombar()
        if self.current.gfile:
            LOGGER.info("saving")

            # cancel any file monitor that is active
            if self.file_monitor:
                self.file_monitor.cancel()
            self.discard_infobar.set_revealed(False)

            # We try to encode the file with the given encoding
            # if that doesn't work, we try with UTF-8
            # if that fails as well, we return False
            try:
                try:
                    encoded_text = self.textview.get_text()\
                        .encode(self.current.encoding)
                except UnicodeEncodeError:
                    encoded_text = self.textview.get_text()\
                        .encode("UTF-8")
                    self.current.encoding = "UTF-8"
            except UnicodeEncodeError as error:
                helpers.show_error(self, str(error.reason))
                LOGGER.warning(str(error.reason))
                return
            else:
                self.save_progressbar.set_opacity(1)
                self.save_progressbar.set_visible(True)
                self.progressbar_animation.play()

                self.current.gfile.replace_contents_bytes_async(
                    GLib.Bytes.new(encoded_text),
                    etag=None,
                    make_backup=False,
                    flags=Gio.FileCreateFlags.NONE,
                    cancellable=None,
                    callback=self._replace_contents_cb,
                    user_data=callback)
                
                if self.snapshot_restored:
                    self.snapshot_restored = False

        # if there's no GFile we ask for one:
        else:
            self.save_document_as(callback=callback)

    def save_document_as(self, _widget=None, _data=None, callback=None):
        """provide to the user a filechooser and save the document
           where they want. Call set_headbar_title after that
        """

        def on_response(dialog, response):
            if response == Gtk.ResponseType.ACCEPT:

                file = dialog.get_file()

                if not file.query_exists():
                    try:
                        file.create(Gio.FileCreateFlags.NONE)
                    except GLib.GError as error:
                        helpers.show_error(self, str(error.message))
                        LOGGER.warning(str(error.message))
                        return

                self.current.gfile = file

                self.update_headerbar_title(False, True)
                dialog.destroy()
                self.save_document(callback=callback)

        filefilter = Gtk.FileFilter.new()
        filefilter.add_mime_type('text/x-markdown')
        filefilter.add_mime_type('text/plain')
        filefilter.set_name('Markdown (.md)')
        self.filechooser = Gtk.FileChooserNative.new(
            _("Save your File"),
            self,
            Gtk.FileChooserAction.SAVE,
            _("Save"),
            _("Cancel")
        )
        self.filechooser.add_filter(filefilter)
        self.filechooser.set_modal(True)
        self.filechooser.set_transient_for(self)

        # / is the base path when the current file has not been saved
        if self.current.base_path != "/":
            self.filechooser.set_current_folder(Gio.File.new_for_path(self.current.base_path))

        title = self.current.title
        if not title.endswith(".md"):
            title += ".md"
        self.filechooser.set_current_name(title)

        self.filechooser.connect("response", on_response)

        self.filechooser.show()

    def _replace_contents_cb(self, gfile, result, callback=None):
        try:
            success, _etag = gfile.replace_contents_finish(result)
        except GLib.GError as error:
            LOGGER.warning(str(error.message))
            self.did_change = True
            self.progressbar_fade_out.play()
            self._set_file_monitor()
            helpers.show_error(self, str(error.message))
            return False

        if success:
            if self.progressbar_animation.get_state() == Adw.AnimationState.PAUSED:
                self.progressbar_animation.resume()

            self.update_headerbar_title()
            self.did_change = False
            # We add a 1ms delay to the call to avoid race conditions
            # see #456
            GLib.timeout_add(500, self._set_file_monitor, None, 0)
            recents_manager = Gtk.RecentManager.get_default()
            recents_manager.add_item(self.current.gfile.get_uri())
            if callback is not None:
                callback(self)

            # remove the snapshot as everything is secure on disc.
            # it'll be recreated if the document changes
            self.delete_snapshot()
        else:
            self.progressbar_fade_out.play()
            self.did_change = True
            self._set_file_monitor()

        return success

    def copy_html_to_clipboard(self, _widget=None, _date=None):
        """Copies only html without headers etc. to Clipboard
        """

        output = helpers.pandoc_convert(self.textview.get_text())
        clipboard = self.get_clipboard()
        clipboard.set(output)

    def open_document(self, _action, _value):
        """open the desired file
        """
        self.headerbar.open_menu.popdown()

        def on_response(dialog, response):
            if response == Gtk.ResponseType.ACCEPT:
                self.get_application().open([dialog.get_file()], "")

        markdown_filter = Gtk.FileFilter.new()
        markdown_filter.add_mime_type('text/markdown')
        markdown_filter.add_mime_type('text/x-markdown')
        markdown_filter.add_suffix('md')
        markdown_filter.set_name(_('Markdown Files'))

        plaintext_filter = Gtk.FileFilter.new()
        plaintext_filter.add_mime_type('text/plain')
        plaintext_filter.set_name(_('Plain Text Files'))

        self.filechooser = Gtk.FileChooserNative.new(
            _("Open a .md file"),
            self,
            Gtk.FileChooserAction.OPEN,
            _("Open"),
            _("Cancel")
        )

        self.filechooser.set_modal(True)
        self.filechooser.set_transient_for(self)

        self.filechooser.add_filter(markdown_filter)
        self.filechooser.add_filter(plaintext_filter)

        self.filechooser.connect("response", on_response)
        self.filechooser.show()

    def open_from_gvariant(self, _action, gvariant):
        self.headerbar.open_menu.popdown()
        self.get_application().open([Gio.File.new_for_uri(gvariant.get_string())], "")

    def load_file(self, file=None):
        """Open File from command line or open / open recent etc."""
        LOGGER.info("trying to open %s", file.get_uri())

        self.current.gfile = file
        self.preview_security_handler.set_file_security_level()

        if self.text_changed_handler_id:
            self.textview.get_buffer().disconnect(self.text_changed_handler_id)

        if spelling_language := file.query_info("metadata::spelling-language", Gio.FileQueryInfoFlags.NONE).get_attribute_string("metadata::spelling-language"):
            self.textview.spelling_checker.set_language(spelling_language)

        self.current.gfile.load_contents_async(None,
                                               self._load_contents_cb, None)
        self._set_file_monitor()

    @Gtk.Template.Callback()
    def reload_file(self, *args):
        self.discard_infobar.set_revealed(False)
        self.load_file(self.current.gfile)

    def _set_file_monitor(self, *args, **kwargs):
        if self.current.gfile:
            self.file_monitor = self.current.gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
            self.file_monitor.connect("changed", self._on_file_changed)

    @Gtk.Template.Callback()
    def dismiss_discard_infobar(self, dialog, response):
        if response == Gtk.ResponseType.CLOSE:
            self.discard_infobar.set_revealed(False)

    def _on_file_changed(self, file, other_file, event, *args):
        self.discard_infobar.set_revealed(True)

    def _on_spellchecking_language_changed(self, *args, **kwargs):
        if language:= self.textview.spelling_checker.get_language():
            if self.current.gfile:
                self.current.gfile.set_attribute_string("metadata::spelling-language", language, Gio.FileQueryInfoFlags.NONE)

    def _load_contents_cb(self, gfile, result, snapshot_restored=False):
        try:
            _success, contents, _etag = gfile.load_contents_finish(result)
        except GLib.GError as error:
            helpers.show_error(self, str(error.message))
            LOGGER.warning(str(error.message))
            self.new_document()
            return

        try:
            try:
                self.current.encoding = 'UTF-8'
                decoded = contents.decode(self.current.encoding)
            except UnicodeDecodeError as error:
                self.current.encoding = chardet.detect(contents)['encoding']
                if not self.current.encoding:
                    raise error
                decoded = contents.decode(self.current.encoding)
        except UnicodeDecodeError as error:
            helpers.show_error(
                self,
                _("Failed to detect encoding for file:\n"
                "{file}\n\nThe error was:\n{err_msg}")
                .format(file=gfile.get_path(),
                        err_msg=str(error).encode().decode("unicode-escape")))
            LOGGER.warning(str(error))
            self.new_document()
            return
        else:
            self.textview.set_text(decoded)

            self.preview_security_handler.assert_security_risk(decoded)
            start_iter = self.textview.get_buffer().get_start_iter()
            GLib.idle_add(
                lambda: self.textview.get_buffer().place_cursor(start_iter))

            # reconnect the text changed handler
            self.text_changed_handler_id = self.textview.get_buffer().connect('changed', self.on_text_changed)

            # add file to recents manager once it's fully loaded,
            # unless it is an internal resource
            if self.current.gfile and not self.current.gfile.get_uri().startswith("resource:"):
                recents_manager = Gtk.RecentManager.get_default()
                recents_manager.add_item(self.current.gfile.get_uri())

            self.update_headerbar_title()

            # when restoring a snapshot we want to preserve the "did_change" state
            # because we restored a dirty state, but we need to revert the snapshot_restored
            # back and show again the headerbar
            if snapshot_restored:
                self.textview.get_buffer().emit("changed")
                self.snapshot_restored = True
                self.reveal_headerbar_bottombar()

    def check_change(self,
                     callback = None):
        """Show dialog to prevent loss of unsaved changes
        """

        if self.did_change:
            dialog = Adw.AlertDialog.new(_("Save Changes?"),
                                         _("“%s” contains unsaved changes. " +
                                           "If you don’t save, " +
                                           "all your changes will be " +
                                           "permanently lost.") % self.current.title
                                         )
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("close", _("Discard"))
            dialog.add_response("save", _("Save"))
            dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("save")
            dialog.set_close_response("cancel")

            def on_response(message_dialog, response):
                match response:
                    case "cancel":
                        return
                    case "close":
                        callback(self)
                    case "save":
                        # If the saving fails, retry
                        self.save_document(callback=callback)

            dialog.connect("response", on_response)

            dialog.present(self)
        else:
            if callback is not None:
                callback(self)

    def new_document(self, *args, **kwargs):
        """create new document in the same window
        """
        def callback(self):
            self.textview.clear()
            self.text_changed_handler_id = self.textview.get_buffer().connect('changed', self.on_text_changed)

            self.did_change = False
            self.current.gfile = None
            self.preview_security_handler.set_file_security_level(PreviewSecurity.UNRESTRICTED)
            self.update_headerbar_title(False, False)

        self.check_change(callback)

    def reload_preview(self, reshow=False):
        self.preview_handler.reload(reshow=reshow)

    def open_export(self, _action, value):
        """open the export dialog
        """
        text = bytes(self.textview.get_text(), "utf-8")

        export_format = value.get_string()

        export_dialog = ExportDialog(self.current, export_format, text)
        export_dialog.export(self)

    def open_advanced_export(self, *args, **kwargs):
        """open the advanced export dialog
        """
        text = bytes(self.textview.get_text(), "utf-8")

        export_dialog = AdvancedExportDialog(self.current, text)
        export_dialog.present(self)

    def show_hemingway_toast(self, *args):
        if self.textview.buffer.hemingway_mode:
            # Only show the first three times
            count = self.settings.get_int("hemingway-toast-count")
            if count >= 3:
                return
            count += 1
            self.settings.set_int("hemingway-toast-count", count)

            self.editor.toast_overlay.add_toast(self.hemingway_toast)

    def show_hemingway_help(self, *args):
        hemingway_dialog = Gtk.Builder.new_from_resource("/org/gnome/gitlab/somas/Apostrophe/ui/AboutHemingway.ui")\
                           .get_object("dialog")
        hemingway_dialog.present(self)

    @Gtk.Template.Callback()
    def reveal_headerbar_bottombar(self, *args):
        self.editor.reveal_bottombar()

        if not self.headerbar_revealer.get_reveal_child():
            self.headerbar_revealer.set_reveal_child(True)
            self.remove_css_class("no-headerbar")

    def hide_headerbar_bottombar(self):
        if self.headerbars_breakpointbin.get_current_breakpoint() == self.headerbars_breakpoint:
            return

        if self.searchbar.search_mode_enabled or\
           self.discard_infobar.get_revealed() or\
           self.snapshot_restored:
            return

        if self.headerbar_revealer.get_reveal_child():
            self.headerbar_revealer.set_reveal_child(False)
            self.add_css_class("no-headerbar")

        self.editor.hide_bottombar()

    # TODO: this has to go
    def update_headerbar_title(self,
                               is_unsaved: bool = False,
                               has_subtitle: bool = True):
        """update headerbar title and subtitle
        """

        if is_unsaved:
            prefix = "• "
            # TODO: this doesn't really belong here
            App().inhibitor.inhibit(Gtk.ApplicationInhibitFlags.LOGOUT)
        else:
            prefix = ""
            App().inhibitor.uninhibit()

        title = prefix + self.current.title

        if has_subtitle:
            subtitle = self.current.path
        else:
            subtitle = ""

        self.set_title(title)
        self.subtitle = subtitle
        self.headerbar.set_tooltip_text(subtitle)

    def do_close_request(self, *args):
        LOGGER.info('close request called')

        if self.close_anyway:
            self.run_dispose()
            return False

        # called if check_change decides we can throw away the contents of the textview
        def callback(window):
            window.close_anyway = True
            window.do_close_request()

        self.check_change(callback)
        return True

    def autosave_snapshot(self):
        try:
            try:
                encoded_text = self.textview.get_text()\
                    .encode(self.current.encoding)
            except UnicodeEncodeError:
                encoded_text = self.textview.get_text()\
                    .encode("UTF-8")
                self.current.encoding = "UTF-8"
        except UnicodeEncodeError as error:
            LOGGER.warning(str(error.reason))
            return
        if not encoded_text:
            return
        else:
            if not self.snapshot:
                import uuid
                snapshot_dir = GLib.build_filenamev([GLib.get_user_state_dir(), "snapshots"])
                GLib.mkdir_with_parents(snapshot_dir, 0o750)
                snapshot_path = GLib.build_filenamev([snapshot_dir, uuid.uuid4().hex])
                self.snapshot = Gio.File.new_for_path(snapshot_path)
            self.snapshot.replace_contents_bytes_async(
                GLib.Bytes.new(encoded_text),
                etag=None,
                make_backup=False,
                flags=Gio.FileCreateFlags.NONE,
                cancellable=None,
                callback=self._autosave_finish)
            return True

    def _autosave_finish(self, gfile, result, callback=None):
        try:
            success, _etag = gfile.replace_contents_finish(result)
        except GLib.GError as error:
            LOGGER.warning(str(error.message))
            return
        if success and self.current.gfile and self.current.gfile.get_path():
            self.snapshot.set_attribute_string("metadata::original-path", self.current.gfile.get_path(), Gio.FileQueryInfoFlags.NONE)
        return success
    
    def load_snapshot(self, file):
        """Load a snapshot file contents while setting apostrophe's gfile to the actual user file."""
        LOGGER.info("trying to load snapshot %s", file.get_uri())

        self.snapshot = file
        if gfile_path := file.query_info("metadata::original-path", Gio.FileQueryInfoFlags.NONE).get_attribute_string("metadata::original-path"):
            self.current.gfile = Gio.File.new_for_path(gfile_path)

        self.snapshot_restored = True
        self.textview.get_buffer().disconnect(self.text_changed_handler_id)
        self.snapshot.load_contents_async(None, self._load_contents_cb, self.snapshot_restored)
        self._set_file_monitor()

    def delete_snapshot(self):
        # delete snapshots in the application in case the deletion outlives the window
        if self.autosave_timer:
            GLib.Source.remove(self.autosave_timer)
            self.autosave_timer = None
        if self.snapshot:
            self.get_application().delete_snapshot(self.snapshot)
            self.snapshot = None

    def do_dispose(self):
        self.delete_snapshot()
        super().do_dispose()

#@dataclass
class File(GObject.Object):
    """Class for keeping track of files, their attributes, and their methods"""

    __gtype_name__ = "ApostropheFile"

    security_level = GObject.Property(type=int, default=PreviewSecurity.ASK)
    _gfile = None

    @GObject.Property(type=GObject.Object, default=None)
    def gfile(self):
        return self._gfile

    @gfile.setter
    def gfile(self, file):
        if file:
            if file.is_native():
                self.path = file.get_parent().get_path()
                self.base_path = file.get_parent().get_path()
                
            else:
                self.path = file.get_parent().get_uri()
                self.base_path = "/"

            file_info = file.query_info("standard",
                                        Gio.FileQueryInfoFlags.NONE,
                                        None)
            self.title = file_info.get_attribute_as_string(
                "standard::display-name")
        else:
            self.title = _("New File")
            self.base_path = "/"

        self.name = self.title
        if self.name.endswith(".md"):
            self.name = self.name[:-3]
        self._gfile = file

    def __init__(self, gfile=None, encoding="UTF-8"):
        super().__init__()
        self._settings = Settings.new()
        self.gfile = gfile
        self.encoding = encoding
        self.path = ""
        self.base_path = "/"
        self.title = _("New File")
        self.name = ""
        self.security_level = PreviewSecurity.ASK

    def do_dispose(self):
        self.gfile = None
        super().do_dispose()
