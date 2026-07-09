"""
Privilegien Handler – Sklave kann Punkte gegen Privilegien einlösen.

Privilegien werden im Sklave-Profil als 'aktive_privilegien' Liste gespeichert
und vom Scheduler/den Task-Generatoren als Modifikatoren berücksichtigt.
Die Domina wird über jede Einlösung informiert und kann sie bestätigen oder verweigern.
"""
import logging
import uuid
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.handlers import entscheidung_flow
from bot.services import qdrant, telegram_helper, punkte, privileg_effekte
from bot.messages import t

logger = logging.getLogger(__name__)


PRIVILEGIEN = [
    {
        "id": "pause_tag",
        "kosten": 50,
        "name": "Pause-Tag",
        "beschreibung": "Skipt die nächste fällige Aufgabe ohne Streak-Verlust",
        "wirkung": "skip_next_task",
    },
    {
        "id": "easy_mode",
        "kosten": 30,
        "name": "Easy Mode (3 Tage)",
        "beschreibung": "3 Tage lang einfachere Aufgaben",
        "wirkung": "schwierigkeit_niedrig_3tage",
    },
    {
        "id": "wunsch_pflicht",
        "kosten": 40,
        "name": "Wunsch-Kategorie",
        "beschreibung": "Nächste Aufgabe MUSS aus deinen Wunsch-Kategorien stammen",
        "wirkung": "naechste_aus_wunsch",
    },
    {
        "id": "frei_aufgabe",
        "kosten": 80,
        "name": "Frei-Aufgabe",
        "beschreibung": "Du darfst die nächste Aufgabe selbst vorschlagen",
        "wirkung": "naechste_selbst_waehlen",
    },
    {
        "id": "lob",
        "kosten": 20,
        "name": "Lob anfordern",
        "beschreibung": "Sofortige Anerkennung deiner Herrin",
        "wirkung": "sofort_lob",
    },
    {
        "id": "ueberraschung",
        "kosten": 35,
        "name": "Überraschung",
        "beschreibung": "Eine Überraschung nach ihrer Wahl – was und wann, entscheidet sie",
        "wirkung": "sofort_ueberraschung",
    },
    {
        "id": "geheimnis_herrin",
        "kosten": 60,
        "name": "Ein Geheimnis der Herrin",
        "beschreibung": "Sie verrät dir etwas, das du noch nicht über sie weißt",
        "wirkung": "sofort_geheimnis",
    },
]

# Zusatz-Anweisung an die Herrin-Stimme je Sofort-Wirkung (bei Bestätigung).
_SOFORT_ANWEISUNG = {
    "sofort_lob": "Das Privileg ist Anerkennung: gib ihm ein ECHTES, konkretes Lob – kurz, ohne Zuckerguss.",
    "sofort_ueberraschung": ("Das Privileg ist eine Überraschung: kündige sie nur an – WAS es ist, "
                             "bleibt offen. Mach ihn neugierig, leg dich auf nichts fest."),
    "sofort_geheimnis": ("Das Privileg ist ein Geheimnis: verrate ihm tatsächlich eine kleine, intime "
                         "Sache über dich (passend zu deiner Persona erfunden, zu allem bisher Gesagten "
                         "konsistent) – kein Ausweichen, kein Vertrösten."),
}


def _privileg_by_id(pid: str) -> dict | None:
    return next((p for p in PRIVILEGIEN if p["id"] == pid), None)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/privileg – zeigt Katalog mit Inline-Buttons für den Sklaven."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.sub_chat_id():
        return

    profil = await qdrant.get_user_profile("sklave") or {}
    aktuelle_punkte = profil.get("punkte", 0)

    katalog = "\n\n".join(
        f"*{p['name']}* – {p['kosten']} P\n_{p['beschreibung']}_"
        for p in PRIVILEGIEN
    )

    # Inline-Buttons – einer pro Privileg, gefärbt je nach Leistbarkeit
    buttons = []
    for p in PRIVILEGIEN:
        leisten = aktuelle_punkte >= p["kosten"]
        emoji = "🎁" if leisten else "🔒"
        buttons.append([InlineKeyboardButton(
            f"{emoji} {p['name']} ({p['kosten']}P)",
            callback_data=f"privileg:einloesen:{p['id']}",
        )])
    keyboard = InlineKeyboardMarkup(buttons)

    state.set_mode(chat_id, "privileg_wahl")

    await update.message.reply_text(
        t("PRIVILEG_KATALOG", punkte=aktuelle_punkte, katalog=katalog),
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet Text-Eingabe (Fallback für User die keine Buttons nutzen)."""
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()

    if text.lower() in ("abbrechen", "/abbrechen", "stop", "stopp"):
        state.set_mode(chat_id, "chat")
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return

    try:
        nummer = int(text)
    except ValueError:
        await update.message.reply_text(t("PRIVILEG_NUR_NUMMER"))
        return

    if nummer < 1 or nummer > len(PRIVILEGIEN):
        await update.message.reply_text(t("PRIVILEG_NUMMER_BEREICH", max=len(PRIVILEGIEN)))
        return

    await _einloesen(update.message, context, PRIVILEGIEN[nummer - 1])


async def callback_einloesen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-Button: Privileg auswählen."""
    query = update.callback_query
    await query.answer()
    _, _, privileg_id = query.data.split(":", 2)
    p = _privileg_by_id(privileg_id)
    if not p:
        return
    await query.edit_message_reply_markup(reply_markup=None)
    await _einloesen(query.message, context, p)


async def _einloesen(message, context, privileg: dict) -> None:
    """Gemeinsame Logik für Text- und Button-basiertes Einlösen."""
    chat_id = str(message.chat_id)
    profil = await qdrant.get_user_profile("sklave") or {}
    aktuelle_punkte = profil.get("punkte", 0)

    if aktuelle_punkte < privileg["kosten"]:
        await message.reply_text(
            t("PRIVILEG_ZU_WENIG_PUNKTE", punkte=aktuelle_punkte,
              name=privileg["name"], kosten=privileg["kosten"]),
            parse_mode="Markdown",
        )
        state.set_mode(chat_id, "chat")
        return

    aktive = profil.get("aktive_privilegien", [])
    aktiv_id = str(uuid.uuid4())
    aktive.append({
        "aktiv_id": aktiv_id,
        "privileg_id": privileg["id"],
        "wirkung": privileg["wirkung"],
        "name": privileg["name"],
        "eingeloest_am": datetime.now(timezone.utc).isoformat(),
        "domina_bestaetigt": False,
        "verbraucht": False,
    })

    # Gezielt patchen statt Full-Upsert – sonst überrollt der stale Read parallel
    # gepatchte Felder (punkte/streak aus Scheduler-Jobs, frische hard_limits).
    await qdrant.patch_profile_fields("sklave", {
        "punkte": aktuelle_punkte - privileg["kosten"],
        "aktive_privilegien": aktive,
    })

    state.set_mode(chat_id, "chat")
    neue_abzeichen = await punkte.privileg_eingeloest()

    bestaetigung = t(
        "PRIVILEG_EINGELOEST", name=privileg["name"], kosten=privileg["kosten"],
        rest=aktuelle_punkte - privileg["kosten"],
    )
    if neue_abzeichen:
        bestaetigung += t("PRIVILEG_NEUE_ABZEICHEN", liste=", ".join(
            f"{a['emoji']} {a['name']}" for a in neue_abzeichen
        ))

    await message.reply_text(bestaetigung, parse_mode="Markdown")

    # Domina informieren – mit Inline-Buttons (Callback ist modus-unabhängig).
    # Text-Mode nur setzen, wenn die Domina frei ist; sonst nicht ihren Flow kapern.
    domina_s = state.get(paare.dom_chat_id())
    domina_s["privileg_aktiv_id"] = aktiv_id
    if state.get_mode(paare.dom_chat_id()) in ("chat", None):
        state.set_mode(paare.dom_chat_id(), "privileg_entscheidung")

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_BESTAETIGEN"), callback_data=f"privileg:bestaetigen:{aktiv_id}"),
        InlineKeyboardButton(t("BUTTON_VERWEIGERN"), callback_data=f"privileg:verweigern:{aktiv_id}"),
    ]])
    await context.bot.send_message(
        chat_id=paare.dom_chat_id(),
        text=t(
            "PRIVILEG_AN_DOMINA", name=privileg["name"], kosten=privileg["kosten"],
            beschreibung=privileg["beschreibung"],
        ),
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def callback_entscheidung(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-Button: schnelle Bestätigung/Verweigerung ohne Kommentar."""
    query = update.callback_query
    await query.answer()
    _, action, aktiv_id = query.data.split(":", 2)
    bestaetigt = action == "bestaetigen"
    await query.edit_message_reply_markup(reply_markup=None)
    await _entscheidung_anwenden(context, aktiv_id, bestaetigt, kommentar="")
    # Mode nur zurücksetzen, wenn er noch UNSERER ist – ein später Tap auf einen
    # alten Button darf keinen gerade aktiven anderen Flow killen.
    if state.get_mode(paare.dom_chat_id()) == "privileg_entscheidung":
        state.set_mode(paare.dom_chat_id(), "chat")
    state.get(paare.dom_chat_id()).pop("privileg_aktiv_id", None)
    emoji = "✅" if bestaetigt else "❌"
    await query.message.reply_text(
        t("PRIVILEG_ENTSCHIEDEN", emoji=emoji, entscheidung="bestätigt" if bestaetigt else "verweigert")
    )


def _parse_entscheidung(text: str) -> tuple[bool, str] | None:
    """Erkennt bestätigen/verweigern (+ optionalen Kommentar) in der Domina-Antwort."""
    text_lower = text.lower()
    if text_lower.startswith("bestätigen") or text_lower.startswith("bestaetigen"):
        return True, (text.split(" ", 1)[1].strip() if " " in text else "")
    if text_lower.startswith("verweigern"):
        return False, text[len("verweigern"):].strip()
    return None


async def handle_entscheidung(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Domina bestätigt oder verweigert ein eingelöstes Privileg."""
    await entscheidung_flow.handle_entscheidung(
        update, context,
        state_key="privileg_aktiv_id",
        parse_entscheidung=_parse_entscheidung,
        hinweis_text=t("PRIVILEG_ENTSCHEIDUNG_HINWEIS"),
        bestaetigung_text=lambda _bestaetigt: t("PRIVILEG_ENTSCHEIDUNG_GESPEICHERT"),
        persistiere=_entscheidung_anwenden,
    )


async def _entscheidung_anwenden(context, aktiv_id: str, bestaetigt: bool, kommentar: str) -> None:
    """Persistiert Entscheidung, erstattet ggf. Punkte und benachrichtigt Sklave."""
    profil = await qdrant.get_user_profile("sklave") or {}
    aktive = profil.get("aktive_privilegien", [])

    eintrag = next((p for p in aktive if p.get("aktiv_id") == aktiv_id), None)
    if not eintrag:
        await telegram_helper.send_domina(context.bot, t("PRIVILEG_NICHT_GEFUNDEN"))
        return

    from bot.services import grok
    from bot.prompts import persona
    kosten = 0
    if bestaetigt:
        sofort_zeile = _SOFORT_ANWEISUNG.get(eintrag.get("wirkung", ""), "")
        if sofort_zeile:
            sofort_zeile = f"{sofort_zeile}\n"
        system = (
            f"Du bist die Herrin. Dein Sklave hat sich ein Privileg mit Punkten "
            f"verdient und du gewährst es ihm jetzt. "
            f"{sofort_zeile}"
            f"Reagiere direkt an ihn – ein bis zwei Sätze, Ich-Form, keine Floskel.\n\n"
            f"{persona.fuer_sklaven_prompt()}"
        )
        fallback = t("FALLBACK_PRIVILEG_GEWAEHRT", name=eintrag["name"]) + (f" {kommentar}" if kommentar else "")
    else:
        privileg_def = _privileg_by_id(eintrag["privileg_id"])
        kosten = privileg_def["kosten"] if privileg_def else 0
        system = (
            f"Du bist die Herrin. Dein Sklave wollte ein Privileg einlösen, "
            f"aber du verweigerst es ihm. "
            f"Seine Punkte hat er zurück. Reagiere direkt an ihn – ein bis zwei Sätze, Ich-Form, "
            f"ehrliche knappe Absage ohne Bot-Spruch.\n\n"
            f"{persona.fuer_sklaven_prompt()}"
        )
        fallback = t("FALLBACK_PRIVILEG_VERWEIGERT", name=eintrag["name"], kosten=kosten)

    prompt = f"Privileg: {eintrag['name']}"
    if kommentar:
        prompt += f"\nDein Kommentar zur Entscheidung: {kommentar}"

    # ERST persistieren (frischer Read direkt vor dem Patch, kein LLM-Await
    # dazwischen → kein Lost-Update-Fenster), DANN die Nachricht generieren.
    # So wird die „Punkte zurück"-Zusage nur angehängt, wenn die Erstattung
    # wirklich passiert ist (Review D6: Vanish-Race versprach sonst Erstattung,
    # die nie gebucht wurde).
    profil = await qdrant.get_user_profile("sklave") or {}
    aktive = profil.get("aktive_privilegien", [])
    eintrag_frisch = next((p for p in aktive if p.get("aktiv_id") == aktiv_id), None)
    erstattet = False
    if eintrag_frisch is None:
        logger.warning("Privileg %s beim Persistieren nicht mehr vorhanden – Entscheidung nicht gespeichert.", aktiv_id)
    elif bestaetigt:
        eintrag_frisch["domina_bestaetigt"] = True
        eintrag_frisch["domina_kommentar"] = kommentar
        privileg_effekte.setze_ttl_bei_bestaetigung(eintrag_frisch)
        await qdrant.patch_profile_fields("sklave", {"aktive_privilegien": aktive})
    else:
        await qdrant.patch_profile_fields("sklave", {
            "punkte": profil.get("punkte", 0) + kosten,
            "aktive_privilegien": [p for p in aktive if p.get("aktiv_id") != aktiv_id],
        })
        erstattet = True

    if not bestaetigt and not erstattet:
        # Eintrag war schon weg (TTL/Cleanup-Race) – nichts erstattet, also darf
        # auch das LLM keine Erstattung versprechen.
        system = system.replace("Seine Punkte hat er zurück. ", "")
        fallback = t("FALLBACK_PRIVILEG_VERWEIGERT", name=eintrag["name"], kosten=0).split(" Deine ")[0]

    try:
        meldung_sklave = await grok.simple(prompt, system=system)
    except Exception as e:
        logger.error("Fehler bei Privileg-Entscheidungs-Nachricht: %s", e)
        meldung_sklave = fallback
    if erstattet:
        meldung_sklave += t("PRIVILEG_PUNKTE_ZURUECK", kosten=kosten)
    await telegram_helper.send_sklave(context.bot, meldung_sklave)
