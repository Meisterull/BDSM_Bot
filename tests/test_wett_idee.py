"""
Regressions-Tests Wettvorschlag 🎲 (16.09.2026, handlers/coach_quiz + scheduler):

Live 15.09.2026: Die Dom-Seite hatte „noch nie einen Wettvorschlag bekommen,
nur Quiz" – der Coach-Impuls würfelte Quiz/Quiz/Wette, die Wett-Idee kam als
reine Text-Idee ohne Flow, und Grok lieferte statt 2–4 Sätzen an sie eine
1100-Zeichen-Herrin-Nachricht an den Sub in Anführungszeichen.

  - _idee_verstoesse: Länge, Zitat-Block, Vokativ-Anrede des Subs
  - _wett_idee_generieren: Drift → ein Retry; zweimal Drift → None
  - sende_wett_idee: Vorschlag mit Buttons, Idee im State geparkt
  - callback senden: Herrin-Ansage (Prompt wette_an_sklaven) als Text ohne
    Sprech-Tags + Voice an den Sub, Buttons weg, State leer; Limits-Treffer /
    LLM-Fehler → Hinweis, State bleibt; fremde Kennung → veraltet
  - callback neu: neue Idee mit Buttons, Zähler, Deckel WETT_IDEE_MAX_NEU
  - _impuls_reihenfolge: strikte Abwechslung Quiz/Wette
  - Schwerpunkt (Wunsch-Wettvorschlag): Prompt, Subsample behält Treffer,
    „Andere Idee" bleibt beim Thema; coach_impuls_job: Wunsch heute sofort,
    Wunsch von gestern verfällt, COACH_IMPULS_QUIZ=0 → nur noch Wetten

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_wett_idee.py
"""
import asyncio
import os
import sys
import tempfile
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
from bot.handlers import coach_quiz as cq  # noqa: E402
from bot.scheduler import followup as sched  # noqa: E402
from bot.prompts import followup as fp  # noqa: E402
from bot.services import persona_config  # noqa: E402
from bot.messages import t  # noqa: E402

DOM = "111"
GUT = "Wer von euch bis Freitag öfter pünktlich ist, gewinnt. Gewinnst du, bringt er dir drei Abende Kaffee ans Bett; gewinnt er, darf er einmal die Filmwahl bestimmen."
DRIFT = ("„Kleine Maus, wir machen eine kleine Wette für die nächsten zwei Tage. Du trägst ab sofort "
         "bis übermorgen Abend durchgehend den Plug, auch bei der Arbeit. Wenn du ihn auch nur eine "
         "Minute rausnimmst, gewinne ich. Dann muss er morgen Abend eine Stunde knien und danach "
         "bekomme ich Kaffee ans Bett. Hält er durch, gewinnt er und darf die Filmwahl bestimmen. "
         "Schick mir jetzt einfach nur ‚Wette angenommen‘ oder ‚Wette abgelehnt‘.“")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------

class _Msg:
    def __init__(self):
        self.replies: list[tuple[str, object]] = []

    async def reply_text(self, text, parse_mode=None, reply_markup=None, **kw):
        self.replies.append((text, reply_markup))


class _Query:
    def __init__(self, data: str):
        self.data = data
        self.message = _Msg()
        self.markup_entfernt = False

    async def answer(self, text=None, **kw):
        self.toast = text

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markup_entfernt = True


class _Welt:
    def __init__(self, ideen: list, ansage: str = "<soft>Wir wetten.</soft> Nimmst du an?"):
        self.ideen = list(ideen)          # Antworten des Ideen-Generators (der Reihe nach)
        self.ansage = ansage
        self.ansage_fehler = False
        self.retry_calls: list[str] = []
        self.simple_calls: list = []
        self.dom_sends: list[tuple[str, object]] = []
        self.sub_sends: list[tuple[str, str | None]] = []
        self.limits_treffer: list = []
        self.sub_fehler = False
        self.sub_markups: list = []
        self.profile = {"sklave": {"vorlieben": ["Kaffee ans Bett"], "hard_limits": []},
                        "domina": {"interessen": ["Lesen"], "grenzen": []}}

    async def fake_retry(self, p, sklave_hard_limits=None, domina_grenzen=None, system="", **kw):
        self.retry_calls.append(p)
        return self.ideen.pop(0) if self.ideen else None

    async def fake_simple(self, prompt, **kw):
        self.simple_calls.append(prompt)
        if self.ansage_fehler:
            raise RuntimeError("LLM down")
        return self.ansage

    async def send_domina(self, bot, text, parse_mode=None, reply_markup=None, **kw):
        self.dom_sends.append((text, reply_markup))

    async def send_sklave(self, bot, text, parse_mode=None, reply_markup=None, voice_text=None, **kw):
        if self.sub_fehler:
            raise RuntimeError("Telegram down")
        self.sub_sends.append((text, voice_text))
        self.sub_markups.append(reply_markup)

    async def get_profile(self, uid):
        return dict(self.profile.get(uid, {}))

    async def patch(self, uid, fields, **kw):
        self.profile.setdefault(uid, {}).update(fields)

    async def verletzungen(self, text, *a, **kw):
        return list(self.limits_treffer)

    def install(self):
        cq.limits_check.generate_mit_limit_retry = self.fake_retry
        cq.limits_check.verletzungen = self.verletzungen
        cq.grok.simple = self.fake_simple
        cq.grok.clean_text = lambda x: (x or "").strip()
        cq.qdrant.get_user_profile = self.get_profile
        cq.qdrant.patch_profile_fields = self.patch
        cq.qdrant.get_recent_tiny_tasks = AsyncMock(return_value=([], [], []))
        cq.telegram_helper.send_domina = self.send_domina
        cq.telegram_helper.send_sklave = self.send_sklave
        cq.state.is_paused = lambda *a, **k: False
        persona_config.sklave_anrede = lambda: "Kleine Maus"
        state.set_mode(DOM, "chat")
        return self

    @property
    def offen(self) -> dict:
        """Geparkter Wettvorschlag im Dom-Profil."""
        return self.profile["domina"].get(cq.FELD_WETT_IDEE) or {}

    @property
    def herrin_wette(self) -> dict:
        return self.profile["sklave"].get("herrin_wette") or {}


def _press(data: str):
    q = _Query(data)
    u = SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=int(DOM)))
    _run(cq.callback_wett_idee(u, SimpleNamespace(bot=object())))
    return q


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_meta_schluss_entfernen():
    idee = ("Wer von euch beiden heute Abend länger durchhält, gewinnt.\n\n"
            "Verliert er, trägt er den Plug. Verlierst du, bekommt er Milking.\n\n"
            "Kurz, klar und in 1–2 Tagen entscheidbar.")
    assert cq._meta_schluss_entfernen(idee).endswith("bekommt er Milking.")
    assert cq._meta_schluss_entfernen("Wer gewinnt, bestimmt den Abend. Kurz und klar.") == "Wer gewinnt, bestimmt den Abend."
    # Echter letzter Satz mit Wett-Inhalt bleibt, auch wenn „messbar" drin steht
    bleibt = "Wer bis Freitag mehr schafft, gewinnt. Verliert er, ist der Einsatz messbar hart: eine Stunde knien."
    assert cq._meta_schluss_entfernen(bleibt) == bleibt
    assert cq._meta_schluss_entfernen("Kurz und klar.") == "Kurz und klar."
    assert cq._meta_schluss_entfernen(GUT) == GUT


def test_vorwort_safeword_und_richtungs_regel():
    _Welt([]).install()
    assert cq._vorwort_entfernen("Hier ist eine Idee für euch:\n\n" + GUT) == GUT
    assert cq._vorwort_entfernen("**Vorschlag:**\n\n" + GUT) == GUT
    assert cq._vorwort_entfernen("Wie wär’s mit der Wette: er muss bis Freitag pünktlich sein.") == "Er muss bis Freitag pünktlich sein."
    assert cq._vorwort_entfernen("Hier meine Idee für eine Wette: Wer zuerst lacht, verliert.") == "Wer zuerst lacht, verliert."
    # Doppelpunkt mitten in der Idee ohne Vorwort-Anfang bleibt
    assert cq._vorwort_entfernen("Er trägt die Schürze bis 22 Uhr: schafft er es, gewinnt er.") == "Er trägt die Schürze bis 22 Uhr: schafft er es, gewinnt er."
    assert cq._nachsatz_entfernen(GUT + " Gefällt dir das so, oder?") == GUT
    assert cq._nachsatz_entfernen(GUT + "\n\nWillst du den Einsatz noch etwas höher ansetzen?") == GUT
    assert cq._nachsatz_entfernen("Wer gewinnt, bestimmt den Abend. Wer schafft mehr?") == "Wer gewinnt, bestimmt den Abend. Wer schafft mehr?"
    assert cq._nachsatz_entfernen(GUT + "\n\nPasst das so für dich?") == GUT
    assert cq._vorwort_entfernen(GUT) == GUT
    # Doppelpunkt-Zeile mit Wett-Inhalt bleibt stehen
    mit_inhalt = "Gewinnt er, gilt:\nKaffee ans Bett."
    assert cq._vorwort_entfernen(mit_inhalt) == mit_inhalt
    assert any("Safeword" in f for f in cq._idee_verstoesse("Verliert er, gibt es die Session ohne Safe-Word."))
    assert cq._meta_schluss_entfernen(GUT + " Alles bleibt in der gewohnten Richtung.") == GUT
    system, _ = fp.wett_idee(["Kaffee ans Bett"], [], [], [])
    assert "egal, wer gewinnt" in system and "nie Teil der Wette" in system


def test_idee_verstoesse():
    _Welt([]).install()
    assert cq._idee_verstoesse(GUT) == []
    # Zitiertes Einzelwort + langer Rest ist KEIN Zitat-Block (Fehlalarm Live 16.09. 20:34)
    einzelwort = ("Er muss bis morgen Abend durchhalten, ohne ein einziges Mal „später“ zu sagen, egal was du ihm "
                  "aufträgst. Verliert er, bekommt er drei Abende Spüldienst. Gewinnt er, wählt er den Film.")
    assert cq._idee_verstoesse(einzelwort) == []
    assert any("Anführungszeichen" in f for f in cq._idee_verstoesse("Gewinnst du, sagst du: „" + "x" * 130 + "“"))
    funde = cq._idee_verstoesse(DRIFT)
    assert any("Anführungszeichen" in f for f in funde), funde
    assert any("Kleine Maus" in f for f in funde), funde
    assert any("zu lang" in f for f in cq._idee_verstoesse("x" * 601))
    # Anrede mitten im Satz (kein Vokativ) ist okay
    assert cq._idee_verstoesse("Die Kleine Maus schafft bis Freitag zehn Liegestütze, sonst gewinnst du.") == []
    # Vokativ am Zeilenanfang ohne Anführungszeichen wird trotzdem erkannt
    assert any("Kleine Maus" in f for f in cq._idee_verstoesse("Kleine Maus, du trägst heute den Plug bis 22 Uhr."))


def test_generieren_retry_und_abbruch():
    w = _Welt([DRIFT, GUT]).install()
    assert _run(cq._wett_idee_generieren()) == GUT
    assert len(w.retry_calls) == 2 and "Mängel" in w.retry_calls[1]
    assert "keine Anführungszeichen" in w.retry_calls[1]
    w = _Welt([DRIFT, DRIFT]).install()
    assert _run(cq._wett_idee_generieren()) is None
    w = _Welt([None]).install()
    assert _run(cq._wett_idee_generieren()) is None
    # Prompt-Härtung: Idee an sie, keine fertige Nachricht, Anrede als Negativ-Beispiel
    system, _ = fp.wett_idee(["Kaffee ans Bett"], [], [], [])
    assert "KEINE fertige Nachricht" in system and "Kleine Maus, wir machen" in system
    assert "höchstens 500 Zeichen" in system


def test_sende_wett_idee_mit_buttons():
    w = _Welt([GUT]).install()
    assert _run(cq.sende_wett_idee(None)) is True
    assert len(w.dom_sends) == 1
    text, markup = w.dom_sends[0]
    assert text.startswith("🎲") and "Wettvorschlag" in text and "pünktlich" in text
    assert markup is not None
    assert w.offen["text"] == GUT and w.offen["kennung"] and w.offen["neu"] == 0
    # Drift ohne Rettung → kein Versand (Scheduler fällt auf Quiz zurück)
    w = _Welt([DRIFT, DRIFT]).install()
    assert _run(cq.sende_wett_idee(None)) is False and w.dom_sends == []


def test_callback_senden():
    w = _Welt([GUT]).install()
    _run(cq.sende_wett_idee(None))
    kennung = w.offen["kennung"]
    # LLM-Fehler → Hinweis, Buttons + State bleiben
    w.ansage_fehler = True
    q = _press(f"wettidee:senden:{kennung}")
    assert not q.markup_entfernt and w.sub_sends == [] and w.offen.get("kennung") == kennung
    assert any("nicht möglich" in r[0] for r in q.message.replies)
    # Limits-Treffer → Hinweis, State bleibt
    w.ansage_fehler = False
    w.limits_treffer = [{"limit": "X", "quelle": "sklave", "matched_via": "X"}]
    q = _press(f"wettidee:senden:{kennung}")
    assert w.sub_sends == [] and w.offen.get("kennung") == kennung
    assert any("Limits" in r[0] for r in q.message.replies)
    # Zustellung scheitert → Hinweis, Vorschlag bleibt geparkt, keine Wette angeboten
    w.limits_treffer = []
    w.sub_fehler = True
    q = _press(f"wettidee:senden:{kennung}")
    assert not q.markup_entfernt and w.offen.get("kennung") == kennung and not w.herrin_wette
    assert any("nicht möglich" in r[0] for r in q.message.replies)
    # Erfolg → Herrin-Ansage (ohne Tags) mit ✅/❌ + Voice, Buttons weg, Wette „angeboten"
    w.sub_fehler = False
    q = _press(f"wettidee:senden:{kennung}")
    assert q.markup_entfernt
    assert w.sub_sends == [("Wir wetten. Nimmst du an?", "<soft>Wir wetten.</soft> Nimmst du an?")]
    assert w.sub_markups[0] is not None
    assert w.offen == {"gesendet": kennung}
    assert any("Ist raus" in r[0] and "annehmen oder ablehnen" in r[0] and "50 Punkte" in r[0]
               for r in q.message.replies)
    hw = w.herrin_wette
    assert hw["status"] == "angeboten" and hw["idee"] == GUT and hw["ansage"].startswith("<soft>")
    assert "frist" not in hw, "die Frist startet erst mit der Annahme"
    # Prompt an die Herrin enthält die Idee und die Rollen-Drehung
    system, user = w.simple_calls[-1]
    assert GUT in user and "dritter Person" in system and "annimmt" in system and "kostet" in system
    assert "50 Punkte obendrauf" in system and "Beigabe" in system
    assert q.toast == t("COACH_WETTIDEE_SCHICKT")
    # Doppel-Tap auf die gesendete Idee → still, keine zweite Meldung, kein zweiter Versand
    q2 = _press(f"wettidee:senden:{kennung}")
    assert len(w.sub_sends) == 1 and q2.message.replies == [] and q2.toast is None
    # fremde/alte Kennung → weiterhin „nicht mehr aktuell"
    q3 = _press("wettidee:senden:deadbeef")
    assert any("nicht mehr aktuell" in r[0] for r in q3.message.replies)


def test_callback_neu_mit_deckel():
    w = _Welt([GUT, GUT + " (2)", GUT + " (3)", GUT + " (4)", GUT + " (5)"]).install()
    _run(cq.sende_wett_idee(None))
    k1 = w.offen["kennung"]
    q = _press(f"wettidee:neu:{k1}")
    assert q.markup_entfernt and len(w.dom_sends) == 2 and w.dom_sends[1][1] is not None
    assert q.toast == t("COACH_WETTIDEE_DENKT")
    assert "(2)" in w.dom_sends[1][0]
    k2 = w.offen["kennung"]
    assert k2 != k1 and w.offen["neu"] == 1
    # alter Button → veraltet, nichts Neues
    q = _press(f"wettidee:neu:{k1}")
    assert len(w.dom_sends) == 2 and any("nicht mehr aktuell" in r[0] for r in q.message.replies)
    _press(f"wettidee:neu:{k2}")
    k3 = w.offen["kennung"]
    _press(f"wettidee:neu:{k3}")
    k4 = w.offen["kennung"]
    assert w.offen["neu"] == 3 and len(w.dom_sends) == 4
    q = _press(f"wettidee:neu:{k4}")
    assert len(w.dom_sends) == 4 and any("reichen" in r[0] for r in q.message.replies)
    # nach dem Deckel geht Senden weiterhin
    q = _press(f"wettidee:senden:{k4}")
    assert len(w.sub_sends) == 1 and w.offen == {"gesendet": k4}


def test_impuls_reihenfolge():
    k = [("coach_quiz", 1), ("wett_idee", 2)]
    assert [n for n, _ in sched._impuls_reihenfolge(k, "coach_quiz")] == ["wett_idee", "coach_quiz"]
    assert [n for n, _ in sched._impuls_reihenfolge(k, "wett_idee")] == ["coach_quiz", "wett_idee"]
    assert sorted(n for n, _ in sched._impuls_reihenfolge(k, "")) == ["coach_quiz", "wett_idee"]


def test_schwerpunkt_prompt_auswahl_und_neu():
    assert "SCHWERPUNKT" not in fp.wett_idee(["Kaffee ans Bett"], [], [], [])[0]
    system, _ = fp.wett_idee(["Kaffee ans Bett"], [], [], [], schwerpunkt="Filmabend")
    assert "SCHWERPUNKT" in system and "„Filmabend\"" in system
    # Treffer überleben das Subsample immer, Schreibweise egal, Reihenfolge bleibt
    zeilen = [f"Vorliebe {i}" for i in range(12)]
    zeilen[9] = "Film-Abend auf dem Boden"
    for _ in range(20):
        auswahl, treffer = cq._auswahl_mit_schwerpunkt(zeilen, 8, "Filmabend")
        assert len(auswahl) == 8 and treffer == ["Film-Abend auf dem Boden"] and zeilen[9] in auswahl
        assert auswahl == [z for z in zeilen if z in auswahl]
    assert cq._auswahl_mit_schwerpunkt(zeilen, 8, "")[1] == []
    # Schwerpunkt landet im Generator-Prompt und bleibt beim „Andere Idee"-Wurf
    w = _Welt([GUT, GUT + " (2)"]).install()
    assert _run(cq.sende_wett_idee(None, schwerpunkt="Filmabend")) is True
    assert w.offen["schwerpunkt"] == "Filmabend"
    _press(f"wettidee:neu:{w.offen['kennung']}")
    assert w.offen["schwerpunkt"] == "Filmabend" and len(w.dom_sends) == 2
    # Ohne Schwerpunkt bleibt alles beim Alten
    w = _Welt([GUT]).install()
    _run(cq.sende_wett_idee(None))
    assert w.offen["schwerpunkt"] == ""


def test_coach_impuls_wunsch_und_quiz_schalter():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    _Welt([]).install()
    heute = datetime.now(ZoneInfo(sched.config.TIMEZONE)).date().isoformat()
    gesendet: list = []
    patches: list = []
    alt = (sched.config.COACH_IMPULS, sched.config.COACH_IMPULS_QUIZ, sched.config.COACH_IMPULS_CHANCE,
           sched._flow_aktiv, sched.zeiten.ist_im_fenster, sched.qdrant.patch_profile_fields,
           cq.sende_wett_idee, cq.sende_spontane_frage)
    profil: dict = {}

    fehlversuche = [0]

    async def fake_wette(bot, schwerpunkt=""):
        if fehlversuche[0]:
            fehlversuche[0] -= 1
            return False
        gesendet.append(("wett_idee", schwerpunkt))
        return True

    async def fake_quiz(bot):
        gesendet.append(("coach_quiz", ""))
        return True

    async def fake_patch(user_id, fields, **kw):
        patches.append(fields)
        return "ok"

    try:
        sched.config.COACH_IMPULS = True
        sched._flow_aktiv = lambda *a, **k: False
        sched.zeiten.ist_im_fenster = lambda *a, **k: True
        sched.qdrant.get_user_profile = AsyncMock(side_effect=lambda rolle: profil)
        sched.qdrant.patch_profile_fields = fake_patch
        cq.sende_wett_idee = fake_wette
        cq.sende_spontane_frage = fake_quiz
        sched._impuls_claim = None

        # Wunsch für heute: sofort, ohne Würfel/Abstand, Wunsch wird geleert –
        # zwei verworfene Entwürfe im selben Tick verhindern den Versand nicht
        sched.config.COACH_IMPULS_CHANCE = 0.0
        fehlversuche[0] = 2
        profil.update({"coach_impuls_letzte_am": datetime.now().astimezone().isoformat(),
                       "coach_wette_wunsch": {"datum": heute, "schwerpunkt": "Filmabend"}})
        _run(sched.coach_impuls_job(None))
        assert gesendet == [("wett_idee", "Filmabend")]
        assert patches[-1]["coach_wette_wunsch"] is None and patches[-1]["coach_impuls_letzter_typ"] == "wett_idee"

        # Wunsch von gestern verfällt, nichts wird gesendet
        gesendet.clear(); patches.clear(); sched._impuls_claim = None
        profil["coach_wette_wunsch"] = {"datum": "2000-01-01", "schwerpunkt": "Filmabend"}
        _run(sched.coach_impuls_job(None))
        assert gesendet == [] and patches == [{"coach_wette_wunsch": None}]

        # Quiz-Schalter aus: auch nach einer Wette kommt kein Quiz, nur wieder Wette
        gesendet.clear(); patches.clear(); sched._impuls_claim = None
        sched.config.COACH_IMPULS_CHANCE = 1.0
        sched.config.COACH_IMPULS_QUIZ = False
        profil.clear()
        profil.update({"coach_impuls_letzter_typ": "wett_idee"})
        _run(sched.coach_impuls_job(None))
        assert gesendet == [("wett_idee", "")]
    finally:
        (sched.config.COACH_IMPULS, sched.config.COACH_IMPULS_QUIZ, sched.config.COACH_IMPULS_CHANCE,
         sched._flow_aktiv, sched.zeiten.ist_im_fenster, sched.qdrant.patch_profile_fields,
         cq.sende_wett_idee, cq.sende_spontane_frage) = alt
        sched._impuls_claim = None


def test_locale_keys():
    for key in ("COACH_IMPULS_WETTE", "BUTTON_WETTIDEE_SENDEN", "BUTTON_WETTIDEE_NEU",
                "COACH_WETTIDEE_GESENDET", "COACH_WETTIDEE_VERALTET", "COACH_WETTIDEE_FEHLER",
                "COACH_WETTIDEE_NEU_LIMIT"):
        assert t(key)
    assert "X" in t("COACH_WETTIDEE_LIMIT", begriffe="X")


def _run_alle():
    test_meta_schluss_entfernen()
    test_idee_verstoesse()
    test_vorwort_safeword_und_richtungs_regel()
    test_generieren_retry_und_abbruch()
    test_sende_wett_idee_mit_buttons()
    test_callback_senden()
    test_callback_neu_mit_deckel()
    test_impuls_reihenfolge()
    test_schwerpunkt_prompt_auswahl_und_neu()
    test_coach_impuls_wunsch_und_quiz_schalter()
    test_locale_keys()
    print("✅ Alle Wettvorschlag-Tests bestanden")


if __name__ == "__main__":
    _run_alle()
