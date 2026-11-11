import os
import html
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsDropShadowEffect
from ui.styles.sale_style import SALE_GREEN
from core.config import get_base_dir
from core.cover_cache import to_file_url


GOLD_PRIMARY = "#FFD700"
GOLD_LIGHT = "#FFE44D"
GOLD_DARK = "#B8860B"
GOLD_GRADIENT = "linear-gradient(135deg, #FFD700 0%, #FFA500 50%, #FFD700 100%)"

GOLD_SALE_CSS = f"""
.card-wrapper.sale-active {{
    background: linear-gradient(135deg, {GOLD_PRIMARY} 0%, {SALE_GREEN} 50%, {GOLD_PRIMARY} 100%) !important;
}}
.card-wrapper.sale-active .cover-glow {{
    background: linear-gradient(135deg, {GOLD_PRIMARY} 0%, {SALE_GREEN} 50%, {GOLD_PRIMARY} 100%) !important;
}}
"""

#goldtier
class GoldTierStyle:
    @staticmethod
    def gold_cover_html(cover_urls, background_dark_purple, update_ribbon_html="", sale_active=False):
        wrapper_class = "card-wrapper" + (" sale-active" if sale_active else "")
        placeholder = to_file_url(os.path.join(get_base_dir(), '.media', 'cover_placeholder.png'))
        def _js_quote(u):
            return "'" + u.replace("\\", "\\\\").replace("'", "\\'") + "'"
        first_url = html.escape(cover_urls[0], quote=True) if cover_urls else ""
        urls_array = [_js_quote(url) for url in cover_urls]
        urls_array.append(_js_quote(placeholder))
        urls_js = f"var urls = [{', '.join(urls_array)}];"

        return f"""
<html>
<head>
<style>
    body {{
        margin: 0; padding: 0;
        background-color: transparent;
        display: flex; justify-content: center; align-items: center;
        overflow: hidden; height: 100vh;
    }}
    .card-wrapper {{
        width: 85%;
        height: 88%;
        max-width: 240px;
        max-height: 360px;
        box-sizing: border-box;
        position: relative;
        border-radius: 10px;
        background: {GOLD_PRIMARY};
        padding: 3px;
    }}
    .cover-glow {{
        position: absolute;
        top: -3px; left: -3px; right: -3px; bottom: -3px;
        border-radius: 10px;
        background: {GOLD_PRIMARY};
        filter: blur(6px);
        opacity: 0.55;
        z-index: 0;
    }}
    .cover-art {{
        position: absolute;
        top: 3px; left: 3px; right: 3px; bottom: 3px;
        border-radius: 7px;
        background-size: cover;
        background-position: center center;
        background-repeat: no-repeat;
        background-color: {background_dark_purple};
        z-index: 1;
    }}
    .cover-preload {{ display: none; }}
    .gold-ribbon {{
        position: absolute;
        top: 0; right: 0;
        width: 120px; height: 120px;
        overflow: hidden;
        z-index: 10;
        pointer-events: none;
    }}
    .gold-ribbon::before {{
        content: "GOLD";
        position: absolute;
        top: 22px; right: -30px;
        background: {GOLD_GRADIENT};
        color: #1a0f2b;
        padding: 4px 40px;
        transform: rotate(45deg);
        font-weight: bold;
        font-size: 12px;
        letter-spacing: 2px;
        box-shadow: 0 2px 12px rgba(255, 215, 0, 0.7);
        text-align: center;
        width: 120px;
    }}
    .glare {{
        position: absolute;
        top: 0; left: 0; right: 0; bottom: 0;
        background: linear-gradient(
            135deg,
            transparent 30%,
            rgba(255, 255, 255, 0.3) 50%,
            transparent 70%
        );
        opacity: 0;
        transition: opacity 0.4s ease;
        pointer-events: none;
        z-index: 5;
        border-radius: 7px;
    }}
    .card-wrapper:hover .glare {{
        opacity: 1;
    }}
    .ribbon {{
        position: absolute; top: 25px; left: -35px;
        background: {GOLD_PRIMARY};
        color: #1a0f2b;
        padding: 5px 40px;
        transform: rotate(-45deg);
        font-weight: bold; font-size: 14px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.8);
        z-index: 11; letter-spacing: 1px; text-align: center;
    }}
    {GOLD_SALE_CSS}
</style>
<script>
    {urls_js}
    var currentIdx = 0;
    function setCover(url) {{
        var el = document.getElementById('coverArt');
        if (el && url) el.style.backgroundImage = "url('" + String(url).replace(/'/g, "%27") + "')";
    }}
    function loadNext(img) {{
        currentIdx++;
        console.log('COVER: onerror fired for', img.src, '-> fallback', currentIdx);
        if(currentIdx < urls.length) {{
            console.log('COVER: trying fallback URL:', urls[currentIdx]);
            img.src = urls[currentIdx];
        }} else {{
            console.log('COVER: no more fallback URLs');
        }}
    }}
</script>
</head>
<body>
    <div class="{wrapper_class}">
        <div class="cover-glow"></div>
        <div class="gold-ribbon"></div>
        <div class="glare"></div>
        {update_ribbon_html}
        <div class="cover-art" id="coverArt" style="background-image: url('{first_url}');"></div>
        <img class="cover-preload" src="{first_url}" onload="setCover(this.src)" onerror="loadNext(this)" alt="">
    </div>
</body>
</html>
"""

    @staticmethod
    def gold_download_glow():
        effect = QGraphicsDropShadowEffect()
        effect.setBlurRadius(20)
        effect.setColor(QColor(GOLD_PRIMARY))
        effect.setOffset(0, 0)
        return effect

    @staticmethod
    def gold_download_button_qss(theme_data):
        return f"""
            QPushButton {{
                text-align: left; padding: 12px;
                background-color: {theme_data['background_dark_purple']};
                border: 2px solid transparent;
                border-image: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {GOLD_PRIMARY}, stop:1 {GOLD_LIGHT}) 1 stretch;
                font-weight: bold; color: {GOLD_LIGHT};
                margin: 8px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 215, 0, 0.15);
                color: {GOLD_PRIMARY};
            }}
        """

    @staticmethod
    def gold_panel_stylesheet():
        return f"""
            QWidget#CentralContentPanel {{
                background-color: transparent;
                border-radius: 8px;
                border: 2px solid {GOLD_PRIMARY};
            }}
            QTextEdit {{
                background-color: transparent;
                border: 1px solid {GOLD_PRIMARY};
                border-radius: 8px;
                padding: 12px;
                font-size: 13px;
                color: #ffffff;
            }}
            QListWidget#LibraryList {{
                background-color: transparent;
                border: 1px solid {GOLD_PRIMARY};
                border-radius: 6px;
                padding: 5px;
            }}
            QProgressBar {{
                border: 1px solid {GOLD_PRIMARY};
                border-radius: 5px;
                text-align: center;
                color: white;
                font-weight: bold;
                background: transparent;
            }}
            QProgressBar::chunk {{
                background-color: {GOLD_PRIMARY};
                border-radius: 4px;
            }}
        """

    @staticmethod
    def gold_version_style():
        return f"color: {GOLD_PRIMARY}; font-weight: bold;"

    @staticmethod
    def why_gold_html(reason: str) -> str:
        return f"""
            <h2 style="color:{GOLD_PRIMARY}; font-weight:bold; margin-top:16px; margin-bottom:8px; border-bottom: 1px solid {GOLD_PRIMARY}; padding-bottom: 4px;">
                Why Gold?
            </h2>
            <p style="color:#e0e0e0; font-size:13px; line-height:1.5;">
                {reason}
            </p>
        """
