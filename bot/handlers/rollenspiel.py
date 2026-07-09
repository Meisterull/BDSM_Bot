"""
Rollenspiel Handler – Szenarien für die Domina.
"""
import logging
import time
from telegram import Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper
from bot.messages import t

logger = logging.getLogger(__name__)

SZENARIEN_BIBLIOTHEK = {
    "1": {
        "name": "Verhör",
        "beschreibung": "Strenge Befragung, Rechenschaft ablegen",
        "ton": "streng, knapp, keine Erklärungen, kurze Befehle",
        "vokabular": ["Rechenschaft", "sofort", "erklär dich", "unzureichend"],
        "aufgaben_kategorien": ["Regeln", "Psycho"],
    },
    "2": {
        "name": "Inspektion",
        "beschreibung": "Detaillierte Kontrolle und Bewertung des Sklaven",
        "ton": "kühl, präzise, bewertend, professionell distanziert",
        "vokabular": ["Inspektion", "Standard", "Mängel", "protokolliert"],
        "aufgaben_kategorien": ["Dienst", "Regeln"],
    },
    "3": {
        "name": "Prüfung",
        "beschreibung": "Der Sklave muss seine Fähigkeiten beweisen",
        "ton": "fordernd, erwartungsvoll, testet Grenzen",
        "vokabular": ["beweise", "zeig mir", "nicht gut genug", "wiederhole"],
        "aufgaben_kategorien": ["Regeln", "Demütigung"],
    },
    "4": {
        "name": "Diener",
        "beschreibung": "Perfekter Service und Aufmerksamkeit",
        "ton": "anspruchsvoll, erwartet Perfektion, lobt selten",
        "vokabular": ["diene", "aufmerksam", "antizipiere", "selbstverständlich"],
        "aufgaben_kategorien": ["Dienst", "Regeln"],
    },
    "5": {
        "name": "Bestrafungsraum",
        "beschreibung": "Konsequenzen für Fehlverhalten",
        "ton": "ernst, unerbittlich, keine Diskussion",
        "vokabular": ["Konsequenz", "Fehlverhalten", "Wiedergutmachung", "verstanden?"],
        "aufgaben_kategorien": ["Bestrafung", "Psycho"],
    },
}

INTENSITAETEN = {
    "1": "leicht – sanft und spielerisch",
    "2": "mittel – bestimmt und klar",
    "3": "intensiv – streng und fordernd",
}


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/rollenspiel – zeigt die Szenarien-Bibliothek."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    zeilen = [t("ROLLENSPIEL_LISTE_TITEL")]
    for nr, s in SZENARIEN_BIBLIOTHEK.items():
        zeilen.append(f"{nr}\\. *{s['name']}* – {s['beschreibung']}")
    zeilen.append(t("ROLLENSPIEL_LISTE_FUSS"))
    zeilen.append(t("ROLLENSPIEL_ABBRECHEN_HINWEIS"))

    state.set_mode(chat_id, "rollenspiel_wahl")
    await update.message.reply_text("\n".join(zeilen), parse_mode="MarkdownV2")


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet alle Rollenspiel-States."""
    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)
    text = update.message.text.strip()
    mode = s.get("mode")

    if text.lower() in ("abbrechen", "/abbrechen"):
        _clear_rollenspiel_state(s)
        state.set_mode(chat_id, "chat")
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return

    if mode == "rollenspiel_wahl":
        await _handle_wahl(update, context, s, text)
    elif mode == "rollenspiel_intensitaet":
        await _handle_intensitaet(update, context, s, text)
    elif mode == "rollenspiel_aktiv":
        from bot.handlers import domina
        await domina.handle(update, context)


async def _handle_wahl(update, context, s: dict, text: str) -> None:
    chat_id = str(update.effective_chat.id)
    if text in SZENARIEN_BIBLIOTHEK:
        s["pending_szenario"] = SZENARIEN_BIBLIOTHEK[text]
        s["pending_szenario_custom"] = False
    else:
        s["pending_szenario"] = {
            "name": text[:50],
            "beschreibung": text,
            "ton": "dominant und bestimmt",
            "vokabular": [],
            "aufgaben_kategorien": ["allgemein"],
        }
        s["pending_szenario_custom"] = True

    state.set_mode(chat_id, "rollenspiel_intensitaet")
    intensitaet_text = "\n".join(f"{nr}\\. {bez}" for nr, bez in INTENSITAETEN.items())
    await update.message.reply_text(
        t("ROLLENSPIEL_INTENSITAET_FRAGE",
          name=telegram_helper.escape_md(s["pending_szenario"]["name"]),
          liste=intensitaet_text),
        parse_mode="MarkdownV2"
    )


async def _handle_intensitaet(update, context, s: dict, text: str) -> None:
    if text not in INTENSITAETEN:
        await update.message.reply_text(t("ROLLENSPIEL_1_2_3"))
        return

    szenario = s.pop("pending_szenario", {})
    custom = s.pop("pending_szenario_custom", False)
    intensitaet = INTENSITAETEN[text]
    await _start_szenario(update, context, szenario, intensitaet, custom)


async def _start_szenario(
    update, context, szenario: dict, intensitaet: str, custom: bool = False
) -> None:
    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)

    s["szenario_name"] = szenario["name"]
    s["szenario_ton"] = szenario["ton"]
    s["szenario_vokabular"] = szenario.get("vokabular", [])
    s["rollenspiel_intensitaet"] = intensitaet
    # Start-Zeitstempel: der Fr/Sa-Vorschlags-Job räumt darüber eingeschlafene,
    # nie beendete Rollenspiele ab (die sonst jeden Vorschlag für immer blocken)
    s["szenario_seit"] = time.time()
    state.set_mode(chat_id, "rollenspiel_aktiv")

    typ_label = "eigenes" if custom else ""
    escaped_name = telegram_helper.escape_md(szenario['name'])
    escaped_intensitaet = telegram_helper.escape_md(intensitaet)
    await update.message.reply_text(
        t("ROLLENSPIEL_AKTIV",
          prefix=typ_label + " " if typ_label else "",
          name=escaped_name, intensitaet=escaped_intensitaet),
        parse_mode="MarkdownV2"
    )

    try:
        from bot.prompts import persona
        intro_system = (
            f"Du bist die Herrin. Ein besonderer Modus beginnt jetzt – schreib deinem Sklaven eine kurze atmosphärische Einleitung, "
            f"zwei bis drei Sätze, Ich-Form, ohne das Szenario direkt zu benennen.\n\n"
            f"{persona.fuer_sklaven_prompt()}"
        )
        intro = await grok.simple(
            f"Szenario (Kontext, NICHT wörtlich erwähnen): '{szenario['name']}'\n"
            f"Ton-Vorgabe für diesen Modus: {szenario['ton']}\n"
            f"Intensität: {intensitaet}",
            system=intro_system,
        )
        await telegram_helper.send_sklave(context.bot, intro)
    except Exception as e:
        logger.error("Fehler bei Rollenspiel-Intro: %s", e)

    logger.info("Rollenspiel gestartet: %s (Intensität: %s)", szenario["name"], intensitaet)


async def beenden(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/rollenspiel_beenden – beendet das aktive Szenario."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    s = state.get(chat_id)
    szenario_name = s.get("szenario_name", "")

    _clear_rollenspiel_state(s)
    state.set_mode(chat_id, "chat")

    if szenario_name:
        await update.message.reply_text(t("ROLLENSPIEL_BEENDET", name=szenario_name))
    else:
        await update.message.reply_text(t("ROLLENSPIEL_KEIN_AKTIV"))

    logger.info("Rollenspiel beendet: %s", szenario_name)


def _clear_rollenspiel_state(s: dict) -> None:
    for key in ("szenario_name", "szenario_ton", "szenario_vokabular", "szenario_seit",
                "rollenspiel_intensitaet", "pending_szenario", "pending_szenario_custom"):
        s.pop(key, None)
