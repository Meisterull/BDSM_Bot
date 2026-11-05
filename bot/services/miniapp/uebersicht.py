"""
Cockpit-Daten (Übersicht, Rang, Sparziel, Wette).

Herausgelöst aus bot/services/miniapp.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.services.miniapp.
"""
from bot import config



# Welche Listen-Felder jede Rolle im EIGENEN Profil pflegen darf – exakt die
# Listen aus handlers/profil.py (DOMINA_FELDER/SKLAVE_FELDER).
_PROFIL_LISTEN = {
    "domina": ("interessen", "grenzen"),
    "sklave": ("vorlieben", "hard_limits"),
}


async def _uebersicht_daten(paar_id: str, rolle: str) -> dict:
    from bot.services import kategorie_logik, paare, persona_config, qdrant
    with paare.kontext(paar_id):
        profil = await qdrant.get_user_profile("sklave") or {}
        score = await qdrant.get_vertrauens_score("sklave")
        tasks = await qdrant.get_tasks_by_status(
            ["offen", "erledigt", "nicht_erledigt"], sort_by_datum=True)
        erledigt_gesamt = await qdrant.get_completed_task_count("sklave")
        stimmung = await qdrant.get_latest_stimmung("sklave", max_stunden=48)
        # Eigene editierbare Listen (Editor) + Abwesenheits-Zustand (Kalender)
        eigenes = profil if rolle == "sklave" else (await qdrant.get_user_profile("domina") or {})
        profil_listen = {f: list(eigenes.get(f, []) or []) for f in _PROFIL_LISTEN[rolle]}
        # Inventar 🧰: Paar-Listen, beide Rollen editieren dieselben (Service-Cache)
        from bot.services import inventar
        profil_listen["inventar"] = inventar.vorhanden()
        profil_listen["inventar_wunsch"] = inventar.wuensche()
        a = persona_config.abwesenheit()
        abwesenheit = ({"von": a[0].isoformat(), "bis": a[1].isoformat(), "grund": a[2]}
                       if a else None)
    offene = [
        {"aufgabe": t.get("aufgabe", ""), "erteilt_am": (t.get("erteilt_am") or "")[:10],
         "kategorie": t.get("kategorie", ""),
         # id + Serien-Marker für die Werkstatt (Löschen-Knopf, Dom-Seite)
         "id": t.get("qdrant_point_id", ""),
         "serie": bool(t.get("serie_id") or t.get("kette_id"))}
        for t in tasks if t.get("status") == "offen"
    ][:10]
    letzte = [
        {"aufgabe": t.get("aufgabe", ""), "status": t.get("status", ""),
         "erteilt_am": (t.get("erteilt_am") or "")[:10]}
        for t in tasks if t.get("status") in ("erledigt", "nicht_erledigt")
    ][:8]
    # Abzeichen: NUR verdiente (wie punkte.format_abzeichen) – unverdiente und
    # damit auch die geheimen ZIELE tauchen nirgends auf; verdiente geheime
    # bekommen den 🤫-Marker.
    from bot.services.punkte import GEHEIME_ABZEICHEN, SKLAVE_ABZEICHEN
    verdient = set(profil.get("abzeichen", []) or [])
    abzeichen = [
        {"emoji": a["emoji"], "name": a["name"],
         "beschreibung": a["beschreibung"], "geheim": bool(a.get("geheim"))}
        for a in SKLAVE_ABZEICHEN + GEHEIME_ABZEICHEN if a["id"] in verdient
    ]
    # Aktive Privilegien + laufende Wette (Anzeige, wie /stats)
    privilegien = [
        {"name": p.get("name", "?"), "gueltig_bis": (p.get("gueltig_bis") or "")[:10]}
        for p in (profil.get("aktive_privilegien", []) or [])
        if p.get("domina_bestaetigt") and not p.get("verbraucht")
    ]
    wette = (profil.get("wette") or {}).get("einsatz") or 0
    # Wochen-Verlauf: erledigte Aufgaben je ISO-Woche (letzte 8) – eine echte
    # Punkte-Historie gibt es nicht (nur der aktuelle Stand liegt im Profil).
    from datetime import datetime, timedelta, timezone
    heute = datetime.now(timezone.utc).date()
    montag = heute - timedelta(days=heute.weekday())
    wochen = [(montag - timedelta(weeks=w)) for w in range(7, -1, -1)]
    verlauf = []
    for start in wochen:
        ende = start + timedelta(days=7)
        n = sum(1 for t in tasks
                if t.get("status") == "erledigt"
                and start.isoformat() <= (t.get("erteilt_am") or "") < ende.isoformat())
        verlauf.append({"woche": start.strftime("%d.%m."), "erledigt": n})
    return {
        "rolle": rolle,
        "punkte": profil.get("punkte", 0),
        "streak": profil.get("streak", 0),
        "streak_max": profil.get("streak_max", 0),
        "vertrauen": {"score": score.get("score", 0), "stufe": score.get("stufe", ""),
                      "quote": score.get("quote", 0)},
        "erledigt_gesamt": erledigt_gesamt,
        "abzeichen_anzahl": len(profil.get("abzeichen", [])),
        "offene_tasks": offene,
        "letzte_tasks": letzte,
        "stimmung": (stimmung or {}).get("zusammenfassung", ""),
        "grok_tts": bool(config.GROK_TTS),
        # Werkstatt-Dropdown (Dom-Seite): alle Kategorien inkl. Profil-Zusätze
        "kategorien": kategorie_logik.alle_kategorien(profil) if rolle == "domina" else [],
        "profil_listen": profil_listen,
        "abwesenheit": abwesenheit,
        "abzeichen": abzeichen,
        "privilegien": privilegien,
        "wette_einsatz": wette,
        "verlauf": verlauf,
        # Währung ⭐: Rang, Sparziel, laufende Herrin-Wette
        "rang": _rang_payload(profil),
        "sparziel": _sparziel_payload(profil),
        "herrin_wette": _herrin_wette_payload(profil),
    }


def _rang_payload(profil: dict) -> dict:
    from bot.services import waehrung
    punkte = int(profil.get("punkte", 0) or 0)
    _, titel = waehrung.rang(punkte)
    naechster = waehrung.naechster_rang(punkte)
    return {"titel": titel, "naechster": naechster[1] if naechster else None,
            "naechster_ab": naechster[0] if naechster else None}


def _sparziel_payload(profil: dict) -> dict | None:
    from bot.services import inventar
    ziel = inventar.sparziel()
    if not ziel:
        return None
    punkte = int(profil.get("punkte", 0) or 0)
    return {"wunsch": ziel[0], "preis": ziel[1], "stand": min(punkte, ziel[1])}


def _herrin_wette_payload(profil: dict) -> dict | None:
    w = profil.get("herrin_wette") or {}
    if w.get("status") not in ("angeboten", "laeuft", "gefragt", "abgelehnt"):
        return None
    return {"einsatz": w.get("einsatz", 0), "frist": (w.get("frist") or "")[:10],
            "status": w.get("status")}
