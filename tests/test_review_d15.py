"""
Regressions-Tests zur Log-Durchsicht vom 21.11.2026. Drei Befunde:

  1. Antwort-Knöpfe der Stimmungsfrage: der Klartext zu einem Knopf sagt nicht
     mehr als der Knopf – Prompt-Regel (Form „Thema: Aussage"), Netz darunter
     (Befund-/Verstärkungs-Wörter, die weder Knopf noch Frage decken), ein
     Nachbesserungs-Versuch, sonst fällt der überzogene Klartext weg
  2. Hinweis an die andere Seite: Frage und Antwort stehen wörtlich im Kopf,
     nicht die Deutung; der Gedanke darunter bleibt beim Gesagten
  3. Zutaten-Sperrliste kennt Gesten unabhängig vom Stoff (Tropfen aufs
     Gesicht, stumme Zeichen per Finger)
  4. Netz unter dem Gedanken im Hinweis: keine behaupteten Gewohnheiten („das
     hilft meistens"), keine Rückfrage am Ende
  5. Machbarkeits-Prüfung: ein Anlass-Stichwort hinter einem Inventar-Eintrag
     ist kein Verbot anderer Verwendungen; Rollen, Mechanik und ausdrückliche
     Einschränkungen bleiben bindend (das Urteil selbst fällt das Modell – hier
     ist nur der Auftrag prüfbar)

Alle Beispiele sind erfunden (Muster, keine Live-Inhalte).

    python3 tests/test_review_d15.py
"""
import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import stubs  # noqa: E402

stubs.aktivieren()

from bot import config  # noqa: E402

config.DOMINA_CHAT_ID = "111"
config.SKLAVE_CHAT_ID = "222"
config.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")

from bot import state  # noqa: E402
from bot.handlers import stimmung as st  # noqa: E402
from bot.prompts import followup as fp  # noqa: E402
from bot.prompts import vanilla as vp  # noqa: E402
from bot.scheduler import schablonen  # noqa: E402
from bot.services.db import progress as db_progress, vorschlaege as db_vorschlaege  # noqa: E402

FRAGE = "Was geht dir heute am ehesten durch den Sinn?"
UEBERZOGEN = ('{"antworten": ['
              '{"knopf": "Dies und das", "klartext": "Gedanken: zerstreut, überfordert"},'
              '{"knopf": "Der Haushalt", "klartext": "Gedanken: starke Belastung im Haushalt"},'
              '{"knopf": "Kaum etwas", "klartext": "Gedanken: wenig"},'
              '{"knopf": "Gar nichts", "klartext": "Gedanken: keine"}]}')
SAUBER = ('{"antworten": ['
          '{"knopf": "Dies und das", "klartext": "Gedanken: nichts Bestimmtes"},'
          '{"knopf": "Der Haushalt", "klartext": "Gedanken: Haushalt"},'
          '{"knopf": "Kaum etwas", "klartext": "Gedanken: wenig"},'
          '{"knopf": "Gar nichts", "klartext": "Gedanken: keine"}]}')


class _Knopf:                          # telegram ist lokal ein Stub
    def __init__(self, text, callback_data=None):
        self.text, self.callback_data = text, callback_data


class _Tasten:
    def __init__(self, zeilen):
        self.inline_keyboard = zeilen


# --------------------------------------------------------------------------
# 1 – Klartext sagt nicht mehr als der Knopf
# --------------------------------------------------------------------------

def test_prompt_klartext_uebersetzt_nur():
    system, user = fp.stimmung_antworten(FRAGE)
    assert "NICHT MEHR als die Antwort" in system and "dann ein Doppelpunkt" in system, system
    assert "keine Verstärkung" in system and "kein Befund" in system
    assert "Bleibt die Antwort unbestimmt" in system and "nie das Bild selbst" in system
    # Die alte Vorgabe verlangte eine Aussage über die Stimmung – auch wo die Antwort keine macht.
    assert "was sie über die Stimmung sagt" not in system
    assert "mehr hineingelegt" not in user
    # Nachbessern nach zu viel Deutung nennt die Wörter; der Form-Hinweis bleibt der Form vorbehalten.
    nach = fp.stimmung_antworten(FRAGE, nachbessern=True, zu_viel=["überfordert", "starke"])[1]
    assert "mehr hineingelegt" in nach and "überfordert, starke" in nach and "unbrauchbar" not in nach
    assert "unbrauchbar" in fp.stimmung_antworten(FRAGE, nachbessern=True)[1]
    assert "unbrauchbar" in fp.stimmung_antworten(FRAGE, nachbessern=True, zu_viel=[])[1]


def test_netz_sagt_mehr():
    f = st._sagt_mehr
    # Befund, den weder Knopf noch Frage nennen
    assert f("Dies und das", "Gedanken: zerstreut, überfordert", FRAGE) == ["überfordert"]
    assert f("Der Haushalt", "Gedanken: Arbeitsbelastung", FRAGE) == ["Arbeitsbelastung"]
    assert f("Die Zukunft", "Gedanken: Zukunftsangst", FRAGE) == ["Zukunftsangst"]
    assert f("Eher grau", "Stimmung: traurig", FRAGE) and f("Weiß nicht", "Stimmung: ziellos", FRAGE)
    # Verstärkung ohne Deckung auf dem Knopf
    assert f("Geht so", "Stimmung: sehr schlecht", FRAGE) == ["sehr"]
    assert f("Der Haushalt", "starke Unruhe", FRAGE) == ["starke"]
    # Bleibt beim Gesagten
    assert f("Dies und das", "Gedanken: nichts Bestimmtes", FRAGE) == []
    assert f("Der Haushalt", "Gedanken: Haushalt", FRAGE) == []
    assert f("Eher grau", "Stimmung: gedrückt, müde", FRAGE) == []          # Alltagswörter bleiben frei
    # Gedeckt: derselbe Befund steht auf dem Knopf (auch in einer Zusammensetzung) oder in der Frage
    assert f("Geldsorgen", "Gedanken: Sorgen ums Geld", FRAGE) == []
    assert f("Mache mir Sorgen", "Gedanken: besorgt", FRAGE) == []
    assert f("Bin überfordert", "Stimmung: überlastet", FRAGE) == []
    assert f("Ja, einiges", "Sorgen: mehrere", "Was macht dir heute Sorgen?") == []
    # Wer selbst zuspitzt, darf zugespitzt wiedergegeben werden
    assert f("Ziemlich mies", "Stimmung: sehr schlecht", FRAGE) == []
    assert f("Gar nichts", "Gedanken: völlig leer", FRAGE) == []
    # Verneint oder nur ähnlich klingend
    assert f("Nichts Schlimmes", "Gedanken: keine Sorgen", FRAGE) == []
    assert f("Nichts Schlimmes", "Sorgen: keine", FRAGE) == []
    assert f("Nichts Schlimmes", "unbelastet, sorgenfrei, angstfrei", FRAGE) == []
    assert f("Zu zweit", "Lust: gemeinsame Zeit, Fürsorge", FRAGE) == []
    assert f("Bin durch", "Tag: längst erledigt", FRAGE) == []
    # Eine Verneinung in einem ANDEREN Satzteil entlastet nicht
    assert f("Geht so", "nicht entspannt, Sorgen", FRAGE) == ["Sorgen"]
    # Englisch
    assert f("A lot", "Mood: very anxious", "What's on your mind?") == ["anxious", "very"]
    assert f("Not much", "Mood: calm", "What's on your mind?") == []
    assert f("", "", "") == [] and f("Knopf", None, None) == []


async def test_nachbessern_und_letzter_halt():
    orig = st.grok.simple
    aufrufe = []
    try:
        # Erster Versuch überzogen → zweiter mit den Wörtern im Auftrag, sauber → der gilt
        folge = [UEBERZOGEN, SAUBER]

        async def _erst_ueberzogen(prompt, **kw):
            aufrufe.append(prompt[1])
            return folge.pop(0)
        st.grok.simple = _erst_ueberzogen
        o = await st._antwort_optionen(FRAGE)
        assert [x["klartext"] for x in o] == ["Gedanken: nichts Bestimmtes", "Gedanken: Haushalt",
                                              "Gedanken: wenig", "Gedanken: keine"], o
        assert len(aufrufe) == 2 and "mehr hineingelegt" not in aufrufe[0]
        assert "mehr hineingelegt" in aufrufe[1] and "überfordert" in aufrufe[1] and "starke" in aufrufe[1]

        # Bleibt es überzogen: Knöpfe bleiben, nur die überzogenen Klartexte fallen weg
        aufrufe.clear()

        async def _immer_ueberzogen(prompt, **kw):
            aufrufe.append(prompt[1])
            return UEBERZOGEN
        st.grok.simple = _immer_ueberzogen
        o = await st._antwort_optionen(FRAGE)
        assert len(aufrufe) == st._OPTIONEN_VERSUCHE
        assert [x["knopf"] for x in o] == ["Dies und das", "Der Haushalt", "Kaum etwas", "Gar nichts"]
        assert [x["klartext"] for x in o] == ["", "", "Gedanken: wenig", "Gedanken: keine"], o

        # Überzogen, dann fällt das Modell aus: der erste Satz gilt – ohne die überzogenen Klartexte
        folge = [UEBERZOGEN]

        async def _dann_weg(prompt, **kw):
            if not folge:
                raise RuntimeError("llm weg")
            return folge.pop(0)
        st.grok.simple = _dann_weg
        o = await st._antwort_optionen(FRAGE)
        assert [x["klartext"] for x in o] == ["", "", "Gedanken: wenig", "Gedanken: keine"], o

        # Überzogen, dann unbrauchbare Form: ebenso
        folge = [UEBERZOGEN, "kein json"]

        async def _dann_kaputt(prompt, **kw):
            return folge.pop(0)
        st.grok.simple = _dann_kaputt
        o = await st._antwort_optionen(FRAGE)
        assert len(o) == 4 and o[0]["klartext"] == "" and o[2]["klartext"] == "Gedanken: wenig", o
    finally:
        st.grok.simple = orig


# --------------------------------------------------------------------------
# 2 – Hinweis an die andere Seite: Frage und Antwort wörtlich
# --------------------------------------------------------------------------

def test_hinweis_text():
    mit = st._hinweis_text("STIMMUNG_HINWEIS_AN_DOMINA", "Ein Gedanke.", "Dies und das", FRAGE,
                           "Gedanken: nichts Bestimmtes")
    assert f"Frage: _{FRAGE}_" in mit and "Antwort: _Dies und das_" in mit and mit.endswith("Ein Gedanke."), mit
    assert "nichts Bestimmtes" not in mit, "die Deutung steht nicht im Kopf"
    # Markdown-Zeichen und Zeilenumbrüche aus Frage/Antwort brechen das Template nicht
    roh = st._hinweis_text("STIMMUNG_HINWEIS_AN_DOMINA", "x", "so_la*la", "Wie ist\ndein _Tag_?", "")
    assert "Frage: _Wie ist dein Tag?_" in roh and "Antwort: _solala_" in roh, roh
    # Ohne Frage (feste Knöpfe, Meldung aus eigenem Antrieb): die eine Zeile wie bisher
    ohne = st._hinweis_text("STIMMUNG_HINWEIS_AN_DOMINA", "Ein Gedanke.", "okay – neutral")
    assert "Frage:" not in ohne and "_okay – neutral_" in ohne, ohne
    partner = st._hinweis_text("STIMMUNG_HINWEIS_AN_PARTNER", "Ein Gedanke.", "Dies und das", FRAGE, "", name="Kim")
    assert "Stimmung von Kim" in partner and f"Frage: _{FRAGE}_" in partner, partner
    assert "Stimmung von Kim: _müde_" in st._hinweis_text("STIMMUNG_HINWEIS_AN_PARTNER", "x", "müde", name="Kim")


async def test_tipp_speichert_gesagtes_und_hinweis_zeigt_frage():
    """Ganzer Weg: überzogener Klartext fällt weg → gespeichert wird das
    Etikett; der Hinweis zeigt Frage und Antwort, der Auftrag dazu verlangt,
    beim Gesagten zu bleiben."""
    sub = config.SKLAVE_CHAT_ID
    orig = (st.InlineKeyboardButton, st.InlineKeyboardMarkup, db_progress.save_training,
            db_progress.get_training_by_id, db_vorschlaege.get_recent_stimmung_eintraege, st.grok.simple,
            st.telegram_helper.send_domina, config.STIMMUNG_ENABLED)
    st.InlineKeyboardButton, st.InlineKeyboardMarkup = _Knopf, _Tasten
    gespeichert, ids, prompts = [], [], []

    async def _save(rolle, daten, point_id=None):
        gespeichert.append(dict(daten))
        ids.append(point_id)
        return point_id or "neu"

    async def _simple(prompt, **kw):
        system = prompt[0] if isinstance(prompt, tuple) else kw.get("system", "")
        user = prompt[1] if isinstance(prompt, tuple) else prompt
        prompts.append((system, user))
        if "Antworten zum Antippen. Du schreibst" in system:
            return UEBERZOGEN
        if "Formuliere jetzt genau EINE solche Frage" in user:
            return FRAGE
        return "Kurzer Gedanke."

    db_progress.save_training = _save
    db_progress.get_training_by_id = AsyncMock(
        side_effect=lambda pid, user_id="domina": next(
            (d for d, i in zip(gespeichert, ids) if i == pid and d["typ"] == "stimmung_frage"), None))
    db_vorschlaege.get_recent_stimmung_eintraege = AsyncMock(return_value=[])
    st.grok.simple = _simple
    st.telegram_helper.send_domina = AsyncMock()
    config.STIMMUNG_ENABLED = True

    def _tap(daten, tasten, mid):
        upd = MagicMock()
        upd.effective_chat.id = sub
        q = upd.callback_query
        q.data = daten
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.edit_message_reply_markup = AsyncMock()
        q.message.message_id = mid
        q.message.text = FRAGE
        q.message.reply_markup = tasten
        q.message.reply_text = AsyncMock()
        return upd

    try:
        state._state.clear()
        bot = MagicMock()
        bot.send_message = AsyncMock()
        ctx = MagicMock()
        ctx.bot = bot
        await st.frage_stellen(bot)
        tasten = bot.send_message.call_args.kwargs["reply_markup"]
        knoepfe = [z[0] for z in tasten.inline_keyboard]
        assert [k.text for k in knoepfe] == ["Dies und das", "Der Haushalt", "Kaum etwas", "Gar nichts"]

        # Tipp auf die erste Antwort: ihr Klartext war überzogen und ist weggefallen
        await st.callback_button(_tap(knoepfe[0].callback_data, tasten, 91), ctx)
        stimmung = [d for d in gespeichert if d["typ"] == "stimmung"]
        assert stimmung == [{"typ": "stimmung", "zusammenfassung": st._mit_frage("Dies und das", FRAGE),
                             "frage": FRAGE}], stimmung
        hinweis = next(p for p in prompts if "Die Frage an ihn" in p[1])
        assert fp.STIMMUNG_BEIM_GESAGTEN in hinweis[0] and "Gemeint ist damit" not in hinweis[1], hinweis
        gesendet = st.telegram_helper.send_domina.await_args.args[1]
        assert f"Frage: _{FRAGE}_" in gesendet and "Antwort: _Dies und das_" in gesendet, gesendet
        assert "überfordert" not in gesendet and "überfordert" not in hinweis[1]

        # Tipp auf eine Antwort mit sauberem Klartext: der wird gespeichert, im Kopf steht trotzdem das Etikett
        prompts.clear()
        await st.frage_stellen(bot)
        tasten = bot.send_message.call_args.kwargs["reply_markup"]
        knoepfe = [z[0] for z in tasten.inline_keyboard]
        await st.callback_button(_tap(knoepfe[2].callback_data, tasten, 92), ctx)
        assert [d for d in gespeichert if d["typ"] == "stimmung"][-1] == {
            "typ": "stimmung", "zusammenfassung": "Gedanken: wenig", "frage": FRAGE, "knopf": "Kaum etwas"}
        gesendet = st.telegram_helper.send_domina.await_args.args[1]
        assert "Antwort: _Kaum etwas_" in gesendet and "Gedanken: wenig" not in gesendet, gesendet
        assert "Gemeint ist damit: Gedanken: wenig" in next(p for p in prompts if "Die Frage an ihn" in p[1])[1]
    finally:
        state._state.clear()
        (st.InlineKeyboardButton, st.InlineKeyboardMarkup, db_progress.save_training,
         db_progress.get_training_by_id, db_vorschlaege.get_recent_stimmung_eintraege, st.grok.simple,
         st.telegram_helper.send_domina, config.STIMMUNG_ENABLED) = orig


def test_vanilla_hinweis_bleibt_beim_gesagten():
    orig = (vp.person, vp.stimme_block, vp.kurz_stil)
    vp.person = lambda rolle: {"anrede": "Kim", "fem": True, "dat": "ihr", "nom": "sie", "name_dat": "Kim",
                               "name": "Kim", "poss": "ihr"}
    vp.stimme_block = lambda rolle: "STIMME"
    vp.kurz_stil = lambda: "STIL"
    try:
        system, user = vp.stimmung_hinweis("sklave", "Dies und das", FRAGE, "")
        assert fp.STIMMUNG_BEIM_GESAGTEN in system and FRAGE in user, system
        assert fp.MITTEILUNG_OHNE_RUECKFRAGE in system
    finally:
        vp.person, vp.stimme_block, vp.kurz_stil = orig


# --------------------------------------------------------------------------
# 3 – Zutaten-Sperrliste: Gesten unabhängig vom Stoff
# --------------------------------------------------------------------------

def _labels(texte, kategorien=("Dienst",)):
    return " | ".join(schablonen._verbrauchte_zutaten(list(texte), list(kategorien)))


def test_zutaten_gesten():
    # dieselbe Geste mit wechselndem Stoff – die Stoff-Zutat allein sähe nur die erste
    for satz in ("Lass etwas Speichel auf sein Gesicht tropfen.",
                 "Vom Eiswürfel fällt ein Tropfen auf seine Stirn.",
                 "Der Schwamm schwebt über seiner Wange. Dann fallen Tropfen.",
                 "Langsam tropft es auf sein Gesicht."):
        assert "Tropfen aufs Gesicht" in _labels([satz]), satz
    assert "Spucken/Speichel" not in _labels(["Vom Eiswürfel fällt ein Tropfen auf seine Stirn."])
    # Tropfen ohne Gesicht bleiben frei
    for satz in ("Das Wachs tropft langsam auf seinen Rücken.",
                 "Er trinkt das Glas bis zum letzten Tropfen aus.",
                 "Stirn an die Wand. Danach tropft das Wasser vom Hahn."):
        assert "Tropfen aufs Gesicht" not in _labels([satz]), satz
    # heute bewusst gewählte Kategorie legitimiert die Geste
    assert "Tropfen aufs Gesicht" not in _labels(["Lass ihm Tropfen aufs Gesicht fallen."], ["Speichelspiel"])

    for satz in ("Drei Finger an seiner Schulter bedeuten: aufstehen.",
                 "Ein gestreckter Finger heißt Pause.",
                 "Wenn sie den Finger hebt, verstummt er.",
                 "Ein Tippen auf die Schulter beendet die Runde.",
                 "Du klopfst ihm zweimal auf die Schulter.",
                 "Auf dein Fingerschnippen hin kniet er."):
        assert "Stumme Zeichen" in _labels([satz]), satz
    for satz in ("Erst ein Finger, dann der zweite, langsam.",
                 "Mit zwei Fingern prüfst du, ob der Knoten hält.",
                 "Er legt die Hände auf die Schultern und wartet."):
        assert "Stumme Zeichen" not in _labels([satz]), satz
    # nur die letzten Volltexte zählen, die der Aufrufer übergibt
    assert _labels(["Etwas ganz anderes."]) == ""


# --------------------------------------------------------------------------
# 4 – Netz unter dem Gedanken: keine Gewohnheiten, keine Rückfrage am Ende
# --------------------------------------------------------------------------

KOPF = "Er ist heute eher still, viel kommt da nicht. "


def test_gewohnheiten_fallen_weg():
    f = schablonen._gewohnheiten_entfernen
    # mitten im Teilsatz: nur das Wort
    assert f(KOPF + "Lass ihn kurz ankommen – das hilft ihm meistens sofort.") == \
        KOPF + "Lass ihn kurz ankommen – das hilft ihm sofort."
    assert f(KOPF + "Das reicht oft schon, um ihn zum Reden zu bringen.") == \
        KOPF + "Das reicht schon, um ihn zum Reden zu bringen."
    assert f("Er ist mal wieder knapp angebunden, viel kommt da heute nicht von ihm.") == \
        "Er ist knapp angebunden, viel kommt da heute nicht von ihm."
    assert f("Das klingt bei ihm meistens nach einem vollen Kopf, mehr steht da nicht.") == \
        "Das klingt nach einem vollen Kopf, mehr steht da nicht."
    assert f("Er wirkt müder als sonst, viel Kraft ist nicht mehr da. Mach es ihm leicht, wie immer mit einem klaren Satz.") == \
        "Er wirkt müder, viel Kraft ist nicht mehr da. Mach es ihm leicht, mit einem klaren Satz."
    # am Satzanfang: der Teilsatz fällt weg, der Rest beginnt groß
    assert f(KOPF + "Meistens hilft ihm dann etwas Ruhe. Lass ihn heute bei dir sitzen.") == \
        KOPF + "Lass ihn heute bei dir sitzen."
    assert f(KOPF + "Meistens hilft ihm dann Ruhe, also lass ihn heute bei dir sitzen.") == \
        KOPF + "Also lass ihn heute bei dir sitzen."
    assert f(KOPF + "Normalerweise, wenn er so antwortet, braucht er Ruhe.") == \
        KOPF + "Wenn er so antwortet, braucht er Ruhe."
    # Adverb am Anfang eines Teilsatzes: der Teilsatz fällt weg
    assert f("Er wirkt entspannt und zufrieden mit dem Tag – meistens will er dann reden. Lass ihn bei dir sitzen.") == \
        "Er wirkt entspannt und zufrieden mit dem Tag. Lass ihn bei dir sitzen."
    # Berufung auf ihre Gewohnheit oder ihr Wissen: ganzer Teilsatz
    assert f(KOPF + "Hak direkt nach, ohne Druck – so wie du das bei ihm sonst auch machst. Mehr braucht es nicht.") == \
        KOPF + "Hak direkt nach, ohne Druck. Mehr braucht es nicht."
    assert f(KOPF + "Nimm ihn dir heute Abend einfach vor, du kennst ihn ja. Mehr braucht es nicht.") == \
        KOPF + "Nimm ihn dir heute Abend einfach vor. Mehr braucht es nicht."
    # kein Gewohnheits-Wort: „zu oft" ist ein Rat, „am meisten" ein Vergleich
    for satz in (KOPF + "Frag nicht zu oft nach, einmal reicht völlig.",
                 KOPF + "Frag ihn, was ihn am meisten beschäftigt, und hör dann einfach zu.",
                 KOPF + "Sonst macht er dicht, also bleib freundlich und kurz."):
        assert f(satz) == satz, f(satz)
    # Englisch
    assert f("He sounds tired today. That usually gets him talking, so just ask him directly.") == \
        "He sounds tired today. That gets him talking, so just ask him directly."
    # bliebe zu wenig übrig, bleibt alles
    assert f("Meistens hilft ihm Ruhe.") == "Meistens hilft ihm Ruhe." and f("") == "" and f(None) == ""


def test_rueckfrage_am_ende_faellt_weg():
    f = schablonen._rueckfrage_am_ende_entfernen
    kern = "Er hat heute kaum noch Kraft, sagt er. Gönn ihm eine ruhige Stunde auf dem Sofa."
    assert f(kern + " Wie willst du es angehen?") == kern
    assert f(kern + "\n\nWas meinst du? 😏") == kern                      # Zeichen dahinter gehen mit
    assert f(kern + " Soll er danach noch etwas tun oder lieber nicht? Oder ganz anders?") == kern
    # bleiben: Vorschlag in Frageform, Frage in Anführungszeichen, Aufforderung mit Fragezeichen
    for text in ("Er hat heute kaum noch Kraft, sagt er. Wie wär's, wenn du ihm eine ruhige Stunde auf dem Sofa gönnst?",
                 "Er hat heute kaum noch Kraft, sagt er. Hol ihn mit einem trockenen „Na, schon wieder leer?“",
                 "Er hat heute kaum noch Kraft, sagt er. Frag ihn einfach, was ihm jetzt guttun würde?",
                 kern):
        assert f(text) == text, f(text)
    # bliebe zu wenig übrig, bleibt die Frage stehen
    assert f("Er ist platt. Willst du ihm Ruhe gönnen?") == "Er ist platt. Willst du ihm Ruhe gönnen?"
    # eine Frage mitten im Text ist keine Rückfrage am Ende
    mitten = "Er fragt sich, was noch kommt? Jedenfalls ist er wach. Gönn ihm eine ruhige Stunde auf dem Sofa."
    assert f(mitten) == mitten


async def test_hinweis_laeuft_durch_das_netz():
    """Beide Wege (Dom-Seite und Vanilla) schicken den bereinigten Gedanken;
    der Auftrag nennt beide Regeln."""
    roh = ("Er ist mal wieder eher still, viel kommt da nicht. Lass ihn kurz ankommen – das hilft ihm meistens "
           "sofort.\n\nWie willst du es angehen?")
    sauber = "Er ist eher still, viel kommt da nicht. Lass ihn kurz ankommen – das hilft ihm sofort."
    assert schablonen.mitteilung_bereinigen(roh) == sauber
    assert st._hinweis_bereinigt(roh) == sauber and st._hinweis_bereinigt(sauber) == sauber

    sub = config.SKLAVE_CHAT_ID
    orig = (db_progress.save_training, st.grok.simple, st.telegram_helper.send_domina)
    prompts = []

    async def _simple(prompt, **kw):
        system = prompt[0] if isinstance(prompt, tuple) else kw.get("system", "")
        prompts.append(system)
        return roh if "Sag der Domina locker Bescheid" in system else "Kurze Reaktion."

    db_progress.save_training = AsyncMock(return_value="neu")
    st.grok.simple = _simple
    st.telegram_helper.send_domina = AsyncMock()
    try:
        state._state.clear()
        await st._antwort_verarbeiten(MagicMock(), sub, "müde, aber zufrieden", AsyncMock(), frage=FRAGE)
        gesendet = st.telegram_helper.send_domina.await_args.args[1]
        assert gesendet.endswith(sauber) and "meistens" not in gesendet and "angehen" not in gesendet, gesendet
        auftrag = next(p for p in prompts if "Sag der Domina locker Bescheid" in p)
        assert fp.STIMMUNG_BEIM_GESAGTEN in auftrag and fp.MITTEILUNG_OHNE_RUECKFRAGE in auftrag
    finally:
        state._state.clear()
        db_progress.save_training, st.grok.simple, st.telegram_helper.send_domina = orig


# --------------------------------------------------------------------------
# 5 – Machbarkeits-Prüfung liest einen Anlass nicht als Verbot
# --------------------------------------------------------------------------

def test_pruefung_liest_anlass_nicht_als_verbot():
    inventar = ["Schürze (trägt nur er)", "Holzlöffel (Kochen)", "Hocker (sie sitzt, er kniet davor)"]
    for art in ("aufgabe", "wette", "strafe"):
        system, user = fp.machbarkeits_pruefung("Er rührt den Teig mit dem Holzlöffel.", inventar, art=art)
        # die drei Lesarten einer Angabe – und was davon bindet
        assert "ROLLEN:" in system and "vertauschte Rollen sind ein Mangel" in system, system
        assert "MECHANIK:" in system and "ANLASS:" in system
        assert "KEIN Verbot anderer Verwendungen" in system and "solange es körperlich geht" in system
        assert "die Rollen gleich bleiben" in system
        assert "ist deshalb kein Mangel" in system                       # der benannte Schein-Mangel
        assert "Ausdrückliche Einschränkungen" in system and "gelten wörtlich" in system
        # die Regel steht bei den Prüfpunkten, vor der Ausgabe-Vorgabe; das Inventar bleibt Daten
        assert system.index("5. Anatomie") < system.index("So liest du die Benutzungsangaben") \
            < system.index("Melde nur klare Widersprüche")
        assert "Holzlöffel (Kochen)" in user and "Holzlöffel" not in system
    # der alte Prüfpunkt zur Haltung bleibt – wer wo liegt oder sitzt, gilt weiter
    assert "wer wo liegt oder sitzt, gilt genau das" in fp.machbarkeits_pruefung("x", inventar)[0]
    # Log 26.11.2026: eine Fessel-gegen-Haltung-Kollision ging durch (Varianz). Gefesselte
    # Glieder zählen zur Belegung – aber nur, was die Fessel wirklich festlegt (Gegenprobe:
    # kurzes Seil zwischen den Knöcheln wurde sonst als „kein Gehen“ abgelehnt).
    system = fp.machbarkeits_pruefung("x", inventar)[0]
    assert "Gefesselte Glieder sind belegt" in system and "kein Vierfüßlerstand" in system
    assert "nimmt nur, was sie wirklich festlegt" in system
    assert system.index("1. Belegung") < system.index("Gefesselte Glieder") < system.index("2. Haltung")


def main():
    for coro in (test_nachbessern_und_letzter_halt, test_tipp_speichert_gesagtes_und_hinweis_zeigt_frage,
                 test_hinweis_laeuft_durch_das_netz):
        asyncio.run(coro())
        print(f"✅ {coro.__name__}")
    for fn in (test_prompt_klartext_uebersetzt_nur, test_netz_sagt_mehr, test_hinweis_text,
               test_vanilla_hinweis_bleibt_beim_gesagten, test_zutaten_gesten,
               test_gewohnheiten_fallen_weg, test_rueckfrage_am_ende_faellt_weg,
               test_pruefung_liest_anlass_nicht_als_verbot):
        fn()
        print(f"✅ {fn.__name__}")
    print("\nAlle Tests zur Log-Durchsicht vom 21.11.2026 grün.")


if __name__ == "__main__":
    main()
