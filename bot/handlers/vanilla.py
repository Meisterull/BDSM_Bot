"""
Vanilla-Modus: freier Chat beider Partner mit der neutralen dritten Stimme
(Etappe 2, 05.11.2026). Gegenstück zu handlers/sklave.py + handlers/domina.py
für den gleichberechtigten Modus – ein Handler, beide Rollen.

Was hier bewusst NICHT läuft (anders als im Sklaven-Chat): Aufgaben-Erkennung,
[AUFGABE:]-Tags, Domina-Relay (die Stimme verspricht nichts auszurichten –
Regel DISKRETION in regeln_vanilla.md), Wunsch-Erfassung ins Sub-Dossier.
Der Präferenz-Detektor (Vorlieben/No-Gos aus dem Chat, ✅/🗑-Vorschlag) läuft
für beide Seiten – er ist schon rollenparametrisiert.
"""
import asyncio
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from bot import state
from bot.services import qdrant, grok, embeddings as emb, telegram_helper, lokal_llm, paare, limits_check
from bot.prompts import vanilla as vp
from bot.handlers import onboarding
from bot.handlers.sklave import (_ist_echo, _ist_spiegel_anfang, _wiederholte_phrase,
                                 _themen, _wichtige_punkte, _BG_TASKS)
from bot.messages import t

logger = logging.getLogger(__name__)


async def letzte_impulse(rolle: str, limit: int = 6) -> list[str]:
    """Die letzten an diese Person gesendeten Impulse (scheduler/vanilla.py)."""
    try:
        from bot.scheduler import vanilla as sv
        return await sv.letzte_impulse(rolle, limit=limit)
    except Exception as e:
        logger.warning("Letzte Impulse nicht ladbar: %s", e)
        return []


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE, rolle: str) -> None:
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()

    # Onboarding (vanilla-Wortwahl kommt aus den Locale-Varianten, s. onboarding.py)
    if await onboarding.start_if_needed(update, context, rolle):
        await onboarding.handle(update, context, rolle)
        return
    if state.get_mode(chat_id) == "onboarding":
        return

    andere = vp.andere_rolle(rolle)

    async def _erinnerung() -> str:
        if len(text) <= 10:
            return ""
        try:
            vektor = await emb.get_embedding(text)
            entries = await qdrant.get_hybrid_conversation_context(rolle, vektor, limit=6,
                                                                    felder=qdrant.KONTEXT_FELDER)
            lines = []
            for e in entries[:6]:
                datum = (e.get("datum") or "")[:10]
                z = e.get("zusammenfassung", "")[:300]
                if z:
                    lines.append(f"[{datum}] {z}")
            if lines:
                return ("\n\nFrühere Gespräche mit dieser Person (als Kontext, nicht direkt zitieren):\n"
                        + "\n".join(lines))
        except Exception as e:
            logger.error("Erinnerungs-Kontext (Vanilla, %s) nicht ladbar: %s", rolle, e)
        return ""

    profil, partner_profil, erinnerung, stimmung_entry, impulse = await asyncio.gather(
        qdrant.get_user_profile(rolle),
        qdrant.get_user_profile(andere),
        _erinnerung(),
        qdrant.get_latest_stimmung(rolle, max_stunden=48),
        letzte_impulse(rolle, limit=3),
    )
    profil = profil or {}
    partner_profil = partner_profil or {}
    stimmung = stimmung_entry.get("zusammenfassung", "") if stimmung_entry else ""

    system = vp.chat(rolle, profil, partner_profil, stimmung=stimmung,
                     letzte_impulse=impulse, erinnerung=erinnerung)
    from bot.services import persona_config
    system += persona_config.abwesenheit_hinweis()

    state.add_message(chat_id, "user", text)
    history = state.get_history(chat_id)
    try:
        async with telegram_helper.typing_action(context.bot, chat_id):
            response = await grok.chat(system, history, temperature=0.9)
    except Exception as e:
        logger.error("Grok-Ausfall im Vanilla-Handler (%s): %s", rolle, e)
        response = ""
        if lokal_llm.aktiv():
            try:
                async with telegram_helper.typing_action(context.bot, chat_id):
                    response = (await lokal_llm.chat_kurz(
                        vp.kurz(rolle, profil, partner_profil),
                        lokal_llm.kuerze_history(history),
                    )).strip()
            except Exception as e2:
                logger.error("Lokales Fallback-Modell ebenfalls fehlgeschlagen: %s", e2)
        if not response:
            state.remove_last_message(chat_id, "user")
            await update.message.reply_text(t("FALLBACK_VANILLA_CHAT"))
            return
        state.add_message(chat_id, "assistant", response)
        for teil in telegram_helper.nachricht_teilen(response):
            await update.message.reply_text(teil)
        return

    # Anti-Echo (gleiche Detektoren wie im Sklaven-Chat)
    letzte_antworten = [m["content"] for m in history if m.get("role") == "assistant"][-6:]
    if (_ist_echo(response, letzte_antworten)
            or _ist_spiegel_anfang(response, text, letzte_antworten)
            or _wiederholte_phrase(response, letzte_antworten)):
        logger.info("Vanilla-Antwort war Echo/Spiegel-Anfang – generiere einmal neu.")
        try:
            async with telegram_helper.typing_action(context.bot, chat_id):
                response = await grok.chat(
                    system + "\n\nACHTUNG: Deine letzte Antwort hat gespiegelt oder sich wiederholt. "
                    "Formulier jetzt völlig neu – beginne NICHT mit den Worten der Person und NICHT wie "
                    "deine letzten Antworten. Eigene Beobachtung, neue Idee, echte Frage oder ein anderes Thema.",
                    history, temperature=1.1, max_tokens=750,
                )
        except Exception as e:
            logger.error("Anti-Echo-Neugenerierung (Vanilla) fehlgeschlagen: %s", e)

    # Limits-Post-Check: No-Gos BEIDER Personen
    try:
        treffer = await limits_check.verletzungen(
            response, vp.no_gos(profil, rolle), vp.no_gos(partner_profil, andere))
        tl = text.lower()
        neu = [tr for tr in treffer
               if tr.get("matched_via", "").lower() not in tl and tr.get("limit", "").lower() not in tl]
        if neu:
            verboten = limits_check.begriffe_zum_verbieten(neu)
            logger.warning("Vanilla-Antwort führt %d No-Go-Begriff(e) ein – generiere einmal neu.", len(neu))
            async with telegram_helper.typing_action(context.bot, chat_id):
                response = await grok.chat(
                    system + "\n\nWICHTIG: Deine letzte Antwort enthielt VERBOTENE BEGRIFFE: "
                    + ", ".join(verboten) + ". Diese Begriffe, ihre Synonyme und alles thematisch "
                    "Verwandte sind ABSOLUT TABU (No-Gos). Antworte neu, ohne diese Themen aufzugreifen.",
                    history, max_tokens=750,
                )
    except Exception as e:
        logger.error("Limits-Post-Check (Vanilla) fehlgeschlagen – Antwort geht unverändert raus: %s", e)

    state.add_message(chat_id, "assistant", response)
    for teil in telegram_helper.nachricht_teilen(response):
        await update.message.reply_text(teil)
    if context.chat_data.get("voice_eingang"):
        await telegram_helper.voice_an(context.bot, chat_id, response, empfaenger_rolle=rolle)

    async def _detektor_bg() -> None:
        try:
            from bot.services import praeferenz_detektor
            await praeferenz_detektor.erkenne_und_schlage_vor(context.bot, rolle, text)
        except Exception as e:
            logger.error("Präferenz-Detektor (Vanilla, %s) fehlgeschlagen: %s", rolle, e)

    bg = asyncio.create_task(_detektor_bg())
    _BG_TASKS.add(bg)
    bg.add_done_callback(_BG_TASKS.discard)

    if len(text) > 10:
        try:
            session_id = f"{rolle}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            themen = _themen(text + " " + response)
            await qdrant.save_conversation(rolle, session_id, {
                "zusammenfassung": f"Person: {text[:2000]}\nStimme: {response[:2000]}",
                "wichtige_punkte": _wichtige_punkte(text),
                "themen": themen,
                "thema": themen[0] if themen else "allgemein",
                "sklave_nachricht": text,
                "herrin_antwort": response,
            })
        except Exception as e:
            logger.error("Fehler beim Speichern der Vanilla-Konversation (%s): %s", rolle, e)
