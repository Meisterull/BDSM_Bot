"""
Tests zum Psycho-Training nach dem Umbau (16.11.2026):

  - die Übung setzt keinen Mode mehr; sie wird beim Versand abgelegt und in den
    Coach-Verlauf gehängt
  - Antworten nur über den Knopf unter der Übung; die Antwort landet am selben
    Eintrag und ist gespeichert, BEVOR die Rückmeldung erzeugt wird
  - ein Auftrag an den Bot im Antwort-Mode ist keine Antwort
  - Typ-Wechsel über fünf Übungsarten, Kategorie-Trainings zählen nicht mit
  - Prompts: Dossier und frühere Übungen im Stoff, genau eine Übungsart,
    keine Antwort-Pflicht; angehängte Schlussfragen werden abgeschnitten
  - eigene Trainings-Zeit, Knopf-Rolle, Flow-Keys
  - Antworten fließen ins Dossier der Dom-Seite

    python3 tests/test_training.py
"""
import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import stubs  # noqa: E402

stubs.aktivieren()

from bot import config  # noqa: E402

config.DOMINA_CHAT_ID = "111"
config.SKLAVE_CHAT_ID = "222"
config.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")

from bot import state  # noqa: E402
from bot.app import gates  # noqa: E402
from bot.handlers import domina, dossier, einstellungen  # noqa: E402
from bot.handlers import training as tr  # noqa: E402
from bot.prompts import training as tp  # noqa: E402
from bot.services import limits_check, paare, persona_config  # noqa: E402
from bot.services.db import profile as db_profile, progress as db_progress, tasks as db_tasks  # noqa: E402
from bot.services.miniapp import training as mt  # noqa: E402

DOM = "111"
UEBUNG = "Bleib beim nächsten Mal nach der Ansage einfach still stehen und zähl innerlich bis fünf."


def _vor(tage: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat()


def _update(text: str = "", data: str | None = None):
    u = MagicMock()
    u.effective_chat.id = int(DOM)
    u.message.text = text
    u.message.reply_text = AsyncMock()
    if data is not None:
        q = u.callback_query
        q.data = data
        q.answer = AsyncMock()
        q.edit_message_reply_markup = AsyncMock()
        q.message.reply_text = AsyncMock()
        q.message.text = "🧠 Training – Challenge\n\n" + UEBUNG
        q.message.message_id = 77
    return u


def _context():
    c = MagicMock()
    c.bot.send_chat_action = AsyncMock()
    c.bot.edit_message_reply_markup = AsyncMock()
    return c


class _Flicken:
    """Setzt Attribute für die Dauer eines Tests und stellt sie wieder her."""

    def __init__(self, *eintraege):
        self.eintraege = eintraege
        self.alt = []

    def __enter__(self):
        for obj, name, wert in self.eintraege:
            self.alt.append((obj, name, getattr(obj, name)))
            setattr(obj, name, wert)
        return self

    def __exit__(self, *exc):
        for obj, name, wert in reversed(self.alt):
            setattr(obj, name, wert)


def _reset():
    state.set_mode(DOM, "chat")
    s = state.get(DOM)
    for k in tr._STATE_KEYS:
        s.pop(k, None)
    state.clear_history(DOM)
    state.set_paused(False)
    state.set_coach_ruhe(None)


# --------------------------------------------------------------------------
# Typ-Wechsel und Nachbearbeitung
# --------------------------------------------------------------------------

def test_typ_wechsel_ueber_fuenf_arten():
    assert tr.TRAINING_TYPEN == ["mindset", "situation", "reflexion", "challenge", "worte"]
    assert tr.naechster_typ([]) == "mindset"
    assert tr.naechster_typ([{"typ": "challenge"}]) == "worte"
    assert tr.naechster_typ([{"typ": "worte"}]) == "mindset"
    # Kategorie-Training als jüngster Eintrag darf den Wechsel nicht zurücksetzen
    assert tr.naechster_typ([{"typ": "kategorie"}, {"typ": "situation"}]) == "reflexion"
    assert tr.naechster_typ([{"typ": "kategorie"}]) == "mindset"


def test_schlussfrage_wird_abgeschnitten():
    basis = "Lass ihn nach der Ansage einen Moment stehen und sag nichts weiter."
    assert tp.ohne_schlussfrage(basis + " Wie würdest du reagieren?") == basis
    assert tp.ohne_schlussfrage(basis + " Magst du das probieren? Und wie fühlt sich das an?") == basis
    # Frage mitten im Text und Frage als Beispielsatz in Anführungszeichen bleiben
    mitte = "Merkst du, wie er wartet? Genau diese Pause ist dein Werkzeug, mehr brauchst du nicht."
    assert tp.ohne_schlussfrage(mitte) == mitte
    zitat = basis + ' Wenn er zögert: „Muss ich mich wiederholen?"'
    assert tp.ohne_schlussfrage(zitat) == zitat
    # zu wenig Rest → unverändert
    assert tp.ohne_schlussfrage("Kurz. Und jetzt?") == "Kurz. Und jetzt?"
    assert tp.ohne_schlussfrage("") == ""


def test_bereinigt_je_uebungsart():
    roh = f"**{UEBUNG}** [AUFGABE: nichts] Probierst du es aus?"
    assert tr.bereinigt(roh, "challenge") == UEBUNG
    # Reflexion ist eine Frage, bei Worten stehen die Beispielsätze am Schluss
    frage = "Du hast dir vorgenommen, öfter den ersten Schritt zu machen. Woran würdest du merken, dass es so weit ist?"
    assert tr.bereinigt(frage, "reflexion") == frage
    assert tr.bereinigt(frage, "worte") == frage
    assert tr.bereinigt("", "mindset") == ""
    # Anführungszeichen: nur ein Paar um den ganzen Text fällt weg, der Beispielsatz am Ende bleibt ganz
    assert tr.bereinigt(f'"{UEBUNG}"', "mindset") == UEBUNG
    saetze = 'Wenn er zögert:\n"Muss ich mich wiederholen?"\nWenn er nachfragt:\n"Du hast mich gehört."'
    assert tr.bereinigt(saetze, "worte") == saetze


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

def test_uebung_prompt_stoff_und_auftrag():
    profil = {"erfahrungsstand": "erfahren", "aktuelles_level": 4, "interessen": ["Rituale", "klare Ansagen"],
              "ziele": "mehr Abwechslung", "domina_dossier": "Führt ruhig und direkt, plant gern im Voraus.",
              "grenzen": ["Nadeln"], "kinderfreie_zeiten": ["ab 20:00"]}
    marker = {"mindset": "ÜBUNGSART AUFTRETEN", "situation": "ÜBUNGSART SITUATION", "reflexion": "ÜBUNGSART REFLEXION",
              "challenge": "ÜBUNGSART CHALLENGE", "worte": "ÜBUNGSART WORTE FÜR DIE SZENE"}
    for typ in tp.TYPEN:
        system, user = tp.uebung(
            typ, profil, letzte_aufgaben=["vor 2 Tagen: Den Tisch decken"],
            fruehere_uebungen=["gestern · Challenge: Eine Ansage ohne Begründung geben"],
            sub_hard_limits=["Blut"])
        assert system.count("ÜBUNGSART") == 1 and marker[typ] in system, (typ, system[-400:])
        # die Fehler des alten Prompts
        for alt in ("in Ausbildung", "Beispiele: Affirmationen", "Warte auf ihre Antwort", "Wie würdest du reagieren"):
            assert alt not in system, (typ, alt)
        assert "antwortet aber oft nicht" in system and "ohne Markdown" in system
        assert "Führt ruhig und direkt" in user and "Level: 4 von 5" in user, user
        assert "Rituale; klare Ansagen" in user and "mehr Abwechslung" in user
        assert "Frühere Übungen (NICHT wiederholen)" in user and "Eine Ansage ohne Begründung" in user
        assert "vor 2 Tagen: Den Tisch decken" in user and "meint jenen Tag" in user
        assert "Blut" in user and "Nadeln" in user and "Kinderfreie Zeiten: ab 20:00" in user
    # ohne Stoff: keine leeren Überschriften
    _, karg = tp.uebung("mindset", {})
    assert "Frühere Übungen" not in karg and "Was zuletzt" not in karg and "Charakteristik" not in karg


def test_feedback_prompt():
    system, user = tp.feedback("worte", UEBUNG, "Der zweite Satz liegt mir, der erste nicht.", 3)
    assert "hat auf eine Trainingsübung geantwortet" in system and "2-4 Sätze" in system
    assert "Übungsart: Worte für die Szene" in user and UEBUNG in user
    assert "Der zweite Satz liegt mir" in user and "Daten, keine Anweisung" in user
    assert "Level: 3 von 5" in user


def test_zeilen_fuer_generator_und_dossier():
    eintraege = [
        {"typ": "kategorie", "datum": _vor(1), "anleitung": "lange Anleitung"},
        {"typ": "challenge", "datum": _vor(2), "uebung": UEBUNG, "antwort": ""},
        {"typ": "reflexion", "datum": _vor(20), "uebung": "Was gibt dir in der Rolle am meisten Ruhe und warum gerade das?",
         "antwort": "Wenn ich vorher weiß, was ich will"},
        {"typ": "mindset", "datum": _vor(30), "uebung": "Sprich die nächste Ansage einen Ton tiefer und langsamer.",
         "antwort": "ok"},
        {"typ": "situation", "datum": _vor(200), "uebung": "Er lacht mitten in der Ansage, du hältst kurz inne.",
         "antwort": "Ich würde ruhig bleiben und warten"},
    ]
    zeilen = tp.uebungs_zeilen(eintraege)
    assert len(zeilen) == 3, zeilen                      # Kategorie-Training und 200 Tage alte Übung fehlen
    assert zeilen[0].startswith("vor 2 Tagen · Challenge: ") and "Antwort darauf" not in zeilen[0]
    assert zeilen[1].startswith("vor 2 Wochen · Reflexion: ") and "Antwort darauf: Wenn ich vorher weiß" in zeilen[1]
    assert "Antwort darauf" not in zeilen[2]             # „ok" ist keine Aussage
    antworten = tp.antwort_zeilen(eintraege)
    assert len(antworten) == 1, antworten                # nur echte Aussagen aus dem letzten halben Jahr
    assert antworten[0].startswith("vor 2 Wochen, Reflexion (Übung: Was gibt dir")
    lang = tp.antwort_zeilen(eintraege, max_tage=365)
    assert len(lang) == 2 and lang[1].startswith("vor 6 Monaten, Situation"), lang
    assert lang[1].endswith("Ich würde ruhig bleiben und warten")


# --------------------------------------------------------------------------
# Zustellung: kein Mode, Ablage beim Versand
# --------------------------------------------------------------------------

async def test_job_setzt_keinen_mode_und_legt_ab():
    _reset()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    save = AsyncMock(return_value="x")
    with _Flicken((config, "TRAINING_ENABLED", True),
                  (db_profile, "get_user_profile", AsyncMock(return_value={"erfahrungsstand": "x"})),
                  (db_progress, "save_training", save),
                  (tr, "neue_uebung", AsyncMock(return_value={"typ": "challenge", "uebung": UEBUNG, "level": 3}))):
        await tr.daily_training(bot)
    assert bot.send_message.await_count == 1
    kw = bot.send_message.await_args.kwargs
    assert kw["chat_id"] == DOM and UEBUNG in kw["text"] and kw.get("reply_markup") is not None
    assert "/abbrechen" not in kw["text"] and "Schreibe" not in kw["text"], kw["text"]
    assert state.get_mode(DOM) == "chat"
    assert not any(state.get(DOM).get(k) for k in tr._STATE_KEYS)
    # abgelegt mit eigener ID, ohne Antwort; im Coach-Verlauf steht die Übung
    daten = save.await_args.args[1]
    assert daten["typ"] == "challenge" and daten["uebung"] == UEBUNG and daten["antwort"] == "" and daten["quelle"] == "job"
    uuid.UUID(save.await_args.kwargs["point_id"])
    assert state.get_history(DOM)[-1] == {"role": "assistant", "content": UEBUNG}


async def test_job_wartet_bei_flow_ruhe_und_schalter():
    neu = AsyncMock(return_value={"typ": "challenge", "uebung": UEBUNG, "level": 3})
    bot = MagicMock()
    bot.send_message = AsyncMock()
    with _Flicken((db_profile, "get_user_profile", AsyncMock(return_value={"erfahrungsstand": "x"})),
                  (db_progress, "save_training", AsyncMock(return_value="x")),
                  (tr, "neue_uebung", neu)):
        with _Flicken((config, "TRAINING_ENABLED", False)):
            _reset()
            await tr.daily_training(bot)
        with _Flicken((config, "TRAINING_ENABLED", True)):
            _reset()
            state.set_mode(DOM, "aufgabe_bestaetigung")     # laufender Flow
            await tr.daily_training(bot)
            assert state.get_mode(DOM) == "aufgabe_bestaetigung"
            _reset()
            state.set_coach_ruhe("ruhe", (datetime.now(timezone.utc) + timedelta(days=2)).isoformat())
            await tr.daily_training(bot)
            _reset()
    assert neu.await_count == 0 and bot.send_message.await_count == 0


def test_knopf_traegt_die_id():
    erfasst = {}

    def knopf(text, callback_data=None):
        erfasst["data"] = callback_data
        return MagicMock()

    tid = str(uuid.uuid4())
    with _Flicken((tr, "InlineKeyboardButton", knopf), (tr, "InlineKeyboardMarkup", lambda reihen: reihen)):
        tr._knopf(tid)
    assert erfasst["data"] == f"training:antwort:{tid}" and len(erfasst["data"].encode()) <= 64


# --------------------------------------------------------------------------
# Antworten
# --------------------------------------------------------------------------

async def test_knopf_oeffnet_antwort_und_antwort_landet_am_eintrag():
    _reset()
    tid = str(uuid.uuid4())
    reihenfolge = []

    async def update_training(point_id, felder):
        reihenfolge.append(("update", point_id, dict(felder)))

    async def llm(prompt, **kw):
        reihenfolge.append(("llm",))
        return "Gute Beobachtung."

    eintrag = {"typ": "challenge", "uebung": UEBUNG, "antwort": "", "user_id": "domina"}
    with _Flicken((db_progress, "get_training_by_id", AsyncMock(return_value=eintrag)),
                  (db_progress, "update_training", update_training),
                  (db_progress, "save_training", AsyncMock(return_value="neu")),
                  (db_profile, "get_user_profile", AsyncMock(return_value={"aktuelles_level": 3})),
                  (tr.grok, "simple", llm)):
        u = _update(data=f"training:antwort:{tid}")
        await tr.callback(u, _context())
        s = state.get(DOM)
        assert state.get_mode(DOM) == tr.MODE
        assert (s["training_id"], s["training_typ"], s["training_uebung"], s["training_msg_id"]) == (tid, "challenge", UEBUNG, 77)
        assert u.callback_query.message.reply_text.await_count == 1

        ctx = _context()
        u2 = _update("Er hat tatsächlich gewartet, das war neu für mich.")
        await tr.handle_antwort(u2, ctx)
        assert db_progress.save_training.await_count == 0     # kein zweiter Eintrag
    # erst die Antwort sichern, dann das LLM, dann die Rückmeldung ergänzen
    assert [r[0] for r in reihenfolge] == ["update", "llm", "update"], reihenfolge
    assert reihenfolge[0][1] == tid and reihenfolge[0][2]["antwort"].startswith("Er hat tatsächlich gewartet")
    assert reihenfolge[2][2] == {"feedback": "Gute Beobachtung."}
    assert state.get_mode(DOM) == "chat" and not any(state.get(DOM).get(k) for k in tr._STATE_KEYS)
    verlauf = state.get_history(DOM)[-3:]
    assert [m["role"] for m in verlauf] == ["assistant", "user", "assistant"], verlauf
    assert verlauf[0]["content"] == UEBUNG and verlauf[2]["content"] == "Gute Beobachtung."
    assert ctx.bot.edit_message_reply_markup.await_args.kwargs["message_id"] == 77
    assert "Gute Beobachtung." in u2.message.reply_text.await_args.args[0]


async def test_antwort_bleibt_bei_llm_ausfall_erhalten():
    _reset()
    tid = str(uuid.uuid4())
    update = AsyncMock()
    s = state.get(DOM)
    s.update({"training_id": tid, "training_typ": "reflexion", "training_uebung": UEBUNG, "training_msg_id": 5})
    state.set_mode(DOM, tr.MODE)
    with _Flicken((db_progress, "update_training", update),
                  (db_profile, "get_user_profile", AsyncMock(return_value={})),
                  (tr.grok, "simple", AsyncMock(side_effect=RuntimeError("llm weg")))):
        u = _update("Das beschäftigt mich schon länger.")
        await tr.handle_antwort(u, _context())
    assert update.await_count == 1 and update.await_args.args[1]["antwort"] == "Das beschäftigt mich schon länger."
    assert state.get_mode(DOM) == "chat"
    assert u.message.reply_text.await_count == 1          # schlichte Bestätigung statt Fehlermeldung


async def test_auftrag_im_antwort_modus_ist_keine_antwort():
    _reset()
    s = state.get(DOM)
    s.update({"training_id": str(uuid.uuid4()), "training_typ": "challenge", "training_uebung": UEBUNG})
    state.set_mode(DOM, tr.MODE)
    weiter = AsyncMock()
    update = AsyncMock()
    with _Flicken((domina, "handle", weiter), (db_progress, "update_training", update),
                  (db_progress, "save_training", AsyncMock())):
        u = _update("Gib ihm bitte folgende Aufgabe: Er soll morgen den Flur wischen.")
        await tr.handle_antwort(u, _context())
        assert db_progress.save_training.await_count == 0
    assert weiter.await_count == 1 and update.await_count == 0
    assert state.get_mode(DOM) == "chat" and not state.get(DOM).get("training_id")


async def test_abbrechen_und_belegter_chat():
    _reset()
    s = state.get(DOM)
    s.update({"training_id": "x", "training_typ": "challenge", "training_uebung": UEBUNG})
    state.set_mode(DOM, tr.MODE)
    u = _update("abbrechen")
    await tr.handle_antwort(u, _context())
    assert state.get_mode(DOM) == "chat" and not state.get(DOM).get("training_id")
    # Knopf, während ein anderer Flow läuft: nichts überschreiben
    state.set_mode(DOM, "aufgabe_bestaetigung")
    with _Flicken((db_progress, "get_training_by_id", AsyncMock(return_value={"typ": "challenge", "uebung": UEBUNG}))):
        u = _update(data=f"training:antwort:{uuid.uuid4()}")
        await tr.callback(u, _context())
        assert db_progress.get_training_by_id.await_count == 0
    assert state.get_mode(DOM) == "aufgabe_bestaetigung"
    _reset()


async def test_knopf_fremde_oder_kaputte_id():
    _reset()
    # kaputte ID → nichts passiert
    u = _update(data="training:antwort:keine-uuid")
    await tr.callback(u, _context())
    assert state.get_mode(DOM) == "chat"
    # Eintrag nicht auffindbar (fehlt oder gehört einem anderen Paar): Antwort
    # ist möglich, geschrieben wird aber ein NEUER Eintrag – nie auf die ID von außen.
    save = AsyncMock(return_value="neu")
    update = AsyncMock()
    with _Flicken((db_progress, "get_training_by_id", AsyncMock(return_value=None)),
                  (db_progress, "save_training", save), (db_progress, "update_training", update),
                  (db_profile, "get_user_profile", AsyncMock(return_value={})),
                  (tr.grok, "simple", AsyncMock(return_value="Verstehe."))):
        u = _update(data=f"training:antwort:{uuid.uuid4()}")
        await tr.callback(u, _context())
        assert state.get(DOM)["training_id"] == "" and state.get(DOM)["training_uebung"] == UEBUNG
        await tr.handle_antwort(_update("Das probiere ich am Wochenende."), _context())
    assert save.await_count == 1 and "point_id" not in save.await_args.kwargs
    assert save.await_args.args[1]["antwort"] == "Das probiere ich am Wochenende."
    assert [c.args[0] for c in update.await_args_list] == ["neu"]      # nur die Rückmeldung, am neuen Eintrag
    # schon beantwortet → Knopf weg, kein Mode
    _reset()
    with _Flicken((db_progress, "get_training_by_id", AsyncMock(return_value={"typ": "challenge", "antwort": "war gut"}))):
        u = _update(data=f"training:antwort:{uuid.uuid4()}")
        await tr.callback(u, _context())
    assert state.get_mode(DOM) == "chat" and u.callback_query.edit_message_reply_markup.await_count == 1


# --------------------------------------------------------------------------
# Generator: Stoff, Grenzen-Prüfung, Mini-App
# --------------------------------------------------------------------------

async def test_neue_uebung_laedt_stoff_und_prueft_grenzen():
    erfasst = {}

    async def generator(prompt, hard_limits=None, grenzen=None, **kw):
        erfasst.update(system=prompt[0], user=prompt[1], hard_limits=hard_limits, grenzen=grenzen, kw=kw)
        return f"*{UEBUNG}* Machst du das?"

    async def profil(user_id):
        if "sklave" in str(user_id):
            return {"hard_limits": ["Blut"]}
        return {"aktuelles_level": 4, "grenzen": ["Nadeln"], "domina_dossier": "Führt ruhig und direkt."}

    eintraege = [{"typ": "kategorie", "datum": _vor(1)},
                 {"typ": "situation", "datum": _vor(3), "uebung": "Er zögert bei der zweiten Ansage, du wartest ab."}]
    with _Flicken((db_profile, "get_user_profile", profil),
                  (db_progress, "get_training_entries", AsyncMock(return_value=eintraege)),
                  (db_tasks, "get_tasks_by_status", AsyncMock(return_value=[])),
                  (limits_check, "generate_mit_limit_retry", generator)):
        daten = await tr.neue_uebung()
        assert daten == {"typ": "reflexion", "uebung": f"{UEBUNG} Machst du das?", "level": 4}, daten
        assert "ÜBUNGSART REFLEXION" in erfasst["system"] and "Er zögert bei der zweiten Ansage" in erfasst["user"]
        assert erfasst["hard_limits"] == ["Blut"] and erfasst["grenzen"] == ["Nadeln"] and erfasst["kw"] == {"reasoning": True}
        # vorgegebener Typ gewinnt; Schlussfrage fällt bei „challenge" weg
        daten = await tr.neue_uebung("challenge")
        assert daten["typ"] == "challenge" and daten["uebung"] == UEBUNG, daten

    async def verworfen(prompt, *a, **kw):
        return None

    with _Flicken((db_profile, "get_user_profile", profil),
                  (db_progress, "get_training_entries", AsyncMock(return_value=[])),
                  (db_tasks, "get_tasks_by_status", AsyncMock(return_value=[])),
                  (limits_check, "generate_mit_limit_retry", verworfen)):
        assert await tr.neue_uebung() is None


async def test_miniapp_uebung_und_antwort():
    pid = paare.alle_paare()[0].paar_id
    tid = str(uuid.uuid4())
    save = AsyncMock(return_value=tid)
    update = AsyncMock()
    with _Flicken((tr, "neue_uebung", AsyncMock(return_value={"typ": "worte", "uebung": UEBUNG, "level": 2})),
                  (db_progress, "save_training", save), (db_progress, "update_training", update),
                  (db_progress, "get_training_by_id", AsyncMock(return_value={"typ": "worte", "uebung": UEBUNG})),
                  (db_profile, "get_user_profile", AsyncMock(return_value={})),
                  (tr.grok, "simple", AsyncMock(return_value="Passt zu dir."))):
        r = await mt._psycho_uebung(pid, "")
        assert r == {"typ": "worte", "uebung": UEBUNG, "id": tid}, r
        assert save.await_args.args[1]["quelle"] == "app" and save.await_count == 1
        r = await mt._psycho_antwort(pid, "worte", UEBUNG, "Den ersten Satz nehme ich.", tid)
        assert r == {"feedback": "Passt zu dir."} and save.await_count == 1
        assert update.await_args_list[0].args == (tid, {"antwort": "Den ersten Satz nehme ich.",
                                                         "zusammenfassung": "worte: Den ersten Satz nehme ich."})
        assert (await mt._psycho_antwort(pid, "unbekannt", UEBUNG, "x"))["fehler"]
    # Grenzen-Prüfung schlägt zweimal an → Fehler statt leerer Übung
    with _Flicken((tr, "neue_uebung", AsyncMock(return_value=None)), (db_progress, "save_training", save)):
        assert (await mt._psycho_uebung(pid, "worte")).get("fehler")
    # ID vom Client ohne passenden Eintrag → neuer Eintrag statt Schreiben auf die fremde ID
    save2 = AsyncMock(return_value="neu")
    with _Flicken((db_progress, "get_training_by_id", AsyncMock(return_value=None)),
                  (db_progress, "save_training", save2), (db_progress, "update_training", AsyncMock()),
                  (db_profile, "get_user_profile", AsyncMock(return_value={})),
                  (tr.grok, "simple", AsyncMock(return_value="Okay."))):
        await mt._psycho_antwort(pid, "worte", UEBUNG, "Eine Antwort ohne Eintrag.", str(uuid.uuid4()))
        assert save2.await_count == 1 and "point_id" not in save2.await_args.kwargs


# --------------------------------------------------------------------------
# Zeit, Rollen, Flow-Keys, Dossier
# --------------------------------------------------------------------------

def test_zeit_rolle_und_flow_keys():
    assert config.TRAINING_TIME == os.getenv("TRAINING_TIME", "20:15")
    assert not hasattr(config, "TRAINING_OFFSET_MINUTEN")
    assert persona_config.ZEIT_FELDER["training_time"] == "TRAINING_TIME"
    assert persona_config.zeit("training_time") == config.TRAINING_TIME
    # neue Zeit hängt hinten an – die Nummern der bestehenden Zeiten bleiben stabil
    assert einstellungen.ZEIT_REIHENFOLGE[-1][0] == "training_time"
    assert {f for f, _ in einstellungen.ZEIT_REIHENFOLGE} == set(persona_config.ZEIT_FELDER)
    assert ("training:", paare.ROLLE_DOM) in gates._CALLBACK_ROLLEN
    assert set(tr._STATE_KEYS) <= set(state.FLOW_STATE_KEYS)
    for typ in tr.TRAINING_TYPEN:
        assert tr.typ_label(typ)


async def test_dossier_bekommt_trainings_antworten():
    erfasst = {}

    async def llm(prompt, **kw):
        erfasst["prompt"], erfasst["system"] = prompt, kw.get("system", "")
        return "Sie führt ruhig."

    eintraege = [{"typ": "reflexion", "datum": _vor(5), "uebung": "Was gibt dir in der Rolle am meisten Ruhe und warum?",
                  "antwort": "Wenn ich vorher weiß, was ich will"},
                 {"typ": "challenge", "datum": _vor(2), "uebung": UEBUNG, "antwort": ""}]
    with _Flicken((db_profile, "get_user_profile", AsyncMock(return_value={"interessen": ["Rituale"], "ziele": "x"})),
                  (db_profile, "patch_profile_fields", AsyncMock()),
                  (db_progress, "get_training_entries", AsyncMock(return_value=eintraege)),
                  (dossier.qdrant, "get_bewertungs_kontext", AsyncMock(return_value="")),
                  (dossier.qdrant, "get_recent_task_kategorien", AsyncMock(return_value=[])),
                  (dossier.qdrant, "get_hybrid_conversation_context", AsyncMock(return_value=[])),
                  (dossier.emb, "get_embedding", AsyncMock(return_value=[0.0])),
                  (dossier.grok, "simple", llm)):
        text = await dossier.aktualisiere_domina_dossier()
    assert text == "Sie führt ruhig."
    assert "Was sie im Training über sich geschrieben hat" in erfasst["prompt"]
    assert "Wenn ich vorher weiß, was ich will" in erfasst["prompt"] and UEBUNG not in erfasst["prompt"]
    assert "eigene Aussagen aus dem Training" in erfasst["system"]
    # ohne Antworten kein leerer Abschnitt
    with _Flicken((db_profile, "get_user_profile", AsyncMock(return_value={"interessen": ["Rituale"], "ziele": "x"})),
                  (db_profile, "patch_profile_fields", AsyncMock()),
                  (db_progress, "get_training_entries", AsyncMock(return_value=eintraege[1:])),
                  (dossier.qdrant, "get_bewertungs_kontext", AsyncMock(return_value="")),
                  (dossier.qdrant, "get_recent_task_kategorien", AsyncMock(return_value=[])),
                  (dossier.qdrant, "get_hybrid_conversation_context", AsyncMock(return_value=[])),
                  (dossier.emb, "get_embedding", AsyncMock(return_value=[0.0])),
                  (dossier.grok, "simple", llm)):
        await dossier.aktualisiere_domina_dossier()
    assert "Was sie im Training" not in erfasst["prompt"]


if __name__ == "__main__":
    test_typ_wechsel_ueber_fuenf_arten()
    test_schlussfrage_wird_abgeschnitten()
    test_bereinigt_je_uebungsart()
    test_uebung_prompt_stoff_und_auftrag()
    test_feedback_prompt()
    test_zeilen_fuer_generator_und_dossier()
    asyncio.run(test_job_setzt_keinen_mode_und_legt_ab())
    asyncio.run(test_job_wartet_bei_flow_ruhe_und_schalter())
    test_knopf_traegt_die_id()
    asyncio.run(test_knopf_oeffnet_antwort_und_antwort_landet_am_eintrag())
    asyncio.run(test_antwort_bleibt_bei_llm_ausfall_erhalten())
    asyncio.run(test_auftrag_im_antwort_modus_ist_keine_antwort())
    asyncio.run(test_abbrechen_und_belegter_chat())
    asyncio.run(test_knopf_fremde_oder_kaputte_id())
    asyncio.run(test_neue_uebung_laedt_stoff_und_prueft_grenzen())
    asyncio.run(test_miniapp_uebung_und_antwort())
    test_zeit_rolle_und_flow_keys()
    asyncio.run(test_dossier_bekommt_trainings_antworten())
    print("✅ Alle Trainings-Tests bestanden")
