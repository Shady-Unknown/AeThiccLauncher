"""Tiny phrase engine for the Stats card.

Data lives in ``data/stats_phrases.json``; the engine only formats safe
placeholders and rotates phrases so the same line does not appear twice in a
row. No network, no randomness at import time.
"""
from __future__ import annotations

import random
import time
from collections import deque

from core.data_files import load_json
from core.price_utils import format_cents

DEFAULT_PHRASES = [
    {
        "id": "generic_default",
        "category": "generic",
        "when": {},
        "text": "{hours}h logged across {sessions} sessions. Keep going.",
    },
    {
        "id": "generic_default_2",
        "category": "generic",
        "when": {},
        "text": "{games} games in the library. {played} have been played; {never} are still waiting.",
    },
]

# Reference constants live in JSON too, so they can be updated without touching
# code. The defaults below keep the engine usable when a user copy is partial.
DEFAULT_REFS = {
    "portal_hours": 3.0,
    "lotr_extended_hours": 11.4,  # 683 minutes, extended trilogy
    "skyrim_main_hours": 34.0,
    "elden_ring_main_hours": 58.0,
    "cake_hours": 1.0,
    "rohirrim_budget_cents": 3_000_000_000,       # $30M
    "gta_v_budget_cents": 26_500_000_000,         # $265M
    "cyberpunk_budget_cents": 44_190_000_000,     # $441.9M
    "star_citizen_budget_cents": 88_500_000_000,  # $885M
    "undertale_budget_cents": 5_112_400,          # $51,124 Kickstarter
    "hardware": {
        "steam_deck_oled_cents": 78_900,  # $789
        "switch_2_cents": 49_999,         # $499.99
    },
    "fx_to_usd": {
        "USD": 1.0,
        "EUR": 1.08,
        "GBP": 1.27,
        "CAD": 0.73,
        "AUD": 0.66,
        "PLN": 0.25,
        "JPY": 0.0067,
        "CNY": 0.14,
        "KRW": 0.00072,
        "BRL": 0.18,
        "MXN": 0.055,
        "INR": 0.012,
        "CHF": 1.12,
        "SEK": 0.095,
        "NOK": 0.093,
        "DKK": 0.145,
        "TRY": 0.028,
        "RUB": 0.011,
        "UAH": 0.024,
        "ZAR": 0.055,
    },
}

CURRENCY_NAMES = {
    "USD": "dollar",
    "EUR": "euro",
    "GBP": "pound",
    "JPY": "yen",
    "CNY": "yuan",
    "KRW": "won",
    "CAD": "Canadian dollar",
    "AUD": "Australian dollar",
    "BRL": "real",
    "MXN": "peso",
    "INR": "rupee",
    "PLN": "zloty",
    "SEK": "krona",
    "NOK": "krone",
    "DKK": "krone",
    "CHF": "franc",
    "TRY": "lira",
    "RUB": "ruble",
    "UAH": "hryvnia",
    "ZAR": "rand",
}

CATEGORY_LABELS = {
    "cost_per_hour": "value per hour",
    "hours_per_dollar": "hours per buck",
    "value_vs_cinema": "wallet check",
    "still_sealed": "still sealed",
    "marathon": "marathon",
    "night_owl": "night owl",
    "streak": "streak",
    "library_value": "library value",
    "genre_devotion": "genre devotion",
    "game_generic": "about this game",
    "nerd_hours": "time warp",
    "nerd_value": "nerd math",
    "generic": "from the launcher",
}


def _merge_refs(payload):
    refs = {
        key: (dict(value) if isinstance(value, dict) else value)
        for key, value in DEFAULT_REFS.items()
    }
    incoming = payload.get("refs") if isinstance(payload, dict) else None
    if isinstance(incoming, dict):
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(refs.get(key), dict):
                refs[key].update(value)
            else:
                refs[key] = value
    return refs


class StatsPhrases:
    def __init__(self, data=None, seed=None):
        payload = data
        if payload is None:
            payload = load_json("stats_phrases.json", {})
        phrases = payload.get("phrases") if isinstance(payload, dict) else None
        if not isinstance(phrases, list):
            phrases = DEFAULT_PHRASES
        self._phrases = [
            phrase for phrase in phrases
            if isinstance(phrase, dict) and phrase.get("text") and phrase.get("id")
        ] or list(DEFAULT_PHRASES)
        self._refs = _merge_refs(payload)
        self._rng = random.Random(seed)
        self._recent = deque(maxlen=3)

    # ------------------------------------------------------------------ public

    def next(self, snapshot: dict, context: dict | None = None,
             category: str | None = None, game: dict | None = None) -> dict:
        raw, fmt = self._context(snapshot, context or {}, game=game)
        if not category:
            category = self._pick_category(raw)

        candidates = self._candidates(category, raw)
        if not candidates and category != "generic":
            candidates = self._candidates("generic", raw)
        if not candidates:
            candidates = [p for p in self._phrases if self._valid(p, raw)]
        if not candidates:
            candidates = [p for p in DEFAULT_PHRASES if self._valid(p, raw)]
        if not candidates:
            candidates = list(DEFAULT_PHRASES)

        phrase = self._choose(candidates)
        text = self._format(phrase.get("text", ""), fmt)
        if not text:
            for candidate in candidates:
                text = self._format(candidate.get("text", ""), fmt)
                if text:
                    phrase = candidate
                    break
        if not text:
            for candidate in DEFAULT_PHRASES:
                text = self._format(candidate.get("text", ""), fmt)
                if text:
                    phrase = candidate
                    break
        if not text:
            text = "No phrase available yet."

        self._recent.append(phrase.get("id"))
        return {
            "id": phrase.get("id", ""),
            "category": phrase.get("category", "generic"),
            "category_label": CATEGORY_LABELS.get(phrase.get("category", "generic"), "from the launcher"),
            "text": text,
        }

    # ------------------------------------------------------------------ context

    @staticmethod
    def _top_game(snapshot):
        games = snapshot.get("top_games") or snapshot.get("games") or []
        return games[0] if games and isinstance(games[0], dict) else {}

    def _ref_float(self, key, default):
        try:
            return float(self._refs.get(key, default) or default)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _num(value):
        if value is None:
            return ""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ""
        if number >= 1000:
            text = f"{number:,.0f}"
        elif number >= 100:
            text = f"{number:.0f}"
        else:
            text = f"{number:.1f}"
        return text[:-2] if text.endswith(".0") else text

    @staticmethod
    def _pct(value):
        if value is None:
            return ""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ""
        if number <= 0:
            return "0"
        if number < 0.01:
            return "under 0.01"
        if number < 1:
            return f"{number:.2f}"
        if number < 10:
            return f"{number:.1f}"
        return f"{number:.0f}"

    @staticmethod
    def _short_usd(cents):
        try:
            value = float(cents) / 100.0
        except (TypeError, ValueError):
            return ""
        if value >= 1_000_000_000:
            return f"${value / 1_000_000_000:.1f}B".replace(".0B", "B")
        if value >= 1_000_000:
            return f"${value / 1_000_000:.1f}M".replace(".0M", "M")
        if value >= 1_000:
            return f"${value:,.0f}"
        return f"${value:,.2f}".rstrip("0").rstrip(".")

    def _context(self, snapshot, override, game=None):
        hero = snapshot.get("hero") or {}
        finance = snapshot.get("finance") or {}
        top = game if isinstance(game, dict) and game else self._top_game(snapshot)
        games = snapshot.get("games") or []
        sealed = (
            top if top.get("installed") and float(top.get("hours_total") or 0) <= 0
            else next(
                (candidate for candidate in games
                 if candidate.get("installed")
                 and float(candidate.get("hours_total") or 0) <= 0),
                {},
            )
        )
        genre_entry = (snapshot.get("genres") or [{}])[0]
        longest = hero.get("longest_session") or {}
        currency = (
            override.get("currency")
            or top.get("price_currency")
            or next((g.get("price_currency") for g in games if g.get("price_currency")), "")
            or "USD"
        )
        currency_code = str(currency or "USD").upper()
        price_cents = top.get("price_cents")
        price_positive = price_cents is not None and int(price_cents) > 0
        cost_per_hour = top.get("cost_per_hour")
        cinema_rate = float(override.get("cinema_rate") or 6.0)
        end_time = "--:--"
        if longest.get("end"):
            try:
                end_time = time.strftime("%H:%M", time.localtime(int(longest["end"])))
            except (TypeError, ValueError, OSError):
                end_time = "--:--"

        hours_total = float(hero.get("hours_total") or 0)
        game_hours = float(top.get("hours_total") or 0)
        library_value_cents = int(
            hero.get("library_value_cents")
            or finance.get("library_value_cents")
            or 0
        )
        unplayed_value_cents = int(finance.get("unplayed_value_cents") or 0)

        price_units = (price_cents / 100.0) if price_positive else None
        hours_per_dollar = (
            game_hours / price_units
            if price_units and game_hours > 0 else None
        )
        minutes_per_dollar = hours_per_dollar * 60.0 if hours_per_dollar is not None else None
        cinema_multiple = (
            hours_per_dollar * cinema_rate
            if hours_per_dollar is not None else None
        )
        cinema_minutes = (60.0 / cinema_rate) if cinema_rate > 0 else 60.0

        played_priced = [
            candidate for candidate in games
            if candidate.get("price_cents") is not None
            and int(candidate.get("price_cents") or 0) > 0
            and float(candidate.get("hours_total") or 0) > 0
        ]
        known_hours = sum(float(candidate.get("hours_total") or 0) for candidate in played_priced)
        known_value_cents = sum(int(candidate.get("price_cents") or 0) for candidate in played_priced)
        library_hours_per_dollar = (
            known_hours / (known_value_cents / 100.0)
            if known_value_cents > 0 and known_hours > 0 else None
        )
        cinema_multiple_library = (
            library_hours_per_dollar * cinema_rate
            if library_hours_per_dollar is not None else None
        )

        copies_of_game = (
            library_value_cents / price_cents
            if price_positive and library_value_cents > 0 else None
        )
        backlog_copies = (
            unplayed_value_cents / price_cents
            if price_positive and unplayed_value_cents > 0 else None
        )
        unplayed_share = (
            unplayed_value_cents / library_value_cents * 100.0
            if library_value_cents > 0 else 0.0
        )

        fx_map = self._refs.get("fx_to_usd") or {}
        try:
            fx = float(fx_map.get(currency_code, 1.0) or 1.0)
        except (TypeError, ValueError):
            fx = 1.0
        if fx <= 0:
            fx = 1.0
        usd_library_cents = library_value_cents * fx

        hardware = self._refs.get("hardware") or {}
        steam_deck_cents = int(hardware.get("steam_deck_oled_cents") or 0)
        switch2_cents = int(hardware.get("switch_2_cents") or 0)
        steam_deck_units = (
            usd_library_cents / steam_deck_cents
            if steam_deck_cents > 0 and usd_library_cents > 0 else None
        )
        switch2_units = (
            usd_library_cents / switch2_cents
            if switch2_cents > 0 and usd_library_cents > 0 else None
        )

        def budget_percent(key):
            budget = int(self._refs.get(key) or 0)
            if budget <= 0 or usd_library_cents <= 0:
                return 0.0
            return usd_library_cents / budget * 100.0

        rohirrim_budget_cents = int(self._refs.get("rohirrim_budget_cents") or 0)
        gta_v_budget_cents = int(self._refs.get("gta_v_budget_cents") or 0)
        cyberpunk_budget_cents = int(self._refs.get("cyberpunk_budget_cents") or 0)
        star_citizen_budget_cents = int(self._refs.get("star_citizen_budget_cents") or 0)
        undertale_budget_cents = int(self._refs.get("undertale_budget_cents") or 0)

        def runs(hours, reference):
            if reference <= 0 or hours <= 0:
                return None
            return hours / reference

        portal_hours = self._ref_float("portal_hours", 3.0)
        lotr_hours = self._ref_float("lotr_extended_hours", 11.4)
        skyrim_hours = self._ref_float("skyrim_main_hours", 34.0)
        elden_hours = self._ref_float("elden_ring_main_hours", 58.0)
        cake_hours = self._ref_float("cake_hours", 1.0)

        raw = {
            "hours_total": hours_total,
            "never": int(hero.get("games_never_played") or 0),
            "session_minutes": float(longest.get("minutes") or 0),
            "night_percent": float(hero.get("night_owl_percent") or 0),
            "streak": int(hero.get("current_streak") or 0),
            "library_value_cents": library_value_cents,
            "genre_hours": float(genre_entry.get("hours") or 0),
            "price_known": price_cents is not None,
            "price_positive": price_positive,
            "cost_per_hour": cost_per_hour,
            "hours_per_dollar": hours_per_dollar,
            "minutes_per_dollar": minutes_per_dollar,
            "cinema_multiple": cinema_multiple,
            "library_hours_per_dollar": library_hours_per_dollar,
            "cinema_multiple_library": cinema_multiple_library,
            "steam_deck_units": steam_deck_units,
            "switch2_units": switch2_units,
            "copies_of_game": copies_of_game,
            "backlog_copies": backlog_copies,
            "unplayed_value_cents": unplayed_value_cents,
            "game_hours": game_hours,
            "game_installed": bool(top.get("installed")),
            "selected": bool(game),
        }
        fmt = {
            "hours": f"{raw['hours_total']:.1f}",
            "tracked": f"{float(hero.get('hours_tracked') or 0):.1f}",
            "imported": f"{float(hero.get('hours_imported') or 0):.1f}",
            "sessions": int(hero.get("sessions") or 0),
            "games": int(hero.get("games_installed") or 0),
            "played": int(hero.get("games_played") or 0),
            "never": raw["never"],
            "game": top.get("title") or "your library",
            "sealed_game": sealed.get("title") or "A game in your library",
            "hours_game": f"{game_hours:.1f}",
            "price": format_cents(price_cents, currency_code) if price_cents is not None else "the price",
            "cents_per_hour": (
                format_cents(int(round(cost_per_hour * 100)), currency_code)
                if cost_per_hour is not None else "a few cents"
            ),
            "dev": next(iter(top.get("developers") or []), "the devs"),
            "genre": genre_entry.get("name") or next(iter(top.get("genres") or []), "your favorite genre"),
            "genre_hours": f"{raw['genre_hours']:.1f}",
            "streak": raw["streak"],
            "days": raw["streak"],
            "library_value": format_cents(raw["library_value_cents"], currency_code),
            "unplayed_value": format_cents(unplayed_value_cents, currency_code),
            "unplayed_share": self._pct(unplayed_share),
            "session_hours": f"{raw['session_minutes'] / 60.0:.1f}",
            "end_time": end_time,
            "night_percent": f"{raw['night_percent']:.0f}",
            "cinema_rate": format_cents(int(round(cinema_rate * 100)), currency_code),
            "cinema_minutes": self._num(cinema_minutes),
            "cinema_multiple": f"{cinema_multiple:.1f}" if cinema_multiple is not None else "",
            "cinema_multiple_library": (
                f"{cinema_multiple_library:.1f}"
                if cinema_multiple_library is not None else ""
            ),
            "currency": currency,
            "currency_name": CURRENCY_NAMES.get(
                currency_code, currency_code.lower() or "currency unit"
            ),
            "percent": f"{float((hero.get('coverage') or {}).get('tracked_percent') or 0):.0f}",
            "hours_per_dollar": f"{hours_per_dollar:.1f}" if hours_per_dollar is not None else "",
            "minutes_per_dollar": self._num(minutes_per_dollar),
            "library_hours_per_dollar": (
                f"{library_hours_per_dollar:.1f}"
                if library_hours_per_dollar is not None else ""
            ),
            "copies_of_game": self._num(copies_of_game),
            "backlog_copies": self._num(backlog_copies),
            "steam_deck_units": self._num(steam_deck_units),
            "switch2_units": self._num(switch2_units),
            "steam_deck_price": self._short_usd(steam_deck_cents),
            "switch2_price": self._short_usd(switch2_cents),
            "portal_runs": self._num(runs(game_hours, portal_hours)),
            "cake_runs": self._num(runs(game_hours, cake_hours)),
            "lotr_runs": self._num(runs(game_hours, lotr_hours)),
            "skyrim_runs": self._num(runs(game_hours, skyrim_hours)),
            "elden_runs": self._num(runs(game_hours, elden_hours)),
            "portal_runs_library": self._num(runs(hours_total, portal_hours)),
            "cake_runs_library": self._num(runs(hours_total, cake_hours)),
            "lotr_runs_library": self._num(runs(hours_total, lotr_hours)),
            "rohirrim_percent": self._pct(budget_percent("rohirrim_budget_cents")),
            "gta_v_percent": self._pct(budget_percent("gta_v_budget_cents")),
            "cyberpunk_percent": self._pct(budget_percent("cyberpunk_budget_cents")),
            "star_citizen_percent": self._pct(budget_percent("star_citizen_budget_cents")),
            "undertale_percent": self._pct(budget_percent("undertale_budget_cents")),
            "rohirrim_budget": self._short_usd(rohirrim_budget_cents),
            "gta_v_budget": self._short_usd(gta_v_budget_cents),
            "cyberpunk_budget": self._short_usd(cyberpunk_budget_cents),
            "star_citizen_budget": self._short_usd(star_citizen_budget_cents),
            "undertale_budget": self._short_usd(undertale_budget_cents),
        }
        return raw, fmt

    # ------------------------------------------------------------------ filters

    @staticmethod
    def _valid(phrase, raw):
        when = phrase.get("when") or {}
        if when.get("selected") is not None and bool(when["selected"]) != raw["selected"]:
            return False
        if when.get("price_known") and not raw["price_known"]:
            return False
        if when.get("price_positive") and not raw["price_positive"]:
            return False
        if when.get("hours_min") is not None and raw["hours_total"] < float(when["hours_min"]):
            return False
        if when.get("unplayed_min") is not None and raw["never"] < int(when["unplayed_min"]):
            return False
        if when.get("session_minutes_min") is not None and raw["session_minutes"] < float(when["session_minutes_min"]):
            return False
        if when.get("night_owl") and raw["night_percent"] <= 0:
            return False
        if when.get("streak_min") is not None and raw["streak"] < int(when["streak_min"]):
            return False
        if when.get("library_value_known") and raw["library_value_cents"] <= 0:
            return False
        if when.get("genre_hours_min") is not None and raw["genre_hours"] < float(when["genre_hours_min"]):
            return False
        if when.get("game_hours_min") is not None and raw["game_hours"] < float(when["game_hours_min"]):
            return False
        if when.get("hours_per_dollar_known") and raw["hours_per_dollar"] is None:
            return False
        if when.get("hours_per_dollar_min") is not None:
            if (raw["hours_per_dollar"] is None
                    or raw["hours_per_dollar"] < float(when["hours_per_dollar_min"])):
                return False
        if when.get("minutes_per_dollar_min") is not None:
            if (raw["minutes_per_dollar"] is None
                    or raw["minutes_per_dollar"] < float(when["minutes_per_dollar_min"])):
                return False
        if when.get("cinema_multiple_min") is not None:
            if (raw["cinema_multiple"] is None
                    or raw["cinema_multiple"] < float(when["cinema_multiple_min"])):
                return False
        if when.get("library_hours_per_dollar_known") and raw["library_hours_per_dollar"] is None:
            return False
        if when.get("library_hours_per_dollar_min") is not None:
            if (raw["library_hours_per_dollar"] is None
                    or raw["library_hours_per_dollar"] < float(when["library_hours_per_dollar_min"])):
                return False
        if when.get("cinema_multiple_library_min") is not None:
            if (raw["cinema_multiple_library"] is None
                    or raw["cinema_multiple_library"] < float(when["cinema_multiple_library_min"])):
                return False
        if when.get("hardware_known") and raw["steam_deck_units"] is None:
            return False
        if when.get("switch_known") and raw["switch2_units"] is None:
            return False
        if when.get("unplayed_value_known") and raw["unplayed_value_cents"] <= 0:
            return False
        if when.get("steam_deck_min") is not None:
            if (raw["steam_deck_units"] is None
                    or raw["steam_deck_units"] < float(when["steam_deck_min"])):
                return False
        if when.get("switch2_min") is not None:
            if (raw["switch2_units"] is None
                    or raw["switch2_units"] < float(when["switch2_min"])):
                return False
        if when.get("copies_of_game_min") is not None:
            if (raw["copies_of_game"] is None
                    or raw["copies_of_game"] < float(when["copies_of_game_min"])):
                return False
        if when.get("backlog_copies_min") is not None:
            if (raw["backlog_copies"] is None
                    or raw["backlog_copies"] < float(when["backlog_copies_min"])):
                return False
        return True

    def _candidates(self, category, raw):
        return [
            phrase for phrase in self._phrases
            if phrase.get("category") == category and self._valid(phrase, raw)
        ]

    def _pick_category(self, raw):
        if raw["selected"]:
            if raw["game_installed"] and raw["game_hours"] <= 0:
                eligible = ["still_sealed"]
            elif raw["price_positive"] and raw["game_hours"] > 0:
                eligible = [
                    "cost_per_hour",
                    "value_vs_cinema",
                    "hours_per_dollar",
                    "nerd_hours",
                    "nerd_value",
                    "library_value",
                ]
            elif raw["game_hours"] > 0:
                eligible = ["game_generic", "nerd_hours", "nerd_value", "library_value"]
            else:
                eligible = ["generic"]
            if raw["genre_hours"] >= 5:
                eligible.append("genre_devotion")
            eligible = [category for category in eligible if self._candidates(category, raw)]
            return self._rng.choice(eligible or ["generic"])

        eligible = []
        if raw["price_known"] and raw["hours_total"] > 0:
            eligible.extend(["cost_per_hour", "value_vs_cinema"])
        if raw["never"] > 0:
            eligible.append("still_sealed")
        if raw["session_minutes"] >= 120:
            eligible.append("marathon")
        if raw["night_percent"] > 0:
            eligible.append("night_owl")
        if raw["streak"] >= 2:
            eligible.append("streak")
        if raw["library_value_cents"] > 0:
            eligible.extend(["library_value", "nerd_value"])
        if raw["genre_hours"] >= 5:
            eligible.append("genre_devotion")
        if raw["hours_total"] >= 3:
            eligible.append("nerd_hours")
        eligible.append("generic")
        eligible = [category for category in eligible if self._candidates(category, raw)]
        return self._rng.choice(eligible or ["generic"])

    def _choose(self, phrases):
        if not phrases:
            return {"id": "", "category": "generic", "text": ""}
        fresh = [p for p in phrases if p.get("id") not in self._recent]
        if fresh:
            pool = fresh
        else:
            last_id = self._recent[-1] if self._recent else None
            pool = [p for p in phrases if p.get("id") != last_id] or phrases
        return self._rng.choice(pool)

    @staticmethod
    def _format(template, values):
        try:
            text = str(template).format(**values)
        except (KeyError, IndexError, ValueError):
            return ""
        if "{" in text or "}" in text:
            return ""
        return " ".join(text.split())
