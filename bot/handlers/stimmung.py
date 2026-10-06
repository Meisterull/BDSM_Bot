"""
Stimmungs-Tracking.
Sklave kann jederzeit /stimmung schreiben wenn er sich mitteilen möchte.
Kein täglicher aufdringlicher Job mehr – nur wenn STIMMUNG_ENABLED=true.

OHNE CHAT-MODE (06.10.2026): Die Frage setzte früher den Mode 'stimmung' und
hielt ihn bis zu 2 h. Blieb sie unbeantwortet, galt der Sub-Chat so lange als
belegt – der Spiel-Impuls fiel jeden Nachmittag aus, das Follow-up des Tages
auch. Jetzt trägt die Frage fünf Stimmungs-Knöpfe (ein Tipp genügt), und für
eine GETIPPTE Antwort gibt es nur noch eine Merkmarke im State
(`stimmung_offen_bis`): Die nächste freie Nachricht im Fenster zählt wie
bisher als Stimmung, aber kein Job sieht den Chat mehr als belegt an.
"""
import difflib
import logging
import random
import time
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper
from bot.handlers.sklave import _normalisiere
from bot.prompts import followup as fp
from bot.messages import t

logger = logging.getLogger(__name__)

# Kein Modul-Level t() (Review D8/N8): beim Import gibt es keinen Paar-
# Kontext – ein EN-Zweitpaar bekäme sonst die eingefrorene DE-Fassung.


def _zu_aehnlich(frage: str, vorherige: list[str]) -> bool:
    """Deterministischer Wiederholungs-Detektor: gleicher Satzanfang oder hohe
    Gesamt-Ähnlichkeit zu einer der letzten Fragen. Die Sperr-Liste im Prompt
    allein reicht nicht – Muster wie beim Sklave-Chat (_ist_echo, Befund
    02.07.): Prompt-Regeln verlieren gegen die Lieblings-Formulierung."""
    n = _normalisiere(frage)
    if not n:
        return False
    for v in vorherige:
        vn = _normalisiere(v)
        if not vn:
            continue
        if n.split()[:3] == vn.split()[:3]:
            return True
        if difflib.SequenceMatcher(None, n, vn).ratio() >= 0.7:
            return True
    return False


_VERSUCHE = 3

# Merkmarke statt Mode: bis wann eine getippte Nachricht als Stimmungs-Antwort
# zählt (Dauer = STALE_STIMMUNG_SECONDS, wie früher der Mode).
_OFFEN = "stimmung_offen_bis"
_STUFEN = ("1", "2", "3", "4", "5")


def _tasten() -> InlineKeyboardMarkup:
    """Fünf Stimmungen, von unten nach oben. Gespeichert wird nicht das Knopf-
    Etikett, sondern der ausführlichere STIMMUNG_WERT_<n> – der steht später in
    den Prompts (Tiny-Task, Dossier) und soll für sich verständlich sein."""
    knoepfe = [InlineKeyboardButton(t(f"BUTTON_STIMMUNG_{n}"), callback_data=f"stimmung:{n}") for n in _STUFEN]
    return InlineKeyboardMarkup([knoepfe[:3], knoepfe[3:]])


def _merke_offen(chat_id: str) -> None:
    state.get(chat_id)[_OFFEN] = time.time() + state.STALE_STIMMUNG_SECONDS


def wartet_auf_antwort(chat_id: str) -> bool:
    """Steht eine Stimmungsfrage offen, deren Fenster noch läuft? Für die
    Nachrichten-Weiche in main.py (nur im freien Chat, nach allen Modes)."""
    s = state.get(chat_id)
    bis = s.get(_OFFEN)
    if not bis:
        return False
    if time.time() > bis:
        s.pop(_OFFEN, None)
        return False
    return True


def _richtungs_kandidaten(verbrauchte: set[str]) -> list[str]:
    """Bis zu _VERSUCHE Richtungen OHNE Zurücklegen, zuerst die zuletzt nicht
    benutzten. Vorher wurde pro Versuch neu gewürfelt – ein Retry konnte damit
    dieselbe Richtung ziehen, die gerade zur Wiederholung geführt hatte
    (Befund 27.08.2026: vier Tage in Folge das Wetter-Bild). Reichen die
    frischen nicht, wird mit den verbrauchten aufgefüllt – sonst stünde nach
    ein paar Tagen gar keine Auswahl mehr zur Verfügung."""
    alle = fp.stimmung_richtungen()
    frisch = [r for r in alle if r not in verbrauchte]
    rest = [r for r in alle if r in verbrauchte]
    random.shuffle(frisch)
    random.shuffle(rest)
    return (frisch + rest)[:_VERSUCHE]


async def _frage_text() -> tuple[str, str]:
    """Jedes Mal frisch per LLM formulierte Stimmungs-Frage (Nutzer-Feedback:
    der immergleiche statische Text wirkt mechanisch). Die letzten Fragen gehen
    als Sperr-Liste in den Prompt; wird es trotzdem die gleiche Formulierung,
    greift ein Retry mit ANDERER Richtung. Fällt bei LLM-Fehlern – und wenn
    alle Versuche zu ähnlich bleiben – auf den statischen Standardtext zurück.
    Gibt (Frage, benutzte Richtung) zurück; die Richtung landet in der
    Sperr-Liste, damit sie morgen nicht sofort wieder dran ist."""
    try:
        letzte = await qdrant.get_recent_stimmung_eintraege(limit=5)
    except Exception as e:
        logger.warning("Letzte Stimmungs-Fragen nicht ladbar (Sperr-Liste leer): %s", e)
        letzte = []
    vorherige = [e.get("zusammenfassung", "") for e in letzte]
    verbrauchte = {e.get("richtung") for e in letzte if e.get("richtung")}
    kandidaten = _richtungs_kandidaten(verbrauchte)

    # Arbeitskopie: verworfene Kandidaten wandern in die Sperr-Liste des
    # NÄCHSTEN Versuchs. Sonst bekommt der Retry exakt dieselbe Vorgabe wie der
    # gescheiterte Versuch – und damit oft denselben Lieblings-Satzanfang.
    sperre = list(vorherige)
    try:
        for versuch, richtung in enumerate(kandidaten, start=1):
            text = (await grok.simple(
                fp.stimmung_abfragen(vermeiden=sperre, richtung=richtung),
                max_tokens=120,
            )).strip()
            if text and not _zu_aehnlich(text, vorherige):
                return text, richtung
            logger.info("Stimmungs-Frage zu ähnlich zu den letzten (Versuch %d, Richtung '%s') – Retry.",
                        versuch, richtung)
            if text:
                sperre.append(text)
    except Exception as e:
        logger.warning("Stimmungs-Frage per LLM fehlgeschlagen – Standardtext: %s", e)
    else:
        # KEIN Ausweichen auf den letzten Kandidaten: der ist als zu ähnlich
        # ERKANNT worden. Genau der ging bisher trotzdem raus – am 26./27.08.2026
        # zeichengleich an zwei Tagen hintereinander.
        logger.warning("Alle %d Stimmungs-Versuche zu ähnlich – statischer Standardtext "
                       "statt Wiederholung.", len(kandidaten))
    return t("STIMMUNG_FRAGE"), ""


async def _merke_frage(frage: str, richtung: str = "") -> None:
    """Gesendete Frage in die Sperr-Liste (typ=stimmung_frage) – NACH dem
    erfolgreichen Senden aufrufen, sonst blockt eine nie zugestellte
    Formulierung künftige Fragen (Trace 06.07., Lücke 6)."""
    if frage == t("STIMMUNG_FRAGE"):
        return  # statischer Fallback gehört nicht in die Sperr-Liste
    try:
        await qdrant.save_training("sklave", {"typ": "stimmung_frage",
                                              "zusammenfassung": frage,
                                              "richtung": richtung})
    except Exception as e:
        logger.warning("Stimmungs-Frage nicht als Sperr-Listen-Eintrag gespeichert: %s", e)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stimmung – Sklave initiiert selbst eine Stimmungsmeldung."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.sub_chat_id():
        return

    s = state.get(chat_id)
    if s.get("mode", "chat") != "chat":
        await update.message.reply_text(t("COMMON_BESCHAEFTIGT"))
        return

    async with telegram_helper.typing_action(context.bot, chat_id):
        frage, richtung = await _frage_text()
    await update.message.reply_text(frage, reply_markup=_tasten())
    # Merkmarke erst NACH erfolgreichem Senden – ein Sendefehler darf keine
    # offene Frage hinterlassen, die es nie gab (Trace 06.07., Lücke 6).
    _merke_offen(chat_id)
    # In die Chat-History – sonst kennt die Herrin im nächsten freien Chat-Turn
    # ihre eigene Frage nicht und deutet seine Folge-Nachricht gegen den Kontext
    # des VORTAGES (Befund 02.07.).
    state.add_message(chat_id, "assistant", frage)
    await _merke_frage(frage, richtung)


async def frage_stellen(bot: Bot) -> None:
    """Vom Scheduler aufgerufen – nur wenn STIMMUNG_ENABLED=true."""
    if not config.STIMMUNG_ENABLED:
        return

    sklave_chat = paare.sub_chat_id()
    state.clear_if_stale(sklave_chat)  # liegengebliebenen UI-Flow nicht ewig blockieren lassen
    s = state.get(sklave_chat)

    if s.get("mode", "chat") != "chat":
        logger.info("Stimmungsfrage übersprungen – aktiver State: %s", s.get("mode"))
        return

    frage, richtung = await _frage_text()
    # Re-Check NACH dem LLM-Await (TOCTOU): im Fenster kann der Sklave einen
    # Flow begonnen oder ein Safeword gesendet haben.
    if state.is_paused() or s.get("mode", "chat") != "chat":
        logger.info("Stimmungsfrage nach Generierung verworfen – Pause/Mode geändert.")
        return
    await bot.send_message(chat_id=sklave_chat, text=frage, reply_markup=_tasten())
    # Merkmarke/Sperr-Liste erst NACH erfolgreichem Senden (Trace 06.07.,
    # Lücke 6). BEWUSST kein set_mode: Der Chat bleibt frei, Spiel-Impuls und
    # Follow-up laufen weiter (s. Modulkopf).
    _merke_offen(sklave_chat)
    state.add_message(sklave_chat, "assistant", frage)  # s. Kommentar in start()
    await _merke_frage(frage, richtung)
    logger.info("Stimmungsfrage gesendet.")


async def callback_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ein Tipp auf einen der fünf Stimmungs-Knöpfe."""
    query = update.callback_query
    await query.answer()
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.sub_chat_id():
        return
    stufe = query.data.split(":", 1)[1]
    if stufe not in _STUFEN:
        return
    await query.edit_message_reply_markup(reply_markup=None)
    # Doppel-Tipp: Telegram kann den zweiten Tap noch zustellen, bevor die
    # Knöpfe weg sind – je Frage-Nachricht nur EINE Stimmung verbuchen.
    s = state.get(chat_id)
    if s.get("stimmung_knopf_mid") == query.message.message_id:
        return
    s["stimmung_knopf_mid"] = query.message.message_id
    await _antwort_verarbeiten(context.bot, chat_id, t(f"STIMMUNG_WERT_{stufe}"), query.message.reply_text)
    logger.info("Stimmung per Knopf erfasst (Stufe %s).", stufe)


async def handle_antwort(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet eine getippte Stimmungsantwort (Merkmarke offen, s. main.py)."""
    chat_id = str(update.effective_chat.id)
    await _antwort_verarbeiten(context.bot, chat_id, update.message.text.strip(), update.message.reply_text)


async def _antwort_verarbeiten(bot, chat_id: str, antwort: str, antworten) -> None:
    """Gemeinsamer Weg für Knopf und Text: speichern, In-Persona-Reaktion,
    Hinweis an die Dom-Seite. `antworten` ist reply_text der passenden Nachricht."""
    state.get(chat_id).pop(_OFFEN, None)
    if state.get_mode(chat_id) == "stimmung":      # Altbestand aus der Zeit mit Mode
        state.set_mode(chat_id, "chat")
    state.add_message(chat_id, "user", antwort)

    # Stimmung dauerhaft speichern, damit sie in Prompts/Dossier einfließt
    try:
        await qdrant.save_training("sklave", {"typ": "stimmung", "zusammenfassung": antwort})
    except Exception as e:
        logger.error("Fehler beim Speichern der Stimmung: %s", e)

    # In-Persona-Reaktion an den Sklaven (statt Textkonserve)
    from bot.prompts import followup as fp
    try:
        reaktion = await grok.simple(fp.reaktion_auf_stimmung(antwort), max_tokens=250)
    except Exception as e:
        logger.error("Fehler bei Stimmungs-Reaktion: %s", e)
        reaktion = t("FALLBACK_STIMMUNG_REAKTION")
    await antworten(reaktion)
    state.add_message(chat_id, "assistant", reaktion)

    # Natürlicher Hinweis an die Domina (Coach-Stimme, kein Bericht-Format, kein Pathos)
    from bot.prompts import coach_persona
    system = (
        f"Der Sklave hat sich von sich aus mit seiner Stimmung gemeldet.\n\n"
        f"{coach_persona.fuer_coach_prompt()}\n\n"
        f"Sag der Domina locker Bescheid, wie seine Stimmung ist, und gib ihr einen kurzen, "
        f"konkreten Gedanken, wie sie darauf eingehen könnte. 1-3 Sätze, du-Form, "
        f"kein Bericht-Format, keine Floskeln."
    )
    try:
        hinweis = await grok.simple(fp.nutzer_text("Seine Stimmungs-Nachricht", antwort), system=system)
        await telegram_helper.send_domina(
            bot,
            t("STIMMUNG_HINWEIS_AN_DOMINA", antwort=antwort, hinweis=hinweis),
            parse_mode="Markdown",
        )
        logger.info("Stimmungs-Hinweis an Domina gesendet.")
    except Exception as e:
        logger.error("Fehler beim Stimmungs-Hinweis: %s", e)