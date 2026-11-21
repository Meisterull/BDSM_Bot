"""
Regressions-Tests für die tägliche Stimmungsfrage an den Sub (Befund 27.08.2026:
26. und 27.08. ging zeichengleich dieselbe Frage raus, davor zwei Tage lang
dasselbe Wetter-Bild – und am 25.08. der Richtungs-Seed im Wortlaut).

  S1 – Alle Versuche zu ähnlich → statischer Standardtext, NICHT der als
       Wiederholung erkannte Kandidat.
  S2 – Ein akzeptierter Kandidat wird samt benutzter Richtung zurückgegeben.
  S3 – Retries ziehen Richtungen ohne Zurücklegen und meiden die zuletzt
       benutzten.
  S4 – Die Richtungen sind Stichworte, keine fertigen Fragesätze, und der
       Prompt verbietet das wörtliche Abschreiben.
  S5 – Antworten zum Antippen passend zur Frage (17.11.2026): Etikett wird
       angezeigt, Klartext gespeichert, die Frage geht in Reaktion und Hinweis;
       unbrauchbare Ausgabe → ein Nachbesserungs-Versuch, sonst feste Knöpfe.

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_stimmung.py
"""
import asyncio
import os
import sys
import tempfile

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
from bot.services.db import progress as db_progress, vorschlaege as db_vorschlaege  # noqa: E402

_config.DOMINA_CHAT_ID = "111"
_config.SKLAVE_CHAT_ID = "222"
_config.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")

from bot.handlers import stimmung as _st  # noqa: E402
from bot.prompts import followup as _fp  # noqa: E402
from bot.messages import t  # noqa: E402

WETTER = "Ich frage mich gerade, wenn dein Tag ein Wetter wäre – welches würde es sein?"


def _mit_stubs(eintraege, antworten):
    """Patcht Sperr-Listen-Abfrage und LLM; gibt eine Restore-Funktion zurück."""
    orig_q, orig_g = db_vorschlaege.get_recent_stimmung_eintraege, _st.grok.simple
    db_vorschlaege.get_recent_stimmung_eintraege = AsyncMock(return_value=eintraege)
    rest = list(antworten)
    async def _simple(*a, **k):
        return rest.pop(0) if rest else antworten[-1]
    _st.grok.simple = _simple

    def restore():
        db_vorschlaege.get_recent_stimmung_eintraege = orig_q
        _st.grok.simple = orig_g
    return restore


# --------------------------------------------------------------------------
# S1 – erkannte Wiederholung geht NICHT raus
# --------------------------------------------------------------------------

async def test_alle_versuche_zu_aehnlich_faellt_auf_standardtext():
    restore = _mit_stubs([{"zusammenfassung": WETTER, "richtung": "ein Wetter-Bild für den heutigen Tag"}],
                         [WETTER] * 5)
    try:
        frage, richtung = await _st._frage_text()
        assert frage == t("STIMMUNG_FRAGE"), f"Wiederholung gesendet: {frage!r}"
        assert richtung == ""
    finally:
        restore()


# --------------------------------------------------------------------------
# S2 – akzeptierter Kandidat kommt mit seiner Richtung zurück
# --------------------------------------------------------------------------

async def test_frische_frage_wird_mit_richtung_zurueckgegeben():
    restore = _mit_stubs([{"zusammenfassung": WETTER, "richtung": "ein Wetter-Bild für den heutigen Tag"}],
                         ["Sag mir, wie viel Kraft heute noch in dir steckt."])
    try:
        frage, richtung = await _st._frage_text()
        assert frage == "Sag mir, wie viel Kraft heute noch in dir steckt."
        assert richtung in _fp.stimmung_richtungen()
        assert richtung != "ein Wetter-Bild für den heutigen Tag"
    finally:
        restore()


# --------------------------------------------------------------------------
# S2b – verworfener Kandidat landet in der Sperr-Liste des nächsten Versuchs
# --------------------------------------------------------------------------

async def test_verworfener_kandidat_geht_in_die_naechste_sperrliste():
    prompts = []
    orig_q, orig_g = db_vorschlaege.get_recent_stimmung_eintraege, _st.grok.simple
    db_vorschlaege.get_recent_stimmung_eintraege = AsyncMock(
        return_value=[{"zusammenfassung": WETTER, "richtung": "ein Wetter-Bild für den heutigen Tag"}])
    antworten = [WETTER, "Sag mir, wie schwer der Tag heute auf dir liegt."]

    async def _simple(prompt, **k):
        prompts.append(prompt[0])
        return antworten[len(prompts) - 1]
    _st.grok.simple = _simple
    try:
        frage, _ = await _st._frage_text()
        assert frage == "Sag mir, wie schwer der Tag heute auf dir liegt."
        assert len(prompts) == 2
        assert WETTER not in prompts[0].split("So hast du zuletzt gefragt")[0]
        # Versuch 2 kennt den verworfenen Kandidaten aus Versuch 1
        assert prompts[1].count(WETTER) >= 2
    finally:
        db_vorschlaege.get_recent_stimmung_eintraege, _st.grok.simple = orig_q, orig_g


# --------------------------------------------------------------------------
# S3 – Richtungen ohne Zurücklegen, verbrauchte hinten anstellen
# --------------------------------------------------------------------------

def test_richtungen_ohne_zuruecklegen():
    alle = _fp.stimmung_richtungen()
    verbrauchte = set(alle[:5])
    for _ in range(50):
        k = _st._richtungs_kandidaten(verbrauchte)
        assert len(k) == _st._VERSUCHE
        assert len(set(k)) == len(k), f"Richtung doppelt gezogen: {k}"
        assert not (set(k) & verbrauchte), f"Zuletzt benutzte Richtung wiederverwendet: {k}"
    # Notausgang: sind fast alle verbraucht, wird trotzdem aufgefüllt
    k = _st._richtungs_kandidaten(set(alle[1:]))
    assert len(k) == _st._VERSUCHE and len(set(k)) == _st._VERSUCHE


# --------------------------------------------------------------------------
# S4 – Stichworte statt fertiger Fragesätze + Abschreib-Verbot im Prompt
# --------------------------------------------------------------------------

def test_richtungen_sind_stichworte():
    import re
    for r in _fp.stimmung_richtungen():
        assert not r.endswith("?"), f"Fertiger Fragesatz als Seed: {r!r}"
        assert not r[0].isupper(), f"Seed liest sich wie ein eigener Satz: {r!r}"
        # Keine 2. Person: "dir/du" meint im Prompt die Dom-Seite. Schreibt das
        # Modell das Stichwort ab, kippt die Anrede auf den Sub und die Richtung
        # dreht sich um (Befund 27.08.2026).
        assert not re.search(r"\b(du|dir|dich|dein\w*)\b", r, re.I), \
            f"Zweite Person im Seed kippt die Richtung: {r!r}"
        # Ebenso keine harten Sub-Pronomen – die Rolle ist konfigurierbar.
        assert not re.search(r"\b(er|ihn|ihm|sein\w*|sie|ihr\w*)\b", r, re.I), \
            f"Festes Rollen-Pronomen im Seed: {r!r}"


def test_prompt_verbietet_woertliche_uebernahme():
    system, _ = _fp.stimmung_abfragen(vermeiden=[WETTER], richtung="wo der Kopf heute steht")
    assert "wo der Kopf heute steht" in system
    assert "NICHT wörtlich" in system
    assert WETTER in system  # Sperr-Liste steht drin


# --------------------------------------------------------------------------
# S5 – die gesperrten Satzanfänge stehen wörtlich im Prompt
# --------------------------------------------------------------------------

def test_prompt_nennt_gesperrte_satzanfaenge():
    vermeiden = [
        "Ich frage mich gerade, wie dein Tag war?",
        "Ich frage mich, was dich heute trägt?",
        "Wenn dein Tag ein Wetter wäre – welches?",
        "Kurz",  # zu kurz für einen Anfang – darf nicht crashen
    ]
    system, _ = _fp.stimmung_abfragen(vermeiden=vermeiden, richtung="wo der Kopf heute steht")
    assert '"Ich frage mich"' in system
    assert '"Wenn dein Tag"' in system
    # Dubletten nur einmal, "Kurz" gar nicht
    assert system.count('"Ich frage mich"') == 1
    assert '"Kurz"' not in system


def test_satzanfaenge_dedupe_und_kurzfilter():
    assert _fp._satzanfaenge(["Ich frage mich, was?", "ich frage mich noch mehr", "Zu kurz"]) == \
        ["Ich frage mich"]
    assert _fp._satzanfaenge([]) == []
    assert _fp._satzanfaenge(["", None]) == []


async def test_frage_ohne_mode_knopf_und_getippte_antwort():
    """Die Stimmungsfrage belegt den Chat nicht mehr: fünf Knöpfe + Merkmarke.
    Ein Tipp speichert die Stimmung (ausführlicher Wert, nicht das Etikett),
    löst Reaktion und Dom-Hinweis aus; ein zweiter Tipp verbucht nichts doppelt.
    Eine getippte Nachricht zählt im Fenster weiter als Antwort – danach nicht mehr."""
    from unittest.mock import MagicMock
    from bot import state as _state
    sub = _config.SKLAVE_CHAT_ID

    class _Knopf:                      # telegram ist lokal ein Stub (s. Kopf)
        def __init__(self, text, callback_data=None):
            self.text, self.callback_data = text, callback_data

    class _Tasten:
        def __init__(self, zeilen):
            self.inline_keyboard = zeilen

    orig = (_st.InlineKeyboardButton, _st.InlineKeyboardMarkup, db_progress.save_training,
            db_vorschlaege.get_recent_stimmung_eintraege, _st.grok.simple, _st.telegram_helper.send_domina,
            _config.STIMMUNG_ENABLED)
    _st.InlineKeyboardButton, _st.InlineKeyboardMarkup = _Knopf, _Tasten
    gespeichert = []
    db_progress.save_training = AsyncMock(side_effect=lambda rolle, daten: gespeichert.append(daten))
    db_vorschlaege.get_recent_stimmung_eintraege = AsyncMock(return_value=[])
    _st.grok.simple = AsyncMock(return_value="Eine frische Frage an dich?")
    _st.telegram_helper.send_domina = AsyncMock()
    _config.STIMMUNG_ENABLED = True

    def _tap(daten, mid=77):
        upd = MagicMock()
        upd.effective_chat.id = sub
        upd.callback_query.data = daten
        upd.callback_query.answer = AsyncMock()
        upd.callback_query.edit_message_reply_markup = AsyncMock()
        upd.callback_query.message.message_id = mid
        upd.callback_query.message.reply_text = AsyncMock()
        return upd

    def _text(text):
        upd = MagicMock()
        upd.effective_chat.id = sub
        upd.message.text = text
        upd.message.reply_text = AsyncMock()
        return upd

    try:
        _state._state.clear()
        bot = MagicMock()
        bot.send_message = AsyncMock()
        await _st.frage_stellen(bot)
        assert _state.get_mode(sub) in ("chat", None), "die Frage darf den Chat nicht belegen"
        assert _st.wartet_auf_antwort(sub)
        tasten = bot.send_message.call_args.kwargs["reply_markup"]
        daten = [k.callback_data for zeile in tasten.inline_keyboard for k in zeile]
        assert daten == [f"stimmung:{n}" for n in "12345"], daten
        assert [d["typ"] for d in gespeichert] == ["stimmung_frage"]

        # Ein Tipp: Stimmung gespeichert, Reaktion + Hinweis, Marke weg
        ctx = MagicMock()
        ctx.bot = bot
        u = _tap("stimmung:4")
        await _st.callback_button(u, ctx)
        stimmungen = [d for d in gespeichert if d["typ"] == "stimmung"]
        assert stimmungen == [{"typ": "stimmung", "zusammenfassung": t("STIMMUNG_WERT_4")}], stimmungen
        assert t("STIMMUNG_WERT_4") != t("BUTTON_STIMMUNG_4")
        u.callback_query.message.reply_text.assert_awaited_once()
        _st.telegram_helper.send_domina.assert_awaited_once()
        assert not _st.wartet_auf_antwort(sub) and _state.get_mode(sub) in ("chat", None)

        # Zweiter Tipp auf dieselbe Frage und ein unbekannter Wert: nichts dazu
        await _st.callback_button(_tap("stimmung:1"), ctx)
        await _st.callback_button(_tap("stimmung:9", mid=78), ctx)
        assert len([d for d in gespeichert if d["typ"] == "stimmung"]) == 1

        # Fremder Chat darf nicht für ihn tippen
        fremd = _tap("stimmung:2", mid=79)
        fremd.effective_chat.id = _config.DOMINA_CHAT_ID
        await _st.callback_button(fremd, ctx)
        assert len([d for d in gespeichert if d["typ"] == "stimmung"]) == 1

        # Getippte Antwort im Fenster zählt weiter als Stimmung …
        await _st.frage_stellen(bot)
        assert _st.wartet_auf_antwort(sub)
        await _st.handle_antwort(_text("müde, aber zufrieden"), ctx)
        assert [d["zusammenfassung"] for d in gespeichert if d["typ"] == "stimmung"][-1] == "müde, aber zufrieden"
        assert not _st.wartet_auf_antwort(sub)

        # … nach Ablauf des Fensters nicht mehr
        await _st.frage_stellen(bot)
        _state.get(sub)["stimmung_offen_bis"] = 1.0
        assert not _st.wartet_auf_antwort(sub)
        assert "stimmung_offen_bis" not in _state.get(sub)
    finally:
        _state._state.clear()
        (_st.InlineKeyboardButton, _st.InlineKeyboardMarkup, db_progress.save_training,
         db_vorschlaege.get_recent_stimmung_eintraege, _st.grok.simple, _st.telegram_helper.send_domina,
         _config.STIMMUNG_ENABLED) = orig


# --------------------------------------------------------------------------
# S5 – Antworten zum Antippen, passend zur Frage
# --------------------------------------------------------------------------

FRAGE = "Ist dein Kopf heute eher ein stiller See oder ein voller Bahnhof?"
OPTIONEN_JSON = (
    '{"antworten": ['
    '{"knopf": "Stiller See", "klartext": "ruhig, gesammelt"},'
    '{"knopf": "Voller Bahnhof.", "klartext": "Er ist unruhig, viel im Kopf"},'
    '{"knopf": "Irgendwo dazwischen", "klartext": "gemischt, mal so mal so"},'
    '{"knopf": "Weder noch", "klartext": "müde, eher leer"}]}')


class _Knopf:                          # telegram ist lokal ein Stub (s. Kopf)
    def __init__(self, text, callback_data=None):
        self.text, self.callback_data = text, callback_data


class _Tasten:
    def __init__(self, zeilen):
        self.inline_keyboard = zeilen


def test_optionen_lesen():
    o = _st._optionen_lesen(OPTIONEN_JSON)
    assert [x["knopf"] for x in o] == ["Stiller See", "Voller Bahnhof", "Irgendwo dazwischen", "Weder noch"], o
    # führendes „Er ist" fällt aus dem Klartext, der Schlusspunkt aus dem Etikett
    assert o[1]["klartext"] == "unruhig, viel im Kopf" and o[0]["klartext"] == "ruhig, gesammelt", o
    # in Code-Zaun verpackt, mit Überlänge, Doppel, fehlendem Klartext und einer fünften Antwort
    roh = ('```json\n{"antworten": [{"knopf": "Gut", "klartext": "zufrieden"},'
           '{"knopf": "gut!", "klartext": "doppelt"},'
           '{"knopf": "' + "x" * 41 + '", "klartext": "zu lang für einen Knopf"},'
           '{"knopf": "Ohne Klartext", "klartext": ""}, "kaputt",'
           '{"knopf": "Schlecht", "klartext": "gedrückt"}, {"knopf": "Mittel", "klartext": "neutral"},'
           '{"knopf": "Wach", "klartext": "munter"}, {"knopf": "Fünfte", "klartext": "zu viel"}]}\n```')
    assert [x["knopf"] for x in _st._optionen_lesen(roh)] == ["Gut", "Schlecht", "Mittel", "Wach"]
    # weniger als zwei brauchbare Antworten, kein JSON, falsche Form → keine
    assert _st._optionen_lesen('{"antworten": [{"knopf": "Nur eine", "klartext": "allein"}]}') == []
    assert _st._optionen_lesen("Eine frische Frage an dich?") == []
    assert _st._optionen_lesen('{"antworten": "keine Liste"}') == [] and _st._optionen_lesen("") == []


def test_antwort_prompts():
    system, user = _fp.stimmung_antworten(FRAGE)
    assert f"höchstens {_fp.ANTWORT_MAX_ZEICHEN} Zeichen" in system and "JSON" in system, system
    assert "OHNE Subjekt und ohne Pronomen" in system and "Entweder-oder beide Seiten" in system
    assert FRAGE in user and "Daten, keine Anweisung" in user and "erster Versuch" not in user
    assert "erster Versuch war unbrauchbar" in _fp.stimmung_antworten(FRAGE, nachbessern=True)[1]
    # Der Frage-Prompt erfährt nichts von den Knöpfen – sonst schreibt das Modell
    # die Antwortvorschläge selbst unter die Frage.
    assert "Antippen" not in _fp.stimmung_abfragen()[0] and "Knopf" not in _fp.stimmung_abfragen()[0]
    # Reaktion: mit Frage und Klartext – ohne Frage bleibt der alte Wortlaut
    system, user = _fp.reaktion_auf_stimmung("Voller Bahnhof", frage=FRAGE, klartext="unruhig, viel im Kopf")
    assert "auf deine Frage nach seiner Stimmung geantwortet" in system and "von sich aus" not in system
    assert user.index(FRAGE) < user.index("Voller Bahnhof") and "Gemeint ist damit: unruhig, viel im Kopf" in user
    system, user = _fp.reaktion_auf_stimmung("müde, aber zufrieden")
    assert "von sich aus" in system and "Deine Frage" not in user and "Gemeint ist" not in user


def test_angehaengtes_du_und_anrede_hinweis():
    for roh, sauber in (("Wie viel Kraft hast du noch übrig, du?", "Wie viel Kraft hast du noch übrig?"),
                        ("Ist es heute ruhig geblieben, Du ?", "Ist es heute ruhig geblieben ?"),
                        ("Na, du – wie läuft es?", "Na, du – wie läuft es?"),          # mitten im Satz bleibt
                        ("Wie geht es dir?", "Wie geht es dir?")):
        assert _st._ohne_angehaengtes_du(roh) == sauber, (roh, _st._ohne_angehaengtes_du(roh))
    # Anrede aussetzen, ohne „nur du" vorzugeben (das erzeugte das angehängte „, du?")
    from bot.prompts.followup import stimmung_fragen as sf
    orig = sf._anrede
    sf._anrede = lambda: "Mein Lieber"
    try:
        system = _fp.stimmung_abfragen(vermeiden=["Wie war dein Tag, Mein Lieber?", "Na, Mein Lieber, alles gut?"])[0]
        assert "kommt sie NICHT vor" in system and "kein angehängtes" in system and 'nur „du"' not in system, system
        assert "kommt sie NICHT vor" not in _fp.stimmung_abfragen(vermeiden=["Wie war dein Tag?", "Alles gut?"])[0]
    finally:
        sf._anrede = orig


async def test_passende_antworten_klick_und_text():
    """Frage mit vier passenden Antworten: Etikett auf dem Knopf, Klartext in
    der Ablage, Frage in Reaktion und Hinweis. Eintrag weg → Etikett aus der
    Nachricht. Getippte Antwort behält die Frage."""
    from unittest.mock import MagicMock
    from bot import state as _state
    sub = _config.SKLAVE_CHAT_ID
    orig = (_st.InlineKeyboardButton, _st.InlineKeyboardMarkup, db_progress.save_training,
            db_progress.get_training_by_id, db_vorschlaege.get_recent_stimmung_eintraege, _st.grok.simple,
            _st.telegram_helper.send_domina, _config.STIMMUNG_ENABLED)
    _st.InlineKeyboardButton, _st.InlineKeyboardMarkup = _Knopf, _Tasten
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
            return OPTIONEN_JSON
        if "Formuliere jetzt genau EINE solche Frage" in user:
            return FRAGE
        return "Kurze Reaktion."

    db_progress.save_training = _save
    db_progress.get_training_by_id = AsyncMock(
        side_effect=lambda pid, user_id="domina": next(
            (d for d, i in zip(gespeichert, ids) if i == pid and d["typ"] == "stimmung_frage"), None))
    db_vorschlaege.get_recent_stimmung_eintraege = AsyncMock(return_value=[])
    _st.grok.simple = _simple
    _st.telegram_helper.send_domina = AsyncMock()
    _config.STIMMUNG_ENABLED = True

    def _tap(daten, tasten=None, mid=77):
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
        _state._state.clear()
        bot = MagicMock()
        bot.send_message = AsyncMock()
        ctx = MagicMock()
        ctx.bot = bot
        await _st.frage_stellen(bot)
        tasten = bot.send_message.call_args.kwargs["reply_markup"]
        assert [len(z) for z in tasten.inline_keyboard] == [1, 1, 1, 1], "ein Knopf je Zeile"
        knoepfe = [z[0] for z in tasten.inline_keyboard]
        assert [k.text for k in knoepfe] == ["Stiller See", "Voller Bahnhof", "Irgendwo dazwischen", "Weder noch"]
        frage_id = ids[-1]
        assert frage_id and [k.callback_data for k in knoepfe] == [f"stimmung:w:{frage_id}:{i}" for i in range(4)]
        assert all(len(k.callback_data.encode()) <= 64 for k in knoepfe)
        eintrag = gespeichert[-1]
        assert eintrag["typ"] == "stimmung_frage" and eintrag["zusammenfassung"] == FRAGE
        assert [o["knopf"] for o in eintrag["antworten"]][1] == "Voller Bahnhof"
        assert _state.get_mode(sub) in ("chat", None) and _st.wartet_auf_antwort(sub)

        # Tipp auf die zweite Antwort
        u = _tap(knoepfe[1].callback_data, tasten)
        await _st.callback_button(u, ctx)
        u.callback_query.edit_message_text.assert_awaited_once_with(f"{FRAGE}\n\n➜ Voller Bahnhof")
        stimmung = [d for d in gespeichert if d["typ"] == "stimmung"]
        assert stimmung == [{"typ": "stimmung", "zusammenfassung": "unruhig, viel im Kopf",
                             "frage": FRAGE, "knopf": "Voller Bahnhof"}], stimmung
        assert _state.get_history(sub)[-2] == {"role": "user", "content": "Voller Bahnhof"}
        reaktion = next(p for p in prompts if "auf deine Frage nach" in p[0])
        assert FRAGE in reaktion[1] and "Voller Bahnhof" in reaktion[1] and "unruhig, viel im Kopf" in reaktion[1]
        hinweis = next(p for p in prompts if "Die Frage an ihn" in p[1])
        assert "auf die Frage nach seiner Stimmung geantwortet" in hinweis[0] and "Gemeint ist damit" in hinweis[1]
        # Im Kopf des Hinweises stehen Frage und Antwort wörtlich, nicht der Klartext (21.11.2026)
        gesendet = _st.telegram_helper.send_domina.await_args.args[1]
        assert f"Frage: _{FRAGE}_" in gesendet and "Antwort: _Voller Bahnhof_" in gesendet, gesendet
        assert "unruhig, viel im Kopf" not in gesendet
        assert not _st.wartet_auf_antwort(sub)

        # zweiter Tipp auf dieselbe Nachricht, kaputte ID, unbekanntes Format: nichts dazu
        await _st.callback_button(_tap(knoepfe[0].callback_data, tasten), ctx)
        await _st.callback_button(_tap("stimmung:w:keine-uuid:0", tasten, mid=78), ctx)
        await _st.callback_button(_tap("stimmung:w", tasten, mid=79), ctx)
        assert len([d for d in gespeichert if d["typ"] == "stimmung"]) == 1

        # Eintrag zur Frage nicht auffindbar → Etikett aus der Nachricht, ohne Klartext
        fremd_id = "00000000-0000-4000-8000-000000000000"
        alt_tasten = _Tasten([[_Knopf("Stiller See", f"stimmung:w:{fremd_id}:0")]])
        u = _tap(f"stimmung:w:{fremd_id}:0", alt_tasten, mid=80)
        await _st.callback_button(u, ctx)
        assert [d for d in gespeichert if d["typ"] == "stimmung"][-1] == {
            "typ": "stimmung", "zusammenfassung": "Stiller See", "frage": FRAGE}
        # … und ohne Etikett in der Nachricht gar nichts
        await _st.callback_button(_tap(f"stimmung:w:{fremd_id}:3", alt_tasten, mid=81), ctx)
        assert len([d for d in gespeichert if d["typ"] == "stimmung"]) == 2

        # Getippte Antwort: Rohtext bleibt die Stimmung, die Frage hängt am Eintrag und im Prompt
        prompts.clear()
        await _st.frage_stellen(bot)
        text = MagicMock()
        text.effective_chat.id = sub
        text.message.text = "eher Bahnhof, aber es geht"
        text.message.reply_text = AsyncMock()
        await _st.handle_antwort(text, ctx)
        assert [d for d in gespeichert if d["typ"] == "stimmung"][-1] == {
            "typ": "stimmung", "zusammenfassung": "eher Bahnhof, aber es geht", "frage": FRAGE}
        assert any("auf deine Frage nach" in p[0] and FRAGE in p[1] for p in prompts)
        assert "stimmung_offen_frage" not in _state.get(sub)
    finally:
        _state._state.clear()
        (_st.InlineKeyboardButton, _st.InlineKeyboardMarkup, db_progress.save_training,
         db_progress.get_training_by_id, db_vorschlaege.get_recent_stimmung_eintraege, _st.grok.simple,
         _st.telegram_helper.send_domina, _config.STIMMUNG_ENABLED) = orig


async def test_befehl_stimmung_mit_passenden_antworten():
    """/stimmung nimmt denselben Weg wie der Zeit-Job: Frage + passende Antworten."""
    from unittest.mock import MagicMock
    from bot import state as _state
    sub = _config.SKLAVE_CHAT_ID
    orig = (_st.InlineKeyboardButton, _st.InlineKeyboardMarkup, db_progress.save_training,
            db_vorschlaege.get_recent_stimmung_eintraege, _st.grok.simple)
    _st.InlineKeyboardButton, _st.InlineKeyboardMarkup = _Knopf, _Tasten
    gespeichert = []

    async def _save(rolle, daten, point_id=None):
        gespeichert.append((dict(daten), point_id))
        return point_id or "neu"

    async def _simple(prompt, **kw):
        return OPTIONEN_JSON if "Antworten zum Antippen. Du schreibst" in prompt[0] else FRAGE

    db_progress.save_training = _save
    db_vorschlaege.get_recent_stimmung_eintraege = AsyncMock(return_value=[])
    _st.grok.simple = _simple
    try:
        _state._state.clear()
        upd = MagicMock()
        upd.effective_chat.id = sub
        upd.message.reply_text = AsyncMock()
        ctx = MagicMock()
        ctx.bot.send_chat_action = AsyncMock()
        await _st.start(upd, ctx)
        assert upd.message.reply_text.await_args.args[0] == FRAGE
        tasten = upd.message.reply_text.await_args.kwargs["reply_markup"]
        daten, frage_id = gespeichert[-1]
        assert [z[0].callback_data for z in tasten.inline_keyboard] == [f"stimmung:w:{frage_id}:{i}" for i in range(4)]
        assert daten["typ"] == "stimmung_frage" and len(daten["antworten"]) == 4
        assert _st.wartet_auf_antwort(sub) and _state.get(sub)["stimmung_offen_frage"] == FRAGE
        assert _state.get_history(sub)[-1] == {"role": "assistant", "content": FRAGE}
    finally:
        _state._state.clear()
        (_st.InlineKeyboardButton, _st.InlineKeyboardMarkup, db_progress.save_training,
         db_vorschlaege.get_recent_stimmung_eintraege, _st.grok.simple) = orig


async def test_antworten_nachbessern_und_rueckfall():
    """Unbrauchbare Ausgabe → genau ein Nachbesserungs-Versuch; bleibt es
    unbrauchbar oder fällt das LLM aus → keine Optionen (feste Knöpfe)."""
    orig = _st.grok.simple
    aufrufe = []
    zu_lang = '{"antworten": [{"knopf": "' + "a" * 50 + '", "klartext": "x y z"}, {"knopf": "' + "b" * 50 + '", "klartext": "x y z"}]}'
    try:
        folge = [zu_lang, OPTIONEN_JSON]

        async def _erst_schlecht(prompt, **kw):
            aufrufe.append(prompt[1])
            return folge.pop(0)
        _st.grok.simple = _erst_schlecht
        o = await _st._antwort_optionen(FRAGE)
        assert len(o) == 4 and len(aufrufe) == 2
        assert "erster Versuch" not in aufrufe[0] and "erster Versuch war unbrauchbar" in aufrufe[1]

        aufrufe.clear()

        async def _immer_schlecht(prompt, **kw):
            aufrufe.append(prompt[1])
            return zu_lang
        _st.grok.simple = _immer_schlecht
        assert await _st._antwort_optionen(FRAGE) == [] and len(aufrufe) == 2

        _st.grok.simple = AsyncMock(side_effect=RuntimeError("llm weg"))
        assert await _st._antwort_optionen(FRAGE) == []
        # Standardfrage bekommt keine erzeugten Antworten (und keinen LLM-Aufruf)
        _st.grok.simple = AsyncMock(return_value=OPTIONEN_JSON)
        assert await _st._antwort_optionen(_st._fallback_frage()) == []
        assert _st.grok.simple.await_count == 0
        # Tasten ohne Optionen = die fünf festen
        orig_k = (_st.InlineKeyboardButton, _st.InlineKeyboardMarkup)
        _st.InlineKeyboardButton, _st.InlineKeyboardMarkup = _Knopf, _Tasten
        try:
            fest = _st._tasten(None, [])
            assert [k.callback_data for z in fest.inline_keyboard for k in z] == [f"stimmung:{n}" for n in "12345"]
        finally:
            _st.InlineKeyboardButton, _st.InlineKeyboardMarkup = orig_k
    finally:
        _st.grok.simple = orig


def main():
    for coro in (
        test_frage_ohne_mode_knopf_und_getippte_antwort,
        test_alle_versuche_zu_aehnlich_faellt_auf_standardtext,
        test_frische_frage_wird_mit_richtung_zurueckgegeben,
        test_verworfener_kandidat_geht_in_die_naechste_sperrliste,
        test_passende_antworten_klick_und_text,
        test_befehl_stimmung_mit_passenden_antworten,
        test_antworten_nachbessern_und_rueckfall,
    ):
        asyncio.run(coro())
        print(f"✅ {coro.__name__}")
    for fn in (
        test_richtungen_ohne_zuruecklegen,
        test_richtungen_sind_stichworte,
        test_prompt_verbietet_woertliche_uebernahme,
        test_prompt_nennt_gesperrte_satzanfaenge,
        test_satzanfaenge_dedupe_und_kurzfilter,
        test_optionen_lesen,
        test_antwort_prompts,
        test_angehaengtes_du_und_anrede_hinweis,
    ):
        fn()
        print(f"✅ {fn.__name__}")
    print("✅ Alle Stimmungs-Tests bestanden")


if __name__ == "__main__":
    main()
