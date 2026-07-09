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
        regel_texte = [r.get("regel", "") for r in regeln if r.get("regel")]
        if regel_texte:
            teile.append("- Ihre verbindlichen Regeln:\n" + "\n".join(f"  • {r}" for r in regel_texte))
    except Exception:
        logger.exception("Quiz: Regeln-Load fehlgeschlagen (weiter ohne)")
    return "\n".join(teile)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/quiz – eine Frage über die Herrin stellen."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.sub_chat_id():
        return

    kontext = await _wissens_kontext()
    if len(kontext) < 40:
        await update.message.reply_text(t("QUIZ_ZU_WENIG_DATEN"))
        return

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
        await update.message.reply_text(t("QUIZ_FEHLER"))
        return

    s["quiz_frage"] = frage
    s["quiz_musterantwort"] = antwort
    s["quiz_letzte_fragen"] = (letzte_fragen + [frage])[-5:]
    state.set_mode(chat_id, "quiz_antwort")
    await update.message.reply_text(t("QUIZ_FRAGE", frage=frage), parse_mode="Markdown")


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

    ergebnis = t(f"QUIZ_{urteil}", punkte=punkte_neu, antwort=muster)
    await update.message.reply_text(ergebnis, parse_mode="Markdown")
    if reaktion:
        await update.message.reply_text(reaktion)
