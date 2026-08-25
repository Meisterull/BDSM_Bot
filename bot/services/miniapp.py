"""
Telegram-Mini-App-Server: Cockpit + Sprachnachrichten-Studio (LAN-only, HTTPS).

Stdlib-only (Muster logserver.py): ThreadingHTTPServer in einem Daemon-Thread,
TLS über ssl.SSLContext (Zertifikat der lokalen Heimnetz-CA, s. .env.example).
Async-Services (Qdrant, TTS, Telegram-Versand) laufen über
run_coroutine_threadsafe auf dem Bot-Event-Loop.

Auth: Telegrams signierte initData (HMAC-SHA256 mit dem Bot-Token nach
Mini-App-Spez). Die User-ID daraus wird über paare.resolve() zu (Paar, Rolle)
aufgelöst – Fremde bekommen 403, egal ob sie die URL kennen. Fail-closed:
ohne MINIAPP_PORT oder ohne lesbares Zertifikat startet der Server nicht
(Telegram öffnet eh nur https-URLs).

Endpunkte (alle außer / verlangen den Header X-Init-Data):
  GET  /                    -> bot/webapp/index.html
  GET  /api/uebersicht      -> Cockpit-Daten (rollenbewusst)
  POST /api/vorschau        -> {text} -> OGG-Audio (nur Dom-Seite)
  POST /api/senden          -> {text} -> Text+Voice an den Sklaven (nur Dom-Seite)
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

from bot import config

logger = logging.getLogger(__name__)

_LOOP: asyncio.AbstractEventLoop | None = None
_BOT = None
_INDEX_PFAD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "webapp", "index.html")
# Kleine Anfragekörper reichen völlig (Text einer Sprachnachricht).
_MAX_BODY = 64 * 1024


def pruefe_init_data(init_data: str) -> dict | None:
    """Validiert Telegrams initData (Signatur + Frische) und gibt das
    user-Objekt zurück, sonst None. Spez: Datencheck-String = sortierte
    key=value-Zeilen ohne hash, Secret = HMAC('WebAppData', Bot-Token)."""
    if not init_data or len(init_data) > 8192:
        return None
    felder = parse_qsl(init_data, keep_blank_values=True)
    hash_wert = dict(felder).get("hash", "")
    if not hash_wert:
        return None
    daten = "\n".join(f"{k}={v}" for k, v in sorted(felder) if k != "hash")
    secret = hmac.new(b"WebAppData", config.TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
    erwartet = hmac.new(secret, daten.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(erwartet, hash_wert):
        return None
    d = dict(felder)
    try:
        # 24h-Frische: initData wird beim App-Öffnen frisch ausgestellt; ein
        # abgegriffener alter String soll nicht ewig als Ausweis taugen.
        if time.time() - int(d.get("auth_date", "0")) > 86400:
            return None
        return json.loads(d.get("user", "{}")) or None
    except (ValueError, TypeError):
        return None


def _await(coro, timeout: float = 60.0):
    """Coroutine auf dem Bot-Loop ausführen (Aufruf aus dem Server-Thread)."""
    fut = asyncio.run_coroutine_threadsafe(coro, _LOOP)
    return fut.result(timeout)


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
    }


async def _vorschau_ogg(text: str) -> bytes | None:
    from bot.services import tts
    return await tts.synthesize(text, rolle=tts.ROLLE_HERRIN)


async def _sende_an_sklaven(paar_id: str, text: str) -> None:
    from bot.services import paare, telegram_helper, tts
    with paare.kontext(paar_id):
        await telegram_helper.send_sklave(_BOT, tts.entferne_sprech_tags(text),
                                          voice_text=text)


async def _werkstatt_aufgabe(paar_id: str, text: str, kategorie: str) -> dict:
    """Aufgabe aus der Werkstatt erteilen – Muster luecke._erteile_jetzt:
    Limits-Gate, Herrin-Anweisung, Task-Anlage mit Rollback bei Sendefehler.
    KEIN set_followup_task – das Followup kommt über den Scheduler."""
    from bot.prompts import followup as fp
    from bot.services import grok, limits_check, paare, qdrant, telegram_helper
    with paare.kontext(paar_id):
        treffer = await limits_check.verletzungen(text)
        if treffer:
            # treffer = Dicts (limit/matched_via) – fürs UI nur die Limit-Namen
            return {"limit": ", ".join(sorted({v["limit"] for v in treffer}))}
        profil = await qdrant.get_user_profile("domina") or {}
        try:
            anweisung = await grok.simple(fp.aufgabe_an_sklaven(text), max_tokens=250)
        except Exception:
            logger.exception("Werkstatt: aufgabe_an_sklaven fehlgeschlagen – sende Rohtext")
            anweisung = text
        point_id = await qdrant.erstelle_task(
            text, kategorie or "allgemein", profil.get("aktuelles_level", 1),
            quelle="miniapp")
        try:
            await telegram_helper.send_sklave(_BOT, anweisung, voice_text=anweisung)
        except Exception:
            await qdrant.loesche_task(point_id)
            raise
    return {"ok": True}


async def _werkstatt_inspiration(paar_id: str) -> dict:
    from bot.handlers import inspiration as insp
    from bot.services import paare, qdrant
    with paare.kontext(paar_id):
        dp = await qdrant.get_user_profile("domina") or {}
        sp = await qdrant.get_user_profile("sklave") or {}
        _, vorschlaege = await insp._generate_vorschlaege(dp, sp)
    return {"vorschlaege": vorschlaege[:3]}


async def _werkstatt_loeschen(paar_id: str, task_id: str) -> dict:
    """Soft-Delete wie /loeschen (status='geloescht'); nur wirklich offene
    Tasks des Paares – die id muss in der aktuellen Offen-Liste stehen."""
    from bot.services import paare, qdrant
    with paare.kontext(paar_id):
        offene = await qdrant.get_tasks_by_status(["offen"])
        if task_id not in {t.get("qdrant_point_id") for t in offene}:
            return {"fehler": "Aufgabe ist nicht (mehr) offen"}
        await qdrant.update_task(task_id, {"status": "geloescht"})
    return {"ok": True}


async def _werkstatt_serie(paar_id: str, text: str, kategorie: str, tage: int) -> dict:
    """Serie erteilen – Kern von serie_handler._save_serie_tasks: Variationen-
    Bogen (Fallback Wiederholung), Limits-Check je Tag mit Rückfall auf den
    Basis-Text, Tag 1 offen + zugestellt, Rest serie_wartend (Scheduler)."""
    from bot.handlers.serie_handler import _parse_variationen
    from bot.prompts import followup as fp
    from bot.services import grok, limits_check, paare, qdrant, telegram_helper
    with paare.kontext(paar_id):
        treffer = await limits_check.verletzungen(text)
        if treffer:
            return {"limit": ", ".join(sorted({v["limit"] for v in treffer}))}
        profil = await qdrant.get_user_profile("domina") or {}
        level = profil.get("aktuelles_level", 1)
        kategorie = kategorie or "allgemein"

        variationen = None
        try:
            raw = await grok.simple(fp.serie_variationen(text, tage, kategorie), reasoning=True)
            variationen = _parse_variationen(raw, tage)
        except Exception:
            logger.exception("Werkstatt-Serie: Variationen fehlgeschlagen – Wiederholung")
        variationen = variationen or [text] * tage
        for i, v in enumerate(variationen):
            if await limits_check.verletzungen(v):
                variationen[i] = text

        import uuid as _uuid
        serie_id = str(_uuid.uuid4())
        for tag in range(tage):
            await qdrant.erstelle_task(
                variationen[tag], kategorie, level,
                status="offen" if tag == 0 else "serie_wartend",
                followup_in_tagen=tag + 1, quelle="miniapp",
                extra={"serie_id": serie_id, "serie_tag": tag + 1, "serie_gesamt": tage},
            )
        try:
            anweisung = await grok.simple(fp.aufgabe_an_sklaven(variationen[0]), max_tokens=250)
        except Exception:
            anweisung = variationen[0]
        await telegram_helper.send_sklave(_BOT, anweisung, voice_text=anweisung)
    return {"ok": True, "tage": tage}


async def _werkstatt_kette(paar_id: str, glieder: list, kategorie: str) -> dict:
    """Kette erteilen – Muster des /aufgabe-Ketten-Flows (domina.py): ALLE
    Glieder durchs Limits-Gate (D8/H1), Glied 1 offen + zugestellt, Rest
    kette_wartend; Rollback aller Glieder bei Anlage-/Sendefehler."""
    from datetime import datetime, timezone
    import uuid as _uuid
    from bot.prompts import followup as fp
    from bot.services import grok, limits_check, paare, qdrant, telegram_helper
    with paare.kontext(paar_id):
        for i, g in enumerate(glieder, 1):
            treffer = await limits_check.verletzungen(g)
            if treffer:
                return {"limit": ", ".join(sorted({v["limit"] for v in treffer})),
                        "glied": i}
        profil = await qdrant.get_user_profile("domina") or {}
        level = profil.get("aktuelles_level", 1)
        kette_id = str(_uuid.uuid4())
        jetzt = datetime.now(timezone.utc).isoformat()
        gespeichert = []
        try:
            for position, g in enumerate(glieder, 1):
                task_id = await qdrant.erstelle_task(
                    g, kategorie or "allgemein", level,
                    status="offen" if position == 1 else "kette_wartend",
                    quelle="miniapp",
                    # Kette prüft zeitnah nach – wie der Chat-Flow (follow_up=erteilt_am)
                    extra={"kette_id": kette_id, "kette_position": position,
                           "kette_gesamt": len(glieder), "follow_up_datum": jetzt},
                )
                gespeichert.append(task_id)
            try:
                anweisung = await grok.simple(fp.aufgabe_an_sklaven(glieder[0]), max_tokens=250)
            except Exception:
                anweisung = glieder[0]
            await telegram_helper.send_sklave(_BOT, anweisung, voice_text=anweisung)
        except Exception:
            for tid in gespeichert:
                try:
                    await qdrant.loesche_task(tid)
                except Exception:
                    logger.exception("Ketten-Rollback: Glied %s nicht löschbar", tid)
            raise
    return {"ok": True, "glieder": len(glieder)}


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
    if feld not in _PROFIL_LISTEN.get(rolle, ()):
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
                await abw._informiere_partner(_BOT, rolle, t("ABWESEND_PARTNER_AUFGEHOBEN"))
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
            _BOT, rolle,
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
        await telegram_helper.send_domina(_BOT, nachricht, parse_mode="Markdown",
                                          reply_markup=keyboard)
    return {"ok": True}


class _Handler(BaseHTTPRequestHandler):
    # --- Antwort-Helfer -------------------------------------------------------
    def _antwort(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, daten: dict) -> None:
        self._antwort(code, json.dumps(daten, ensure_ascii=False).encode(),
                      "application/json; charset=utf-8")

    def _auth(self) -> tuple | None:
        """X-Init-Data prüfen -> (paar, rolle) oder None (Antwort schon gesendet)."""
        from bot.services import paare
        user = pruefe_init_data(self.headers.get("X-Init-Data", ""))
        if not user or not user.get("id"):
            self._json(401, {"fehler": "ungueltige initData"})
            return None
        aufgeloest = paare.resolve(str(user["id"]))
        if aufgeloest is None:
            self._json(403, {"fehler": "nicht autorisiert"})
            return None
        return aufgeloest

    def _body_json(self) -> dict | None:
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if not 0 < n <= _MAX_BODY:
            self._json(400, {"fehler": "Body fehlt/zu groß"})
            return None
        try:
            return json.loads(self.rfile.read(n))
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"fehler": "kein JSON"})
            return None

    # --- Routen ---------------------------------------------------------------
    def do_GET(self):
        pfad = urlparse(self.path).path
        if pfad == "/":
            try:
                with open(_INDEX_PFAD, "rb") as f:
                    self._antwort(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._antwort(500, b"index.html fehlt", "text/plain")
            return
        if pfad == "/telegram-web-app.js":
            # Selbst gehostet: keine Abhängigkeit von telegram.org im LAN-Betrieb.
            try:
                with open(os.path.join(os.path.dirname(_INDEX_PFAD), "telegram-web-app.js"), "rb") as f:
                    self._antwort(200, f.read(), "application/javascript; charset=utf-8")
            except OSError:
                self._antwort(404, b"script fehlt", "text/plain")
            return
        if pfad == "/favicon.ico":
            self._antwort(204, b"", "image/x-icon")
            return
        if pfad == "/api/uebersicht":
            auth = self._auth()
            if not auth:
                return
            paar, rolle = auth
            try:
                daten = _await(_uebersicht_daten(paar.paar_id, rolle))
            except Exception:
                logger.exception("Mini-App: Übersicht fehlgeschlagen")
                self._json(500, {"fehler": "Daten nicht ladbar"})
                return
            self._json(200, daten)
            return
        if pfad == "/api/trainings":
            from bot.services import paare
            auth = self._auth()
            if not auth:
                return
            paar, rolle = auth
            if rolle != paare.ROLLE_DOM:
                self._json(403, {"fehler": "nur für die Dom-Seite"})
                return
            try:
                self._json(200, _await(_trainings_liste(paar.paar_id)))
            except Exception:
                logger.exception("Mini-App: Trainings-Liste fehlgeschlagen")
                self._json(500, {"fehler": "Liste nicht ladbar"})
            return
        self._antwort(404, b"nicht gefunden", "text/plain")

    def do_POST(self):
        from bot.services import paare
        pfad = urlparse(self.path).path
        if pfad == "/api/log":
            # Diagnose-Beacon der Seite (Inbetriebnahme): bewusst OHNE Auth –
            # loggt nur, führt nichts aus; Body hart gedeckelt.
            try:
                n = min(int(self.headers.get("Content-Length", "0")), 2048)
                logger.info("Mini-App-Diagnose %s: %s", self.client_address[0],
                            self.rfile.read(n).decode("utf-8", "replace"))
            except Exception:
                pass
            self._json(200, {"ok": True})
            return
        # Pfad -> (erlaubte Rolle, braucht 'text'-Feld). Vorschau/Senden/Werkstatt
        # sind Dom-Sache, Wünsche kommen vom Sub.
        routen = {
            "/api/vorschau": (paare.ROLLE_DOM, True),
            "/api/senden": (paare.ROLLE_DOM, True),
            "/api/aufgabe": (paare.ROLLE_DOM, True),
            "/api/inspiration": (paare.ROLLE_DOM, False),
            "/api/aufgabe_loeschen": (paare.ROLLE_DOM, False),
            "/api/serie": (paare.ROLLE_DOM, True),
            "/api/kette": (paare.ROLLE_DOM, False),
            "/api/training": (paare.ROLLE_DOM, False),
            "/api/psycho_uebung": (paare.ROLLE_DOM, False),
            "/api/psycho_antwort": (paare.ROLLE_DOM, False),
            "/api/anruf": (paare.ROLLE_DOM, False),
            "/api/wunsch": (paare.ROLLE_SUB, True),
            # None = beide Rollen (bearbeiten je nur EIGENE Daten, s. Coroutinen)
            "/api/profil_liste": (None, False),
            "/api/abwesenheit": (None, False),
        }
        if pfad not in routen:
            self._antwort(404, b"nicht gefunden", "text/plain")
            return
        auth = self._auth()
        if not auth:
            return
        paar, rolle = auth
        noetige_rolle, braucht_text = routen[pfad]
        if noetige_rolle and rolle != noetige_rolle:
            self._json(403, {"fehler": f"nur für die Rolle '{noetige_rolle}'"})
            return
        if pfad == "/api/anruf":
            # Roh-Audio (WAV) statt JSON – eigener, größerer Body-Deckel.
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = 0
            if not 44 < n <= 8 * 1024 * 1024:
                self._json(400, {"fehler": "Audio fehlt oder ist zu groß (max 8 MB)"})
                return
            try:
                ergebnis = _await(_anruf(paar.paar_id, self.rfile.read(n)), timeout=120)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            except Exception:
                logger.exception("Mini-App: /api/anruf fehlgeschlagen")
                self._json(500, {"fehler": "Anruf fehlgeschlagen"})
            return
        body = self._body_json()
        if body is None:
            return
        text = (body.get("text") or "").strip()
        if braucht_text and not (0 < len(text) <= config.TTS_MAX_ZEICHEN * 2):
            self._json(400, {"fehler": "Text fehlt oder zu lang"})
            return
        try:
            if pfad == "/api/vorschau":
                ogg = _await(_vorschau_ogg(text), timeout=45)
                if not ogg:
                    self._json(502, {"fehler": "TTS nicht verfügbar"})
                    return
                self._antwort(200, ogg, "audio/ogg")
            elif pfad == "/api/senden":
                _await(_sende_an_sklaven(paar.paar_id, text), timeout=45)
                self._json(200, {"ok": True})
            elif pfad == "/api/aufgabe":
                kategorie = (body.get("kategorie") or "").strip()[:60]
                ergebnis = _await(_werkstatt_aufgabe(paar.paar_id, text, kategorie),
                                  timeout=90)
                self._json(409 if ergebnis.get("limit") else 200, ergebnis)
            elif pfad == "/api/inspiration":
                # reasoning-Generierung + Limits-Filter: darf dauern
                self._json(200, _await(_werkstatt_inspiration(paar.paar_id), timeout=180))
            elif pfad == "/api/aufgabe_loeschen":
                task_id = (body.get("task_id") or "").strip()
                if not task_id:
                    self._json(400, {"fehler": "task_id fehlt"})
                    return
                ergebnis = _await(_werkstatt_loeschen(paar.paar_id, task_id), timeout=30)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            elif pfad == "/api/serie":
                try:
                    tage = int(body.get("tage", 0))
                except (TypeError, ValueError):
                    tage = 0
                if tage not in (2, 3, 7, 14):
                    self._json(400, {"fehler": "tage muss 2, 3, 7 oder 14 sein"})
                    return
                kategorie = (body.get("kategorie") or "").strip()[:60]
                ergebnis = _await(_werkstatt_serie(paar.paar_id, text, kategorie, tage),
                                  timeout=180)
                self._json(409 if ergebnis.get("limit") else 200, ergebnis)
            elif pfad == "/api/kette":
                glieder = body.get("glieder")
                if (not isinstance(glieder, list) or not 2 <= len(glieder) <= 8
                        or not all(isinstance(g, str) and 0 < len(g.strip()) <= 500
                                   for g in glieder)):
                    self._json(400, {"fehler": "glieder: 2–8 Texte (je ≤500 Zeichen)"})
                    return
                kategorie = (body.get("kategorie") or "").strip()[:60]
                ergebnis = _await(_werkstatt_kette(
                    paar.paar_id, [g.strip() for g in glieder], kategorie), timeout=120)
                self._json(409 if ergebnis.get("limit") else 200, ergebnis)
            elif pfad == "/api/training":
                kategorie = (body.get("kategorie") or "").strip()[:60]
                if not kategorie:
                    self._json(400, {"fehler": "kategorie fehlt"})
                    return
                ergebnis = _await(_training_erstellen(paar.paar_id, kategorie), timeout=180)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            elif pfad == "/api/psycho_uebung":
                ergebnis = _await(_psycho_uebung(
                    paar.paar_id, str(body.get("typ", "")).strip()), timeout=120)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            elif pfad == "/api/psycho_antwort":
                uebung = str(body.get("uebung", "")).strip()[:4000]
                antwort = str(body.get("antwort", "")).strip()[:4000]
                if not (uebung and antwort):
                    self._json(400, {"fehler": "uebung/antwort fehlt"})
                    return
                ergebnis = _await(_psycho_antwort(
                    paar.paar_id, str(body.get("typ", "")).strip(), uebung, antwort),
                    timeout=120)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            elif pfad == "/api/profil_liste":
                werte = body.get("werte")
                if not isinstance(werte, list):
                    self._json(400, {"fehler": "werte-Liste fehlt"})
                    return
                ergebnis = _await(_profil_liste_setzen(
                    paar.paar_id, rolle, str(body.get("feld", "")), werte), timeout=30)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            elif pfad == "/api/abwesenheit":
                ergebnis = _await(_abwesenheit_setzen(paar.paar_id, rolle, body), timeout=30)
                self._json(409 if ergebnis.get("fehler") else 200, ergebnis)
            else:  # /api/wunsch
                _await(_wunsch_einreichen(paar.paar_id, text), timeout=45)
                self._json(200, {"ok": True})
        except Exception:
            logger.exception("Mini-App: %s fehlgeschlagen", pfad)
            self._json(500, {"fehler": "Aktion fehlgeschlagen"})

    def log_message(self, fmt, *args):
        # Bewusst LAUT (anders als logserver): das Ding wird gerade in Betrieb
        # genommen, und "kommt das Handy überhaupt an?" ist die Kernfrage.
        # UA + initData-Länge unterscheiden Telegram-WebView von Browser-Test.
        ua = (self.headers.get("User-Agent") or "?")[:60]
        init_len = len(self.headers.get("X-Init-Data") or "")
        plattform = self.headers.get("X-Tg-Platform") or "-"
        logger.info("Mini-App-Zugriff %s: %s [tg=%s, initData=%d Z., UA=%s]",
                    self.client_address[0], fmt % args, plattform, init_len, ua)


class _TLSServer(ThreadingHTTPServer):
    def get_request(self):
        # TLS-Handshake läuft im accept(); Fehler (z.B. Client vertraut der CA
        # nicht → "unknown ca"-Alert) wären sonst als OSError STILL verschluckt.
        try:
            return super().get_request()
        except ssl.SSLError as e:
            logger.warning("Mini-App: TLS-Handshake abgelehnt: %s", e)
            raise OSError(str(e))


def start(loop: asyncio.AbstractEventLoop, bot) -> None:
    """Startet den Mini-App-Server im Daemon-Thread (no-op wenn MINIAPP_PORT=0)."""
    global _LOOP, _BOT
    if not config.MINIAPP_PORT:
        return
    if not (os.path.isfile(config.MINIAPP_SSL_CERT) and os.path.isfile(config.MINIAPP_SSL_KEY)):
        logger.error("Mini-App NICHT gestartet: Zertifikat/Key fehlen (%s / %s).",
                     config.MINIAPP_SSL_CERT, config.MINIAPP_SSL_KEY)
        return
    _LOOP, _BOT = loop, bot
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(config.MINIAPP_SSL_CERT, config.MINIAPP_SSL_KEY)
        srv = _TLSServer(("0.0.0.0", config.MINIAPP_PORT), _Handler)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True, name="miniapp").start()
        logger.info("Mini-App-Server läuft auf Port %d (HTTPS, initData-Auth).",
                    config.MINIAPP_PORT)
    except Exception:
        logger.exception("Mini-App-Server konnte nicht gestartet werden")
