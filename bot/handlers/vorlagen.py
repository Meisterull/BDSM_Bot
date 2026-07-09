"""
Vorlagen Handler – Aufgaben-Vorlagen speichern und verwenden.
Nur für Domina. Aufgaben werden erst nach Bestätigung weitergeleitet.
"""
import uuid
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes
from qdrant_client import models as qm

from bot import config, state
from bot.services import paare
from bot.services import qdrant, embeddings as emb
from bot.services import kategorie_logik, telegram_helper
from bot.messages import t


async def _get_vorlagen() -> list[dict]:
    results, _ = await qdrant.run_io(
        qdrant.client.scroll,
        collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="kategorie", match=qm.MatchValue(value="vorlage")),
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key("domina"))),
        ]),
        limit=20,
        with_payload=True,
        with_vectors=False,
    )
    return [r.payload for r in results]


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    vorlagen = await _get_vorlagen()
    s = state.get(chat_id)
    state.set_mode(chat_id, "vorlage_wahl")
    s["vorlagen_liste"] = {str(i): v for i, v in enumerate(vorlagen, 1)}

    lines = [t("VORLAGEN_TITEL")]
    if vorlagen:
        for i, v in enumerate(vorlagen, 1):
            lines.append(f"{i}. {v.get('name', '–')}")
            lines.append(f"   _{v.get('inhalt', '')[:80]}_\n")
    else:
        lines.append(t("VORLAGEN_KEINE"))

    lines.append(t("VORLAGEN_AKTIONEN"))
    if vorlagen:
        lines.append(t("VORLAGEN_AKTIONEN_MIT_LISTE"))
    lines.append(t("VORLAGEN_ABBRECHEN_HINWEIS"))

    await telegram_helper.reply_markdown_safe(update.message, "\n".join(lines))


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)
    text = update.message.text.strip()
    mode = s.get("mode")

    if text.lower() in ("abbrechen", "/abbrechen"):
        state.set_mode(chat_id, "chat")
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return

    if mode == "vorlage_wahl":
        if text.lower() == "neu":
            state.set_mode(chat_id, "vorlage_name")
            await update.message.reply_text(t("VORLAGEN_NAME_FRAGE"))
            return

        # Vorlage löschen
        if text.lower().startswith("l") and text[1:] in s.get("vorlagen_liste", {}):
            v = s["vorlagen_liste"][text[1:]]
            point_id = v.get("qdrant_point_id")
            if point_id:
                await qdrant.run_io(
                    qdrant.client.delete,
                    collection_name="knowledge_base",
                    points_selector=qm.PointIdsList(points=[point_id])
                )
            state.set_mode(chat_id, "chat")
            await update.message.reply_text(t("VORLAGEN_GELOESCHT"))
            return

        # Vorlage wählen → Bestätigung mit korrektem Level
        if text in s.get("vorlagen_liste", {}):
            v = s["vorlagen_liste"][text]
            aufgabe_text = v.get("inhalt", "")

            # Aktuelles Level der Domina laden
            domina_profile = await qdrant.get_user_profile("domina") or {}
            level = domina_profile.get("aktuelles_level", 1)

            s["pending_task_text"] = aufgabe_text
            s["pending_task_level"] = level
            s["pending_task_profile"] = domina_profile
            s["pending_task_kategorie"] = await kategorie_logik.klassifiziere(aufgabe_text)
            state.set_mode(chat_id, "aufgabe_bestaetigung")

            await telegram_helper.reply_markdown_safe(
                update.message,
                t("VORLAGEN_BESTAETIGUNG", aufgabe=aufgabe_text),
            )
            return

        await update.message.reply_text(t("VORLAGEN_UNGUELTIG"))

    elif mode == "vorlage_name":
        s["neue_vorlage_name"] = text
        state.set_mode(chat_id, "vorlage_text")
        await update.message.reply_text(t("VORLAGEN_TEXT_FRAGE"))

    elif mode == "vorlage_text":
        name = s.get("neue_vorlage_name", "Unbekannt")
        inhalt = text

        # Async Embedding statt sync httpx
        vector = await emb.get_embedding(inhalt)
        point_id = str(uuid.uuid4())

        await qdrant.run_io(
            qdrant.client.upsert,
            collection_name="knowledge_base",
            points=[qm.PointStruct(
                id=point_id,
                vector={"text": vector},
                payload={
                    "user_id": qdrant.mandanten_key("domina"),
                    "kategorie": "vorlage",
                    "name": name,
                    "inhalt": inhalt,
                    "erstellt_am": datetime.now(timezone.utc).isoformat(),
                    "qdrant_point_id": point_id,
                },
            )],
        )

        state.set_mode(chat_id, "chat")
        await telegram_helper.reply_markdown_safe(
            update.message,
            t("VORLAGEN_GESPEICHERT", name=name),
        )