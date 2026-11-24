"""
Regressions-Tests zur Log-Durchsicht vom 24.11.2026. Vier Befunde:

  1. Stimmungs-Kette: Eine Frage stellte etwas über den Tag fest, die Antwort
     konnte das nur bewerten, und der Klartext dazu nannte den Bezug nicht –
     gespeichert stand eine bloße Bewertung, die Reaktion und Vorschlag als
     Aussage über die Person lasen. Jetzt nennt der Klartext, worauf sich so
     eine Antwort bezieht (Auftrag + Netz + Nachbesserung); ohne brauchbaren
     Klartext steht die Frage hinter der Antwort; die Frage selbst soll nichts
     mehr feststellen.
  2. Lernkurve: las aus den über den Bot gelaufenen Aufgaben die ganze Zeit der
     beiden und hielt der Dom-Seite Menge und fehlende Bewertungen vor. Jetzt
     ordnet der Auftrag die Zahlen ein, die jüngsten Gespräche liegen bei, und
     ein Netz fängt Mengen-Urteile und Vorwürfe ab.
  3. Tagesvorschlag: Die Begründung kam fast immer aus der Stimmung und stand als
     letzter Satz da. Stand sie in einem der letzten beiden Vorschläge dort,
     setzt sie aus (Auftrag + Netz).
  4. Wett-Bestätigung: doppelter Punkt hinter dem Datum.

Alle Beispiele sind erfunden (Muster, keine Live-Inhalte).

    python3 tests/test_review_d17.py
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
from bot.locales import de, en  # noqa: E402
from bot.prompts import followup as fp  # noqa: E402
from bot.scheduler import schablonen, tagesjobs as tj  # noqa: E402
from bot.services import persona_config as pc  # noqa: E402
from bot.services.db import conversations as db_conversations, progress as db_progress  # noqa: E402
from bot.services.db import vorschlaege as db_vorschlaege  # noqa: E402

FRAGE = "Im Haus ist es heute laut und voll. Passt das zu deinem Kopf?"
OHNE_BEZUG = ('{"antworten": ['
              '{"knopf": "Passt genau", "klartext": "Stimmung: passt"},'
              '{"knopf": "Gar nicht", "klartext": "Stimmung: passt nicht"},'
              '{"knopf": "Eher lauter", "klartext": "Kopf: unruhig"},'
              '{"knopf": "Schwer zu sagen", "klartext": "Kopf: unbestimmt"}]}')
MIT_BEZUG = ('{"antworten": ['
             '{"knopf": "Passt genau", "klartext": "Laut und voll: passt"},'
             '{"knopf": "Gar nicht", "klartext": "Laut und voll: passt nicht"},'
             '{"knopf": "Eher lauter", "klartext": "Kopf: unruhig"},'
             '{"knopf": "Schwer zu sagen", "klartext": "Kopf: unbestimmt"}]}')


class _Knopf:                          # telegram ist lokal ein Stub
    def __init__(self, text, callback_data=None):
        self.text, self.callback_data = text, callback_data


class _Tasten:
    def __init__(self, zeilen):
        self.inline_keyboard = zeilen


# --------------------------------------------------------------------------
# 1 – Klartext nennt den Bezug; ohne Klartext steht die Frage dahinter
# --------------------------------------------------------------------------

def test_auftrag_klartext_bezug():
    system, user = fp.stimmung_antworten(FRAGE)
    assert "Bewertet eine Antwort nur das, was die Frage selbst behauptet oder beschreibt" in system
    assert "mit den Worten der Frage" in system and "einzige Ausnahme: der nächste Punkt" in system
    assert "Dein erster Versuch" not in user and "ersten Versuch" not in user
    # Nachbesserung nennt die Klartexte ohne Bezug – und lässt die übrigen in Ruhe
    _, user = fp.stimmung_antworten(FRAGE, nachbessern=True, ohne_bezug=["Stimmung: passt", "Stimmung: passt nicht"])
    assert "worauf sich die Antwort bezieht: Stimmung: passt; Stimmung: passt nicht." in user
    assert "Die übrigen Klartexte bleiben, wie sie sind" in user and "unbrauchbar" not in user
    # beide Mängel zugleich: beide Hinweise
    _, user = fp.stimmung_antworten(FRAGE, nachbessern=True, zu_viel=["überfordert"], ohne_bezug=["Stimmung: passt"])
    assert "mehr hineingelegt" in user and "worauf sich die Antwort bezieht" in user
    # nur die Form war unbrauchbar: der alte Hinweis
    _, user = fp.stimmung_antworten(FRAGE, nachbessern=True)
    assert "unbrauchbar" in user and "worauf sich" not in user


def test_netz_klartext_ohne_bezug():
    ohne = ["Stimmung: passt", "Stimmung: falsch", "Tag: passt nicht", "Stimmung: eher das Erste", "passt zu mir",
            "Gefühl: fühlt sich richtig an", "Stimmung: weder noch", "Lage: trifft nicht zu", "Kopf: dazwischen",
            "Passt zu Kopf: ja", "Kopf heute: weder noch", "Heutiger Tag: passt", "Mood: fits"]
    mit = ["Stimmung: ruhig", "Stimmung: unbestimmt", "Gedanken: gemischt", "Laut und voll: passt nicht",
           "Stiller See: passt", "Volles Haus: fühlt sich richtig an", "Auf dem Sprung: ja",
           "Auf dem Sprung oder angekommen: dazwischen", "Kopf heute: weder stiller See noch voller Bahnhof",
           "Energie: wenig", "Laune: sehr gut", ""]
    for k in ohne:
        assert st._klartext_bezugslos(k), ("nicht erkannt", k)
    for k in mit:
        assert not st._klartext_bezugslos(k), ("Fehlalarm", k)
    optionen = st._optionen_lesen(OHNE_BEZUG)
    assert st._ohne_bezug(optionen) == ["Stimmung: passt", "Stimmung: passt nicht"]
    # letzter Halt: der Knopf bleibt, der Klartext ohne Bezug fällt weg
    assert [o["klartext"] for o in st._ohne_ueberzogenen_klartext(optionen, FRAGE)] == [
        "", "", "Kopf: unruhig", "Kopf: unbestimmt"]


async def test_klartext_wird_nachgebessert():
    orig = st.grok.simple
    aufrufe = []
    try:
        def _modell(folge):
            async def _simple(prompt, **kw):
                aufrufe.append(prompt[1])
                x = folge.pop(0) if len(folge) > 1 else folge[0]
                if isinstance(x, Exception):
                    raise x
                return x
            return _simple

        # erster Versuch ohne Bezug → zweiter nennt ihn → der gilt
        st.grok.simple = _modell([OHNE_BEZUG, MIT_BEZUG])
        o = await st._antwort_optionen(FRAGE)
        assert [x["klartext"] for x in o][:2] == ["Laut und voll: passt", "Laut und voll: passt nicht"], o
        assert len(aufrufe) == 2 and "worauf sich die Antwort bezieht" not in aufrufe[0]
        assert "worauf sich die Antwort bezieht: Stimmung: passt; Stimmung: passt nicht." in aufrufe[1]

        # bleibt es dabei: die Knöpfe bleiben, die Klartexte ohne Bezug fallen weg
        aufrufe.clear()
        st.grok.simple = _modell([OHNE_BEZUG])
        o = await st._antwort_optionen(FRAGE)
        assert len(aufrufe) == st._OPTIONEN_VERSUCHE
        assert [x["knopf"] for x in o] == ["Passt genau", "Gar nicht", "Eher lauter", "Schwer zu sagen"]
        assert [x["klartext"] for x in o] == ["", "", "Kopf: unruhig", "Kopf: unbestimmt"], o

        # es gilt der Versuch mit weniger unbrauchbaren Klartexten – auch wenn es der erste ist
        schlechter = OHNE_BEZUG.replace("Kopf: unruhig", "Stimmung: falsch")
        st.grok.simple = _modell([OHNE_BEZUG, schlechter])
        o = await st._antwort_optionen(FRAGE)
        assert [x["klartext"] for x in o] == ["", "", "Kopf: unruhig", "Kopf: unbestimmt"], o

        # Modell fällt nach dem ersten Versuch aus → der erste gilt, ohne die Klartexte ohne Bezug
        st.grok.simple = _modell([OHNE_BEZUG, RuntimeError("llm weg"), "nie"])
        o = await st._antwort_optionen(FRAGE)
        assert [x["klartext"] for x in o] == ["", "", "Kopf: unruhig", "Kopf: unbestimmt"], o
    finally:
        st.grok.simple = orig


def test_antwort_mit_frage():
    assert st._mit_frage("Passt schon", "Wie  geht es\ndir?") == "Passt schon (Antwort auf die Frage: Wie geht es dir?)"
    assert st._mit_frage("gut – entspannt", "") == "gut – entspannt"          # feste Knöpfe: steht für sich
    lang = st._mit_frage("Ja", "Wort " * 80)
    assert lang.endswith("…)") and len(lang) < st._FRAGE_IM_EINTRAG_MAX + 40


async def test_eintrag_steht_fuer_sich():
    """Ganzer Weg: Tipp auf eine Antwort, deren Klartext den Bezug nicht nennt →
    gespeichert wird die Antwort mit der Frage dahinter; mit brauchbarem
    Klartext der Klartext; eine getippte Antwort ebenfalls mit Frage."""
    sub = config.SKLAVE_CHAT_ID
    orig = (st.InlineKeyboardButton, st.InlineKeyboardMarkup, db_progress.save_training, db_progress.update_training,
            db_progress.get_training_by_id, db_vorschlaege.get_recent_stimmung_eintraege,
            db_vorschlaege.get_recent_stimmung_reaktionen, st.grok.simple, st.telegram_helper.send_domina,
            config.STIMMUNG_ENABLED)
    st.InlineKeyboardButton, st.InlineKeyboardMarkup = _Knopf, _Tasten
    gespeichert, ids = [], []

    async def _save(rolle, daten, point_id=None):
        gespeichert.append(dict(daten))
        ids.append(point_id)
        return point_id or "neu"

    async def _simple(prompt, **kw):
        system = prompt[0] if isinstance(prompt, tuple) else kw.get("system", "")
        user = prompt[1] if isinstance(prompt, tuple) else prompt
        if "Antworten zum Antippen. Du schreibst" in system:
            return OHNE_BEZUG
        if "Formuliere jetzt genau EINE solche Frage" in user:
            return "Wie ist es gerade in deinem Kopf?"
        return "Kurzer Gedanke."

    db_progress.save_training = _save
    db_progress.update_training = AsyncMock()
    db_progress.get_training_by_id = AsyncMock(
        side_effect=lambda pid, user_id="domina": next(
            (d for d, i in zip(gespeichert, ids) if i == pid and d["typ"] == "stimmung_frage"), None))
    db_vorschlaege.get_recent_stimmung_eintraege = AsyncMock(return_value=[])
    db_vorschlaege.get_recent_stimmung_reaktionen = AsyncMock(return_value=[])
    st.grok.simple = _simple
    st.telegram_helper.send_domina = AsyncMock()
    config.STIMMUNG_ENABLED = True
    frage = "Wie ist es gerade in deinem Kopf?"

    def _tap(daten, tasten, mid):
        upd = MagicMock()
        upd.effective_chat.id = sub
        q = upd.callback_query
        q.data = daten
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.edit_message_reply_markup = AsyncMock()
        q.message.message_id = mid
        q.message.text = frage
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
        # Klartext ohne Bezug ist weggefallen → Antwort mit Frage
        await st.callback_button(_tap(knoepfe[1].callback_data, tasten, 91), ctx)
        assert [d for d in gespeichert if d["typ"] == "stimmung"][-1] == {
            "typ": "stimmung", "zusammenfassung": f"Gar nicht (Antwort auf die Frage: {frage})", "frage": frage}
        # brauchbarer Klartext → der steht da
        await st.frage_stellen(bot)
        tasten = bot.send_message.call_args.kwargs["reply_markup"]
        knoepfe = [z[0] for z in tasten.inline_keyboard]
        await st.callback_button(_tap(knoepfe[2].callback_data, tasten, 92), ctx)
        assert [d for d in gespeichert if d["typ"] == "stimmung"][-1] == {
            "typ": "stimmung", "zusammenfassung": "Kopf: unruhig", "frage": frage, "knopf": "Eher lauter"}
        # getippte Antwort → mit Frage
        await st.frage_stellen(bot)
        text = MagicMock()
        text.effective_chat.id = sub
        text.message.text = "passt schon"
        text.message.reply_text = AsyncMock()
        await st.handle_antwort(text, ctx)
        assert [d for d in gespeichert if d["typ"] == "stimmung"][-1]["zusammenfassung"] == \
            f"passt schon (Antwort auf die Frage: {frage})"
    finally:
        state._state.clear()
        (st.InlineKeyboardButton, st.InlineKeyboardMarkup, db_progress.save_training, db_progress.update_training,
         db_progress.get_training_by_id, db_vorschlaege.get_recent_stimmung_eintraege,
         db_vorschlaege.get_recent_stimmung_reaktionen, st.grok.simple, st.telegram_helper.send_domina,
         config.STIMMUNG_ENABLED) = orig


async def test_frage_stellt_nichts_fest():
    # Auftrag
    system, _ = fp.stimmung_abfragen()
    assert "Du fragst, du stellst nichts fest" in system and "welches Bild" in system
    assert "ein Wetter-Bild für den heutigen Tag" not in fp.stimmung_richtungen()
    # Netz: Aussagesatz davor + Rückbezug in der Frage
    fest = ["Im Haus ist es heute laut und voll. Wie fühlt sich das bei dir an?", "Du wirkst heute abwesend. Stimmt das?",
            "Der Tag war lang. Was macht das mit dir?"]
    frei = ["Sag mir, was dir gerade durch den Kopf geht.", "Wie viel Kraft ist heute noch übrig?",
            "Eher stiller See oder voller Bahnhof?", "Ich bin neugierig. Wie geht es dir?",
            "Wie fühlt sich das Ende des Tages für dich an?"]
    for f in fest:
        assert st._stellt_etwas_fest(f), f
    for f in frei:
        assert not st._stellt_etwas_fest(f), f
    # Ablauf: so eine Frage geht nicht raus – es folgt ein neuer Versuch mit anderer Richtung
    orig = (db_vorschlaege.get_recent_stimmung_eintraege, st.grok.simple)
    antworten = [fest[0], "Wie viel Kraft ist heute noch übrig?"]
    richtungen = []

    async def _simple(prompt, **kw):
        richtungen.append(prompt[0].split("Richtung diesmal: ", 1)[1].split(" – Tonlage", 1)[0])
        return antworten[len(richtungen) - 1]
    db_vorschlaege.get_recent_stimmung_eintraege = AsyncMock(return_value=[])
    st.grok.simple = _simple
    try:
        frage, richtung = await st._frage_text()
        assert frage == "Wie viel Kraft ist heute noch übrig?" and len(richtungen) == 2
        assert richtungen[0] != richtungen[1] and richtung == richtungen[1]
        # bleibt es bei Fragen mit Vorgabe → Standardfrage
        richtungen.clear()
        antworten[:] = [fest[0], fest[1], fest[2]]
        frage, richtung = await st._frage_text()
        assert frage == st.t("STIMMUNG_FRAGE") and richtung == ""
    finally:
        db_vorschlaege.get_recent_stimmung_eintraege, st.grok.simple = orig


# --------------------------------------------------------------------------
# 2 – Lernkurve urteilt nicht über Menge und Bewertungen
# --------------------------------------------------------------------------

DATEN = {"erledigt": 1, "nicht_erledigt": 0, "kategorien": {"Dienst": 1}, "avg_bewertung": 0,
         "task_details": ["Abends den Tisch decken"]}
VORWURF = "Das Tischdecken lief rund. Die Ausbeute dieser Tage ist mager. Als Nächstes vielleicht ein Abend ohne Uhr?"
OHNE_VORWURF = "Das Tischdecken lief rund. Du hast erzählt, dass er neuerdings pünktlich ist. Als Nächstes vielleicht ein Abend ohne Uhr?"


def test_lernkurve_auftrag():
    system, prompt = tj._lernkurve_auftrag(3, DATEN, "")
    assert "WAS DU SIEHST – UND WAS NICHT" in system and "KEIN Urteil über die Menge" in system
    assert "mach ihr keinen Vorwurf" in system and "ehrliches Feedback" not in system
    assert "Über dich gelaufene Aufgaben in den zwei Wochen: erledigt 1, nicht erledigt (lag am Sklaven) 0" in prompt
    # ohne Bewertungen fehlt die Zeile ganz – sie lud zum Vorhalten ein
    assert "Bewertung" not in prompt and "Was sie dir in diesen zwei Wochen" not in prompt
    assert "Abends den Tisch decken" in prompt and "Dienst 1×" in prompt
    _, mit = tj._lernkurve_auftrag(3, {**DATEN, "avg_bewertung": 4.5}, "eine Aufgabe blieb liegen",
                                   "[2026-01-05] Themen: abend\n  Domina: Heute war er aufmerksam.")
    assert "Durchschnittliche Bewertung: 4.5★" in mit and "eine Aufgabe blieb liegen" in mit
    assert "Was sie dir in diesen zwei Wochen selbst erzählt hat" in mit and "Heute war er aufmerksam." in mit


def test_lernkurve_netz():
    ja = ["Das Programm dieser Tage wirkt dünn.", "Die Ausbeute ist mager.",
          "Für dein Können ist das zu wenig.", "Ich habe nur eine Aufgabe von dir gesehen.",
          "Du bewertest nie, was er abliefert.", "Ohne Bewertungen bleibt mir sein Abschneiden verborgen.",
          "Das wirkt wie durchgewunken.", "Da geht mehr.", "In diesen Tagen war wenig los.",
          "Nicht viel, was hier ankam."]
    nein = ["Das Tischdecken hat er sauber erledigt.", "Ein wenig mehr Biss hätte der Aufgabe gutgetan.",
            "Du hast erzählt, dass er neuerdings pünktlich ist.", "Er hat nichts liegen lassen.",
            "Wenig Worte, klare Ansage – das war stark.", "Als Nächstes könnte ich mir einen Abend ohne Uhr vorstellen."]
    for satz in ja:
        assert tj._lernkurve_vorwuerfe(satz) == [satz], ("nicht erkannt", satz)
    for satz in nein:
        assert tj._lernkurve_vorwuerfe(satz) == [], ("Fehlalarm", satz)
    # letzter Halt: die Sätze fallen weg, wenn genug übrig bleibt – sonst bleibt alles stehen
    lang = ("Das Tischdecken lief rund. Die Ausbeute ist mager.\n\n"
            "Du hast erzählt, dass er pünktlich ist. Als Nächstes ein Abend ohne Uhr?")
    assert tj._lernkurve_ohne_vorwurf(lang) == (
        "Das Tischdecken lief rund.\n\nDu hast erzählt, dass er pünktlich ist. Als Nächstes ein Abend ohne Uhr?")
    assert tj._lernkurve_ohne_vorwurf("Das ist mager. Mehr nicht.") == "Das ist mager. Mehr nicht."
    assert tj._lernkurve_ohne_vorwurf(OHNE_VORWURF) == OHNE_VORWURF


async def test_lernkurve_wird_nachgebessert():
    orig = tj.grok.simple
    aufrufe = []
    try:
        def _modell(folge):
            async def _simple(prompt, **kw):
                aufrufe.append(prompt)
                x = folge.pop(0) if len(folge) > 1 else folge[0]
                if isinstance(x, Exception):
                    raise x
                return x
            return _simple

        # sauberer Entwurf: ein Aufruf
        tj.grok.simple = _modell([OHNE_VORWURF])
        assert await tj._lernkurve_text(3, DATEN, "") == OHNE_VORWURF and len(aufrufe) == 1
        # Vorwurf → Neu-Entwurf mit dem Satz im Auftrag, der gilt
        aufrufe.clear()
        tj.grok.simple = _modell([VORWURF, OHNE_VORWURF])
        assert await tj._lernkurve_text(3, DATEN, "") == OHNE_VORWURF and len(aufrufe) == 2
        assert "Dein erster Entwurf hat über die Menge geurteilt" in aufrufe[1] and "ist mager" in aufrufe[1]
        assert aufrufe[1].startswith(aufrufe[0])
        # bleibt es dabei → der Satz fällt weg
        aufrufe.clear()
        tj.grok.simple = _modell([VORWURF])
        text = await tj._lernkurve_text(3, DATEN, "")
        assert text == "Das Tischdecken lief rund. Als Nächstes vielleicht ein Abend ohne Uhr?" and len(aufrufe) == 2
        # Neu-Entwurf fällt aus → erster Entwurf ohne den Satz
        tj.grok.simple = _modell([VORWURF, RuntimeError("llm weg"), "nie"])
        assert await tj._lernkurve_text(3, DATEN, "") == "Das Tischdecken lief rund. Als Nächstes vielleicht ein Abend ohne Uhr?"
    finally:
        tj.grok.simple = orig


async def test_lernkurve_gespraeche():
    orig = db_conversations.get_conversations_in_range
    try:
        gesehen = {}

        async def _range(user_id, start_iso, end_iso, limit=200):
            gesehen.update(user=user_id, limit=limit, start=start_iso, ende=end_iso)
            return [{"datum": "2026-01-05T19:00:00+00:00", "themen": ["abend"],
                     "zusammenfassung": "Domina: Heute war er aufmerksam.\nCoach: Schön."}]
        db_conversations.get_conversations_in_range = _range
        text = await tj._lernkurve_gespraeche()
        assert "Heute war er aufmerksam." in text and gesehen["user"] == "domina"
        assert gesehen["limit"] == tj._LERNKURVE_GESPRAECHE and gesehen["start"] < gesehen["ende"]
        # nicht ladbar → ohne Gesprächs-Kontext weiter
        db_conversations.get_conversations_in_range = AsyncMock(side_effect=RuntimeError("db weg"))
        assert await tj._lernkurve_gespraeche() == ""
    finally:
        db_conversations.get_conversations_in_range = orig


# --------------------------------------------------------------------------
# 3 – Tagesvorschlag: Stimmung nicht jedes Mal als Schluss-Begründung
# --------------------------------------------------------------------------

MIT_STIMMUNG = "Leg ihm das Tuch um und zähl bis zehn.\n\nEr ist gerade eher still, also reichen fünf Minuten."
OHNE_STIMMUNG = "Leg ihm das Tuch um und zähl bis zehn.\n\nEr hat zuletzt alles ohne Murren erledigt, da darf es mehr sein."


def test_stimmungs_satz_am_schluss():
    ja = [MIT_STIMMUNG,
          "Leg ihm das Tuch um.\n\nEr ist müde drauf, das merkt man an jedem Schritt. Den Rest setzt du.",
          "Leg ihm das Tuch um. Er wirkt momentan abwesend, also fang leise an.",
          "Leg ihm das Tuch um.\n\nDas hält ihn bei dir. Er ist gut gelaunt, das trägt."]
    nein = [OHNE_STIMMUNG,
            "Leg ihm das Tuch um.\n\nEr ist gerade erst fertig, wenn du es sagst.",
            "Er ist gerade still.\n\nLeg ihm das Tuch um und zähl bis zehn.",          # nicht im Schluss-Absatz
            "Leg ihm das Tuch um.\n\nDas Tuch ist gerade lang genug dafür."]
    for text in ja:
        assert schablonen._stimmungs_satz(text), text
    for text in nein:
        assert not schablonen._stimmungs_satz(text), text
    # der Satz fällt weg, der Rest bleibt unverändert
    assert schablonen._stimmungs_satz_entfernen(MIT_STIMMUNG) == "Leg ihm das Tuch um und zähl bis zehn."
    assert schablonen._stimmungs_satz_entfernen(ja[1]) == "Leg ihm das Tuch um.\n\nDen Rest setzt du."
    assert schablonen._stimmungs_satz_entfernen(ja[2]) == "Leg ihm das Tuch um."
    assert schablonen._stimmungs_satz_entfernen(ja[3]) == "Leg ihm das Tuch um.\n\nDas hält ihn bei dir."
    assert schablonen._stimmungs_satz_entfernen(OHNE_STIMMUNG) == OHNE_STIMMUNG
    assert schablonen._stimmungs_satz_entfernen("Er ist gerade still.") == "Er ist gerade still."   # sonst bliebe nichts
    # die eingestellte Anrede zählt als Subjekt
    orig = pc.sklave_anrede
    pc.sklave_anrede = lambda: "Braver Welpe"
    try:
        mit_anrede = "Leg ihm das Tuch um.\n\nDer Brave Welpe ist heute dran. Braver Welpe ist gerade still, also kurz."
        assert schablonen._stimmungs_satz(mit_anrede) == "Braver Welpe ist gerade still, also kurz."
    finally:
        pc.sklave_anrede = orig
    # höchstens jeder dritte Vorschlag: stand der Satz in einem der letzten beiden, setzt die Stimmung aus
    assert schablonen.stimmung_setzt_aus([MIT_STIMMUNG, OHNE_STIMMUNG, OHNE_STIMMUNG])
    assert schablonen.stimmung_setzt_aus([OHNE_STIMMUNG, MIT_STIMMUNG, OHNE_STIMMUNG])
    assert not schablonen.stimmung_setzt_aus([OHNE_STIMMUNG, OHNE_STIMMUNG, MIT_STIMMUNG])
    assert not schablonen.stimmung_setzt_aus([]) and not schablonen.stimmung_setzt_aus(None)


def test_auftrag_setzt_stimmung_aus():
    basis = dict(erfahrungsstand="etwas", level=2, interessen=[], sklave_vorlieben=[], sklave_hard_limits=[],
                 stimmung="Laune: gut")
    for bauer in (fp.tiny_task_vorschlag, fp.ausfuehrlicher_task_vorschlag):
        system, prompt = bauer(**basis)
        assert "(wie er zuletzt reagiert hat, Stimmung, was ansteht)" in system
        assert "so, wie sie dasteht – nicht umgedeutet und nicht größer gemacht" in prompt
        assert "ERWÄHNE die Stimmung" not in prompt
        system, prompt = bauer(**basis, stimmung_nicht_nennen=True)
        assert "heute NICHT seine Stimmung" in system and "nicht wieder als letzten Satz" in system
        assert "ERWÄHNE die Stimmung in der Nachricht heute NICHT" in prompt and "Laune: gut" in prompt
        assert "so, wie sie dasteht" not in prompt
        # ohne Stimmung gibt es den Block nicht
        _, ohne = bauer(**{**basis, "stimmung": ""}, stimmung_nicht_nennen=True)
        assert "Aktuelle Stimmung" not in ohne and "ERWÄHNE die Stimmung" not in ohne


# --------------------------------------------------------------------------
# 4 – Wett-Bestätigung ohne doppelten Punkt
# --------------------------------------------------------------------------

def test_wett_texte_ohne_doppelpunkt():
    for sprache in (de, en):
        m = sprache.MESSAGES
        angenommen = m["WETTE_ANGENOMMEN_DOM"].format(sub_gross="X", zusatz="", datum="03.02.")
        assert angenommen.endswith("03.02.") and ".." not in angenommen, angenommen
        offen = m["WETTE_ABRUF_OFFEN"].format(lage=m["WETTE_LAGE_LAEUFT"].format(datum="03.02."))
        assert "03.02.). " in offen and ".." not in offen, offen


def main():
    for fn in (test_auftrag_klartext_bezug, test_netz_klartext_ohne_bezug, test_antwort_mit_frage,
               test_lernkurve_auftrag, test_lernkurve_netz, test_stimmungs_satz_am_schluss,
               test_auftrag_setzt_stimmung_aus, test_wett_texte_ohne_doppelpunkt):
        fn()
        print(f"✅ {fn.__name__}")
    for coro in (test_klartext_wird_nachgebessert, test_eintrag_steht_fuer_sich, test_frage_stellt_nichts_fest,
                 test_lernkurve_wird_nachgebessert, test_lernkurve_gespraeche):
        asyncio.run(coro())
        print(f"✅ {coro.__name__}")
    print("\nAlle Tests zur Log-Durchsicht vom 24.11.2026 grün.")


if __name__ == "__main__":
    main()
