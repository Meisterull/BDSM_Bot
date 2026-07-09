"""
Betreiber-Kommandos für die Paar-Verwaltung (Multiuser).

NUR für config.ADMIN_CHAT_ID (Env; leer = Kommandos komplett aus). Der
Admin-Chat muss kein Paar-Mitglied sein.

/paare                          – alle Paare + offene Invites auflisten
/paar_loeschen <id>             – Sicherheitsabfrage anzeigen
/paar_loeschen <id> LOESCHEN    – Paar entfernen + ALLE seine Qdrant-Daten
                                  löschen (unwiderruflich; Backups rotieren
                                  erst nach BACKUP_KEEP Tagen heraus)
"""
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare, persona_config, qdrant
from bot.messages import t

logger = logging.getLogger(__name__)


def _ist_admin(update: Update) -> bool:
    return bool(config.ADMIN_CHAT_ID) and (
        str(update.effective_chat.id) == str(config.ADMIN_CHAT_ID))


async def paare_liste(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _ist_admin(update) or not update.message:
        return
    zeilen = [t("ADMIN_PAARE_KOPF")]
    for paar in paare.alle_paare():
        env_marker = " (Env)" if paar.paar_id == paare.LEGACY_PAAR_ID else ""
        zeilen.append(f"• Paar {paar.paar_id}{env_marker}: dom={paar.dom_chat_id}, sub={paar.sub_chat_id}")
    invites = paare._lade_registry()["invites"]
    if invites:
        zeilen.append("")
        zeilen.append(t("ADMIN_INVITES_KOPF", anzahl=len(invites)))
        for code, inv in invites.items():
            alter_h = (datetime.now().timestamp() - float(inv.get("erstellt_am", 0))) / 3600
            zeilen.append(f"• {code}: chat={inv.get('chat_id')}, rolle={inv.get('rolle')}, {alter_h:.0f}h alt")
    await update.message.reply_text("\n".join(zeilen))


async def paar_loeschen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _ist_admin(update) or not update.message:
        return
    args = context.args or []
    if not args:
        await update.message.reply_text(t("ADMIN_PAAR_LOESCHEN_USAGE"))
        return
    paar_id = str(args[0])
    if paar_id == paare.LEGACY_PAAR_ID:
        await update.message.reply_text(t("ADMIN_PAAR_ENV"))
        return
    paar = paare.get_paar(paar_id)
    if paar is None:
        await update.message.reply_text(t("ADMIN_PAAR_UNBEKANNT", paar_id=paar_id))
        return

    if len(args) < 2 or args[1] != "LOESCHEN":
        await update.message.reply_text(
            t("ADMIN_PAAR_LOESCHEN_BESTAETIGUNG", paar_id=paar_id,
              dom=paar.dom_chat_id, sub=paar.sub_chat_id))
        return

    # 1) Zeit-Jobs stoppen, 2) Registry-Eintrag raus (resolve schlägt ab jetzt
    # fehl -> keine neuen Nachrichten/Jobs), 3) Qdrant-Daten löschen,
    # 4) In-Memory-Reste (State/History, Pause-Flag, persona-Cache) aufräumen,
    # 5) beide Chats neutral informieren (best-effort).
    try:
        from bot import main as main_mod  # lazy: main importiert diesen Handler
        main_mod.entferne_zeit_jobs(paar_id)
    except Exception:
        logger.exception("Paar-Löschung: Zeit-Jobs für %s nicht entfernbar", paar_id)

    paare.entferne_paar(paar_id)
    bericht = await qdrant.loesche_paar_daten(paar_id)

    state.set_paused(False, paar_id=paar_id)
    state.vergiss_chat(paar.dom_chat_id)
    state.vergiss_chat(paar.sub_chat_id)
    persona_config.vergiss_paar(paar_id)

    for chat in (paar.dom_chat_id, paar.sub_chat_id):
        try:
            await context.bot.send_message(chat_id=chat, text=t("ADMIN_ZUGANG_BEENDET"))
        except Exception:
            logger.info("Paar-Löschung: Chat %s nicht benachrichtigbar", chat)

    geloescht = sum(n for n in bericht.values() if n > 0)
    fehler = [col for col, n in bericht.items() if n < 0]
    text = t("ADMIN_PAAR_GELOESCHT", paar_id=paar_id, punkte=geloescht)
    if fehler:
        text += "\n" + t("ADMIN_PAAR_LOESCHEN_FEHLER", collections=", ".join(fehler))
    await update.message.reply_text(text)
