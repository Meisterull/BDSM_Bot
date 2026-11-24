"""
Regressions-Tests zur Log-Durchsicht vom 22.11.2026. Zwei Befunde:

  1. Reaktion der Herrin auf eine Stimmungs-Antwort: jeden Tag gleich gebaut
     und meist mit einer Ankündigung für „gleich" oder den Abend, die nie kam.
     Jetzt wählt der Code die Bauform (nie eine der letzten drei), der Auftrag
     verbietet Ankündigungen und nennt die Satzanfänge der letzten Reaktionen;
     darunter ein Netz mit Nachbesserung, und die Reaktion liegt am Eintrag.
  2. Wochenplan: kannte seine Vorgänger nicht – ein fester Kern von Kategorien
     kam Woche für Woche wieder. Jetzt liegt der Verlauf im Profil; was in
     beiden letzten Plänen stand, pausiert eine Woche, aus dem letzten Plan
     kommen höchstens drei Kategorien wieder, die letzten Tagesvorschläge
     stehen im Auftrag. Ein eigenes Thema der Dom-Seite geht vor.

Alle Beispiele sind erfunden (Muster, keine Live-Inhalte).

    python3 tests/test_review_d16.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import timedelta
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
from bot.handlers import wochenplanung as wp  # noqa: E402
from bot.prompts import coach_persona, persona  # noqa: E402
from bot.prompts import followup as fp  # noqa: E402
from bot.services import inventar as inv, kategorie_logik as kl, persona_config as pc, zeiten  # noqa: E402
from bot.services.db import kern, profile as db_profile, progress as db_progress  # noqa: E402
from bot.services.db import vorschlaege as db_vorschlaege  # noqa: E402

FRAGE = "Wie ruhig ist es heute in dir?"
ANTWORT = "eher ruhig"
ALT = [{"reaktion": "Soso, wieder so einer. Dir traue ich heute alles zu.", "form": "umdrehen"},
       {"reaktion": "Na schön. Womit hast du das verdient?", "form": "nachhaken"}]
ALT_TEXTE = [e["reaktion"] for e in ALT]
MIT_ANKUENDIGUNG = "Ruhig steht dir. Ich zeige dir gleich, was daraus wird."
SAUBER = "Ruhig steht dir. Bleib so."


# --------------------------------------------------------------------------
# 1 – Reaktion auf die Stimmungs-Antwort
# --------------------------------------------------------------------------

def test_auftrag_form_und_alleinstehen():
    formen = fp.reaktion_bauformen()
    assert len(formen) >= 4 and len(set(formen)) == len(formen)
    for form in formen:
        system, user = fp.reaktion_auf_stimmung(ANTWORT, FRAGE, "Stimmung: ruhig", bauform=form)
        assert f"- Form heute: {fp.modul_von('reaktion_bauformen')._REAKTION_BAUFORMEN[form]}" in system
        # die Vorgaben stehen NACH der Persona und gehen ihr ausdrücklich vor
        kopf = "FÜR DIESE NACHRICHT (geht den Stil-Regeln oben vor):"
        assert system.index(persona.fuer_sklaven_prompt()) < system.index(kopf)
        assert "Die Nachricht steht für sich" in system and "Kündige deshalb nichts an" in system
        assert "stell keine Szene in Aussicht" in system
        assert "wiederhole die Antwort nicht erst im Wortlaut" in system
        assert "Direkte Anrede: Du redest MIT ihm (du, dich, dir), nie in der dritten Person über ihn" in system
        assert "Verlangt sie keine Frage, dann stellst du keine" in system
        assert FRAGE in user and ANTWORT in user and "Gemeint ist damit: Stimmung: ruhig" in user
    # Formen mit und ohne Frage am Ende sind beide vertreten
    texte = fp.modul_von("reaktion_bauformen")._REAKTION_BAUFORMEN
    ohne_frage = [k for k, v in texte.items() if "Keine Frage" in v]
    mit_frage = [k for k in texte if k not in ohne_frage]
    assert ohne_frage and mit_frage and all("Frage" in texte[k] for k in mit_frage), (ohne_frage, mit_frage)
    # ohne Bauform (alter Aufruf) bleibt die offene Vorgabe – aber die Ankündigungs-Regel gilt auch dort
    system, _ = fp.reaktion_auf_stimmung(ANTWORT, FRAGE)
    assert "Ein bis drei Sätze" in system and "Form heute" not in system and "Kündige deshalb nichts an" in system
    # ohne Frage: von sich aus gemeldet
    assert "von sich aus" in fp.reaktion_auf_stimmung(ANTWORT)[0]


def test_auftrag_nennt_nur_satzanfaenge():
    system, _ = fp.reaktion_auf_stimmung(ANTWORT, FRAGE, vorherige=ALT_TEXTE, bauform="ein_satz")
    for anfang in ('"soso wieder"', '"dir traue"', '"na schön"', '"womit hast"'):
        assert anfang in system, anfang
    # der Wortlaut der letzten Reaktionen steht NICHT im Auftrag (das Modell übernähme ihre Wendungen)
    assert "alles zu" not in system and "verdient" not in system
    # auch der Anfang der Antwort selbst ist gesperrt – als Erstes in der Liste
    assert 'Wortfolgen: "eher ruhig", "soso wieder"' in system and "waren alle ähnlich gebaut" in system
    # ohne letzte Reaktionen bleibt nur das; bei einer Ein-Wort-Antwort gar keine Sperre
    allein = fp.reaktion_auf_stimmung(ANTWORT, FRAGE, bauform="ein_satz")[0]
    assert 'Wortfolgen: "eher ruhig".' in allein and "ähnlich gebaut" not in allein
    assert "Wortfolgen" not in fp.reaktion_auf_stimmung("Ruhig", FRAGE, bauform="ein_satz")[0]
    # Nachbesserung: die Mängel stehen wörtlich im Auftrag
    system, _ = fp.reaktion_auf_stimmung(ANTWORT, FRAGE, bauform="ein_satz",
                                         nachbessern=["kündigt etwas an („x“)", "beginnt wie zuletzt mit „na schön“"])
    assert "Dein erster Versuch ging so nicht: kündigt etwas an („x“); beginnt wie zuletzt mit „na schön“." in system
    assert "in der Form von oben" in system
    # Anrede setzt aus
    orig = pc.sklave_anrede
    pc.sklave_anrede = lambda: "Braver Welpe"
    try:
        mit = fp.reaktion_auf_stimmung(ANTWORT, FRAGE, bauform="ein_satz", anrede_aus=True)[0]
        ohne = fp.reaktion_auf_stimmung(ANTWORT, FRAGE, bauform="ein_satz")[0]
        assert "Heute kommt sie NICHT vor" in mit and "Heute kommt sie NICHT vor" not in ohne
    finally:
        pc.sklave_anrede = orig


def test_teilsatz_anfaenge():
    assert fp.teilsatz_anfaenge("Soso, wieder so einer. Das ist mir zu wenig – und dir auch: gib es zu!") == [
        "soso wieder", "das ist", "und dir", "gib es"]
    assert fp.teilsatz_anfaenge("Hm. Na.") == []                       # zu kurz für einen Anfang
    assert fp.teilsatz_anfaenge("Das ist so. Das ist anders.") == ["das ist"]


def test_netz_ankuendigungen():
    ja = ["Ich zeige dir gleich, was daraus wird.",
          "Heute Abend nehme ich mir das vor.",
          "Das kriegst du nachher zu spüren.",
          "Ich werde dich daran erinnern.",
          "Dann werde ich dir die Flausen austreiben.",
          "Gleich bekommst du etwas zu tun.",
          "Heute Abend, wenn du heimkommst, zeige ich dir den Unterschied.",
          "Mal sehen, wie ich dich später dafür zahlen lasse.",
          "Bald bist du dran.",
          "Du wirst heute noch merken, was das heißt.",
          "Gut so, dann kann ich dich heute länger zappeln lassen.",
          "Das ist die beste Lage, um dich heute müde zu spielen.",
          "Ruhig ist eine Einladung, das gleich zu ändern.",
          "Das schreit danach, später ausgenutzt zu werden.",
          "Dein Tag wird gleich schwerer.",
          "Das wird mir heute noch nützen.",
          "I'll deal with you tonight."]
    nein = ["Das ist mir gleich.",
            "Bist du gleich fertig?",
            "Da werde ich neugierig.",
            "Ich werde nicht schlau aus dir.",
            "Früher warst du schneller.",
            "Ich bin gespannt, ob du gleich noch so redest.",
            "Geh gleich duschen.",
            "Später ist auch ein Wort, hinter dem man sich verstecken kann.",
            "Wie war dein Tag heute?",
            "Ich habe dich heute vermisst.",
            "Heute bist du also der Stille.",
            "Hast du vor, gleich zu gehen?",
            "Gleich zu müde zum Reden, wie es scheint.",
            "Ruhig steht dir. Bleib so."]
    for satz in ja:
        assert st._ankuendigungen(satz) == [satz], ("nicht erkannt", satz)
    for satz in nein:
        assert st._ankuendigungen(satz) == [], ("Fehlalarm", satz)
    # letzter Halt: der Satz fällt weg, wenn etwas Tragfähiges bleibt – sonst bleibt alles stehen
    assert st._ohne_ankuendigung(MIT_ANKUENDIGUNG) == "Ruhig steht dir."
    assert st._ohne_ankuendigung("Ich zeige dir gleich, was daraus wird.") == "Ich zeige dir gleich, was daraus wird."
    assert st._ohne_ankuendigung("Hm. Ich zeige dir gleich, was daraus wird.") == "Hm. Ich zeige dir gleich, was daraus wird."
    assert st._ohne_ankuendigung(SAUBER) == SAUBER


def test_netz_wiederholung():
    # Antwort im Wortlaut am Anfang – ein einzelnes aufgegriffenes Wort zählt nicht
    assert st._wiederholt_antwort("Eher ruhig, soso.", ANTWORT)
    assert st._wiederholt_antwort("Ach, eher ruhig also.", ANTWORT)
    assert not st._wiederholt_antwort("Ruhig steht dir.", ANTWORT)
    assert not st._wiederholt_antwort("Ruhig also.", "ruhig")
    assert not st._wiederholt_antwort("Das passt zu einem, der an so einem Tag wie heute eher ruhig sein will.", ANTWORT)
    assert not st._wiederholt_antwort("Dein „eher ruhig“ nehme ich dir nicht ab.", ANTWORT)       # Zitat mitten im Satz
    assert st._wiederholt_antwort("So so", "so so") and not st._wiederholt_antwort("So, das war es.", "so so")
    # Satzanfang wie zuletzt
    m = st._reaktion_maengel("Na schön, dann eben so.", ALT_TEXTE, ANTWORT, FRAGE)
    assert m == ["beginnt wie zuletzt mit „na schön“"], m
    # Wendung von zuletzt mitten im Satz (drei Wörter am Stück)
    m = st._reaktion_maengel("Gerade deshalb traue ich heute alles deiner Ruhe zu.", ALT_TEXTE, ANTWORT, FRAGE)
    assert m == ["greift Wendungen von zuletzt wieder auf („traue ich heute“, „ich heute alles“)"], m
    # … was aus Frage oder Antwort stammt, darf wiederkommen
    alt = ["Wie ruhig ist es denn wirklich?"]
    assert st._reaktion_maengel("Mich wundert, wie ruhig ist es bei dir.", alt, ANTWORT, FRAGE) == []
    assert st._reaktion_maengel("Mich wundert, wie ruhig ist es bei dir.", alt, ANTWORT, "") != []
    # Lieblingswendung: zwei Wörter am Stück, die in mindestens zwei der letzten Reaktionen standen
    oft = ["Das riecht nach Ausrede. Gut.", "Hm, es riecht nach mehr.", "Soso."]
    m = st._reaktion_maengel("Mir riecht nach Müdigkeit, was du da schreibst.", oft, ANTWORT, FRAGE)
    assert m == ["greift Wendungen von zuletzt wieder auf („riecht nach“)"], m
    assert st._reaktion_maengel("Mir riecht nach Müdigkeit, was du da schreibst.", oft[:1] + ["Soso."], ANTWORT, FRAGE) == []
    # … reine Füllwort-Paare zählen nicht, und was er selbst gesagt hat, auch nicht
    fuell = ["Tja, das ist so eine Sache mit dir.", "Und morgen? Dann sehen wir, ob das ist, was du willst."]
    assert st._reaktion_maengel("Mir gefällt, wie das ist, wenn du nichts erklärst.", fuell, ANTWORT, FRAGE) == []
    assert st._reaktion_maengel("Mir riecht nach Müdigkeit, was du da schreibst.", oft, "es riecht nach Regen", FRAGE) == []
    # sauber
    assert st._reaktion_maengel(SAUBER, ALT_TEXTE, ANTWORT, FRAGE) == []
    # mehrere Mängel zusammen, Ankündigung zuerst
    m = st._reaktion_maengel("Eher ruhig, hm? Ich zeige dir gleich, was daraus wird.", ALT_TEXTE, ANTWORT, FRAGE)
    assert m[0].startswith("kündigt etwas an") and "steigt mit der Antwort im Wortlaut ein" in m, m


def test_bauform_wahl_und_anrede():
    formen = fp.reaktion_bauformen()
    for _ in range(40):
        wahl = st._bauform_wahl(["ein_satz", "frage", "umdrehen", "lesen"])
        assert wahl in formen and wahl not in ("ein_satz", "frage", "umdrehen"), wahl   # nur die letzten drei sperren
    assert st._bauform_wahl([]) in formen and st._bauform_wahl(["", "unbekannt"]) in formen
    assert st._bauform_wahl(formen[:3] * 2) in formen[3:]
    # Anrede als Anrede fällt weg – mitten im Satz ohne Kommas bleibt sie
    a = "Braver Welpe"
    assert st._ohne_anrede("Ruhig steht dir, Braver Welpe. Bleib so.", a) == "Ruhig steht dir. Bleib so."
    assert st._ohne_anrede("Braver Welpe, das steht dir.", a) == "Das steht dir."
    assert st._ohne_anrede("Ruhig, braver  welpe, steht dir.", a) == "Ruhig, steht dir."
    assert st._ohne_anrede("Ruhig steht dir, Braver Welpe – bleib so.", a) == "Ruhig steht dir – bleib so."
    assert st._ohne_anrede("Mein Braver Welpe ist heute still.", a) == "Mein Braver Welpe ist heute still."
    assert st._ohne_anrede("Ruhig steht dir.", "") == "Ruhig steht dir."


async def test_reaktion_wird_nachgebessert():
    orig = (db_vorschlaege.get_recent_stimmung_reaktionen, st.grok.simple, st._bauform_wahl, pc.sklave_anrede)
    aufrufe = []
    try:
        db_vorschlaege.get_recent_stimmung_reaktionen = AsyncMock(return_value=list(ALT))
        st._bauform_wahl = lambda letzte: "lesen" if letzte == ["umdrehen", "nachhaken"] else "falsch"
        pc.sklave_anrede = lambda: ""

        def _modell(folge):
            async def _simple(prompt, **kw):
                aufrufe.append(prompt[0])
                x = folge.pop(0) if len(folge) > 1 else folge[0]
                if isinstance(x, Exception):
                    raise x
                return x
            return _simple

        # Ankündigung im ersten Versuch → zweiter mit dem Mangel im Auftrag, sauber → der gilt
        st.grok.simple = _modell([MIT_ANKUENDIGUNG, SAUBER])
        text, form = await st._reaktion_text("sklave", ANTWORT, FRAGE, "")
        assert (text, form) == (SAUBER, "lesen") and len(aufrufe) == 2
        assert "Dein erster Versuch" not in aufrufe[0] and '"soso wieder"' in aufrufe[0]
        assert "Dein erster Versuch ging so nicht: kündigt etwas an" in aufrufe[1]

        # bleibt es bei der Ankündigung: alle Versuche, dann fällt der Satz weg
        aufrufe.clear()
        st.grok.simple = _modell([MIT_ANKUENDIGUNG])
        text, _ = await st._reaktion_text("sklave", ANTWORT, FRAGE, "")
        assert text == "Ruhig steht dir." and len(aufrufe) == st._REAKTION_VERSUCHE

        # letzter Versuch nach lauter Ankündigungen: andere Bauform – trifft die, gilt sie samt ihrer Form
        aufrufe.clear()
        st._bauform_wahl = lambda letzte: {("umdrehen", "nachhaken"): "ein_satz",
                                           ("ein_satz", "umdrehen", "nachhaken"): "frage"}[tuple(letzte)]

        async def _nur_frage_sauber(prompt, **kw):
            aufrufe.append(prompt[0])
            return "Wie kommt das?" if "Genau EINE Frage und sonst nichts" in prompt[0] else "Ich zeige dir gleich mehr."
        st.grok.simple = _nur_frage_sauber
        assert await st._reaktion_text("sklave", ANTWORT, FRAGE, "") == ("Wie kommt das?", "frage")
        assert len(aufrufe) == 3 and "Genau EIN Satz" in aufrufe[1] and "Genau EINE Frage" in aufrufe[2]
        st._bauform_wahl = lambda letzte: "lesen"

        # es gilt der Versuch mit den wenigsten Mängeln, auch wenn er nicht der letzte ist
        aufrufe.clear()
        st.grok.simple = _modell(["Na schön, bleib so.", "Eher ruhig, na schön. Ich zeige dir gleich, was daraus wird.",
                                  "Na schön also. Ich zeige dir gleich mehr."])
        text, _ = await st._reaktion_text("sklave", ANTWORT, FRAGE, "")
        assert text == "Na schön, bleib so." and len(aufrufe) == 3

        # Nachbesserung fällt aus → der erste Versuch gilt (ohne den Ankündigungs-Satz)
        st.grok.simple = _modell([MIT_ANKUENDIGUNG, RuntimeError("llm weg"), "nie"])
        assert (await st._reaktion_text("sklave", ANTWORT, FRAGE, ""))[0] == "Ruhig steht dir."

        # schon der erste Aufruf scheitert → Fehler an den Aufrufer (Textkonserve)
        st.grok.simple = _modell([RuntimeError("llm weg")])
        try:
            await st._reaktion_text("sklave", ANTWORT, FRAGE, "")
            raise AssertionError("Fehler erwartet")
        except RuntimeError:
            pass

        # Zeilenumbrüche des Modells werden geglättet
        st.grok.simple = _modell(["Ruhig steht dir.  \nBleib so."])
        assert (await st._reaktion_text("sklave", ANTWORT, FRAGE, ""))[0] == SAUBER

        # letzte Reaktionen nicht ladbar → es geht ohne Abwechslungs-Vorgabe weiter
        aufrufe.clear()
        db_vorschlaege.get_recent_stimmung_reaktionen = AsyncMock(side_effect=RuntimeError("db weg"))
        st._bauform_wahl = lambda letzte: "ein_satz" if letzte == [] else "falsch"
        st.grok.simple = _modell(["Na schön, bleib so."])
        assert await st._reaktion_text("sklave", ANTWORT, FRAGE, "") == ("Na schön, bleib so.", "ein_satz")
        assert '"soso wieder"' not in aufrufe[0] and "ähnlich gebaut" not in aufrufe[0]

        # Anrede stand zuletzt fast immer drin → setzt aus, auch wenn das Modell sie trotzdem schreibt
        aufrufe.clear()
        pc.sklave_anrede = lambda: "Braver Welpe"
        db_vorschlaege.get_recent_stimmung_reaktionen = AsyncMock(return_value=[
            {"reaktion": "Soso, Braver Welpe.", "form": "ein_satz"},
            {"reaktion": "Na, Braver Welpe, wie kommt das?", "form": "frage"},
            {"reaktion": "Aha.", "form": "lesen"}])
        st._bauform_wahl = lambda letzte: "nachhaken"
        st.grok.simple = _modell(["Ruhig steht dir, Braver Welpe. Bleib so, mein Süßer."])
        assert (await st._reaktion_text("sklave", ANTWORT, FRAGE, ""))[0] == SAUBER
        assert "Heute kommt sie NICHT vor" in aufrufe[0]
        # … stand sie nur einmal drin, bleibt sie erlaubt
        aufrufe.clear()
        db_vorschlaege.get_recent_stimmung_reaktionen = AsyncMock(return_value=[
            {"reaktion": "Soso, Braver Welpe.", "form": "ein_satz"}, {"reaktion": "Aha.", "form": "lesen"}])
        st.grok.simple = _modell(["Ruhig steht dir, Braver Welpe."])
        assert (await st._reaktion_text("sklave", ANTWORT, FRAGE, ""))[0] == "Ruhig steht dir, Braver Welpe."
        assert "Heute kommt sie NICHT vor" not in aufrufe[0]
    finally:
        (db_vorschlaege.get_recent_stimmung_reaktionen, st.grok.simple, st._bauform_wahl, pc.sklave_anrede) = orig


async def test_reaktion_liegt_am_eintrag():
    """Ganzer Weg: die Reaktion wird samt Bauform am Stimmungs-Eintrag gemerkt –
    die Textkonserve nach einem Modell-Ausfall nicht."""
    sub = config.SKLAVE_CHAT_ID
    orig = (db_progress.save_training, db_progress.update_training,
            db_vorschlaege.get_recent_stimmung_reaktionen, st.grok.simple, st.telegram_helper.send_domina)
    try:
        db_progress.save_training = AsyncMock(return_value="p1")
        db_progress.update_training = AsyncMock()
        db_vorschlaege.get_recent_stimmung_reaktionen = AsyncMock(return_value=list(ALT))
        st.telegram_helper.send_domina = AsyncMock()

        async def _simple(prompt, **kw):
            return SAUBER if isinstance(prompt, tuple) and "Du reagierst darauf" in prompt[0] else "Kurzer Gedanke."
        st.grok.simple = _simple
        state._state.clear()
        antworten = AsyncMock()
        await st._antwort_verarbeiten(MagicMock(), sub, ANTWORT, antworten, frage=FRAGE)
        antworten.assert_awaited_once_with(SAUBER)
        punkt, felder = db_progress.update_training.await_args.args
        assert punkt == "p1" and felder["reaktion"] == SAUBER and felder["reaktion_form"] in fp.reaktion_bauformen()
        assert felder["reaktion_form"] not in ("umdrehen", "nachhaken")      # nicht die Form der letzten Reaktionen
        assert state.get(sub)["message_history"][-1]["content"] == SAUBER

        # Modell fällt aus → Textkonserve, nichts wird gemerkt
        db_progress.update_training.reset_mock()

        async def _weg(prompt, **kw):
            if isinstance(prompt, tuple) and "Du reagierst darauf" in prompt[0]:
                raise RuntimeError("llm weg")
            return "Kurzer Gedanke."
        st.grok.simple = _weg
        antworten = AsyncMock()
        await st._antwort_verarbeiten(MagicMock(), sub, ANTWORT, antworten, frage=FRAGE)
        antworten.assert_awaited_once_with(st.t("FALLBACK_STIMMUNG_REAKTION"))
        db_progress.update_training.assert_not_awaited()

        # Merken scheitert → die Reaktion ist trotzdem draußen, der Hinweis geht weiter
        db_progress.update_training = AsyncMock(side_effect=RuntimeError("db weg"))
        st.grok.simple = _simple
        st.telegram_helper.send_domina.reset_mock()
        await st._antwort_verarbeiten(MagicMock(), sub, ANTWORT, AsyncMock(), frage=FRAGE)
        st.telegram_helper.send_domina.assert_awaited_once()
    finally:
        state._state.clear()
        (db_progress.save_training, db_progress.update_training,
         db_vorschlaege.get_recent_stimmung_reaktionen, st.grok.simple, st.telegram_helper.send_domina) = orig


async def test_letzte_reaktionen_aus_den_eintraegen():
    class _Punkt:
        def __init__(self, payload):
            self.payload = payload

    orig = kern.client.scroll
    gesehen = {}

    def _scroll(**kw):
        gesehen.update(kw)
        return ([_Punkt({"typ": "stimmung", "zusammenfassung": "a", "reaktion": "Eins.", "reaktion_form": "frage"}),
                 _Punkt({"typ": "stimmung", "zusammenfassung": "b"}),                      # Altbestand ohne Reaktion
                 _Punkt({"typ": "stimmung", "zusammenfassung": "c", "reaktion": "Zwei."}),
                 _Punkt({"typ": "stimmung", "zusammenfassung": "d", "reaktion": "Drei.", "reaktion_form": "lesen"}),
                 _Punkt(None)], None)
    kern.client.scroll = _scroll
    try:
        aus = await db_vorschlaege.get_recent_stimmung_reaktionen(limit=2)
        assert aus == [{"reaktion": "Eins.", "form": "frage"}, {"reaktion": "Zwei.", "form": ""}], aus
        assert gesehen["collection_name"] == "training" and gesehen["limit"] >= 6
    finally:
        kern.client.scroll = orig


# --------------------------------------------------------------------------
# 2 – Wochenplan kennt seine Vorgänger
# --------------------------------------------------------------------------

def _vor(tage: int) -> str:
    return (zeiten.heute_lokal() - timedelta(days=tage)).isoformat()


def _verlauf():
    return [{"datum": _vor(7), "kategorien": ["Demütigung", "Dienst", "Peitsche", "Pet_Play", "Spanking",
                                              "Psycho", "Bestrafung"]},
            {"datum": _vor(14), "kategorien": ["Peitsche", "Erniedrigung", "Impact", "Schmerz", "Smothering",
                                               "Speichelspiel", "Fisting"]}]


PAUSE = ["Demütigung", "Erniedrigung", "Peitsche"]
FRISCH = ["Impact", "Schmerz", "Smothering", "Speichelspiel", "Fisting", "Orgasmusverweigerung", "Dienst"]
HARMLOS = "Er deckt um acht den Tisch und wartet."


def _plan(kategorien, aufgaben=None) -> str:
    teile = ["🗓 Wochenplan", ""]
    for tag, kat in zip(wp._WOCHENTAGE, kategorien):
        teile += [f"{tag} – {kat}", f"Aufgabe: {(aufgaben or {}).get(tag, HARMLOS)}", "Warum: Passt in die Woche.", ""]
    return "\n".join(teile) + "\nKurzer Schluss."


def test_verlauf_und_pause():
    v = _verlauf()
    assert wp._verlauf({wp.FELD_VERLAUF: list(reversed(v))}) == v                 # jüngster zuerst
    kaputt = [None, "x", {"datum": _vor(3)}, {"kategorien": ["Dienst"]}, {"datum": _vor(2), "kategorien": [" ", 5]}]
    assert wp._verlauf({wp.FELD_VERLAUF: kaputt + v}) == v
    assert wp._verlauf({}) == [] and wp._verlauf(None) == []
    viele = [{"datum": _vor(7 * i), "kategorien": ["Dienst"]} for i in range(1, 8)]
    assert len(wp._verlauf({wp.FELD_VERLAUF: viele})) == wp._VERLAUF_MAX
    # in BEIDEN letzten Plänen – dasselbe Thema unter zwei Namen zählt als dasselbe
    assert wp._pausiert(v) == PAUSE
    assert wp._pausiert(v[:1]) == [] and wp._pausiert([]) == []
    # nur die zwei jüngsten zählen: was davor lief, pausiert nicht mehr
    assert wp._pausiert([{"datum": _vor(1), "kategorien": ["Dienst"]}] + v) == ["Dienst"]


def test_auftrag_block():
    v = _verlauf()
    block = wp._verlauf_block(v, PAUSE, mit_thema=False)
    assert "  - vor 7 Tagen: Demütigung, Dienst, Peitsche" in block and "  - vor 14 Tagen: Peitsche, Erniedrigung" in block
    assert "Pause in dieser Woche, weil sie in beiden Plänen standen: Demütigung, Erniedrigung, Peitsche." in block
    assert "auch wenn sie sonst bevorzugt werden" in block and "unter einem anderen Etikett" in block
    assert f"höchstens {wp._MAX_WIEDERHOLT} Kategorien wieder" in block
    assert "Erwähne im Plan nicht, was pausiert" in block
    # ein Plan allein: keine Pause, aber die Obergrenze
    einer = wp._verlauf_block(v[:1], [], mit_thema=False)
    assert "Pause in dieser Woche" not in einer and "höchstens" in einer
    # eigenes Thema: nur Hinweis, keine Regeln
    thema = wp._verlauf_block(v, [], mit_thema=True)
    assert "Das Thema der Woche geht vor" in thema and "Pause" not in thema and "höchstens" not in thema
    assert wp._verlauf_block([], [], mit_thema=False) == ""
    # Tagesvorschläge als Stichworte
    vb = wp._vorschlaege_block(["Erstes Stichwort", "", "Zweites Stichwort"])
    assert "jüngster zuerst): Erstes Stichwort; Zweites Stichwort\n" in vb and "Wochenanfang" in vb
    assert wp._vorschlaege_block([]) == "" and wp._vorschlaege_block(None) == ""
    assert len(wp._vorschlaege_block([f"S{i}" for i in range(9)]).split("; ")) == wp._VORSCHLAEGE_IM_AUFTRAG


def test_netz_vielfalt():
    v = _verlauf()
    assert wp._vielfalt_maengel(_plan(FRISCH), v, PAUSE) == []
    # pausierte Kategorie als Etikett
    m = wp._vielfalt_maengel(_plan(["Demütigung"] + FRISCH[1:]), v, PAUSE)
    assert m == ["Montag: Demütigung pausiert diese Woche"], m
    # dieselbe Praktik unter anderem Etikett (eindeutiges Stichwort im Aufgabentext)
    m = wp._vielfalt_maengel(_plan(FRISCH, {"Mittwoch": "Sie nimmt die Peitsche und zählt bis zehn."}), v, PAUSE)
    assert m == ["Mittwoch: bringt Peitsche unter anderem Etikett"], m
    # ein kurzes Stichwort trifft zu vieles und zählt für die Text-Prüfung nicht
    assert "arsch" in kl.keywords()["Anal"]
    assert wp._im_text(["Anal", "Peitsche"], "Arsch raus, die Peitsche liegt bereit.") == ["Peitsche"]
    # mehr als drei Kategorien aus dem letzten Plan
    viele = ["Dienst", "Pet_Play", "Spanking", "Psycho", "Impact", "Schmerz", "Fisting"]
    m = wp._vielfalt_maengel(_plan(viele), v, PAUSE)
    assert m == ["4 Kategorien aus dem letzten Plan wiederholt (Dienst, Pet_Play, Spanking, Psycho) – höchstens 3"], m
    assert wp._vielfalt_maengel(_plan(viele[:3] + FRISCH[:4]), v, PAUSE) == []
    # unlesbarer Plan oder kein Verlauf: nichts zu prüfen
    assert wp._vielfalt_maengel("kein Plan", v, PAUSE) == [] and wp._vielfalt_maengel(_plan(viele), [], []) == []


class _Generator:
    """Ersetzt den Modell-Aufruf des Wochenplans und alles, was er nachlädt."""

    def __init__(self, plaene, labels=("Erstes Stichwort", "Zweites Stichwort")):
        self.plaene, self.aufrufe, self.ohne = list(plaene), [], "unberührt"
        self.labels = list(labels)

    async def _retry(self, prompt, hard_limits, grenzen, system=None, reasoning=False, **kw):
        self.aufrufe.append((system, prompt))
        x = self.plaene.pop(0) if self.plaene else None
        if isinstance(x, Exception):
            raise x
        return x

    async def _impuls(self, chance=None, ohne=None):
        self.ohne = ohne
        return None

    def __enter__(self):
        self.orig = (wp.limits_check.generate_mit_limit_retry, inv.impuls_wahl,
                     coach_persona.skill_kontext_block, db_vorschlaege.get_recent_tiny_tasks)
        wp.limits_check.generate_mit_limit_retry = self._retry
        inv.impuls_wahl = self._impuls
        coach_persona.skill_kontext_block = AsyncMock(return_value="")
        db_vorschlaege.get_recent_tiny_tasks = AsyncMock(return_value=(self.labels, [], []))
        return self

    def __exit__(self, *a):
        (wp.limits_check.generate_mit_limit_retry, inv.impuls_wahl,
         coach_persona.skill_kontext_block, db_vorschlaege.get_recent_tiny_tasks) = self.orig


async def _erzeuge(profil, thema=""):
    return await wp._generiere_wochenplan(domina_profile=profil, sklave_profile={}, thema=thema,
                                          letzte_kategorien=[], bewertungs_kontext="")


def _kategorie_liste(system: str) -> list[str]:
    zeile = system.split("nicht übersetzen, nicht erfinden):\n", 1)[1].split("\n", 1)[0]
    return [k.strip() for k in zeile.split(",")]


async def test_plan_setzt_sich_von_den_vorwochen_ab():
    profil = {wp.FELD_VERLAUF: _verlauf()}
    gut, schlecht = _plan(FRISCH), _plan(["Demütigung", "Peitsche"] + FRISCH[2:])

    # sauberer Entwurf: ein Aufruf; Verlauf, Pause und Tagesvorschläge stehen im Auftrag
    with _Generator([gut]) as g:
        assert await _erzeuge(profil) == gut and len(g.aufrufe) == 1
        system, prompt = g.aufrufe[0]
        liste = _kategorie_liste(system)
        assert not set(PAUSE) & set(liste) and "Dienst" in liste and "Spanking" in liste
        assert "Deine letzten Wochenpläne (jüngster zuerst):" in prompt and "Pause in dieser Woche" in prompt
        assert "Zuletzt als Tagesvorschlag gelaufen (jüngster zuerst): Erstes Stichwort; Zweites Stichwort" in prompt
        # Geräte eines pausierten Themas stehen für den Ausstattungs-Impuls nicht zur Wahl
        assert g.ohne("Peitsche (Hinweis)") and g.ohne("Federkiel") is False

    # Entwurf mit pausierten Kategorien → ein Neu-Entwurf mit den Fundstellen, der bessere gilt
    with _Generator([schlecht, gut]) as g:
        assert await _erzeuge(profil) == gut and len(g.aufrufe) == 2
        assert "Dein erster Entwurf" not in g.aufrufe[0][1]
        assert ("Dein erster Entwurf hat die Regeln zu den letzten Wochenplänen verletzt: Montag: Demütigung "
                "pausiert diese Woche; Dienstag: Peitsche pausiert diese Woche.") in g.aufrufe[1][1]
        assert g.aufrufe[1][1].startswith(g.aufrufe[0][1])
    # … ist der Neu-Entwurf nicht besser, verworfen oder fällt er aus, gilt der erste
    for zweiter in (schlecht, None, RuntimeError("llm weg")):
        with _Generator([schlecht, zweiter]) as g:
            assert await _erzeuge(profil) == schlecht and len(g.aufrufe) == 2
    # … besser, aber nicht sauber: gilt trotzdem
    halb = _plan(["Demütigung"] + FRISCH[1:])
    with _Generator([schlecht, halb]) as g:
        assert await _erzeuge(profil) == halb

    # erster Entwurf an den Grenzen gescheitert → wie bisher ein Fehler
    with _Generator([None]) as g:
        try:
            await _erzeuge(profil)
            raise AssertionError("Fehler erwartet")
        except ValueError:
            pass

    # ohne Verlauf: kein Block, kein Neu-Entwurf, alle Kategorien wählbar, Impuls ohne Einschränkung
    with _Generator([schlecht]) as g:
        assert await _erzeuge({}) == schlecht and len(g.aufrufe) == 1
        assert "Deine letzten Wochenpläne" not in g.aufrufe[0][1] and "Peitsche" in _kategorie_liste(g.aufrufe[0][0])
        assert g.ohne is None

    # Tagesvorschläge nicht ladbar → der Plan entsteht trotzdem
    with _Generator([gut]) as g:
        db_vorschlaege.get_recent_tiny_tasks = AsyncMock(side_effect=RuntimeError("db weg"))
        assert await _erzeuge(profil) == gut and "Tagesvorschlag" not in g.aufrufe[0][1]


async def test_eigenes_thema_geht_vor():
    profil = {wp.FELD_VERLAUF: _verlauf()}
    schlecht = _plan(["Demütigung", "Peitsche", "Dienst", "Pet_Play", "Spanking", "Psycho", "Bestrafung"])
    with _Generator([schlecht, "nie"]) as g:
        assert await _erzeuge(profil, thema="alles rund ums Dienen") == schlecht and len(g.aufrufe) == 1
        system, prompt = g.aufrufe[0]
        assert set(PAUSE) <= set(_kategorie_liste(system))                      # nichts pausiert
        assert "Das Thema der Woche geht vor" in prompt and "Pause in dieser Woche" not in prompt
        assert g.ohne is None


async def test_abgelehntes_thema_ruht_auch_im_plan():
    from datetime import datetime, timezone
    bis = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    profil = {"kategorie_praeferenzen": {"Smothering": {kl.FELD_PAUSE_BIS: bis}}}
    assert kl.thema_pausiert(profil["kategorie_praeferenzen"]) == ["Smothering"]
    with _Generator(["x"]) as g:
        await _erzeuge(profil)
        system, prompt = g.aufrufe[0]
        assert "Smothering" not in _kategorie_liste(system) and "Impact" in _kategorie_liste(system)
        assert "reizt sie gerade nicht (ganz weglassen, auch als Beiwerk): Smothering" in prompt
        assert g.ohne("Maske fürs Smothering") and g.ohne("Federkiel") is False
    # nennt sie das Thema selbst als Thema der Woche, steht es wieder zur Wahl
    with _Generator(["x"]) as g:
        await _erzeuge(profil, thema="eine Woche Smothering")
        assert "Smothering" in _kategorie_liste(g.aufrufe[0][0]) and "reizt sie gerade nicht" not in g.aufrufe[0][1]


async def test_gesendeter_plan_kommt_in_den_verlauf():
    orig = (db_profile.get_user_profile, db_profile.patch_profile_fields)
    heute = zeiten.heute_lokal().isoformat()
    try:
        alt = [{"datum": heute, "kategorien": ["Dienst"]}] + _verlauf() + [
            {"datum": _vor(21), "kategorien": ["Psycho"]}, {"datum": _vor(28), "kategorien": ["Schmerz"]}]
        db_profile.get_user_profile = AsyncMock(return_value={wp.FELD_VERLAUF: alt})
        db_profile.patch_profile_fields = AsyncMock()
        state._state.clear()
        bot = MagicMock()
        bot.send_message = AsyncMock()
        await wp.sende_plan(bot, config.DOMINA_CHAT_ID, _plan(FRISCH), "Kopf")
        rolle, felder = db_profile.patch_profile_fields.await_args.args
        neu = felder[wp.FELD_VERLAUF]
        # der heutige Plan ersetzt den vom selben Tag, höchstens _VERLAUF_MAX bleiben
        assert rolle == "domina" and neu[0] == {"datum": heute, "kategorien": FRISCH}
        assert [e["datum"] for e in neu] == [heute, _vor(7), _vor(14), _vor(21)] and len(neu) == wp._VERLAUF_MAX

        # ein erfundenes Etikett landet auf der nächstliegenden Kategorie, „allgemein" fällt weg
        db_profile.patch_profile_fields.reset_mock()
        await wp.sende_plan(bot, config.DOMINA_CHAT_ID, _plan(["Impacts", "Ganz_Neu"] + FRISCH[2:]), "Kopf")
        assert db_profile.patch_profile_fields.await_args.args[1][wp.FELD_VERLAUF][0]["kategorien"] == ["Impact"] + FRISCH[2:]

        # unlesbarer Plan → wird gesendet, aber nicht gemerkt
        db_profile.patch_profile_fields.reset_mock()
        await wp.sende_plan(bot, config.DOMINA_CHAT_ID, "nur Text", "Kopf")
        db_profile.patch_profile_fields.assert_not_awaited()

        # Sendefehler → nichts gemerkt (der Plan kam nie an)
        bot.send_message = AsyncMock(side_effect=RuntimeError("telegram weg"))
        try:
            await wp.sende_plan(bot, config.DOMINA_CHAT_ID, _plan(FRISCH), "Kopf")
            raise AssertionError("Fehler erwartet")
        except RuntimeError:
            pass
        db_profile.patch_profile_fields.assert_not_awaited()

        # Merken scheitert → der Plan ist trotzdem draußen
        bot.send_message = AsyncMock()
        db_profile.patch_profile_fields = AsyncMock(side_effect=RuntimeError("db weg"))
        await wp.sende_plan(bot, config.DOMINA_CHAT_ID, _plan(FRISCH), "Kopf")
        bot.send_message.assert_awaited_once()
    finally:
        state._state.clear()
        db_profile.get_user_profile, db_profile.patch_profile_fields = orig


async def test_impuls_ohne():
    orig = (inv.vorhanden, pc.ist_abwesend, db_profile.patch_profile_fields)
    try:
        inv.vorhanden = lambda: ["Gerte (hart, nur Po)", "Federkiel", "Seidentuch"]
        pc.ist_abwesend = lambda: False
        db_profile.patch_profile_fields = AsyncMock()
        for _ in range(12):
            inv._aktueller_cache()["zuletzt"] = []
            assert await inv.impuls_wahl(chance=1.0, ohne=lambda e: e.startswith("Gerte")) in ("Federkiel", "Seidentuch")
        inv._aktueller_cache()["zuletzt"] = []
        assert await inv.impuls_wahl(chance=1.0, ohne=lambda e: True) is None
    finally:
        inv._aktueller_cache()["zuletzt"] = []
        inv.vorhanden, pc.ist_abwesend, db_profile.patch_profile_fields = orig


def main():
    for fn in (test_auftrag_form_und_alleinstehen, test_auftrag_nennt_nur_satzanfaenge, test_teilsatz_anfaenge,
               test_netz_ankuendigungen, test_netz_wiederholung, test_bauform_wahl_und_anrede,
               test_verlauf_und_pause, test_auftrag_block, test_netz_vielfalt):
        fn()
        print(f"✅ {fn.__name__}")
    for coro in (test_reaktion_wird_nachgebessert, test_reaktion_liegt_am_eintrag,
                 test_letzte_reaktionen_aus_den_eintraegen, test_plan_setzt_sich_von_den_vorwochen_ab,
                 test_eigenes_thema_geht_vor, test_abgelehntes_thema_ruht_auch_im_plan,
                 test_gesendeter_plan_kommt_in_den_verlauf, test_impuls_ohne):
        asyncio.run(coro())
        print(f"✅ {coro.__name__}")
    print("\nAlle Tests zur Log-Durchsicht vom 22.11.2026 grün.")


if __name__ == "__main__":
    main()
