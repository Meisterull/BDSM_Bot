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
from bot.services import paare, modi
from bot.services import qdrant, grok, telegram_helper
from bot.handlers.sklave import _normalisiere
from bot.prompts import followup as fp
from bot.messages import t

# Vanilla (Etappe 2): die Stimmungsfrage geht an BEIDE Personen, Reaktion und
# Hinweis kommen von der neutralen Stimme (prompts/vanilla.py). `rolle` ist
# überall der Slot (paare.ROLLE_*), im BDSM/Kink-Modus immer der Sub.

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


def _rolle_von(chat_id: str) -> str:
    return paare.ROLLE_DOM if chat_id == paare.dom_chat_id() else paare.ROLLE_SUB


def _darf(chat_id: str) -> bool:
    """Sub immer; die Dom-Seite nur im gleichberechtigten Modus."""
    return chat_id == paare.sub_chat_id() or (modi.gleichberechtigt() and chat_id == paare.dom_chat_id())


def _fallback_frage() -> str:
    return t("STIMMUNG_FRAGE_VANILLA") if modi.ist_vanilla() else t("STIMMUNG_FRAGE")


async def _frage_text(rolle: str = paare.ROLLE_SUB) -> tuple[str, str]:
    """Jedes Mal frisch per LLM formulierte Stimmungs-Frage (Nutzer-Feedback:
    der immergleiche statische Text wirkt mechanisch). Die letzten Fragen gehen
    als Sperr-Liste in den Prompt; wird es trotzdem die gleiche Formulierung,
    greift ein Retry mit ANDERER Richtung. Fällt bei LLM-Fehlern – und wenn
    alle Versuche zu ähnlich bleiben – auf den statischen Standardtext zurück.
    Gibt (Frage, benutzte Richtung) zurück; die Richtung landet in der
    Sperr-Liste, damit sie morgen nicht sofort wieder dran ist."""
    try:
        letzte = await qdrant.get_recent_stimmung_eintraege(limit=5, user_id=rolle)
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
            if modi.ist_vanilla():
                from bot.prompts import vanilla as vp
                prompt = vp.stimmung_frage(rolle, vermeiden=sperre, richtung=richtung)
            else:
                prompt = fp.stimmung_abfragen(vermeiden=sperre, richtung=richtung)
            text = (await grok.simple(prompt, max_tokens=120)).strip()
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
    return _fallback_frage(), ""


async def _merke_frage(frage: str, richtung: str = "", rolle: str = paare.ROLLE_SUB) -> None:
    """Gesendete Frage in die Sperr-Liste (typ=stimmung_frage) – NACH dem
    erfolgreichen Senden aufrufen, sonst blockt eine nie zugestellte
    Formulierung künftige Fragen (Trace 06.07., Lücke 6)."""
    if frage == _fallback_frage():
        return  # statischer Fallback gehört nicht in die Sperr-Liste
    try:
        await qdrant.save_training(rolle, {"typ": "stimmung_frage",
                                              "zusammenfassung": frage,
                                              "richtung": richtung})
    except Exception as e:
        logger.warning("Stimmungs-Frage nicht als Sperr-Listen-Eintrag gespeichert: %s", e)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stimmung – Sklave (Vanilla: beide) initiiert selbst eine Stimmungsmeldung."""
    chat_id = str(update.effective_chat.id)
    if not _darf(chat_id):
        return
    rolle = _rolle_von(chat_id)

    s = state.get(chat_id)
    if s.get("mode", "chat") != "chat":
        await update.message.reply_text(t("COMMON_BESCHAEFTIGT"))
        return

    async with telegram_helper.typing_action(context.bot, chat_id):
        frage, richtung = await _frage_text(rolle)
    await update.message.reply_text(frage, reply_markup=_tasten())
    # Merkmarke erst NACH erfolgreichem Senden – ein Sendefehler darf keine
    # offene Frage hinterlassen, die es nie gab (Trace 06.07., Lücke 6).
    _merke_offen(chat_id)
    # In die Chat-History – sonst kennt die Herrin im nächsten freien Chat-Turn
    # ihre eigene Frage nicht und deutet seine Folge-Nachricht gegen den Kontext
    # des VORTAGES (Befund 02.07.).
    state.add_message(chat_id, "assistant", frage)
    await _merke_frage(frage, richtung, rolle)


async def frage_stellen(bot: Bot) -> None:
    """Vom Scheduler aufgerufen – nur wenn STIMMUNG_ENABLED=true. Vanilla:
    an beide Personen (nacheinander, je mit eigener Sperr-Liste)."""
    if not config.STIMMUNG_ENABLED:
        return
    rollen = (paare.ROLLE_DOM, paare.ROLLE_SUB) if modi.ist_vanilla() else (paare.ROLLE_SUB,)
    for rolle in rollen:
        try:
            await _frage_an(bot, rolle)
        except Exception:
            logger.exception("Stimmungsfrage (%s) fehlgeschlagen", rolle)


async def _frage_an(bot: Bot, rolle: str) -> None:
    chat = paare.paar_im_kontext().chat_id(rolle)
    state.clear_if_stale(chat)  # liegengebliebenen UI-Flow nicht ewig blockieren lassen
    s = state.get(chat)

    if s.get("mode", "chat") != "chat":
        logger.info("Stimmungsfrage (%s) übersprungen – aktiver State: %s", rolle, s.get("mode"))
        return

    frage, richtung = await _frage_text(rolle)
    # Re-Check NACH dem LLM-Await (TOCTOU): im Fenster kann die Person einen
    # Flow begonnen oder ein Safeword gesendet haben.
    if state.is_paused() or s.get("mode", "chat") != "chat":
        logger.info("Stimmungsfrage nach Generierung verworfen – Pause/Mode geändert.")
        return
    await bot.send_message(chat_id=chat, text=frage, reply_markup=_tasten())
    # Merkmarke/Sperr-Liste erst NACH erfolgreichem Senden (Trace 06.07.,
    # Lücke 6). BEWUSST kein set_mode: Der Chat bleibt frei, Spiel-Impuls und
    # Follow-up laufen weiter (s. Modulkopf).
    _merke_offen(chat)
    state.add_message(chat, "assistant", frage)  # s. Kommentar in start()
    await _merke_frage(frage, richtung, rolle)
    logger.info("Stimmungsfrage gesendet (%s).", rolle)


async def callback_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ein Tipp auf einen der fünf Stimmungs-Knöpfe."""
    query = update.callback_query
    await query.answer()
    chat_id = str(update.effective_chat.id)
    if not _darf(chat_id):
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
    rolle = _rolle_von(chat_id)

    # Stimmung dauerhaft speichern, damit sie in Prompts/Dossier einfließt
    try:
        await qdrant.save_training(rolle, {"typ": "stimmung", "zusammenfassung": antwort})
    except Exception as e:
        logger.error("Fehler beim Speichern der Stimmung: %s", e)

    if modi.ist_vanilla():
        await _vanilla_reaktion_und_hinweis(bot, chat_id, rolle, antwort, antworten)
        return

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

async def _vanilla_reaktion_und_hinweis(bot, chat_id: str, rolle: str, antwort: str, antworten) -> None:
    """Vanilla: Reaktion der neutralen Stimme an die Person, kurzer Hinweis an
    die andere Person (beide sehen den Stand des anderen – Entscheidung 05.11.2026,
    im Onboarding angekündigt)."""
    from bot.prompts import vanilla as vp
    try:
        system, user = vp.stimmung_reaktion(rolle, antwort)
        reaktion = await grok.simple(user, system=system, max_tokens=250)
    except Exception as e:
        logger.error("Fehler bei Stimmungs-Reaktion (Vanilla): %s", e)
        reaktion = t("FALLBACK_STIMMUNG_REAKTION")
    await antworten(reaktion)
    state.add_message(chat_id, "assistant", reaktion)

    andere = vp.andere_rolle(rolle)
    try:
        system, user = vp.stimmung_hinweis(rolle, antwort)
        hinweis = await grok.simple(user, system=system)
        ich = vp.person(rolle)
        von = ich["anrede"] or ("deine Partnerin" if ich["fem"] else "dein Partner")
        await telegram_helper.send_an(
            bot, paare.paar_im_kontext(), andere,
            t("STIMMUNG_HINWEIS_AN_PARTNER", name=telegram_helper.escape_md(von),
              antwort=telegram_helper.md_einbett_sicher(antwort), hinweis=hinweis),
            parse_mode="Markdown",
        )
        logger.info("Stimmungs-Hinweis (Vanilla) an %s gesendet.", andere)
    except Exception as e:
        logger.error("Fehler beim Stimmungs-Hinweis (Vanilla): %s", e)
