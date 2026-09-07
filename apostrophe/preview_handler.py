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
import logging
import os
import webbrowser
from enum import IntEnum, auto
from gettext import gettext as _

import gi

from apostrophe.preview_layout_switcher import PreviewLayout
from apostrophe.preview_security import PreviewSecurity
from apostrophe.settings import Settings

gi.require_version('WebKit', '6.0')
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, WebKit, GObject

from apostrophe import config
from apostrophe.preview_converter import PreviewConverter
from apostrophe.preview_web_view import PreviewWebView

logger = logging.getLogger('apostrophe')

class Step(IntEnum):
    CONVERT_HTML = auto()
    LOAD_WEBVIEW = auto()
    RENDER = auto()


class PreviewHandler:
    """Handles showing/hiding the preview, and allows the user to toggle between modes.

    The rendering itself is handled by `PreviewRendered`. This class handles conversion/loading and
    connects it all together (including synchronization, ie. text changes, scroll)."""

    def __init__(self, window, text_view, panels):
        self.window = window.weak_ref(self.on_main_window_closed)
        self.text_view = text_view.weak_ref()
        self.panels = panels.weak_ref()

        self.web_view = None
        self.web_view_pending_html = None

        self.preview_converter = PreviewConverter()

        self.text_changed_handler_id = None

        self.settings = Settings.new()
        self.scroll_handler_id = None

        self.loading = False
        self.shown = False
        self.preview_visible = self.settings.get_boolean("preview-active")

        self.snapshot = False

        self.window().connect("notify::title", self.on_window_title_changed)

    def show(self):
        self.__show()
        self.panels().revealed = True

    def __show(self, html=None, step=Step.CONVERT_HTML, *args):

        if self.window().current.security_level not in [PreviewSecurity.RESTRICTED, PreviewSecurity.UNRESTRICTED]:
            return

        if step == Step.CONVERT_HTML:
            # First step: convert text to HTML.
            buf = self.text_view().get_buffer()

            secure_preview = self.window().current.security_level == PreviewSecurity.RESTRICTED

            self.preview_converter.convert(
                buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True),
                secure_preview,
                self.window().current.base_path,
                self.__show, Step.LOAD_WEBVIEW)

        elif step == Step.LOAD_WEBVIEW:
            # Second step: load HTML.
            if not self.web_view:
                self.web_view = PreviewWebView()
                self.web_view.get_settings().set_allow_universal_access_from_file_urls(True)
                self.web_view.get_settings().set_enable_developer_extras(config.PROFILE == '.Devel')

                # Show preview once the load is finished
                self.web_view.connect_after("load-changed", self.on_load_changed)
                self.web_view.connect("rendered", self.on_rendered)

                # All links will be opened in default browser, but local files are opened in apps.
                self.web_view.connect("decide-policy", self.on_click_link)

                self.web_view.connect("context-menu", self.on_right_click)

                # Bind the windows topbar height to the webview's top margin
                self.window().connect("notify::topbars-height", self._update_preview_top_margin)

            # only make a new screenshot if the preview is fully loaded and rendered
            if self.web_view.get_estimated_load_progress() == 1 and not self.loading:
                self.web_view.get_snapshot(
                    WebKit.SnapshotRegion.VISIBLE, WebKit.SnapshotOptions.NONE,
                    None, self.update_webview_snapshot)

            # stop syncing the scroll scale
            if self.scroll_handler_id:
                self.scroll_handler_id.unbind()
                self.scroll_handler_id = None

            if self.loading:
                self.web_view_pending_html = html
            else:
                self.loading = True
                self.web_view.load_html(html, "file://localhost/")

        elif step == Step.RENDER:
            # Last step: show the preview. This is a one-time step.
            if not self.text_changed_handler_id:
                self.text_changed_handler_id = \
                    self.text_view().get_buffer().connect("changed-debounced", self.__show)

            if not self.preview_visible:
                self.preview_visible = True

                self.__show()

    def reload(self, *_widget, reshow=False):
        if self.preview_visible:
            if reshow:
                self.hide()
            self.show()

    def refresh_preview(self, *args, **kwargs):
        if self.preview_visible:
            self.__show()

    def load_webview(self):
        if not self.window().preview_stack.get_child_by_name("webview"):
            self.window().preview_stack.add_named(self.web_view, "webview")
        self.window().preview_stack.set_visible_child(self.web_view)

    def hide(self, *args, **kwargs):
        if self.preview_visible:
            self.preview_visible = False
            self.panels().revealed = False

        if self.text_changed_handler_id:
            self.text_view().get_buffer().disconnect(self.text_changed_handler_id)
            self.text_changed_handler_id = None

        if self.scroll_handler_id:
            self.scroll_handler_id.unbind()
            self.scroll_handler_id = None

        if self.loading:
            self.loading = False
            self.panels().revealed = False

    def update_webview_snapshot(self, web_view, result):
        try:
            self.snapshot = web_view.get_snapshot_finish(result)
            self.window().webview_snapshot.set_paintable(self.snapshot)
        except Exception as e:
            logger.debug(e)

    def on_load_changed(self, web_view, event):
        if event == WebKit.LoadEvent.STARTED:
            if self.snapshot:
                # we need to change the transition to none to not bleed the loading underneath
                self.window().preview_stack.set_transition_type(Gtk.StackTransitionType.NONE)
                self.window().preview_stack.set_visible_child(self.window().webview_snapshot)
                self.window().preview_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
            self.shown = False
        elif event == WebKit.LoadEvent.FINISHED:
            if self.web_view_pending_html:
                self.__show(html=self.web_view_pending_html, step=Step.LOAD_WEBVIEW)
                self.web_view_pending_html = None

    def on_rendered(self, web_view):
        self.loading = False

        # sync scroll before showing again the preview
        if self.settings.get_boolean("sync-scroll") and not self.scroll_handler_id:
            self.scroll_handler_id = self.text_view().bind_property("scroll-scale",
                self.web_view, "scroll-scale",
                GObject.BindingFlags.DEFAULT
                | GObject.BindingFlags.SYNC_CREATE
                | GObject.BindingFlags.BIDIRECTIONAL,
            )
        if not self.shown:
            self.load_webview()
            self.shown = True

        self.__show(step=Step.RENDER)
        # update top margin
        self._update_preview_top_margin()

    def on_window_title_changed(self, *args, **kwargs):
        self.panels().panel_window_title = self.window().get_title() + " - " + _("Preview")

    def on_main_window_closed(self, *args):
        if self.panels().panel_window:
            self.panels().panel_window.destroy()
            self.panels().panel_window = None

    @staticmethod
    def on_click_link(web_view, decision, _decision_type):
        if web_view.get_uri().startswith(("http://", "https://", "www.")):
            webbrowser.open(web_view.get_uri())
            decision.ignore()
            return True

    @staticmethod
    def on_right_click(web_view, context_menu, _hit_test):
        # disable some context menu option
        for item in context_menu.get_items():
            if item.get_stock_action() in [WebKit.ContextMenuAction.RELOAD,
                                           WebKit.ContextMenuAction.GO_BACK,
                                           WebKit.ContextMenuAction.GO_FORWARD,
                                           WebKit.ContextMenuAction.STOP]:
                context_menu.remove(item)

    def _get_preview_top_margin(self):
        if self.panels().layout in [PreviewLayout.HALF_WIDTH, PreviewLayout.FULL_WIDTH]:
            return self.window().topbars_height
        else:
            return 0

    def _update_preview_top_margin(self, *args, **kwargs):
        self.web_view.top_margin = self._get_preview_top_margin()
