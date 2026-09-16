"""
Währung ⭐ – gibt den Punkten des Subs Bedeutung (Bauplan 16.09.2026):
Ränge nach Stand (können sinken), Abzüge (nicht erledigt / Strafe), Schwellen
für den Shop-Push, Sparziel auf der Wunschliste (services/inventar) und die
Herrin-Wette mit festem Punkte-Einsatz (handlers/waehrung). Hier liegt die
reine Logik + Profil-Buchung; Nachrichten/Buttons wohnen in handlers/waehrung.
"""
import logging
import re
from datetime import date, datetime, timezone

from bot import config
from bot.services import qdrant

logger = logging.getLogger(__name__)

# Rangstufen nach Punktestand (aufsteigend). Der Rang folgt dem AKTUELLEN Stand –
# Abzüge können ihn senken, das ist gewollt (etwas zu verteidigen).
RAENGE: tuple[tuple[int, str], ...] = (
    (0, "Frischling"),
    (100, "Fußschemel"),
    (250, "Laufbursche"),
    (500, "Diener"),
    (1000, "Leibdiener"),
    (2000, "Ihr Eigentum"),
)

ABZUG_NICHT_ERLEDIGT = int(getattr(config, "PUNKTE_ABZUG_NICHT_ERLEDIGT", 20))
STRAFE_ABZUEGE = (25, 50)          # Buttons unter dem Strafvorschlag
SCHWELLE = 100                     # Shop-Push bei jeder vollen Hundert (nur aufwärts)
WUNSCH_PREISE = (200, 300, 500)    # Buttons an die Dom-Seite beim neuen Wunsch
WETT_EINSATZ = 50                  # fest, Beigabe zur echten Wette
WETT_FRIST_DEFAULT_TAGE = 2
WETT_FRIST_MAX_TAGE = 7
WETT_MELDEFRIST_TAGE = 3           # keine Meldung → verloren
EINSPRUCH_STUNDEN = 24
SPARZIEL_RUHE_TAGE = 14            # nach „noch nicht"


# ---------------------------------------------------------------------------
# Ränge / Schwellen (pur)
# ---------------------------------------------------------------------------

def rang(punkte: int) -> tuple[int, str]:
    """(Stufe 0-basiert, Titel) für einen Punktestand."""
    stufe, titel = 0, RAENGE[0][1]
    for i, (ab, name) in enumerate(RAENGE):
        if punkte >= ab:
            stufe, titel = i, name
    return stufe, titel


def naechster_rang(punkte: int) -> tuple[int, str] | None:
    """(Punkte ab, Titel) der nächsten Stufe; None auf der höchsten."""
    for ab, name in RAENGE:
        if punkte < ab:
            return ab, name
    return None


def rang_wechsel(alt: int, neu: int) -> str | None:
    """'auf' | 'ab' | None – Stufenwechsel zwischen zwei Ständen."""
    a, b = rang(alt)[0], rang(neu)[0]
    if b > a:
        return "auf"
    if b < a:
        return "ab"
    return None


def schwelle_ueberschritten(alt: int, neu: int) -> int | None:
    """Höchste volle Hundert in (alt, neu] – nur aufwärts, sonst None."""
    if neu <= alt:
        return None
    hoechste = (neu // SCHWELLE) * SCHWELLE
    return hoechste if hoechste > alt and hoechste > 0 else None


# ---------------------------------------------------------------------------
# Buchung
# ---------------------------------------------------------------------------

async def buchen(delta: int, grund: str) -> dict:
    """Punkte des Subs um `delta` ändern (nie unter 0). Rückgabe
    {alt, neu, delta (tatsächlich gebucht)}. Einziger Schreibweg für Abzüge
    und Wett-Buchungen; task_erledigt (punkte.py) bucht Verdienste selbst."""
    profil = await qdrant.get_user_profile("sklave") or {}
    alt = int(profil.get("punkte", 0) or 0)
    neu = max(0, alt + int(delta))
    if neu != alt:
        await qdrant.patch_profile_fields("sklave", {"punkte": neu})
    logger.info("Punkte %s: %d → %d (%s)", "+" if delta >= 0 else "−", alt, neu, grund)
    return {"alt": alt, "neu": neu, "delta": neu - alt}


# ---------------------------------------------------------------------------
# Wett-Frist aus dem Ideen-Text
# ---------------------------------------------------------------------------

_ZAHLWORTE = {
    "ein": 1, "eine": 1, "einen": 1, "einem": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5,
    "sechs": 6, "sieben": 7, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7,
}
_TAGE_RE = re.compile(
    r"\b(\d{1,2}|ein|eine|einen|einem|zwei|drei|vier|fünf|sechs|sieben|one|two|three|four|five|six|seven)"
    r"\s+(?:tage?n?|days?)\b", re.I)
_STUNDEN_RE = re.compile(r"\b(\d{1,2})\s*(?:stunden|std|hours|h)\b", re.I)


def wett_frist_tage(text: str, heute: date | None = None) -> int:
    """Frist einer Wette in Tagen aus der Idee: „zwei Tage" / „3 Tage" / „24 Stunden"
    → Zahl; „morgen" 1, „übermorgen" 2; Wochentag/Datum via datum_erkennung;
    sonst WETT_FRIST_DEFAULT_TAGE. Immer 1..WETT_FRIST_MAX_TAGE."""
    t = (text or "").lower()
    tage: int | None = None
    m = _TAGE_RE.search(t)
    if m:
        w = m.group(1)
        tage = int(w) if w.isdigit() else _ZAHLWORTE.get(w)
    if tage is None:
        m = _STUNDEN_RE.search(t)
        if m:
            tage = max(1, -(-int(m.group(1)) // 24))
    if tage is None and "übermorgen" in t:
        tage = 2
    if tage is None and re.search(r"\b(morgen|tomorrow)\b", t):
        tage = 1
    # „heute Abend …" (Live-Render 16.09.: Wette spielt heute, Urteil morgen zur
    # Followup-Zeit statt erst übermorgen)
    if tage is None and re.search(r"\b(heute|tonight|today)\b", t):
        tage = 1
    if tage is None:
        try:
            from bot.services import datum_erkennung
            termin = datum_erkennung.finde_termin(text)
            if termin:
                tage = (termin[0] - (heute or date.today())).days
        except Exception:
            tage = None
    if tage is None:
        tage = WETT_FRIST_DEFAULT_TAGE
    return max(1, min(WETT_FRIST_MAX_TAGE, int(tage)))


def frist_iso(tage: int) -> str:
    """Frist als UTC-ISO: Followup-Zeit des Paares in `tage` Tagen (wie Tasks)."""
    return qdrant.followup_zeitpunkt_utc(tage)


def jetzt_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
