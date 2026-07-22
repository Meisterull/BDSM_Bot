"""
Termin-Erkennung für Aufgaben: findet in Freitext der Domina ein Ziel-DATUM
("am Samstag", "morgen", "am 26.07."). Bewusst deterministisch (Regex statt
LLM) – Datums-Fehldeutungen wären für den Flow teurer als eine Rückfrage.

Kein Termin sind: "heute" (= sofort erteilen), wiederkehrende Angaben
("jeden Samstag" → das ist eine Serie, kein Einzeltermin) und Uhrzeiten.
"""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bot import config

# Wochentag → weekday()-Index; EN-Namen für die englische UI-Locale gleich mit.
_WOCHENTAGE = {
    "montag": 0, "monday": 0,
    "dienstag": 1, "tuesday": 1,
    "mittwoch": 2, "wednesday": 2,
    "donnerstag": 3, "thursday": 3,
    "freitag": 4, "friday": 4,
    "samstag": 5, "sonnabend": 5, "saturday": 5,
    "sonntag": 6, "sunday": 6,
}
_TAGE_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_TAGE_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Vorwörter, die eine WIEDERKEHRENDE Angabe markieren ("jeden Samstag") –
# dann ist es kein Einzeltermin und die Erkennung überspringt den Treffer.
_WIEDERKEHREND = {"jeden", "jedem", "jede", "alle", "immer", "every", "each"}
# Vorwörter, nach denen "morgen" der Tageszeit-Morgen ist ("am Morgen", "guten Morgen").
_MORGEN_TAGESZEIT = {"guten", "jeden", "am", "jedem", "zum", "vom"}

# "26.07." / "26.07.2026" – der Punkt nach dem Monat ist Pflicht, sonst würden
# Dezimalzahlen ("1.5 Liter") und Uhrzeiten als Datum durchgehen.
_DATUM_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})?(?!\d)")
_UEBERMORGEN_RE = re.compile(
    r"\b(?:übermorgen|uebermorgen|day\s+after\s+tomorrow)\b", re.IGNORECASE)
# matcht NICHT "morgens" (Wortgrenze) – "morgen früh" ergibt korrekt morgen.
_MORGEN_RE = re.compile(r"\b(?:morgen|tomorrow)\b", re.IGNORECASE)
_WOCHENTAG_RE = re.compile(
    r"\b(?:(nächste[nm]?|naechste[nm]?|kommende[nm]?|next)\s+)?"
    r"(" + "|".join(_WOCHENTAGE) + r")\b",
    re.IGNORECASE,
)
_SOFORT = {"sofort", "jetzt", "gleich", "direkt", "heute", "now", "today", "immediately"}


def _heute() -> date:
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


def _vorwort(text: str, start: int) -> str:
    """Letztes Wort vor Position `start` (lowercase, '' wenn keins)."""
    m = re.search(r"([\wäöüß]+)\W*$", text[:start].lower())
    return m.group(1) if m else ""


def _datum_treffer(text: str, heute: date) -> tuple[date, str] | None:
    for m in _DATUM_RE.finditer(text):
        tag, monat, jahr = int(m.group(1)), int(m.group(2)), m.group(3)
        try:
            if jahr:
                kandidat = date(int(jahr), monat, tag)
            else:
                kandidat = date(heute.year, monat, tag)
                if kandidat < heute:
                    kandidat = date(heute.year + 1, monat, tag)
        except ValueError:
            continue
        if kandidat >= heute:
            return kandidat, m.group(0)
    return None


def _wochentag_treffer(text: str, heute: date) -> tuple[date, str] | None:
    for m in _WOCHENTAG_RE.finditer(text):
        if _vorwort(text, m.start()) in _WIEDERKEHREND:
            continue
        delta = (_WOCHENTAGE[m.group(2).lower()] - heute.weekday()) % 7
        if m.group(1) and delta == 0:
            delta = 7  # "nächsten Samstag" AM Samstag = nächste Woche
        if delta == 0:
            return None  # "am Samstag" am Samstag selbst = heute = sofort
        return heute + timedelta(days=delta), m.group(0)
    return None


def finde_termin(text: str) -> tuple[date, str] | None:
    """Erstes erkanntes ZUKÜNFTIGES Datum im Text als (datum, gefundener_ausdruck),
    sonst None. Priorität: explizites Datum > übermorgen > morgen > Wochentag.
    'heute' ergibt bewusst None (= sofort erteilen)."""
    if not text:
        return None
    heute = _heute()

    treffer = _datum_treffer(text, heute)
    if treffer and treffer[0] > heute:
        return treffer

    m = _UEBERMORGEN_RE.search(text)
    if m:
        return heute + timedelta(days=2), m.group(0)

    for m in _MORGEN_RE.finditer(text):
        if _vorwort(text, m.start()) in _MORGEN_TAGESZEIT:
            continue
        return heute + timedelta(days=1), m.group(0)

    return _wochentag_treffer(text, heute)


def parse_termin_antwort(text: str):
    """Antwort auf die Wann-Rückfrage: 'sofort' | date | None (unverstanden)."""
    bereinigt = (text or "").strip().lower().rstrip(".!")
    if bereinigt in _SOFORT:
        return "sofort"
    treffer = finde_termin(text)
    return treffer[0] if treffer else None


def format_termin(datum: date) -> str:
    """'Samstag, 26.07.2026' – Wochentagsname in der UI-Locale des Paares."""
    try:
        from bot.services import persona_config
        locale = persona_config.ui_locale() or config.BOT_LOCALE
    except Exception:
        locale = config.BOT_LOCALE
    tage = _TAGE_EN if str(locale).lower().startswith("en") else _TAGE_DE
    return f"{tage[datum.weekday()]}, {datum.strftime('%d.%m.%Y')}"
