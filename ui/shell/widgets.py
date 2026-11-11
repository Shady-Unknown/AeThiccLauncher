"""Explicit widget registry built by ExplorerTab.

Historically ExplorerTab assigned every widget onto the AppWindow instance
(``self.mw.desc_display = ...``), which made the window an implicit global
namespace. ``WindowWidgets`` is the explicit contract: ExplorerTab fills it,
and AppWindow exposes read-only proxy properties while legacy consumers are
migrated.
"""
from __future__ import annotations


class WindowWidgets:
    __slots__ = (
        "search_input",
        "completer_model",
        "completer",
        "db_progress",
        "content_splitter",
        "sidebar_widget",
        "lib_search",
        "header_icon_labels",
        "lib_list",
        "center_panel",
        "lbl_game_title",
        "btn_play_main",
        "btn_steam_optional",
        "lbl_playtime",
        "lbl_game_version",
        "btn_version_status",
        "lbl_pegi",
        "lbl_esrb",
        "version_status_panel",
        "vs_rows",
        "vs_alert",
        "vs_subtext",
        "vs_meta",
        "btn_vs_refresh",
        "btn_vs_steamdb",
        "desc_display",
        "cover_image",
        "btn_steam_store",
        "btn_gog_store",
        "task_panel",
        "task_label",
        "task_progress",
        "btn_pause_dl",
        "btn_stop_dl",
        "req_display",
        "info_display",
        "links_scroll",
        "links_scroll_content",
        "links_container",
        "v_splitter",
        "lbl_disclaimer",
    )

    def __init__(self):
        for name in self.__slots__:
            setattr(self, name, None)
