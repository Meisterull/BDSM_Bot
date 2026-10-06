"""
Tiny-Task Feedback Handler.

Wenn die Domina einen Tiny-Task-Vorschlag nicht weitergeleitet hat, fragt der Bot
abends nach dem Grund. Antwort wird in der knowledge_base am Vorschlag gespeichert
und fließt in zukünftige Vorschläge als 'aus Fehlern lernen'-Kontext ein.

EIN-TIPP-ABLAUF (06.10.2026): Die Frage belegt den Chat NICHT mehr. Vorher
setzte sie den Mode 'tiny_task_feedback' und wartete stundenlang auf einen
getippten Grund – blieb sie unbeantwortet, galt der Domina-Chat bis zum
Stale-Reset als belegt (Coach-Impuls fiel jeden Abend aus), und jede andere
Nachricht in der Zeit lief Gefahr, als Ablehnungsgrund verbucht zu werden.
Jetzt: Knöpfe „Übernommen / Gut, nicht heute / Passte nicht"; „Passte nicht"
klappt drei feste Gründe auf. Der Mode entsteht nur noch, wenn sie
ausdrücklich „Eigenen Grund schreiben" antippt.
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, telegram_helper, grok, kategorie_logik
from bot.messages import t

logger = logging.getLogger(__name__)

# Referenzen auf Fire-and-forget-Tasks halten, sonst GC sie evtl. vor Abschluss.
_BG_TASKS: set = set()

# Kein Modul-Level t() (Review D8/N8): t() ist bewusst per-Paar-dynamisch.

# Feste Ablehnungsgründe: Aktion → (Text-Key, Kategorie-Signal verbuchen?).
# Nur „Thema" spricht gegen die KATEGORIEN; Aufwand und Intensität sind Kritik
# am konkreten Vorschlag und sollen die Kategorie-Gewichtung nicht verzerren.
_GRUENDE = {
    "g_thema":   ("BUTTON_TINYFB_G_THEMA", True),
    "g_aufwand": ("BUTTON_TINYFB_G_AUFWAND", False),
    "g_lahm":    ("BUTTON_TINYFB_G_LAHM", False),
}


def _tasten_frage(point_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t("BUTTON_UEBERNOMMEN"), callback_data=f"tinyfb:uebernommen:{point_id}")],
        [InlineKeyboardButton(t("BUTTON_GUT_NICHT_HEUTE"), callback_data=f"tinyfb:gut:{point_id}")],
        [InlineKeyboardButton(t("BUTTON_TINYFB_NEIN"), callback_data=f"tinyfb:nein:{point_id}")],
    ])


def _tasten_gruende(point_id: str) -> InlineKeyboardMarkup:
    zeilen = [[InlineKeyboardButton(t(key), callback_data=f"tinyfb:{aktion}:{point_id}")]
              for aktion, (key, _) in _GRUENDE.items()]
    zeilen.append([InlineKeyboardButton(t("BUTTON_TINYFB_TEXT"), callback_data=f"tinyfb:text:{point_id}")])
    zeilen.append([InlineKeyboardButton(t("BUTTON_TINYFB_ZURUECK"), callback_data=f"tinyfb:zurueck:{point_id}")])
    return InlineKeyboardMarkup(zeilen)


def _flow_beenden(point_id: str) -> None:
    """Freitext-Mode nur zurücksetzen, wenn er noch UNSERER ist (und zu DIESEM
    Vorschlag gehört) – ein später Tap auf einen alten Knopf darf keinen gerade
    aktiven anderen Flow killen."""
    chat = paare.dom_chat_id()
    s = state.get(chat)
    if s.get("tiny_task_feedback_id") not in (None, point_id):
        return
    if state.get_mode(chat) == "tiny_task_feedback":
        state.set_mode(chat, "chat")
    s.pop("tiny_task_feedback_id", None)


async def _positives_feedback(point_id: str, action: str) -> str:
    """Speichert 'uebernommen' bzw. 'gut' am Tiny-Task und liefert den Antwort-Text.
    Gemeinsame Logik fuer Button- und Text-Pfad. Verbucht zusätzlich das getrennte
    Domina-Präferenz-Signal für die Kategorien des Vorschlags."""
    tiny_task = await qdrant.get_tiny_task_by_id(point_id) or {}
    kategorien = tiny_task.get("kategorien", [])
    if action == "uebernommen":
        await qdrant.mark_tiny_task_status(point_id, "uebernommen")
        await kategorie_logik.record_domina_praeferenz(kategorien, "genutzt")
        return t("TINYFB_ANTWORT_UEBERNOMMEN")
    await qdrant.mark_tiny_task_status(point_id, "gut_aber_ungenutzt", grund="Vorschlag war gut, heute aber nicht umgesetzt")
    await kategorie_logik.record_domina_praeferenz(kategorien, "gut")
    return t("TINYFB_ANTWORT_GUT")


async def _vorschlag_aus_ablehnung(bot, tiny_task_inhalt: str, kategorien: list, grund: str) -> None:
    """Bei einer Ablehnung mit Begruendung: Grok formuliert daraus eine Regel-Suggestion.
    Wird als 'pending' gespeichert und mit Ja/Nein-Buttons an die Domina geschickt."""
    from bot.handlers import coach_regeln as _cr
    try:
        system = """Du analysierst eine Ablehnung eines BDSM-Aufgaben-Vorschlags.

Leite daraus eine VERALLGEMEINERTE Regel oder Notiz ab, an die sich der Coach
zukuenftig bei Vorschlaegen halten soll. Maximal EIN Satz, konkret, in du-Form.

Wenn aus der Begruendung keine allgemeine Regel ableitbar ist (z.B. nur Tagesform),
antworte NUR mit: KEINE_REGEL

Sonst nur die Regel als reinen Text, ohne Anfuehrungszeichen, ohne Erklaerung."""
        from bot.prompts import followup as fp
        prompt = (
            f"Vorschlag (Kategorien: {', '.join(kategorien) if kategorien else '?'}):\n"
            f"{tiny_task_inhalt}\n\n"
            f"{fp.nutzer_text('Grund der Ablehnung durch die Domina', grund)}"
        )

        antwort = grok.clean_text(await grok.simple(prompt, system=system, temperature=0))  # Regel-Ableitung: deterministisch
        if not antwort or antwort.upper().startswith("KEINE_REGEL"):
            logger.info("Tiny-Task-Ablehnung: keine generalisierbare Regel ableitbar.")
            return

        # Als 'notiz' speichern (lockerer Hinweis), Status pending bis Domina bestaetigt
        point_id = await qdrant.save_coach_regel(
            user_id="domina",
            text=antwort,
            typ="notiz",
            status="pending",
            quelle="abgeleitet_ablehnung",
            kontext=f"Abgelehnter Vorschlag: {tiny_task_inhalt[:200]} | Grund: {grund[:200]}",
        )
        await _cr.sende_vorschlag(
            bot, point_id, antwort,
            kontext=f"abgelehnter Vorschlag mit Begruendung „{grund[:80]}“",
        )
    except Exception as e:
        logger.error("Fehler beim Erzeugen einer Regel aus Ablehnung: %s", e)


async def _ist_ablehnungsgrund(tiny_task_inhalt: str, text: str) -> bool:
    """Klassifiziert den Freitext der Domina im Feedback-Modus: echter
    Ablehnungsgrund zum Vorschlag ODER ein anderes Anliegen (Frage/Auftrag/
    neues Thema)? Vorher wurde JEDER Freitext als Ablehnungsgrund gespeichert –
    live vergiftet durch „Kannst du ihm den Wochenplan senden?" (Review D7, B2).
    Fail-safe: bei LLM-Fehlern wie bisher als Ablehnungsgrund behandeln.
    Vor dem LLM ein deterministischer Detektor: ein Auftrag an den Bot („schreib
    ihm …", „Aufgabe: …") ist nie ein Ablehnungsgrund – das LLM wertete ihn
    als solchen, sobald er thematisch zum Vorschlag passte (live 28.09.2026)."""
    from bot.prompts import followup as fp
    from bot.handlers import domina  # lazy: zirkulären Import vermeiden
    if domina.ist_auftrag_an_bot(text):
        return False
    system = (
        "Der Bot hat die Domina gefragt, warum sie einen Aufgaben-Vorschlag nicht übernommen hat.\n"
        "Klassifiziere ihre Antwort:\n"
        "- ABLEHNUNG: eine Begründung oder Kritik zum Vorschlag (auch knapp: "
        "'zu langweilig', 'keine Zeit gehabt', 'passt gerade nicht')\n"
        "- ANDERES: ein anderes Anliegen – eine Frage, ein Auftrag an den Bot "
        "(dem Sub etwas ausrichten, ihm eine eigene Aufgabe geben), Smalltalk oder "
        "ein neues Thema. Eine EIGENE Aufgabe oder Anweisung für den Sub ist immer "
        "ANDERES, auch wenn sie thematisch zum Vorschlag passt.\n"
        "Antworte NUR mit ABLEHNUNG oder ANDERES."
    )
    prompt = (
        f"Vorschlag, um den es geht:\n{tiny_task_inhalt[:400]}\n\n"
        f"{fp.nutzer_text('Antwort der Domina', text)}"
    )
    try:
        antwort = grok.clean_text(await grok.simple(prompt, system=system, temperature=0))
        if (antwort or "").upper().startswith("ANDERES"):
            return False
    except Exception:
        logger.exception("Feedback-Klassifikation fehlgeschlagen – behandle als Ablehnungsgrund")
    return True


async def frage_stellen(bot, tiny_task_payload: dict) -> None:
    """Wird vom Scheduler aufgerufen – fragt die Domina nach dem Grund."""
    point_id = tiny_task_payload.get("qdrant_point_id")
    # LLM-Text landet im Template INNERHALB von _…_ → Marker vorher neutralisieren,
    # sonst bricht fast jeder Vorschlag das Markdown der ganzen Rückfrage.
    inhalt = telegram_helper.md_einbett_sicher(tiny_task_payload.get("inhalt", ""))
    kategorien = tiny_task_payload.get("kategorien", [tiny_task_payload.get("kategorie", "?")])

    # Kategorie-Slugs tragen Underscores (Ruiniertes_Orgasmen) und stehen im
    # Template in _…_ – ohne Ersetzung kippt die Marker-Parität fast täglich.
    kategorien_anzeige = ", ".join(kategorien).replace("_", " ")

    await telegram_helper.send_domina(
        bot,
        t("TINYFB_FRAGE", kategorien=kategorien_anzeige, inhalt=inhalt),
        parse_mode="Markdown",
        reply_markup=_tasten_frage(point_id),
    )
    # BEWUSST kein set_mode: Die Frage wartet in ihren Knöpfen, der Chat bleibt
    # frei (s. Modulkopf). Den Freitext-Mode setzt erst der Knopf „Eigenen Grund
    # schreiben" in callback_button.


async def manuelle_frage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tinyfb – verschickt die Rückfrage zum neuesten offenen Tiny-Task-Vorschlag
    SOFORT über den laufenden Bot (wird also korrekt geloggt). Wie die abendliche
    Frage: nur Knöpfe, kein Mode. Nur Domina."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return
    pending = await qdrant.get_pending_tiny_tasks_for_feedback(hours_back=72)
    if not pending:
        await update.message.reply_text(t("TINYFB_KEIN_OFFENER"))
        return
    await frage_stellen(context.bot, pending[0])


async def callback_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-Knöpfe der Rückfrage: Übernommen / Gut / Passte nicht → fester
    Grund oder (nur auf Wunsch) Freitext."""
    query = update.callback_query
    await query.answer()
    _, action, point_id = query.data.split(":", 2)

    # Doppel-Tap-/Stale-Guard (D9/N3): nur einen noch VORGESCHLAGENEN Eintrag
    # entscheiden – ein zweiter/verspäteter Tap verbuchte das Kategorie-Signal
    # sonst doppelt (verzerrt die 60/30/10-Gewichtung) bzw. überschrieb einen
    # späteren Status.
    eintrag = await qdrant.get_tiny_task_by_id(point_id)
    if not eintrag or eintrag.get("status", "vorgeschlagen") != "vorgeschlagen":
        await query.edit_message_reply_markup(reply_markup=None)
        return

    # Zwischenschritte: nur die Knopfreihe tauschen, nichts verbuchen.
    if action == "nein":
        await query.edit_message_reply_markup(reply_markup=_tasten_gruende(point_id))
        return
    if action == "zurueck":
        _flow_beenden(point_id)
        await query.edit_message_reply_markup(reply_markup=_tasten_frage(point_id))
        return
    if action == "text":
        # Der EINZIGE Weg in den Freitext-Mode: sie will ausdrücklich schreiben.
        # Die Knöpfe bleiben stehen – überlegt sie es sich anders, reicht ein Tipp.
        chat = paare.dom_chat_id()
        if state.get_mode(chat) not in ("chat", None, "tiny_task_feedback"):
            await query.message.reply_text(t("TINYFB_GERADE_BELEGT"))
            return
        state.get(chat)["tiny_task_feedback_id"] = point_id
        state.set_mode(chat, "tiny_task_feedback")
        await query.message.reply_text(t("TINYFB_GRUND_SCHREIBEN"))
        return

    if action in ("uebernommen", "gut"):
        antwort = await _positives_feedback(point_id, action)
    elif action in _GRUENDE:
        key, kategorie_signal = _GRUENDE[action]
        grund = t(key)
        await qdrant.mark_tiny_task_status(point_id, "abgelehnt", grund=grund)
        if kategorie_signal:
            await kategorie_logik.record_domina_praeferenz(eintrag.get("kategorien", []), "abgelehnt")
        # Bewusst KEINE Regel-Ableitung (_vorschlag_aus_ablehnung) aus einem
        # festen Grund: ein Standardsatz trägt keine verallgemeinerbare Regel,
        # und jede Ableitung wäre eine weitere Nachricht mit Rückfrage.
        antwort = t("TINYFB_NOTIERT", grund=telegram_helper.md_einbett_sicher(grund))
    else:
        return

    await query.edit_message_reply_markup(reply_markup=None)
    _flow_beenden(point_id)
    await query.message.reply_text(antwort, parse_mode="Markdown")
    logger.info("Tiny-Task-Feedback (Button) gespeichert (point_id=%s, action=%s)", point_id, action)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet einen getippten Grund – erreichbar nur noch nach dem Knopf
    „Eigenen Grund schreiben" (der setzt den Mode)."""
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()
    s = state.get(chat_id)
    point_id = s.get("tiny_task_feedback_id")

    if not point_id:
        state.set_mode(chat_id, "chat")
        return

    text_lower = text.lower()

    # Getipptes "abbrechen" nicht als Ablehnungsgrund persistieren (würde sonst
    # eine Regel-Ableitung triggern).
    if text_lower in ("abbrechen", "/abbrechen"):
        state.set_mode(chat_id, "chat")
        s.pop("tiny_task_feedback_id", None)
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return

    if text_lower in ("übernommen", "uebernommen", "ja", "genutzt"):
        antwort = await _positives_feedback(point_id, "uebernommen")
    elif text_lower in ("gut", "ok", "passt"):
        antwort = await _positives_feedback(point_id, "gut")
    else:
        tiny_task = await qdrant.get_tiny_task_by_id(point_id) or {}
        tt_inhalt = tiny_task.get("inhalt", "")
        tt_kategorien = tiny_task.get("kategorien", [])
        # Kein Ablehnungsgrund, sondern anderes Anliegen → als normale Chat-
        # Nachricht an den Domina-Handler; die Feedback-Frage bleibt offen
        # (Mode + Buttons unangetastet), sie kann später noch antworten.
        if not await _ist_ablehnungsgrund(tt_inhalt, text):
            logger.info(
                "Tiny-Task-Feedback: Freitext als anderes Anliegen erkannt → Domina-Chat (point_id=%s)",
                point_id,
            )
            from bot.handlers import domina
            await domina.handle(update, context)
            return
        await qdrant.mark_tiny_task_status(point_id, "abgelehnt", grund=text)
        # grund steht im Template ebenfalls in _…_ → Marker neutralisieren
        antwort = t("TINYFB_NOTIERT", grund=telegram_helper.md_einbett_sicher(text[:80]))
        # Getrenntes Domina-Präferenz-Signal: sie mochte diese Kategorien-Richtung nicht.
        await kategorie_logik.record_domina_praeferenz(tt_kategorien, "abgelehnt")
        # Ebene 2: implizit lernen – Grok schlaegt eine generalisierte Regel vor.
        if tt_inhalt:
            import asyncio
            _bg = asyncio.create_task(_vorschlag_aus_ablehnung(context.bot, tt_inhalt, tt_kategorien, text))
            _BG_TASKS.add(_bg)
            _bg.add_done_callback(_BG_TASKS.discard)

    state.set_mode(chat_id, "chat")
    s.pop("tiny_task_feedback_id", None)

    await telegram_helper.reply_markdown_safe(update.message, antwort)
    logger.info("Tiny-Task-Feedback gespeichert (point_id=%s)", point_id)
