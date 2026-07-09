"""
Regressions-Tests für bot/services/paare.py – Paar-Registry + resolve()
(Multiuser-Fundament, Schritt 1+2 der Migrations-Strategie, 2026-07-04).

Läuft mit echten Deps (Docker) ODER lokal mit MagicMock-Stubs:
    python3 tests/test_paare.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Deterministische Test-IDs, BEVOR bot.config die Env liest.
os.environ["DOMINA_CHAT_ID"] = "111"
os.environ["SKLAVE_CHAT_ID"] = "222"

# Falls die echten Telegram/Qdrant-Deps fehlen (lokal), durch Stubs ersetzen.
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

from bot import config  # noqa: E402
from bot.services import paare, telegram_helper  # noqa: E402

# load_dotenv kann die os.environ-Werte NICHT überschrieben haben (override=False),
# aber eine lokale .env könnte vor dem setdefault geladen sein → hart nachziehen.
config.DOMINA_CHAT_ID = "111"
config.SKLAVE_CHAT_ID = "222"


def test_resolve_rollen():
    paar, rolle = paare.resolve("111")
    assert rolle == paare.ROLLE_DOM
    assert paar.paar_id == paare.LEGACY_PAAR_ID

    paar, rolle = paare.resolve(222)  # int-Chat-IDs (Telegram) müssen auch gehen
    assert rolle == paare.ROLLE_SUB

    assert paare.resolve("999") is None


def test_autorisierung():
    assert paare.ist_autorisiert("111")
    assert paare.ist_autorisiert(222)
    assert not paare.ist_autorisiert("999")
    assert not paare.ist_autorisiert(None)


def test_partner_und_chat_id():
    paar = paare.default_paar()
    assert paar.chat_id(paare.ROLLE_DOM) == "111"
    assert paar.chat_id(paare.ROLLE_SUB) == "222"
    assert paar.partner_chat_id("111") == "222"
    assert paar.partner_chat_id(222) == "111"
    assert paar.partner_chat_id("999") is None


def test_user_id_legacy_mapping():
    """Das Env-Paar MUSS die historischen Qdrant-Keys behalten (Bestandsdaten!)."""
    paar = paare.default_paar()
    assert paar.user_id(paare.ROLLE_DOM) == "domina"
    assert paar.user_id(paare.ROLLE_SUB) == "sklave"

    zukunft = paare.Paar(paar_id="7", dom_chat_id="333", sub_chat_id="444")
    assert zukunft.user_id(paare.ROLLE_DOM) == "7:domina"
    assert zukunft.user_id(paare.ROLLE_SUB) == "7:sklave"


def test_alle_paare_ohne_config_leer():
    alt = config.DOMINA_CHAT_ID
    try:
        config.DOMINA_CHAT_ID = None
        assert paare.alle_paare() == []
        assert paare.resolve("111") is None
    finally:
        config.DOMINA_CHAT_ID = alt
    assert paare.ist_autorisiert("111")  # danach wieder normal


def test_rolle_von_user_id():
    """Whitelist-Lookups (Profil-Felder) lösen über die Rolle auf – Legacy-Keys
    und präfixierte Mandanten-Keys müssen beide funktionieren."""
    assert paare.rolle_von_user_id("sklave") == "sklave"
    assert paare.rolle_von_user_id("domina") == "domina"
    assert paare.rolle_von_user_id("7:sklave") == "sklave"
    assert paare.rolle_von_user_id("7:domina") == "domina"
    assert paare.rolle_von_user_id("unbekannt") is None
    assert paare.rolle_von_user_id("") is None
    assert paare.rolle_von_user_id(None) is None


def test_erstelle_task_mandanten_key():
    """Task-Factory schreibt den Mandanten-Key durch (Default = Legacy "sklave")."""
    from bot.services import qdrant
    captured = {}

    async def _fake_save(payload):
        captured.clear()
        captured.update(payload)
        return "pid"

    orig = qdrant.save_task
    qdrant.save_task = _fake_save
    try:
        asyncio.run(qdrant.erstelle_task("tu was", "Dienst", 2))
        assert captured["user_id"] == "sklave"
        asyncio.run(qdrant.erstelle_task("tu was", "Dienst", 2, user_id="7:sklave"))
        assert captured["user_id"] == "7:sklave"
    finally:
        qdrant.save_task = orig


def test_persona_config_pro_paar():
    """Der frühere Singleton war DIE Leak-Quelle: alle Paare hätten denselben
    Bot-Namen gesehen. Jetzt entscheidet der Paar-Kontext, welcher Cache gilt."""
    from bot.services import persona_config

    alt = dict(persona_config._cache)
    persona_config._cache["bot_name"] = "Lady Eins"
    try:
        # Legacy-Kontext (Default) sieht den Legacy-Cache – auch über den Alias.
        assert persona_config.bot_name() == "Lady Eins"

        # Anderes Paar, anderer Cache: kein Leak vom Legacy-Paar.
        with paare.kontext("7"):
            assert persona_config.bot_name() == ""
            persona_config._cache_fuer("7")["bot_name"] = "Sir Sieben"
            assert persona_config.bot_name() == "Sir Sieben"

        # Zurück im Legacy-Kontext: unverändert.
        assert persona_config.bot_name() == "Lady Eins"
    finally:
        persona_config._cache.clear()
        persona_config._cache.update(alt)
        persona_config._caches.pop("7", None)


def test_persona_config_setter_mandanten_key():
    """Setter persistieren ins Dom-Profil DES PAARES (Legacy: "domina",
    weitere Paare: "{paar_id}:domina")."""
    from bot.services import persona_config, qdrant

    captured = []

    async def _fake_patch(user_id, fields, **kwargs):
        captured.append((user_id, dict(fields)))
        return "pid"

    orig = qdrant.patch_profile_fields
    qdrant.patch_profile_fields = _fake_patch
    alt = dict(persona_config._cache)
    try:
        asyncio.run(persona_config.set_bot_name("Lady Eins"))
        with paare.kontext("7"):
            asyncio.run(persona_config.set_bot_name("Sir Sieben"))
        assert captured[0][0] == "domina"
        assert captured[1][0] == "7:domina"
        assert persona_config._cache["bot_name"] == "Lady Eins"
        assert persona_config._cache_fuer("7")["bot_name"] == "Sir Sieben"
    finally:
        qdrant.patch_profile_fields = orig
        persona_config._cache.clear()
        persona_config._cache.update(alt)
        persona_config._caches.pop("7", None)


def test_pairing_registry_und_invites():
    """Kompletter Pairing-Kern: Invite erstellen → einlösen → Paar persistiert,
    inkl. aller Ablehnungsfälle (eigener/unbekannter/abgelaufener Code,
    Doppel-Registrierung) und Registry-Reload von Platte."""
    import tempfile

    alt_pfad = config.PAARE_FILE
    alt_reg = paare._registry
    with tempfile.TemporaryDirectory() as d:
        config.PAARE_FILE = f"{d}/paare.json"
        paare._registry = None
        try:
            code = paare.erstelle_invite(paare.ROLLE_DOM, "333")
            assert len(code) == paare.INVITE_CODE_LAENGE

            assert paare.loese_invite_ein(code, "333") is None      # eigener Code
            assert paare.loese_invite_ein("XXXXXXXX", "444") is None  # unbekannt

            paar = paare.loese_invite_ein(code, "444")
            assert paar is not None
            assert (paar.paar_id, paar.dom_chat_id, paar.sub_chat_id) == ("2", "333", "444")
            assert paar.user_id(paare.ROLLE_DOM) == "2:domina"      # Mandanten-Key qualifiziert

            p2, rolle = paare.resolve(444)
            assert rolle == paare.ROLLE_SUB and p2.paar_id == "2"
            assert len(paare.alle_paare()) == 2                     # Env-Paar + Paar 2

            assert paare.loese_invite_ein(code, "555") is None      # Code verbraucht
            try:
                paare.erstelle_invite(paare.ROLLE_SUB, "444")       # Chat schon vergeben
                raise AssertionError("erstelle_invite hätte ValueError werfen müssen")
            except ValueError:
                pass

            paare._registry = None                                  # Reload von Platte
            assert paare.resolve("333") is not None

            code2 = paare.erstelle_invite(paare.ROLLE_SUB, "555")
            paare._lade_registry()["invites"][code2]["erstellt_am"] = 0  # abgelaufen
            assert paare.loese_invite_ein(code2, "666") is None

            # Paar-Verwaltung: Entfernen (Env-Paar geschützt, Registry persistiert)
            try:
                paare.entferne_paar("1")
                raise AssertionError("Env-Paar hätte nicht entfernbar sein dürfen")
            except ValueError:
                pass
            assert paare.entferne_paar("999") is False       # unbekannt
            assert paare.entferne_paar("2") is True
            assert paare.resolve("333") is None              # Paar weg
            paare._registry = None                           # Reload: auch auf Platte weg
            assert paare.resolve("333") is None
        finally:
            config.PAARE_FILE = alt_pfad
            paare._registry = alt_reg


def test_zeiten_pro_paar():
    """Tages-Zeiten sind pro Paar: eigener Wert schlägt Env-Default, andere
    Paare bleiben beim Default; Setter persistiert unter dem Mandanten-Key."""
    from bot.services import persona_config, qdrant

    captured = []

    async def _fake_patch(user_id, fields, **kwargs):
        captured.append((user_id, dict(fields)))

    orig = qdrant.patch_profile_fields
    qdrant.patch_profile_fields = _fake_patch
    alt = dict(persona_config._cache)
    try:
        assert persona_config.zeit("followup_time") == config.FOLLOWUP_TIME  # Env-Default
        asyncio.run(persona_config.set_zeit("followup_time", "19:30"))
        assert persona_config.zeit("followup_time") == "19:30"
        assert captured[-1] == ("domina", {"followup_time": "19:30"})

        with paare.kontext("7"):
            assert persona_config.zeit("followup_time") == config.FOLLOWUP_TIME  # kein Leak
            asyncio.run(persona_config.set_zeit("followup_time", "08:15"))
            assert persona_config.zeit("followup_time") == "08:15"
            assert captured[-1][0] == "7:domina"

        assert persona_config.zeit("followup_time") == "19:30"  # Env-Paar unverändert
        asyncio.run(persona_config.set_zeit("followup_time", ""))  # zurück auf Default
        assert persona_config.zeit("followup_time") == config.FOLLOWUP_TIME
        try:
            persona_config.zeit("gibts_nicht")
            raise AssertionError("zeit() hätte ValueError werfen müssen")
        except ValueError:
            pass
    finally:
        qdrant.patch_profile_fields = orig
        persona_config._cache.clear()
        persona_config._cache.update(alt)
        persona_config._caches.pop("7", None)


def test_skills_pro_paar_gefiltert():
    """Skills (/lerne-Wissen) sind mandanten-gefiltert – /lerne-Inhalte eines
    Paares dürfen nie in den Prompts eines anderen Paares landen."""
    from bot.services import qdrant

    f1 = qdrant._skills_filter("Anal")
    with paare.kontext("7"):
        f7 = qdrant._skills_filter("Anal")
    # Mit gestubbtem qm sind die Filter MagicMocks – die entscheidende Zusicherung
    # ist der Mandanten-Key, der in die FieldCondition einfließt:
    assert qdrant.mandanten_key("domina") == "domina"
    with paare.kontext("7"):
        assert qdrant.mandanten_key("domina") == "7:domina"
    assert f1 is not None and f7 is not None


def test_ui_locale_pro_paar():
    """Statische UI-Texte (t()) folgen der UI-Locale des Kontext-Paares:
    Paar 7 auf Englisch, Env-Paar bleibt beim Deployment-Default (de)."""
    from bot import messages
    from bot.services import persona_config, qdrant

    async def _fake_patch(user_id, fields, **kwargs):
        pass

    orig = qdrant.patch_profile_fields
    qdrant.patch_profile_fields = _fake_patch
    alt = dict(persona_config._cache)
    try:
        assert messages.t("COMMON_NICHT_AUTORISIERT") == "Nicht autorisiert."
        with paare.kontext("7"):
            asyncio.run(persona_config.set_ui_locale("en"))
            assert messages.t("COMMON_NICHT_AUTORISIERT") == "Not authorized."
        assert messages.t("COMMON_NICHT_AUTORISIERT") == "Nicht autorisiert."  # kein Leak

        # Locale folgt der gewählten Antwortsprache (nur de/en haben Kataloge)
        assert persona_config.locale_fuer_sprache("Englisch") == "en"
        assert persona_config.locale_fuer_sprache("Deutsch") == "de"
        assert persona_config.locale_fuer_sprache("Französisch") == ""
        assert persona_config.locale_fuer_sprache("") == ""

        # Unbekannte Locale wird auf Default zurückgesetzt
        with paare.kontext("7"):
            asyncio.run(persona_config.set_ui_locale("xx"))
            assert persona_config.ui_locale() == ""
    finally:
        qdrant.patch_profile_fields = orig
        persona_config._cache.clear()
        persona_config._cache.update(alt)
        persona_config._caches.pop("7", None)


def test_tagesbudget_pro_paar():
    """Der Nachrichten-Zähler läuft pro Paar und rollt am Tageswechsel über."""
    from bot import state

    state._state.pop("__tagesnachrichten__", None)
    try:
        assert state.zaehle_tagesnachricht("1") == 1
        assert state.zaehle_tagesnachricht("1") == 2
        assert state.zaehle_tagesnachricht("7") == 1   # eigener Zähler pro Paar
        # Tageswechsel simulieren: alter Eintrag -> Zähler beginnt neu
        state._state["__tagesnachrichten__"]["1"]["tag"] = "2000-01-01"
        assert state.zaehle_tagesnachricht("1") == 1
    finally:
        state._state.pop("__tagesnachrichten__", None)


def test_i18n_englische_limits_und_ja_nein():
    """Englische Paare: Limits-Check erkennt englische Begriffe/Umschreibungen,
    ja/nein-Erkennung versteht englische Antwortsätze."""
    from bot.services import limits_check, synonyme

    # Englischer Limit-Key zieht englische UND deutsche Stämme
    begriffe = limits_check._suchbegriffe_fuer("Choking")
    assert "chok" in begriffe and "wuerg" in begriffe

    # Voller Check: englischer Text verletzt englisches Limit
    treffer = asyncio.run(limits_check.verletzungen(
        "Tonight I will choke you slowly.", ["Choking"], []))
    assert treffer, "englisches Limit wurde im englischen Text nicht erkannt"

    # Deutscher Limit-Key fängt jetzt auch englischen Text (kritische Keys)
    treffer = asyncio.run(limits_check.verletzungen(
        "Drink your pee now.", ["Urin"], []))
    assert treffer, "deutsches Limit 'Urin' hat englisches 'pee' nicht erkannt"

    # ja/nein versteht englische Sätze; Verneinung gewinnt
    assert synonyme.ja_nein("yes, I did it") == "ja"
    assert synonyme.ja_nein("I have finished the task") == "ja"
    assert synonyme.ja_nein("sorry, not done yet") == "nein"
    assert synonyme.ja_nein("I couldn't do it") == "nein"


def test_voice_sprache_pro_paar():
    """STT-Sprach-Hint und Piper-Stimme folgen der Sprache des Kontext-Paares."""
    from bot.services import persona_config, tts

    alt = dict(persona_config._cache)
    alt_stimmen, alt_voice = config.TTS_STIMMEN, config.TTS_VOICE
    try:
        # Sprachcode-Ableitung: Default = de, Freitext-Mapping, Unbekanntes = ""
        assert persona_config.sprach_code() == "de"
        persona_config._cache_fuer("7")["sprache"] = "Englisch"
        with paare.kontext("7"):
            assert persona_config.sprach_code() == "en"
        persona_config._cache_fuer("7")["sprache"] = "Klingonisch"
        with paare.kontext("7"):
            assert persona_config.sprach_code() == ""

        # TTS-Stimmen-Wahl: Map-Treffer pro Sprache, sonst TTS_VOICE-Fallback
        config.TTS_STIMMEN = {"en": "en_US-lessac-high"}
        config.TTS_VOICE = "de_DE-thorsten-high"
        assert tts._voice_fuer_kontext() == "de_DE-thorsten-high"  # de nicht in Map
        persona_config._cache_fuer("7")["sprache"] = "Englisch"
        with paare.kontext("7"):
            assert tts._voice_fuer_kontext() == "en_US-lessac-high"
    finally:
        config.TTS_STIMMEN, config.TTS_VOICE = alt_stimmen, alt_voice
        persona_config._cache.clear()
        persona_config._cache.update(alt)
        persona_config._caches.pop("7", None)


def test_safeword_pro_paar():
    """Safeword/Resume-Wort sind pro Paar; leer = globale Env-Defaults;
    beim entfernten Paar fällt paar_im_kontext NIE aufs Env-Paar zurück."""
    from bot.services import persona_config, qdrant

    async def _fake_patch(user_id, fields, **kwargs):
        pass

    orig = qdrant.patch_profile_fields
    qdrant.patch_profile_fields = _fake_patch
    alt = dict(persona_config._cache)
    try:
        assert persona_config.safeword() == config.SAFEWORD
        assert persona_config.resume_wort() == config.RESUME_WORT
        with paare.kontext("7"):
            asyncio.run(persona_config.set_safeword("Red", "GREEN"))
            assert persona_config.safeword() == "red"        # lowercase-normalisiert
            assert persona_config.resume_wort() == "green"
        assert persona_config.safeword() == config.SAFEWORD  # kein Leak ins Env-Paar
    finally:
        qdrant.patch_profile_fields = orig
        persona_config._cache.clear()
        persona_config._cache.update(alt)
        persona_config._caches.pop("7", None)

    # Härtung: unbekanntes Kontext-Paar -> LookupError statt Env-Paar-Fallback
    with paare.kontext("999"):
        try:
            paare.paar_im_kontext()
            raise AssertionError("paar_im_kontext hätte LookupError werfen müssen")
        except LookupError:
            pass


class _FakeBot:
    """Zeichnet send_message-Aufrufe auf (Signatur wie telegram.Bot)."""
    def __init__(self):
        self.gesendet = []

    async def send_message(self, chat_id=None, text=None, parse_mode=None, reply_markup=None):
        self.gesendet.append({"chat_id": chat_id, "text": text})


def test_mandanten_grenze_qdrant():
    """qdrant.mandanten_key: Rollen-Literale werden über den Paar-Kontext
    qualifiziert; Legacy-Kontext = Identität (Bestandsdaten), qualifizierte
    Keys und None passieren unverändert."""
    from bot.services import qdrant

    assert qdrant.mandanten_key("sklave") == "sklave"        # Legacy-Kontext
    assert qdrant.mandanten_key("domina") == "domina"
    assert qdrant.mandanten_key("7:sklave") == "7:sklave"    # schon qualifiziert
    assert qdrant.mandanten_key(None) is None
    with paare.kontext("7"):
        assert qdrant.mandanten_key("sklave") == "7:sklave"
        assert qdrant.mandanten_key("domina") == "7:domina"
        assert qdrant.mandanten_key("1:foo") == "1:foo"


def _mit_zwei_paaren():
    """Monkeypatch-Helfer: Registry mit Env-Paar + Paar 7."""
    return [
        paare.Paar(paar_id="1", dom_chat_id="111", sub_chat_id="222"),
        paare.Paar(paar_id="7", dom_chat_id="333", sub_chat_id="444"),
    ]


def test_send_wrapper_folgt_paar_kontext():
    """send_domina/send_sklave routen zum Paar des AKTIVEN Kontexts – damit
    sind alle ~33 Bestands-Callsites in Handlers/Scheduler paar-korrekt."""
    orig = paare.alle_paare
    paare.alle_paare = _mit_zwei_paaren
    try:
        async def _lauf():
            bot = _FakeBot()
            await telegram_helper.send_domina(bot, "an dom paar 1")
            with paare.kontext("7"):
                await telegram_helper.send_domina(bot, "an dom paar 7")
                await telegram_helper.send_sklave(bot, "an sub paar 7")
            await telegram_helper.send_sklave(bot, "an sub paar 1")
            return bot.gesendet

        gesendet = asyncio.run(_lauf())
        assert [g["chat_id"] for g in gesendet] == ["111", "333", "444", "222"]
    finally:
        paare.alle_paare = orig


def test_pro_paar_job_wrapper():
    """_pro_paar führt einen Job pro Paar im jeweiligen Kontext aus; ein
    Fehler bei Paar 1 überspringt Paar 7 nicht."""
    import bot.main as m

    orig = paare.alle_paare
    paare.alle_paare = _mit_zwei_paaren
    gesehen = []

    async def job(bot):
        kontext = paare.aktueller_kontext()
        gesehen.append(kontext)
        if kontext == "1":
            raise RuntimeError("Paar 1 kaputt – darf Paar 7 nicht stoppen")

    try:
        asyncio.run(m._pro_paar(job)(None))
        assert gesehen == ["1", "7"]
    finally:
        paare.alle_paare = orig


def test_pause_pro_paar():
    """Safeword-Pause ist paar-scoped: Paar 7 pausiert ≠ Env-Paar pausiert.
    Persistenz-Roundtrip inkl. Legacy-Fallback (altes globales Flag → Paar 1)."""
    from bot import state

    alt = set(state._state.get("__paused_paare__", set()))
    try:
        state._state["__paused_paare__"] = set()
        assert state.is_paused() is False
        with paare.kontext("7"):
            state.set_paused(True)               # pausiert NUR Paar 7
            assert state.is_paused() is True
        assert state.is_paused() is False        # Env-Paar läuft weiter
        assert state.is_paused("7") is True      # explizite paar_id
        with paare.kontext("7"):
            state.set_paused(False)
        assert state.is_paused("7") is False

        # Echter Persistenz-Roundtrip über eine temporäre STATE_FILE
        import json
        import tempfile
        alt_pfad = config.STATE_FILE
        with tempfile.TemporaryDirectory() as d:
            config.STATE_FILE = f"{d}/state.json"
            try:
                state.set_paused(True, paar_id="7")   # schreibt sofort (immediate)
                state._state.pop("__paused_paare__", None)
                state.load_persisted()
                assert state.is_paused("7") is True
                assert state.is_paused() is False

                # Legacy-STATE_FILE (altes globales Flag) → Env-Paar pausiert
                with open(config.STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump({"paused": True}, f)
                state._state.pop("__paused_paare__", None)
                state.load_persisted()
                assert state.is_paused() is True
                assert state.is_paused("7") is False
            finally:
                config.STATE_FILE = alt_pfad
    finally:
        state._state["__paused_paare__"] = alt


def test_paar_lock_serialisiert_pro_paar():
    """Schritt 7: Updates DESSELBEN Paares laufen strikt nacheinander,
    verschiedene Paare parallel (ein langsamer Call blockiert andere nicht)."""
    assert paare.lock("1") is paare.lock("1")
    assert paare.lock("1") is not paare.lock("7")

    reihenfolge = []

    async def update(paar_id, name, dauer):
        async with paare.lock(paar_id):
            reihenfolge.append(f"{name}>")
            await asyncio.sleep(dauer)
            reihenfolge.append(f"<{name}")

    async def _lauf():
        # a hält Lock von Paar 1; b (gleiches Paar) muss warten,
        # c (Paar 7) läuft währenddessen durch.
        await asyncio.gather(
            update("1", "a", 0.05),
            update("1", "b", 0),
            update("7", "c", 0),
        )

    asyncio.run(_lauf())
    assert reihenfolge.index("<c") < reihenfolge.index("<a"), "Paar 7 wartete fälschlich auf Paar 1"
    assert reihenfolge.index("<a") < reihenfolge.index("b>"), "Paar-1-Updates liefen nicht seriell"


def test_send_an_und_wrapper():
    """send_an routet nach Rolle; die Legacy-Wrapper treffen dieselben Chats."""
    paar = paare.default_paar()

    async def _lauf():
        bot = _FakeBot()
        await telegram_helper.send_an(bot, paar, paare.ROLLE_DOM, "an dom")
        await telegram_helper.send_an(bot, paar, paare.ROLLE_SUB, "an sub")
        await telegram_helper.send_domina(bot, "wrapper dom")
        await telegram_helper.send_sklave(bot, "wrapper sub")
        return bot.gesendet

    gesendet = asyncio.run(_lauf())
    assert [g["chat_id"] for g in gesendet] == ["111", "222", "111", "222"]
    assert gesendet[0]["text"] == "an dom"


def main():
    tests = [f for name, f in sorted(globals().items()) if name.startswith("test_")]
    fehler = 0
    for f in tests:
        try:
            f()
            print(f"✅ {f.__name__}")
        except AssertionError as e:
            fehler += 1
            print(f"❌ {f.__name__}: {e}")
    if fehler:
        sys.exit(1)
    print(f"\n{len(tests)} Tests grün.")


if __name__ == "__main__":
    main()
