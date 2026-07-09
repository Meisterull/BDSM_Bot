import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import ContextTypes
from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, punkte, kategorie_logik
from bot.prompts import followup as fp
from bot.prompts import sklave as sp
from bot.messages import t

logger = logging.getLogger(__name__)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()
    s = state.get(chat_id)
    task_id = s.get("followup_task_id")
    if not task_id:
        state.set_mode(chat_id, "chat")
        return
    task = await qdrant.get_task(task_id)
    if not task:
        state.set_mode(chat_id, "chat")
        return
    aufgabe = task.get("aufgabe", "")

    # Gefühl und Status speichern
    await qdrant.update_task(task_id, {"status": "erledigt", "gefuehl": text})

    # State zurücksetzen — Reihenfolge wichtig: erst task_id löschen, dann Mode wechseln
    # (sonst kann followup_job einen neuen task_id setzen der hier gelöscht wird)
    s["followup_task_id"] = None
    state.set_mode(chat_id, "chat")

    domina_profile, level, erledigte = await _speichere_fortschritt(aufgabe, text)

    # --- Sklaven-Pfad: erst inhaltliche Reaktion auf das Gefühl, dann Mechanik ---

    # Persönliche Reaktion der Herrin auf die Gefühl-Antwort
    try:
        reaktion = await grok.simple(fp.reaktion_auf_gefuehl(aufgabe, text), max_tokens=250)
    except Exception as e:
        logger.error("Fehler bei Reaktion-auf-Gefühl: %s", e)
        reaktion = t("FALLBACK_GEFUEHL_REAKTION")
    await update.message.reply_text(reaktion)

    await _punkte_und_achievements(context, task, text)

    # Persönlichkeitsprofil des Sklaven aktualisieren – VOR der Ketten-Freischaltung,
    # damit die klassifizierte Stimmung für die adaptive Kette verfügbar ist.
    stimmung_result = await _update_sklave_persoenlichkeit(aufgabe, text, task)
    aktuelle_stimmung = stimmung_result.get("stimmung") if stimmung_result else None

    # Klassifizierte Stimmung am Task festhalten – für Langeweile-Erkennung & Auswertungen.
    if aktuelle_stimmung:
        try:
            await qdrant.update_task(task_id, {"stimmung_klassifikation": aktuelle_stimmung})
        except Exception as e:
            logger.error("Fehler beim Speichern der Stimmungs-Klassifikation: %s", e)

    await _kette_naechster_schritt(context, task, text, aktuelle_stimmung)

    # --- Domina-Pfad: Bericht und Feedback ---

    if aktuelle_stimmung in ("langweilig", "überfordert", "abgelehnt"):
        await _streak_penalty_bei_negativ()

    await _sende_bericht_an_domina(context, task_id, aufgabe, text)

    # Coach-Feedback + Lernpfad an Domina
    await _send_coach_feedback(context, aufgabe, text, level, erledigte)

    # Level-Up Teaser wenn nah am nächsten Level
    await _check_level_teaser(context, profile=domina_profile, level=level)

    # Domina zur Bewertung auffordern – Daten immer setzen, aber einen aktiven
    # Domina-Flow nicht kapern: Bewertungs-Mode nur setzen, wenn sie frei ist.
    domina_chat = paare.dom_chat_id()
    domina_state = state.get(domina_chat)
    domina_state["bewertung_task_id"] = task_id
    if state.get_mode(domina_chat) in ("chat", None):
        state.set_mode(domina_chat, "aufgabe_bewertung")
    await telegram_helper.send_domina(context.bot, t("GEFUEHL_BEWERTUNG_FRAGE"))


async def _speichere_fortschritt(aufgabe: str, gefuehl: str) -> tuple[dict, int, int]:
    """Fortschritt in der progress-Collection festhalten.
    Gibt (domina_profile, level, erledigte) für die weiteren Schritte zurück."""
    domina_profile = await qdrant.get_user_profile("domina") or {}
    level = domina_profile.get("aktuelles_level", 1)
    erledigte = await qdrant.get_completed_task_count("sklave")
    await qdrant.save_progress("domina", {
        "level": level,
        "thema": "aufgabe_erledigt",
        "beschreibung": f"Aufgabe erledigt: {aufgabe}",
        "aufgaben_erledigt": erledigte,
        "aufgaben_gesamt": erledigte,
        "gefuehl_sklave": gefuehl,   # vollständig (zusätzlich zur Kopie auf dem Task)
    })
    return domina_profile, level, erledigte


async def _punkte_und_achievements(context, task: dict, gefuehl_text: str = "") -> None:
    """Punkte & Streak gutschreiben, danach ggf. Würfel-Achievement melden."""
    try:
        ergebnis = await punkte.task_erledigt(task, gefuehl_text=gefuehl_text)
        await _send_punkte_feedback(context, ergebnis)
    except Exception as e:
        logger.error("Fehler bei Punkte-Update: %s", e)

    # Würfel-Achievement falls Task aus dem Würfel kam
    if task.get("quelle") == "wuerfel":
        try:
            wuerfel_neu = await punkte.wuerfel_erledigt()
            if wuerfel_neu:
                await telegram_helper.send_sklave(
                    context.bot,
                    t("GEFUEHL_WUERFEL_ABZEICHEN", liste=", ".join(
                        f"{a['emoji']} {a['name']} – {a['beschreibung']}" for a in wuerfel_neu
                    )),
                )
        except Exception as e:
            logger.error("Fehler bei Würfel-Achievement: %s", e)


async def _kette_naechster_schritt(context, task: dict, gefuehl: str, aktuelle_stimmung: str | None) -> None:
    """Nächsten Ketten-Task freischalten falls vorhanden.
    Adaptive Kette: war das Gefühl negativ, schlägt der Coach der Domina eine
    angepasste nächste Aufgabe zur Freigabe vor (statt sie direkt zu senden)."""
    kette_id = task.get("kette_id")
    if not kette_id:
        return
    try:
        naechster = await qdrant.get_naechster_ketten_task(kette_id, task.get("kette_position", 0))
        if not naechster:
            return
        naechster_id = naechster.get("qdrant_point_id")
        from bot.handlers import kette_adaptiv
        vorschlag_gesendet = False
        if aktuelle_stimmung in ("langweilig", "überfordert", "abgelehnt"):
            vorschlag_gesendet = await kette_adaptiv.schlage_vor(
                context.bot, naechster, gefuehl, aktuelle_stimmung
            )
        if not vorschlag_gesendet:
            await qdrant.update_task(naechster_id, {"status": "offen"})
            state.set_followup_task(paare.sub_chat_id(), naechster_id)
            pos = naechster.get("kette_position", "?")
            gesamt = naechster.get("kette_gesamt", "?")
            naechste_aufgabe = naechster.get("aufgabe", "")
            try:
                anweisung = await grok.simple(fp.aufgabe_an_sklaven(naechste_aufgabe), max_tokens=250)
            except Exception as e:
                logger.error("Kette: aufgabe_an_sklaven fehlgeschlagen, sende Roh-Text: %s", e)
                anweisung = naechste_aufgabe
            await telegram_helper.send_sklave(
                context.bot,
                t("KETTE_FREIGESCHALTET", pos=pos, gesamt=gesamt, anweisung=anweisung),
                voice_text=anweisung,
            )
    except Exception as e:
        logger.error("Fehler beim Freischalten des nächsten Ketten-Tasks: %s", e)


async def _streak_penalty_bei_negativ() -> None:
    """Streak-Penalty bei negativer/langweiliger Stimmung: das heutige
    Streak-Increment EINMALIG rückgängig machen.

    Das Increment passiert nur einmal/Tag (punkte.task_erledigt: gleicher Tag =
    kein Increment). Ohne den `letzter_streak_penalty_tag`-Guard würde jeder
    weitere negativ bewertete Task desselben Tages erneut 1 abziehen und den
    Streak unter den Vortagswert drücken (Über-Bestrafung)."""
    try:
        heute = datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d")
        sklave_prof = await qdrant.get_user_profile("sklave") or {}
        if (sklave_prof.get("letzter_streak_tag") == heute
                and sklave_prof.get("letzter_streak_penalty_tag") != heute):
            # Increment rückgängig + Tag merken, damit es nur einmal/Tag greift.
            await qdrant.patch_profile_fields("sklave", {
                "streak": max(0, sklave_prof.get("streak", 0) - 1),
                "letzter_streak_penalty_tag": heute,
            })
    except Exception as e:
        logger.error("Fehler beim Streak-Penalty: %s", e)


async def _sende_bericht_an_domina(context, task_id: str, aufgabe: str, gefuehl: str) -> None:
    """Bericht über die erledigte Aufgabe an die Domina – mit den letzten
    Gefühlen als Vergleichskontext."""
    erledigt_tasks = await qdrant.get_tasks_by_status(["erledigt"])
    erledigt_mit_gefuehl = sorted(
        [t for t in erledigt_tasks if t.get("gefuehl") and t.get("qdrant_point_id") != task_id],
        key=lambda x: x.get("erteilt_am", ""), reverse=True,
    )
    vorherige_gefuehle = [t.get("gefuehl", "") for t in erledigt_mit_gefuehl[:3] if t.get("gefuehl")]

    prompt = fp.bericht_erledigt(aufgabe, gefuehl, vorherige_gefuehle=vorherige_gefuehle)
    try:
        bericht = await grok.simple(prompt)
    except Exception as e:
        logger.error("Bericht-erledigt fehlgeschlagen, sende Roh-Meldung: %s", e)
        # Kein wörtliches Zitat des Gefühls an die Domina (schützt die Intimität –
        # der LLM-Bericht oben fasst bewusst nur zusammen). Zitiert wird die
        # AUFGABE, nicht seine Äußerung – der Satzbau muss das klar machen.
        bericht = f"✅ Er hat die Aufgabe „{aufgabe}“ erledigt und sich dazu geäußert."
    await telegram_helper.send_domina(context.bot, bericht, parse_mode="Markdown")


async def _update_sklave_persoenlichkeit(aufgabe: str, gefuehl: str, task: dict) -> dict | None:
    """Analysiert Gefühl-Antwort und akkumuliert Persönlichkeitsmuster.
    Gibt das Analyse-Ergebnis zurück (stimmung, intensitaet, kategorie_reaktion) oder None bei Fehler."""
    import json
    kategorie = task.get("kategorie", "allgemein")
    sklave_profil = await qdrant.get_user_profile("sklave") or {}

    analyse_system = """Analysiere kurz die Reaktion eines Sklaven auf eine Aufgabe.

WICHTIG: Im D/s-Kontext können negativ klingende Gefühle wie "erniedrigt", "demütig", "schwach" POSITIV gemeint sein
– der Sklave GENIEßT diese Gefühle. Bewerte nur eindeutige Verweigerung oder Unbehagen als negativ.

Unterscheide 5 Stimmungs-Typen:
- "begeistert": positiv, herausfordernd, will mehr davon
- "positiv": positiv aber ruhig, okay damit
- "langweilig": nicht genug Herausforderung, Routine
- "überfordert": zu schwer, unangenehm, möchte weniger
- "abgelehnt": negative Kategorie, will gar nicht

Antworte NUR mit einem JSON-Objekt ohne Markdown:
{
  "stimmung": "begeistert|positiv|langweilig|überfordert|abgelehnt",
  "intensitaet": "hoch|mittel|niedrig",
  "kategorie_reaktion": "mag_sehr|neutral|mag_nicht"
}

Die JSON-Werte sind feste Daten-IDs – gib sie EXAKT so zurück (deutsch),
unabhängig davon, in welcher Sprache die Reaktion formuliert ist."""
    analyse_prompt = (
        f"Aufgabe-Kategorie: {kategorie}\n"
        f"Aufgabe: {aufgabe}\n"
        f"{fp.nutzer_text('Reaktion/Gefühl', gefuehl)}"
    )

    try:
        raw = await grok.simple(analyse_prompt, system=analyse_system, temperature=0)  # Klassifikation: deterministisch
        analyse = grok.parse_json(raw)
        stimmung = analyse.get("stimmung", "")

        reaktionen = sklave_profil.get("kategorie_reaktionen", {})
        if kategorie not in reaktionen:
            reaktionen[kategorie] = {"positiv": 0, "neutral": 0, "negativ": 0}
        # 5-Stimmungs-Mapping auf kategorie_reaktionen buckets
        _STIMMUNG_MAP = {
            "begeistert": "positiv",
            "positiv": "positiv", 
            "langweilig": "negativ",
            "überfordert": "negativ",
            "abgelehnt": "negativ",
        }
        bucket = _STIMMUNG_MAP.get(stimmung, "neutral")
        reaktionen[kategorie][bucket] = reaktionen[kategorie].get(bucket, 0) + 1
        # Zeitstempel für staleness-basiertes Decay: aktiv gepflegte Kategorien
        # sollen NICHT wöchentlich wegaltern (sonst überlebt nur die häufigste).
        from datetime import datetime, timezone
        reaktionen[kategorie]["letztes_signal"] = datetime.now(timezone.utc).isoformat()

        # Feingranular: langweilig/überfordert auch separat zählen
        if stimmung in ("langweilig", "überfordert", "begeistert"):
            detail_key = f"{stimmung}_count"
            reaktionen[kategorie][detail_key] = reaktionen[kategorie].get(detail_key, 0) + 1

        tags = list(sklave_profil.get("persoenlichkeit_tags", []))
        # Ratio-basiert statt fester Schwellenwert – verhindert Tag-Flip bei 3:2 vs 2:3
        total = reaktionen[kategorie].get("positiv", 0) + reaktionen[kategorie].get("neutral", 0) + reaktionen[kategorie].get("negativ", 0)
        if total >= 3:
            pos_ratio = reaktionen[kategorie].get("positiv", 0) / total
            neg_ratio = reaktionen[kategorie].get("negativ", 0) / total
            if pos_ratio > 0.6 and f"mag_{kategorie}" not in tags:
                tags.append(f"mag_{kategorie}")
            if neg_ratio > 0.5 and f"mag_nicht_{kategorie}" not in tags:
                tags.append(f"mag_nicht_{kategorie}")
            # Entfernen wenn Ratio sich umkehrt
            if neg_ratio > 0.4 and f"mag_{kategorie}" in tags:
                tags.remove(f"mag_{kategorie}")
            if pos_ratio > 0.4 and f"mag_nicht_{kategorie}" in tags:
                tags.remove(f"mag_nicht_{kategorie}")

        # Progressive Steigerung: Intensitäts-Level dieser Kategorie fortschreiben
        levels = dict(sklave_profil.get("kategorie_level", {}) or {})
        aktuell = int(levels.get(kategorie, kategorie_logik.LEVEL_DEFAULT))
        levels[kategorie] = kategorie_logik.naechstes_level(aktuell, stimmung)

        await qdrant.patch_profile_fields("sklave", {
            "kategorie_reaktionen": reaktionen,
            "persoenlichkeit_tags": tags,
            "kategorie_level": levels,
        })
    except Exception as e:
        logger.error("Fehler bei Persönlichkeits-Update: %s", e)
        return None
    return analyse


async def _send_punkte_feedback(context, ergebnis: dict) -> None:
    """Informiert Sklave über Punkte und neue Abzeichen – Domina über neue Abzeichen."""
    gewonnene_punkte = ergebnis["gewonnene_punkte"]
    streak = ergebnis["streak"]
    punkte_gesamt = ergebnis["punkte"]
    neue_abzeichen = ergebnis["neue_abzeichen"]

    # Sklave informieren – mit Bonus-Breakdown wenn mehr als nur Basis
    boni = ergebnis.get("boni", [])
    msg = t("GEFUEHL_PUNKTE", punkte=gewonnene_punkte, gesamt=punkte_gesamt)
    if len(boni) > 1:
        breakdown = "\n".join(f"  • {name}: +{p}" for name, p in boni)
        msg += f"\n{breakdown}"
    if streak > 1:
        msg += t("GEFUEHL_STREAK_SUFFIX", streak=streak)
    await telegram_helper.send_sklave(context.bot, msg, parse_mode="Markdown")

    # Neue Abzeichen – Domina fragen ob sie es dem Sklaven mitteilen möchte
    for abzeichen in neue_abzeichen:
        try:
            prompt = sp.abzeichen_vorschlag(abzeichen["name"], abzeichen["emoji"])
            vorschlag = await grok.simple(prompt)
            await telegram_helper.send_domina(
                context.bot, t("GEFUEHL_ABZEICHEN_VERDIENT", vorschlag=vorschlag),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error("Fehler bei Abzeichen-Nachricht: %s", e)


async def _check_level_teaser(context, profile: dict, level: int) -> None:
    """Sendet Teaser wenn Domina im letzten Viertel vor dem nächsten Level-Schwellenwert ist."""
    from bot import config
    naechstes_level = level + 1
    schwelle = config.LEVEL_THRESHOLDS.get(naechstes_level)
    if not schwelle:
        return
    score = await qdrant.get_level_score("sklave")
    gesamt = score.get("gesamt", 0)
    fehlend = schwelle - gesamt
    viertel = schwelle // 4
    if 0 < fehlend <= viertel:
        try:
            await telegram_helper.send_domina(
                context.bot,
                t("GEFUEHL_LEVEL_TEASER", fehlend=fehlend,
                  plural="e" if fehlend != 1 else "", level=naechstes_level),
            )
        except Exception as e:
            logger.error("Fehler beim Level-Teaser: %s", e)


async def _send_coach_feedback(
    context,
    aufgabe: str,
    gefuehl: str,
    level: int,
    erledigte: int,
) -> None:
    """Sendet Coach-Feedback und Lernpfad-Hinweis an die Domina."""
    from bot.prompts import coach_persona
    system = f"""Eine Aufgabe wurde gerade erledigt. Du sprichst jetzt mit der Domina darüber – wie eine vertraute Freundin, nicht wie ein Lehrer.

{coach_persona.fuer_coach_prompt()}

Inhaltlich soll deine Antwort drei Sachen leisten – aber NICHT als nummerierte Liste oder mit Überschriften wie "Feedback:". Lass es fließen wie ein normaler Chat-Beitrag:
- Eine Beobachtung zur Aufgabe und seiner Reaktion (nicht mehr als 2 Sätze)
- Ein konkreter nächster Schritt oder Gedanke für Level {level} (1-2 Sätze)
- Wenn seine Reaktion klar positiv war: ein kurzer persönlicher Satz dazu was das über ihre Wirkung sagt – ohne Pathos, ohne "Du kannst stolz auf dich sein"

Kein [AUFGABE: ...] Tag."""
    prompt = (
        f"Aufgabe: {aufgabe}\n"
        f"{fp.nutzer_text('Gefühl des Sklaven', gefuehl)}\n"
        f"Aktuelles Level der Domina: {level}\n"
        f"Bisher erledigte Aufgaben gesamt: {erledigte}"
    )
    try:
        feedback = await grok.simple(prompt, system=system, reasoning=True)
        await telegram_helper.send_domina(
            context.bot,
            feedback,
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("Fehler beim Senden des Coach-Feedbacks")