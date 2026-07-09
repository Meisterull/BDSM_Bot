"""
Gemeinsamer Entscheidungs-Flow für Domina-Text-Entscheidungen.

Wird von wunsch.handle_entscheidung und privileg.handle_entscheidung genutzt:
Die Domina antwortet als Text (Schlüsselwort + optionaler Kommentar), der Flow
prüft den State, parst die Entscheidung, räumt den State auf, bestätigt der
Domina und ruft die handler-spezifische Persistenz-Funktion auf.
"""
from typing import Any, Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from bot import state


async def handle_entscheidung(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    state_key: str,
    parse_entscheidung: Callable[[str], tuple[Any, str] | None],
    hinweis_text: str,
    bestaetigung_text: Callable[[Any], str],
    persistiere: Callable[..., Awaitable[None]],
) -> None:
    """Generischer Text-Entscheidungs-Flow der Domina.

    state_key: Schlüssel im Domina-State, der die Referenz-ID enthält
               (z.B. 'wunsch_id' oder 'privileg_aktiv_id').
    parse_entscheidung: nimmt den rohen Text, gibt (entscheidung, kommentar)
               zurück oder None, wenn kein Schlüsselwort erkannt wurde.
    hinweis_text: Markdown-Hinweis bei nicht erkannter Eingabe.
    bestaetigung_text: erzeugt aus der Entscheidung die Bestätigung an die Domina.
    persistiere: async Funktion (context, ref_id, entscheidung, kommentar).
    """
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()
    s = state.get(chat_id)
    ref_id = s.get(state_key)

    if not ref_id:
        state.set_mode(chat_id, "chat")
        return

    geparst = parse_entscheidung(text)
    if geparst is None:
        await update.message.reply_text(hinweis_text, parse_mode="Markdown")
        return
    entscheidung, kommentar = geparst

    vorheriger_mode = state.get_mode(chat_id)
    state.set_mode(chat_id, "chat")
    s.pop(state_key, None)
    # ERST persistieren, DANN bestätigen – sonst stünde bei einem Persistenz-
    # Fehler eine falsche Erfolgsmeldung im Chat und die Entscheidung wäre
    # unwiederholbar verloren (ref_id bereits gepoppt).
    try:
        await persistiere(context, ref_id, entscheidung, kommentar)
    except Exception:
        # State wiederherstellen, damit die Domina einfach erneut antworten
        # kann; den Fehler meldet der globale error_handler.
        s[state_key] = ref_id
        state.set_mode(chat_id, vorheriger_mode)
        raise
    await update.message.reply_text(bestaetigung_text(entscheidung))
