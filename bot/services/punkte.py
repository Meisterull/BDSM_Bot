"""
Punkte, Streak und Abzeichen Service.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bot import config
from bot.services import qdrant


def _katalog() -> list[str]:
    from bot.services import kategorie_logik  # lazy: zirkulären Import vermeiden
    return kategorie_logik.katalog_kategorien()

# ---------------------------------------------------------------------------
# Abzeichen Definitionen
# ---------------------------------------------------------------------------

SKLAVE_ABZEICHEN = [
    {"id": "erster_task",       "emoji": "🏁", "name": "Erster Schritt",      "beschreibung": "Ersten Task erledigt"},
    {"id": "streak_5",          "emoji": "🔥", "name": "Auf Kurs",            "beschreibung": "5er Streak erreicht"},
    {"id": "streak_10",         "emoji": "⚡", "name": "Unaufhaltsam",        "beschreibung": "10er Streak erreicht"},
    {"id": "streak_30",         "emoji": "👑", "name": "Eiserne Disziplin",   "beschreibung": "30er Streak erreicht"},
    {"id": "punkte_100",        "emoji": "💯", "name": "Hundert Punkte",      "beschreibung": "100 Punkte erreicht"},
    {"id": "punkte_500",        "emoji": "🏆", "name": "Tribut-Sammler",      "beschreibung": "500 Punkte erreicht"},
    {"id": "tasks_25",          "emoji": "🌟", "name": "Treuer Diener",       "beschreibung": "25 Tasks erledigt"},
    {"id": "tasks_100",         "emoji": "🎖️", "name": "Hingebungsvoll",      "beschreibung": "100 Tasks erledigt"},
    {"id": "vielfalt_5",        "emoji": "🎨", "name": "Vielseitig",          "beschreibung": "5 verschiedene Kategorien erledigt"},
    {"id": "vielfalt_15",       "emoji": "🌈", "name": "Erfahren",            "beschreibung": "15 verschiedene Kategorien erledigt"},
    {"id": "vielfalt_alle",     "emoji": "🦄", "name": "Allrounder",          "beschreibung": "Mind. 1 Task in jeder Kategorie"},
    {"id": "privileg_erstes",   "emoji": "🎁", "name": "Erstes Privileg",     "beschreibung": "Erstes Privileg eingelöst"},
    {"id": "privileg_5",        "emoji": "💝", "name": "Belohnt",             "beschreibung": "5 Privilegien eingelöst"},
    {"id": "arc_erste",         "emoji": "📖", "name": "Storyline-Held",      "beschreibung": "Erste Storyline abgeschlossen"},
    {"id": "wuerfel_mutig",     "emoji": "🎲", "name": "Mutprobe bestanden",  "beschreibung": "Erste Würfel-Aufgabe erledigt"},
]

DOMINA_ABZEICHEN = [
    {"id": "level_1",  "emoji": "🌱", "name": "Lernende",  "beschreibung": "Level 1 abgeschlossen"},
    {"id": "level_3",  "emoji": "👑", "name": "Herrin",    "beschreibung": "Level 3 erreicht"},
    {"id": "level_5",  "emoji": "🔮", "name": "Meisterin", "beschreibung": "Level 5 erreicht"},
    {"id": "level_10", "emoji": "💎", "name": "Legende",   "beschreibung": "Level 10 erreicht"},
]

# Versteckte Abzeichen – tauchen NIRGENDS als Ziel auf (nicht in /stats-Übersicht,
# kein Fortschrittshinweis), nur der Erwerb wird verkündet. Überraschung > Ankündigung.
GEHEIME_ABZEICHEN = [
    {"id": "nachtaktiv", "emoji": "🦉", "name": "Nachtaktiv",
     "beschreibung": "Mitten in der Nacht Bericht erstattet", "geheim": True},
    {"id": "romanautor", "emoji": "📜", "name": "Romanautor",
     "beschreibung": "Gefühlsbericht mit über 500 Zeichen", "geheim": True},
    {"id": "blitz", "emoji": "💨", "name": "Blitzschnell",
     "beschreibung": "Aufgabe innerhalb einer Stunde erledigt", "geheim": True},
    {"id": "dreifach", "emoji": "🎯", "name": "Im Rausch",
     "beschreibung": "Drei Aufgaben an einem einzigen Tag erledigt", "geheim": True},
]

def _abzeichen(katalog: list[dict], abzeichen_id: str) -> dict | None:
    """Katalog-Lookup ohne StopIteration-Footgun (z.B. nach Katalog-Umbenennungen)."""
    return next((a for a in katalog if a["id"] == abzeichen_id), None)


PUNKTE_PRO_TASK = 10
STREAK_BONUS_AB = 5
STREAK_BONUS_PUNKTE = 5
WUERFEL_BONUS = 5      # Risiko-Bonus für Würfel-Tasks
BLITZ_BONUS = 10       # Countdown-Bonus für geschaffte Blitzaufgaben
VIELFALT_BONUS = 5     # Bonus für selten erledigte Kategorien
ARC_BONUS = 10         # Bonus für Storyline-Tasks
WOCHENENDE_FAKTOR = 2  # Wochenend-Multiplikator

# ---------------------------------------------------------------------------
# Punkte & Streak
# ---------------------------------------------------------------------------

async def task_erledigt(task: dict | None = None, gefuehl_text: str = "") -> dict:
    """
    Wird nach erfolgreich erledigtem Task aufgerufen.
    Gibt dict mit neuen Werten, Bonus-Breakdown und neuen Abzeichen zurück.

    `task` ist der Task-Payload für Bonus-Berechnung (quelle, kategorie, arc_id).
    Wenn None, gibt's nur den Basis-Punkt + Streak-Bonus.
    `gefuehl_text` (der Gefühlsbericht) fließt nur in die geheimen Abzeichen ein.
    """
    task = task or {}
    kat = task.get("kategorie")

    async def _kat_haeufigkeit() -> int:
        return await qdrant.get_completed_count_by_kategorie("sklave", kat) if kat else 0

    # Unabhängige Loads parallel statt 4-5 seriell (D9/A2) – dieser Pfad läuft
    # bei JEDER Task-Erledigung unter dem Paar-Lock.
    import asyncio
    profil, tasks_gesamt, kategorien_erledigt, kat_haeufigkeit = await asyncio.gather(
        qdrant.get_user_profile("sklave"),
        qdrant.get_completed_task_count("sklave"),
        qdrant.get_completed_kategorien_set("sklave"),
        _kat_haeufigkeit(),
    )
    profil = profil or {}

    punkte = profil.get("punkte", 0)
    # Streak: +1 pro Kalendertag, nicht pro Task – aber nur bei lückenloser Folge.
    jetzt = datetime.now(ZoneInfo(config.TIMEZONE))
    heute_d = jetzt.date()
    heute = heute_d.strftime("%Y-%m-%d")
    gestern = (heute_d - timedelta(days=1)).strftime("%Y-%m-%d")
    letzter_streak_tag = profil.get("letzter_streak_tag", "")
    if heute == letzter_streak_tag:
        streak = profil.get("streak", 0)  # Gleicher Tag = kein Streak-Increment
    elif letzter_streak_tag == gestern:
        streak = profil.get("streak", 0) + 1  # direkt fortgesetzt
    else:
        streak = 1  # Lücke (oder allererster Tag) → Streak neu beginnen
    streak_max = max(profil.get("streak_max", 0), streak)

    # Punkte-Berechnung mit Bonus-Breakdown
    boni: list[tuple[str, int]] = [("Basis", PUNKTE_PRO_TASK)]
    if streak >= STREAK_BONUS_AB:
        boni.append((f"Streak {streak}🔥", STREAK_BONUS_PUNKTE))
    if task.get("quelle") == "wuerfel":
        boni.append(("Würfel-Risiko 🎲", WUERFEL_BONUS))
    if task.get("quelle") == "blitz":
        boni.append(("Blitz ⚡", BLITZ_BONUS))
    if task.get("arc_id"):
        boni.append(("Storyline 📖", ARC_BONUS))
    # Vielfalt: Kategorie wurde inkl. dieses Tasks ≤ 2x erledigt
    if kat and kat_haeufigkeit <= 2:
        boni.append((f"Selten ({kat}) 🎨", VIELFALT_BONUS))
    # Wochenende
    wochentag = jetzt.weekday()
    if wochentag in (5, 6):  # Sa, So
        zwischensumme = sum(p for _, p in boni)
        zusatz = zwischensumme * (WOCHENENDE_FAKTOR - 1)
        boni.append(("Wochenende 2x", zusatz))

    # Aktive Wette („Doppelt oder nichts", /wette): Einsatz wurde beim Setzen
    # abgezogen, Gewinn = doppelter Einsatz zurück. Bewusst NACH dem Wochenend-
    # Multiplikator – der Wettgewinn verdoppelt sich nicht nochmal.
    # Blitzaufgaben zählen NICHT für die Wette (beide Richtungen ausgenommen) –
    # die Wette gilt für die nächste reguläre Aufgabe.
    wette = profil.get("wette") or {}
    wette_aufgeloest = bool(wette.get("einsatz")) and task.get("quelle") != "blitz"
    if wette_aufgeloest:
        boni.append(("Wette gewonnen 🎰", int(wette["einsatz"]) * 2))

    gewonnene_punkte = sum(p for _, p in boni)
    punkte += gewonnene_punkte

    # Tages-Zähler (fürs geheime „Im Rausch"-Abzeichen) – selbstgeführt im Profil,
    # kein extra Qdrant-Scan pro Erledigung.
    if profil.get("tasks_heute_datum") == heute:
        tasks_heute = profil.get("tasks_heute", 0) + 1
    else:
        tasks_heute = 1

    # Abzeichen prüfen
    alte_abzeichen = set(profil.get("abzeichen", []))
    privilegien_eingeloest = profil.get("privilegien_eingeloest", 0)
    arcs_abgeschlossen = profil.get("arcs_abgeschlossen", 0)
    neue_abzeichen = _prüfe_sklave_abzeichen(
        punkte=punkte,
        streak=streak,
        tasks_gesamt=tasks_gesamt,
        kategorien_count=len(kategorien_erledigt),
        # Bewusst der feste Katalog (nicht der dynamische Pool aus kategorie_logik.
        # alle_kategorien): das Abzeichen-Ziel soll sich nicht rückwirkend verschieben,
        # wenn eigene Kategorien dazukommen. Konstellations-gefiltert – Kategorien,
        # die diese Kombi nie bekommen kann, zählen nicht als Ziel.
        alle_kategorien_erledigt=kategorien_erledigt.issuperset(set(_katalog())),
        privilegien_eingeloest=privilegien_eingeloest,
        arcs_abgeschlossen=arcs_abgeschlossen,
        vorhandene=alte_abzeichen,
    )
    neue_abzeichen += _prüfe_geheime_abzeichen(
        task=task, gefuehl_text=gefuehl_text, jetzt=jetzt,
        tasks_heute=tasks_heute, vorhandene=alte_abzeichen,
    )

    # Profil speichern – race-sicher per patch (nur diese Felder, kein Full-Overwrite,
    # kein Re-Embedding; Punkte/Streak/Abzeichen sind keine Embed-Felder).
    patch = {
        "punkte": punkte,
        "streak": streak,
        "streak_max": streak_max,
        "letzter_streak_tag": heute,
        "tasks_heute": tasks_heute,
        "tasks_heute_datum": heute,
        "abzeichen": list(alte_abzeichen | {a["id"] for a in neue_abzeichen}),
    }
    if wette_aufgeloest:
        patch["wette"] = {}
    await qdrant.patch_profile_fields("sklave", patch)

    return {
        "punkte": punkte,
        "streak": streak,
        "gewonnene_punkte": gewonnene_punkte,
        "boni": boni,
        "neue_abzeichen": neue_abzeichen,
    }


async def privileg_eingeloest() -> list[dict]:
    """Prüft Achievements nach Privileg-Einlösung. Gibt neue Abzeichen zurück."""
    profil = await qdrant.get_user_profile("sklave") or {}
    count = profil.get("privilegien_eingeloest", 0) + 1
    alte_abzeichen = set(profil.get("abzeichen", []))

    neue = []
    if count >= 1 and "privileg_erstes" not in alte_abzeichen:
        neue.append(_abzeichen(SKLAVE_ABZEICHEN, "privileg_erstes"))
    if count >= 5 and "privileg_5" not in alte_abzeichen:
        neue.append(_abzeichen(SKLAVE_ABZEICHEN, "privileg_5"))
    neue = [a for a in neue if a]

    await qdrant.patch_profile_fields("sklave", {
        "privilegien_eingeloest": count,
        "abzeichen": list(alte_abzeichen | {a["id"] for a in neue}),
    })
    return neue


async def arc_abgeschlossen() -> list[dict]:
    """Prüft Achievements nach Arc-Abschluss."""
    profil = await qdrant.get_user_profile("sklave") or {}
    count = profil.get("arcs_abgeschlossen", 0) + 1
    alte_abzeichen = set(profil.get("abzeichen", []))

    neue = []
    if count >= 1 and "arc_erste" not in alte_abzeichen:
        eintrag = _abzeichen(SKLAVE_ABZEICHEN, "arc_erste")
        if eintrag:
            neue.append(eintrag)

    await qdrant.patch_profile_fields("sklave", {
        "arcs_abgeschlossen": count,
        "abzeichen": list(alte_abzeichen | {a["id"] for a in neue}),
    })
    return neue


async def wuerfel_erledigt() -> list[dict]:
    """Prüft Achievements nach erstem Würfel-Task."""
    profil = await qdrant.get_user_profile("sklave") or {}
    alte_abzeichen = set(profil.get("abzeichen", []))
    if "wuerfel_mutig" in alte_abzeichen:
        return []

    eintrag = _abzeichen(SKLAVE_ABZEICHEN, "wuerfel_mutig")
    await qdrant.patch_profile_fields("sklave", {
        "abzeichen": list(alte_abzeichen | {"wuerfel_mutig"}),
    })
    return [eintrag] if eintrag else []


async def task_nicht_erledigt() -> int:
    """Streak reset bei nicht erledigtem Task. Eine aktive Wette (/wette) ist
    damit verloren – der Einsatz verfällt. Gibt den verlorenen Einsatz zurück
    (0 = keine Wette aktiv), damit der Aufrufer es ansagen kann."""
    profil = await qdrant.get_user_profile("sklave") or {}
    felder: dict = {"streak": 0}
    verloren = 0
    wette = profil.get("wette") or {}
    if wette.get("einsatz"):
        verloren = int(wette["einsatz"])
        felder["wette"] = {}
    await qdrant.patch_profile_fields("sklave", felder)
    return verloren


async def domina_level_up(neues_level: int) -> list[dict]:
    """
    Prüft ob Domina neue Abzeichen durch Level-Up verdient.
    Gibt Liste neuer Abzeichen zurück.
    """
    profil = await qdrant.get_user_profile("domina") or {}
    alte_abzeichen = set(profil.get("abzeichen", []))
    neue_abzeichen = _prüfe_domina_abzeichen(neues_level, alte_abzeichen)

    if neue_abzeichen:
        await qdrant.patch_profile_fields("domina", {
            "abzeichen": list(alte_abzeichen | {a["id"] for a in neue_abzeichen}),
        })

    return neue_abzeichen


# ---------------------------------------------------------------------------
# Interne Helfer
# ---------------------------------------------------------------------------

def _prüfe_sklave_abzeichen(
    punkte: int,
    streak: int,
    tasks_gesamt: int,
    kategorien_count: int,
    alle_kategorien_erledigt: bool,
    privilegien_eingeloest: int,
    arcs_abgeschlossen: int,
    vorhandene: set,
) -> list[dict]:
    neue = []
    checks = [
        ("erster_task",     tasks_gesamt >= 1),
        ("streak_5",        streak >= 5),
        ("streak_10",       streak >= 10),
        ("streak_30",       streak >= 30),
        ("punkte_100",      punkte >= 100),
        ("punkte_500",      punkte >= 500),
        ("tasks_25",        tasks_gesamt >= 25),
        ("tasks_100",       tasks_gesamt >= 100),
        ("vielfalt_5",      kategorien_count >= 5),
        ("vielfalt_15",     kategorien_count >= 15),
        ("vielfalt_alle",   alle_kategorien_erledigt),
        ("privileg_erstes", privilegien_eingeloest >= 1),
        ("privileg_5",      privilegien_eingeloest >= 5),
        ("arc_erste",       arcs_abgeschlossen >= 1),
    ]
    for abzeichen_id, bedingung in checks:
        if bedingung and abzeichen_id not in vorhandene:
            eintrag = _abzeichen(SKLAVE_ABZEICHEN, abzeichen_id)
            if eintrag:
                neue.append(eintrag)
    return neue


def _prüfe_geheime_abzeichen(
    task: dict,
    gefuehl_text: str,
    jetzt: datetime,
    tasks_heute: int,
    vorhandene: set,
) -> list[dict]:
    """Versteckte Abzeichen – Bedingungen bewusst nirgends dokumentiert."""
    checks = [
        ("nachtaktiv", 2 <= jetzt.hour < 5),
        ("romanautor", len(gefuehl_text or "") >= 500),
        ("blitz", _innerhalb_stunde_erledigt(task)),
        ("dreifach", tasks_heute >= 3),
    ]
    neue = []
    for abzeichen_id, bedingung in checks:
        if bedingung and abzeichen_id not in vorhandene:
            eintrag = _abzeichen(GEHEIME_ABZEICHEN, abzeichen_id)
            if eintrag:
                neue.append(eintrag)
    return neue


def _innerhalb_stunde_erledigt(task: dict) -> bool:
    """True wenn zwischen Erteilung und jetzt höchstens eine Stunde liegt."""
    erteilt = task.get("erteilt_am") or ""
    try:
        von = datetime.fromisoformat(erteilt)
        if von.tzinfo is None:
            from datetime import timezone as _tz
            von = von.replace(tzinfo=_tz.utc)
        from datetime import timezone as _tz
        return (datetime.now(_tz.utc) - von).total_seconds() <= 3600
    except (ValueError, TypeError):
        return False


def _prüfe_domina_abzeichen(level: int, vorhandene: set) -> list[dict]:
    neue = []
    checks = [
        ("level_1",  level >= 1),
        ("level_3",  level >= 3),
        ("level_5",  level >= 5),
        ("level_10", level >= 10),
    ]
    for abzeichen_id, bedingung in checks:
        if bedingung and abzeichen_id not in vorhandene:
            eintrag = _abzeichen(DOMINA_ABZEICHEN, abzeichen_id)
            if eintrag:
                neue.append(eintrag)
    return neue


def format_abzeichen(abzeichen_ids: list[str], domina: bool = False) -> str:
    """Formatiert Abzeichen-Liste für Anzeige im Profil. Verdiente geheime
    Abzeichen erscheinen mit 🤫-Marker; unverdiente tauchen nirgends auf."""
    alle = DOMINA_ABZEICHEN if domina else SKLAVE_ABZEICHEN + GEHEIME_ABZEICHEN
    if not abzeichen_ids:
        return "Noch keine Abzeichen"
    zeilen = []
    for a in alle:
        if a["id"] in abzeichen_ids:
            marker = " 🤫" if a.get("geheim") else ""
            zeilen.append(f"{a['emoji']} *{a['name']}*{marker} – {a['beschreibung']}")
    return "\n".join(zeilen) if zeilen else "Noch keine Abzeichen"