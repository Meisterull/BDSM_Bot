"""
Adaptive Aufgaben-Kette.

Wenn der Sklave die zuletzt erledigte Ketten-Aufgabe negativ erlebt hat
(langweilig / überfordert / abgelehnt), generiert der Coach eine angepasste
Alternative zur NÄCHSTEN geplanten Ketten-Aufgabe und schickt sie der Domina
mit ✅/🗑-Buttons. Erst nach ihrer Freigabe geht eine Aufgabe an den Sklaven.

Passt zum Bot-Prinzip „nichts wird still angewendet" (immer Domina-Bestätigung).
Die nächste Aufgabe bleibt bis zur Entscheidung auf Status `kette_wartend`.
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, limits_check
from bot.prompts import followup as fp
from bot.messages import t

logger = logging.getLogger(__name__)


def _buttons(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_ANPASSUNG_SENDEN"), callback_data=f"ketteanpass:approve:{task_id}"),
        InlineKeyboardButton(t("BUTTON_ORIGINAL_SENDEN"), callback_data=f"ketteanpass:keep:{task_id}"),
    ]])


async def schlage_vor(bot, naechster_task: dict, vorheriges_gefuehl: str, stimmung: str) -> bool:
    """Generiert eine angepasste Alternative und schickt sie der Domina zur Freigabe.

    Gibt True zurück, wenn ein Vorschlag rausging (die nächste Aufgabe bleibt dann
    `kette_wartend` bis zur Entscheidung). Gibt False zurück, wenn der normale
    Freischalt-Pfad genutzt werden soll (z.B. Grok-Fehler oder Limits-Verletzung).
    """
    try:
        naechster_id = naechster_task.get("qdrant_point_id")
        original = naechster_task.get("aufgabe", "")
        if not naechster_id or not original:
            return False

        sklave_profil = await qdrant.get_user_profile("sklave") or {}
        domina_profil = await qdrant.get_user_profile("domina") or {}
        hard_limits = sklave_profil.get("hard_limits", []) or []
        domina_grenzen = domina_profil.get("grenzen", []) or []

        prompt = fp.kette_anpassung(original, vorheriges_gefuehl, stimmung)
        # Limits-Check (beide Profile): bei Treffer einmal verschärft neu, sonst Abbruch.
        adapted = await limits_check.generate_mit_limit_retry(prompt, hard_limits, domina_grenzen)
        if adapted is None:
            logger.warning("Adaptive Kette: Anpassung auch nach Re-Gen grenzverletzend – normaler Pfad.")
            return False
        adapted = grok.clean_text(adapted)
        if not adapted or len(adapted) < 5:
            return False

        # Vorschlag am Task festhalten (überlebt Neustart), Status bleibt kette_wartend.
        await qdrant.update_task(naechster_id, {"kette_anpass_vorschlag": adapted})

        pos = naechster_task.get("kette_position", "?")
        gesamt = naechster_task.get("kette_gesamt", "?")
        await telegram_helper.send_domina(
            bot,
            t(
                "KETTE_ANPASSUNG_VORSCHLAG", pos=pos, gesamt=gesamt,
                stimmung=stimmung, adapted=adapted, original=original,
            ),
            parse_mode="Markdown",
            reply_markup=_buttons(naechster_id),
        )
        logger.info("Adaptive Kette: Anpassungs-Vorschlag an Domina gesendet (Task %s, %s).",
                    naechster_id, stimmung)
        return True
    except Exception:
        logger.exception("Adaptive Kette: Vorschlag fehlgeschlagen")
        return False


def _fehlschlag_buttons(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_KETTE_WEITER"), callback_data=f"kettefail:weiter:{task_id}"),
        InlineKeyboardButton(t("BUTTON_KETTE_ABBRECHEN"), callback_data=f"kettefail:abbruch:{task_id}"),
    ]])


async def frage_bei_fehlschlag(bot, fehl_task: dict) -> bool:
    """Fragt die Domina nach einem GESCHEITERTEN Ketten-Glied, ob die Kette
    weiterlaufen oder abgebrochen werden soll (Test-Befund F12: vorher blieben
    die Folge-Glieder für immer `kette_wartend` – es gab nur den Erfolgs-Pfad).

    Gibt True zurück, wenn eine Entscheidungs-Frage rausging."""
    kette_id = fehl_task.get("kette_id")
    if not kette_id:
        return False
    try:
        naechster = await qdrant.get_naechster_ketten_task(
            kette_id, fehl_task.get("kette_position", 0))
        if not naechster:
            return False  # kein wartendes Glied mehr -> nichts zu entscheiden
        await telegram_helper.send_domina(
            bot,
            t(
                "KETTE_FEHLSCHLAG_FRAGE",
                pos=fehl_task.get("kette_position", "?"),
                gesamt=fehl_task.get("kette_gesamt", "?"),
                naechste=naechster.get("aufgabe", "")[:200],
            ),
            parse_mode="Markdown",
            reply_markup=_fehlschlag_buttons(naechster.get("qdrant_point_id")),
        )
        logger.info("Kette %s: Weiter/Abbruch-Frage nach Fehlschlag an Domina gesendet.", kette_id)
        return True
    except Exception:
        logger.exception("Kette-Fehlschlag-Frage fehlgeschlagen")
        return False


async def callback_fehlschlag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """▶️ Weiterführen / 🛑 Abbrechen nach gescheitertem Ketten-Glied."""
    query = update.callback_query
    await query.answer()
    try:
        _, action, task_id = query.data.split(":", 2)
    except ValueError:
        return
    await query.edit_message_reply_markup(reply_markup=None)

    task = await qdrant.get_task(task_id)
    if not task:
        await query.message.reply_text(t("KETTE_NICHT_VORHANDEN"))
        return
    # Doppel-Tap-Guard: nur entscheiden, solange das Glied wirklich noch wartet.
    if task.get("status") != "kette_wartend":
        await query.message.reply_text(t("KETTE_BEREITS_ENTSCHIEDEN"))
        return

    pos = task.get("kette_position", "?")
    gesamt = task.get("kette_gesamt", "?")

    if action == "weiter":
        aufgabe_text = task.get("aufgabe", "")
        await qdrant.update_task(task_id, {"status": "offen"})
        state.set_followup_task(paare.sub_chat_id(), task_id)
        try:
            anweisung = await grok.simple(fp.aufgabe_an_sklaven(aufgabe_text), max_tokens=250)
        except Exception as e:
            logger.error("Kette-Fehlschlag: aufgabe_an_sklaven fehlgeschlagen, sende Roh-Text: %s", e)
            anweisung = aufgabe_text
        await telegram_helper.send_sklave(
            context.bot,
            t("KETTE_FREIGESCHALTET", pos=pos, gesamt=gesamt, anweisung=anweisung),
            voice_text=anweisung,
        )
        await query.message.reply_text(t("KETTE_WEITER_BESTAETIGT", pos=pos, gesamt=gesamt))
        return

    # abbruch: ALLE noch wartenden Glieder der Kette verwerfen
    wartende = await qdrant.get_kette_wartende(task.get("kette_id"))
    for glied in wartende:
        gid = glied.get("qdrant_point_id")
        if gid:
            await qdrant.update_task(gid, {"status": "kette_abgebrochen"})
    await query.message.reply_text(t("KETTE_ABGEBROCHEN_DOMINA", anzahl=len(wartende)))
    logger.info("Kette %s: abgebrochen, %d Glieder verworfen.", task.get("kette_id"), len(wartende))


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """✅ Anpassung übernehmen / 🗑 Original behalten → nächste Aufgabe freischalten + senden."""
    query = update.callback_query
    await query.answer()
    try:
        _, action, task_id = query.data.split(":", 2)
    except ValueError:
        return
    await query.edit_message_reply_markup(reply_markup=None)

    task = await qdrant.get_task(task_id)
    if not task:
        await query.message.reply_text(t("KETTE_NICHT_VORHANDEN"))
        return
    # Doppel-Tap-Guard (Review D8/M6, wie callback_fehlschlag): ein verspäteter
    # zweiter Tap darf ein bereits entschiedenes/erledigtes Glied nicht wieder
    # auf "offen" setzen und erneut senden (sonst doppelte Punkte möglich).
    if task.get("status") != "kette_wartend":
        await query.message.reply_text(t("KETTE_BEREITS_ENTSCHIEDEN"))
        return

    if action == "approve":
        neuer_text = task.get("kette_anpass_vorschlag") or task.get("aufgabe", "")
    else:  # keep
        neuer_text = task.get("aufgabe", "")

    await qdrant.update_task(task_id, {
        "aufgabe": neuer_text,
        "status": "offen",
        "kette_anpass_vorschlag": None,
    })
    state.set_followup_task(paare.sub_chat_id(), task_id)

    pos = task.get("kette_position", "?")
    gesamt = task.get("kette_gesamt", "?")
    try:
        anweisung = await grok.simple(fp.aufgabe_an_sklaven(neuer_text), max_tokens=250)
    except Exception as e:
        logger.error("Kette-Adaptiv: aufgabe_an_sklaven fehlgeschlagen, sende Roh-Text: %s", e)
        anweisung = neuer_text
    await telegram_helper.send_sklave(
        context.bot,
        t("KETTE_FREIGESCHALTET", pos=pos, gesamt=gesamt, anweisung=anweisung),
        voice_text=anweisung,
    )

    label = "Angepasste" if action == "approve" else "Originale"
    await query.message.reply_text(t("KETTE_GESENDET", label=label))
