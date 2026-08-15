"""
In-Memory State für aktive Konversationen.
Wird beim Start aus Qdrant (Modi/Tasks) und aus STATE_FILE (message_history,
Pause-Flag) wiederhergestellt.
"""
import asyncio
import json
import logging
import os
import threading
import time

from bot import config

logger = logging.getLogger(__name__)
_state: dict = {}

# Stale-Mode-Guard: ein mehrstufiger UI-Flow (z.B. profil_wahl), den der Nutzer
# abbricht/liegen lässt, darf nicht ewig hängen bleiben – sonst blockiert er
# Followup-/Stimmungs-Jobs (siehe eval-6). Nach dieser Zeit ohne Abschluss wird
# der Mode automatisch auf "chat" zurückgesetzt.
STALE_MODE_SECONDS = int(os.getenv("STALE_MODE_SECONDS", "1800"))  # 30 Min
# Die Stimmungsfrage wartet nur passiv auf eine Antwort und blockiert keinen
# UI-Flow – sie darf länger offen bleiben, damit eine späte Antwort noch als
# Stimmung erfasst wird (Frage 16:00 → Antwort soll bis zum Tiny-Task-Vorschlag
# 18:00 einfließen). Log-Befund 05.07.: Antwort nach 73 min wurde als normaler
# Chat geroutet und ging als Stimmungs-Datum verloren.
STALE_STIMMUNG_SECONDS = int(os.getenv("STALE_STIMMUNG_SECONDS", "7200"))  # 2 Std
# Die 21:30-Feedback-Frage wird real oft erst am Folgemorgen beantwortet (Log
# 06.07.: Button 06:56). Buttons überleben den Stale-Reset, getippter FREITEXT
# ging nach 30 min als normaler Coach-Chat verloren. 12h decken die Nacht ab;
# fremde Anliegen im Fenster fängt die Klassifikation in tiny_task_feedback
# (_ist_ablehnungsgrund → ANDERES wird als normaler Chat geroutet).
STALE_TINYFB_SECONDS = int(os.getenv("STALE_TINYFB_SECONDS", "43200"))  # 12 Std
# reaktion_pending sitzt auf dem DOMINA-Chat und wartet darauf, dass die Herrin
# nach einem "nicht erledigt" eine Reaktion tippt (followup_response._handle_no).
# Der zugehörige Task steht in Qdrant auf "nicht_erledigt", NICHT auf
# "reaktion_pending" – dieser Mode ist also NICHT recovery-gedeckt (kein Pfad
# schreibt je Task-Status "reaktion_pending"). Reagiert die Herrin nie, blockierte
# er früher als _PROTECTED_MODE unbegrenzt ALLE Domina-seitigen Jobs (Log-Befund
# 19.07.: >24h Dauerhänger). Darum bewusst kein Schutz, sondern ein beschränktes
# Fenster: reagiert sie, endet der Mode sofort; vergisst sie es, läuft er ab und
# die Jobs laufen wieder. 3h decken den Abend, ohne den Rest-Tageszyklus zu opfern.
STALE_REAKTION_SECONDS = int(os.getenv("STALE_REAKTION_SECONDS", "10800"))  # 3 Std
# Aktives Rollenspiel ist kein kurzlebiger UI-Flow, sondern ein über Tage
# laufender Chat-Modus: der 30-Minuten-Default killte Mode + szenario_*-Keys
# mitten im Szenario (Regression D9/M1 des D6-"Flow-Leichen"-Fixes). Das Fenster
# zählt ab der LETZTEN Rollenspiel-Nachricht (touch_mode in main.py) – ein
# eingeschlafenes Spiel verfällt also 3 Tage nach der letzten Aktivität.
STALE_ROLLENSPIEL_SECONDS = int(os.getenv("STALE_ROLLENSPIEL_SECONDS", str(3 * 86400)))  # 3 Tage
_MODE_MAX_AGE = {"stimmung": STALE_STIMMUNG_SECONDS,
                 "tiny_task_feedback": STALE_TINYFB_SECONDS,
                 "reaktion_pending": STALE_REAKTION_SECONDS,
                 "rollenspiel_aktiv": STALE_ROLLENSPIEL_SECONDS}
# Wartezustände, die durch Qdrant/Recovery gedeckt sind – nie auto-verfallen lassen.
# "pausiert" MUSS hier stehen: sonst setzt clear_if_stale die Safeword-Pause nach
# STALE_MODE_SECONDS auf "chat" zurück und der Bot reagiert trotz Safeword wieder.
# reaktion_pending gehört bewusst NICHT hierher (siehe Kommentar oben).
_PROTECTED_MODES = {"followup", "gefuehl", "pausiert"}

_DEFAULT_STATE = {
    "mode": "chat",        # chat | onboarding | followup | gefuehl | reaktion_pending
                           # | pausiert | aufgabe_bestaetigung | serie_wahl
                           # | training_antwort | inspiration_* | profil_* | vorlage_*
    "mode_since": None,    # Zeitstempel (time.time()) seit dem der Nicht-chat-Mode aktiv ist
    "followup_task_id": None,
    "reaktion_fuer_task_id": None,
    "message_history": [],
    # Aufgabe Bestätigung
    "pending_task_text": None,
    "pending_task_level": None,
    "pending_task_profile": None,
    "pending_task_kategorie": None,
    "pending_task_termin": None,   # ISO-Datum, wenn im Wortlaut ein Termin erkannt wurde
    # Serie
    "serie_task_text": None,
    "serie_task_level": None,
    "serie_task_profile": None,
    "serie_task_kategorie": None,
    # Inspiration Flow
    "inspiration_vorschlaege": [],
    "inspiration_point_ids": [],
    "inspiration_iteration": 1,
    "inspiration_feedback": "",
    # Training
    "training_typ": None,
    "training_uebung": None,
    # Aufgaben Filter
    "aufgaben_kategorie_filter": None,
}

MAX_HISTORY = 30


def get(chat_id: str) -> dict:
    if chat_id not in _state:
        _state[chat_id] = dict(_DEFAULT_STATE)
        # Frische mutable Objekte, damit die _DEFAULT_STATE-Defaults nicht geteilt werden.
        _state[chat_id]["message_history"] = []
        _state[chat_id]["inspiration_vorschlaege"] = []
        _state[chat_id]["inspiration_point_ids"] = []
    return _state[chat_id]


def get_mode(chat_id: str) -> str:
    return get(chat_id)["mode"]


def set_mode(chat_id: str, mode: str) -> None:
    s = get(chat_id)
    s["mode"] = mode
    s["mode_since"] = None if mode == "chat" else time.time()


def touch_mode(chat_id: str) -> None:
    """Frischt mode_since eines aktiven Nicht-Chat-Modes auf. Für langlaufende
    Modi (rollenspiel_aktiv): das Stale-Fenster zählt ab der letzten gerouteten
    Nachricht, nicht ab dem Start (D9/M1)."""
    s = get(chat_id)
    if s.get("mode", "chat") != "chat":
        s["mode_since"] = time.time()


def clear_if_stale(chat_id: str, max_age: int | None = None) -> bool:
    """Setzt einen zu lange aktiven UI-Flow-Mode auf 'chat' zurück.

    Schützt Wartezustände (_PROTECTED_MODES), die durch Qdrant/Recovery gedeckt
    sind. Gibt True zurück, wenn zurückgesetzt wurde. Vor Mode-Routing/Jobs aufrufen.
    max_age=None → Default je Mode (_MODE_MAX_AGE, sonst STALE_MODE_SECONDS).
    """
    s = get(chat_id)
    mode = s.get("mode", "chat")
    if mode == "chat" or mode in _PROTECTED_MODES:
        return False
    if max_age is None:
        max_age = _MODE_MAX_AGE.get(mode, STALE_MODE_SECONDS)
    since = s.get("mode_since")
    if since is None or (time.time() - since) <= max_age:
        return False
    logger.info("Stale-Mode '%s' (%.0f min inaktiv) automatisch auf 'chat' zurückgesetzt für %s",
                mode, (time.time() - since) / 60, chat_id)
    s["mode"] = "chat"
    s["mode_since"] = None
    # Flow-Leichen miträumen (Review D6): ein verfallener reaktion_pending ließ
    # sonst reaktion_fuer_task_id/strafe_id liegen, die die nächste Eingabe
    # fehlrouten könnten. clear_flow_keys ist forward-referenziert (Modul-Ebene).
    clear_flow_keys(chat_id)
    return True


# Alle Flow-State-Keys, die /abbrechen und das Safeword-Resume aufräumen müssen
# (zentral, damit neue Flows nur EINE Liste pflegen; Review D6: _resume ließ
# Flow-Leichen liegen, die die nächste Eingabe fehlrouten konnten).
FLOW_STATE_KEYS = (
    # Profil / Einstellungen
    "profil_edit_feld", "profil_edit_rolle", "einstellungen_feld",
    # Aufgabe Bestätigung / Serie
    "pending_task_text", "pending_task_level", "pending_task_profile", "pending_task_kategorie",
    "pending_task_termin",
    "serie_task_text", "serie_task_level", "serie_task_profile", "serie_task_kategorie",
    # Inspiration / Vorlagen / Löschen / Training / Quiz
    "inspiration_vorschlaege", "inspiration_point_ids", "inspiration_iteration", "inspiration_feedback",
    "neue_vorlage_name", "vorlagen_liste",
    "loeschen_tasks", "loeschen_bestaetigung_id", "loeschen_serie_stopp",
    "training_typ", "training_uebung",
    "quiz_frage", "quiz_musterantwort",
    # Rollenspiel / Wunsch / Kommentar / Geheimnis
    "szenario_name", "szenario_ton", "szenario_vokabular", "szenario_seit",
    "rollenspiel_intensitaet", "pending_szenario", "pending_szenario_custom",
    "wunsch_eingabe", "wunsch_id",
    "aufgabe_kommentar", "kommentar_task_id",
    "geheimnis_text_inhalt",
    # Kette / Reaktion / Bewertung / Skill / Feedback / Privileg
    "kette_erste_text", "kette_level", "kette_profile", "kette_kategorie", "kette_aufgaben_liste",
    "strafe_id", "reaktion_fuer_task_id",
    "bewertung_task_id",
    "skill_edit_kategorie",
    "tiny_task_feedback_id",
    "privileg_aktiv_id",
    # Würfel / Roulette / Wette / Lücke (inkl. Nonces – Review D6: wuerfel_nonce blieb liegen)
    "wuerfel_kategorie", "wuerfel_aufgabe", "wuerfel_nonce",
    "roulette_nonce", "roulette_strafe", "roulette_stufe",
    "wette_nonce",
    "luecke_aufgabe", "luecke_kategorie", "luecke_level", "luecke_nonce",
)


def clear_flow_keys(chat_id: str) -> None:
    """Räumt alle Flow-State-Keys eines Chats (Mode bleibt unangetastet)."""
    s = get(chat_id)
    for key in FLOW_STATE_KEYS:
        s.pop(key, None)


def set_followup_task(chat_id: str, task_id: str) -> bool:
    """Setzt followup_task_id + Mode. Gibt False zurück, wenn ein aktiver Mode
    das verhindert hat – Aufrufer dürfen den DB-Status dann NICHT auf 'gefragt'
    setzen (sonst Status-Drift: Task 'gefragt', Frage nie gestellt)."""
    s = get(chat_id)
    current_mode = s.get("mode", "chat")
    # Race condition fix: don't overwrite active modes. stimmung/quiz_antwort
    # (D9/M2): ein Ketten-Approve im Antwortfenster überschrieb sonst den
    # Sklaven-Mode und die Stimmungs-/Quiz-Antwort lief in die Followup-Schleife.
    blocked_modes = {"gefuehl", "reaktion_pending", "aufgabe_bestaetigung", "serie_wahl",
                     "aufgabe_bewertung", "followup", "stimmung", "quiz_antwort"}
    if current_mode in blocked_modes:
        logger.warning("Skipping set_followup_task – user in mode %s", current_mode)
        return False
    s["followup_task_id"] = task_id
    s["mode"] = "followup"
    return True


def set_gefuehl_pending(chat_id: str, task_id: str) -> None:
    s = get(chat_id)
    current_mode = s.get("mode", "chat")
    # 'followup' ist hier KEIN Konflikt, sondern der reguläre Vorgänger: nach
    # "Erledigt" geht es vom Followup direkt in die Gefühl-Abfrage. Nur echte
    # Domina-seitige Parallel-Flows blockieren.
    blocked_modes = {"reaktion_pending", "aufgabe_bestaetigung", "serie_wahl", "aufgabe_bewertung"}
    if current_mode in blocked_modes:
        logger.warning("Skipping set_gefuehl_pending – user in mode %s", current_mode)
        return
    s["followup_task_id"] = task_id
    s["mode"] = "gefuehl"


def set_reaktion_pending(chat_id: str, task_id: str) -> None:
    s = get(chat_id)
    # Daten immer setzen (recovery-fähig), aber einen aktiven Domina-Flow nicht
    # kapern: den Mode nur wechseln, wenn sie gerade frei ist.
    s["reaktion_fuer_task_id"] = task_id
    current_mode = s.get("mode", "chat")
    if current_mode in ("chat", None):
        s["mode"] = "reaktion_pending"
        s["mode_since"] = time.time()
    else:
        logger.warning("set_reaktion_pending – Domina in Mode %s, Mode nicht gewechselt", current_mode)


def _pause_paar_id(paar_id: str | None) -> str:
    """None = Paar des aktiven Kontexts (Updates: TypeHandler, Jobs: _pro_paar).
    Lazy-Import, um die Import-Reihenfolge beim Start nicht zu verhaken."""
    if paar_id is not None:
        return str(paar_id)
    from bot.services import paare
    return paare.aktueller_kontext()


def zaehle_tagesnachricht(paar_id: str) -> int:
    """Tages-Zähler der Chat-Nachrichten eines Paares (Missbrauchs-/Kosten-
    Bremse, config.LLM_BUDGET_PRO_TAG). In-Memory mit Datums-Rollover –
    ein Neustart resettet den Zähler; das ist bewusst simpel, die Bremse
    ist Schutz vor Dauerfeuer, keine Abrechnung."""
    import datetime
    from zoneinfo import ZoneInfo
    from bot import config as _config  # lokal: state wird früh importiert
    # Rollover in Bot-Zeitzone statt System-TZ (D9/N21) – nur Budget-Bremse.
    heute = datetime.datetime.now(ZoneInfo(_config.TIMEZONE)).date().isoformat()
    zaehler = _state.setdefault("__tagesnachrichten__", {})
    eintrag = zaehler.get(str(paar_id))
    if not eintrag or eintrag.get("tag") != heute:
        eintrag = {"tag": heute, "anzahl": 0}
        zaehler[str(paar_id)] = eintrag
    eintrag["anzahl"] += 1
    return eintrag["anzahl"]


def vergiss_chat(chat_id: str) -> None:
    """Kompletten In-Memory-State (inkl. message_history) eines Chats verwerfen
    und die Persistenz nachziehen – Teil der Paar-Löschung (handlers/admin.py)."""
    with _persist_lock:
        _state.pop(str(chat_id), None)
    _persist(immediate=True)


def is_paused(paar_id: str | None = None) -> bool:
    """Safeword-Pause DES PAARES (Multiuser Schritt 6; vorher global –
    das Safeword eines Paares hätte alle Paare gestoppt)."""
    return _pause_paar_id(paar_id) in _state.get("__paused_paare__", set())


def set_paused(value: bool, paar_id: str | None = None) -> None:
    pausierte: set = _state.setdefault("__paused_paare__", set())
    pid = _pause_paar_id(paar_id)
    if value:
        pausierte.add(pid)
    else:
        pausierte.discard(pid)
    _persist(immediate=True)  # Safeword/Pause ist safety-relevant – sofort schreiben


# ---------------------------------------------------------------------------
# Message History
# ---------------------------------------------------------------------------

def add_message(chat_id: str, role: str, content: str) -> None:
    s = get(chat_id)
    # Mutation unter dem Persist-Lock: der Timer-Thread serialisiert die Live-Listen
    # unter demselben Lock (_persist_now) – sonst kann json.dump bei gleichzeitigem
    # append fehlschlagen und der Persist-Lauf fällt aus. _persist() erst NACH dem
    # Block (Lock ist nicht reentrant).
    with _persist_lock:
        history = s.setdefault("message_history", [])
        history.append({"role": role, "content": content})
        if len(history) > MAX_HISTORY:
            s["message_history"] = history[-MAX_HISTORY:]
    _persist()


def remove_last_message(chat_id: str, role: str | None = None) -> None:
    """Entfernt die letzte Nachricht aus der History (z.B. bei LLM-Ausfall, damit
    die User-Nachricht nicht unbeantwortet stehen bleibt). Wenn `role` gesetzt ist,
    wird nur gepoppt, falls die letzte Nachricht diese Rolle hat."""
    with _persist_lock:
        history = get(chat_id).get("message_history", [])
        geloescht = bool(history and (role is None or history[-1].get("role") == role))
        if geloescht:
            history.pop()
    if geloescht:
        _persist()


def get_history(chat_id: str) -> list[dict]:
    return get(chat_id).get("message_history", [])


def clear_history(chat_id: str) -> None:
    get(chat_id)["message_history"] = []
    _persist()


# ---------------------------------------------------------------------------
# Persistenz (message_history + Pause-Flag) – überlebt Neustart via STATE_FILE.
# Modi/Tasks werden separat aus Qdrant via restore_state() wiederhergestellt.
# ---------------------------------------------------------------------------

# Debounce: add_message feuert bei jeder Nachricht – Schreibvorgänge werden auf
# max. einen pro _PERSIST_DELAY gebündelt und im Thread-Pool ausgeführt, damit der
# Event-Loop nicht bei jeder Nachricht synchron Datei-I/O macht. Safety-relevante
# Änderungen (Pause-Flag) gehen mit immediate=True sofort und synchron raus.
_PERSIST_DELAY = float(os.getenv("STATE_PERSIST_DELAY", "2.0"))
_persist_timer = None
_persist_lock = threading.Lock()


def _persist(immediate: bool = False) -> None:
    global _persist_timer
    if immediate:
        if _persist_timer is not None:
            _persist_timer.cancel()
            _persist_timer = None
        _persist_now()
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _persist_now()  # kein Event-Loop (Tests/Sync-Kontext) – direkt schreiben
        return
    if _persist_timer is not None:
        return  # Schreibvorgang bereits geplant – nimmt den dann aktuellen Stand mit

    def _fire():
        global _persist_timer
        _persist_timer = None
        loop.run_in_executor(None, _persist_now)

    _persist_timer = loop.call_later(_PERSIST_DELAY, _fire)


def _persist_now() -> None:
    """Schreibt message_history aller Chats + Pause-Flag atomar nach STATE_FILE.
    Defensiv: ein Fehler hier darf das Message-Handling nie unterbrechen."""
    try:
        with _persist_lock:
            pausierte = sorted(_state.get("__paused_paare__", set()))
            daten = {
                # Legacy-Feld fürs Env-Paar (Abwärts-/Downgrade-Kompatibilität)
                "paused": "1" in pausierte,
                "paused_paare": pausierte,
                "histories": {
                    cid: s.get("message_history", [])
                    for cid, s in _state.items()
                    if isinstance(s, dict) and s.get("message_history")
                },
            }
            pfad = config.STATE_FILE
            os.makedirs(os.path.dirname(pfad) or ".", exist_ok=True)
            tmp = pfad + ".tmp"
            # 0600 ab Anlage (D9/S2): die History ist Klartext-Intimchat –
            # der umask-Default (0644, world-readable) wäre inkonsistent zur
            # 0600-Policy von bot.log/Backups. os.replace übernimmt die Rechte
            # der tmp-Datei; das chmod danach heilt zusätzlich einen 0644-Bestand.
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(daten, f, ensure_ascii=False)
            os.replace(tmp, pfad)
            try:
                os.chmod(pfad, 0o600)
            except OSError:
                pass
    except Exception as e:
        logger.warning("State-Persistenz fehlgeschlagen: %s", e)


def load_persisted() -> None:
    """Lädt message_history + Pause-Flag aus STATE_FILE (beim Start aufrufen)."""
    try:
        if not os.path.exists(config.STATE_FILE):
            return
        with open(config.STATE_FILE, "r", encoding="utf-8") as f:
            daten = json.load(f)
        if "paused_paare" in daten:
            _state["__paused_paare__"] = set(str(p) for p in daten["paused_paare"] or [])
        elif daten.get("paused"):
            # Alte STATE_FILE (globales Flag) → gehörte dem Env-Paar
            _state["__paused_paare__"] = {"1"}
        for cid, history in (daten.get("histories") or {}).items():
            s = get(cid)
            s["message_history"] = history[-MAX_HISTORY:] if history else []
        logger.info("State geladen: %d Chats, pausierte Paare=%s",
                    len(daten.get("histories") or {}),
                    sorted(_state.get("__paused_paare__", set())) or "keine")
    except Exception as e:
        logger.warning("State-Laden fehlgeschlagen: %s", e)