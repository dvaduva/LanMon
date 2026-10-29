"""Translations: English is the source language; other languages live in app/locales/*.json.

Catalog values are strings, or lists of plural forms for messages used with ngettext.
Messages take named placeholders filled with str.format: gettext(lang, "Hi {name}", name=...).
"""

import json
from functools import cache
from pathlib import Path

LANGUAGES = {"en": "English", "ro": "Română"}
DEFAULT_LANGUAGE = "en"
LOCALES_DIR = Path(__file__).parent / "locales"


def N_(message: str) -> str:  # noqa: N802 — gettext convention
    """Marks a message for translation without translating it (it is translated on display)."""
    return message


WEEKDAYS = [N_("Mon"), N_("Tue"), N_("Wed"), N_("Thu"), N_("Fri"), N_("Sat"), N_("Sun")]
# Names of the device types seeded by the first migration (translated on display).
BUILTIN_TYPES = [
    N_("PC"),
    N_("Laptop"),
    N_("TV"),
    N_("NAS"),
    N_("Printer"),
    N_("Phone"),
    N_("Tablet"),
    N_("IoT"),
    N_("Router/AP"),
    N_("Other"),
]


@cache
def catalog(lang: str) -> dict[str, str | list[str]]:
    path = LOCALES_DIR / f"{lang}.json"
    if lang == "en" or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def plural_index(lang: str, n: int) -> int:
    if lang == "ro":
        if n == 1:
            return 0
        return 1 if n == 0 or 1 <= n % 100 <= 19 else 2
    return 0 if n == 1 else 1


def gettext(lang: str, message: str, **params) -> str:
    translated = catalog(lang).get(message, message)
    if isinstance(translated, list):
        translated = translated[0]
    return translated.format(**params) if params else translated


def ngettext(lang: str, singular: str, plural: str, n: int, **params) -> str:
    forms = catalog(lang).get(singular)
    if isinstance(forms, list):
        text = forms[min(plural_index(lang, n), len(forms) - 1)]
    else:
        text = singular if n == 1 else plural
    return text.format(n=n, **params)


def detail_text(lang: str, text: str | None) -> str:
    """Stored details are English: "Message." or "Message: raw system error"."""
    if not text:
        return ""
    if text in catalog(lang):
        return gettext(lang, text)
    head, sep, rest = text.partition(": ")
    if sep and head in catalog(lang):
        return gettext(lang, head) + sep + rest
    return text


def normalize(lang: str | None) -> str | None:
    return lang if lang in LANGUAGES else None


def negotiate(accept_language: str | None) -> str:
    """Best match from an Accept-Language header, e.g. "ro-RO,ro;q=0.9,en;q=0.8"."""
    choices = []
    for part in (accept_language or "").split(","):
        tag, _, q = part.strip().partition(";q=")
        try:
            weight = float(q) if q else 1.0
        except ValueError:
            weight = 0.0
        lang = normalize(tag.split("-")[0].strip().lower())
        if lang:
            choices.append((weight, lang))
    return max(choices, key=lambda c: c[0])[1] if choices else DEFAULT_LANGUAGE
