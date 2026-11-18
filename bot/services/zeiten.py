"""
Zeiten-Helper – Validierung und Normalisierung von Zeitfenster-Eingaben
(z.B. kinderfreie Zeiten im Onboarding und Profil-Edit) sowie Fenster-Checks
(Blitzaufgaben senden nur in erlaubten Zeitfenstern).
"""
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from bot import config

logger = logging.getLogger(__name__)

# Eingaben, die explizit "keine Zeitfenster" bedeuten
_VERNEINUNGEN = {
    "keine", "kein", "nein", "-", "–",
    "immer frei", "immer", "keine kinder",
}

# Ein Zeitfenster: 20:00-23:00, auch 7:00 - 8:00, 20.00–23.00, optional "Uhr"
_ZEITFENSTER_RE = re.compile(
    r"^(\d{1,2})[:.](\d{2})\s*(?:uhr)?\s*[-–]\s*(\d{1,2})[:.](\d{2})\s*(?:uhr)?$",
    re.IGNORECASE,
)

# Halboffene Freitext-Formen, wie sie der Profil-Merge aus dem Gespräch
# schreibt: "Täglich ab 20:00", "ab 20 Uhr", "bis 07:00". Der Live-Befund
# 03.09.2026: 'Täglich ab 20:00' war nicht parsebar, ist_im_fenster damit
# IMMER False – Spiel-Impuls und Blitz konnten nie senden (fail-closed).
# Minuten optional; \b verhindert, dass "abends" als "ab" gelesen wird.
_AB_RE = re.compile(r"\bab\s+(\d{1,2})(?:[:.](\d{2}))?\s*(?:uhr)?\b", re.IGNORECASE)
_BIS_RE = re.compile(r"\bbis\s+(\d{1,2})(?:[:.](\d{2}))?\s*(?:uhr)?\b", re.IGNORECASE)

# Je Wortlaut nur einmal warnen – der Fenster-Check läuft alle 30 Minuten.
_unparsebar_gewarnt: set[str] = set()


def alter_label(datum_iso: str) -> str:
    """Deterministisches Zeit-Label ('heute'/'gestern'/'vor N Tagen') für
    Kontext-Listen in Generator-Prompts. Ohne Label datieren die Modelle die
    Einträge selbst – und erfinden dabei Zeitbezüge ('gestern' über eine
    mehrere Tage alte Aufgabe, Live-Befund 08.08.).
    Kalendertage in lokaler Zeit statt timedelta.days: die Sekunden-Kante
    einer Differenz knapp unter N*24h würde sonst einen Tag verschlucken."""
    try:
        d = datetime.fromisoformat(datum_iso)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        # Bot-Zeitzone statt System-TZ (D9/N21): der Container setzt TZ zwar
        # passend, aber ein Fremdbetrieb ohne TZ liefe auf UTC und „heute/
        # gestern" kippte um Mitternacht einen Tag daneben.
        bot_tz = ZoneInfo(config.TIMEZONE)
        tage = (datetime.now(bot_tz).date() - d.astimezone(bot_tz).date()).days
    except (ValueError, TypeError):
        return ""
    if tage <= 0:
        return "heute"
    if tage == 1:
        return "gestern"
    return f"vor {tage} Tagen"


WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


def heute_lokal() -> date:
    """Der heutige Kalendertag in der Bot-Zeitzone."""
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


def tage_seit(datum_iso: str) -> int | None:
    """Kalendertage (Bot-Zeitzone) seit einem ISO-Zeitstempel – 0 = heute,
    1 = gestern. None, wenn er nicht lesbar ist oder in der Zukunft liegt."""
    try:
        d = datetime.fromisoformat(datum_iso)
    except (ValueError, TypeError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    bot_tz = ZoneInfo(config.TIMEZONE)
    tage = (datetime.now(bot_tz).date() - d.astimezone(bot_tz).date()).days
    return tage if tage >= 0 else None


# Tageszeit hinter einem Tag: wie sie am festen Wort hängt („gestern Abend"),
# am Wochentag („am Sonntagabend") und am Datum („am 08.11. abends").
_TAGESZEITEN = {
    "abend": ("Abend", "abend", "abends"),
    "nacht": ("Nacht", " nachts", "nachts"),
    "früh": ("früh", " früh", "früh"),
    "morgen": ("Morgen", "morgen", "morgens"),
    "vormittag": ("Vormittag", "vormittag", "vormittags"),
    "mittag": ("Mittag", "mittag", "mittags"),
    "nachmittag": ("Nachmittag", "nachmittag", "nachmittags"),
}
_TAG_WORTE = {0: "heute", 1: "gestern", 2: "vorgestern", -1: "morgen", -2: "übermorgen"}


def tag_label(tage_her: int, tageszeit: str = "", heute: date | None = None) -> str:
    """Ein Tag relativ zu heute in Worten, wie man ihn in einem Satz sagt:
    tage_her > 0 liegt zurück, < 0 voraus. 'heute', 'gestern', 'vorgestern',
    'morgen', 'übermorgen'; darüber hinaus bis sechs Tage Abstand der Wochentag
    ('am Sonntag'), sonst das Datum ('am 08.11.'). Mit `tageszeit` ('abend',
    'früh' …) als fertige Wendung: 'gestern Abend', 'am Sonntagabend',
    'am 08.11. abends'. Ohne das hängen die Modelle die Tageszeit selbst an
    und schreiben „vor zwei Tagen Abend" (live 18.11.2026)."""
    zeit = _TAGESZEITEN.get((tageszeit or "").strip().lower().replace("frueh", "früh"))
    if tage_her in _TAG_WORTE:
        wort = _TAG_WORTE[tage_her]
        if not zeit:
            return wort
        # „morgen Morgen" sagt niemand – das ist „morgen früh".
        return f"{wort} früh" if (wort == "morgen" and zeit[0] == "Morgen") else f"{wort} {zeit[0]}"
    tag = (heute or heute_lokal()) - timedelta(days=tage_her)
    if abs(tage_her) <= 6:
        return f"am {WOCHENTAGE[tag.weekday()]}{zeit[1] if zeit else ''}"
    return f"am {tag:%d.%m.}{' ' + zeit[2] if zeit else ''}"


def wann_label(zeitstempel: float, jetzt: float | None = None) -> str:
    """Wann etwas war, auf die Minute und in Worten: 'gerade eben', 'vor 9
    Minuten', 'heute um 14:05', 'gestern um 21:30', 'vor 3 Tagen'. Für
    Kontext im Chat-Prompt aus Unix-Zeitstempeln – ohne Angabe hält das Modell
    alles, was es zu sehen bekommt, für eben geschehen."""
    if jetzt is None:
        jetzt = datetime.now(timezone.utc).timestamp()
    sekunden = max(0.0, jetzt - zeitstempel)
    if sekunden < 90:
        return "gerade eben"
    if sekunden < 3600:
        return f"vor {max(2, round(sekunden / 60))} Minuten"
    bot_tz = ZoneInfo(config.TIMEZONE)
    damals = datetime.fromtimestamp(zeitstempel, bot_tz)
    tage = (datetime.fromtimestamp(jetzt, bot_tz).date() - damals.date()).days
    if tage <= 0:
        return f"heute um {damals:%H:%M}"
    if tage == 1:
        return f"gestern um {damals:%H:%M}"
    return f"vor {tage} Tagen"


def mit_alter_label(text: str, datum_iso: str) -> str:
    """'vor 4 Tagen: <text>' – lässt den Text unverändert, wenn kein Datum parsebar."""
    label = alter_label(datum_iso)
    return f"{label}: {text}" if label else text


def parse_kinderfreie_zeiten(text: str) -> list[str] | None:
    """
    Parst eine User-Eingabe zu kinderfreien Zeiten.

    Akzeptiert:
    - Verneinungen ("keine", "nein", "-", "immer frei", ...) → []
    - Zeitfenster im Format HH:MM-HH:MM, auch mehrere kommagetrennt

    Rückgabe: normalisierte Liste ["HH:MM-HH:MM", ...],
    [] bei Verneinung, None wenn die Eingabe nicht parsebar ist.
    """
    eingabe = (text or "").strip()
    if not eingabe:
        return None
    if eingabe.lower() in _VERNEINUNGEN:
        return []

    fenster = []
    for teil in eingabe.split(","):
        teil = teil.strip()
        if not teil:
            continue
        m = _ZEITFENSTER_RE.match(teil)
        if not m:
            return None
        h1, m1, h2, m2 = (int(x) for x in m.groups())
        if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
            return None
        if (h1, m1) == (h2, m2):
            return None  # leeres Fenster ("20:00-20:00")
        # Über-Nacht-Fenster ("21:00-06:00" – Kinder schlafen) sind GÜLTIG:
        # ist_im_fenster() behandelt sie korrekt (seit dem Blitz-Feature
        # vergleicht blitz_check_job real Uhrzeiten gegen diese Fenster).
        fenster.append(f"{h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}")

    return fenster or None


def _parse_fenster(fenster: str) -> tuple[time, time] | None:
    """'HH:MM-HH:MM' → (start, ende); dazu die halboffenen Freitext-Formen
    'ab HH[:MM]' → (start, 23:59) und 'bis HH[:MM]' → (00:00, ende).
    None bei Murks."""
    text = (fenster or "").strip()
    m = _ZEITFENSTER_RE.match(text)
    if m:
        h1, m1, h2, m2 = (int(x) for x in m.groups())
        try:
            return time(h1, m1), time(h2, m2)
        except ValueError:
            return None
    m = _AB_RE.search(text)
    if m:
        try:
            return time(int(m.group(1)), int(m.group(2) or 0)), time(23, 59)
        except ValueError:
            return None
    m = _BIS_RE.search(text)
    if m:
        try:
            return time(0, 0), time(int(m.group(1)), int(m.group(2) or 0))
        except ValueError:
            return None
    return None


def ist_im_fenster(jetzt: datetime, fenster_liste: list[str]) -> bool:
    """True wenn `jetzt` in mindestens einem Fenster liegt. Über-Nacht-Fenster
    ('21:00-06:00') werden korrekt behandelt. Leere Liste = immer frei.
    Nicht parsebare Einträge werden übersprungen (fail-open pro Eintrag wäre
    hier falsch – ein kaputtes Fenster erlaubt nichts zusätzlich)."""
    if not fenster_liste:
        return True
    t = jetzt.time()
    for eintrag in fenster_liste:
        geparst = _parse_fenster(eintrag)
        if not geparst:
            # Laut, nicht lautlos: Ein unverstandenes Fenster sperrt sonst
            # unbemerkt alles (fail-closed) — genau so blieb der Spiel-Impuls
            # stumm (Live-Befund 03.09.2026).
            if eintrag not in _unparsebar_gewarnt:
                _unparsebar_gewarnt.add(eintrag)
                logger.warning("Zeitfenster nicht parsebar, wird ignoriert: %r", eintrag)
            continue
        start, ende = geparst
        if start <= ende:
            if start <= t <= ende:
                return True
        else:  # über Mitternacht
            if t >= start or t <= ende:
                return True
    return False
