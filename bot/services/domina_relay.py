"""
Sklave → Domina Hinweis-Weiterleitung.

Wenn der Sklave im freien Chat mit der Herrin (dem Bot) durchblicken lässt, dass
seine ECHTE Domina etwas erfahren soll – eine Bitte, ein Geständnis, ein Wunsch
an sie ("ein Hinweis an meine Domina wäre schön", "sag ihr bitte…", "sie soll
wissen…") – wird das NICHT roh durchgereicht, sondern von Grok in der
Coach-Stimme (vertraute beste Freundin der Domina) zu einem kurzen Hinweis
umformuliert und via `send_domina` zugestellt.

Best-effort: blockiert nie den Sklaven-Chat, ein Fehler verschluckt sich still.
Gated über inhaltliche Nachrichtenlänge; die eigentliche Intent-Erkennung macht
Grok deterministisch (temperature=0) und antwortet "KEINE", wenn nichts dran ist.
"""
import logging

from telegram import Bot

from bot.services import grok, telegram_helper
from bot.prompts import coach_persona
from bot.prompts import followup as fp

logger = logging.getLogger(__name__)


def _system_prompt() -> str:
    return (
        coach_persona.fuer_strukturierten_output()
        + "\n\n"
        "Der Sklave schreibt gerade mit seiner Herrin (dem Bot). Manchmal möchte er, dass "
        "seine ECHTE Domina (eine reale Person, mit der du als ihre beste Freundin sprichst) "
        "etwas erfährt – eine Bitte, ein Hinweis, ein Geständnis oder ein Wunsch, den sie "
        "wissen soll (z.B. 'ein Hinweis an meine Domina wäre schön', 'sag ihr bitte…', "
        "'sie soll wissen…', 'ich wünsch mir von ihr…').\n\n"
        "Prüfe NUR, ob in SEINER Nachricht so ein an-die-Domina-gerichtetes Anliegen steckt.\n"
        "- Wenn NEIN (normales Spiel, Geplauder, Rückmeldung zur Szene, Vorfreude – nichts, "
        "das die Domina aktiv erfahren soll): antworte AUSSCHLIESSLICH mit dem Wort KEINE.\n"
        "- Wenn JA: formuliere als ihre vertraute beste Freundin EINEN kurzen Hinweis an die "
        "Domina (1-3 Sätze), der ihr beiläufig ausrichtet, was er möchte oder braucht. Keine "
        "Anrede-Floskel, kein Briefkopf, keine Anführungszeichen. Beginne nicht mit 'KEINE'."
    )


async def pruefe_und_leite_weiter(bot: Bot, sklave_text: str) -> bool:
    """Erkennt einen an-die-Domina-Hinweis im Sklaven-Text und stellt ihn der
    Domina in der Coach-Stimme zu. Gibt True zurück, wenn etwas gesendet wurde."""
    text = (sklave_text or "").strip()
    if len(text) < 10:
        return False

    try:
        antwort = grok.clean_text(await grok.simple(
            fp.nutzer_text("Nachricht des Sklaven", text[:1000]),
            system=_system_prompt(),
            temperature=0,  # deterministische Erkennung
        ))
    except Exception:
        logger.exception("Domina-Hinweis-Erkennung (Grok) fehlgeschlagen")
        return False

    if not antwort or antwort.upper().startswith("KEINE") or len(antwort) < 8:
        return False

    try:
        await telegram_helper.send_domina(bot, antwort, parse_mode="Markdown")
    except Exception:
        logger.exception("Domina-Hinweis konnte nicht zugestellt werden")
        return False

    logger.info("Domina-Hinweis weitergeleitet: %s", antwort[:120])
    return True
