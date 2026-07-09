"""
Zentrale Nutzer-Texte (UI/Flow, Fehler, Safety, Persona-Fallbacks).

Namespaces:
  COMMON_*    – projektweit wiederverwendete UI-Texte
  FEHLER_*    – technische Fehlertexte
  SAFEWORD_*  – Safety-Texte (bewusst statisch, NIE per LLM generieren/variieren)
  FALLBACK_*  – Persona-Fallbacks bei LLM-Ausfall (in der Stimme der Figur)
  Feature-Namespaces (ONBOARDING_*, VORLAGEN_*, DOMINA_*, ARC_*, MEDIEN_*, …)
  – UI-/Flow-Texte des jeweiligen Handlers.

Zugriff: t("COMMON_ABGEBROCHEN") bzw. t("FALLBACK_FOLLOWUP_FRAGE", aufgabe=...).
Achtung bei Templates: Platzhalter mit Markdown-Kontext (parse_mode am Callsite)
müssen dort ggf. vor-escaped übergeben werden (telegram_helper.escape_md).

Neue oder geänderte Nutzer-Texte bitte in bot/locales/de.py anlegen (Referenz-
Locale) statt im Handler hartzucodieren – und, wenn möglich, direkt in en.py
mitübersetzen (sonst greift der deutsche Fallback).
"""

# Die Texte selbst liegen pro Sprache in bot/locales/ (de.py = Referenz).
# Default-Locale: config.BOT_LOCALE (Env); ein Paar kann sie überschreiben
# (persona_config.ui_locale, über den Paar-Kontext aufgelöst) – so bekommt
# Paar 2 englische Menüs, während das Env-Paar deutsche behält. Fehlende
# Übersetzungen fallen auf Deutsch zurück. _MESSAGES bleibt als Name bestehen –
# Tests und Introspektion (test_messages_konsistenz) hängen daran.
from bot import config
from bot.locales import lade

_MESSAGES = lade(config.BOT_LOCALE)
_KATALOGE: dict[str, dict] = {config.BOT_LOCALE: _MESSAGES}


def _katalog() -> dict:
    """Message-Katalog der UI-Locale des Kontext-Paares (lazy geladen+gecacht).
    Lazy-Import: persona_config → qdrant wäre sonst ein Import-Zyklus-Risiko."""
    from bot.services import persona_config
    try:
        locale = persona_config.ui_locale() or config.BOT_LOCALE
    except Exception:
        locale = config.BOT_LOCALE
    if locale not in _KATALOGE:
        _KATALOGE[locale] = lade(locale)
    return _KATALOGE[locale]


def t(key: str, **fmt) -> str:
    """Liefert den Text zu `key` in der UI-Locale des Kontext-Paares; KeyError
    bei unbekanntem Key (Tippfehler sollen laut knallen, nicht still leer senden)."""
    text = _katalog()[key]
    return text.format(**fmt) if fmt else text
