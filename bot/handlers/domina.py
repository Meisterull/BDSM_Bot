"""
Domina Handler – normaler Chat + Aufgaben erkennen + weiterleiten.
Aufgaben werden erst nach Bestätigung + Serie-Frage weitergeleitet.
"""
import logging
import re
import uuid
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, embeddings, punkte, telegram_helper, kategorie_logik, synonyme
from bot.services import sticker_reaktionen
from bot.prompts import domina_coach, followup as fp
from bot.handlers import onboarding
from bot.messages import t

logger = logging.getLogger(__name__)

# Referenzen auf Hintergrund-Tasks (D9/A2, Muster sklave._BG_TASKS) – sonst
# kann der GC einen laufenden Task einsammeln.
_BG_TASKS: set = set()


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()

    # Onboarding prüfen
    if await onboarding.start_if_needed(update, context, "domina"):
        await onboarding.handle(update, context, "domina")
        return

    if state.get_mode(chat_id) == "onboarding":
        return

    # Aufgabe Bestätigung abwarten
    if state.get_mode(chat_id) == "aufgabe_bestaetigung":
        await _handle_aufgabe_bestaetigung(update, context, chat_id, text)
        return

    # Embedding best-effort (D9/M11, Muster sklave._erinnerung): ein
    # Ollama-Ausfall darf den Coach-Chat nicht töten – ohne Vektor läuft der
    # Gesprächs-Kontext Recency-only weiter, Grok antwortet trotzdem.
    async def _embed_best_effort() -> list[float] | None:
        try:
            return await embeddings.get_embedding(text)
        except Exception as e:
            logger.warning("Embedding fehlgeschlagen – Coach-Kontext ohne Semantik-Arm: %s", e)
            return None

    # Profile + Embedding parallel statt 3 seriell (D9/A2) – lief unter dem Paar-Lock.
    import asyncio
    profile, sklave_profile, query_vector = await asyncio.gather(
        qdrant.get_user_profile("domina"),
        qdrant.get_user_profile("sklave"),
        _embed_best_effort(),
    )
    profile = profile or {}
    sklave_profile = sklave_profile or {}
    level = profile.get("aktuelles_level", 1)

    # Keyword Aufgabe erkennen
    is_task, task_text = grok.extract_keyword_task(text)

    system = await _baue_system_prompt(chat_id, profile, sklave_profile, level, query_vector)

    response = await _chat_antwort(update, context, chat_id, system, text)
    if response is None:
        return

    # Aufgabe in Grok-Antwort erkennen
    if not is_task:
        is_task, task_text = grok.extract_task(response)
    # Nachricht an den Sklaven ([SPRACHNACHRICHT: …], s. domina_coach-Regeln)
    sn_gefunden, sn_inhalt = grok.extract_sprachnachricht(response)

    # Antwort senden (ohne [AUFGABE: ...] Tag) – Markdown mit Fallback,
    # weil der Coach Bold/Listen nutzen darf.
    clean_response = response.split("[AUFGABE:")[0].strip() if is_task else response
    # Abgeschnittenes Tag-Fragment strippen (D9/A4): kappt max_tokens die
    # Antwort MITTEN im Tag, erkennt extract_task (verlangt ']') nichts und
    # das rohe "[AUFGABE: …"-Fragment stünde wörtlich in der Coach-Nachricht.
    if not is_task:
        clean_response = re.sub(r"\[AUFGABE:[^\]]*$", "", clean_response).rstrip()
    # [SPRACHNACHRICHT:]-Tag (komplett ODER von max_tokens gekappt) nie anzeigen.
    clean_response = re.sub(r"\[SPRACHNACHRICHT:[^\]]*\]?", "", clean_response).rstrip()
    if clean_response:
        await telegram_helper.reply_markdown_safe(update.message, clean_response)
        # „Telefonieren" (Flag aus handle_voice): gesprochene Frage → Antwort
        # zusätzlich als Voice-Bubble in der Coach-Stimme (best-effort).
        if context.chat_data.get("voice_eingang"):
            await telegram_helper.voice_an(context.bot, chat_id, clean_response,
                                           empfaenger_rolle=paare.ROLLE_DOM)

    if sn_gefunden and sn_inhalt:
        await _sende_sprachnachricht_an_sklaven(update, context, sn_inhalt)

    # Aufgabe gefunden → Limits-Check, dann Bestätigung anfragen
    if is_task and task_text:
        await _starte_aufgaben_bestaetigung(
            update, chat_id, task_text, level, profile, sklave_profile,
            quelltext=text,
        )
        # Gespräch IMMER speichern (Review D6): beim gestarteten Bestätigungs-
        # Dialog ging vorher genau das aufgabenbezogene Gespräch dem Langzeit-
        # Gedächtnis verloren – die Aufgabe selbst liegt separat in `tasks`,
        # aber ihr Kontext (Wortlaut/Stimmung der Domina) nur hier.
        await _save_conversation(text, response)
        return

    await _check_level_up(update, context, profile, level)
    await _save_conversation(text, response)

    # Vorlieben / No-Gos der Domina aus dem Gespräch erkennen und als ✅/🗑-Vorschlag
    # fürs eigene Profil anbieten. Als Hintergrund-Task (D9/A2, Muster D8/N1 im
    # Sklave-Pfad): der Grok-Call hielt sonst den Paar-Lock 10-30 s für die
    # NÄCHSTE Nachricht beider Partner.
    import asyncio as _asyncio

    async def _detektor_bg() -> None:
        try:
            from bot.services import praeferenz_detektor
            await praeferenz_detektor.erkenne_und_schlage_vor(context.bot, "domina", text)
        except Exception as e:
            logger.error("Präferenz-Detektor (Domina) fehlgeschlagen: %s", e)

    _bg = _asyncio.create_task(_detektor_bg())
    _BG_TASKS.add(_bg)
    _bg.add_done_callback(_BG_TASKS.discard)


async def _sende_sprachnachricht_an_sklaven(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                            inhalt: str) -> None:
    """[SPRACHNACHRICHT:]-Tag des Coachs: Inhalt durchs Limits-Gate (wie die
    Aufgaben-Pfade, D8/H1), in der Herrin-Stimme ausformulieren (Sprech-Tags
    bei Grok-TTS) und dem Sklaven als Text (ohne Tags) + Voice zustellen."""
    from bot.services import limits_check, tts
    try:
        treffer = await limits_check.verletzungen(inhalt)
    except Exception:
        logger.exception("Limits-Check der Sprachnachricht fehlgeschlagen – nicht gesendet.")
        await update.message.reply_text(t("COACH_SPRACHNACHRICHT_FEHLER"))
        return
    if treffer:
        # verletzungen() liefert Dicts (limit/matched_via) – nur die Limit-Namen zeigen
        await update.message.reply_text(
            t("COACH_SPRACHNACHRICHT_LIMIT",
              begriffe=", ".join(sorted({v["limit"] for v in treffer}))))
        return
    try:
        nachricht = await grok.simple(fp.nachricht_an_sklaven(inhalt), max_tokens=250)
    except Exception as e:
        # Muster Ketten-Start: bei LLM-Fehler lieber den Roh-Inhalt zustellen
        # als die zugesagte Nachricht verfallen zu lassen.
        logger.error("nachricht_an_sklaven fehlgeschlagen, sende Inhalt direkt: %s", e)
        nachricht = inhalt
    try:
        await telegram_helper.send_sklave(context.bot, tts.entferne_sprech_tags(nachricht),
                                          voice_text=nachricht)
    except Exception:
        logger.exception("Sprachnachricht-Zustellung an den Sklaven fehlgeschlagen.")
        await update.message.reply_text(t("COACH_SPRACHNACHRICHT_FEHLER"))
        return
    await update.message.reply_text(t("COACH_SPRACHNACHRICHT_GESENDET"))


async def _baue_system_prompt(
    chat_id: str,
    profile: dict,
    sklave_profile: dict,
    level: int,
    query_vector: list[float] | None,
) -> str:
    """Lädt allen Chat-Kontext (Konversation, Lerntagebuch, Coach-Regeln, Rollenspiel,
    Vertrauen, Stimmung, entdeckte Wünsche) und baut den System-Prompt des Coachs."""
    # Unabhängige Kontext-Loads parallel statt seriell (Review D8/N2) – vorher
    # 6 serielle Qdrant-Roundtrips pro Domina-Nachricht.
    # limit=6 statt 12: à ~2000 Zeichen pro Eintrag wuchs der System-Prompt sonst
    # auf >30k Zeichen (plus Lerntagebücher, Dossier, Regeln).
    import asyncio
    (ctx_entries, lerntagebuch_entries, aktive_regeln, letzte_kategorien,
     vertrauens_score, stimmung_entry, quiz_wissen_entries) = await asyncio.gather(
        qdrant.get_hybrid_conversation_context("domina", query_vector, limit=6,
                                               felder=qdrant.KONTEXT_FELDER),
        qdrant.get_recent_lerntagebuch("domina", limit=3),
        qdrant.get_active_coach_regeln("domina"),
        qdrant.get_recent_task_kategorien("sklave", limit=5),
        qdrant.get_vertrauens_score("sklave"),
        qdrant.get_latest_stimmung("sklave", max_stunden=48),  # D9/N13: nur frische Stimmung als "aktuell"
        qdrant.get_recent_quiz_wissen("domina", limit=3),
    )
    context_str = domina_coach.format_context(ctx_entries)

    # Langzeit-Wissen: letzte Lerntagebuch-Einträge + Coach-Quiz-Auflösungen
    lerntagebuch_str = domina_coach.format_lerntagebuch(lerntagebuch_entries)
    quiz_wissen_str = domina_coach.format_quiz_wissen(quiz_wissen_entries)

    # Gelernte Regeln + Notizen (bestaetigt) als harter Prompt-Block
    coach_regeln_texte = [r.get("text", "") for r in aktive_regeln if r.get("typ") == "regel"]
    coach_notiz_texte = [r.get("text", "") for r in aktive_regeln if r.get("typ") == "notiz"]
    sklave_persoenlichkeit = {
        "tags": sklave_profile.get("persoenlichkeit_tags", []),
        "reaktionen": sklave_profile.get("kategorie_reaktionen", {}),
        "dossier": sklave_profile.get("dossier", ""),
        "offene_faeden": sklave_profile.get("offene_faeden", []),
    }

    # Rollenspiel-Kontext aus State
    s_domina = state.get(chat_id)
    rollenspiel = None
    if s_domina.get("szenario_name"):
        rollenspiel = {
            "szenario_name": s_domina.get("szenario_name"),
            "ton": s_domina.get("szenario_ton"),
            "intensitaet": s_domina.get("rollenspiel_intensitaet"),
            "vokabular": s_domina.get("szenario_vokabular", []),
        }

    # Schwierigkeit + Vertrauens-Score + Stimmung (Score/Stimmung aus dem gather oben)
    schwierigkeit = profile.get("aufgaben_schwierigkeit", "normal")
    stimmung = stimmung_entry.get("zusammenfassung", "") if stimmung_entry else ""

    system = domina_coach.get(
        erfahrungsstand=profile.get("erfahrungsstand", "Anfänger"),
        level=level,
        interessen=profile.get("interessen", []),
        grenzen=profile.get("grenzen", []),
        ziele=profile.get("ziele", ""),
        conversation_context=context_str,
        sklave_hard_limits=sklave_profile.get("hard_limits", []),
        sklave_vorlieben=sklave_profile.get("vorlieben", []),
        kinderfreie_zeiten=profile.get("kinderfreie_zeiten", []),
        kind_anzahl=profile.get("kind_anzahl"),
        letzte_kategorien=letzte_kategorien,
        sklave_persoenlichkeit=sklave_persoenlichkeit,
        rollenspiel=rollenspiel,
        schwierigkeit=schwierigkeit,
        vertrauens_score=vertrauens_score,
        stimmung=stimmung,
        lerntagebuch_context=lerntagebuch_str,
        quiz_wissen_context=quiz_wissen_str,
        coach_regeln=coach_regeln_texte,
        coach_notizen=coach_notiz_texte,
        domina_dossier=profile.get("domina_dossier", ""),
        kategorien_pool=kategorie_logik.alle_kategorien(sklave_profile),
    )

    # Entdeckte Wünsche als OPTIONALEN Kontext – das LLM streut sie nur ein, wenn sie
    # thematisch passen (nicht generell, nicht erzwungen).
    from bot.handlers import dossier as _dossier
    _wunsch_hinweis = await _dossier.wunsch_kontext_hinweis(sklave_profile.get("entdeckte_wuensche"))
    if _wunsch_hinweis:
        system += "\n\n" + _wunsch_hinweis

    # Abwesenheit als harter Fakt auch für den Coach – Aufgaben-Vorschläge und
    # Planung müssen den Zeitraum kennen (/abwesend).
    from bot.services import persona_config
    system += persona_config.abwesenheit_hinweis()
    return system


async def _chat_antwort(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: str,
    system: str,
    text: str,
) -> str | None:
    """Grok-Chat mit History-Pflege. Gibt die Antwort zurück oder None bei Fehler
    (dann ist die Fehlermeldung bereits gesendet)."""
    state.add_message(chat_id, "user", text)
    history = state.get_history(chat_id)
    try:
        async with telegram_helper.typing_action(context.bot, chat_id):
            response = await grok.chat(system, history)
    except Exception as e:
        logger.error("Fehler bei Grok-Chat: %s", e)
        # User-Nachricht aus History entfernen damit sie nicht unbeantwortet bleibt.
        # Über state statt history.pop(): Mutationen der Live-Liste müssen unters
        # _persist_lock, sonst Race mit dem Persist-Thread (Review Hermes 09.07.2026).
        state.remove_last_message(chat_id, "user")
        await update.message.reply_text(t("FEHLER_KEINE_ANTWORT"))
        return None
    state.add_message(chat_id, "assistant", response)
    return response


async def _starte_aufgaben_bestaetigung(
    update: Update,
    chat_id: str,
    task_text: str,
    level: int,
    profile: dict,
    sklave_profile: dict,
    quelltext: str = "",
) -> bool:
    """Limits-Check der erkannten Aufgabe; wenn sauber, Bestätigungs-Dialog starten.
    Erkennt dabei einen Termin ("am Samstag", "morgen", "26.07.") aus dem
    Original-Wortlaut der Domina (`quelltext`) bzw. dem Aufgabentext.
    Gibt True zurück, wenn der Dialog gestartet wurde (False = Aufgabe blockiert)."""
    from bot.services import limits_check
    sk_hl = sklave_profile.get("hard_limits", []) or []
    do_gr = profile.get("grenzen", []) or []
    treffer = await limits_check.verletzungen(task_text, sk_hl, do_gr)
    if treffer:
        # Nur Anzahl/Quelle ins Log – die konkreten Limit-Begriffe (intim) gehören
        # nicht auf WARNING (Logserver!), nur in die Nachricht an die Domina.
        _quellen = sorted({tr["quelle"] for tr in treffer})
        logger.warning(
            "Aufgabe aus Chat verletzt %d Grenze(n) [%s] – nicht in Bestaetigung uebernommen.",
            len(treffer), ", ".join(_quellen),
        )
        logger.debug("Chat-Aufgaben-Verletzungen: %s", limits_check.format_verletzungen(treffer))
        await telegram_helper.reply_markdown_safe(
            update.message,
            t("DOMINA_AUFGABE_GRENZEN", treffer=limits_check.format_verletzungen(treffer)),
        )
        return False

    # Termin zuerst im Original-Wortlaut suchen (der [AUFGABE:]-Tag verliert die
    # Zeitangabe oft beim Umformulieren), dann im Aufgabentext selbst.
    from bot.services import datum_erkennung
    termin = datum_erkennung.finde_termin(quelltext) or datum_erkennung.finde_termin(task_text)

    s = state.get(chat_id)
    s["pending_task_text"] = task_text
    s["pending_task_level"] = level
    s["pending_task_profile"] = profile
    s["pending_task_kategorie"] = await kategorie_logik.klassifiziere(task_text)
    s["pending_task_termin"] = termin[0].isoformat() if termin else None
    state.set_mode(chat_id, "aufgabe_bestaetigung")
    if termin:
        await update.message.reply_text(
            t("DOMINA_AUFGABE_ERKANNT_TERMIN",
              aufgabe=telegram_helper.escape_md(task_text),
              termin=telegram_helper.escape_md(datum_erkennung.format_termin(termin[0]))),
            parse_mode="MarkdownV2"
        )
    else:
        await update.message.reply_text(
            t("DOMINA_AUFGABE_ERKANNT", aufgabe=telegram_helper.escape_md(task_text)),
            parse_mode="MarkdownV2"
        )
    return True


_PENDING_TASK_KEYS = ("pending_task_text", "pending_task_level",
                      "pending_task_profile", "pending_task_kategorie",
                      "pending_task_termin")


def _weiter_zur_kette(s: dict, chat_id: str) -> None:
    """pending_* → kette_*-State umziehen und die Kette-Frage vorbereiten."""
    s["kette_erste_text"] = s.get("pending_task_text", "")
    s["kette_level"] = s.get("pending_task_level", 1)
    s["kette_profile"] = s.get("pending_task_profile", {})
    s["kette_kategorie"] = s.get("pending_task_kategorie", "allgemein")
    for key in _PENDING_TASK_KEYS:
        s.pop(key, None)
    state.set_mode(chat_id, "kette_frage")


async def _erteile_termin_aufgabe(update: Update, chat_id: str, s: dict, datum) -> None:
    """Speichert die bestätigte Aufgabe als geplanten Termin-Task. Zustellung
    übernimmt der (generische) Zustellungs-Job im Scheduler, sobald
    `zustellung_ab` erreicht ist; das Followup fragt am Zieltag zur
    Followup-Zeit nach (follow_up_datum landet via followup_in_tagen dort)."""
    from bot.services import datum_erkennung
    task_text = s.get("pending_task_text", "")
    level = s.get("pending_task_level", 1)
    kategorie = s.get("pending_task_kategorie", "allgemein")

    from bot.services import persona_config
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(config.TIMEZONE)
    h, m = config.hm(persona_config.zeit("termin_zustellung_time"))
    zustellung = datetime(datum.year, datum.month, datum.day, h, m, tzinfo=tz)
    jetzt = datetime.now(tz)
    if zustellung <= jetzt:
        zustellung = jetzt

    await qdrant.erstelle_task(
        task_text, kategorie, level,
        status="geplant", quelle="termin",
        followup_in_tagen=max((datum - jetzt.date()).days, 0),
        extra={"zustellung_ab": zustellung.astimezone(timezone.utc).isoformat(),
               "termin_datum": datum.isoformat()},
    )
    for key in _PENDING_TASK_KEYS:
        s.pop(key, None)
    state.set_mode(chat_id, "chat")
    await update.message.reply_text(
        t("DOMINA_AUFGABE_TERMIN_GEPLANT", termin=datum_erkennung.format_termin(datum)),
        parse_mode="Markdown"
    )


async def _handle_aufgabe_bestaetigung(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: str,
    text: str,
) -> None:
    """Verarbeitet die Ja/Nein Bestätigung. Mit erkanntem Termin → als geplanten
    Task speichern; ohne Termin → Rückfrage wann (sofort oder Tag X)."""
    s = state.get(chat_id)
    answer = text.lower().strip()

    if answer in synonyme.JA:
        termin_iso = s.get("pending_task_termin")
        if termin_iso:
            from datetime import date as _date
            await _erteile_termin_aufgabe(update, chat_id, s, _date.fromisoformat(termin_iso))
            return
        # Kein Termin erkannt → fragen, wann sie erteilt werden soll.
        state.set_mode(chat_id, "aufgabe_termin")
        await update.message.reply_text(t("DOMINA_AUFGABE_WANN"), parse_mode="Markdown")

    elif answer in synonyme.NEIN:
        for key in _PENDING_TASK_KEYS:
            s.pop(key, None)
        state.set_mode(chat_id, "chat")
        await update.message.reply_text(t("DOMINA_AUFGABE_VERWORFEN"))

    else:
        await update.message.reply_text(t("COMMON_JA_NEIN"))


async def handle_aufgabe_termin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Verarbeitet die Antwort auf die Wann-Rückfrage: 'sofort' → normaler Weg
    (Kette-/Serie-Frage wie bisher), Tag/Datum → geplanter Termin-Task."""
    from bot.services import datum_erkennung
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()
    s = state.get(chat_id)

    ergebnis = datum_erkennung.parse_termin_antwort(text)
    if ergebnis == "sofort":
        _weiter_zur_kette(s, chat_id)
        await update.message.reply_text(t("DOMINA_KETTE_FRAGE"), parse_mode="Markdown")
    elif ergebnis is not None:
        await _erteile_termin_aufgabe(update, chat_id, s, ergebnis)
    else:
        await update.message.reply_text(t("DOMINA_AUFGABE_WANN_UNKLAR"), parse_mode="Markdown")


async def handle_kette_frage(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Verarbeitet die Ja/Nein-Antwort auf die Kette-Frage."""
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip().lower()
    s = state.get(chat_id)

    task_text = s.get("kette_erste_text", "")
    level = s.get("kette_level", 1)
    profile = s.get("kette_profile", {})
    kategorie = s.get("kette_kategorie", "allgemein")

    if text in synonyme.NEIN:
        for key in ("kette_erste_text", "kette_level", "kette_profile", "kette_kategorie"):
            s.pop(key, None)
        # Wedge-Schutz: wirft frage_serie, darf der Chat nicht in "kette_frage" hängen
        # bleiben (frage_serie setzt bei Erfolg selbst "serie_wahl").
        state.set_mode(chat_id, "chat")
        from bot.handlers.serie_handler import frage_serie
        await frage_serie(update, context, task_text, profile, level, kategorie)
        await _check_level_up(update, context, profile, level)

    elif text in synonyme.JA:
        s["kette_aufgaben_liste"] = [task_text]
        state.set_mode(chat_id, "kette_aufgaben")
        await update.message.reply_text(
            t("DOMINA_KETTE_START", aufgabe=telegram_helper.escape_md(task_text)),
            parse_mode="MarkdownV2"
        )
    else:
        await update.message.reply_text(t("COMMON_JA_NEIN"))


async def handle_kette_aufgaben(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Sammelt weitere Aufgaben für die Kette oder schließt sie bei 'fertig' ab."""
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()
    s = state.get(chat_id)

    if text.lower() in synonyme.FERTIG:
        kette_liste = s.get("kette_aufgaben_liste", [])
        level = s.get("kette_level", 1)
        profile = s.get("kette_profile", {})
        kategorie = s.get("kette_kategorie", "allgemein")

        if not kette_liste:
            state.set_mode(chat_id, "chat")
            return

        # Kette speichern
        kette_id = str(uuid.uuid4())
        gesamt = len(kette_liste)
        erteilt_am = datetime.now(timezone.utc).isoformat()

        sklave_chat = paare.sub_chat_id()
        first_task_id = None

        # Teil-Rollback (D9/N6): wirft save_task bei Glied k, würden die
        # Glieder 1..k-1 stehen bleiben und ein erneutes "fertig" der Domina
        # legte die KOMPLETTE Liste nochmal unter neuer kette_id an (zweites
        # offenes Erst-Glied, doppelte Kette). Bei Teilfehler alles aufräumen.
        gespeicherte_ids: list[str] = []
        try:
            for position, aufgabe_text in enumerate(kette_liste, start=1):
                status = "offen" if position == 1 else "kette_wartend"
                task_id = await qdrant.save_task({
                    "user_id": "sklave",
                    "aufgabe": aufgabe_text,
                    "level": level,
                    "kategorie": kategorie,
                    "status": status,
                    "erteilt_am": erteilt_am,
                    "follow_up_datum": erteilt_am,
                    "kette_id": kette_id,
                    "kette_position": position,
                    "kette_gesamt": gesamt,
                })
                gespeicherte_ids.append(task_id)
                if position == 1:
                    first_task_id = task_id
        except Exception:
            logger.exception("Ketten-Anlage bei Glied %d/%d fehlgeschlagen – räume %d gespeicherte Glieder auf.",
                             len(gespeicherte_ids) + 1, gesamt, len(gespeicherte_ids))
            for tid in gespeicherte_ids:
                try:
                    await qdrant.loesche_task(tid)
                except Exception:
                    logger.exception("Ketten-Rollback: Glied %s nicht löschbar.", tid)
            raise

        # State aufräumen
        for key in ("kette_aufgaben_liste", "kette_erste_text",
                    "kette_level", "kette_profile", "kette_kategorie"):
            s.pop(key, None)
        state.set_mode(chat_id, "chat")

        await update.message.reply_text(t("DOMINA_KETTE_ERSTELLT", gesamt=gesamt))

        # Erste Aufgabe an Sklaven senden. Mode erst NACH erfolgreichem Send
        # (Review D8/M1): vorher stand set_followup_task VOR dem Grok-Call –
        # schlug der fehl, steckte der Sklave im Followup-Mode für eine
        # Aufgabe, die er nie gesehen hat. Bei LLM-Fehler Roh-Text senden
        # (Muster gefuehl._kette_naechster_schritt).
        if first_task_id:
            try:
                anweisung = await grok.simple(fp.aufgabe_an_sklaven(kette_liste[0]), max_tokens=250)
            except Exception as e:
                logger.error("Ketten-Start: aufgabe_an_sklaven fehlgeschlagen, sende Roh-Text: %s", e)
                anweisung = kette_liste[0]
            # Befehls-Sticker gelegentlich als Auftakt der Anweisung
            await sticker_reaktionen.sende_sklave(context.bot, sticker_reaktionen.BEFEHL, chance=0.5)
            try:
                await telegram_helper.send_sklave(context.bot, anweisung, voice_text=anweisung)
            except Exception as e:
                # Task steht auf "offen" mit follow_up_datum – der followup_job
                # stellt die Frage nach, der Sklave bleibt im Chat-Mode.
                logger.error("Fehler beim Senden der ersten Ketten-Aufgabe: %s", e)
            else:
                if not state.set_followup_task(sklave_chat, first_task_id):
                    logger.warning("Ketten-Start %s gesendet, aber Followup-State blockiert – "
                                   "Nachfrage kommt über den regulären Followup-Zyklus.", first_task_id)

    else:
        # Auch Glieder 2..n durchs Sicherheits-Gate (Review D8/H1): vorher lief
        # nur die ERSTE Ketten-Aufgabe durch den Limits-Check – nachgetippte
        # Glieder waren der einzige ungeprüfte Weg zum Sklaven. kette_profile
        # enthält nur das Domina-Profil, darum lädt verletzungen() beide
        # Profile selbst (None-Defaults).
        from bot.services import limits_check
        treffer = await limits_check.verletzungen(text)
        if treffer:
            _quellen = sorted({tr["quelle"] for tr in treffer})
            logger.warning(
                "Ketten-Glied verletzt %d Grenze(n) [%s] – nicht in Kette uebernommen.",
                len(treffer), ", ".join(_quellen),
            )
            await telegram_helper.reply_markdown_safe(
                update.message,
                t("DOMINA_AUFGABE_GRENZEN", treffer=limits_check.format_verletzungen(treffer)),
            )
            return
        kette_liste = s.get("kette_aufgaben_liste", [])
        kette_liste.append(text)
        s["kette_aufgaben_liste"] = kette_liste
        nummer = len(kette_liste) + 1
        await update.message.reply_text(
            t("DOMINA_KETTE_AUFGABE_HINZU", nummer=len(kette_liste), naechste=nummer),
            parse_mode="Markdown"
        )


async def _save_conversation(text: str, response: str) -> None:
    # Länge-Gate wie im Sklave-Pfad (Review D8/N10): Kurznachrichten ("ok")
    # blähen conversations nur auf. query_vector-Parameter entfernt – er wurde
    # nie genutzt (save_conversation embeddet selbst).
    if len(text) <= 10:
        return
    themen = _detect_themen(text + " " + response)
    session_id = f"sess_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    # Zusammenfassung NUR fürs Embedding/Such-Preview (Embedder kürzt intern ohnehin).
    # Der vollständige Wortlaut steht ungekürzt in domina_nachricht/coach_antwort.
    zusammenfassung = (
        f"Domina: {text[:2000]}\n"
        f"Coach: {response[:2000]}"
    )

    # Wichtige Punkte: erste 1-2 Sätze der Domina-Nachricht – nicht hart gekappt
    saetze = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    wichtige_punkte = saetze[:2] if saetze else []

    # Best-effort wie im Sklave-Pfad (Hermes-Review H1): die Antwort ist längst
    # gesendet – ein Qdrant-/Embedding-Fehler hier darf dem Nutzer kein
    # irreführendes "Fehler aufgetreten" mehr anzeigen.
    try:
        await qdrant.save_conversation("domina", session_id, {
            "zusammenfassung": zusammenfassung,
            "wichtige_punkte": wichtige_punkte,
            "themen": themen,
            "thema": themen[0] if themen else "allgemein",  # Kompatibilität
            "domina_nachricht": text,      # vollständig speichern (kein Abschneiden)
            "coach_antwort": response,     # vollständig speichern (kein Abschneiden)
        })
    except Exception as e:
        logger.error("Fehler beim Speichern der Domina-Konversation: %s", e)


def _detect_themen(text: str) -> list[str]:
    text_lower = text.lower()
    themen = {
        "aufgabe":     ["aufgabe", "task", "auftrag", "soll er", "er soll"],
        "ritual":      ["ritual", "zeremonie", "routine", "täglich", "morgen"],
        "service":     ["service", "dienen", "putzen", "kochen", "massage"],
        "gefühl":      ["gefühl", "fühle", "emotion", "angst", "freude", "stolz"],
        "grenze":      ["grenze", "limit", "nein", "safeword", "stopp"],
        "fortschritt": ["fortschritt", "level", "entwicklung", "besser", "gelernt"],
        "idee":        ["idee", "vorschlag", "wie wäre", "was denkst", "kannst du"],
        "strafe":      ["strafe", "bestrafung", "konsequenz"],
        "lob":         ["lob", "stolz", "freue", "gut gemacht", "anerkenn"],
    }
    treffer = [t for t, kws in themen.items() if any(kw in text_lower for kw in kws)]
    return treffer if treffer else ["allgemein"]


async def _check_level_up(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    profile: dict,
    current_level: int,
) -> None:
    if current_level >= 5:
        return
    score = await qdrant.get_level_score("sklave")
    gesamt = score["gesamt"]
    for lvl, threshold in sorted(config.LEVEL_THRESHOLDS.items()):
        if lvl > current_level and gesamt >= threshold:
            new_level = lvl
            # Gezielt patchen – `profile` ist hier ein alter Snapshot (im
            # Kette-Pfad sogar minutenalt aus dem State); ein Full-Upsert damit
            # würde zwischenzeitliche Patches (dossier, grenzen, …) überrollen.
            await qdrant.patch_profile_fields("domina", {"aktuelles_level": new_level})
            await qdrant.save_progress("domina", {
                "level": new_level,
                "thema": "level_aufstieg",
                "beschreibung": f"Level {new_level} erreicht (Score: {gesamt})",
                "aufgaben_erledigt": score["task_count"],
                "aufgaben_gesamt": score["task_count"],
            })
            await update.message.reply_text(
                t("DOMINA_LEVEL_UP", level=new_level, vielfalt=score["vielfalt"],
                  streak=score["streak"], bewertung=score["bewertung"]),
                parse_mode="Markdown"
            )

            # Abzeichen prüfen
            neue_abzeichen = await punkte.domina_level_up(new_level)
            for abzeichen in neue_abzeichen:
                await update.message.reply_text(
                    t("DOMINA_NEUES_ABZEICHEN", emoji=abzeichen["emoji"], name=abzeichen["name"],
                      beschreibung=abzeichen["beschreibung"]),
                    parse_mode="Markdown"
                )
            break