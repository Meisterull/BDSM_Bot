"""
Bewertung einer erledigten Aufgabe durch die Dom-Seite: fünf Sterne zum
Antippen unter dem Bericht, dazu ein Knopf, um dem Sub etwas mitzugeben.

Bewusst OHNE Chat-Mode (18.11.2026). Vorher setzte der Bericht den Mode
`aufgabe_bewertung`: 30 Minuten lang galt jede Nachricht der Dom-Seite als
Bewertung („Bitte bewerte mit 1-5") – ein Auftrag an den Bot ging so verloren,
eine nur zum Weiterkommen getippte Ziffer zählte als Urteil, und die Tages-Jobs
für ihren Chat fielen in dieser Zeit aus. Wer mitliest, aber selten antwortet,
braucht eine Frage, die warten kann: Sie steht jetzt in den Knöpfen, der Chat
bleibt frei. Den Kommentar-Mode setzt erst der Knopf dafür.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from bot import state
from bot.services import qdrant, telegram_helper, grok
from bot.messages import t

logger = logging.getLogger(__name__)

# Referenzen auf Fire-and-forget-Tasks halten, sonst GC sie evtl. vor Abschluss.
_BG_TASKS: set = set()

# So lange gilt eine getippte Ziffer 1–5 noch als Antwort auf die letzte
# Bewertungsfrage (s. per_ziffer). Wann gefragt wurde, steht an der Aufgabe
# selbst (FELD_GEFRAGT) – kein Chat-State, ein Neustart ändert daran nichts.
ZIFFER_FENSTER = timedelta(hours=12)
FELD_GEFRAGT = "bewertung_gefragt_am"
MODE_KOMMENTAR = "aufgabe_kommentar"


def _kommentar_zeile(task_id: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(t("BUTTON_BEWERTUNG_KOMMENTAR"),
                                 callback_data=f"bewertung:kommentar:{task_id}")]


def sterne_buttons(task_id: str, kommentar: bool = True) -> InlineKeyboardMarkup:
    """Knöpfe unter dem Bericht: 1–5 Sterne in einer Reihe, darunter „ihm etwas
    dazu sagen". Die Aufgabe steckt in den Knöpfen – sie verfallen nicht."""
    zeilen = [[InlineKeyboardButton(f"{n} ⭐", callback_data=f"bewertung:{n}:{task_id}")
               for n in range(1, 6)]]
    if kommentar:
        zeilen.append(_kommentar_zeile(task_id))
    return InlineKeyboardMarkup(zeilen)


async def merke_gefragt(task_id: str) -> None:
    """An der Aufgabe festhalten, dass die Bewertungsfrage eben gestellt wurde –
    nur damit eine aus Gewohnheit GETIPPTE Ziffer noch ankommt (per_ziffer).
    Kein Mode, nichts wird blockiert."""
    await qdrant.update_task(task_id, {FELD_GEFRAGT: datetime.now(timezone.utc).isoformat()})


def _gefragt_vor(task: dict) -> timedelta | None:
    try:
        wann = datetime.fromisoformat(task.get(FELD_GEFRAGT) or "")
    except (TypeError, ValueError):
        return None
    if wann.tzinfo is None:
        wann = wann.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - wann


async def _offene_frage() -> str | None:
    """Kennung der Aufgabe, deren Bewertungsfrage zuletzt gestellt wurde und
    noch offen ist (unbewertet, innerhalb von ZIFFER_FENSTER) – sonst None."""
    tasks = await qdrant.get_tasks_by_status(["erledigt"], limit=20, sort_by_datum=True)
    offen = []
    for task in tasks:
        vor = _gefragt_vor(task)
        if vor is None or vor > ZIFFER_FENSTER or task.get("domina_bewertung"):
            continue
        if task.get("qdrant_point_id"):
            offen.append((vor, str(task["qdrant_point_id"])))
    return min(offen)[1] if offen else None


async def _eigener_task(task_id: str) -> dict | None:
    """Die Aufgabe, wenn es sie gibt und sie diesem Paar gehört – die Kennung
    kommt aus callback_data, also von außen."""
    task = await qdrant.get_task(task_id)
    if not task:
        return None
    besitzer = task.get("user_id")
    if besitzer and besitzer != qdrant.mandanten_key("sklave"):
        logger.warning("Bewertung für fremde Aufgabe verworfen (%s).", task_id)
        return None
    return task


async def _bewerten(bot, task_id: str, wert: int) -> str:
    """Bewertung ablegen samt Folgen (Schwierigkeit, Vorlieben-Notiz).
    'ok' | 'schon' (war bereits bewertet) | 'fehlt' (Aufgabe unbekannt)."""
    task = await _eigener_task(task_id)
    if task is None:
        return "fehlt"
    if task.get("domina_bewertung"):
        return "schon"
    await qdrant.update_task(task_id, {"domina_bewertung": wert})
    logger.info("Aufgabe bewertet: %d von 5 (%s).", wert, task_id)
    kontext = SimpleNamespace(bot=bot)
    try:
        await _pruefe_schwierigkeit(kontext, wert)
    except Exception as e:
        logger.error("Fehler bei Schwierigkeitsgrad-Prüfung: %s", e)
    # Ebene 2 (implizit lernen): bei Top-Bewertung Vorlieben-Notiz vorschlagen.
    if wert >= 5:
        import asyncio
        _bg = asyncio.create_task(_vorlieben_notiz_vorschlagen(bot, task_id))
        _BG_TASKS.add(_bg)
        _bg.add_done_callback(_BG_TASKS.discard)
    return "ok"


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Knöpfe unter dem Bericht (Rolle via gates._callback_gate: Dom):
    `bewertung:<1-5>:<task>` legt die Sterne ab, `bewertung:kommentar:<task>`
    öffnet den Kommentar an den Sub."""
    query = update.callback_query
    try:
        _, aktion, roh_id = (query.data or "").split(":", 2)
        task_id = str(uuid.UUID(roh_id))
    except ValueError:
        await query.answer()
        return
    chat_id = str(update.effective_chat.id)

    if aktion == "kommentar":
        await query.answer()
        state.clear_if_stale(chat_id)
        if state.get_mode(chat_id) not in ("chat", None, MODE_KOMMENTAR):
            await query.message.reply_text(t("BEWERTUNG_GERADE_BELEGT"))
            return
        if await _eigener_task(task_id) is None:
            await query.message.reply_text(t("COMMON_TASK_NICHT_GEFUNDEN"))
            return
        s = state.get(chat_id)
        s["kommentar_task_id"] = task_id
        s["kommentar_msg_id"] = query.message.message_id
        state.set_mode(chat_id, MODE_KOMMENTAR)
        await query.message.reply_text(t("BEWERTUNG_KOMMENTAR_BITTE"))
        return

    if aktion not in ("1", "2", "3", "4", "5"):
        await query.answer()
        return
    ergebnis = await _bewerten(context.bot, task_id, int(aktion))
    if ergebnis == "fehlt":
        await query.answer(t("COMMON_TASK_NICHT_GEFUNDEN"))
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    await query.answer(t("BEWERTUNG_SCHON") if ergebnis == "schon"
                       else t("BEWERTUNG_NOTIERT_TOAST", wert=aktion))
    # Die Sterne fallen weg, der Kommentar-Knopf bleibt stehen.
    try:
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([_kommentar_zeile(task_id)]))
    except Exception:
        pass


async def per_ziffer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Eine getippte Ziffer 1–5 der Dom-Seite im freien Chat gilt als Antwort
    auf die Bewertungsfrage – aber nur, wenn die Frage das LETZTE ist, was in
    ihrem Chat steht (state.gesendet_offen: seither kein Wortwechsel mit dem
    Coach und keine andere Nachricht des Bots). Sonst kann die Zahl alles
    Mögliche meinen und geht an den Coach. True, wenn verbraucht."""
    text = (update.message.text or "").strip()
    if text not in ("1", "2", "3", "4", "5"):
        return False
    chat_id = str(update.effective_chat.id)
    zuletzt = state.gesendet_offen(chat_id)
    if not zuletzt or t("GEFUEHL_BEWERTUNG_FRAGE") not in (zuletzt[-1].get("text") or ""):
        return False
    try:
        task_id = await _offene_frage()
    except Exception:
        logger.exception("Offene Bewertungsfrage nicht lesbar – Ziffer geht an den Coach.")
        return False
    if not task_id:
        return False
    if await _bewerten(context.bot, task_id, int(text)) != "ok":
        return False
    await update.message.reply_text(t("BEWERTUNG_NOTIERT_TOAST", wert=text))
    return True


async def _pruefe_schwierigkeit(context, bewertung: int) -> None:
    """Passt die Aufgaben-Schwierigkeit automatisch an Bewertungs-Durchschnitt an."""
    # sort_by_datum: ohne serverseitige Sortierung liefert scroll bei >100
    # erledigten Tasks eine willkürliche Teilmenge statt der neuesten.
    results = await qdrant.get_tasks_by_status(["erledigt"], sort_by_datum=True)
    letzte = sorted(
        [r for r in results if r.get("domina_bewertung")],
        key=lambda x: x.get("erteilt_am", ""), reverse=True
    )[:5]

    if len(letzte) < 5:
        return

    bewertungen = [t["domina_bewertung"] for t in letzte if "domina_bewertung" in t]
    avg = sum(bewertungen) / len(bewertungen)

    domina_profile = await qdrant.get_user_profile("domina") or {}
    schwierigkeit = domina_profile.get("aufgaben_schwierigkeit", "normal")

    neue_schwierigkeit = schwierigkeit
    hinweis = ""

    if avg >= 4.5 and schwierigkeit != "hoch":
        neue_schwierigkeit = "hoch"
        hinweis = t("BEWERTUNG_KOMPLEX_HOCH")
    elif avg <= 2.0 and schwierigkeit != "niedrig":
        neue_schwierigkeit = "niedrig"
        hinweis = t("BEWERTUNG_KOMPLEX_NIEDRIG")
    elif 2.5 <= avg <= 3.5 and schwierigkeit != "normal":
        neue_schwierigkeit = "normal"
        hinweis = t("BEWERTUNG_KOMPLEX_NORMAL")

    if neue_schwierigkeit != schwierigkeit:
        await qdrant.patch_profile_fields("domina", {"aufgaben_schwierigkeit": neue_schwierigkeit})
        await telegram_helper.send_domina(context.bot, hinweis)
        try:
            from bot.prompts import coach_persona
            tipp_system = (
                f"{coach_persona.fuer_strukturierten_output()}\n\n"
                f"Gib der Domina in 1-2 Sätzen einen konkreten Tipp, wie sie Aufgaben formulieren kann, "
                f"die besser zu ihr und ihrem Sklaven passen. Kein Markdown."
            )
            tipp = await grok.simple(
                f"Die Domina hat ihre letzten 5 Aufgaben im Schnitt mit {avg:.1f}/5 Sternen "
                f"bewertet. Die Aufgaben-Komplexität wurde auf '{neue_schwierigkeit}' angepasst.",
                system=tipp_system,
            )
            await telegram_helper.send_domina(context.bot, t("BEWERTUNG_TIPP_PREFIX", tipp=tipp))
        except Exception as e:
            logger.error("Fehler bei Bewertungs-Coaching-Tipp: %s", e)


async def _vorlieben_notiz_vorschlagen(bot, task_id: str) -> None:
    """Bei 5-Sterne-Bewertung: Grok formuliert eine Vorlieben-Notiz, die der Coach
    sich fuer zukuenftige Vorschlaege merken kann. Wird als 'pending' gespeichert
    und mit Ja/Nein-Buttons an die Domina geschickt."""
    from bot.handlers import coach_regeln as _cr
    try:
        task = await qdrant.get_task(task_id)
        if not task:
            return
        aufgabe = task.get("aufgabe", "")
        kategorie = task.get("kategorie", "")
        if not aufgabe:
            return

        system = """Die Domina hat eine Aufgabe mit 5/5 Sternen bewertet.

Formuliere daraus eine VERALLGEMEINERTE Vorlieben-Notiz, die der Coach sich
fuer zukuenftige Aufgaben-Vorschlaege merken kann. Maximal EIN Satz, konkret,
in der 2./3. Person ueber die Domina (z.B. "Sie mag Aufgaben mit X").

Wenn nichts Verallgemeinerbares ableitbar ist (z.B. zu spezifisch), antworte NUR mit:
KEINE_REGEL

Sonst nur die Notiz als reinen Text, ohne Anfuehrungszeichen."""
        prompt = f"Aufgabe (Kategorie: {kategorie or '?'}):\n{aufgabe[:600]}"

        antwort = grok.clean_text(await grok.simple(prompt, system=system, temperature=0))  # Notiz-Ableitung: deterministisch
        if not antwort or antwort.upper().startswith("KEINE_REGEL"):
            return

        point_id = await qdrant.save_coach_regel(
            user_id="domina",
            text=antwort,
            typ="notiz",
            status="pending",
            quelle="abgeleitet_bewertung",
            kontext=f"5-Sterne-Aufgabe (Kategorie {kategorie}): {aufgabe[:200]}",
        )
        await _cr.sende_vorschlag(
            bot, point_id, antwort,
            kontext=f"5-Sterne-Bewertung der Aufgabe „{aufgabe[:80]}…“",
        )
    except Exception as e:
        logger.error("Fehler beim Vorlieben-Notiz-Vorschlag: %s", e)
