"""
Regressions-Tests zur Log-Durchsicht zweier Tage (18.11.2026). Sieben Befunde:

  1. Bewertung einer erledigten Aufgabe: Sterne-Knöpfe unter dem Bericht statt
     eines Chat-Modes, der 30 Minuten lang jede Nachricht als Bewertung las
  2. Zeitbezug: der Aufgabentext sagt „heute Abend" – alle Prompts der
     Nachfrage-Kette und der Chat erfahren, wann die Aufgabe gestellt wurde,
     und bekommen die Zeitwörter als fertige Wendungen umgerechnet
  3. Wett-Frist: nennt die Wette einen Tag, kommt die Urteils-Frage am Tag danach
  4. Rückfrage zum Vorschlag: ohne Kategorie keine leere Klammer
  5. Wett-Ansage: kein erfundener Preis fürs Ablehnen
  6. Wett-Idee: sagt ausdrücklich, worauf gewettet wird (ein Retry, kein Ausschluss)
  7. Offene Fäden: keine Profil-Bitten und nichts, was eine Grenze berührt

    python3 tests/test_review_d13.py
"""
import asyncio
import glob
import os
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import stubs  # noqa: E402

stubs.aktivieren()

from bot import config  # noqa: E402

config.DOMINA_CHAT_ID = "111"
config.SKLAVE_CHAT_ID = "222"
config.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")

from bot import state  # noqa: E402
from bot.handlers import bewertung as bw  # noqa: E402
from bot.handlers import coach_quiz as cq  # noqa: E402
from bot.handlers import dossier, gefuehl, kommentar  # noqa: E402
from bot.handlers import sklave as sklave_h  # noqa: E402
from bot.handlers import tiny_task_feedback as tfb  # noqa: E402
from bot.messages import t  # noqa: E402
from bot.prompts import bestrafung as bp  # noqa: E402
from bot.prompts import followup as fp  # noqa: E402
from bot.prompts.followup import bausteine  # noqa: E402
from bot.services import datum_erkennung as de  # noqa: E402
from bot.services import kontext_pflege as kp  # noqa: E402
from bot.services import persona_config  # noqa: E402
from bot.services import waehrung as ws  # noqa: E402
from bot.services import zeiten  # noqa: E402
from bot.services.db import profile as db_profile, tasks as db_tasks  # noqa: E402

DOM = "111"
TZ = ZoneInfo(config.TIMEZONE)
MITTWOCH = date(2026, 11, 18)
AUFGABE = "Heute Abend den Tisch decken und Tee kochen; Belohnung ist morgen Abend ein Eis"


class _Knopf:
    def __init__(self, text, callback_data=None):
        self.text, self.callback_data = text, callback_data


class _Tasten:
    def __init__(self, zeilen):
        self.inline_keyboard = zeilen


# Ohne echtes telegram-Paket sind die Tastatur-Klassen Mocks – für die Prüfung
# der Knöpfe schlichte Ersatz-Klassen (Muster wie in test_stimmung).
bw.InlineKeyboardButton, bw.InlineKeyboardMarkup = _Knopf, _Tasten


def _vor(tage: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat()


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
        q.message.message_id = 77
    return u


def _context():
    c = MagicMock()
    c.bot.edit_message_reply_markup = AsyncMock()
    return c


def _reset():
    state.set_mode(DOM, "chat")
    s = state.get(DOM)
    for k in ("kommentar_task_id", "kommentar_msg_id"):
        s.pop(k, None)


def _knoepfe(markup) -> list[str]:
    return [b.callback_data for zeile in markup.inline_keyboard for b in zeile]


# --------------------------------------------------------------------------
# 1. Bewertung per Knopf, ohne Chat-Mode
# --------------------------------------------------------------------------

def test_sterne_knoepfe():
    tid = str(uuid.uuid4())
    daten = _knoepfe(bw.sterne_buttons(tid))
    assert daten == [f"bewertung:{n}:{tid}" for n in range(1, 6)] + [f"bewertung:kommentar:{tid}"]
    assert all(len(d.encode()) <= 64 for d in daten), "callback_data darf höchstens 64 Bytes haben"
    assert _knoepfe(bw.sterne_buttons(tid, kommentar=False)) == daten[:5]


async def test_bericht_setzt_keinen_mode():
    """Der Bericht trägt die Knöpfe; der Chat der Dom-Seite bleibt frei."""
    _reset()
    tid = str(uuid.uuid4())
    gesendet = []

    async def send_domina(bot, text, parse_mode=None, reply_markup=None, **kw):
        gesendet.append((text, reply_markup))

    prompts = []

    async def simple(prompt, **kw):
        prompts.append(prompt)
        return "Er hat es erledigt und war zufrieden."

    update_task = AsyncMock()
    with _Flicken((db_tasks, "get_tasks_by_status", AsyncMock(return_value=[])),
                  (db_tasks, "update_task", update_task),
                  (gefuehl.grok, "simple", simple),
                  (gefuehl.telegram_helper, "send_domina", send_domina)):
        await gefuehl._sende_bericht_an_domina(_context(), tid, AUFGABE, "Schön", erteilt_am=_vor(2))
    assert state.get_mode(DOM) == "chat", "kein Bewertungs-Mode mehr"
    text, markup = gesendet[0]
    assert t("GEFUEHL_BEWERTUNG_FRAGE") in text and "1-5" not in text
    assert f"bewertung:5:{tid}" in _knoepfe(markup)
    # Dass gefragt wurde, steht an der Aufgabe – nicht im flüchtigen Chat-State.
    assert update_task.await_args.args[0] == tid and bw.FELD_GEFRAGT in update_task.await_args.args[1]
    assert not any("bewertung" in k for k in state.get(DOM))
    # Punkt 2: der Bericht-Prompt kennt den Tag der Aufgabe
    assert "ZEITBEZUG" in prompts[0][1] and "„heute Abend“ → „vorgestern Abend“" in prompts[0][1]


async def test_bewertung_per_knopf():
    _reset()
    tid = str(uuid.uuid4())
    task = {"aufgabe": AUFGABE, "user_id": "sklave"}
    update_task = AsyncMock()
    notiz = AsyncMock()
    with _Flicken((db_tasks, "get_task", AsyncMock(return_value=task)),
                  (db_tasks, "update_task", update_task),
                  (db_tasks, "get_tasks_by_status", AsyncMock(return_value=[])),
                  (bw, "_vorlieben_notiz_vorschlagen", notiz)):
        u = _update(data=f"bewertung:4:{tid}")
        await bw.callback(u, _context())
        await asyncio.sleep(0)
    update_task.assert_awaited_once_with(tid, {"domina_bewertung": 4})
    q = u.callback_query
    assert "4" in q.answer.await_args.args[0]
    # Die Sterne fallen weg, der Kommentar-Knopf bleibt.
    assert _knoepfe(q.edit_message_reply_markup.await_args.kwargs["reply_markup"]) == [f"bewertung:kommentar:{tid}"]
    q.message.reply_text.assert_not_awaited()
    notiz.assert_not_awaited()
    assert state.get_mode(DOM) == "chat"

    # Fünf Sterne stoßen die Vorlieben-Notiz an.
    with _Flicken((db_tasks, "get_task", AsyncMock(return_value=dict(task))),
                  (db_tasks, "update_task", AsyncMock()),
                  (db_tasks, "get_tasks_by_status", AsyncMock(return_value=[])),
                  (bw, "_vorlieben_notiz_vorschlagen", notiz)):
        await bw.callback(_update(data=f"bewertung:5:{tid}"), _context())
        await asyncio.sleep(0)
    notiz.assert_awaited_once()


async def test_bewertung_doppelt_fremd_und_murks():
    _reset()
    tid = str(uuid.uuid4())
    update_task = AsyncMock()
    # schon bewertet: nichts wird überschrieben
    with _Flicken((db_tasks, "get_task", AsyncMock(return_value={"domina_bewertung": 2, "user_id": "sklave"})),
                  (db_tasks, "update_task", update_task)):
        u = _update(data=f"bewertung:5:{tid}")
        await bw.callback(u, _context())
    update_task.assert_not_awaited()
    assert u.callback_query.answer.await_args.args[0] == t("BEWERTUNG_SCHON")
    # Aufgabe eines anderen Paares / unbekannte Aufgabe: nichts schreiben, Knöpfe weg
    for task in ({"user_id": "7:sklave"}, None):
        with _Flicken((db_tasks, "get_task", AsyncMock(return_value=task)),
                      (db_tasks, "update_task", update_task)):
            u = _update(data=f"bewertung:3:{tid}")
            await bw.callback(u, _context())
        update_task.assert_not_awaited()
        assert u.callback_query.edit_message_reply_markup.await_args.kwargs["reply_markup"] is None
    # kaputte Daten: still
    for daten in ("bewertung:9:" + tid, "bewertung:4:keine-uuid", "bewertung"):
        u = _update(data=daten)
        await bw.callback(u, _context())
        u.callback_query.message.reply_text.assert_not_awaited()
    update_task.assert_not_awaited()


async def test_kommentar_nur_per_knopf():
    _reset()
    tid = str(uuid.uuid4())
    task = {"aufgabe": AUFGABE, "user_id": "sklave"}
    with _Flicken((db_tasks, "get_task", AsyncMock(return_value=task))):
        u = _update(data=f"bewertung:kommentar:{tid}")
        await bw.callback(u, _context())
    s = state.get(DOM)
    assert state.get_mode(DOM) == bw.MODE_KOMMENTAR and s["kommentar_task_id"] == tid
    assert s["kommentar_msg_id"] == 77
    assert u.callback_query.message.reply_text.await_args.args[0] == t("BEWERTUNG_KOMMENTAR_BITTE")

    # Der Kommentar geht raus; unter dem Bericht bleiben nur die Sterne (noch nicht bewertet).
    update_task = AsyncMock()
    send_sklave = AsyncMock()
    c = _context()
    with _Flicken((db_tasks, "get_task", AsyncMock(return_value=task)),
                  (db_tasks, "update_task", update_task),
                  (kommentar.grok, "simple", AsyncMock(return_value="Das war ordentlich, weiter so.")),
                  (kommentar.telegram_helper, "send_sklave", send_sklave)):
        u = _update(text="Sag ihm, das war ordentlich")
        await kommentar.handle(u, c)
    assert state.get_mode(DOM) == "chat" and "kommentar_task_id" not in s and "kommentar_msg_id" not in s
    send_sklave.assert_awaited_once()
    assert update_task.await_args.args[1]["domina_kommentar_original"] == "Sag ihm, das war ordentlich"
    markup = c.bot.edit_message_reply_markup.await_args.kwargs["reply_markup"]
    assert _knoepfe(markup) == [f"bewertung:{n}:{tid}" for n in range(1, 6)]

    # Läuft bei ihr gerade etwas anderes, öffnet der Knopf nichts.
    _reset()
    state.set_mode(DOM, "aufgabe_bestaetigung")
    with _Flicken((db_tasks, "get_task", AsyncMock(return_value=task))):
        u = _update(data=f"bewertung:kommentar:{tid}")
        await bw.callback(u, _context())
    assert state.get_mode(DOM) == "aufgabe_bestaetigung" and "kommentar_task_id" not in state.get(DOM)
    assert u.callback_query.message.reply_text.await_args.args[0] == t("BEWERTUNG_GERADE_BELEGT")
    _reset()


async def test_getippte_ziffer_nur_bei_offener_frage():
    """Eine nackte Ziffer zählt nur, solange zu einer Aufgabe eine Bewertungsfrage
    offen ist – und dann für die zuletzt gefragte. Der Vermerk liegt an der
    Aufgabe, übersteht also einen Neustart."""
    _reset()
    alt, neu = str(uuid.uuid4()), str(uuid.uuid4())

    def vor_stunden(h: float) -> str:
        return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()

    welt = {
        alt: {"qdrant_point_id": alt, "user_id": "sklave", bw.FELD_GEFRAGT: vor_stunden(5)},
        neu: {"qdrant_point_id": neu, "user_id": "sklave", bw.FELD_GEFRAGT: vor_stunden(1)},
        "x": {"qdrant_point_id": "x", "user_id": "sklave"},                      # nie gefragt
    }

    async def get_tasks_by_status(status, **kw):
        return list(welt.values())

    async def get_task(tid):
        return welt.get(tid)

    async def update_task(tid, felder):
        welt[tid].update(felder)

    bericht = "Er hat es erledigt und war zufrieden.\n\n" + t("GEFUEHL_BEWERTUNG_FRAGE")
    state.gesendet_abhaken(DOM, 9e12)
    with _Flicken((db_tasks, "get_task", get_task), (db_tasks, "update_task", update_task),
                  (db_tasks, "get_tasks_by_status", get_tasks_by_status)):
        # Im Chat steht gar keine Bewertungsfrage: die Ziffer gehört dem Coach
        assert await bw.per_ziffer(_update(text="4"), _context()) is False
        # … auch dann, wenn nach dem Bericht noch etwas anderes kam (die Zahl
        # könnte dann eine Antwort darauf sein)
        state.merke_gesendet(DOM, bericht)
        state.merke_gesendet(DOM, "💡 Tipp für heute: zehn Minuten früher ins Bett.")
        assert await bw.per_ziffer(_update(text="4"), _context()) is False
        assert not any("domina_bewertung" in w for w in welt.values())
        # Der Bericht ist das Letzte in ihrem Chat:
        state.merke_gesendet(DOM, bericht)
        # Text ist keine nackte Ziffer: nichts verbraucht – auch kein Auftrag geht verloren
        for text in ("Bitte gib ihm morgen eine neue Aufgabe", "4 Hiebe mehr", "6", ""):
            assert await bw.per_ziffer(_update(text=text), _context()) is False, text
        assert not any("domina_bewertung" in w for w in welt.values())
        u = _update(text=" 4 ")
        assert await bw.per_ziffer(u, _context()) is True
        assert welt[neu]["domina_bewertung"] == 4 and "domina_bewertung" not in welt[alt]
        assert "4" in u.message.reply_text.await_args.args[0]
        # die nächste Ziffer gilt der älteren, noch offenen Frage
        assert await bw.per_ziffer(_update(text="2"), _context()) is True
        assert welt[alt]["domina_bewertung"] == 2
        # nichts mehr offen: die Ziffer gehört dem Coach
        assert await bw.per_ziffer(_update(text="5"), _context()) is False
        # zu lange her
        welt[alt].pop("domina_bewertung")
        welt[alt][bw.FELD_GEFRAGT] = vor_stunden(13)
        assert await bw.per_ziffer(_update(text="5"), _context()) is False
        welt[alt][bw.FELD_GEFRAGT] = "kein-datum"
        assert await bw.per_ziffer(_update(text="5"), _context()) is False
    # Datenbank nicht erreichbar: die Ziffer geht normal weiter, nichts knallt
    with _Flicken((db_tasks, "get_tasks_by_status", AsyncMock(side_effect=RuntimeError("db weg")))):
        assert await bw.per_ziffer(_update(text="3"), _context()) is False
    state.gesendet_abhaken(DOM, 9e12)
    _reset()


def test_mode_ist_ueberall_weg():
    """Kein Code setzt oder liest den alten Mode mehr; der Knopf ist verdrahtet."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    quellen = {pf: open(pf, encoding="utf-8").read()
               for pf in glob.glob(os.path.join(root, "bot", "**", "*.py"), recursive=True)}
    treffer = [os.path.relpath(pf, root) for pf, src in quellen.items()
               if '"aufgabe_bewertung"' in src or "bewertung_task_id" in src]
    assert treffer == [], treffer
    app = "".join(src for pf, src in quellen.items() if os.sep + "app" + os.sep in pf)
    assert '("bewertung:",          paare.ROLLE_DOM)' in app
    assert 'CallbackQueryHandler(bewertung.callback,' in app and "bewertung.per_ziffer" in app
    from bot.locales import de, en
    for katalog in (de.MESSAGES, en.MESSAGES):
        assert "BEWERTUNG_1_5" not in katalog and "BEWERTUNG_KOMMENTAR_FRAGE" not in katalog
        for schluessel in ("BUTTON_BEWERTUNG_KOMMENTAR", "BEWERTUNG_NOTIERT_TOAST", "BEWERTUNG_SCHON",
                           "BEWERTUNG_GERADE_BELEGT", "BEWERTUNG_KOMMENTAR_BITTE", "TINYFB_KATEGORIE"):
            assert schluessel in katalog, schluessel
        assert "1-5" not in katalog["GEFUEHL_BEWERTUNG_FRAGE"]


# --------------------------------------------------------------------------
# 2. Zeitbezug einer Aufgabe
# --------------------------------------------------------------------------

def test_tag_label():
    h = MITTWOCH
    assert [zeiten.tag_label(n, heute=h) for n in (0, 1, 2, -1, -2)] == \
        ["heute", "gestern", "vorgestern", "morgen", "übermorgen"]
    assert zeiten.tag_label(1, "abend", h) == "gestern Abend"
    assert zeiten.tag_label(2, "Abend", h) == "vorgestern Abend"
    assert zeiten.tag_label(3, heute=h) == "am Sonntag" and zeiten.tag_label(3, "abend", h) == "am Sonntagabend"
    assert zeiten.tag_label(3, "früh", h) == "am Sonntag früh"
    assert zeiten.tag_label(10, heute=h) == "am 08.11." and zeiten.tag_label(10, "abend", h) == "am 08.11. abends"
    assert zeiten.tag_label(-1, "morgen", h) == "morgen früh" and zeiten.tag_label(0, "morgen", h) == "heute Morgen"
    assert zeiten.tage_seit(_vor(2)) == 2 and zeiten.tage_seit("") is None and zeiten.tage_seit("murks") is None
    assert zeiten.tage_seit((datetime.now(timezone.utc) + timedelta(days=3)).isoformat()) is None


def test_zeitbezug_baustein():
    z = bausteine.aufgaben_zeitbezug
    assert z(AUFGABE, 0) == "" and z(AUFGABE, None) == "" and z(AUFGABE, -1) == ""
    heute = z(AUFGABE, 0, rueckfrage=True)
    assert "am selben Tag" in heute and "→" not in heute
    zwei = z(AUFGABE, 2, heute=MITTWOCH)
    assert "vorgestern gestellt (Montag, 16.11.)" in zwei and "heute ist Mittwoch, der 18.11." in zwei
    assert "„heute Abend“ → „vorgestern Abend“; „morgen Abend“ → „gestern Abend“" in zwei
    # Die Formulierung, die live herauskam, steht nirgends mehr als Vorlage da.
    assert "vor 2 Tagen" not in zwei and ".." not in zwei
    assert "in Aussicht stellt" in zwei, "die Belohnung von gestern wird nicht neu angekündigt"
    # Gestern gestellt: „morgen" ist heute – nichts, was schon vorbei wäre.
    eins = z(AUFGABE, 1, heute=MITTWOCH)
    assert "„morgen Abend“ → „heute Abend“" in eins and "in Aussicht stellt" not in eins
    assert "am Samstagabend" in z(AUFGABE, 4, heute=MITTWOCH) and "(14.11.)" in z(AUFGABE, 4, heute=MITTWOCH)
    assert "am 08.11. abends" in z(AUFGABE, 10, heute=MITTWOCH)
    assert "RÜCKBLICKEND" in z(AUFGABE, 2, rueckfrage=True, heute=MITTWOCH)
    ohne = z("Zehn Minuten still knien", 1, heute=MITTWOCH)
    assert "gestern gestellt" in ohne and "→" not in ohne and "nicht, als wäre sie von heute" in ohne
    # Der Morgen ist kein „morgen": „heute Morgen", „jeden Morgen", „morgens", „Guten Morgen"
    morgen = z("Heute Morgen und jeden Morgen den Tee bringen, morgens pünktlich, Guten Morgen sagen; "
               "übermorgen früh berichten", 1, heute=MITTWOCH)
    assert "„heute Morgen“ → „gestern Morgen“" in morgen and "„übermorgen früh“ → „morgen früh“" in morgen
    assert "„morgen“ →" not in morgen and morgen.count("→") == 2


def test_kette_prompts_tragen_den_zeitbezug():
    alt = _vor(2)
    bauer = {
        "gefuehl_abfragen": lambda d: fp.gefuehl_abfragen(AUFGABE, erteilt_am=d),
        "reaktion_auf_gefuehl": lambda d: fp.reaktion_auf_gefuehl(AUFGABE, "Schön", "Wie war es?", erteilt_am=d),
        "reaktion_auf_nicht_erledigt": lambda d: fp.reaktion_auf_nicht_erledigt(AUFGABE, erteilt_am=d),
        "bericht_erledigt": lambda d: fp.bericht_erledigt(AUFGABE, "Schön", erteilt_am=d),
        "bericht_nicht_erledigt": lambda d: fp.bericht_nicht_erledigt(AUFGABE, erteilt_am=d),
        "bestrafungsvorschlag": lambda d: bp.bestrafungsvorschlag(AUFGABE, 0, erteilt_am=d),
        "eskalations_vorschlag": lambda d: bp.eskalations_vorschlag(AUFGABE, 3, erteilt_am=d),
    }
    for name, bau in bauer.items():
        _, user = bau(alt)
        assert "ZEITBEZUG: Die Aufgabe wurde vorgestern gestellt" in user, name
        assert "„heute Abend“ → „vorgestern Abend“" in user and "„morgen Abend“ → „gestern Abend“" in user, name
        # direkt unter dem Aufgabentext, nicht irgendwo
        assert user.index(AUFGABE) < user.index("ZEITBEZUG") < user.index(AUFGABE) + len(AUFGABE) + 12, name
        for datum in ("", _vor(0), "kein-datum"):
            assert "ZEITBEZUG" not in bau(datum)[1], (name, datum)
    # Ohne Datum (alte Aufrufer) bleibt alles wie bisher.
    assert "ZEITBEZUG" not in fp.gefuehl_abfragen(AUFGABE)[1]


def test_nachfrage_mit_fertigen_wendungen():
    _, user = fp.followup_frage(AUFGABE, tage_her=2)
    assert "RÜCKBLICKEND" in user and "„heute Abend“ → „vorgestern Abend“" in user
    assert "vor 2 Tagen Abend" not in user
    _, user = fp.followup_frage(AUFGABE, tage_her=0)
    assert "am selben Tag" in user


def test_offene_aufgaben_im_chat_datiert():
    assert sklave_h._offene_aufgaben_zeilen([]) == "Keine offenen Aufgaben."
    frisch = sklave_h._offene_aufgaben_zeilen([{"aufgabe": "Heute Abend abwaschen", "erteilt_am": _vor(0)}])
    assert frisch == "- Heute Abend abwaschen"
    text = sklave_h._offene_aufgaben_zeilen([
        {"aufgabe": "Heute Abend abwaschen", "erteilt_am": _vor(2)},
        {"aufgabe": "Die Schuhe putzen", "erteilt_am": _vor(1)},
        {"aufgabe": "Ohne Datum"},
    ])
    zeilen = text.split("\n")
    assert zeilen[0] == "- [gestellt vorgestern – heute heißt das: „heute Abend“ → „vorgestern Abend“] Heute Abend abwaschen"
    assert zeilen[1] == "- [gestellt gestern] Die Schuhe putzen" and zeilen[2] == "- Ohne Datum"
    assert "Umrechnung aus der Klammer" in zeilen[3] and len(zeilen) == 4


# --------------------------------------------------------------------------
# 3. Wett-Frist
# --------------------------------------------------------------------------

def _frist(idee: str, angeboten, jetzt) -> tuple[str, int]:
    iso, tage = ws.wett_frist(idee, angeboten.astimezone(timezone.utc).isoformat() if angeboten else "", jetzt)
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%d.%m. %H:%M"), tage


def test_wett_frist_nach_dem_genannten_tag():
    zeit = lambda tag, h, m=0: datetime(2026, 11, tag, h, m, tzinfo=TZ)   # noqa: E731
    with _Flicken((persona_config, "zeit", lambda schluessel: "17:30")):
        abend = "Morgen Abend liest er dir zehn Minuten vor, ohne zu stocken"
        # Abends angeboten und angenommen: nicht morgen um 17:30 (vor dem Abend), sondern übermorgen.
        assert _frist(abend, zeit(17, 22, 26), zeit(17, 22, 27)) == ("19.11. 17:30", 2)
        # Erst am nächsten Morgen angenommen: „morgen" bleibt derselbe Abend.
        assert _frist(abend, zeit(17, 23, 50), zeit(18, 7)) == ("19.11. 17:30", 1)
        assert _frist("Wer heute Abend länger durchhält", zeit(17, 19), zeit(17, 19, 5)) == ("18.11. 17:30", 1)
        # Der genannte Tag ist bei der Annahme schon vorbei: nächste Nachfrage-Zeit, nie Vergangenheit.
        assert _frist("Wer heute Abend länger durchhält", zeit(17, 19), zeit(18, 19)) == ("19.11. 17:30", 1)
        assert _frist("Wer heute Abend länger durchhält", zeit(17, 19), zeit(18, 9)) == ("18.11. 17:30", 0)
        # Eine Dauer zählt ab der Annahme – nach der Nachfrage-Zeit ein Tag mehr.
        assert _frist("die nächsten 24 Stunden", zeit(17, 22), zeit(17, 22, 5)) == ("19.11. 17:30", 2)
        assert _frist("die nächsten 24 Stunden", zeit(17, 9), zeit(17, 9, 5)) == ("18.11. 17:30", 1)
        assert _frist("Wer zuerst lacht, verliert", zeit(17, 22), zeit(17, 22, 5)) == ("19.11. 17:30", 2)
        # Ohne (lesbares) Angebots-Datum zählt der Tag der Annahme.
        assert _frist("Bis morgen Abend", None, zeit(17, 22, 5)) == ("19.11. 17:30", 2)


def test_heute_morgen_ist_kein_termin():
    h = date(2026, 7, 22)
    assert de.finde_termin("Heute Morgen war er frech, dafür räumt er auf", h) is None
    assert de.finde_termin("Morgen soll er früh aufstehen", h)[0] == date(2026, 7, 23)
    assert de.finde_spaetesten_termin("von morgen früh bis übermorgen früh", h) == date(2026, 7, 24)
    assert de.finde_spaetesten_termin("Heute Abend, jeden Morgen, am Morgen", h) is None
    assert de.finde_spaetesten_termin("", h) is None


# --------------------------------------------------------------------------
# 4. Rückfrage zum Vorschlag ohne Kategorie
# --------------------------------------------------------------------------

async def test_rueckfrage_ohne_leere_klammer():
    gesendet = []

    async def send_domina(bot, text, parse_mode=None, reply_markup=None, **kw):
        gesendet.append(text)

    with _Flicken((tfb.telegram_helper, "send_domina", send_domina)):
        pid = str(uuid.uuid4())
        await tfb.frage_stellen(object(), {"qdrant_point_id": pid, "inhalt": "Ein Vorschlag.", "kategorien": []})
        await tfb.frage_stellen(object(), {"qdrant_point_id": pid, "inhalt": "Ein Vorschlag.",
                                           "kategorien": [], "kategorie": "allgemein"})
        await tfb.frage_stellen(object(), {"qdrant_point_id": pid, "inhalt": "Ein Vorschlag.",
                                           "kategorien": ["Haltungs_Training", "Dienst"]})
        await tfb.frage_stellen(object(), {"qdrant_point_id": pid, "inhalt": "Ein Vorschlag.", "kategorie": "Dienst"})
    for text in gesendet[:2]:
        assert "Vorschlag:\n" in text and "Kategorie" not in text and "__" not in text, text
    assert "Vorschlag (Kategorie: _Haltungs Training, Dienst_):\n" in gesendet[2]
    assert "Vorschlag (Kategorie: _Dienst_):\n" in gesendet[3]


# --------------------------------------------------------------------------
# 5. + 6. Wette: kein Preis fürs Ablehnen, der Sieger steht da
# --------------------------------------------------------------------------

def test_wett_prompts():
    system, _ = fp.wette_an_sklaven("Er wettet, dass er bis morgen still ist.", einsatz=50)
    assert "Nenne also keinen Preis und keine Strafe fürs Ablehnen" in system
    assert "und was, entscheidest du" not in system
    assert "bei welchem Ausgang" in system
    system, _ = fp.wett_idee(["Tee ans Bett"], [], ["Lesen"], [])
    assert "wettet, dass" in system and "sind keine Wette" in system
    letzte = [{"datum": "2026-11-10", "ergebnis": "gewonnen", "idee": f"Er schafft es {n}mal."} for n in ("drei", "vier", "fünf")]
    system, _ = fp.wett_idee(["Tee ans Bett"], [], ["Lesen"], [], letzte_wetten=letzte)
    assert "KEINE Zähl-Wette" in system and "Auch dann steht ausdrücklich da, worauf er wettet" in system


def test_sieger_unklar():
    unklar = ("Morgen Abend würfelt ihr einmal. Bei einer geraden Zahl bekommt er Tee ans Bett. "
              "Bei einer ungeraden bestimmst du den Film.")
    assert cq._sieger_unklar(unklar)
    for klar in ("Er wettet, dass er bis morgen Abend still bleibt. Bleibt er still, bekommt er Tee.",
                 "Bleibt er bis Freitag still, hat er gewonnen und bekommt Tee; sonst bekommst du den Abwasch.",
                 "Schafft er es bis morgen, bekommt er Tee. Scheitert er, macht er den Abwasch.",
                 "Wer zuerst lacht, verliert: Lacht er, macht er den Abwasch.",
                 "Hält er das durch, bekommt er Tee."):
        assert not cq._sieger_unklar(klar), klar
    assert cq._sieger_unklar("")


# --------------------------------------------------------------------------
# 7. Offene Fäden
# --------------------------------------------------------------------------

def test_profil_bitte():
    for text in ("Könntest du mein Profil ergänzen? Ich wäre gern mal eine lebende Fußbank",
                 "Trag es bitte ein", "Kannst du unter Vorlieben notieren, dass ich gern Tee serviere",
                 "trag das bitte in mein Profil ein", "Nimm das in meine Vorlieben auf",
                 "streich bitte Kitzeln aus meinen Vorlieben", "Bitte füge das noch hinzu", "speicher das bitte",
                 "kannst du das hinzufügen zu meinen grenzen"):
        assert kp.ist_profil_bitte(text), text
    for text in ("Ich hatte heute Stress im Büro und freue mich auf den Abend",
                 "Ich würde gern mal wieder gefesselt werden", "Füge dich, sagst du immer",
                 "Das nehme ich gern auf mich", "Meine Grenzen kennst du ja",
                 "Meine Grenzen ändern sich gerade, ich bin unsicher",
                 "Zu meinen Vorlieben gehört, dass ich das Halsband trage",
                 "Bitte sei heute sanft zu mir", "Nimm mich bitte ernst", ""):
        assert not kp.ist_profil_bitte(text), text


async def test_offene_faeden_filtern_quelle_und_ergebnis():
    """Profil-Bitten und alles, was eine Grenze berührt, kommen gar nicht erst
    beim Modell an; berührt ein fertiger Faden eine Grenze, fällt er weg."""
    eintraege = [
        {"sklave_nachricht": "Kannst du unter Vorlieben notieren, dass ich gern Tee serviere", "datum": _vor(1)},
        {"sklave_nachricht": "Trag es bitte ein", "datum": _vor(1)},
        {"sklave_nachricht": "Ich würde gern mal Nadeln ausprobieren", "datum": _vor(1)},
        {"sklave_nachricht": "Morgen habe ich ein schwieriges Gespräch im Büro", "datum": _vor(1)},
    ]
    profile = {"sklave": {"hard_limits": ["Nadeln"]}, "domina": {"grenzen": ["Kitzeln"]}}
    prompts = []
    gespeichert = {}

    async def simple(prompt, **kw):
        prompts.append(prompt)
        return "- hat morgen ein schwieriges Gespräch im Büro\n- will von der Herrin gekitzelt werden"

    async def get_profile(uid):
        return dict(profile[uid])

    async def patch(uid, fields, **kw):
        gespeichert.update(fields)

    with _Flicken((dossier.emb, "get_embedding", AsyncMock(return_value=[0.0])),
                  (dossier.qdrant, "get_hybrid_conversation_context", AsyncMock(return_value=eintraege)),
                  (db_tasks, "get_tasks_by_status", AsyncMock(return_value=[
                      {"gefuehl": "Die Nadeln im Kopfkino haben mich nervös gemacht"},
                      {"gefuehl": "Das Warten war das Schwerste daran"}])),
                  (db_profile, "get_user_profile", get_profile),
                  (db_profile, "patch_profile_fields", patch),
                  (dossier.grok, "simple", simple)):
        faeden = await dossier.aktualisiere_offene_faeden()
    eingabe = prompts[0]
    assert "schwieriges Gespräch" in eingabe and "Das Warten war das Schwerste" in eingabe
    for raus in ("notieren", "Trag es", "Nadeln"):
        assert raus not in eingabe, raus
    assert faeden == ["hat morgen ein schwieriges Gespräch im Büro"] == gespeichert["offene_faeden"]


if __name__ == "__main__":
    test_sterne_knoepfe()
    asyncio.run(test_bericht_setzt_keinen_mode())
    asyncio.run(test_bewertung_per_knopf())
    asyncio.run(test_bewertung_doppelt_fremd_und_murks())
    asyncio.run(test_kommentar_nur_per_knopf())
    asyncio.run(test_getippte_ziffer_nur_bei_offener_frage())
    test_mode_ist_ueberall_weg()
    test_tag_label()
    test_zeitbezug_baustein()
    test_kette_prompts_tragen_den_zeitbezug()
    test_nachfrage_mit_fertigen_wendungen()
    test_offene_aufgaben_im_chat_datiert()
    test_wett_frist_nach_dem_genannten_tag()
    test_heute_morgen_ist_kein_termin()
    asyncio.run(test_rueckfrage_ohne_leere_klammer())
    test_wett_prompts()
    test_sieger_unklar()
    test_profil_bitte()
    asyncio.run(test_offene_faeden_filtern_quelle_und_ergebnis())
    print("✅ Alle Review-D13-Tests bestanden")
