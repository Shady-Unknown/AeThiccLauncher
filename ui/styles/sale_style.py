import re

SALE_GREEN = "#00ff88"
SALE_GREEN_LIGHT = "#66ffbb"
SALE_GREEN_DARK = "#00cc6a"

SALE_GLOW_CSS = f"""
.card-wrapper.sale-active {{
    box-shadow: 0 0 20px rgba(0,255,136,0.5);
}}
"""

class SaleStyle:
    @staticmethod
    def sale_badge_html(platform_str: str, discount_percent: int) -> str:
        discount_str = f"{discount_percent}% OFF " if discount_percent > 0 else ""
        return (
            f"<span style='color:{SALE_GREEN}; font-weight: bold;'>"
            f"🔥 {discount_str}ON SALE ON {platform_str}!</span>"
        )

    @staticmethod
    def sale_cover_banner_text(platform_str: str, discount_percent: int) -> str:
        discount_str = f"{discount_percent}% OFF " if discount_percent > 0 else ""
        return f"🔥 {discount_str}ON {platform_str}!"

    @staticmethod
    def sale_title_style() -> str:
        return f"color: {SALE_GREEN}; font-weight: bold;"

    @staticmethod
    def sale_version_style() -> str:
        return f"color: {SALE_GREEN}; font-weight: bold;"

    @staticmethod
    def greenify_headings(html_content: str) -> str:
        def _replace(match):
            tag = match.group(1)
            attrs = match.group(2) or ""
            content = match.group(3)
            style_attr = f' style="color:{SALE_GREEN}; font-weight:bold;"'
            if 'style=' in attrs:
                attrs = re.sub(r'style="[^"]*"', style_attr, attrs)
            else:
                attrs += style_attr
            return f"<{tag}{attrs}>{content}</{tag}>"

        pattern = re.compile(r'<(h[123])([^>]*)>(.*?)</\1>', re.IGNORECASE | re.DOTALL)
        return pattern.sub(_replace, html_content)

    @staticmethod
    def sale_button_glow() -> str:
        return f"""
            QPushButton#BtnSteamStore, QPushButton#BtnGogStore {{
                background-color: rgba(0, 255, 136, 0.15);
                border: none;
                border-radius: 12px;
            }}
            QPushButton#BtnSteamStore:hover, QPushButton#BtnGogStore:hover {{
                background-color: rgba(0, 255, 136, 0.3);
            }}
        """


