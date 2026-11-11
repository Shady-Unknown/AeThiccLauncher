import os
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QPixmap
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel

from core.config import get_base_dir


CARD_WIDTH = 160
COVER_W = 140
COVER_H = 196
FONT_SIZE_TITLE = 13
FONT_SIZE_META_VAL = 12
FONT_SIZE_META_HEADER = 11


class SavegameCard(QFrame):
    clicked = pyqtSignal(str)
    context_menu_requested = pyqtSignal(str)

    def __init__(self, game_title, cover_pixmap, last_save_date, last_sync_date, game_root, playtime_str="", parent=None):
        super().__init__(parent)
        self._game_root = game_root
        self.setFixedWidth(CARD_WIDTH)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self.setStyleSheet("""
            SavegameCard {
                background-color: #2b1a42;
                border: 1px solid #733d91;
                border-radius: 12px;
            }
            SavegameCard:hover {
                background-color: #3b2a52;
                border: 2px solid #ff00a0;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 8, 8, 8)

        self.title_label = QLabel(game_title)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setMaximumHeight(38)
        self.title_label.setStyleSheet(
            "color: #ffffff; font-size: {}px; font-weight: bold; "
            "border: none; background: transparent; padding: 0px;".format(FONT_SIZE_TITLE)
        )
        layout.addWidget(self.title_label)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(COVER_W, COVER_H)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setStyleSheet("border: none; background: transparent; padding: 0px;")
        if cover_pixmap and not cover_pixmap.isNull():
            scaled = cover_pixmap.scaled(
                COVER_W, COVER_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.cover_label.setPixmap(scaled)
        else:
            placeholder = self._make_placeholder(COVER_W)
            self.cover_label.setPixmap(placeholder)
        layout.addWidget(self.cover_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.lbl_save_header = QLabel("LAST SAVE")
        self.lbl_save_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_save_header.setStyleSheet(
            "color: #8a7fa4; font-size: {}px; font-weight: bold; "
            "border: none; background: transparent; padding: 0px;".format(FONT_SIZE_META_HEADER)
        )
        layout.addWidget(self.lbl_save_header)

        self.lbl_save_value = QLabel(last_save_date)
        self.lbl_save_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_save_value.setStyleSheet(
            "color: #8a7fa4; font-size: {}px; "
            "border: none; background: transparent; padding: 0px;".format(FONT_SIZE_META_VAL)
        )
        layout.addWidget(self.lbl_save_value)

        self.lbl_sync_header = QLabel("LAST SYNC")
        self.lbl_sync_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_sync_header.setStyleSheet(
            "color: #8a7fa4; font-size: {}px; font-weight: bold; "
            "border: none; background: transparent; padding: 0px;".format(FONT_SIZE_META_HEADER)
        )
        layout.addWidget(self.lbl_sync_header)

        self.lbl_sync_value = QLabel(last_sync_date)
        self.lbl_sync_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_sync_value.setStyleSheet(
            "color: #8a7fa4; font-size: {}px; "
            "border: none; background: transparent; padding: 0px;".format(FONT_SIZE_META_VAL)
        )
        layout.addWidget(self.lbl_sync_value)

        self.lbl_playtime_header = QLabel("PLAY TIME")
        self.lbl_playtime_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_playtime_header.setStyleSheet(
            "color: #8a7fa4; font-size: {}px; font-weight: bold; "
            "border: none; background: transparent; padding: 0px;".format(FONT_SIZE_META_HEADER)
        )
        self.lbl_playtime_header.setVisible(bool(playtime_str))
        layout.addWidget(self.lbl_playtime_header)

        self.lbl_playtime_value = QLabel(playtime_str)
        self.lbl_playtime_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_playtime_value.setStyleSheet(
            "color: #ff00a0; font-size: {}px; font-weight: bold; "
            "border: none; background: transparent; padding: 0px;".format(FONT_SIZE_META_VAL)
        )
        self.lbl_playtime_value.setVisible(bool(playtime_str))
        layout.addWidget(self.lbl_playtime_value)

    def _make_placeholder(self, target_w):
        logo_path = os.path.join(get_base_dir(), ".media", "AeThiccLauncherLogoBig.png")
        if os.path.exists(logo_path):
            px = QPixmap(logo_path)
            if not px.isNull():
                aspect_ratio = px.height() / px.width() if px.width() else 1
                target_h = int(target_w * aspect_ratio)
                return px.scaled(
                    target_w, target_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
        return QPixmap()

    @property
    def game_root(self):
        return self._game_root

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._game_root)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        self.context_menu_requested.emit(self._game_root)
        event.accept()
