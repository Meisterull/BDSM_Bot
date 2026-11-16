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
        # Vorlieben je Zeile statt komma-verkettet: mehrere tragen Zusätze mit
        # Richtung/Bedingung („nur bei …, nie umgekehrt"), die in einer flachen
        # Komma-Liste vom Eintrag abgetrennt werden.
        vorlieben = [v for v in (sp.get("vorlieben", []) or [])[:10] if v]
        vorlieben_block = ("\n" + "\n".join(f"  - {v}" for v in vorlieben)) if vorlieben else " –"
        prompt = (
            f"Kategorie für das Training: {kategorie}\n"
            f"Erfahrungsstand der Domina: {dp.get('erfahrungsstand', 'Anfänger')}\n"
            f"{coach_persona.level_zeile(level)}\n"
            f"Ihre Interessen: {'; '.join(dp.get('interessen', []) or []) or '–'}\n"
            f"Seine Vorlieben (Auszug):{vorlieben_block}\n"
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
    """Psycho-Training wie /training: Übung generieren und ablegen. typ leer =
    automatischer Typ-Wechsel (nur echte Übungen zählen – Kategorie-Trainings
    nicht). Die ID geht mit zurück, damit eine Antwort am selben Eintrag landet."""
    from bot.handlers import training as tr
    from bot.services import paare
    # BEWUSST ohne TRAINING_ENABLED-Gate: das Flag schaltet den Zeit-Job und
    # /training im Chat – die App-Übung ist manuell auf Abruf und hat keine
    # Job-Nebenwirkungen.
    with paare.kontext(paar_id):
        daten = await tr.neue_uebung(typ)
        if not daten:
            return {"fehler": "Übung verletzte auch im zweiten Versuch Limits"}
        training_id = await tr.uebung_ablegen(daten, quelle="app")
    return {"typ": daten["typ"], "uebung": daten["uebung"], "id": training_id}


async def _psycho_antwort(paar_id: str, typ: str, uebung: str, antwort: str,
                          training_id: str = "") -> dict:
    """Antwort der Domina → Coach-Feedback + Ablage am Eintrag der Übung (Kern
    wie im Chat: handlers/training.antwort_verarbeiten). Übung/Typ/ID hält die
    App clientseitig statt im Chat-State."""
    import uuid
    from bot.handlers import training as tr
    from bot.services import paare, qdrant
    if typ not in tr.TRAINING_TYPEN:
        return {"fehler": "unbekannter Trainings-Typ"}
    try:
        training_id = str(uuid.UUID(training_id)) if training_id else ""
    except ValueError:
        training_id = ""
    with paare.kontext(paar_id):
        # Die ID kommt vom Client: geschrieben wird nur auf einen Eintrag, der
        # diesem Paar gehört – sonst entsteht ein neuer.
        if training_id and not await qdrant.get_training_by_id(training_id):
            training_id = ""
        feedback = await tr.antwort_verarbeiten(typ, uebung, antwort, training_id)
    return {"feedback": feedback}
