"""
Kurzlabels für gespeicherte Vorschläge (Tiny-Tasks, Inspirationen).

Hintergrund (Review D7, B1): die letzten Vorschläge landen als
"NICHT wiederholen"-Liste in den Generator-Prompts. Als Volltexte ankern
12 Beispiele derselben Formel ("Hey, wie wär's mit … / Klingt das machbar?")
das Modell genau auf das Muster, das der Prompt verbieten will – Mitursache
des Template-Kollapses. Deshalb wird beim Speichern ein Kurzlabel
(nur Kern-Handlung, ≤80 Zeichen) miterzeugt; Bestand ohne Label wird
beim Lesen heuristisch auf die erste Zeile gekürzt.
"""
import logging
import re

from bot.services import persona_config

logger = logging.getLogger(__name__)

MAX_LEN = 80

# Wiederkehrende Vorschlags-Floskeln am Satzanfang, die im Label nichts verloren haben.
# Apostroph auch typografisch (’/`) – die LLM-Texte nutzen U+2019, nicht ASCII '.
_OPENER = re.compile(
    r"^(hey[,!]?\s*)?(wie\s+w[äa]r['’`]?s\s*(heute|mal|denn)?\s*(damit|mit)?[:,]?\s*)",
    re.IGNORECASE,
)


def heuristik_label(text: str, max_len: int = MAX_LEN) -> str:
    """Rein heuristisches Kurzlabel (für Bestand ohne gespeichertes `kurzlabel`):
    Listen-Nummerierung/Markdown abstreifen, erste Zeile, Floskel-Opener weg,
    erster Satz, an Wortgrenze kürzen."""
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"^\d+\.\s*", "", t)            # "3. " aus dem Inspirations-Listenformat
    t = t.splitlines()[0].strip()
    t = re.sub(r"[*_`\[\]]+", "", t).strip()   # Telegram-Markdown raus
    t = _OPENER.sub("", t).strip() or t
    # Begründungs-/Abschluss-Satz gehört nicht ins Label → erster Satz reicht
    satz = re.split(r"(?<=[.!?])\s+", t)[0].strip()
    t = satz or t
    if len(t) > max_len:
        cut = t[:max_len].rsplit(" ", 1)[0]
        t = (cut or t[:max_len]).rstrip(" ,;:–-") + "…"
    return t


async def kurzlabel(text: str) -> str:
    """Kern-Handlung via Grok (temp=0), best-effort mit Heuristik-Fallback.
    Läuft nur beim Speichern eines Vorschlags, nicht im Tagespfad."""
    from bot.services import grok
    try:
        antwort = grok.clean_text(await grok.simple(
            f"Vorschlagstext:\n{(text or '')[:1500]}",
            system=(
                "Extrahiere aus dem Aufgaben-Vorschlag NUR die Kern-Handlung als knappes "
                "Stichwort-Label, maximal 10 Wörter. Ohne Anrede, ohne Begründung, ohne "
                "Abschlussfrage, ohne Anführungszeichen, kein vollständiger Satz. "
                "Beispiel: Eiswürfel-Spiel an den Brustwarzen beim Duschen"
                + persona_config.sprache_anweisung()
            ),
            temperature=0,
        ))
        antwort = " ".join((antwort or "").split())
        # Plausibilitäts-Gate: leere/ausufernde Antworten (Refusal, Erklärtext) verwerfen
        if 3 <= len(antwort) <= 120:
            return antwort[:MAX_LEN]
    except Exception:
        logger.exception("Kurzlabel-Generierung fehlgeschlagen – Heuristik-Fallback")
    return heuristik_label(text)
