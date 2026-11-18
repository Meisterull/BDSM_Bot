"""
Währung ⭐ – gibt den Punkten des Subs Bedeutung (Bauplan 16.09.2026):
Ränge nach Stand (können sinken), Abzüge (nicht erledigt / Strafe), Schwellen
für den Shop-Push, Sparziel auf der Wunschliste (services/inventar) und die
Herrin-Wette mit festem Punkte-Einsatz (handlers/waehrung). Hier liegt die
reine Logik + Profil-Buchung; Nachrichten/Buttons wohnen in handlers/waehrung.
"""
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

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
# Annahme / Ablehnung einer Herrin-Wette (Bauplan 16.09.2026 abends)
WETT_ANNAHME_ERINNERUNG_STUNDEN = 4   # Erinnerung an den Sub (nur tagsüber)
WETT_ANNAHME_AUTO_STUNDEN = 24        # ohne Antwort → gilt als angenommen
WETT_ABLEHNUNG_ABZUEGE = (25, 50, 100)  # Dom-Seite wählt, was das Ablehnen kostet
WETT_ABLEHNUNG_AUTO_STUNDEN = 24      # Dom-Seite wählt nichts → Default-Abzug, keine Strafe
WETT_ABLEHNUNG_DEFAULT = 50
WETT_STRAFE_MAX_NEU = 3               # 🎲-Würfe für die Strafvorschläge
TAGSUEBER = (9, 23)                   # Sub-Nachrichten der Jobs nur in diesem Stunden-Fenster
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


def _wett_frist(text: str, heute: date | None = None) -> tuple[int, str]:
    """(Tage bis zur Urteils-Frage, Art der Angabe: 'dauer' | 'tag' | 'ohne')."""
    t = (text or "").lower()
    tage: int | None = None
    art = "dauer"
    m = _TAGE_RE.search(t)
    if m:
        w = m.group(1)
        tage = int(w) if w.isdigit() else _ZAHLWORTE.get(w)
    if tage is None:
        m = _STUNDEN_RE.search(t)
        if m:
            tage = max(1, -(-int(m.group(1)) // 24))
    if tage is None:
        # Die Idee nennt einen TAG („heute Abend", „bis morgen Abend", „bis
        # Freitag"): Die Urteils-Frage kommt am Tag danach – dieselbe Regel wie
        # die Nachfrage zu Termin-Aufgaben. Die Nachfrage-Zeit liegt am späten
        # Nachmittag; für „morgen Abend" kam die Frage sonst vor dem Abend, um
        # den es ging (live 18.11.2026). Nur eine Uhrzeit klar davor lässt sie
        # am genannten Tag selbst.
        try:
            from bot.services import datum_erkennung
            bezug = heute or datum_erkennung._heute()
            # Bezugsdatum durchreichen – sonst rechnet der Wochentag ab dem
            # echten Heute und die Differenz gegen ein übergebenes `heute` kippt.
            termin = datum_erkennung.finde_spaetesten_termin(text, bezug)
            if termin is None and re.search(r"\b(heute|tonight|today)\b", t):
                termin = bezug
            if termin is not None:
                tage = datum_erkennung.nachfrage_tage(termin, text, bezug)
                art = "tag"
        except Exception:
            logger.exception("Wett-Frist: Tagesangabe nicht auswertbar – Standardfrist")
            tage = None
    if tage is None:
        tage, art = WETT_FRIST_DEFAULT_TAGE, "ohne"
    return max(1, min(WETT_FRIST_MAX_TAGE, int(tage))), art


def wett_frist_tage(text: str, heute: date | None = None) -> int:
    """Tage bis zur Urteils-Frage einer Wette (sie kommt zur Nachfrage-Zeit des
    Paares), gezählt ab `heute`: Dauer („zwei Tage" / „3 Tage" / „24 Stunden") →
    so viele Tage; ein genannter Tag („heute Abend", „morgen", Wochentag,
    Datum) → der Tag DANACH; sonst WETT_FRIST_DEFAULT_TAGE. Immer
    1..WETT_FRIST_MAX_TAGE."""
    return _wett_frist(text, heute)[0]


def wett_frist(idee: str, angeboten_am: str = "", jetzt: datetime | None = None) -> tuple[str, int]:
    """Frist einer eben angenommenen Wette: (UTC-ISO der Urteils-Frage, Tage bis
    dahin ab heute).

    Tagesangaben der Idee („morgen Abend") zählen ab dem Tag, an dem sie dem Sub
    angeboten wurde – nimmt er erst am nächsten Morgen an, bleibt „morgen"
    derselbe Abend. Eine Dauer zählt ab der Annahme; liegt die schon hinter der
    heutigen Nachfrage-Zeit, wäre die Dauer zur Nachfrage-Zeit des letzten Tages
    noch nicht um – dann ein Tag mehr. Nie in der Vergangenheit: mindestens die
    nächste Nachfrage-Zeit."""
    from bot.services import persona_config   # lazy: persona_config importiert qdrant
    tz = ZoneInfo(config.TIMEZONE)
    jetzt_l = (jetzt or datetime.now(timezone.utc)).astimezone(tz)
    stunde, minute = map(int, persona_config.zeit("followup_time").split(":"))

    def _nachfrage(tag: date) -> datetime:
        return datetime.combine(tag, time(stunde, minute), tzinfo=tz)

    heute = jetzt_l.date()
    basis = heute
    try:
        angeboten = datetime.fromisoformat(angeboten_am)
        if angeboten.tzinfo is None:
            angeboten = angeboten.replace(tzinfo=timezone.utc)
        tag = angeboten.astimezone(tz).date()
        if timedelta(0) <= heute - tag <= timedelta(days=2):
            basis = tag
    except (TypeError, ValueError):
        pass
    tage, art = _wett_frist(idee, basis)
    if art != "tag":
        basis = heute
        if art == "dauer" and jetzt_l > _nachfrage(heute):
            tage = min(WETT_FRIST_MAX_TAGE, tage + 1)
    ziel = _nachfrage(basis + timedelta(days=tage))
    if ziel <= jetzt_l:
        ziel = _nachfrage(heute) if jetzt_l < _nachfrage(heute) else _nachfrage(heute + timedelta(days=1))
    return ziel.astimezone(timezone.utc).isoformat(), (ziel.date() - heute).days


def frist_iso(tage: int) -> str:
    """Frist als UTC-ISO: Followup-Zeit des Paares in `tage` Tagen (wie Tasks)."""
    return qdrant.followup_zeitpunkt_utc(tage)


def jetzt_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
