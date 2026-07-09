"""
Locale-Auflösung für die UI-/Flow-Texte (Veröffentlichungs-Schritt 2).

Deutsch (de.py) ist die Referenz-Locale mit garantiert vollständigem Key-Satz.
Andere Locales überschreiben davon, was sie übersetzt haben – fehlende Keys
fallen still auf Deutsch zurück (lieber ein deutscher Text als ein KeyError
im Handler). Die Ziel-Locale kommt aus config.BOT_LOCALE (Env, Default "de").

Die Sprache der LLM-Antworten ist davon UNABHÄNGIG konfiguriert
(persona_config.sprache, zur Laufzeit änderbar) – BOT_LOCALE betrifft nur
die statischen Texte und Command-Aliase.
"""
import importlib
import logging

logger = logging.getLogger(__name__)

VERFUEGBAR = ("de", "en")


def lade(locale: str) -> dict:
    """Message-Dict für `locale`: de-Basis + Übersetzungs-Overlay."""
    from bot.locales import de
    messages = dict(de.MESSAGES)
    locale = (locale or "de").strip().lower()
    if locale in ("", "de"):
        return messages
    try:
        modul = importlib.import_module(f"bot.locales.{locale}")
    except ImportError:
        logger.warning("Unbekannte BOT_LOCALE %r – nutze 'de'.", locale)
        return messages
    overlay = getattr(modul, "MESSAGES", {})
    fehlend = set(messages) - set(overlay)
    if fehlend:
        logger.warning("Locale %r: %d Keys nicht übersetzt – deutsche Fallbacks aktiv.",
                       locale, len(fehlend))
    messages.update({k: v for k, v in overlay.items() if k in messages})
    fremd = set(overlay) - set(messages)
    if fremd:
        logger.warning("Locale %r: %d unbekannte Keys ignoriert: %s",
                       locale, len(fremd), sorted(fremd)[:5])
    return messages
