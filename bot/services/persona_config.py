"""
Zentrale, gecachte Namens-Konfiguration, vom Domina gesetzt – PRO PAAR.

- bot_name:       Name der Bot-Herrin (gilt für Sklaven- UND Coach-Persona).
- sklave_anrede:  Wie der Sklave angesprochen/genannt werden soll.

Beides ist OPTIONAL. Leer = bisheriges Verhalten ("deine Herrin", neutrale Anrede).
Wird im Dom-Profil des Paares persistiert und beim Start via load() in den Cache
geholt, damit die Persona-Bausteine ohne Qdrant-Read pro Prompt darauf zugreifen.

Multiuser (Schritt 4): Der frühere Modul-Singleton `_cache` war die gefährlichste
Leak-Quelle – alle Paare hätten denselben Bot-Namen/Setup-Kontext bekommen. Jetzt
gibt es einen Cache PRO paar_id; welcher gilt, entscheidet der Paar-Kontext
(paare.aktueller_kontext(), gesetzt pro Update in main.py). Die Getter/Setter
behalten ihre Signaturen – Prompt-Builder bleiben unangetastet.
"""
import logging

from bot import config
from bot.services import paare, qdrant

logger = logging.getLogger(__name__)

# Nutzer-konfigurierbare Tages-Zeiten PRO PAAR (leer = globaler Env-Default aus
# config). Gelesen von main.plane_zeit_jobs (Cron pro Paar) und
# qdrant._followup_zeitpunkt_utc; gesetzt über /einstellungen (Feld 7).
ZEIT_FELDER = {
    "followup_time": "FOLLOWUP_TIME",
    "tiny_task_time": "TINY_TASK_TIME",
    "stimmung_time": "STIMMUNG_TIME",
    "ziel_erinnerung_time": "ZIEL_ERINNERUNG_TIME",
    "rollenspiel_vorschlag_time": "ROLLENSPIEL_VORSCHLAG_TIME",
    "wochenplanung_time": "WOCHENPLANUNG_TIME",
    "tiny_task_feedback_time": "TINY_TASK_FEEDBACK_TIME",
    "training_erinnerung_time": "TRAINING_ERINNERUNG_TIME",
    "luecken_check_time": "LUECKEN_CHECK_TIME",
    "luecken_abend_time": "LUECKEN_ABEND_TIME",
    "termin_zustellung_time": "TERMIN_ZUSTELLUNG_TIME",
}

_DEFAULTS = {"bot_name": "", "sklave_anrede": "", "setup_kontext": "", "persona_stil": "", "sprache": "",
             "bot_locale": "", "safeword": "", "resume_wort": "",
             "dom_geschlecht": "", "sub_geschlecht": "",
             "abwesend_von": "", "abwesend_bis": "", "abwesend_grund": "",
             **{feld: "" for feld in ZEIT_FELDER}}

_caches: dict[str, dict] = {}


def _cache_fuer(paar_id: str) -> dict:
    return _caches.setdefault(str(paar_id), dict(_DEFAULTS))


def _aktueller_cache() -> dict:
    return _cache_fuer(paare.aktueller_kontext())


def _profil_user_id() -> str:
    """Mandanten-Key des Dom-Profils, in dem die Persona-Felder liegen
    (Legacy-Paar: "domina", weitere Paare: "{paar_id}:domina")."""
    return paare.user_id_fuer(paare.aktueller_kontext(), paare.ROLLE_DOM)


# Rückwärts-kompatibler Alias auf den Cache des Legacy-/Env-Paars: Tests (und
# evtl. Alt-Code) mutieren `persona_config._cache` in-place – das ist DERSELBE
# dict wie _caches["1"], daher sehen die Getter die Änderungen weiterhin.
_cache = _cache_fuer(paare.LEGACY_PAAR_ID)


async def load() -> None:
    """Beim Bot-Start aufrufen – lädt die Persona-Felder ALLER Paare in die Caches."""
    for paar in paare.alle_paare():
        try:
            p = await qdrant.get_user_profile(paar.user_id(paare.ROLLE_DOM)) or {}
            cache = _cache_fuer(paar.paar_id)
            for feld in _DEFAULTS:
                cache[feld] = (p.get(feld) or "").strip()
            logger.info("persona_config[%s] geladen: bot_name=%r, sklave_anrede=%r, setup=%s, stil=%r, sprache=%r, rollen=%r/%r",
                        paar.paar_id, cache["bot_name"], cache["sklave_anrede"], bool(cache["setup_kontext"]),
                        cache["persona_stil"], cache["sprache"],
                        cache["dom_geschlecht"] or "frau", cache["sub_geschlecht"] or "mann")
        except Exception:
            logger.exception("persona_config.load fehlgeschlagen (Paar %s)", paar.paar_id)


def vergiss_paar(paar_id: str) -> None:
    """Cache eines entfernten Paares verwerfen (Paar-Löschung, handlers/admin.py)."""
    _caches.pop(str(paar_id), None)


def bot_name() -> str:
    return _aktueller_cache().get("bot_name", "")


def sklave_anrede() -> str:
    return _aktueller_cache().get("sklave_anrede", "")


def setup_kontext() -> str:
    return _aktueller_cache().get("setup_kontext", "")


async def set_bot_name(name: str) -> str:
    name = (name or "").strip()
    _aktueller_cache()["bot_name"] = name
    await qdrant.patch_profile_fields(_profil_user_id(), {"bot_name": name})
    return name


async def set_sklave_anrede(name: str) -> str:
    name = (name or "").strip()
    _aktueller_cache()["sklave_anrede"] = name
    await qdrant.patch_profile_fields(_profil_user_id(), {"sklave_anrede": name})
    return name


async def set_setup_kontext(text: str) -> str:
    text = (text or "").strip()
    _aktueller_cache()["setup_kontext"] = text
    await qdrant.patch_profile_fields(_profil_user_id(), {"setup_kontext": text})
    return text


def sprache() -> str:
    """Antwort-Sprache der generativen Pfade (leer = Deutsch/Prompt-Sprache).

    Deckt über die drei zentralen Persona-Bausteine ~90 % der LLM-Outputs ab.
    NICHT abgedeckt sind UI-Festtexte (bot/messages.py + Handler), Keyword-
    Matching (synonyme/limits_check/kategorie_logik) und Command-Beschreibungen
    – siehe TODO „Sprach-Unterstützung“."""
    return _aktueller_cache().get("sprache", "")


def sprache_anweisung() -> str:
    """Einzeilige Sprach-Anweisung für generative Prompts OHNE Persona-/Coach-
    Baustein (persona.py/coach_persona.py tragen sie schon selbst). Auch für
    intern gespeicherte Artefakte (Dossier, offene Fäden, Kurzlabels) – die
    landen wieder in Prompts/UI und sollen zur Antwortsprache passen.
    NICHT an Klassifikations-Prompts hängen, deren Parser feste (deutsche)
    Token erwarten (klassifiziere, _ist_ablehnungsgrund, limits_check …)."""
    s = sprache()
    return f"\nSPRACHE: Antworte ausschließlich auf {s}." if s else ""


async def set_sprache(wert: str) -> str:
    wert = (wert or "").strip()
    _aktueller_cache()["sprache"] = wert
    await qdrant.patch_profile_fields(_profil_user_id(), {"sprache": wert})
    return wert


def safeword() -> str:
    """Safeword DES PAARES (lowercase; leer = globaler Env-Default config.SAFEWORD).
    SICHERHEIT: nur lesen, nie cachen – muss immer den aktuellen Wert liefern."""
    return (_aktueller_cache().get("safeword", "") or config.SAFEWORD).lower()


def resume_wort() -> str:
    """Resume-Wort des Paares (hebt die Safeword-Pause auf; leer = config.RESUME_WORT)."""
    return (_aktueller_cache().get("resume_wort", "") or config.RESUME_WORT).lower()


async def set_safeword(wort: str, resume: str) -> tuple[str, str]:
    """Setzt Safeword + Resume-Wort des Paares ("" = zurück auf Env-Default).
    Validierung (2 verschiedene Einzel-Wörter) macht der Aufrufer (einstellungen.py)."""
    wort = (wort or "").strip().lower()
    resume = (resume or "").strip().lower()
    cache = _aktueller_cache()
    cache["safeword"] = wort
    cache["resume_wort"] = resume
    await qdrant.patch_profile_fields(_profil_user_id(), {"safeword": wort, "resume_wort": resume})
    return wort, resume


# Antwortsprache (Freitext) → ISO-639-1-Code für Whisper-STT und die
# Piper-Stimmen-Map (config.TTS_STIMMEN). Bewusst nur gängige Sprachen –
# Unbekanntes → "" (dann greifen STT_SPRACHE/TTS_VOICE bzw. Server-Default).
_SPRACH_CODES = {
    "": "de", "deutsch": "de", "german": "de", "de": "de",
    "englisch": "en", "english": "en", "en": "en",
    "französisch": "fr", "franzoesisch": "fr", "french": "fr", "fr": "fr",
    "spanisch": "es", "spanish": "es", "es": "es",
    "italienisch": "it", "italian": "it", "it": "it",
    "niederländisch": "nl", "niederlaendisch": "nl", "dutch": "nl", "nl": "nl",
    "polnisch": "pl", "polish": "pl", "pl": "pl",
    "portugiesisch": "pt", "portuguese": "pt", "pt": "pt",
    "russisch": "ru", "russian": "ru", "ru": "ru",
    "türkisch": "tr", "tuerkisch": "tr", "turkish": "tr", "tr": "tr",
}


def sprach_code() -> str:
    """ISO-Code der Antwortsprache des Kontext-Paares ("" = unbekannte Sprache;
    leer/Standard = "de", die Default-Prompt-Sprache). Für Voice: Whisper-
    language-Hint (stt.py) und Piper-Stimmen-Wahl (tts.py)."""
    return _SPRACH_CODES.get(sprache().strip().lower(), "")


def locale_fuer_sprache(sprache: str) -> str:
    """Leitet die UI-Locale aus der gewählten Antwortsprache ab – nur für
    Sprachen, zu denen ein Locale-Katalog existiert (de/en); alles andere
    (inkl. "Standard" = leer) bleibt beim Deployment-Default."""
    s = (sprache or "").strip().lower()
    if s in ("englisch", "english", "en"):
        return "en"
    if s in ("deutsch", "german", "de"):
        return "de"
    return ""


def ui_locale() -> str:
    """UI-Locale DES PAARES für statische Texte/Menüs ("de"/"en"; leer =
    Deployment-Default config.BOT_LOCALE). Aufgelöst in messages.t() und
    commands_katalog – im Gegensatz zu sprache() (LLM-Antwortsprache,
    Freitext) sind hier nur die vorhandenen Locale-Kataloge möglich."""
    return _aktueller_cache().get("bot_locale", "")


async def set_ui_locale(wert: str) -> str:
    """Setzt die UI-Locale des Paares. Unbekannte Locales → "" (Default)."""
    from bot.locales import VERFUEGBAR
    wert = (wert or "").strip().lower()
    if wert not in VERFUEGBAR:
        wert = ""
    _aktueller_cache()["bot_locale"] = wert
    await qdrant.patch_profile_fields(_profil_user_id(), {"bot_locale": wert})
    return wert


def dom_geschlecht() -> str:
    """Geschlecht der dominanten Rolle ('frau'/'mann', leer = 'frau').
    Auflösung/Validierung + alle abgeleiteten Prompt-Bausteine: bot/prompts/rollen.py."""
    return _aktueller_cache().get("dom_geschlecht", "")


def sub_geschlecht() -> str:
    """Geschlecht der devoten Rolle ('mann'/'frau', leer = 'mann'). Siehe rollen.py."""
    return _aktueller_cache().get("sub_geschlecht", "")


async def set_rollen(dom: str, sub: str) -> tuple[str, str]:
    """Setzt die Rollen-Konstellation. Werte-Validierung macht rollen.py beim
    Lesen (unbekannt → Default), hier wird nur persistiert."""
    dom = (dom or "").strip().lower()
    sub = (sub or "").strip().lower()
    cache = _aktueller_cache()
    cache["dom_geschlecht"] = dom
    cache["sub_geschlecht"] = sub
    await qdrant.patch_profile_fields(_profil_user_id(), {"dom_geschlecht": dom, "sub_geschlecht": sub})
    return dom, sub


def abwesenheit() -> tuple["date", "date", str] | None:
    """Eingetragene Abwesenheit des Paares als (von, bis, grund) – auch eine erst
    GEPLANTE (von in der Zukunft), damit Prompts sie ankündigen können. None,
    wenn keine gesetzt oder der Zeitraum abgelaufen ist (läuft automatisch aus,
    kein Aufräum-Job nötig)."""
    from datetime import date
    cache = _aktueller_cache()
    von_s, bis_s = cache.get("abwesend_von", ""), cache.get("abwesend_bis", "")
    if not von_s or not bis_s:
        return None
    try:
        von, bis = date.fromisoformat(von_s), date.fromisoformat(bis_s)
    except ValueError:
        return None
    if _heute_lokal() > bis:
        return None
    return von, bis, cache.get("abwesend_grund", "")


def ist_abwesend() -> bool:
    """True nur im LAUFENDEN Zeitraum (von <= heute <= bis) – eine erst geplante
    Abwesenheit zählt nicht. BEWUSST kein Job-Stopp daran gekoppelt: alle Jobs
    laufen während einer Abwesenheit weiter, nur die Generier-Prompts bekommen
    den Zeitraum als Fakt (abwesenheit_hinweis)."""
    a = abwesenheit()
    return bool(a and a[0] <= _heute_lokal())


def abwesenheit_hinweis() -> str:
    """Prompt-Baustein (wie sprache_anweisung): die eingetragene Abwesenheit als
    harter Fakt für Herrin-/Coach-Chat und ALLE Aufgaben-Generatoren (zentral
    eingespeist in limits_check.generate_mit_limit_retry). Leer = keine.
    Deterministisch statt über Retrieval – sonst 'weiß' das Modell es nur, wenn
    der Gesprächsschnipsel zufällig in den Kontext-Treffern landet."""
    from datetime import timedelta
    a = abwesenheit()
    if not a:
        return ""
    von, bis, grund = a
    from bot.prompts import rollen  # lazy: rollen importiert persona_config (lazy)
    s = rollen.sub()
    wer = s["label_nom"][0].upper() + s["label_nom"][1:]
    zeitraum = f"{von.strftime('%d.%m.%Y')} – {bis.strftime('%d.%m.%Y')}"
    grund_teil = f", Grund: {grund}" if grund else ""
    if von > _heute_lokal():
        lage = f"{wer} wird ABWESEND sein (verreist/unterwegs, nicht zu Hause): {zeitraum}{grund_teil}."
    else:
        lage = f"{wer} ist zurzeit ABWESEND (verreist/unterwegs, nicht zu Hause): {zeitraum}{grund_teil}."
    zurueck = (bis + timedelta(days=1)).strftime("%d.%m.%Y")
    return (
        "\n\nABWESENHEIT (Fakt – hat Vorrang vor allem, was der Verlauf nahelegt): "
        + lage
        + f" Ab dem {zurueck} ist {s['nom']} wieder zu Hause."
        + " Berücksichtige das bei allem, was du vorschlägst oder aufträgst: nichts, was"
        + " Anwesenheit zu Hause, häusliche Ausstattung oder gemeinsame Zeit vor Ort erfordert –"
        + " unterwegs machbare Aufgaben (Nachrichten, Fotos, Übungen ohne Hilfsmittel) sind okay."
        + f" Behaupte nach der Rückkehr NICHT, {s['nom']} sei noch weg."
    )


def _heute_lokal() -> "date":
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


async def set_abwesenheit(von: "date | None", bis: "date | None", grund: str = "") -> None:
    """Setzt (oder mit von=bis=None: löscht) die Abwesenheit des Paares.
    Darf von BEIDEN Rollen geändert werden (handlers/abwesenheit.py) –
    persistiert wird wie alle Persona-Felder im Dom-Profil."""
    werte = {
        "abwesend_von": von.isoformat() if von else "",
        "abwesend_bis": bis.isoformat() if bis else "",
        "abwesend_grund": (grund or "").strip() if von else "",
    }
    _aktueller_cache().update(werte)
    await qdrant.patch_profile_fields(_profil_user_id(), werte)


def zeit(feld: str) -> str:
    """Tages-Zeit (HH:MM) des Kontext-Paares für `feld` (s. ZEIT_FELDER);
    leer gespeichert = globaler Env-Default aus config."""
    if feld not in ZEIT_FELDER:
        raise ValueError(f"Unbekanntes Zeit-Feld: {feld!r}")
    wert = _aktueller_cache().get(feld, "")
    return wert or getattr(config, ZEIT_FELDER[feld])


async def set_zeit(feld: str, wert: str) -> str:
    """Setzt eine Tages-Zeit des Kontext-Paares. "" = zurück auf Env-Default.
    Format-Validierung (HH:MM, 00-23) macht der Aufrufer (einstellungen.py)."""
    if feld not in ZEIT_FELDER:
        raise ValueError(f"Unbekanntes Zeit-Feld: {feld!r}")
    wert = (wert or "").strip()
    _aktueller_cache()[feld] = wert
    await qdrant.patch_profile_fields(_profil_user_id(), {feld: wert})
    return wert


def persona_stil() -> str:
    """Key des aktiven Stil-Presets (leer = Default). Aufgelöst in persona_presets."""
    return _aktueller_cache().get("persona_stil", "")


async def set_persona_stil(key: str) -> str:
    """Setzt das Stil-Preset. Validierung gegen die Preset-Liste macht der Aufrufer
    (einstellungen.py) – hier wird nur persistiert."""
    key = (key or "").strip()
    _aktueller_cache()["persona_stil"] = key
    await qdrant.patch_profile_fields(_profil_user_id(), {"persona_stil": key})
    return key
