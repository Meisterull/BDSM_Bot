"""
Tests: Der Coach kennt, was sonst noch im Chat der Dom-Seite steht (17.11.2026).

Live-Befund: Auf eine automatische Info, die wie eine Auswahl endete, kam als
Antwort ein einzelnes Wort. Der Coach kannte die Info nicht, bezog das Wort auf
den letzten Auftrag in seinem Verlauf und schickte ihn noch einmal los.

  - state merkt sich je Chat die jüngsten Bot-Nachrichten außerhalb des Verlaufs
    (höchstens sechs, höchstens 24 Stunden, über Neustarts hinweg)
  - der zentrale Sende-Wrapper merkt nur Zugestelltes und nur für die Dom-Seite
  - der Coach bekommt sie als Vorspann der neuen Nachricht – mit Zeitangabe,
    die jüngste ausführlich, ältere gekürzt; der gespeicherte Verlauf behält
    den Wortlaut der Dom-Seite
  - bloße Quittungen („✅ Abgebrochen.") bekommt er nicht zu sehen
  - nach seiner Antwort sind sie abgehakt, bei einem Modell-Fehler nicht
  - was ohnehin im Verlauf steht (Coach-Antwort, Trainings-Übung), wird nicht
    zusätzlich gemerkt
  - die Wett-Info endet nicht mehr mit einer Auswahl

    python3 tests/test_coach_automatik.py
"""
import asyncio
import contextlib
import logging
import os
import sys
import tempfile
import time
from datetime import datetime
from types import SimpleNamespace
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
from bot.app import routing  # noqa: E402
from bot.handlers import domina  # noqa: E402
from bot.handlers import training as tr  # noqa: E402
from bot.locales import lade  # noqa: E402
from bot.prompts import domina_coach  # noqa: E402
from bot.scheduler import schablonen  # noqa: E402
from bot.services import persona_config, zeiten  # noqa: E402
from bot.services.db import profile as db_profile, progress as db_progress  # noqa: E402

DOM, SUB = "111", "222"


def _reset():
    state._state.pop("__gesendet__", None)
    state.clear_history(DOM)
    state.set_mode(DOM, "chat")
    state.set_paused(False)
    state.set_coach_ruhe(None)


def _texte(chat=DOM) -> list[str]:
    return [e["text"] for e in state.gesendet_offen(chat)]


# --------------------------------------------------------------------------
# Zeitangabe
# --------------------------------------------------------------------------

def test_wann_label():
    tz = ZoneInfo(config.TIMEZONE)
    jetzt = datetime(2026, 3, 10, 17, 30, tzinfo=tz).timestamp()
    assert zeiten.wann_label(jetzt - 20, jetzt) == "gerade eben"
    assert zeiten.wann_label(jetzt - 100, jetzt) == "vor 2 Minuten"
    assert zeiten.wann_label(jetzt - 9 * 60, jetzt) == "vor 9 Minuten"
    assert zeiten.wann_label(jetzt - 3 * 3600, jetzt) == "heute um 14:30"
    assert zeiten.wann_label(jetzt - 20 * 3600, jetzt) == "gestern um 21:30"
    assert zeiten.wann_label(jetzt - 3 * 86400, jetzt) == "vor 3 Tagen"
    # Zukunft (Uhren-Sprung) kippt nicht ins Negative
    assert zeiten.wann_label(jetzt + 50, jetzt) == "gerade eben"


# --------------------------------------------------------------------------
# Merken, Abhaken, Persistenz
# --------------------------------------------------------------------------

def test_merken_und_abhaken():
    _reset()
    assert state.gesendet_offen(DOM) == []
    state.merke_gesendet(DOM, "  Erste Info  ")
    state.merke_gesendet(DOM, "")
    state.merke_gesendet(DOM, None)
    assert _texte() == ["Erste Info"]
    # Kopie: Änderungen am Ergebnis treffen den Speicher nicht
    state.gesendet_offen(DOM)[0]["text"] = "verändert"
    assert _texte() == ["Erste Info"]
    # höchstens GESENDET_MAX, die ältesten fallen heraus; je Chat getrennt
    for i in range(state.GESENDET_MAX + 2):
        state.merke_gesendet(DOM, f"Info {i}")
    assert len(_texte()) == state.GESENDET_MAX and _texte()[-1] == f"Info {state.GESENDET_MAX + 1}"
    assert "Erste Info" not in _texte() and state.gesendet_offen(SUB) == []
    # überlange Nachricht wird auf Telegrams Obergrenze beschnitten
    state.merke_gesendet(DOM, "x" * 9000)
    assert len(_texte()[-1]) == state.GESENDET_MAX_ZEICHEN
    # Abhaken bis zu einem Zeitstempel: Späteres bleibt stehen
    offen = state.gesendet_offen(DOM)
    grenze = offen[-2]["zeit"]
    state._state["__gesendet__"][DOM][-1]["zeit"] = grenze + 5
    state.gesendet_abhaken(DOM, grenze)
    assert _texte() == ["x" * state.GESENDET_MAX_ZEICHEN]
    state.gesendet_abhaken(DOM, grenze + 5)
    assert state.gesendet_offen(DOM) == [] and DOM not in state._state["__gesendet__"]


def test_alter_und_verlaufs_marke():
    _reset()
    state.merke_gesendet(DOM, "von vorgestern")
    state._state["__gesendet__"][DOM][0]["zeit"] = time.time() - state.GESENDET_MAX_ALTER - 60
    assert state.gesendet_offen(DOM) == []
    state.merke_gesendet(DOM, "frisch")            # räumt das Alte beim Schreiben weg
    assert [e["text"] for e in state._state["__gesendet__"][DOM]] == ["frisch"]
    # Innerhalb der Marke wird nichts gemerkt – auch nicht nach einem Fehler darin
    with state.steht_im_verlauf():
        state.merke_gesendet(DOM, "steht schon im Verlauf")
    try:
        with state.steht_im_verlauf():
            raise RuntimeError("Versand gescheitert")
    except RuntimeError:
        pass
    state.merke_gesendet(DOM, "wieder normal")
    assert _texte() == ["frisch", "wieder normal"]


def test_ueberlebt_den_neustart():
    _reset()
    state.merke_gesendet(DOM, "vor dem Neustart")
    state.merke_gesendet(DOM, "zu alt")
    state._state["__gesendet__"][DOM][1]["zeit"] = time.time() - state.GESENDET_MAX_ALTER - 60
    state._persist(immediate=True)
    state._state.pop("__gesendet__")
    state.load_persisted()
    assert _texte() == ["vor dem Neustart"]
    # Paar gelöscht → auch das Gemerkte ist weg
    state.vergiss_chat(DOM)
    assert state.gesendet_offen(DOM) == []
    state._state.pop("__gesendet__", None)
    state.load_persisted()
    assert state.gesendet_offen(DOM) == []


# --------------------------------------------------------------------------
# Zentraler Sende-Wrapper
# --------------------------------------------------------------------------

class _Bot:
    def __init__(self):
        self.kaputt = False

    async def send_message(self, *args, **kwargs):
        if self.kaputt:
            raise RuntimeError("Telegram nicht erreichbar")
        return "zugestellt"

    async def copy_message(self, *args, **kwargs):
        return "kopiert"

    @contextlib.contextmanager
    def _unfrozen(self):
        yield


async def test_wrapper_merkt_nur_zugestelltes_an_die_dom_seite():
    _reset()
    bot = _Bot()
    routing._wrap_outgoing_logging(SimpleNamespace(bot=bot))
    assert await bot.send_message(chat_id=DOM, text="Info an sie") == "zugestellt"
    assert await bot.send_message(SUB, "Nachricht an ihn") == "zugestellt"
    assert await bot.send_message(DOM, "positional übergeben") == "zugestellt"
    await bot.send_message(chat_id="999", text="fremder Chat")
    assert _texte() == ["Info an sie", "positional übergeben"]
    assert state.gesendet_offen(SUB) == [] and state.gesendet_offen("999") == []
    # gescheiterter Versand (z. B. Markdown-Fehler vor dem Klartext-Versuch) zählt nicht
    bot.kaputt = True
    try:
        await bot.send_message(chat_id=DOM, text="kam nie an")
    except RuntimeError:
        pass
    assert _texte() == ["Info an sie", "positional übergeben"]
    # Ein Fehler beim Merken darf den Versand nicht stören
    bot.kaputt = False
    alt = state.merke_gesendet
    state.merke_gesendet = MagicMock(side_effect=RuntimeError("Speicher kaputt"))
    logging.disable(logging.CRITICAL)      # der erwartete Traceback soll die Ausgabe nicht füllen
    try:
        assert await bot.send_message(chat_id=DOM, text="geht trotzdem raus") == "zugestellt"
        assert state.merke_gesendet.call_count == 1
    finally:
        logging.disable(logging.NOTSET)
        state.merke_gesendet = alt


# --------------------------------------------------------------------------
# Hinweis-Text für den Coach
# --------------------------------------------------------------------------

def test_hinweis_text():
    assert domina_coach.automatik_hinweis([]) == ""
    assert domina_coach.automatik_hinweis([{"zeit": 1.0, "text": ""}]) == ""
    jetzt = datetime(2026, 3, 10, 17, 30, tzinfo=ZoneInfo(config.TIMEZONE)).timestamp()
    lang = "Anfang des Berichts. " + "Mittelteil ohne Belang. " * 40 + "Wie soll es weitergehen?"
    nachrichten = [
        {"zeit": jetzt - 23 * 3600, "text": "Uralt-Info, fällt heraus"},
        {"zeit": jetzt - 20 * 3600, "text": lang},
        {"zeit": jetzt - 3 * 3600, "text": "📋 *Angelegt* – Nachfrage folgt.\n\nZweite Zeile"},
        {"zeit": jetzt - 600, "text": "Er hat sich gerade bei dir gemeldet."},
        {"zeit": jetzt - 570, "text": "✅ Abgebrochen."},
        {"zeit": jetzt - 540, "text": lang},
    ]
    h = domina_coach.automatik_hinweis(nachrichten, jetzt=jetzt)
    assert h.startswith("[Zur Einordnung") and h.endswith("]"), h
    assert "Uralt-Info" not in h and h.count("\n– ") == domina_coach.AUTOMATIK_MAX
    assert "– gestern um 21:30: „Anfang des Berichts." in h
    assert "– heute um 14:30: „📋 Angelegt – Nachfrage folgt. Zweite Zeile“" in h      # einzeilig, ohne Markdown
    assert "– vor 10 Minuten: „Er hat sich gerade bei dir gemeldet.“" in h
    assert "– vor 9 Minuten: „Anfang des Berichts." in h and "Abgebrochen" not in h      # Quittung zählt nicht
    aeltere, juengste = h.split("– heute um 14:30")[0], h.split("– vor 9 Minuten: ")[1]
    # ältere gekürzt (Anfang UND Schluss bleiben), die jüngste ausführlich
    assert " […] " in aeltere and aeltere.rstrip().endswith("Wie soll es weitergehen?“"), aeltere[-200:]
    assert len(aeltere) < 700 and "Wie soll es weitergehen?“" in juengste
    assert juengste.count("Mittelteil ohne Belang.") == 40, juengste.count("Mittelteil ohne Belang.")
    for regel in ("am ehesten auf das Jüngste", "Erfinde keinen Ausgang", "frag kurz nach und setze keinen Tag",
                  "erwähne es nicht"):
        assert regel in h, regel
    assert "hat das System ihr in diesem Chat" in h and "aus ihrer Sicht" in h and "Ihre Nachricht unten" in h
    # andere Konstellation: Formen der dominanten Rolle wechseln mit
    alt = dict(persona_config._cache)
    persona_config._cache["dom_geschlecht"] = "mann"
    try:
        h = domina_coach.automatik_hinweis(nachrichten[-1:], jetzt=jetzt)
    finally:
        persona_config._cache.clear()
        persona_config._cache.update(alt)
    assert "hat nicht er geschrieben" in h and "hat das System ihm" in h and "aus seiner Sicht" in h
    assert "Seine Nachricht unten" in h, h
    # nur Quittungen → kein Vorspann; eine kurze Frage ist keine Quittung
    quittungen = [{"zeit": jetzt - 60, "text": "✅ Abgebrochen."}, {"zeit": jetzt - 30, "text": "🔊  Ist\nraus."}]
    assert domina_coach.automatik_hinweis(quittungen, jetzt=jetzt) == ""
    h = domina_coach.automatik_hinweis(quittungen + [{"zeit": jetzt - 10, "text": "Strafe: ja oder nein?"}], jetzt=jetzt)
    assert h.count("\n– ") == 1 and "„Strafe: ja oder nein?“" in h, h


def test_verlauf_fuer_modell_ist_eine_kopie():
    verlauf = [{"role": "assistant", "content": "Na?"}, {"role": "user", "content": "Ja"}]
    assert domina._verlauf_fuer_modell(verlauf, "") is verlauf
    neu = domina._verlauf_fuer_modell(verlauf, "[Hinweis]")
    assert neu[-1] == {"role": "user", "content": "[Hinweis]\n\nJa"} and neu[0] is verlauf[0]
    assert verlauf[-1]["content"] == "Ja" and len(neu) == 2
    # ohne Nutzer-Nachricht am Ende bleibt alles, wie es ist
    nur_coach = [{"role": "assistant", "content": "Na?"}]
    assert domina._verlauf_fuer_modell(nur_coach, "[Hinweis]") is nur_coach
    assert domina._verlauf_fuer_modell([], "[Hinweis]") == []


# --------------------------------------------------------------------------
# Coach-Antwort: Vorspann für das Modell, Wortlaut im Verlauf, Abhaken
# --------------------------------------------------------------------------

@contextlib.asynccontextmanager
async def _tippt(bot, chat_id):
    yield


class _Modell:
    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.aufrufe: list[tuple[str, list[dict]]] = []

    async def chat(self, system, messages, **kw):
        self.aufrufe.append((system, [dict(m) for m in messages]))
        antwort = self.antworten.pop(0)
        if isinstance(antwort, Exception):
            raise antwort
        return antwort


def _update(text: str):
    u = MagicMock()
    u.effective_chat.id = int(DOM)
    u.message.text = text
    u.message.reply_text = AsyncMock()
    return u


async def _antwort(modell, text, tarn=False):
    alt = (domina.grok.chat, domina.telegram_helper.typing_action, schablonen.verraet_sub_wissen)
    domina.grok.chat = modell.chat
    domina.telegram_helper.typing_action = _tippt
    treffer = [tarn]
    schablonen.verraet_sub_wissen = lambda antwort: treffer.pop(0) if treffer else False
    try:
        return await domina._chat_antwort(_update(text), MagicMock(), DOM, "SYSTEM", text)
    finally:
        domina.grok.chat, domina.telegram_helper.typing_action, schablonen.verraet_sub_wissen = alt


async def test_coach_bekommt_den_vorspann_und_hakt_ab():
    _reset()
    state.add_message(DOM, "user", "Sag ihm, er soll den Tisch decken")
    state.add_message(DOM, "assistant", "Mach ich.\n\n[SPRACHNACHRICHT: Deck den Tisch]")
    state.merke_gesendet(DOM, "🎲 Nebenbei: Er hat gerade etwas gewagt.")
    modell = _Modell(["Du meinst das von eben?"])
    assert await _antwort(modell, "Einverstanden") == "Du meinst das von eben?"
    system, nachrichten = modell.aufrufe[0]
    assert system == "SYSTEM" and len(nachrichten) == 3
    letzte = nachrichten[-1]["content"]
    assert letzte.startswith("[Zur Einordnung") and letzte.endswith("]\n\nEinverstanden"), letzte
    assert "„🎲 Nebenbei: Er hat gerade etwas gewagt.“" in letzte and "gerade eben" in letzte
    # der gespeicherte Verlauf behält ihren Wortlaut; die Info ist abgehakt
    assert state.get_history(DOM)[-2:] == [{"role": "user", "content": "Einverstanden"},
                                          {"role": "assistant", "content": "Du meinst das von eben?"}]
    assert state.gesendet_offen(DOM) == []
    # nächste Nachricht ohne neue Info: kein Vorspann mehr
    modell = _Modell(["Gern."])
    await _antwort(modell, "Danke")
    assert modell.aufrufe[0][1][-1] == {"role": "user", "content": "Danke"}


async def test_quittungen_ohne_vorspann_aber_abgehakt():
    _reset()
    state.merke_gesendet(DOM, "✅ Abgebrochen.")
    modell = _Modell(["Gern."])
    await _antwort(modell, "Hast du eine Idee für heute?")
    assert modell.aufrufe[0][1][-1] == {"role": "user", "content": "Hast du eine Idee für heute?"}
    assert state.gesendet_offen(DOM) == []


async def test_spaeter_gesendetes_bleibt_offen():
    """Was während der Modell-Antwort hinzukommt, hat der Coach nicht gesehen."""
    _reset()
    state.merke_gesendet(DOM, "Info eins: Er hat sich gerade gemeldet.")

    class _Langsam(_Modell):
        async def chat(self, system, messages, **kw):
            state.merke_gesendet(DOM, "Info zwei, kam währenddessen")
            state._state["__gesendet__"][DOM][-1]["zeit"] += 1     # eindeutig später
            return await super().chat(system, messages, **kw)

    modell = _Langsam(["Antwort"])
    await _antwort(modell, "Hm")
    assert "Info eins" in modell.aufrufe[0][1][-1]["content"]
    assert "Info zwei" not in modell.aufrufe[0][1][-1]["content"]
    assert _texte() == ["Info zwei, kam währenddessen"]


async def test_modell_fehler_laesst_alles_stehen():
    _reset()
    state.merke_gesendet(DOM, "Info, auf die sie gleich antworten wird")
    modell = _Modell([RuntimeError("Modell nicht erreichbar")])
    assert await _antwort(modell, "Ja") is None
    assert state.get_history(DOM) == [] and _texte() == ["Info, auf die sie gleich antworten wird"]
    # zweiter Anlauf sieht die Info wieder
    modell = _Modell(["Jetzt aber."])
    await _antwort(modell, "Ja")
    assert "Info, auf die sie gleich antworten wird" in modell.aufrufe[0][1][-1]["content"]


async def test_tarn_neuversuch_sieht_denselben_verlauf():
    _reset()
    state.merke_gesendet(DOM, "Info für beide Anläufe des Modells")
    modell = _Modell(["Erster Entwurf", "Zweiter Entwurf"])
    assert await _antwort(modell, "Und?", tarn=True) == "Zweiter Entwurf"
    assert len(modell.aufrufe) == 2 and modell.aufrufe[1][0].startswith("SYSTEM") and modell.aufrufe[1][0] != "SYSTEM"
    assert modell.aufrufe[0][1] == modell.aufrufe[1][1]
    assert "Info für beide Anläufe des Modells" in modell.aufrufe[1][1][-1]["content"]
    assert state.get_history(DOM)[-1] == {"role": "assistant", "content": "Zweiter Entwurf"}


# --------------------------------------------------------------------------
# Was im Verlauf steht, wird nicht noch einmal gemerkt
# --------------------------------------------------------------------------

async def _wie_der_wrapper(*args, **kwargs):
    """Stellt den Sende-Wrapper nach: Jede Sendung an sie wird gemerkt."""
    text = kwargs.get("text", args[1] if len(args) > 1 else "")
    state.merke_gesendet(DOM, text)


async def test_coach_antwort_gilt_nicht_als_automatische_nachricht():
    _reset()

    async def sprachnachricht(update, context, inhalt, msg=None):
        await _wie_der_wrapper(DOM, "🔊 Ist raus.")        # Bestätigung außerhalb des Verlaufs
        return True

    async def antwort_senden(message, text, reply_markup=None):
        await _wie_der_wrapper(DOM, text)

    flicken = [
        (domina.onboarding, "start_if_needed", AsyncMock(return_value=False)),
        (domina.embeddings, "get_embedding", AsyncMock(return_value=None)),
        (db_profile, "get_user_profile", AsyncMock(return_value={"aktuelles_level": 2})),
        (domina, "_baue_system_prompt", AsyncMock(return_value="SYSTEM")),
        (domina, "_chat_antwort", AsyncMock(return_value="Mach ich.\n\n[SPRACHNACHRICHT: Ich freue mich auf heute]")),
        (domina, "_sende_sprachnachricht_an_sklaven", sprachnachricht),
        (domina, "_netz_funde", lambda *a, **k: []),
        (domina, "_save_conversation", AsyncMock()),
        (domina, "_check_level_up", AsyncMock()),
        (domina.telegram_helper, "reply_markdown_safe", antwort_senden),
    ]
    from bot.services import praeferenz_detektor
    flicken.append((praeferenz_detektor, "erkenne_und_schlage_vor", AsyncMock()))
    alt = [(obj, name, getattr(obj, name)) for obj, name, _ in flicken]
    for obj, name, wert in flicken:
        setattr(obj, name, wert)
    try:
        context = MagicMock()
        context.chat_data = {}
        await domina.handle(_update("Sag ihm, ich freue mich auf heute"), context)
        await asyncio.sleep(0)       # Hintergrund-Detektor auslaufen lassen
    finally:
        for obj, name, wert in alt:
            setattr(obj, name, wert)
    # seine Antwort steht im Verlauf – gemerkt ist nur die Bestätigung daneben
    assert _texte() == ["🔊 Ist raus."]


async def test_training_uebung_und_rueckmeldung_nicht_doppelt():
    _reset()
    uebung = "Bleib nach der Ansage still stehen und zähl innerlich bis fünf."
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=_wie_der_wrapper)
    alt = (config.TRAINING_ENABLED, db_profile.get_user_profile, db_progress.save_training, tr.neue_uebung)
    config.TRAINING_ENABLED = True
    db_profile.get_user_profile = AsyncMock(return_value={"erfahrungsstand": "x"})
    db_progress.save_training = AsyncMock(return_value="x")
    tr.neue_uebung = AsyncMock(return_value={"typ": "challenge", "uebung": uebung, "level": 3})
    try:
        await tr.daily_training(bot)                      # Zeit-Job
        assert bot.send_message.await_count == 1
        u = _update("/training")
        u.message.reply_text = AsyncMock(side_effect=_wie_der_wrapper)
        context = MagicMock()
        context.bot.send_chat_action = AsyncMock()
        await tr.show(u, context)                         # auf Abruf
        assert u.message.reply_text.await_count == 1
    finally:
        config.TRAINING_ENABLED, db_profile.get_user_profile, db_progress.save_training, tr.neue_uebung = alt
    assert state.get_history(DOM)[-1] == {"role": "assistant", "content": uebung}
    assert state.gesendet_offen(DOM) == []
    # Gegenprobe: dieselbe Sendung außerhalb der Marke würde gemerkt
    await _wie_der_wrapper(DOM, "irgendeine Info")
    assert _texte() == ["irgendeine Info"]


# --------------------------------------------------------------------------
# Wett-Info
# --------------------------------------------------------------------------

def test_wett_info_endet_nicht_als_auswahl():
    for sprache, auswahl in (("de", "Doppelt oder nichts"), ("en", "Double or nothing")):
        text = lade(sprache)["WETTE_INFO_DOMINA"]
        assert "{einsatz}" in text and auswahl.lower() not in text.lower(), text
        assert "?" not in text and text.rstrip().endswith("."), text
    assert "du musst nichts tun" in lade("de")["WETTE_INFO_DOMINA"]


if __name__ == "__main__":
    test_wann_label()
    test_merken_und_abhaken()
    test_alter_und_verlaufs_marke()
    test_ueberlebt_den_neustart()
    asyncio.run(test_wrapper_merkt_nur_zugestelltes_an_die_dom_seite())
    test_hinweis_text()
    test_verlauf_fuer_modell_ist_eine_kopie()
    asyncio.run(test_coach_bekommt_den_vorspann_und_hakt_ab())
    asyncio.run(test_quittungen_ohne_vorspann_aber_abgehakt())
    asyncio.run(test_spaeter_gesendetes_bleibt_offen())
    asyncio.run(test_modell_fehler_laesst_alles_stehen())
    asyncio.run(test_tarn_neuversuch_sieht_denselben_verlauf())
    asyncio.run(test_coach_antwort_gilt_nicht_als_automatische_nachricht())
    asyncio.run(test_training_uebung_und_rueckmeldung_nicht_doppelt())
    test_wett_info_endet_nicht_als_auswahl()
    print("✅ Alle Tests zum Automatik-Kontext des Coachs bestanden")
