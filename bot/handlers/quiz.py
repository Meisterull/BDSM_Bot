"""
Quiz 🧠 – „Wie gut kennst du deine Herrin?"

/quiz (Sklave): Grok stellt EINE Frage, deren Antwort aus den GESPEICHERTEN
Daten belegbar ist (Domina-Profil, Domina-Dossier, aktive Coach-Regeln) –
nichts Erfundenes. Frage + Musterantwort landen im State (mode quiz_antwort);
die Antwort des Sklaven bewertet Grok mit temp=0 (RICHTIG/TEILWEISE/FALSCH):
richtig +15 Punkte, teilweise +5, falsch 0 + Konter der Herrin.
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper
from bot.services import sticker_reaktionen
from bot.prompts import persona
from bot.messages import t

logger = logging.getLogger(__name__)

PUNKTE_RICHTIG = 15
PUNKTE_TEILWEISE = 5


async def _wissens_kontext() -> str:
    """Belegbare Fakten über die Domina/Dynamik als Prompt-Block."""
    profil = await qdrant.get_user_profile("domina") or {}
    teile = []
    for feld, label in (("interessen", "Interessen"), ("grenzen", "Ihre Grenzen"),
                        ("ziele", "Ziele"), ("tempo", "Tempo"),
                        ("erfahrungsstand", "Erfahrungsstand")):
        wert = profil.get(feld)
        if isinstance(wert, list):
            wert = ", ".join(wert)
        if wert:
            teile.append(f"- {label}: {wert}")
    dossier = (profil.get("domina_dossier") or "").strip()
    if dossier:
        teile.append(f"- Dossier über sie:\n{dossier[:1200]}")
    try:
        regeln = await qdrant.get_active_coach_regeln("domina", limit=15)
        # Feld heißt "text" (qdrant.save_coach_regel) – "regel" war immer leer
        # (Hermes-Review H11: Regeln fehlten dadurch still im Quiz).
        regel_texte = [r.get("text", "") for r in regeln if r.get("text")]
        if regel_texte:
            teile.append("- Ihre verbindlichen Regeln:\n" + "\n".join(f"  • {r}" for r in regel_texte))
    except Exception:
        logger.exception("Quiz: Regeln-Load fehlgeschlagen (weiter ohne)")
    return "\n".join(teile)


async def _generiere_frage(chat_id: str, kontext: str) -> tuple[str, str] | None:
    """Erzeugt (frage, musterantwort) aus den belegbaren Daten – None bei
    Generierungs-Fehler. Merkt sich die Frage in der Anti-Wiederholungs-Liste."""
    s = state.get(chat_id)
    letzte_fragen = s.get("quiz_letzte_fragen", [])

    system = (
        "Du bist die Herrin und stellst deinem Sklaven EINE Quizfrage: wie gut kennt "
        "er dich und eure Dynamik? STRIKT:\n"
        "- Die Frage MUSS aus den Daten unten eindeutig beantwortbar sein – erfinde "
        "NICHTS, was dort nicht steht.\n"
        "- Keine Ja/Nein-Frage, keine Fangfrage.\n"
        + ("- NICHT diese kürzlich gestellten Fragen wiederholen: "
           + " | ".join(letzte_fragen) + "\n" if letzte_fragen else "")
        + "Antworte NUR als JSON: {\"frage\": \"...\", \"antwort\": \"knappe Musterantwort\"}\n"
        "Kein Text außerhalb des JSON.\n\n" + persona.fuer_sklaven_prompt()
    )
    try:
        roh = await grok.simple(f"Belegbare Daten:\n{kontext}", system=system, temperature=0.7)
        daten = grok.parse_json(roh)
        frage = (daten.get("frage") or "").strip()
        antwort = (daten.get("antwort") or "").strip()
        if not frage or not antwort:
            raise ValueError("Frage/Antwort leer")
    except Exception:
        logger.exception("Quiz-Fragen-Generierung fehlgeschlagen")
        return None
    s["quiz_letzte_fragen"] = (letzte_fragen + [frage])[-5:]
    return frage, antwort


def _frage_scharf_schalten(chat_id: str, frage: str, antwort: str) -> None:
    """Frage + Musterantwort in den State; die nächste Sklaven-Nachricht wird
    als Quiz-Antwort gewertet (mode quiz_antwort → handle_antwort)."""
    s = state.get(chat_id)
    s["quiz_frage"] = frage
    s["quiz_musterantwort"] = antwort
    state.set_mode(chat_id, "quiz_antwort")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/quiz – eine Frage über die Herrin stellen."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.sub_chat_id():
        return

    kontext = await _wissens_kontext()
    if len(kontext) < 40:
        await update.message.reply_text(t("QUIZ_ZU_WENIG_DATEN"))
        return

    ergebnis = await _generiere_frage(chat_id, kontext)
    if not ergebnis:
        await update.message.reply_text(t("QUIZ_FEHLER"))
        return
    frage, antwort = ergebnis
    _frage_scharf_schalten(chat_id, frage, antwort)
    await update.message.reply_text(t("QUIZ_FRAGE", frage=frage), parse_mode="Markdown")


async def sende_spontane_frage(bot) -> bool:
    """Spiel-Impuls 🎲 (scheduler.spiel_impuls_job): die Herrin stellt UNGEFRAGT
    eine Quiz-Frage – gleiche Mechanik wie /quiz, andere Zustellung. True nur
    bei echtem Versand (der Job setzt dann erst den Throttle-Anker)."""
    chat_id = paare.sub_chat_id()
    kontext = await _wissens_kontext()
    if len(kontext) < 40:
        logger.info("Spiel-Impuls-Quiz übersprungen – zu wenig belegbare Daten.")
        return False
    ergebnis = await _generiere_frage(chat_id, kontext)
    if not ergebnis:
        return False
    # TOCTOU-Re-Check nach dem LLM-Await (Muster _nach_llm_verworfen): im
    # Generierungs-Fenster kann ein Safeword oder ein UI-Flow gekommen sein.
    if state.is_paused() or state.get_mode(chat_id) not in ("chat", None):
        logger.info("Spiel-Impuls-Quiz nach Generierung verworfen – Pause/Mode geändert.")
        return False
    frage, antwort = ergebnis
    _frage_scharf_schalten(chat_id, frage, antwort)
    # Kontroll-Sticker als Auftakt ("ich sehe alles") – best-effort intern
    await sticker_reaktionen.sende_sklave(bot, sticker_reaktionen.AUGE)
    try:
        await telegram_helper.send_sklave(
            bot, t("SPIEL_IMPULS_QUIZ", frage=frage), parse_mode="Markdown")
    except Exception:
        # Nicht zugestellte Frage nicht scharf lassen – sonst würde die nächste
        # Chat-Nachricht als Antwort auf eine nie gesehene Frage gewertet.
        state.set_mode(chat_id, "chat")
        state.get(chat_id).pop("quiz_frage", None)
        state.get(chat_id).pop("quiz_musterantwort", None)
        raise
    return True


async def handle_antwort(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Antwort des Sklaven bewerten (mode quiz_antwort)."""
    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)
    text = update.message.text.strip()

    if text.lower() in ("/abbrechen", "abbrechen"):
        for k in ("quiz_frage", "quiz_musterantwort"):
            s.pop(k, None)
        state.set_mode(chat_id, "chat")
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return

    frage = s.pop("quiz_frage", "")
    muster = s.pop("quiz_musterantwort", "")
    state.set_mode(chat_id, "chat")
    if not frage or not muster:
        return

    urteil = "FALSCH"
    try:
        bewertung = await grok.simple(
            f'Frage: "{frage}"\nMusterantwort: "{muster}"\nSeine Antwort: """{text}"""',
            system=("Bewerte, ob seine Antwort inhaltlich zur Musterantwort passt. "
                    "Sei fair: andere Formulierung mit gleichem Kern = RICHTIG; "
                    "teilweise getroffen = TEILWEISE; daneben = FALSCH. "
                    "Antworte NUR mit einem Wort: RICHTIG, TEILWEISE oder FALSCH."),
            temperature=0,
        )
        wort = (bewertung or "").strip().upper()
        if "RICHTIG" in wort and "TEILWEISE" not in wort:
            urteil = "RICHTIG"
        elif "TEILWEISE" in wort:
            urteil = "TEILWEISE"
    except Exception:
        # Fail-fair: bei Bewertungs-Fehler lieber TEILWEISE als FALSCH.
        logger.exception("Quiz-Bewertung fehlgeschlagen – werte als TEILWEISE")
        urteil = "TEILWEISE"

    punkte_neu = {"RICHTIG": PUNKTE_RICHTIG, "TEILWEISE": PUNKTE_TEILWEISE, "FALSCH": 0}[urteil]
    if punkte_neu:
        profil = await qdrant.get_user_profile("sklave") or {}
        await qdrant.patch_profile_fields("sklave", {"punkte": profil.get("punkte", 0) + punkte_neu})

    # Herrin reagiert aufs Ergebnis (best-effort, Fallback = nüchterner Text)
    reaktion = ""
    try:
        stimmungen = {
            "RICHTIG": "Er hat richtig geantwortet – zeig dich (auf deine Art) zufrieden, dass er dich kennt.",
            "TEILWEISE": "Er lag halb richtig – amüsier dich über die Lücke und stell die Musterantwort klar.",
            "FALSCH": "Er lag daneben – konter genüsslich-spöttisch und nenne die richtige Antwort. Er sollte dich besser kennen.",
        }
        system = (
            f"Du sprichst direkt mit ihm. Quiz über dich: {stimmungen[urteil]} "
            f"Ein bis zwei Sätze, kein Markdown.\n\n" + persona.fuer_sklaven_prompt()
        )
        reaktion = grok.clean_text(await grok.simple(
            f'Frage: "{frage}"\nRichtige Antwort: "{muster}"\nSeine Antwort: """{text}"""',
            system=system, max_tokens=150)) or ""
    except Exception:
        logger.exception("Quiz-Reaktion fehlgeschlagen")

    # md_einbett_sicher (D9/N4): die LLM-Musterantwort steht im Template in
    # _…_ – ein '_'/'*' darin bräche das Markup und die Ergebnis-Nachricht
    # ginge NACH der Punktebuchung verloren (BadRequest ohne Fallback).
    ergebnis = t(f"QUIZ_{urteil}", punkte=punkte_neu,
                 antwort=telegram_helper.md_einbett_sicher(muster))
    await update.message.reply_text(ergebnis, parse_mode="Markdown")
    if reaktion:
        await update.message.reply_text(reaktion)
