"""
Vanilla-Modus: Punkte, Streak, Rang und Wette JE SLOT (Etappe 3, 05.11.2026).

Im BDSM-Stack buchen punkte.py/waehrung.py fest auf das Sklaven-Profil. Hier
ein schlankes Pendant, das beide Slots gleich behandelt: Verdienst für
erledigte Impulse/Würfel, Quiz-Punkte, „Doppelt oder nichts“-Wette auf die
eigene nächste Aufgabe, Gutschein-Einlösung. Felder im jeweiligen Slot-Profil:
punkte, streak, streak_max, letzter_streak_tag, vanilla_wette.
Keine Abzüge – Strafen sind im Vanilla-Modus aus (modi.strafen_aktiv()).
"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot import config
from bot.services import qdrant

logger = logging.getLogger(__name__)

PUNKTE_ERLEDIGT = 10
STREAK_BONUS_AB = 5
STREAK_BONUS = 5
WUERFEL_BONUS = 5
QUIZ_RICHTIG = 15
QUIZ_TEILWEISE = 5
WETT_EINSAETZE = (10, 25, 50)

# Rangstufen nach Punktestand – Bilder für ein Paar, kein Machtgefälle.
RAENGE: tuple[tuple[int, str], ...] = (
    (0, "Funke"),
    (100, "Flamme"),
    (250, "Glut"),
    (500, "Feuer"),
    (1000, "Lauffeuer"),
    (2000, "Unzertrennlich"),
)

GUTSCHEINE = [
    {"id": "kuschelstunde", "kosten": 30, "name": "Kuschelstunde",
     "beschreibung": "Eine Stunde Nähe ohne Handy – der andere ist ganz da"},
    {"id": "fruehstueck", "kosten": 40, "name": "Frühstück im Bett",
     "beschreibung": "Der andere macht Frühstück und bringt es ans Bett"},
    {"id": "massage", "kosten": 60, "name": "Massage",
     "beschreibung": "20 Minuten Massage – der andere gibt, du nimmst"},
    {"id": "freie_wahl", "kosten": 80, "name": "Freie Wahl",
     "beschreibung": "Du bestimmst heute Film, Essen oder Programm"},
    {"id": "date_abend", "kosten": 100, "name": "Date-Abend",
     "beschreibung": "Der andere plant einen Abend für euch zwei"},
    {"id": "wunsch_abend", "kosten": 150, "name": "Wunsch-Abend",
     "beschreibung": "Ein Abend ganz nach deinen Wünschen – der andere macht mit"},
]


def gutschein(gid: str) -> dict | None:
    return next((g for g in GUTSCHEINE if g["id"] == gid), None)


def rang(punkte: int) -> str:
    titel = RAENGE[0][1]
    for ab, name in RAENGE:
        if punkte >= ab:
            titel = name
    return titel


def naechster_rang(punkte: int) -> tuple[int, str] | None:
    for ab, name in RAENGE:
        if punkte < ab:
            return ab, name
    return None


async def buchen(rolle: str, delta: int, grund: str) -> dict:
    """Punkte des Slots um `delta` ändern (nie unter 0). {alt, neu, delta}."""
    profil = await qdrant.get_user_profile(rolle) or {}
    alt = int(profil.get("punkte", 0) or 0)
    neu = max(0, alt + int(delta))
    if neu != alt:
        await qdrant.patch_profile_fields(rolle, {"punkte": neu})
    logger.info("Vanilla-Punkte %s: %d → %d (%s)", rolle, alt, neu, grund)
    return {"alt": alt, "neu": neu, "delta": neu - alt}


async def erledigt(rolle: str, task: dict | None = None) -> dict:
    """Verdienst für eine erledigte Aufgabe: Basis + Streak-Bonus + Würfel-Bonus,
    Streak zählt pro Kalendertag. Löst eine laufende Wette (gewonnen) auf.
    Rückgabe {punkte, streak, boni, wette_gewinn, alt}."""
    task = task or {}
    profil = await qdrant.get_user_profile(rolle) or {}
    jetzt = datetime.now(ZoneInfo(config.TIMEZONE))
    heute = jetzt.date().strftime("%Y-%m-%d")
    gestern = (jetzt.date() - timedelta(days=1)).strftime("%Y-%m-%d")
    letzter = profil.get("letzter_streak_tag", "")
    if letzter == heute:
        streak = int(profil.get("streak", 0) or 0)
    elif letzter == gestern:
        streak = int(profil.get("streak", 0) or 0) + 1
    else:
        streak = 1
    boni: list[tuple[str, int]] = [("Basis", PUNKTE_ERLEDIGT)]
    if streak >= STREAK_BONUS_AB:
        boni.append((f"Streak {streak}🔥", STREAK_BONUS))
    if task.get("quelle") == "vanilla_wuerfel":
        boni.append(("Würfel 🎲", WUERFEL_BONUS))
    gewinn = 0
    wette = profil.get("vanilla_wette") or {}
    if wette.get("einsatz"):
        gewinn = int(wette["einsatz"]) * 2
        boni.append(("Wette gewonnen 🎰", gewinn))
    alt = int(profil.get("punkte", 0) or 0)
    neu = alt + sum(p for _, p in boni)
    await qdrant.patch_profile_fields(rolle, {
        "punkte": neu, "streak": streak,
        "streak_max": max(int(profil.get("streak_max", 0) or 0), streak),
        "letzter_streak_tag": heute,
        "vanilla_wette": None,
    })
    logger.info("Vanilla erledigt (%s): +%d → %d, Streak %d", rolle, neu - alt, neu, streak)
    return {"punkte": neu, "alt": alt, "streak": streak, "boni": boni, "wette_gewinn": gewinn}


async def nicht_erledigt(rolle: str) -> int:
    """Kein Abzug (Strafen aus) – nur eine laufende Wette verfällt.
    Rückgabe: verlorener Einsatz (0 = keine Wette)."""
    profil = await qdrant.get_user_profile(rolle) or {}
    wette = profil.get("vanilla_wette") or {}
    einsatz = int(wette.get("einsatz", 0) or 0)
    if einsatz:
        await qdrant.patch_profile_fields(rolle, {"vanilla_wette": None})
        logger.info("Vanilla-Wette verloren (%s): %d Punkte", rolle, einsatz)
    return einsatz


async def wette_setzen(rolle: str, einsatz: int) -> dict | None:
    """Einsatz abziehen und Wette am Slot-Profil merken. None, wenn nicht möglich
    (läuft schon / zu wenig Punkte)."""
    profil = await qdrant.get_user_profile(rolle) or {}
    if (profil.get("vanilla_wette") or {}).get("einsatz"):
        return None
    punkte = int(profil.get("punkte", 0) or 0)
    if einsatz not in WETT_EINSAETZE or punkte < einsatz:
        return None
    await qdrant.patch_profile_fields(rolle, {
        "punkte": punkte - einsatz,
        "vanilla_wette": {"einsatz": einsatz, "gesetzt_am": datetime.now(ZoneInfo(config.TIMEZONE)).isoformat()},
    })
    return {"einsatz": einsatz, "rest": punkte - einsatz}
