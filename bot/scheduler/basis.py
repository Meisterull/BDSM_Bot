"""
Scheduler-Grundgerüst: Job-Guards, Fangnetz, Nachholen blockierter Einmal-Jobs, Flow-Prüfung.

Aus bot/scheduler/followup.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
"""
import functools
from contextvars import ContextVar
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from telegram import Bot
from bot import config, state
from bot.services import paare
from bot.services import limits_check, zeiten

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Nachholen blockierter Einmal-Jobs
# ---------------------------------------------------------------------------
# Jobs, die einmal am Tag/in der Woche laufen, prüfen vorher den Chat-Mode
# (_flow_aktiv) und fielen bisher ersatzlos aus, wenn dort gerade ein Flow
# offen war (live 01.10.2026: Nachfrage 17:30 mitten in der unbeantworteten
# Stimmungsfrage; 27.09.: Wochenplanung im offenen Stille-Check-in). Ein mit
# @_nachholbar markierter Job, der an einem MODE scheitert, wird vorgemerkt;
# nachhol_tick_job holt ihn nach, sobald der Chat frei ist. Safeword-Pause und
# Coach-Ruhe sind gewollte Stopps und lösen kein Nachholen aus.
# Bewusst nur im Speicher: ein Neustart verliert auch den blockierenden Mode.
_blockiert_cv: ContextVar = ContextVar("job_blockiert", default=None)

_nachhol_lauf: ContextVar = ContextVar("nachhol_lauf", default=False)

_nachhol: dict = {}   # (paar_id, job_name) -> {"fn", "chat", "seit", "max_stunden"}


def _melde_blockiert(chat_id: str) -> None:
    """Vom Mode-Guard eines Jobs aufgerufen: dieser Lauf scheiterte an einem
    laufenden Flow in `chat_id`. Außerhalb eines @_nachholbar-Jobs ein No-op."""
    box = _blockiert_cv.get()
    if box is not None:
        box.append(chat_id)


def _nachholbar(max_stunden: int):
    """Markiert einen Einmal-Job als nachholbar. `max_stunden`: so lange wird
    ab dem regulären Lauf versucht – kürzer als der Abstand zum nächsten
    regulären Lauf wählen, sonst käme der Job doppelt (täglich: 5, wöchentlich: 26)."""
    def deko(fn):
        @functools.wraps(fn)
        async def wrapper(bot, *args, **kwargs):
            box: list = []
            token = _blockiert_cv.set(box)
            try:
                ergebnis = await fn(bot, *args, **kwargs)
            finally:
                _blockiert_cv.reset(token)
            schluessel = (paare.aktueller_kontext(), fn.__name__)
            if box:
                alt = _nachhol.get(schluessel)
                if not alt:
                    logger.info("%s wird nachgeholt, sobald der Chat frei ist.", fn.__name__)
                # Die Frist zählt ab dem REGULÄREN Lauf, nicht ab jedem Nachhol-Versuch.
                seit = alt["seit"] if (alt and _nachhol_lauf.get()) else datetime.now(timezone.utc)
                _nachhol[schluessel] = {"fn": wrapper, "chat": box[0], "seit": seit,
                                        "max_stunden": max_stunden}
            else:
                _nachhol.pop(schluessel, None)
            return ergebnis
        return wrapper
    return deko


def _zweiwochen_takt() -> bool:
    """True in geraden ISO-Wochen – deterministischer 2-Wochen-Takt für die
    cron-registrierten Jobs (lernkurve/coach_reflexion/profil_pflege).
    Ersetzt IntervalTrigger(weeks=2): der verlor bei jedem Deploy seine Phase
    (Anker = nächstes Wochentags-Vorkommen → bei häufigen Deploys faktisch
    wöchentlich) und drifte nach jeder DST-Umstellung um eine Stunde.
    Ein Nachhol-Lauf hat den Takt beim regulären Lauf schon bestanden – am
    Folgetag (neue ISO-Woche nach einem Sonntags-Job) gilt er weiter."""
    if _nachhol_lauf.get():
        return True
    return datetime.now(ZoneInfo(config.TIMEZONE)).isocalendar().week % 2 == 0


# Vanilla-Modus (Etappe 2): nur diese Jobs laufen für ein gleichberechtigtes
# Paar – alles andere (Follow-up, Tiny-Task-Freigabe, Training, Wochenplan,
# Coach-/Spiel-Impuls, Blitz, Lücken, Reflexionen …) setzt eine führende Seite
# voraus und wird zentral im _job_guard übersprungen. Etappe 3 erweitert die Liste.
VANILLA_JOBS = frozenset({"stimmung_job", "vanilla_impuls_job", "nachhol_tick_job"})


def _job_guard(fn):
    """Scheduler-Job absichern: (1) bei aktiver Safeword-Pause gar nicht laufen
    (kein automatischer Versand, solange pausiert), (1b) im Vanilla-Modus nur
    die Jobs aus VANILLA_JOBS, (2) Qdrant-/LLM-Fehler dürfen
    den Job nicht ungebremst durchschlagen lassen (Traceback-Log).
    Eine Abwesenheit (/abwesend) pausiert BEWUSST nichts – alle Jobs laufen
    weiter, der Zeitraum fließt nur als Fakt in die Generier-Prompts ein
    (abwesenheit.prompt_hinweis in limits_check/Chat-Prompts)."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        if state.is_paused():
            logger.info("Scheduler-Job '%s' übersprungen – System per Safeword pausiert", fn.__name__)
            return
        from bot.services import modi
        if modi.ist_vanilla() and fn.__name__ not in VANILLA_JOBS:
            logger.debug("Scheduler-Job '%s' übersprungen – Vanilla-Modus", fn.__name__)
            return
        try:
            return await fn(*args, **kwargs)
        except Exception:
            logger.exception("Scheduler-Job '%s' fehlgeschlagen", fn.__name__)
    return wrapper


def _job_fangnetz(fn):
    """Nur Fehler-Fangnetz OHNE Pause-Check – für Jobs, die auch während einer
    Safeword-Pause weiterlaufen sollen (Backups: eine tagelange Pause darf
    keine Backup-Lücke reißen; kein User-facing Versand)."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except Exception:
            logger.exception("Scheduler-Job '%s' fehlgeschlagen", fn.__name__)
    return wrapper


def _parse_datum(val: str) -> datetime:
    """Parse ISO datetime string, handling timezone-naive and aware formats."""
    if not val:
        return datetime(2000, 1, 1, tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return datetime(2000, 1, 1, tzinfo=timezone.utc)


def _flow_aktiv(chat_id: str, job_name: str) -> bool:
    """Gemeinsamer Guard vor Scheduler-Sends: nicht in einen aktiven UI-Flow
    hineinplatzen. Räumt vorher liegengebliebene (stale) Flows auf, damit ein
    vergessener Dialog die Jobs nicht dauerhaft blockiert."""
    if state.is_paused():
        logger.info("%s übersprungen – System per Safeword pausiert", job_name)
        return True
    # Coach-Ruhe / Zuschauer-Modus (Stille-Check-in, 09.09.): ALLE proaktiven
    # Dom-Jobs laufen über diesen Guard – zentral statt 13 Einzel-Gates.
    # Berichte an die Domina (Aufgaben-Ergebnis, Gefühl, Wünsche) gehen nicht
    # hier durch und laufen weiter. Sub-seitige Jobs sind nicht betroffen.
    if chat_id == paare.dom_chat_id():
        ruhe = state.coach_ruhe()
        if ruhe:
            logger.info("%s übersprungen – Coach-%s aktiv (Stille-Check-in)", job_name,
                        "Ruhe" if ruhe.get("modus") == "ruhe" else "Zuschauer-Modus")
            return True
    state.clear_if_stale(chat_id)
    mode = state.get_mode(chat_id)
    if mode not in ("chat", None):
        logger.info("%s übersprungen – Chat in Mode '%s'", job_name, mode)
        _melde_blockiert(chat_id)
        return True
    return False


def _nach_llm_verworfen(chat_id: str, job_name: str) -> bool:
    """Re-Check NACH einem LLM-Await (TOCTOU): im Generierungs-Fenster kann ein
    Safeword oder ein neuer UI-Flow gekommen sein – dann nicht mehr senden.
    Gegenstück zu _flow_aktiv (das VOR der Generierung prüft); Muster wie in
    followup_job/serie (Trace 06.07., Kleinkram)."""
    if state.is_paused() or state.get_mode(chat_id) not in ("chat", None):
        logger.info("%s nach Generierung verworfen – Pause/Mode im LLM-Fenster geändert.", job_name)
        if not state.is_paused():
            _melde_blockiert(chat_id)
        return True
    return False


@_job_guard
async def nachhol_tick_job(bot: Bot) -> None:
    """Holt vorgemerkte Einmal-Jobs nach (s. _nachholbar), sobald der Chat frei
    ist – nur im Abendfenster NACHHOL_FENSTER und höchstens EIN Job pro Tick,
    damit nach einem langen Flow keine Nachrichten-Salve kommt."""
    pid = paare.aktueller_kontext()
    jetzt = datetime.now(timezone.utc)
    for schluessel, e in list(_nachhol.items()):
        if schluessel[0] == pid and jetzt - e["seit"] > timedelta(hours=e["max_stunden"]):
            _nachhol.pop(schluessel, None)
            logger.info("Nachholen von %s aufgegeben – Chat blieb zu lange belegt.", schluessel[1])
    offen = sorted(((k, e) for k, e in _nachhol.items() if k[0] == pid),
                   key=lambda ke: ke[1]["seit"])
    if not offen:
        return
    if not zeiten.ist_im_fenster(datetime.now(ZoneInfo(config.TIMEZONE)), [config.NACHHOL_FENSTER]):
        return
    for schluessel, e in offen:
        state.clear_if_stale(e["chat"])
        if state.get_mode(e["chat"]) not in ("chat", None):
            continue
        logger.info("Nachhol-Lauf: %s", schluessel[1])
        token = _nachhol_lauf.set(True)
        try:
            await e["fn"](bot)
        except Exception:
            _nachhol.pop(schluessel, None)   # kein Fehler-Dauerfeuer bis zur Frist
            raise
        finally:
            _nachhol_lauf.reset(token)
        return
