"""
Mini-App im Vanilla-Modus (Etappe 4, 05.11.2026): Cockpit und Aktionen für
BEIDE Seiten – dieselben Kerne wie die Chat-Befehle (handlers/vanilla_spiele),
nur über HTTP. Die Seite (webapp/index.html) zeigt bei `modus == "vanilla"`
eine eigene Ansicht; die BDSM-Routen (Werkstatt, Studio, Training, …) sind in
diesem Modus gesperrt (server.py).
"""
import logging
from bot.services.miniapp import kern

logger = logging.getLogger(__name__)

# Aktionen der Seite -> (Kern, Pflichtfeld). Beide Rollen, je eigene Daten.
AKTIONEN = ("erledigt", "wuerfel", "wunsch", "gutschein", "wette", "termin", "wochenplan")


async def _uebersicht_vanilla(paar_id: str, rolle: str) -> dict:
    from bot.prompts import vanilla as vp
    from bot.services import inventar, paare, persona_config, qdrant, vanilla_punkte as vpkt
    from bot.services.miniapp import uebersicht
    from bot.handlers import vanilla_spiele as vs
    with paare.kontext(paar_id):
        andere = vp.andere_rolle(rolle)
        profil = await qdrant.get_user_profile(rolle) or {}
        partner = await qdrant.get_user_profile(andere) or {}
        eigene_tasks = await qdrant.get_tasks_by_status(
            ["offen", "gefragt", "erledigt", "nicht_erledigt"], sort_by_datum=True, user_id=rolle)
        erledigt_gesamt = await qdrant.get_completed_task_count(rolle)
        termine = []
        for r in (paare.ROLLE_DOM, paare.ROLLE_SUB):
            for x in await qdrant.get_tasks_by_status(["geplant"], user_id=r, quelle="vanilla_termin"):
                termine.append({"text": x.get("aufgabe", ""), "datum": x.get("termin_datum", ""),
                                "von": "ich" if r == rolle else "partner"})
        termine.sort(key=lambda x: x["datum"])
        stimmung = await qdrant.get_latest_stimmung(rolle, max_stunden=48)
        stimmung_partner = await qdrant.get_latest_stimmung(andere, max_stunden=48)
        try:
            from bot.scheduler import vanilla as sv
            plaene = await sv._letzte_wochenplaene(limit=1)
        except Exception:
            plaene = []
        profil_listen = {f: list(profil.get(f, []) or []) for f in uebersicht._PROFIL_LISTEN[rolle]}
        profil_listen["inventar"] = inventar.vorhanden()
        profil_listen["inventar_wunsch"] = inventar.wuensche()
        a = persona_config.abwesenheit()
        abwesenheit = ({"von": a[0].isoformat(), "bis": a[1].isoformat(), "grund": a[2]} if a else None)
        ich, pp = vp.person(rolle), vp.person(andere)
        partner_name = pp["anrede"] or ("deine Partnerin" if pp["fem"] else "dein Partner")
        punkte = int(profil.get("punkte", 0) or 0)
        p_punkte = int(partner.get("punkte", 0) or 0)
        naechster = vpkt.naechster_rang(punkte)
        wette = (profil.get("vanilla_wette") or {}).get("einsatz") or 0
        stil = persona_config.persona_stil()
        from bot.prompts import persona_presets
        stimme = persona_presets.aktuelles_label()
    offene = [
        {"id": x.get("qdrant_point_id", ""), "aufgabe": x.get("aufgabe", ""),
         "erteilt_am": (x.get("erteilt_am") or "")[:10], "quelle": x.get("quelle", "")}
        for x in eigene_tasks if x.get("status") in vs._OFFENE_STATUS and x.get("qdrant_point_id")
    ][:10]
    letzte = [
        {"aufgabe": x.get("aufgabe", ""), "status": x.get("status", ""),
         "erteilt_am": (x.get("erteilt_am") or "")[:10]}
        for x in eigene_tasks if x.get("status") in ("erledigt", "nicht_erledigt")
    ][:8]
    return {
        "modus": "vanilla",
        "rolle": rolle,
        "ich": ich["anrede"] or "",
        "partner_name": partner_name,
        "stimme": stimme,
        "punkte": punkte,
        "rang": {"titel": vpkt.rang(punkte), "naechster": naechster[1] if naechster else None,
                 "naechster_ab": naechster[0] if naechster else None},
        "streak": int(profil.get("streak", 0) or 0),
        "streak_max": int(profil.get("streak_max", 0) or 0),
        "erledigt_gesamt": erledigt_gesamt,
        "gutscheine_eingeloest": int(profil.get("gutscheine_eingeloest", 0) or 0),
        "wette_einsatz": wette,
        "partner": {"punkte": p_punkte, "rang": vpkt.rang(p_punkte),
                    "streak": int(partner.get("streak", 0) or 0),
                    "stimmung": (stimmung_partner or {}).get("zusammenfassung", "")},
        "stimmung": (stimmung or {}).get("zusammenfassung", ""),
        "offene_tasks": offene,
        "letzte_tasks": letzte,
        "termine": termine[:10],
        "gutscheine": [{"id": g["id"], "name": g["name"], "kosten": g["kosten"],
                        "beschreibung": g["beschreibung"], "moeglich": punkte >= g["kosten"]}
                       for g in vpkt.GUTSCHEINE],
        "wett_einsaetze": [e for e in vpkt.WETT_EINSAETZE if e <= punkte],
        "wochenplan": plaene[0] if plaene else "",
        "profil_listen": profil_listen,
        "abwesenheit": abwesenheit,
    }


async def _aktion(paar_id: str, rolle: str, name: str, body: dict) -> dict:
    """Eine Vanilla-Aktion der Seite ausführen – Rückgabe wie die Kerne
    ({fehler} oder {ok, …}). Läuft im Paar-Kontext mit dem Bot-Handle."""
    from bot.services import paare
    from bot.handlers import vanilla_spiele as vs
    bot = kern._BOT
    with paare.kontext(paar_id):
        if name == "erledigt":
            task_id = str(body.get("task_id") or "").strip()
            if not task_id:
                return {"fehler": "task_id fehlt"}
            return await vs.aufgabe_abschliessen(bot, rolle, task_id, bool(body.get("ja", True)))
        if name == "wuerfel":
            return await vs.wuerfel_idee(bot, rolle)
        if name == "wunsch":
            return await vs.wunsch_senden(bot, rolle, str(body.get("text") or ""))
        if name == "gutschein":
            return await vs.gutschein_einloesen(bot, rolle, str(body.get("id") or ""))
        if name == "wette":
            try:
                einsatz = int(body.get("einsatz", 0))
            except (TypeError, ValueError):
                einsatz = 0
            return await vs.wette_platzieren(bot, rolle, einsatz)
        if name == "termin":
            return await vs.termin_anlegen(bot, rolle, str(body.get("text") or "")[:500])
        if name == "wochenplan":
            from bot.scheduler import vanilla as sv
            ok = await sv.wochenplan_senden(bot, thema=str(body.get("thema") or "")[:100])
            return {"ok": True} if ok else {"fehler": "Plan gerade nicht möglich"}
    return {"fehler": "unbekannte Aktion"}
