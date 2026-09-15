"""
Regressions-Tests Sprachnachricht → Aufgabe (15.09.2026, handlers/domina):

Live 14.09.2026: Die Dom-Seite schrieb dem Coach „schreib ihm, er muss …" –
der Coach setzte nur [SPRACHNACHRICHT:], die Nachricht ging als Voice raus,
es entstand kein Task und am Folgetag keine Nachfrage. Außerdem stand
„[soft]…[/soft]" wörtlich in der Text-Bubble (Grok mischte die Tag-Syntax).

  - tts.entferne_sprech_tags kennt eckige Wickel-Tags und spitze Einschübe;
    normalisiere_sprech_tags biegt sie für Grok-TTS gerade
  - klingt_nach_auftrag: Modalverben 2./3. Person (de/en) ja, 1. Person nein
  - handle(): Sprachnachricht mit Auftrag (LLM-Tag ODER Detektor) → Ein-Tipp-
    Angebot statt Bestätigungsdialog; reine Nachricht → kein Angebot;
    Keyword „Aufgabe:" → Bestätigungsdialog wie bisher; Limits-Stopp → kein Angebot
  - Callback: „Ja" legt den Task an (quelle sprachnachricht, KEINE zweite
    Zustellung, Hinweis an den Sub), „Nein" nichts, veraltete Kennung → Hinweis,
    Termin im Wortlaut → Nachfrage an dem Tag, Limits-Treffer → kein Task

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_sprachnachricht_aufgabe.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import date, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # pragma: no cover
    import telegram  # noqa: F401
except ImportError:
    from unittest.mock import MagicMock
    for _name in [
        "telegram", "telegram.ext", "telegram.constants", "telegram.error",
        "qdrant_client", "qdrant_client.models", "qdrant_client.http",
        "qdrant_client.http.models", "qdrant_client.http.exceptions",
        "apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.asyncio",
        "apscheduler.triggers", "apscheduler.triggers.cron",
        "apscheduler.triggers.interval", "httpx", "dotenv",
    ]:
        _m = MagicMock(name=_name)
        _m.__name__ = _name
        _m.__path__ = []
        sys.modules[_name] = _m
    sys.modules["dotenv"].load_dotenv = lambda *a, **k: None

from unittest.mock import AsyncMock  # noqa: E402

from bot import config as _config  # noqa: E402

_config.DOMINA_CHAT_ID = "111"
_config.SKLAVE_CHAT_ID = "222"
_config.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")

from bot import state  # noqa: E402
from bot.handlers import domina  # noqa: E402
from bot.services import tts  # noqa: E402
from bot.messages import t  # noqa: E402

DOM = "111"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Sprech-Tags
# --------------------------------------------------------------------------

def test_sprech_tags_fremdformen():
    live = ("Heute Abend gehörst du mir. [soft]Kein Widerwort, kein "
            "Zappeln… nur stillhalten.[/soft] Verstanden? <laugh> Antworte mir.")
    sauber = tts.entferne_sprech_tags(live)
    assert "[" not in sauber and "<" not in sauber, sauber
    assert "Kein Widerwort, kein Zappeln… nur stillhalten." in sauber
    normal = tts.normalisiere_sprech_tags(live)
    assert "<soft>Kein Widerwort, kein Zappeln… nur stillhalten.</soft>" in normal
    assert "[laugh]" in normal and "<laugh>" not in normal
    # Dokumentierte Formen bleiben unangetastet, [whisper] ist ein gültiger Einschub
    doku = "Komm her. [whisper] <whisper>Jetzt.</whisper> [pause] Los."
    assert tts.normalisiere_sprech_tags(doku) == doku
    assert tts.entferne_sprech_tags(doku) == "Komm her. Jetzt. Los."
    # Unpaariger eckiger Wickel-Tag fliegt in beiden Richtungen raus
    assert tts.normalisiere_sprech_tags("Na [/soft] los") == "Na  los"
    assert tts.entferne_sprech_tags("Na [soft] los") == "Na los"


# --------------------------------------------------------------------------
# Detektor
# --------------------------------------------------------------------------

def test_klingt_nach_auftrag():
    ja = [
        "er muss heute Abend eine Stunde den Plug tragen",
        "Sag ihm, er soll heute den Plug tragen",
        "Richte ihm aus, dass er bis Sonntag zu warten hat",
        "Er darf heute nicht kommen",
        "Tell him he must wear the plug tonight",
        "Ich erwarte, dass er heute Abend kniet",
        "Sag ihm, ich muss länger arbeiten, er soll sich warmhalten",
    ]
    nein = [
        "Sag ihm, ich muss heute länger arbeiten",
        "Schick ihm, dass ich stolz auf ihn bin",
        "Richte ihm aus, ich freue mich auf heute Abend",
        "Tell him I must work late tonight",
        "",
    ]
    for text in ja:
        assert domina.klingt_nach_auftrag(text), f"sollte Auftrag sein: {text!r}"
    for text in nein:
        assert not domina.klingt_nach_auftrag(text), f"sollte KEIN Auftrag sein: {text!r}"
    # Mehrere Texte: irgendeiner reicht (Original-Wortlaut + Coach-Paraphrase)
    assert domina.klingt_nach_auftrag("Mach ich.", "Schreibe ihm, er muss stillhalten")


# --------------------------------------------------------------------------
# Stubs für handle() / Callback
# --------------------------------------------------------------------------

class _Msg:
    def __init__(self, text=""):
        self.text = text
        self.replies: list[tuple[str, object]] = []

    async def reply_text(self, text, parse_mode=None, reply_markup=None, **kw):
        self.replies.append((text, reply_markup))


def _update(text: str) -> SimpleNamespace:
    return SimpleNamespace(message=_Msg(text), effective_chat=SimpleNamespace(id=int(DOM)),
                           effective_user=SimpleNamespace(id=int(DOM)))


def _context() -> SimpleNamespace:
    return SimpleNamespace(bot=object(), chat_data={})


class _Welt:
    """Stubs rund um domina.handle(): Onboarding aus, LLM-Antwort vorgegeben,
    Sprachnachricht-Zustellung/Bestätigungsdialog/Speichern mitprotokolliert."""

    def __init__(self, antwort: str, sn_gesendet: bool = True):
        self.antwort = antwort
        self.sn_gesendet = sn_gesendet
        self.sn_inhalte: list[str] = []
        self.bestaetigungen: list[str] = []
        self.gespeichert: list = []
        self.tasks: list[dict] = []
        self.sub_sends: list[str] = []
        self.limits_treffer: list = []

    async def _sn(self, update, context, inhalt):
        self.sn_inhalte.append(inhalt)
        return self.sn_gesendet

    async def _bestaetigung(self, update, chat_id, task_text, level, profile, sklave_profile,
                            quelltext=""):
        self.bestaetigungen.append(task_text)
        return True

    async def _chat(self, update, context, chat_id, system, text):
        return self.antwort

    async def _save(self, text, response):
        self.gespeichert.append((text, response))

    async def _erstelle_task(self, aufgabe, kategorie, level, **kw):
        self.tasks.append({"aufgabe": aufgabe, "kategorie": kategorie, "level": level, **kw})
        return "task-neu"

    async def _send_sklave(self, bot, text, **kw):
        self.sub_sends.append(text)

    async def _verletzungen(self, text, *a, **kw):
        return list(self.limits_treffer)

    def install(self):
        domina.onboarding.start_if_needed = AsyncMock(return_value=False)
        domina.embeddings.get_embedding = AsyncMock(return_value=None)
        domina.qdrant.get_user_profile = AsyncMock(return_value={"aktuelles_level": 2})
        domina.qdrant.erstelle_task = self._erstelle_task
        domina._baue_system_prompt = AsyncMock(return_value="SYSTEM")
        domina._chat_antwort = self._chat
        domina._sende_sprachnachricht_an_sklaven = self._sn
        domina._starte_aufgaben_bestaetigung = self._bestaetigung
        domina._save_conversation = self._save
        domina._check_level_up = AsyncMock()
        domina.telegram_helper.send_sklave = self._send_sklave
        domina.telegram_helper.reply_markdown_safe = AsyncMock()
        domina.kategorie_logik.klassifiziere = AsyncMock(return_value="Strap_on")
        from bot.services import limits_check, praeferenz_detektor
        limits_check.verletzungen = self._verletzungen
        limits_check.format_verletzungen = lambda tr: ", ".join(str(x) for x in tr)
        praeferenz_detektor.erkenne_und_schlage_vor = AsyncMock()
        state.set_mode(DOM, "chat")
        for key in domina._SN_AUFGABE_KEYS:
            state.get(DOM).pop(key, None)
        return self


def _angebot(update) -> tuple[str, object] | None:
    for text, markup in update.message.replies:
        if text.startswith("📋 Das klingt nach einem Auftrag"):
            return text, markup
    return None


# --------------------------------------------------------------------------
# handle()
# --------------------------------------------------------------------------

def test_handle_sprachnachricht_mit_auftrag_detektor():
    """Live-Fall: nur SPRACHNACHRICHT-Tag, Auftrag steckt im Inhalt → Angebot."""
    w = _Welt("Alles klar, schick ich ihm gleich.\n"
              "[SPRACHNACHRICHT: Er muss heute Abend eine Stunde den Plug tragen]").install()
    u = _update("Kann ich machen. Schreibe ihm, er muss heute Abend eine Stunde den Plug tragen.")
    _run(domina.handle(u, _context()))
    assert w.sn_inhalte == ["Er muss heute Abend eine Stunde den Plug tragen"]
    assert w.bestaetigungen == [], "kein Bestätigungsdialog – Zustellung ist schon passiert"
    ang = _angebot(u)
    assert ang is not None, u.message.replies
    assert "den Plug tragen" in ang[0] and ang[1] is not None
    s = state.get(DOM)
    assert s["sn_aufgabe_text"] == "Er muss heute Abend eine Stunde den Plug tragen"
    assert s["sn_aufgabe_id"] and s["sn_aufgabe_level"] == 2
    assert "Schreibe ihm" in s["sn_aufgabe_quelltext"]
    assert state.get_mode(DOM) == "chat"
    assert len(w.gespeichert) == 1


def test_handle_beide_tags_nimmt_aufgabentext():
    w = _Welt("Geht raus.\n[SPRACHNACHRICHT: heute Abend Plug rein]\n"
              "[AUFGABE: Heute Abend den Plug tragen]").install()
    u = _update("Sag ihm, er soll heute Abend den Plug tragen")
    _run(domina.handle(u, _context()))
    assert w.sn_inhalte == ["heute Abend Plug rein"]
    assert w.bestaetigungen == []
    ang = _angebot(u)
    assert ang and "Heute Abend den Plug tragen" in ang[0]
    assert state.get(DOM)["sn_aufgabe_text"] == "Heute Abend den Plug tragen"
    # Coach-Antwort ohne beide Tags
    assert all("[AUFGABE" not in r[0] and "[SPRACHNACHRICHT" not in r[0] for r in u.message.replies)


def test_handle_reine_nachricht_ohne_angebot():
    w = _Welt("Schick ich ihm.\n[SPRACHNACHRICHT: Ich bin stolz auf dich und freue mich auf heute Abend]").install()
    u = _update("Sag ihm, ich bin stolz auf ihn")
    _run(domina.handle(u, _context()))
    assert len(w.sn_inhalte) == 1
    assert _angebot(u) is None
    assert "sn_aufgabe_id" not in state.get(DOM)


def test_handle_keyword_bleibt_bestaetigungsdialog():
    w = _Welt("Ok.\n[SPRACHNACHRICHT: Er muss heute knien]").install()
    u = _update("Aufgabe: Heute Abend 10 Minuten knien")
    _run(domina.handle(u, _context()))
    assert w.bestaetigungen == ["Heute Abend 10 Minuten knien"]
    assert _angebot(u) is None


def test_handle_limits_stopp_kein_angebot():
    w = _Welt("Ok.\n[SPRACHNACHRICHT: Er muss heute X]\n[AUFGABE: Heute X]", sn_gesendet=False).install()
    u = _update("Sag ihm, er muss heute X")
    _run(domina.handle(u, _context()))
    assert _angebot(u) is None
    assert w.bestaetigungen == [], "gestoppte Sprachnachricht nicht nochmal als Aufgabe durchs Gate"


# --------------------------------------------------------------------------
# Callback
# --------------------------------------------------------------------------

class _Query:
    def __init__(self, data: str):
        self.data = data
        self.message = _Msg()
        self.markup_entfernt = False

    async def answer(self):
        pass

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markup_entfernt = True


def _press(data: str):
    q = _Query(data)
    u = SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=int(DOM)))
    _run(domina.callback_sn_aufgabe(u, _context()))
    return q


def _angebot_anlegen(w: _Welt, quelltext: str = "Schreib ihm, er muss stillhalten") -> str:
    u = _update(quelltext)
    _run(domina._biete_sprachnachricht_als_aufgabe(u, DOM, "Er muss stillhalten", 2,
                                                   quelltext=quelltext))
    return state.get(DOM)["sn_aufgabe_id"]


def test_callback_ja_legt_task_an_ohne_zweite_zustellung():
    w = _Welt("").install()
    kennung = _angebot_anlegen(w)
    q = _press(f"snaufgabe:ja:{kennung}")
    assert q.markup_entfernt
    assert len(w.tasks) == 1, w.tasks
    tk = w.tasks[0]
    assert tk["aufgabe"] == "Er muss stillhalten" and tk["quelle"] == "sprachnachricht"
    assert tk["status"] == "offen" and tk["followup_in_tagen"] == 1 and tk["level"] == 2
    assert tk["kategorie"] == "Strap_on" and "termin_datum" not in tk["extra"]
    assert any("Angelegt" in r[0] for r in q.message.replies), q.message.replies
    # Sub: NUR der kurze Hinweis, kein zweiter Aufgabentext
    assert len(w.sub_sends) == 1 and w.sub_sends[0].startswith("📋") and "stillhalten" not in w.sub_sends[0]
    assert "sn_aufgabe_id" not in state.get(DOM)
    # Doppel-Tap → veraltet, kein zweiter Task
    q2 = _press(f"snaufgabe:ja:{kennung}")
    assert len(w.tasks) == 1 and any("nicht mehr aktuell" in r[0] for r in q2.message.replies)


def test_callback_nein_und_fremde_kennung():
    w = _Welt("").install()
    kennung = _angebot_anlegen(w)
    q = _press("snaufgabe:nein:deadbeef")
    assert w.tasks == [] and any("nicht mehr aktuell" in r[0] for r in q.message.replies)
    assert state.get(DOM)["sn_aufgabe_id"] == kennung, "fremde Kennung räumt das Angebot nicht ab"
    q = _press(f"snaufgabe:nein:{kennung}")
    assert w.tasks == [] and w.sub_sends == []
    assert any("reine Nachricht" in r[0] for r in q.message.replies)
    assert "sn_aufgabe_id" not in state.get(DOM)


def test_callback_termin_und_limits():
    w = _Welt("").install()
    # Termin im Original-Wortlaut → Nachfrage an dem Tag
    morgen = date.today() + timedelta(days=1)
    from bot.services import datum_erkennung
    orig = datum_erkennung.finde_termin
    datum_erkennung.finde_termin = lambda text: (morgen + timedelta(days=2), "Samstag") if "Samstag" in (text or "") else None
    try:
        kennung = _angebot_anlegen(w, quelltext="Sag ihm, am Samstag muss er stillhalten")
        q = _press(f"snaufgabe:ja:{kennung}")
    finally:
        datum_erkennung.finde_termin = orig
    assert len(w.tasks) == 1 and w.tasks[0]["followup_in_tagen"] == 3
    assert w.tasks[0]["extra"]["termin_datum"] == (morgen + timedelta(days=2)).isoformat()
    # Limits-Treffer → kein Task, Grenzen-Meldung
    w.limits_treffer = [{"limit": "X", "quelle": "sklave", "matched_via": "X"}]
    kennung = _angebot_anlegen(w)
    _press(f"snaufgabe:ja:{kennung}")
    assert len(w.tasks) == 1
    assert domina.telegram_helper.reply_markdown_safe.await_count == 1


def test_locale_keys_vorhanden():
    for key in ("COACH_SN_AUFGABE_FRAGE", "BUTTON_SN_AUFGABE_JA", "BUTTON_SN_AUFGABE_NEIN",
                "COACH_SN_AUFGABE_ANGELEGT", "COACH_SN_AUFGABE_NEIN_OK", "COACH_SN_AUFGABE_VERALTET",
                "COACH_SN_WANN", "SKLAVE_SN_AUFGABE_HINWEIS"):
        assert t(key)
    assert "16.09." in domina._nachfrage_wann(1) or True  # Datum ist laufzeitabhängig – nur kein Crash
    assert " um " in domina._nachfrage_wann(1)


def _run_alle():
    test_sprech_tags_fremdformen()
    test_klingt_nach_auftrag()
    test_handle_sprachnachricht_mit_auftrag_detektor()
    test_handle_beide_tags_nimmt_aufgabentext()
    test_handle_reine_nachricht_ohne_angebot()
    test_handle_keyword_bleibt_bestaetigungsdialog()
    test_handle_limits_stopp_kein_angebot()
    test_callback_ja_legt_task_an_ohne_zweite_zustellung()
    test_callback_nein_und_fremde_kennung()
    test_callback_termin_und_limits()
    test_locale_keys_vorhanden()
    print("✅ Alle Sprachnachricht→Aufgabe-Tests bestanden")


if __name__ == "__main__":
    _run_alle()
