"""
Anruf, Profil-Listen, Abwesenheit, Wunsch.

Herausgelöst aus bot/services/miniapp.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.services.miniapp.
"""
from bot.services.miniapp import kern, uebersicht



async def _anruf(paar_id: str, wav: bytes) -> dict:
    """Sprech-Runde mit dem Coach ('Telefonieren'): WAV → Whisper → Coach-Antwort
    (voller System-Prompt + GEMEINSAME Chat-History wie der Text-Chat, aber ohne
    Aufgaben-Extraktion) → Ara-TTS. Muster _chat_antwort inkl. History-Rollback."""
    import base64
    import re as _re
    from bot import state
    from bot.handlers import domina as dom_handler
    from bot.services import embeddings, grok, paare, qdrant, stt as stt_service, tts
    with paare.kontext(paar_id):
        if not stt_service.aktiv():
            return {"fehler": "STT ist nicht konfiguriert"}
        pcm, rate, width, channels = stt_service._wav_parameter(wav)
        transkript = await stt_service._wyoming_transcribe(pcm, rate, width, channels)
        if not transkript:
            return {"fehler": "Ich habe dich nicht verstanden – sprich nochmal."}
        try:
            qv = await embeddings.get_embedding(transkript)
        except Exception:
            qv = None
        dp = await qdrant.get_user_profile("domina") or {}
        sp = await qdrant.get_user_profile("sklave") or {}
        chat_id = paare.dom_chat_id()
        system = await dom_handler._baue_system_prompt(
            chat_id, dp, sp, dp.get("aktuelles_level", 1), qv)
        system += (
            "\n\nGERADE TELEFONIERT IHR (Sprachgespräch in der App): antworte wie "
            "gesprochen – 2 bis 4 kurze Sätze, keine Listen, kein Markdown. Sparsame "
            "Sprech-Tags sind erlaubt ([pause], <soft>…</soft>). KEIN [AUFGABE:]- "
            "und KEIN [SPRACHNACHRICHT:]-Tag – am Telefon wird nur geredet."
        )
        state.add_message(chat_id, "user", transkript)
        try:
            antwort = await grok.chat(system, state.get_history(chat_id))
        except Exception:
            state.remove_last_message(chat_id, "user")
            raise
        antwort = _re.sub(r"\[(?:AUFGABE|SPRACHNACHRICHT):[^\]]*\]?", "", antwort).strip()
        state.add_message(chat_id, "assistant", antwort)
        ogg = await tts.synthesize(antwort, rolle=tts.ROLLE_COACH)
    return {
        "transkript": transkript,
        "antwort": tts.entferne_sprech_tags(antwort),
        "audio": base64.b64encode(ogg).decode() if ogg else "",
    }


async def _profil_liste_setzen(paar_id: str, rolle: str, feld: str, werte: list) -> dict:
    """Eigene Profil-Liste (Vorlieben/Limits bzw. Interessen/Grenzen) ersetzen –
    wie der /profil-Edit: patch_profile_fields mit erlaube_geschuetzt (der Owner
    darf die eigenen hard_limits pflegen, automatische Schreiber nicht)."""
    from bot.services import paare, qdrant
    if feld in ("inventar", "inventar_wunsch"):
        # Paar-Listen (beide Rollen dürfen) – Deckel/Dedupe macht der Service
        from bot.services import inventar
        with paare.kontext(paar_id):
            v, w = await inventar.setze(
                vorhanden_neu=werte if feld == "inventar" else None,
                wuensche_neu=werte if feld == "inventar_wunsch" else None,
            )
        return {"ok": True, "werte": v if feld == "inventar" else w}
    if feld not in uebersicht._PROFIL_LISTEN.get(rolle, ()):
        return {"fehler": f"Feld '{feld}' ist für diese Rolle nicht editierbar"}
    sauber = []
    for w in werte[:60]:
        w = str(w).strip()[:200]
        if w and w not in sauber:
            sauber.append(w)
    with paare.kontext(paar_id):
        await qdrant.patch_profile_fields(rolle, {feld: sauber}, erlaube_geschuetzt=True)
    return {"ok": True, "werte": sauber}


async def _abwesenheit_setzen(paar_id: str, rolle: str, body: dict) -> dict:
    """Abwesenheit eintragen/aufheben – gleicher Ablauf wie /abwesend inkl.
    Partner-Info (ein Zustand pro Paar, bewusst KEIN Job-Stopp)."""
    from datetime import date
    from bot.handlers import abwesenheit as abw
    from bot.messages import t
    from bot.services import paare, persona_config
    with paare.kontext(paar_id):
        if body.get("ende"):
            hatte = persona_config.abwesenheit() is not None
            await persona_config.set_abwesenheit(None, None)
            if hatte:
                await abw._informiere_partner(kern._BOT, rolle, t("ABWESEND_PARTNER_AUFGEHOBEN"))
            return {"ok": True}
        try:
            von = date.fromisoformat(str(body.get("von", "")))
            bis = date.fromisoformat(str(body.get("bis", "")))
        except ValueError:
            return {"fehler": "Datum unverständlich (JJJJ-MM-TT)"}
        if bis < von or bis < date.today():
            return {"fehler": "Zeitraum liegt rückwärts oder komplett in der Vergangenheit"}
        grund = str(body.get("grund", "")).strip()[:100]
        await persona_config.set_abwesenheit(von, bis, grund)
        await abw._informiere_partner(
            kern._BOT, rolle,
            t("ABWESEND_PARTNER_GESETZT", zeitraum=abw._zeitraum_text(von, bis),
              grund=f" ({grund})" if grund else ""))
    return {"ok": True}


async def _wunsch_einreichen(paar_id: str, text: str) -> dict:
    """Wunsch aus der App – gleicher Ablauf wie handlers/wunsch.handle:
    speichern, Domina mit Annehmen/Ablehnen-Buttons benachrichtigen (deren
    Callback ist modus-unabhängig), Entscheidungs-Modus nur wenn sie frei ist."""
    from datetime import datetime, timezone
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from bot import state
    from bot.messages import t
    from bot.services import paare, qdrant, telegram_helper
    with paare.kontext(paar_id):
        wunsch_id = await qdrant.save_wunsch("sklave", {
            "text": text,
            "datum": datetime.now(timezone.utc).isoformat(),
            "status": "eingereicht",
        })
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(t("BUTTON_ANNEHMEN"),
                                 callback_data=f"wunsch:annehmen:{wunsch_id}"),
            InlineKeyboardButton(t("BUTTON_ABLEHNEN"),
                                 callback_data=f"wunsch:ablehnen:{wunsch_id}"),
        ]])
        state.get(paare.dom_chat_id())["wunsch_id"] = wunsch_id
        if state.get_mode(paare.dom_chat_id()) == "chat":
            state.set_mode(paare.dom_chat_id(), "wunsch_entscheidung")
            nachricht = t("WUNSCH_AN_DOMINA", text=text)
        else:
            nachricht = t("WUNSCH_AN_DOMINA_WARTEND", text=text)
        await telegram_helper.send_domina(kern._BOT, nachricht, parse_mode="Markdown",
                                          reply_markup=keyboard)
    return {"ok": True}
