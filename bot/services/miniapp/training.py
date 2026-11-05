"""
Training und Psycho-Übungen.

Herausgelöst aus bot/services/miniapp.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.services.miniapp.
"""



async def _training_erstellen(paar_id: str, kategorie: str) -> dict:
    """Kategorie-Training für die Domina: praktische Anleitung in Coach-Stimme,
    profilbewusst + kuratiertes Wissen (/lerne) zur Kategorie, Limits-geprüft
    (generate_mit_limit_retry regeneriert einmal, danach Abbruch). Wird in der
    training-Collection abgelegt (typ='kategorie') und ist in der App nachlesbar."""
    from bot.prompts import coach_persona
    from bot.services import limits_check, paare, qdrant
    with paare.kontext(paar_id):
        dp = await qdrant.get_user_profile("domina") or {}
        sp = await qdrant.get_user_profile("sklave") or {}
        level = dp.get("aktuelles_level", 1)
        system = (
            "Du schreibst der Domina eine praktische Trainings-Anleitung für EINE "
            "Kategorie – wie eine vertraute Freundin, die ihr Handwerk erklärt.\n\n"
            f"{coach_persona.fuer_coach_prompt()}\n\n"
            "Aufbau (ohne Markdown-Überschriften, einfache Absätze mit kurzen "
            "Zwischentiteln): Worum es geht und was es mit ihm macht · Vorbereitung "
            "& Sicherheit · Schritt für Schritt fürs erste/nächste Mal · Woran sie "
            "merkt, dass es gut läuft · Nachsorge. Konkret und umsetzbar, auf ihr "
            "Level und SEIN Profil zugeschnitten, 250-400 Wörter. Kein [AUFGABE:]-Tag."
        )
        prompt = (
            f"Kategorie für das Training: {kategorie}\n"
            f"Erfahrungsstand der Domina: {dp.get('erfahrungsstand', 'Anfänger')}\n"
            f"{coach_persona.level_zeile(level)}\n"
            f"Ihre Interessen: {', '.join(dp.get('interessen', []) or []) or '–'}\n"
            f"Seine Vorlieben (Auszug): {', '.join((sp.get('vorlieben', []) or [])[:10]) or '–'}\n"
            f"Seine Hard Limits (TABU): {', '.join(sp.get('hard_limits', []) or []) or '–'}\n"
        )
        sb = await coach_persona.skill_kontext_block([kategorie])
        if sb:
            prompt += "\n" + sb
        anleitung = await limits_check.generate_mit_limit_retry(
            (system, prompt), reasoning=True)
        if not anleitung:
            return {"fehler": "Anleitung verletzte auch im zweiten Versuch Limits"}
        await qdrant.save_training("domina", {
            "typ": "kategorie",
            "kategorie": kategorie,
            "anleitung": anleitung,
            "zusammenfassung": f"Kategorie-Training {kategorie}",
            "level": level,
        })
    return {"ok": True, "kategorie": kategorie, "anleitung": anleitung}


async def _trainings_liste(paar_id: str) -> dict:
    from bot.services import paare, qdrant
    with paare.kontext(paar_id):
        eintraege = await qdrant.get_training_entries("domina", limit=50)
    return {"trainings": [
        {"kategorie": e.get("kategorie", ""), "anleitung": e.get("anleitung", ""),
         "datum": (e.get("datum") or "")[:10]}
        for e in eintraege if e.get("typ") == "kategorie"
    ][:20]}


async def _psycho_uebung(paar_id: str, typ: str) -> dict:
    """Psycho-Training wie /training: Übung generieren. typ leer = automatischer
    Typ-Wechsel (nur echte Psycho-Typen zählen – Kategorie-Trainings ignorieren)."""
    from bot.handlers.training import TRAINING_TYPEN, _generiere_uebung
    from bot.services import paare, qdrant
    # BEWUSST ohne TRAINING_ENABLED-Gate: das Flag schaltet den TÄGLICHEN
    # Trainings-Job und /training im Chat – die App-Übung ist manuell auf
    # Abruf und hat keine Job-Nebenwirkungen.
    with paare.kontext(paar_id):
        dp = await qdrant.get_user_profile("domina") or {}
        eintraege = await qdrant.get_training_entries("domina", limit=10)
        psycho = [e for e in eintraege if e.get("typ") in TRAINING_TYPEN]
        kontext = ""
        if psycho:
            letzte = psycho[0]
            kontext = (f"Letztes Training ({letzte.get('datum', '')[:10]}): "
                       f"{letzte.get('typ', '')} – {letzte.get('zusammenfassung', '')[:100]}")
        if typ not in TRAINING_TYPEN:
            letzter_typ = psycho[0].get("typ", "") if psycho else ""
            try:
                typ = TRAINING_TYPEN[(TRAINING_TYPEN.index(letzter_typ) + 1) % len(TRAINING_TYPEN)]
            except ValueError:
                typ = TRAINING_TYPEN[0]
        erledigt = await qdrant.get_tasks_by_status(["erledigt"], limit=5, sort_by_datum=True)
        letzte_tasks = [t.get("aufgabe", "") for t in erledigt[:3] if t.get("aufgabe")]
        uebung = await _generiere_uebung(dp, typ, kontext, letzte_tasks)
    return {"typ": typ, "uebung": uebung}


async def _psycho_antwort(paar_id: str, typ: str, uebung: str, antwort: str) -> dict:
    """Antwort der Domina → Coach-Feedback + Ablage (Kern von handle_antwort;
    Übung/Typ hält die App clientseitig statt im Chat-State)."""
    from bot.handlers.training import TRAINING_TYPEN
    from bot.prompts import coach_persona, followup as fp
    from bot.services import grok, paare, qdrant
    if typ not in TRAINING_TYPEN:
        return {"fehler": "unbekannter Trainings-Typ"}
    with paare.kontext(paar_id):
        dp = await qdrant.get_user_profile("domina") or {}
        level = dp.get("aktuelles_level", 1)
        system = (
            "Die Domina hat gerade auf eine Trainingsübung geantwortet. Reagiere "
            "konkret darauf – wie eine vertraute Freundin.\n\n"
            f"{coach_persona.fuer_coach_prompt()}\n\n"
            "2-4 Sätze, lass es fließen, keine nummerierte Liste. Geh konkret auf "
            "ihre Antwort ein – nicht generisch ermutigen. Kein [AUFGABE: ...] Tag."
        )
        prompt = (f"Trainingstyp: {typ}\nÜbung: {uebung}\n"
                  f"{fp.nutzer_text('Ihre Antwort', antwort)}\nLevel: {level}")
        feedback = await grok.simple(prompt, system=system, reasoning=True)
        await qdrant.save_training("domina", {
            "typ": typ, "uebung": uebung, "antwort": antwort, "feedback": feedback,
            "zusammenfassung": f"{typ}: {antwort[:80]}", "level": level,
        })
    return {"feedback": feedback}
