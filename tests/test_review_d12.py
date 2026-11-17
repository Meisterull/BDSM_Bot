"""
Regressions-Tests zur Prüfung eines Tages-Vorschlags (17.11.2026). Sechs Befunde
aus EINEM Lauf – der bessere Entwurf wurde verworfen, der Ersatz hatte Fehler:

  1. Grenzen-Prüfung: Ortswörter treffen nur noch als Ort ("Bus", "Park",
     "draußen") – nicht mehr in Körper- und Alltagswörtern
  2. Vorschlags-Prompt: heutiges Datum, Abstand an den Gesprächs-Einträgen und
     die Regel, Zeitwörter daraus umzurechnen
  3. „Thema reizt mich nicht": die Kategorie ruht in der Auswahl, der Prompt
     nennt das abgelehnte Thema statt des Satzanfangs
  4. Der eigene Name ist kein Einstieg einer fertigen Nachricht (Regel + Filter)
  5. Kategorie-Beleg: Merkmal je Kategorie, Urteil je Kategorie
  6. Termin einer Aufgabe: Zeitangaben im Lohn-Satz zählen nicht

    python3 tests/test_review_d12.py
"""
import asyncio
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import stubs  # noqa: E402

stubs.aktivieren()

from bot import config  # noqa: E402
from bot.handlers import domina  # noqa: E402
from bot.handlers import tiny_task_feedback as tfb  # noqa: E402
from bot.prompts import coach_persona, domina_coach  # noqa: E402
from bot.prompts.followup import aufgaben_vorschlag as av  # noqa: E402
from bot.scheduler import schablonen  # noqa: E402
from bot.services import datum_erkennung as de  # noqa: E402
from bot.services import kategorie_logik as kl  # noqa: E402
from bot.services import kontext_pflege as kp  # noqa: E402
from bot.services import limits_check as lc  # noqa: E402
from bot.services import persona_config  # noqa: E402
from bot.services.db import vorschlaege as db_vorschlaege  # noqa: E402


def _vor(tage: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=tage)).isoformat()


class _Persona:
    """Setzt Felder der Persona-Konfiguration für einen Test und stellt sie wieder her."""

    def __init__(self, **felder):
        self.felder = felder

    def __enter__(self):
        self.alt = dict(persona_config._cache)
        persona_config._cache.update(self.felder)

    def __exit__(self, *exc):
        persona_config._cache.clear()
        persona_config._cache.update(self.alt)


# --------------------------------------------------------------------------
# 1. Grenzen-Prüfung: Ortswörter
# --------------------------------------------------------------------------

def _trifft(text: str, limit: str = "Öffentlichkeit (Ausnahme: zu Hause)") -> str | None:
    treffer = lc._prufe_liste(lc._normalisiere(text), [limit], "sklave_hard_limit")
    return treffer[0]["matched_via"] if treffer else None


def test_ortswoerter_nur_als_ort():
    kein_ort = [
        "Hose runter, Schwanz draußen, Hände auf den Rücken",
        "Die Zunge bleibt draußen, bis du es erlaubst",
        "Lass die Eichel draußen",
        "Der Plug bleibt heute draußen liegen",
        "Er küsst deinen Busen",
        "Er kniet auf dem Parkett",
        "Zur Buße kniet er zehn Minuten",
        "Das Auto ist geparkt, er wartet im Flur",
        "Zieh ihm den Parka aus",
        "Er räumt die Küche auf und deckt den Tisch",
    ]
    for text in kein_ort:
        assert _trifft(text) is None, (text, _trifft(text))
    ort = [
        "Er wartet draußen vor der Tür",
        "Geht zusammen nach draußen",
        "Er kniet draußen",
        "Mit dem Plug draußen spazieren gehen",
        "Schwanz draußen im Garten zeigen",          # Körperteil, aber mit Ortsangabe danach
        "Draußen bleibt die Zunge drin",
        "Im Bus trägt er den Plug",
        "An der Bushaltestelle wartet er",
        "Fahrt mit dem Linienbus",
        "Im Stadtpark auf der Bank",
        "Auf dem Parkplatz",
        "Ein Spaziergang im Park",
        "Im Restaurant trägt er den Plug",
        "Führ ihn öffentlich vor",
        "take the bus downtown",
        "a walk in the park",
    ]
    for text in ort:
        assert _trifft(text), text
    # Heißt die Grenze selbst „draußen“, gilt dieselbe Unterscheidung
    assert _trifft("Schwanz draußen lassen", "draußen") is None
    assert _trifft("Er kniet draußen", "draußen") and _trifft("Im Wald spazieren", "draußen")
    # Andere Grenzen arbeiten weiter über den Wortanfang (samt Vorsilben)
    assert _trifft("Er wird ausgepeitscht", "Peitsche") and _trifft("Zehn Nadeln", "Nadeln")
    assert _trifft("Eine wunderbare Idee", "wund") is None and _trifft("Die Haut ist wund", "wund")


# --------------------------------------------------------------------------
# 2. Datum und Zeitangaben im Vorschlags-Prompt
# --------------------------------------------------------------------------

def test_abstand_am_gespraechs_kontext():
    assert kp.abstand_hinweis(_vor(0)) == ", heute"
    assert kp.abstand_hinweis(_vor(3)) == ", vor 3 Tagen"
    assert kp.abstand_hinweis(_vor(21)) == ", vor 3 Wochen" and kp.abstand_hinweis(_vor(150)) == ", vor 5 Monaten"
    assert kp.abstand_hinweis("") == "" and kp.abstand_hinweis("kaputt") == ""
    # „gestern“ zählt nach Kalendertagen, nicht nach 24 Stunden
    tz = ZoneInfo(config.TIMEZONE)
    gestern_abend = (datetime.now(tz) - timedelta(days=1)).replace(hour=23, minute=30)
    assert kp.abstand_hinweis(gestern_abend.isoformat()) == ", gestern"
    kontext = domina_coach.format_context([
        {"datum": gestern_abend.isoformat(), "zusammenfassung": "Domina: Er soll heute Abend kochen.\nCoach: Mach ich."}])
    assert kontext.startswith(f"[{gestern_abend:%Y-%m-%d}, gestern]"), kontext


def test_prompt_nennt_heute_und_die_umrechnung():
    heute = datetime.now(ZoneInfo(config.TIMEZONE)).date()
    basis = dict(erfahrungsstand="x", level=2, interessen=[], sklave_vorlieben=[], sklave_hard_limits=[])
    for bauer in (av.tiny_task_vorschlag, av.ausfuehrlicher_task_vorschlag):
        _, ohne = bauer(**basis)
        assert ohne.startswith(f"Heute ist {av._WOCHENTAGE[heute.weekday()]}, der {heute:%d.%m.%Y}.\n"), ohne[:80]
        assert "Zeitwörter in den Einträgen" not in ohne
        _, mit = bauer(conversation_context="[2026-01-05, gestern]\n  Domina: heute Abend kochen", **basis)
        assert "Zeitwörter in den Einträgen" in mit and "nicht heute" in mit and "schon vorbei" in mit, mit
        assert mit.index("[2026-01-05, gestern]") < mit.index("Zeitwörter in den Einträgen")


# --------------------------------------------------------------------------
# 3. Themen-Pause und Themen-Nennung
# --------------------------------------------------------------------------

def _praeferenzen() -> dict:
    jetzt = datetime.now(timezone.utc)
    return {
        "Spanking": {"positiv": 3, "negativ": 0, "letztes_signal": jetzt.isoformat()},
        "Sissy_Training": {"positiv": 1, "negativ": 1, "letztes_signal": jetzt.isoformat(),
                           kl.FELD_PAUSE_BIS: (jetzt + timedelta(days=20)).isoformat()},
        "Psycho": {"positiv": 0, "negativ": 1, kl.FELD_PAUSE_BIS: (jetzt - timedelta(days=1)).isoformat()},
        "Dienst": {"positiv": 0, "negativ": 2, "letztes_signal": jetzt.isoformat(), kl.FELD_PAUSE_BIS: "kein Datum"},
    }


def test_thema_pausiert():
    praef = _praeferenzen()
    assert kl.thema_pausiert(praef) == ["Sissy_Training"]          # abgelaufen/unlesbar zählt nicht
    assert kl.thema_pausiert(None) == [] and kl.thema_pausiert({}) == []
    assert kl.thema_pausiert({"Peitsche": "kaputt", "Anal": None}) == []
    spaeter = datetime.now(timezone.utc) + timedelta(days=30)
    assert kl.thema_pausiert(praef, jetzt=spaeter) == []
    # ohne Zeitzone gespeichert → als UTC gelesen
    naiv = (datetime.now(timezone.utc) + timedelta(days=2)).replace(tzinfo=None).isoformat()
    assert kl.thema_pausiert({"Anal": {kl.FELD_PAUSE_BIS: naiv}}) == ["Anal"]


def test_pausierte_kategorie_wird_nicht_gewaehlt():
    random.seed(12)
    praef = _praeferenzen()
    profil = {"vorlieben": [], "wunsch_kategorien": ["Sissy_Training"]}     # selbst als Wunsch nicht
    mit = sum("Sissy_Training" in kl.gewichtete_auswahl(profil, count=3, domina_praeferenzen=praef) for _ in range(300))
    ohne = sum("Sissy_Training" in kl.gewichtete_auswahl(profil, count=3, domina_praeferenzen={}) for _ in range(300))
    assert mit == 0 and ohne > 50, (mit, ohne)
    # die abgelaufene Pause sperrt nicht mehr
    assert any("Psycho" in kl.gewichtete_auswahl({"vorlieben": []}, count=3, domina_praeferenzen=praef)
               for _ in range(400))


async def test_buchung_setzt_und_hebt_die_pause():
    from bot.services import qdrant
    gespeichert: dict = {}

    async def patch(user_id, felder, **kw):
        gespeichert.update(felder["kategorie_praeferenzen"])

    alt = (qdrant.get_user_profile, qdrant.patch_profile_fields)
    qdrant.get_user_profile = AsyncMock(side_effect=lambda *_a, **_k: {"kategorie_praeferenzen": dict(gespeichert)})
    qdrant.patch_profile_fields = patch
    try:
        await kl.record_domina_praeferenz(["Sissy_Training"], "abgelehnt")           # anderer Grund
        assert gespeichert["Sissy_Training"]["negativ"] == 1 and kl.FELD_PAUSE_BIS not in gespeichert["Sissy_Training"]
        await kl.record_domina_praeferenz(["Sissy_Training", "Dienst"], "abgelehnt", thema_pause=True)
        bis = datetime.fromisoformat(gespeichert["Sissy_Training"][kl.FELD_PAUSE_BIS])
        tage = (bis - datetime.now(timezone.utc)).total_seconds() / 86400
        assert kl.THEMA_PAUSE_TAGE - 0.01 < tage <= kl.THEMA_PAUSE_TAGE, tage
        assert gespeichert["Sissy_Training"]["negativ"] == 2
        # Dasselbe Thema unter anderem Namen ruht mit – ohne eigenes Minus
        assert kl.thema_pausiert(gespeichert) == ["Dienst", "Feminisierung", "Sissy_Training"]
        assert gespeichert["Feminisierung"] == {"positiv": 0, "negativ": 0, kl.FELD_PAUSE_BIS: bis.isoformat()}
        assert gespeichert["Dienst"]["negativ"] == 1 and "Bestrafung" not in gespeichert      # kein Doppel, nur Cluster-Nachbar
        # ein positives Signal hebt die Pause auf (auch beim Doppel) – mit thema_pause-Flag setzt es keine
        await kl.record_domina_praeferenz(["Feminisierung"], "gut", thema_pause=True)
        assert kl.FELD_PAUSE_BIS not in gespeichert["Sissy_Training"] and kl.thema_pausiert(gespeichert) == ["Dienst"]
        assert gespeichert["Feminisierung"]["positiv"] == 1 and gespeichert["Sissy_Training"]["positiv"] == 0
        assert kl._mit_gleichem_thema("Pegging") == ["Pegging", "Strap_on"] and kl._mit_gleichem_thema("Dienst") == ["Dienst"]
        assert all(k in config.AUFGABEN_KATEGORIEN for gruppe in kl._GLEICHES_THEMA for k in gruppe)
    finally:
        qdrant.get_user_profile, qdrant.patch_profile_fields = alt


async def test_nur_der_grund_thema_setzt_die_pause():
    rufe = []

    async def verbuchen(inhalt, kategorien, signal, thema_pause=False):
        rufe.append((signal, thema_pause))

    def _query(aktion: str):
        q = MagicMock()
        q.data = f"tinyfb:{aktion}:punkt-1"
        q.answer = AsyncMock()
        q.edit_message_reply_markup = AsyncMock()
        q.message.reply_text = AsyncMock()
        return SimpleNamespace(callback_query=q)

    eintrag = {"status": "vorgeschlagen", "inhalt": "Ein Vorschlag, lang genug.", "kategorien": ["Dienst"]}
    alt = (tfb._signal_verbuchen, tfb.qdrant.get_tiny_task_by_id, tfb.qdrant.mark_tiny_task_status, tfb._flow_beenden)
    tfb._signal_verbuchen = verbuchen
    tfb.qdrant.get_tiny_task_by_id = AsyncMock(return_value=eintrag)
    tfb.qdrant.mark_tiny_task_status = AsyncMock()
    tfb._flow_beenden = lambda point_id: None
    try:
        await tfb.callback_button(_query("g_thema"), MagicMock())
        await tfb.callback_button(_query("g_aufwand"), MagicMock())      # Kritik am Vorschlag, nicht am Thema
        await tfb.callback_button(_query("g_lahm"), MagicMock())
    finally:
        tfb._signal_verbuchen, tfb.qdrant.get_tiny_task_by_id, tfb.qdrant.mark_tiny_task_status, tfb._flow_beenden = alt
    assert rufe == [("abgelehnt", True)], rufe
    # _signal_verbuchen reicht das Flag bis zur Buchung durch
    gebucht = []

    async def rec(kats, sig, thema_pause=False):
        gebucht.append((list(kats), sig, thema_pause))

    alt = (kl.record_domina_praeferenz, kl.kategorien_im_text_belegt)
    kl.record_domina_praeferenz = rec
    kl.kategorien_im_text_belegt = AsyncMock(return_value=["Dienst"])
    try:
        await tfb._signal_verbuchen("Text", ["Dienst", "Psycho"], "abgelehnt", thema_pause=True)
        await tfb._signal_verbuchen("Text", ["Dienst", "Psycho"], "abgelehnt")
    finally:
        kl.record_domina_praeferenz, kl.kategorien_im_text_belegt = alt
    assert gebucht == [(["Dienst"], "abgelehnt", True), (["Dienst"], "abgelehnt", False)], gebucht


def test_prompt_nennt_pause_und_abgelehntes_thema():
    _, user = av.tiny_task_vorschlag(
        erfahrungsstand="x", level=2, interessen=[], sklave_vorlieben=[], sklave_hard_limits=[],
        domina_kategorie_praeferenzen=_praeferenzen(),
        abgelehnte_tiny_tasks=[
            {"inhalt": "Heute mal ganz anders, also pass auf: " + "x" * 80, "kurzlabel": "Kerzen-Ritual am Küchentisch",
             "grund": "Thema reizt mich nicht"},
            {"inhalt": "Älterer Vorschlag ohne Label, der einfach so anfängt", "grund": "zu aufwendig"}])
    zeile = next(z for z in user.splitlines() if z.startswith("Vorschlags-Feedback"))
    assert "gern übernommen: Spanking" in zeile and "eher abgelehnt: Dienst, Psycho" in zeile, zeile
    assert zeile.endswith("reizt sie gerade nicht (ganz weglassen, auch als Beiwerk): Sissy_Training"), zeile
    assert "  • 'Kerzen-Ritual am Küchentisch' – Grund: Thema reizt mich nicht" in user
    assert "Heute mal ganz anders" not in user                      # Satzanfang nur noch als Rückfall …
    assert "  • 'Älterer Vorschlag ohne Label, der einfach so anfängt' – Grund: zu aufwendig" in user
    # nur pausierte Signale → keine leeren „gern/abgelehnt“-Teile
    nur_pause = {"Dienst": {"positiv": 2, "negativ": 0, "letztes_signal": _vor(0),
                            kl.FELD_PAUSE_BIS: (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()}}
    _, user = av.tiny_task_vorschlag(erfahrungsstand="x", level=2, interessen=[], sklave_vorlieben=[],
                                     sklave_hard_limits=[], domina_kategorie_praeferenzen=nur_pause)
    zeile = next(z for z in user.splitlines() if z.startswith("Vorschlags-Feedback"))
    assert "gern übernommen" not in zeile and "Dienst" in zeile, zeile


async def test_abgelehnte_vorschlaege_liefern_das_kurzlabel():
    treffer = [SimpleNamespace(payload={"inhalt": "Voller Text", "kurzlabel": "Kurzes Thema", "kategorien": ["Dienst"],
                                        "feedback_grund": "Thema reizt mich nicht"}),
               SimpleNamespace(payload={"inhalt": "Ohne Grund", "kurzlabel": "x"})]
    alt = db_vorschlaege.kern._aio
    db_vorschlaege.kern._aio = AsyncMock(return_value=(treffer, None))
    try:
        aus = await db_vorschlaege.get_recent_rejected_tiny_tasks(limit=5)
    finally:
        db_vorschlaege.kern._aio = alt
    assert aus == [{"inhalt": "Voller Text", "kurzlabel": "Kurzes Thema", "kategorien": ["Dienst"],
                    "grund": "Thema reizt mich nicht"}], aus


# --------------------------------------------------------------------------
# 4. Der eigene Name
# --------------------------------------------------------------------------

def test_namens_regel_nur_in_fertigen_nachrichten():
    with _Persona(bot_name="Mira"):
        chat = coach_persona.fuer_coach_prompt()
        assert "Dein Name ist Mira. Du darfst dich so geben" in chat and "kein Einstieg mit dem Namen" not in chat
        for baustein in (coach_persona.fuer_aufgaben_vorschlag(), coach_persona.fuer_strukturierten_output()):
            assert "Dein Name ist Mira. In dieser Nachricht kommt er nicht vor" in baustein, baustein[:400]
            assert "kein Einstieg mit dem Namen, keine Unterschrift" in baustein
            assert "nicht der der Domina: Sprich sie nie mit einem Namen an." in baustein
            assert "Du darfst dich so geben" not in baustein
        with _Persona(dom_geschlecht="mann"):
            assert "nicht der des Doms: Sprich ihn nie mit einem Namen an." in coach_persona.fuer_strukturierten_output()
    with _Persona(bot_name=""):
        assert "Dein Name ist" not in coach_persona.fuer_aufgaben_vorschlag()


def test_namens_filter():
    f = schablonen._eigener_name_entfernen
    rest = "kurz und bündig. Heute legst du zuerst die Regeln fest."
    with _Persona(bot_name="Mira"):
        assert f(f"Mira, {rest}") == "Kurz und bündig. Heute legst du zuerst die Regeln fest."
        assert f(f"mira: {rest}") == "Kurz und bündig. Heute legst du zuerst die Regeln fest."
        assert f(f"Hey Mira – {rest}") == "Kurz und bündig. Heute legst du zuerst die Regeln fest."
        assert f(f"„Mira, {rest}") == "„Kurz und bündig. Heute legst du zuerst die Regeln fest."
        # Name mitten im Text, als Wortanfang eines anderen Wortes oder ohne Rest: unverändert
        for bleibt in ("Heute übernimmt Mira das Kommando, und zwar ganz allein.",
                       "Mirabellen pflückt er heute, eine Schale voll für dich.",
                       "Mira, kurz.", "", "Kurz und bündig. Heute legst du zuerst die Regeln fest."):
            assert f(bleibt) == bleibt, bleibt
    with _Persona(bot_name=""):
        assert f(f"Mira, {rest}") == f"Mira, {rest}"
    with _Persona(bot_name="A.I."):                       # Sonderzeichen im Namen brechen das Muster nicht
        assert f(f"A.I., {rest}").startswith("Kurz und bündig.")
        assert f(f"AxIx, {rest}").startswith("AxIx")


# --------------------------------------------------------------------------
# 5. Kategorie-Beleg
# --------------------------------------------------------------------------

def test_merkmale_decken_den_katalog():
    assert set(kl._MERKMALE) == set(config.AUFGABEN_KATEGORIEN), set(kl._MERKMALE) ^ set(config.AUFGABEN_KATEGORIEN)
    assert all(len(m) > 15 and "Sklav" not in m and "Herrin" not in m for m in kl._MERKMALE.values())
    assert kl.merkmal("Facesitting") and kl.merkmal("Gibt es nicht") == ""


async def test_beleg_urteil_je_kategorie():
    alt = (kl.grok.simple, kl.grok.clean_text)
    kl.grok.clean_text = lambda x: x
    etiketten = ["Sissy_Training", "Facesitting", "Eigene_Kategorie"]
    text = "Er trägt heute ein Kleid und Nagellack und übt vor dem Spiegel einen Knicks."
    try:
        kl.grok.simple = AsyncMock(return_value="Sissy_Training: JA\nFacesitting: NEIN\nEigene_Kategorie: NEIN")
        assert await kl.kategorien_im_text_belegt(text, etiketten) == ["Sissy_Training"]
        prompt = kl.grok.simple.call_args.args[0]
        system = kl.grok.simple.call_args.kwargs["system"]
        assert f"- Sissy_Training – Merkmal: {kl.merkmal('Sissy_Training')}" in prompt
        assert "\n- Eigene_Kategorie\n" in prompt                     # ohne hinterlegtes Merkmal: nur der Name
        assert "Name: JA oder Name: NEIN" in system and "JEDE Kategorie einzeln" in system
        # Formvarianten der Antwort
        kl.grok.simple = AsyncMock(return_value="- **Sissy_Training**: Ja, eindeutig\n- Facesitting: nein")
        assert await kl.kategorien_im_text_belegt(text, etiketten) == ["Sissy_Training"]
        # Urteil nur für einen Teil → der Rest gilt als nicht belegt
        kl.grok.simple = AsyncMock(return_value="Facesitting: JA")
        assert await kl.kategorien_im_text_belegt(text, etiketten) == ["Facesitting"]
        # die alte Ein-Wort-Antwort ist kein Urteil mehr → Stichwort-Abgleich
        kl.grok.simple = AsyncMock(return_value="KEINE")
        assert await kl.kategorien_im_text_belegt(text, etiketten) == []
        assert await kl.kategorien_im_text_belegt("Eine Runde Sissy-Training vor dem Spiegel.", etiketten) == ["Sissy_Training"]
    finally:
        kl.grok.simple, kl.grok.clean_text = alt


async def test_beleg_kosename_zaehlt_auch_als_stichwort_nicht():
    alt = (kl.grok.simple, kl.grok.clean_text)
    kl.grok.clean_text = lambda x: x
    kl.grok.simple = AsyncMock(return_value="Pet_Play: NEIN\nDienst: JA")
    try:
        text = "Der Brave Welpe deckt den Tisch, dann räumt der Brave Welpe die Küche auf."
        with _Persona(sklave_anrede="Braver Welpe"):
            assert await kl.kategorien_im_text_belegt(text, ["Pet_Play", "Dienst"]) == ["Dienst"]
        # ohne die Anrede ist „Welpe“ ein wörtlicher Treffer und wird ergänzt
        with _Persona(sklave_anrede=""):
            assert await kl.kategorien_im_text_belegt(text, ["Pet_Play", "Dienst"]) == ["Pet_Play", "Dienst"]
    finally:
        kl.grok.simple, kl.grok.clean_text = alt


# --------------------------------------------------------------------------
# 6. Termin einer Aufgabe
# --------------------------------------------------------------------------

def test_lohn_satz_bestimmt_den_termin_nicht():
    heute = date(2026, 7, 22)   # Mittwoch
    f = de.finde_aufgaben_termin
    heute_faellig = [
        "Er muss heute Abend kochen. Als Belohnung bekommt er dann morgen Abend ein Eis.",
        "Heute Abend das Zimmer aufräumen – Wäsche falten, Bett machen; Belohnung ist morgen Abend ein Kinobesuch",
        "Heute Abend kochen. Wenn er das gut macht, darf er am Samstag ausschlafen.",
        "Die Küche putzen; dafür bekommt er übermorgen frei",
        "Clean the kitchen tonight. As a reward he may sleep in tomorrow.",
        "Er soll heute brav sein", "",
    ]
    for text in heute_faellig:
        assert f(text, heute) is None, (text, f(text, heute))
    spaeter = [
        ("Er war heute frech, dafür muss er morgen Abend die Küche putzen", date(2026, 7, 23)),
        ("Zur Strafe trägt er morgen den ganzen Tag die Schürze", date(2026, 7, 23)),
        ("Morgen Abend das Zimmer aufräumen. Als Belohnung darf er am Samstag ausschlafen.", date(2026, 7, 23)),
        ("Er soll am Samstag das Bad putzen", date(2026, 7, 25)),
        ("Am 26.07. wäscht er das Auto", date(2026, 7, 26)),
    ]
    for text, soll in spaeter:
        assert f(text, heute)[0] == soll, (text, f(text, heute))
    # finde_termin selbst bleibt unverändert – Zeiträume („heute bis Sonntag“) bauen darauf
    assert de.finde_termin("Heute Abend kochen. Als Belohnung bekommt er morgen ein Eis.", heute)[0] == date(2026, 7, 23)
    assert de.finde_termin("heute bis Sonntag", heute)[0] == date(2026, 7, 26)


def test_aufgaben_termin_aus_wortlaut_dann_aufgabe():
    alt = de._heute
    de._heute = lambda: date(2026, 7, 22)
    try:
        lohn = "Er soll heute Abend kochen. Als Belohnung gibt es morgen ein Eis."
        assert domina._aufgaben_termin(lohn, "Heute Abend kochen; Belohnung ist morgen ein Eis") is None
        assert domina._aufgaben_termin("Gib ihm das bitte auf", "Am Samstag das Bad putzen")[0] == date(2026, 7, 25)
        assert domina._aufgaben_termin("Er soll morgen kochen", "Kochen")[0] == date(2026, 7, 23)
        assert domina._aufgaben_termin("", "") is None
    finally:
        de._heute = alt


if __name__ == "__main__":
    test_ortswoerter_nur_als_ort()
    test_abstand_am_gespraechs_kontext()
    test_prompt_nennt_heute_und_die_umrechnung()
    test_thema_pausiert()
    test_pausierte_kategorie_wird_nicht_gewaehlt()
    asyncio.run(test_buchung_setzt_und_hebt_die_pause())
    asyncio.run(test_nur_der_grund_thema_setzt_die_pause())
    test_prompt_nennt_pause_und_abgelehntes_thema()
    asyncio.run(test_abgelehnte_vorschlaege_liefern_das_kurzlabel())
    test_namens_regel_nur_in_fertigen_nachrichten()
    test_namens_filter()
    test_merkmale_decken_den_katalog()
    asyncio.run(test_beleg_urteil_je_kategorie())
    asyncio.run(test_beleg_kosename_zaehlt_auch_als_stichwort_nicht())
    test_lohn_satz_bestimmt_den_termin_nicht()
    test_aufgaben_termin_aus_wortlaut_dann_aufgabe()
    print("✅ Alle Review-D12-Tests bestanden")
