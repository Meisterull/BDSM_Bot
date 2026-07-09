"""
Coach-Regeln Handler – Lern-System fuer den Bot.

Ebene 1 (manuell):
  /regel <text>        – verbindliche Regel fuer den Coach (NIE ignorieren)
  /merken <text>       – lockere Notiz/Vorliebe
  /regeln              – aktive Regeln + offene Vorschlaege anzeigen
  /vergessen <nr>      – Regel deaktivieren (Nummer aus /regeln)

Inline-Callback fuer Ebene 2/3 Vorschlaege:
  coachregel:ja:<id>   – pending -> aktiv
  coachregel:nein:<id> – pending -> verworfen
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config
from bot.services import paare
from bot.services import qdrant, telegram_helper
from bot.messages import t

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def merken(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/merken <text> – speichert eine lockere Notiz/Vorliebe (Typ: notiz)."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.message.reply_text(t("COACHREGELN_MERKEN_USAGE"), parse_mode="Markdown")
        return

    point_id = await qdrant.save_coach_regel(
        user_id="domina",
        text=text,
        typ="notiz",
        status="aktiv",
        quelle="manuell",
    )
    logger.info("Coach-Notiz gespeichert: %s (%s)", text[:60], point_id)
    await update.message.reply_text(
        t("COACHREGELN_NOTIZ_GESPEICHERT", text=telegram_helper.escape_md(text)),
        parse_mode="MarkdownV2",
    )


async def regel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/regel <text> – speichert eine verbindliche Regel (Typ: regel)."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.message.reply_text(t("COACHREGELN_REGEL_USAGE"), parse_mode="Markdown")
        return

    point_id = await qdrant.save_coach_regel(
        user_id="domina",
        text=text,
        typ="regel",
        status="aktiv",
        quelle="manuell",
    )
    logger.info("Coach-Regel gespeichert: %s (%s)", text[:60], point_id)
    await update.message.reply_text(
        t("COACHREGELN_REGEL_AKTIV", text=telegram_helper.escape_md(text)),
        parse_mode="MarkdownV2",
    )


async def regeln(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/regeln – zeigt alle aktiven Regeln/Notizen + offene Vorschlaege."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    aktive = await qdrant.get_active_coach_regeln("domina")
    pending = await qdrant.get_pending_coach_regeln("domina")

    if not aktive and not pending:
        await update.message.reply_text(t("COACHREGELN_KEINE"))
        return

    # Aktive Liste
    s = context.chat_data.setdefault("regeln_index", {})
    s.clear()
    teile = []

    if aktive:
        zeilen = [t("COACHREGELN_LISTE_TITEL")]
        for i, r in enumerate(aktive, 1):
            s[i] = r.get("qdrant_point_id")
            symbol = "⚡" if r.get("typ") == "regel" else "📝"
            quelle = r.get("quelle", "manuell")
            # Kein Italic: Quellen wie "abgeleitet_bewertung" enthalten Unterstriche,
            # die das Legacy-Markdown-Parsing brechen würden.
            quelle_label = "" if quelle == "manuell" else f" ({quelle})"
            zeilen.append(f"{i}. {symbol} {r.get('text','')}{quelle_label}")
        zeilen.append(t("COACHREGELN_LISTE_FUSS"))
        teile.append("\n".join(zeilen))

    if pending:
        zeilen = [t("COACHREGELN_PENDING_TITEL")]
        for r in pending:
            symbol = "⚡" if r.get("typ") == "regel" else "📝"
            zeilen.append(f"{symbol} {r.get('text','')}")
        zeilen.append(t("COACHREGELN_PENDING_FUSS"))
        teile.append("\n".join(zeilen))

    # Fallback-Familie: Regel-Texte sind roher Nutzer-/LLM-Text – ein unbalanciertes
    # */_ darf /regeln (und damit /vergessen) nicht komplett brechen.
    await telegram_helper.reply_markdown_safe(update.message, "\n\n".join(teile))


async def vergessen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/vergessen <nr> – deaktiviert eine Regel anhand der Nummer aus /regeln."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    if not context.args:
        await update.message.reply_text(t("COACHREGELN_VERGESSEN_USAGE"), parse_mode="Markdown")
        return

    try:
        nr = int(context.args[0])
    except ValueError:
        await update.message.reply_text(t("COACHREGELN_KEINE_NUMMER"))
        return

    index = context.chat_data.get("regeln_index", {})
    point_id = index.get(nr)
    if not point_id:
        await update.message.reply_text(t("COACHREGELN_NUMMER_UNBEKANNT"))
        return

    await qdrant.set_coach_regel_status(point_id, "verworfen")
    index.pop(nr, None)
    await update.message.reply_text(t("COACHREGELN_DEAKTIVIERT", nr=nr))


# ---------------------------------------------------------------------------
# Inline-Callback fuer Vorschlaege aus Ebene 2/3
# ---------------------------------------------------------------------------

async def callback_bestaetigen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    parts = data.split(":", 2)
    if len(parts) != 3:
        return
    _, entscheidung, point_id = parts

    if entscheidung == "ja":
        eintrag = await qdrant.get_coach_regel(point_id) or {}
        await qdrant.set_coach_regel_status(point_id, "aktiv")

        nachricht_suffix = t("COACHREGELN_UEBERNOMMEN")

        # Wenn das ein Profil-Update ist, wenden wir den Patch jetzt an.
        if eintrag.get("typ") == "profil_update":
            profile_user = eintrag.get("profile_user", "")
            patch = eintrag.get("profile_patch") or {}
            try:
                bericht = await qdrant.apply_profile_patch(profile_user, patch)
                if bericht["angewandt"]:
                    aend = "\n".join(bericht["angewandt"])
                    nachricht_suffix = t(
                        "COACHREGELN_PROFIL_AKTUALISIERT",
                        profile_user=profile_user, aenderungen=aend,
                    )
                else:
                    nachricht_suffix = t("COACHREGELN_PATCH_LEER")
                if bericht["ignoriert"]:
                    nachricht_suffix += t("COACHREGELN_PATCH_IGNORIERT", liste="; ".join(bericht["ignoriert"]))
            except Exception as e:
                logger.error("Fehler beim Anwenden des Profil-Patches: %s", e)
                nachricht_suffix = t("COACHREGELN_PATCH_FEHLER", fehler=e)

        neuer_text = (query.message.text or "") + nachricht_suffix
        try:
            await query.edit_message_text(neuer_text, parse_mode="Markdown")
        except Exception as e:
            # query.message.text ist Plain-Text – enthält es * oder _, scheitert Markdown.
            if "parse" in str(e).lower():
                await query.edit_message_text(telegram_helper.strip_md(neuer_text))
            else:
                logger.warning("edit_message_text fehlgeschlagen: %s", e)
    elif entscheidung == "nein":
        await qdrant.set_coach_regel_status(point_id, "verworfen")
        await query.edit_message_text(
            (query.message.text or "") + t("COACHREGELN_VERWORFEN")
        )


def vorschlag_buttons(point_id: str) -> InlineKeyboardMarkup:
    """Helper fuer Ebene 2/3 – fertige Ja/Nein-Buttons."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_MERKEN"), callback_data=f"coachregel:ja:{point_id}"),
        InlineKeyboardButton(t("BUTTON_VERWERFEN"), callback_data=f"coachregel:nein:{point_id}"),
    ]])


async def sende_vorschlag(bot, point_id: str, text: str, kontext: str = "") -> None:
    """Schickt einen Lern-Vorschlag mit Buttons an die Domina."""
    nachricht = t("COACHREGELN_VORSCHLAG", text=text)
    if kontext:
        nachricht += t("COACHREGELN_VORSCHLAG_ANLASS", kontext=kontext)
    nachricht += t("COACHREGELN_VORSCHLAG_FRAGE")
    try:
        await telegram_helper.send_domina(
            bot, nachricht, parse_mode="Markdown",
            reply_markup=vorschlag_buttons(point_id),
        )
    except Exception as e:
        logger.error("Fehler beim Senden des Lern-Vorschlags: %s", e)


def _format_patch(patch: dict) -> str:
    """Patch in lesbare Zeilen umwandeln."""
    zeilen = []
    for ch in (patch or {}).get("changes", []):
        if not isinstance(ch, dict):
            continue
        feld = ch.get("feld", "?")
        op = ch.get("operation", "?")
        wert = ch.get("wert", "")
        if op == "list_add":
            werte = ", ".join(wert) if isinstance(wert, list) else str(wert)
            zeilen.append(f"+ {feld}: {werte}")
        elif op == "limit_add":
            werte = ", ".join(wert) if isinstance(wert, list) else str(wert)
            zeilen.append(f"🚫 {feld}: {werte}")
        elif op == "limit_refine":
            for paar in (wert if isinstance(wert, list) else [wert]):
                if isinstance(paar, dict):
                    zeilen.append(f"✏️ {feld}: {paar.get('alt', '?')} → {paar.get('neu', '?')}")
        elif op == "text_replace":
            zeilen.append(f"~ {feld}: {wert}")
        else:
            zeilen.append(f"? {feld}/{op}: {wert}")
    return "\n".join(zeilen) if zeilen else "(leer)"


async def profil_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/profil_check – Auto-Profil-Pflege manuell ausloesen.

    Sammelt Signale der letzten 14 Tage (optionales Argument /profil_check 30
    nimmt 30 Tage) und schickt Aenderungs-Vorschlaege mit ✅/🗑."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    days = 14
    if context.args:
        try:
            days = max(3, min(90, int(context.args[0])))
        except ValueError:
            pass

    await update.message.reply_text(t("COACHREGELN_PROFILCHECK_WARTE", days=days))

    from bot.scheduler.followup import generiere_profil_vorschlaege
    result = await generiere_profil_vorschlaege(context.bot, days=days)
    if result["status"] == "ok":
        await update.message.reply_text(
            t("COACHREGELN_PROFILCHECK_OK", anzahl=result["vorschlaege"], zeitraum=result["zeitraum"])
        )
    elif result["status"] == "leer":
        await update.message.reply_text(
            t("COACHREGELN_PROFILCHECK_LEER", info=result.get("info", ""))
        )
    else:
        await update.message.reply_text(
            t("COACHREGELN_PROFILCHECK_FEHLER", info=result.get("info", "unbekannt"))
        )


async def sende_profil_vorschlag(
    bot, point_id: str, profile_user: str, patch: dict, kontext: str = "",
) -> None:
    """Schickt einen Profil-Update-Vorschlag mit ✅/🗑 an die Domina."""
    diff = _format_patch(patch)
    rolle = "Domina" if profile_user == "domina" else "Sklave"
    nachricht = t("COACHREGELN_PROFIL_VORSCHLAG", rolle=rolle, diff=diff)
    if kontext:
        nachricht += t("COACHREGELN_VORSCHLAG_ANLASS", kontext=kontext)
    nachricht += t("COACHREGELN_PROFIL_VORSCHLAG_FUSS")
    try:
        await telegram_helper.send_domina(
            bot, nachricht, parse_mode="Markdown",
            reply_markup=vorschlag_buttons(point_id),
        )
    except Exception as e:
        logger.error("Fehler beim Senden des Profil-Vorschlags: %s", e)
