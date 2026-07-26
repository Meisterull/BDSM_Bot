"""
Zeiten-Helper – Validierung und Normalisierung von Zeitfenster-Eingaben
(z.B. kinderfreie Zeiten im Onboarding und Profil-Edit) sowie Fenster-Checks
(Blitzaufgaben senden nur in erlaubten Zeitfenstern).
"""
import re
from datetime import datetime, time

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
    """'HH:MM-HH:MM' → (start, ende) als time-Objekte; None bei Murks."""
    m = _ZEITFENSTER_RE.match((fenster or "").strip())
    if not m:
        return None
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    try:
        return time(h1, m1), time(h2, m2)
    except ValueError:
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
            continue
        start, ende = geparst
        if start <= ende:
            if start <= t <= ende:
                return True
        else:  # über Mitternacht
            if t >= start or t <= ende:
                return True
    return False
