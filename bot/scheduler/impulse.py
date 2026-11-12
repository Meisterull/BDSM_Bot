"""
Spiel-/Coach-Impulse, Blitz-, Event-, Dauer- und Kalender-Ticks.

Aus bot/scheduler/followup.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
"""
import logging
import random
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from telegram import Bot
from bot import config, state
from bot.services import paare
from bot.services import qdrant, telegram_helper, zeiten
from bot.messages import t
from bot.scheduler import basis

logger = logging.getLogger(__name__)


@basis._job_guard
async def event_check_job(bot: Bot) -> None:
    """Startet geplante Event-Arcs 🎂, deren Startfenster erreicht ist
    (Finale = Event-Tag; Logik in handlers/event_arc.starte_faellige)."""
    from bot.handlers import event_arc  # lazy: zirkulären Import vermeiden
    await event_arc.starte_faellige(bot)


@basis._job_guard
async def dauer_job(bot: Bot) -> None:
    """Dauer-Anweisungen 🕰: Enden nachfragen (→ normaler Followup-Flow) +
    zufällige Zwischen-Checks (Logik in handlers/dauer.pruefe_laufende)."""
    from bot.handlers import dauer  # lazy: zirkulären Import vermeiden
    await dauer.pruefe_laufende(bot)


@basis._job_guard
async def kalender_job(bot: Bot) -> None:
    """Adventskalender 🎄: öffnet das heutige Türchen (handlers/advent)."""
    from bot.handlers import advent  # lazy: zirkulären Import vermeiden
    await advent.oeffne_tuerchen(bot)


@basis._job_guard
async def blitz_check_job(bot: Bot) -> None:
    """Entscheidet zufällig, ob JETZT eine Blitzaufgabe ⚡ kommt (Opt-in via
    /blitz). Fenster: BLITZ_FENSTER ∩ kinderfreie Zeiten; Throttle über
    BLITZ_MIN_ABSTAND_TAGE; nie eine zweite offene Blitzaufgabe."""
    domina_profile = await qdrant.get_user_profile("domina") or {}
    if not domina_profile.get("blitz_aktiv", False):
        return
    if basis._flow_aktiv(paare.sub_chat_id(), "Blitz-Check"):
        return

    jetzt_lokal = datetime.now(ZoneInfo(config.TIMEZONE))
    if not zeiten.ist_im_fenster(jetzt_lokal, [config.BLITZ_FENSTER]):
        return
    # kinderfreie_zeiten = Fenster in denen sie UNGESTÖRT ist (leer = immer).
    if not zeiten.ist_im_fenster(jetzt_lokal, domina_profile.get("kinderfreie_zeiten", []) or []):
        return

    jetzt = datetime.now(timezone.utc)
    letzte = basis._parse_datum(domina_profile.get("blitz_letzte_am", ""))
    if jetzt - letzte < timedelta(days=config.BLITZ_MIN_ABSTAND_TAGE):
        return

    # Serverseitig im quelle-Index zählen statt 50 Voll-Payloads laden (D8/N5).
    if await qdrant.count_tasks_by_status(["offen"], quelle="blitz") > 0:
        return

    if random.random() >= config.BLITZ_CHANCE:
        return

    from bot.handlers import blitz  # lazy: zirkulären Import vermeiden
    await blitz.sende_blitz(bot)


# Gegenseitige Kollisions-Sperre der beiden Impulse (Live-Befund 06.09.:
# spiel_impuls_job und coach_impuls_job werden beim Start zeitgleich als
# 30-Min-Interval-Jobs registriert, ticken also in derselben Sekunde und
# feuerten beide – zwei Quizze gleichzeitig in beiden Kanälen). Persistente
# Anker (spiel_/coach_impuls_letzte_am) decken Tick-übergreifend ab, der
# In-Prozess-Claim das Rennen innerhalb EINES Ticks (Anker fällt erst nach
# dem Send, dazwischen liegt die LLM-Generierung). Dauer: IMPULS_KOLLISION_MINUTEN.
_IMPULS_KOLLISION = timedelta(minutes=config.IMPULS_KOLLISION_MINUTEN)

_impuls_claim: datetime | None = None

_WUNSCH_WETTE_VERSUCHE = 3


def _wett_fenster_offen(jetzt_lokal: datetime) -> bool:
    """True, wenn WETT_IMPULS_FENSTER gesetzt ist und `jetzt_lokal` darin liegt –
    dann dürfen Wett-Impulse auch außerhalb der kinderfreien Zeiten raus."""
    return bool(config.WETT_IMPULS_FENSTER) and zeiten.ist_im_fenster(
        jetzt_lokal, [config.WETT_IMPULS_FENSTER])


def _impuls_sperrgrund(anderer_anker: str) -> str | None:
    """Warum der Slot belegt ist: "claim" (der andere Impuls hat ihn in diesem
    Tick reserviert und sendet gerade), "anker" (der andere Impuls ging vor
    weniger als _IMPULS_KOLLISION wirklich raus) oder None (frei)."""
    jetzt = datetime.now(timezone.utc)
    if _impuls_claim and jetzt - _impuls_claim < _IMPULS_KOLLISION:
        return "claim"
    if jetzt - basis._parse_datum(anderer_anker or "") < _IMPULS_KOLLISION:
        return "anker"
    return None


def _impuls_slot_frei(anderer_anker: str) -> bool:
    """True, wenn weder der persistente Sende-Anker des ANDEREN Impulses noch
    ein frischer In-Prozess-Claim jünger als _IMPULS_KOLLISION ist."""
    return _impuls_sperrgrund(anderer_anker) is None


def _log_sperre(wer: str, anderer: str, grund: str) -> None:
    """Die beiden Sperrgründe getrennt loggen – "war vor < 45 min dran" stand
    vorher auch dann im Log, wenn der andere Impuls gar nichts gesendet hatte."""
    if grund == "claim":
        logger.info("%s ausgesetzt – %s hat den Slot in diesem Tick reserviert.", wer, anderer)
    else:
        logger.info("%s ausgesetzt – Kollisions-Sperre (%s ging vor < %s raus).",
                    wer, anderer, _IMPULS_KOLLISION)


@basis._job_guard
async def spiel_impuls_job(bot: Bot) -> None:
    """Spiel-Impuls 🎲 (Env-Gate SPIEL_IMPULS): die Herrin startet von sich aus
    ein Spiel Richtung Sklave – Quiz-Frage oder Wett-Angebot. Bewusst OHNE
    Task-Erteilung (die bleibt Domina-Sache bzw. Blitz-Opt-in), darum auch ohne
    Einzel-Freigabe. Fenster: SPIEL_IMPULS_FENSTER ∩ kinderfreie Zeiten;
    Throttle über SPIEL_IMPULS_MIN_ABSTAND_TAGE (Anker im Sklaven-Profil).
    Gegenseitige Kollisions-Sperre mit dem Coach-Impuls (s. _impuls_slot_frei)."""
    global _impuls_claim
    if not config.SPIEL_IMPULS:
        return
    # Fenster VOR dem Belegt-Check (wie beim Coach-Impuls): kein Log-Rauschen nachts.
    jetzt_lokal = datetime.now(ZoneInfo(config.TIMEZONE))
    if not zeiten.ist_im_fenster(jetzt_lokal, [config.SPIEL_IMPULS_FENSTER]):
        return
    if basis._flow_aktiv(paare.sub_chat_id(), "Spiel-Impuls"):
        return
    domina_profile = await qdrant.get_user_profile("domina") or {}
    # kinderfreie_zeiten wie beim Blitz respektieren – ein Quiz soll nicht
    # mitten in die Familienzeit platzen. Das Wett-Angebot darf im
    # WETT_IMPULS_FENSTER auch außerhalb raus (Owner-Entscheid 12.11.2026).
    kinderfrei = zeiten.ist_im_fenster(jetzt_lokal, domina_profile.get("kinderfreie_zeiten", []) or [])
    if not kinderfrei and not _wett_fenster_offen(jetzt_lokal):
        return

    sklave_profile = await qdrant.get_user_profile("sklave") or {}
    jetzt = datetime.now(timezone.utc)
    letzte = basis._parse_datum(sklave_profile.get("spiel_impuls_letzte_am", ""))
    if jetzt - letzte < timedelta(days=config.SPIEL_IMPULS_MIN_ABSTAND_TAGE):
        return
    grund = _impuls_sperrgrund(domina_profile.get("coach_impuls_letzte_am", ""))
    if grund:
        _log_sperre("Spiel-Impuls", "Coach-Impuls", grund)
        return
    # Der Wurf wird geloggt (Owner-Wunsch 03.09.2026): Er fällt nur, wenn alle
    # Gates offen sind — max. ein paar Zeilen am Abend, und man sieht beim
    # Beobachten, DASS gewürfelt wurde, nicht nur die seltenen Treffer.
    wurf = random.random()
    if wurf >= config.SPIEL_IMPULS_CHANCE:
        logger.info("Spiel-Impuls: Würfel dagegen (%.2f ≥ %.2f)",
                    wurf, config.SPIEL_IMPULS_CHANCE)
        return
    # Pool VOR der Reservierung bestimmen (Live-Befund 12.11.2026): vorher
    # reservierte der Job den Slot, stellte dann fest, dass nichts anzubieten
    # ist, und hatte den zeitgleich tickenden Coach-Impuls bis dahin schon
    # ausgesperrt – der Tick ging für beide verloren.
    from bot.handlers import quiz, wette  # lazy: zirkulären Import vermeiden
    spiele = []
    if config.SPIEL_IMPULS_QUIZ and kinderfrei:
        spiele.append(("quiz", quiz.sende_spontane_frage))
    lage = await wette.angebots_lage(sklave_profile)
    if lage == "ok":
        spiele.append(("wette", wette.sende_spontanes_angebot))
    else:
        # Sichtbar machen statt still schrumpfen (Owner-Frage 06.09.: "Wetten
        # kamen nie") – ohne offene reguläre Aufgabe gibt es nichts zu wetten.
        logger.info("Spiel-Impuls: Wette nicht anbietbar (%s).", lage)
    if not spiele:
        logger.info("Spiel-Impuls: nichts im Pool (Quiz aus, Wette nicht anbietbar) – ausgesetzt.")
        return
    # In-Prozess-Claim: Prüfen und Setzen SYNCHRON hintereinander (kein await
    # dazwischen). Beide Impuls-Jobs ticken als gleich registrierte 30-Min-Jobs
    # in derselben Sekunde – die persistenten Anker fallen erst nach dem Send,
    # die LLM-Generierung läuft dazwischen (Live-Befund 06.09.: beide Kanäle
    # bekamen zeitgleich ein Quiz).
    if _impuls_claim and datetime.now(timezone.utc) - _impuls_claim < _IMPULS_KOLLISION:
        logger.info("Spiel-Impuls ausgesetzt – Coach-Impuls hat den Slot in diesem Tick reserviert.")
        return
    mein_claim = datetime.now(timezone.utc)
    _impuls_claim = mein_claim
    random.shuffle(spiele)
    gesendet = None
    for name, senden in spiele:
        if await senden(bot):
            gesendet = name
            break
    if not gesendet:
        if _impuls_claim is mein_claim:
            _impuls_claim = None  # Slot wieder freigeben – es ging nichts raus
        return
    # Throttle-Anker erst nach erfolgreichem Versand (Muster blitz_letzte_am).
    await qdrant.patch_profile_fields(
        "sklave", {"spiel_impuls_letzte_am": datetime.now(timezone.utc).isoformat()})
    logger.info("Spiel-Impuls gesendet: %s", gesendet)


async def _quiz_verfall_nachreichen(bot: Bot) -> None:
    """Verfallenes Fachwissen-Quiz auflösen statt still schlucken: clear_if_stale
    parkt die Auflösung unter coach_quiz_verfallen (state.py), der nächste
    30-Min-Tick liefert sie nach und legt das Thema als status=offen in die
    knowledge_base (darf wie FALSCH nach >= 7 Tagen wiederkommen). Best-effort:
    bei Fehler bleibt der Park-Key liegen -> Retry beim nächsten Tick."""
    chat_id = paare.dom_chat_id()
    s = state.get(chat_id)
    daten = s.get("coach_quiz_verfallen")
    if not daten:
        return
    if state.is_paused() or state.get_mode(chat_id) not in ("chat", None):
        return  # nicht in einen aktiven Flow platzen – nächster Tick
    try:
        await telegram_helper.send_domina(
            bot,
            t("COACH_QUIZ_VERFALLEN",
              frage=telegram_helper.md_einbett_sicher(daten.get("frage", "")),
              muster=telegram_helper.md_einbett_sicher(daten.get("muster", "")),
              aufloesung=telegram_helper.md_einbett_sicher(daten.get("aufloesung", ""))),
            parse_mode="Markdown")
        s.pop("coach_quiz_verfallen", None)
        await qdrant.save_quiz_wissen("domina", {
            "thema": daten.get("thema", ""), "frage": daten.get("frage", ""),
            "inhalt": daten.get("aufloesung") or daten.get("muster", ""),
            "urteil": "UNBEANTWORTET", "status": "offen",
        })
        logger.info("Verfallenes Coach-Quiz nachgereicht: %s", daten.get("thema", "?"))
    except Exception:
        logger.exception("Quiz-Verfall-Nachreichen fehlgeschlagen – Retry nächster Tick")


def _impuls_reihenfolge(kandidaten: list, letzter_typ: str) -> list:
    """Kandidaten so drehen, dass der Typ NACH dem zuletzt gesendeten vorn steht
    (strikte Abwechslung Quiz/Wette); ohne bekannten letzten Typ zufällig."""
    namen = [n for n, _ in kandidaten]
    if letzter_typ not in namen:
        k = list(kandidaten)
        random.shuffle(k)
        return k
    i = namen.index(letzter_typ)
    return list(kandidaten[i + 1:]) + list(kandidaten[:i + 1])


@basis._job_guard
async def coach_impuls_job(bot: Bot) -> None:
    """Coach-Impuls ☕ (Env-Gate COACH_IMPULS): der Coach meldet sich von sich
    aus bei der Domina – spontane Quiz-Frage (Lern- oder Sklaven-Wissen, per
    COACH_IMPULS_QUIZ abschaltbar) oder eine fertige Wett-Idee zum Weitergeben.
    Spiegel des Spiel-Impulses: Fenster ∩ kinderfreie Zeiten, Throttle-Anker
    coach_impuls_letzte_am im Domina-Profil, Würfel-Log, Kollisions-Sperre
    (s. _impuls_slot_frei).

    Wunsch-Wettvorschlag: steht im Domina-Profil coach_wette_wunsch =
    {"datum": "YYYY-MM-DD", "schwerpunkt": "…"} für HEUTE, geht beim nächsten
    Tick im Fenster eine Wett-Idee mit diesem Schwerpunkt raus – ohne Würfel,
    Abstand und Kollisions-Sperre. Ein Wunsch von gestern verfällt still."""
    global _impuls_claim
    # Vor allen Gates: liegengebliebene Quiz-Auflösung nachreichen – auch ein
    # manuelles /quiz kann verfallen, unabhängig vom COACH_IMPULS-Schalter.
    await _quiz_verfall_nachreichen(bot)

    if not config.COACH_IMPULS:
        return
    # Fenster VOR dem Belegt-Check: nachts ist ohnehin Ruhe – sonst füllt jeder
    # Tick außerhalb des Fensters das Log mit „übersprungen – Chat in Mode …".
    jetzt_lokal = datetime.now(ZoneInfo(config.TIMEZONE))
    if not zeiten.ist_im_fenster(jetzt_lokal, [config.COACH_IMPULS_FENSTER]):
        return
    if basis._flow_aktiv(paare.dom_chat_id(), "Coach-Impuls"):
        return
    domina_profile = await qdrant.get_user_profile("domina") or {}
    # Die Wett-Idee darf im WETT_IMPULS_FENSTER auch außerhalb der kinderfreien
    # Zeiten raus, das Coach-Quiz nicht (s. Spiel-Impuls).
    kinderfrei = zeiten.ist_im_fenster(jetzt_lokal, domina_profile.get("kinderfreie_zeiten", []) or [])
    if not kinderfrei and not _wett_fenster_offen(jetzt_lokal):
        return

    from bot.handlers import coach_quiz  # lazy: zirkulären Import vermeiden
    wunsch = domina_profile.get("coach_wette_wunsch") or {}
    heute = jetzt_lokal.date().isoformat()
    if wunsch and str(wunsch.get("datum", "")) < heute:
        await qdrant.patch_profile_fields("domina", {"coach_wette_wunsch": None})
        logger.info("Wunsch-Wettvorschlag vom %s verfallen – nicht zugestellt.", wunsch.get("datum"))
    elif wunsch and str(wunsch.get("datum", "")) == heute:
        mein_claim = datetime.now(timezone.utc)
        _impuls_claim = mein_claim  # Spiel-Impuls im selben Tick fernhalten
        schwerpunkt = str(wunsch.get("schwerpunkt") or "")
        # Mehrere Anläufe pro Tick: ein ausdrücklicher Wunsch soll nicht an einem
        # einzelnen verworfenen Entwurf eine halbe Stunde hängen (Live 16.09. 20:34).
        gesendet = False
        for versuch in range(1, _WUNSCH_WETTE_VERSUCHE + 1):
            if await coach_quiz.sende_wett_idee(bot, schwerpunkt=schwerpunkt):
                gesendet = True
                break
            logger.info("Wunsch-Wettvorschlag: Anlauf %d/%d ohne Versand.", versuch, _WUNSCH_WETTE_VERSUCHE)
        if not gesendet:
            if _impuls_claim is mein_claim:
                _impuls_claim = None
            logger.info("Wunsch-Wettvorschlag nicht gesendet – nächster Tick.")
            return
        await qdrant.patch_profile_fields(
            "domina", {"coach_impuls_letzte_am": datetime.now(timezone.utc).isoformat(),
                       "coach_impuls_letzter_typ": "wett_idee",
                       "coach_wette_wunsch": None})
        logger.info("Wunsch-Wettvorschlag gesendet (Schwerpunkt: %s).", schwerpunkt or "–")
        return

    jetzt = datetime.now(timezone.utc)
    letzte = basis._parse_datum(domina_profile.get("coach_impuls_letzte_am", ""))
    if jetzt - letzte < timedelta(days=config.COACH_IMPULS_MIN_ABSTAND_TAGE):
        return
    sklave_profile = await qdrant.get_user_profile("sklave") or {}
    grund = _impuls_sperrgrund(sklave_profile.get("spiel_impuls_letzte_am", ""))
    if grund:
        _log_sperre("Coach-Impuls", "Spiel-Impuls", grund)
        return
    wurf = random.random()
    if wurf >= config.COACH_IMPULS_CHANCE:
        logger.info("Coach-Impuls: Würfel dagegen (%.2f ≥ %.2f)",
                    wurf, config.COACH_IMPULS_CHANCE)
        return
    # In-Prozess-Claim synchron nach dem Würfel (s. Kommentar am Spiel-Impuls).
    if _impuls_claim and datetime.now(timezone.utc) - _impuls_claim < _IMPULS_KOLLISION:
        logger.info("Coach-Impuls ausgesetzt – Spiel-Impuls hat den Slot in diesem Tick reserviert.")
        return
    mein_claim = datetime.now(timezone.utc)
    _impuls_claim = mein_claim

    kandidaten = [("wett_idee", coach_quiz.sende_wett_idee)]
    if config.COACH_IMPULS_QUIZ and kinderfrei:
        kandidaten.insert(0, ("coach_quiz", coach_quiz.sende_spontane_frage))
    # Strikt abwechseln statt würfeln (Live 15.09.: Quiz, Quiz, dann erst Wette –
    # die Dom-Seite hatte „noch nie einen Wettvorschlag"). Fallback statt stillem
    # Ausfall bleibt (Owner-Frage 06.09.): schlägt der Zweig fehl (z.B. leere
    # LLM-Antwort), kommt der andere dran.
    kandidaten = _impuls_reihenfolge(kandidaten, domina_profile.get("coach_impuls_letzter_typ", ""))
    gesendet = None
    for name, senden in kandidaten:
        if await senden(bot):
            gesendet = name
            break
    if not gesendet:
        if _impuls_claim is mein_claim:
            _impuls_claim = None
        return
    await qdrant.patch_profile_fields(
        "domina", {"coach_impuls_letzte_am": datetime.now(timezone.utc).isoformat(),
                   "coach_impuls_letzter_typ": gesendet})
    logger.info("Coach-Impuls gesendet: %s", gesendet)


@basis._job_guard
async def blitz_ablauf_job(bot: Bot) -> None:
    """Markiert Blitzaufgaben mit abgelaufenem Countdown als verpasst –
    restart-sicher (Deadline liegt Qdrant-persistiert am Task).

    Läuft alle 5 Min (288×/Tag/Paar) – darum billiger count als Vorprüfung
    und der quelle-Filter serverseitig im Index statt in Python (D8/N5)."""
    if await qdrant.count_tasks_by_status(["offen"], quelle="blitz") == 0:
        return
    offene = await qdrant.get_tasks_by_status(["offen"], limit=50, quelle="blitz")
    jetzt_iso = datetime.now(timezone.utc).isoformat()
    from bot.handlers import blitz  # lazy
    for task in offene:
        deadline = task.get("blitz_deadline", "")
        if deadline and jetzt_iso > deadline:
            try:
                await blitz.markiere_verpasst(bot, task)
            except Exception:
                logger.exception("Blitz-Ablauf-Markierung fehlgeschlagen (point_id=%s)",
                                 task.get("qdrant_point_id"))
