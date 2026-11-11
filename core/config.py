# style_settings.py
import json
import os
import sys

CONFIG_FILE = "config.json"

DEFAULT_PURPLE_RIP_THEME = {
    "main_background_color": "#120a1c",   # Sfondo globale scuro
    "main_accent_color": "#733d91",       # Viola SteamRIP
    "bright_neon_accent": "#ff00a0",      # Rosa/Magenta Neon
    "background_dark_purple": "#1a0f2b",  # Deep Purple Background
    "surface_panel_purple": "#2b1a42",    # Pannelli centrali e schede
    "input_bg_light_pink": "#f9f2ff",     # Campi di testo chiari
    "text_primary_white": "#ffffff",
    "text_secondary_pink": "#f3c6ff",
    "progress_bar_color": "#8d0bb1",
    "neon_gradient_start": "#8d0bb1",     # NUOVO: Viola Neon per il gradiente
    "neon_gradient_end": "#00d2ff",        # NUOVO: Azzurro Cyberpunk per il gradiente
    "status_green": "#00ff88",   # Verde neon per i download attivi
    "status_yellow": "#ffcc00",  # Giallo per la pausa
    "status_red": "#ff00a0",     # Rosso/Magenta per gli errori
    "text_muted": "#8a7fa4",     # Grigio/Lilla per i testi secondari (Info, Space)
}

DEFAULT_ORANGE_CYBER_THEME = {
    "main_background_color": "#000000",
    "main_accent_color": "#be4c00",
    "bright_neon_accent": "#ff7300",
    "background_dark_purple": "#000000",
    "surface_panel_purple": "#000000",
    "input_bg_light_pink": "#f9f2ff",
    "text_primary_white": "#ffffff",
    "text_secondary_pink": "#ffa358",
    "progress_bar_color": "#b55400",
    "neon_gradient_start": "#00fff7",
    "neon_gradient_end": "#ff6a00",
    "status_green": "#23b100",
    "status_yellow": "#ffcc00",
    "status_red": "#9d0003",
    "text_muted": "#a46d00"
}

DEFAULT_THEMES = {
    "PurpleRip": DEFAULT_PURPLE_RIP_THEME,
    "OrangeCyber": DEFAULT_ORANGE_CYBER_THEME,
}

DEFAULT_CONFIG = {
    "theme": DEFAULT_PURPLE_RIP_THEME,        # Tema attivo (dict piatto, per compatibilità)
    "themes": DEFAULT_THEMES,                   # Registro dei temi per nome
    "current_theme": "PurpleRip",
    "onboarding": {                             # Primo avvio / setup wizard
        "completed": False,
        "version": 1,
    },
    "watched_folders": [],                    # Librerie giochi gestite
    "default_library": "",                    # Libreria preselezionata per le installazioni
    "savegames": {
        "sync_folder": "",
        "sync_interval": 30,
        "sync_unit": "minutes"
    },
    "auto_extract": False,
    "default_temp_folder": "",                # Sezione cartella temporanea
    "playtime": {
        "auto_sync": True,                    # Sincronizza automaticamente le ore con Steam
        "display": "hours"                    # "hours" (12.4h) oppure "timestamp" (12h 24m)
    },
    "stats": {
        "region": "US",                       # ISO country code used for Steam prices
        "range": "90d",                       # 30d | 90d | 365d | all
        "cinema_rate": 6.0,                   # cost per hour used by the phrase engine
        "library_sort": "installed_first",    # Stats library ordering
        "library_installed_only": False       # Hide non-installed games
    },
    "ui": {
        "explorer_blur": 0,                   # 0..100: sfocatura sfondo Explorer
    }
}

def get_base_dir():
    # Siccome la cartella bin/ e .media/ verranno distribuite a corredo dell'eseguibile,
    # in ambiente compilato usiamo la cartella dove risiede l'exe.
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _deep_merge(base, override):
    """Merge ``override`` into ``base`` without losing new default keys.

    Dicts are merged recursively; every other value (including lists) is
    replaced by the user value. This keeps old configs compatible when a
    release adds a new key inside an existing section (the old code only
    merged top-level dicts one level deep).
    """
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    return override


def load_config():
    from core.paths import PathManager
    # Creiamo una copia profonda dei default come base di partenza in RAM
    config = json.loads(json.dumps(DEFAULT_CONFIG))

    # Usiamo PathManager per ottenere il percorso della configurazione
    config_path = str(PathManager.get_config_dir() / CONFIG_FILE)

    # Upgrade with a new data root: if the new location has no config yet, start
    # from the legacy one so libraries/theme/settings survive. The wizard (or
    # the next save) writes the merged copy to the new root.
    source_path = config_path
    if not os.path.exists(source_path):
        legacy_path = str(PathManager.get_legacy_root() / "config" / CONFIG_FILE)
        if legacy_path != config_path and os.path.exists(legacy_path):
            source_path = legacy_path

    if os.path.exists(source_path):
        try:
            with open(source_path, "r", encoding="utf-8-sig") as f:
                user_config = json.load(f)

                # Merge ricorsivo: i default nuovi restano, le scelte utente vincono.
                config = _deep_merge(config, user_config)
        except Exception as e:
            print(f"[Config] Error loading {source_path}: {e}")

    # --- MIGRAZIONE TEMI: normalizza il registro "themes" ---
    user_had_themes = isinstance(user_config.get("themes"), dict) and user_config["themes"] if 'user_config' in locals() else False
    themes = config.get("themes")
    if not isinstance(themes, dict) or not themes:
        # Nessun registro: usiamo il tema piatto (default o utente) come unico tema
        config["themes"] = {"PurpleRip": config.get("theme", json.loads(json.dumps(DEFAULT_PURPLE_RIP_THEME)))}
    elif not user_had_themes:
        # Vecchio config: salva il tema piatto modificato dall'utente nel registro
        active = config.get("current_theme", "PurpleRip")
        if active not in config["themes"]:
            active = next(iter(config["themes"]))
            config["current_theme"] = active
        config["themes"][active] = config.get("theme", json.loads(json.dumps(DEFAULT_PURPLE_RIP_THEME)))
    current = config.get("current_theme")
    if not current or current not in config["themes"]:
        config["current_theme"] = next(iter(config["themes"]))
    # "theme" rispecchia SEMPRE il tema attivo (dict piatto per il resto dell'app)
    config["theme"] = config["themes"][config["current_theme"]]

    return config

def save_config(config_data):
    # --- ONBOARDING PROTECTION ---
    # We DO NOT create the file on disk if the user hasn't added at least one watched folder.
    # This ensures the Onboarding Dialog will appear again on next launch if they skipped it.
    if not config_data.get("watched_folders"):
        print("[Config] Nessuna cartella monitorata. Salvataggio su disco annullato per permettere l'onboarding.")
        return

    # Allineiamo il salvataggio al caricamento, usando PathManager
    from core.paths import PathManager
    config_dir = PathManager.get_config_dir()
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = str(config_dir / CONFIG_FILE)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4)
    except Exception as e:
        print(f"Error saving config: {e}")

def get_stylesheet(theme_data):
    main_bg = theme_data.get('main_background_color', theme_data.get('background_dark_purple', '#1a0f2b'))
    return f"""
        QMainWindow, QDialog {{ 
            background-color: {main_bg}; 
        }}
        QWidget {{ 
            color: {theme_data.get('text_primary_white', '#ffffff')}; 
            font-family: 'Segoe UI', Arial; 
        }}
        
        QScrollArea, QListWidget, QTreeWidget {{
            background-color: transparent;
            border: none;
        }}
        
        QSplitter::handle {{
            background-color: {theme_data.get('surface_panel_purple', '#2b1a42')};
        }}
        
        
        /* Dropdowns, SpinBox, ComboBox e Header */
        QHeaderView::section {{
            background-color: {theme_data.get('surface_panel_purple', '#2b1a42')};
            color: {theme_data.get('text_primary_white', '#ffffff')};
            padding: 4px;
            border: 1px solid {theme_data.get('main_accent_color', '#733d91')};
        }}
        
        QToolTip {{
            background-color: {main_bg};
            color: {theme_data.get('text_primary_white', '#ffffff')};
            border: 1px solid {theme_data.get('main_accent_color', '#733d91')};
            padding: 4px;
        }}
        
        QComboBox, QSpinBox {{
            background-color: {theme_data['input_bg_light_pink']};
            color: #1a0f2b;
            border: 1px solid {theme_data.get('surface_panel_purple', '#2b1a42')};
            border-radius: 4px;
            padding: 3px 8px;
            font-weight: bold;
        }}
        
        QComboBox::drop-down {{
            border: none;
        }}
        
        QComboBox QAbstractItemView {{
            background-color: {main_bg};
            color: {theme_data.get('text_primary_white', '#ffffff')};
            selection-background-color: {theme_data.get('surface_panel_purple', '#2b1a42')};
            selection-color: {theme_data.get('text_primary_white', '#ffffff')};
        }}
        
        QAbstractItemView {{
            background-color: {main_bg};
            color: {theme_data.get('text_primary_white', '#ffffff')};
            selection-background-color: {theme_data.get('surface_panel_purple', '#2b1a42')};
        }}
        
        /* Input Box */
        QLineEdit {{ 
            background-color: {theme_data['input_bg_light_pink']}; 
            border: 1px solid {theme_data['surface_panel_purple']}; /* Bordo molto più delicato */
            padding: 3px 8px; /* Padding ridotto per abbassare l'altezza */
            border-radius: 4px; 
            color: #1a0f2b;
            font-size: 11px; /* Font rimpicciolito */
            font-weight: bold;
        }}
        QLineEdit:focus {{
            border: 1px solid {theme_data['main_accent_color']}; /* Feedback visivo leggero quando scrivi */
        }}
        
        /* Bottoni */
        QPushButton {{ 
            background-color: {theme_data['main_accent_color']}; 
            border: 1px solid {theme_data['bright_neon_accent']}; 
            padding: 8px 16px; 
            border-radius: 5px; 
            color: #ffffff; 
            font-weight: bold; 
        }}
        QPushButton:hover {{ 
            background-color: {theme_data['bright_neon_accent']}; 
            border-color: #ffffff;
        }}
        
        /* Contenitore centrale (Sempre tonalità di viola) */
        QWidget#CentralContentPanel {{
            background-color: {theme_data['surface_panel_purple']};
            border-radius: 8px;
            border: 1px solid {theme_data['main_accent_color']};
        }}
        
        QLabel#GameTitle {{ 
            font-size: 24px;  
            font-weight: bold; 
            color: #ffffff;
            padding-top: 5px;    
            padding-bottom: 5px; 
        }}
        QLabel#GameVersion {{ 
            color: #cba6d1; /* Eredità globale del rosa desaturato */
            font-weight: bold; 
            font-size: 14px; /* Via di mezzo: Titolo è 24px, Testo normale è 13px */
            margin-bottom: 5px; 
            margin-top: 2px;
        }}
        QLabel#LinksTitle {{ 
            font-size: 15px; 
            font-weight: bold; 
            margin-top: 10px; 
            color: {theme_data['text_secondary_pink']}; 
        }}
        
        QTextEdit {{ 
            background-color: {theme_data['background_dark_purple']}; 
            border: 1px solid {theme_data['main_accent_color']}; 
            border-radius: 8px; 
            padding: 12px; 
            font-size: 13px;
            color: #ffffff;
        }}
        
        /* --- BARRA DEI MENU PRINCIPALE (Stile Minimal/Nativo) --- */
        QMenuBar {{ 
            background-color: transparent; 
            padding: 2px 0px;
            border-bottom: 1px solid {theme_data['surface_panel_purple']}; /* Bordo finissimo e discreto */
            font-size: 12px; /* Testo più piccolo */
        }}
        QMenuBar::item {{
            background-color: transparent; /* Niente effetto bottone a riposo */
            padding: 4px 10px;
            border-radius: 4px; /* Smussatura solo per l'hover */
            margin: 2px 4px;
        }}
        QMenuBar::item:selected {{ 
            background-color: {theme_data['surface_panel_purple']}; /* Grigio scuro/viola per l'hover */
            color: {theme_data['text_primary_white']};
        }}
        
        /* --- DROPDOWN MENUS --- */
        QMenu {{
            background-color: {theme_data['background_dark_purple']}; /* Sfondo scuro unificato */
            color: {theme_data['text_secondary_pink']};
            border: none; /* Eliminato il bordo duro aggressivo */
            padding: 2px 0px; /* Dropdown ultra compatto in altezza */
        }}
        QMenu::item {{
            /* Padding drasticamente ridotto (3px sopra/sotto, meno spazio laterale) */
            padding: 3px 20px 3px 12px; 
            background-color: transparent;
            font-size: 11px; /* Font interno leggermente più piccolo e discreto */
        }}
        QMenu::item:selected {{
            /* Travaso dello stile della libreria: superficie chiara quando selezionato */
            background-color: {theme_data['surface_panel_purple']}; 
            color: {theme_data['text_primary_white']};
            border-radius: 2px;
        }}
        QMenu::separator {{
            height: 1px;
            background: {theme_data['surface_panel_purple']};
            margin: 2px 6px;
        }}
        
        /* TAB WIDGET CUSTOMIZATION - segmented, same language as Stats sub-tabs */
        QTabWidget::pane {{
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 4px;
            background: transparent;
            top: 0px;
        }}
        QTabBar {{
            background: transparent;
        }}
        QTabBar::tab {{
            background: transparent;
            color: {theme_data['text_muted']};
            padding: 6px 14px;
            margin-right: 6px;
            border: 1px solid {theme_data['main_accent_color']};
            border-radius: 6px;
            font-weight: bold;
            font-size: 12px;
        }}
        QTabBar::tab:selected {{
            background: {theme_data['bright_neon_accent']};
            color: #ffffff;
            border-color: {theme_data['bright_neon_accent']};
        }}
        QTabBar::tab:hover:!selected {{
            color: {theme_data['text_primary_white']};
            border-color: {theme_data['bright_neon_accent']};
            background: rgba(255, 255, 255, 0.04);
        }}
        QTabBar::tab:last {{
            margin-right: 0px;
        }}
        
    """