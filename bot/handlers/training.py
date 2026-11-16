"""
Psycho-Training für die Dom-Seite.

Eine Übung ist eine fertige Nachricht: Sie wirkt beim Lesen und verlangt keine
Antwort. Wer etwas dazu sagen will, tippt den Knopf unter der Übung – erst der
öffnet (kurz) den Antwort-Mode. Vorher setzte jede Übung den Mode selbst: 30
Minuten lang galt dann JEDE Nachricht als Trainings-Antwort (ein Auftrag an
den Bot wurde so als Antwort verbucht und ging verloren), danach verfiel die
Übung still.

Die Übung liegt ab dem Versand in der training-Sammlung (auch unbeantwortet –
Typ-Wechsel und „nicht wiederholen" hängen an den gesendeten Übungen); eine
Antwort wird später am selben Eintrag ergänzt und fließt ins Dossier der
Dom-Seite (handlers/dossier.aktualisiere_domina_dossier).

/training            → Übung auf Abruf
training_job (Di+Do) → Übung zur Trainings-Zeit des Paares (TRAINING_TIME)
Mini-App             → services/miniapp/training.py (gleicher Generator)
"""
import logging
import uuid

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.messages import t
from bot.prompts import training as tp
from bot.services import grok, kontext_pflege, limits_check, paare, qdrant, telegram_helper

logger = logging.getLogger(__name__)


TRAINING_TYPEN = list(tp.TYPEN)

MODE = "training_antwort"
# Flow-Keys des Antwort-Modes (stehen auch in state.FLOW_STATE_KEYS).
_STATE_KEYS = ("training_id", "training_typ", "training_uebung", "training_msg_id")


def typ_label(typ: str) -> str:
    """Anzeigename der Übungsart in der UI-Sprache des Paares."""
    return t(f"TRAINING_TYP_{typ.upper()}") if typ in TRAINING_TYPEN else typ.capitalize()


def naechster_typ(eintraege: list[dict]) -> str:
    """Typ-Wechsel reihum. `eintraege` neueste zuerst; gezählt werden nur echte
    Übungen – ein Kategorie-Training aus der Mini-App (typ='kategorie') ließ
    den Wechsel sonst dauerhaft auf dem ersten Typ hängen."""
    for e in eintraege or []:
        if e.get("typ") in TRAINING_TYPEN:
            return TRAINING_TYPEN[(TRAINING_TYPEN.index(e["typ"]) + 1) % len(TRAINING_TYPEN)]
    return TRAINING_TYPEN[0]


def bereinigt(text: str, typ: str) -> str:
    """Nachbearbeitung der generierten Übung: Steuer-Tags und Markdown raus
    (die Nachricht geht ohne parse_mode raus); bei Übungsarten, die mit dem
    Lesen fertig sind, eine angehängte Schlussfrage abschneiden."""
    sauber = telegram_helper.strip_md(kontext_pflege.ohne_steuer_tags(text or "")).strip()
    # Umschließende Anführungszeichen nur als Paar um den GANZEN Text entfernen –
    # ein Beispielsatz am Ende („Worte für die Szene") behält sein Schlusszeichen.
    if len(sauber) > 1 and sauber[0] == sauber[-1] == '"' and '"' not in sauber[1:-1]:
        sauber = sauber[1:-1].strip()
    if typ in tp.OHNE_SCHLUSSFRAGE:
        sauber = tp.ohne_schlussfrage(sauber)
    return sauber.strip()


async def neue_uebung(typ: str = "") -> dict | None:
    """Erzeugt eine Übung für das Paar im Kontext: wählt den Typ (wenn keiner
    vorgegeben ist), lädt den Stoff und generiert mit Grenzen-Prüfung.

    Rückgabe {"typ", "uebung", "level"} – oder None, wenn der Text auch im
    zweiten Versuch eine Grenze berührt oder leer bleibt. Speichert nichts."""
    domina_profile = await qdrant.get_user_profile("domina") or {}
    sklave_profile = await qdrant.get_user_profile("sklave") or {}
    eintraege = await qdrant.get_training_entries("domina", limit=20)
    if typ not in TRAINING_TYPEN:
        typ = naechster_typ(eintraege)
    hard_limits = sklave_profile.get("hard_limits") or []
    system, prompt = tp.uebung(
        typ, domina_profile,
        letzte_aufgaben=await kontext_pflege.letzte_aufgaben(limit=4, max_tage=30),
        fruehere_uebungen=tp.uebungs_zeilen(eintraege),
        sub_hard_limits=hard_limits,
    )
    text = await limits_check.generate_mit_limit_retry(
        (system, prompt), hard_limits, domina_profile.get("grenzen") or [], reasoning=True)
    text = bereinigt(text or "", typ)
    if not text:
        return None
    return {"typ": typ, "uebung": text, "level": domina_profile.get("aktuelles_level", 1)}


async def uebung_ablegen(daten: dict, quelle: str, training_id: str | None = None) -> str:
    """Legt eine zugestellte Übung ab (noch ohne Antwort) und gibt ihre ID zurück."""
    return await qdrant.save_training("domina", {
        "typ": daten["typ"],
        "uebung": daten["uebung"],
        "antwort": "",
        "feedback": "",
        "zusammenfassung": f"{daten['typ']}: {daten['uebung'][:80]}",   # nur Kurz-Label/Embedding
        "level": daten.get("level", 1),
        "quelle": quelle,
    }, point_id=training_id)


async def antwort_verarbeiten(typ: str, uebung: str, antwort: str, training_id: str = "") -> str:
    """Kern für Chat und Mini-App: Antwort ablegen, Rückmeldung des Coach
    erzeugen und ergänzen. Die Antwort wird VOR dem LLM-Aufruf gespeichert –
    fällt die Rückmeldung aus, ist ihr Text trotzdem nicht verloren.
    Rückgabe: die Rückmeldung ("" bei LLM-Ausfall)."""
    typ = typ if typ in TRAINING_TYPEN else "reflexion"
    profil = await qdrant.get_user_profile("domina") or {}
    level = profil.get("aktuelles_level", 1)
    felder = {"antwort": antwort, "zusammenfassung": f"{typ}: {antwort[:80]}"}
    if training_id:
        await qdrant.update_training(training_id, felder)
    else:
        training_id = await qdrant.save_training("domina", {
            "typ": typ, "uebung": uebung, "feedback": "", "level": level, **felder})
    system, prompt = tp.feedback(typ, uebung, antwort, level)
    try:
        feedback = (await grok.simple(prompt, system=system, reasoning=True) or "").strip()
    except Exception as e:
        logger.error("Fehler beim Training-Feedback: %s", e)
        return ""
    if feedback:
        try:
            await qdrant.update_training(training_id, {"feedback": feedback})
        except Exception:
            logger.exception("Training: Rückmeldung nicht ablegbar (Antwort ist gespeichert)")
    return feedback


# ---------------------------------------------------------------------------
# Zustellung im Chat
# ---------------------------------------------------------------------------

def _nachricht(typ: str, uebung: str) -> str:
    return t("TRAINING_NACHRICHT", typ=typ_label(typ), uebung=uebung)


def _knopf(training_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        t("TRAINING_KNOPF_ANTWORT"), callback_data=f"training:antwort:{training_id}")]])


async def _zugestellt(chat_id: str, training_id: str, daten: dict, quelle: str) -> None:
    """Nach erfolgreichem Versand: Übung ablegen und in den Chat-Verlauf des
    Coach hängen. Ohne den Verlaufs-Eintrag kennt der Coach seine eigene Übung
    nicht, wenn sie einfach lostippt statt den Knopf zu nehmen."""
    try:
        await uebung_ablegen(daten, quelle, training_id)
    except Exception:
        # Der Knopf fällt dann auf den Nachrichtentext zurück (s. callback).
        logger.exception("Training: Ablage der Übung fehlgeschlagen")
    state.add_message(chat_id, "assistant", daten["uebung"])


def _modus_beenden(chat_id: str) -> None:
    s = state.get(chat_id)
    for key in _STATE_KEYS:
        s.pop(key, None)
    state.set_mode(chat_id, "chat")


# ---------------------------------------------------------------------------
# /training Command
# ---------------------------------------------------------------------------

async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/training – eine Übung auf Abruf."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    if not config.TRAINING_ENABLED:
        await update.message.reply_text(t("TRAINING_DEAKTIVIERT"))
        return

    try:
        async with telegram_helper.typing_action(context.bot, chat_id):
            daten = await neue_uebung()
    except Exception as e:
        logger.error("Fehler beim Generieren der Trainingsübung: %s", e)
        daten = None
    if not daten:
        await update.message.reply_text(t("FEHLER_ALLGEMEIN"))
        return

    training_id = str(uuid.uuid4())
    await update.message.reply_text(_nachricht(daten["typ"], daten["uebung"]),
                                    reply_markup=_knopf(training_id))
    await _zugestellt(chat_id, training_id, daten, quelle="befehl")


# ---------------------------------------------------------------------------
# Antworten: Knopf unter der Übung → Antwort-Mode → Rückmeldung
# ---------------------------------------------------------------------------

def _uebung_aus_nachricht(text: str) -> str:
    """Übungstext aus der gesendeten Nachricht (ohne Kopfzeile) – Rückfall,
    wenn der Eintrag zur Übung fehlt."""
    _kopf, _trenner, rest = (text or "").partition("\n\n")
    return (rest or text or "").strip()


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Knopf „Antworten" unter einer Übung (Rolle via gates._callback_gate: Dom).
    Der EINZIGE Weg in den Antwort-Mode: Sie will ausdrücklich etwas dazu
    schreiben. Der Knopf verfällt nicht – die Antwort hängt über die ID an
    genau dieser Übung, egal wann sie kommt."""
    query = update.callback_query
    await query.answer()
    try:
        _, action, roh_id = query.data.split(":", 2)
        training_id = str(uuid.UUID(roh_id))
    except ValueError:
        return
    if action != "antwort":
        return

    chat_id = str(update.effective_chat.id)
    state.clear_if_stale(chat_id)
    if state.get_mode(chat_id) not in ("chat", None, MODE):
        await query.message.reply_text(t("TRAINING_GERADE_BELEGT"))
        return

    eintrag = await qdrant.get_training_by_id(training_id) or {}
    if eintrag.get("antwort"):
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.message.reply_text(t("TRAINING_SCHON_BEANTWORTET"))
        return

    s = state.get(chat_id)
    # Die ID kommt von außen: geschrieben wird nur auf einen Eintrag, der
    # nachweislich diesem Paar gehört – sonst entsteht beim Antworten ein neuer.
    s["training_id"] = training_id if eintrag else ""
    s["training_typ"] = eintrag.get("typ") or ""
    s["training_uebung"] = eintrag.get("uebung") or _uebung_aus_nachricht(query.message.text)
    s["training_msg_id"] = query.message.message_id
    state.set_mode(chat_id, MODE)
    await query.message.reply_text(t("TRAINING_ANTWORT_BITTE"))


def _verlauf_ergaenzen(chat_id: str, uebung: str, antwort: str, feedback: str) -> None:
    """Der Wechsel soll im Coach-Chat weitergehen können: Übung (falls sie dort
    nicht mehr unter den jüngsten Nachrichten steht), Antwort und Rückmeldung
    kommen in den Verlauf."""
    juengste = [m.get("content") for m in state.get_history(chat_id)[-6:]]
    if uebung and uebung not in juengste:
        state.add_message(chat_id, "assistant", uebung)
    state.add_message(chat_id, "user", antwort)
    if feedback:
        state.add_message(chat_id, "assistant", feedback)


async def handle_antwort(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Antwort auf eine Übung – erreichbar nur nach dem Knopf unter der Übung."""
    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)
    text = update.message.text.strip()

    if text.lower() in ("abbrechen", "/abbrechen"):
        _modus_beenden(chat_id)
        await update.message.reply_text(t("TRAINING_BEENDET"))
        return

    # Ein Auftrag an den Bot („schreib ihm …", „Aufgabe: …") ist keine Antwort
    # auf die Übung: Mode verlassen und normal weiterreichen. Der Knopf unter
    # der Übung bleibt für später stehen.
    from bot.handlers import domina  # lazy: zirkulären Import vermeiden
    if domina.ist_auftrag_an_bot(text):
        _modus_beenden(chat_id)
        await domina.handle(update, context)
        return

    training_id = s.get("training_id") or ""
    typ = s.get("training_typ") or "reflexion"
    uebung = s.get("training_uebung", "")
    msg_id = s.get("training_msg_id")

    try:
        async with telegram_helper.typing_action(context.bot, chat_id):
            feedback = await antwort_verarbeiten(typ, uebung, text, training_id)
    except Exception as e:
        # Ablage fehlgeschlagen – Mode bleibt, sie kann es noch einmal schicken.
        logger.error("Fehler beim Ablegen der Trainings-Antwort: %s", e)
        await update.message.reply_text(t("FEHLER_ALLGEMEIN"))
        return

    _modus_beenden(chat_id)
    _verlauf_ergaenzen(chat_id, uebung, text, feedback)
    if msg_id:
        try:   # beantwortet → Knopf unter der Übung entfernen (rein kosmetisch)
            await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=msg_id, reply_markup=None)
        except Exception:
            pass
    if feedback:
        await telegram_helper.reply_markdown_safe(update.message, t("TRAINING_FEEDBACK_PREFIX", feedback=feedback))
    else:
        await update.message.reply_text(t("TRAINING_ANTWORT_ABGELEGT"))


# ---------------------------------------------------------------------------
# Zeit-Job (vom Scheduler aufgerufen: tagesjobs.training_job, Di+Do)
# ---------------------------------------------------------------------------

async def daily_training(bot: Bot) -> None:
    """Sendet der Dom-Seite eine Übung. Setzt KEINEN Mode: Die Nachricht steht
    für sich, der Chat bleibt frei für alles andere."""
    if not config.TRAINING_ENABLED:
        return
    from bot.scheduler import basis  # lazy: das scheduler-Paket importiert die Handler

    domina_chat = paare.dom_chat_id()
    # Pause, Coach-Ruhe/Zuschauer-Modus, laufender Flow (→ wird nachgeholt)
    if basis._flow_aktiv(domina_chat, "Training"):
        return
    if not await qdrant.get_user_profile("domina"):
        return

    try:
        daten = await neue_uebung()
    except Exception as e:
        logger.error("Fehler beim Generieren der Trainingsübung: %s", e)
        return
    if not daten:
        logger.warning("Training: keine Übung erzeugt (Grenzen-Prüfung oder leere Antwort).")
        return

    # Re-Check NACH dem LLM-Await (TOCTOU, D9/M9): im Reasoning-Fenster kann ein
    # Safeword oder ein neuer Flow gekommen sein.
    if basis._nach_llm_verworfen(domina_chat, "Training"):
        return

    training_id = str(uuid.uuid4())
    try:
        await telegram_helper.send_domina(bot, _nachricht(daten["typ"], daten["uebung"]),
                                          reply_markup=_knopf(training_id))
    except Exception as e:
        # Ablage und Verlauf erst NACH erfolgreichem Versand: eine nie gesehene
        # Übung darf weder den Typ-Wechsel weiterdrehen noch im Verlauf stehen.
        logger.error("Fehler beim Senden der Trainingsübung: %s", e)
        return
    await _zugestellt(domina_chat, training_id, daten, quelle="job")
    logger.info("Training gesendet: %s", daten["typ"])
