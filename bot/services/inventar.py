"""
Inventar 🧰 – Spielsachen und Hilfsmittel, die im Haushalt des Paares wirklich da sind.

Zwei Listen pro Paar, gepflegt von BEIDEN Rollen (/inventar, /profil, Mini-App):

  * vorhanden  – was da ist ("Gerte (hart, nur Po)", "Plug (Größe M)")
  * wunsch     – Anschaffungswünsche; nie Voraussetzung einer Aufgabe, höchstens
                 als Aussicht/Belohnung erwähnbar, für den Coach eine Geschenkidee

Freitext je Eintrag, optionale Klammer-Notiz steuert die Nutzung (gleiche
Klammer-Logik wie bei den Vorlieben). Die Listen liegen als Paar-Konfiguration
im Dom-Profil (wie setup_kontext/abwesend_*), gecacht pro Paar wie
persona_config – die Prompt-Bausteine (coach_persona.inventar_block) greifen
synchron zu, die Aufgaben-Generatoren und beide Chats bekommen damit dasselbe
Wissen. Der stille Profil-Auto-Updater kennt die Felder nicht und fasst sie
nie an.

Wirkung in den Prompts:
  * WISSEN + VERBOT überall, wo auch der Setup-Kontext hinwandert: kein
    Spielzeug verlangen, das nicht auf der Liste steht (Alltagsgegenstände
    bleiben erlaubt). Leere Liste = kein Verbot (fail-open).
  * AUSSTATTUNGS-IMPULS (Chance config.INVENTAR_IMPULS_CHANCE) in den täglichen
    Generatoren: gezielt EIN Gegenstand tragend einbauen. Die zuletzt gewählten
    Gegenstände (ZULETZT_MERKEN) sind ausgeschlossen, während einer eingetragenen
    Abwesenheit fällt der Impuls aus.
"""
import logging
import random
import re

from bot import config
from bot.services import paare, qdrant

logger = logging.getLogger(__name__)

FELD_VORHANDEN = "inventar"
FELD_WUNSCH = "inventar_wunsch"
FELD_ZULETZT = "inventar_zuletzt"
# Sparziel ⭐ (Bauplan 16.09.2026): Punkte-Preis je Wunsch (Dom-Seite tippt
# 200/300/500, ohne Tipp kein Preis) und gewährte Wünsche (Punkte abgebucht,
# Gegenstand noch nicht da). Sparziel = günstigster bepreister Wunsch.
FELD_PREISE = "inventar_wunsch_preise"
FELD_GEWAEHRT = "inventar_gewaehrt"
MAX_GEWAEHRT = 15

# Hook der Nachrichten-Schicht (handlers/waehrung.frage_wunschpreis): wird
# NACH dem Persistieren für jeden NEUEN Wunsch mit dem Text aufgerufen – egal
# ob er per /inventar, Mini-App oder Skript kam. None = still.
NEUER_WUNSCH_HOOK = None

MAX_VORHANDEN = 40
MAX_WUNSCH = 15
MAX_LAENGE = 100
ZULETZT_MERKEN = 5

# Eingabe-Formen des /inventar-Flows (Präfix-Erkennung, Rest = Nutzlast):
#   + Gerte (hart, nur Po)      → hinzufügen (vorhanden)
#   + wunsch: Käfig / wunsch: X → hinzufügen (Wunschliste)
#   - 3  /  - w2                → entfernen (Nummer aus der Anzeige)
#   da w2                       → Wunsch erfüllt → nach „vorhanden“ verschieben
_WUNSCH_PRAEFIX_RE = re.compile(r"^(?:wunsch|wish|w)\s*[:\-–]\s*", re.IGNORECASE)
_REF_RE = re.compile(r"^(w)?\s*(\d{1,2})$", re.IGNORECASE)
_DA_WORTE = {"da", "gekauft", "angeschafft", "habe", "have", "got", "bought"}

_caches: dict[str, dict] = {}


def _leer() -> dict:
    return {"vorhanden": [], "wunsch": [], "zuletzt": [], "preise": {}, "gewaehrt": []}


def saubere_preise(werte, wuensche: list[str]) -> dict:
    """{Wunschtext: Preis} – nur ganze Preise > 0 und nur für Wünsche, die noch
    auf der Liste stehen (Schlüssel-Abgleich ohne Groß/Klein)."""
    if not isinstance(werte, dict):
        return {}
    kanon = {w.lower(): w for w in wuensche}
    sauber: dict = {}
    for k, v in werte.items():
        try:
            preis = int(v)
        except (TypeError, ValueError):
            continue
        w = kanon.get(str(k).strip().lower())
        if w and preis > 0:
            sauber[w] = preis
    return sauber


def _cache_fuer(paar_id: str) -> dict:
    return _caches.setdefault(str(paar_id), _leer())


def _aktueller_cache() -> dict:
    return _cache_fuer(paare.aktueller_kontext())


def _profil_user_id() -> str:
    """Mandanten-Key des Dom-Profils, in dem die Paar-Konfiguration liegt."""
    return paare.user_id_fuer(paare.aktueller_kontext(), paare.ROLLE_DOM)


def saubere_liste(werte, max_n: int) -> list[str]:
    """Strings trimmen, deckeln (Länge + Anzahl), Duplikate (Groß/Klein-blind)
    verwerfen. Reihenfolge bleibt erhalten."""
    out: list[str] = []
    gesehen: set[str] = set()
    for w in werte or []:
        if not isinstance(w, str):
            continue
        w = " ".join(w.split())[:MAX_LAENGE].strip()
        if not w or w.lower() in gesehen:
            continue
        gesehen.add(w.lower())
        out.append(w)
        if len(out) >= max_n:
            break
    return out


async def load() -> None:
    """Beim Bot-Start aufrufen (neben persona_config.load): Listen ALLER Paare cachen."""
    for paar in paare.alle_paare():
        try:
            p = await qdrant.get_user_profile(paar.user_id(paare.ROLLE_DOM)) or {}
            cache = _cache_fuer(paar.paar_id)
            cache["vorhanden"] = saubere_liste(p.get(FELD_VORHANDEN) or [], MAX_VORHANDEN)
            cache["wunsch"] = saubere_liste(p.get(FELD_WUNSCH) or [], MAX_WUNSCH)
            cache["zuletzt"] = saubere_liste(p.get(FELD_ZULETZT) or [], ZULETZT_MERKEN)
            cache["preise"] = saubere_preise(p.get(FELD_PREISE) or {}, cache["wunsch"])
            cache["gewaehrt"] = saubere_liste(p.get(FELD_GEWAEHRT) or [], MAX_GEWAEHRT)
            logger.info("inventar[%s] geladen: %d vorhanden, %d Wünsche",
                        paar.paar_id, len(cache["vorhanden"]), len(cache["wunsch"]))
        except Exception:
            logger.exception("inventar.load fehlgeschlagen (Paar %s)", paar.paar_id)


def vergiss_paar(paar_id: str) -> None:
    _caches.pop(str(paar_id), None)


def vorhanden() -> list[str]:
    return list(_aktueller_cache()["vorhanden"])


def wuensche() -> list[str]:
    return list(_aktueller_cache()["wunsch"])


def preise() -> dict:
    return dict(_aktueller_cache().get("preise") or {})


def gewaehrte() -> list[str]:
    return list(_aktueller_cache().get("gewaehrt") or [])


def preis(wunsch: str) -> int | None:
    """Punkte-Preis eines Wunsches (Abgleich ohne Groß/Klein), None = unbepreist."""
    ziel = (wunsch or "").strip().lower()
    for w, p in (_aktueller_cache().get("preise") or {}).items():
        if w.lower() == ziel:
            return int(p)
    return None


def wunsch_kanonisch(wunsch: str) -> str | None:
    """Text des Wunsches, wie er auf der Liste steht (Abgleich ohne Groß/Klein)."""
    ziel = (wunsch or "").strip().lower()
    return next((w for w in _aktueller_cache()["wunsch"] if w.lower() == ziel), None)


def sparziel() -> tuple[str, int] | None:
    """Günstigster bepreister Wunsch als (Text, Preis); bei Gleichstand der
    weiter oben stehende. None ohne bepreisten Wunsch."""
    cache = _aktueller_cache()
    bestes: tuple[str, int] | None = None
    for w in cache["wunsch"]:
        p = preis(w)
        if p is None:
            continue
        if bestes is None or p < bestes[1]:
            bestes = (w, p)
    return bestes


async def setze_preis(wunsch: str, punkte: int) -> str | None:
    """Preis eines Wunsches setzen/ändern (persistieren, dann Cache). Gibt den
    kanonischen Wunschtext zurück, None wenn der Wunsch nicht (mehr) existiert."""
    w = wunsch_kanonisch(wunsch)
    if not w or int(punkte) <= 0:
        return None
    cache = _aktueller_cache()
    neu = {k: v for k, v in (cache.get("preise") or {}).items() if k.lower() != w.lower()}
    neu[w] = int(punkte)
    await qdrant.patch_profile_fields(_profil_user_id(), {FELD_PREISE: neu})
    cache["preise"] = neu
    return w


async def gewaehren(wunsch: str) -> str | None:
    """Wunsch als gewährt verbuchen: von der Wunschliste (samt Preis) in die
    Gewährt-Liste. Punkte bucht der Aufrufer (waehrung.buchen). Gibt den
    kanonischen Text zurück, None wenn unbekannt."""
    w = wunsch_kanonisch(wunsch)
    if not w:
        return None
    cache = _aktueller_cache()
    gewaehrt = saubere_liste([e for e in cache["gewaehrt"] if e.lower() != w.lower()] + [w],
                             MAX_GEWAEHRT)
    wuensche = [e for e in cache["wunsch"] if e.lower() != w.lower()]
    preise_neu = {k: v for k, v in (cache.get("preise") or {}).items() if k.lower() != w.lower()}
    await qdrant.patch_profile_fields(_profil_user_id(), {
        FELD_WUNSCH: wuensche, FELD_PREISE: preise_neu, FELD_GEWAEHRT: gewaehrt,
    })
    cache["wunsch"], cache["preise"], cache["gewaehrt"] = wuensche, preise_neu, gewaehrt
    return w


def zuletzt() -> list[str]:
    return list(_aktueller_cache()["zuletzt"])


def name(eintrag: str) -> str:
    """Kurzname ohne Klammer-Notiz: 'Gerte (hart, nur Po)' → 'Gerte'."""
    return (eintrag or "").split("(", 1)[0].strip() or (eintrag or "").strip()


async def setze(vorhanden_neu: list | None = None, wuensche_neu: list | None = None) -> tuple[list, list]:
    """Listen ersetzen (None = unverändert). Erst persistieren, dann Cache
    (persona_config-Muster D9/N18). Gibt die bereinigten Listen zurück."""
    cache = _aktueller_cache()
    felder: dict = {}
    v = cache["vorhanden"] if vorhanden_neu is None else saubere_liste(vorhanden_neu, MAX_VORHANDEN)
    w = cache["wunsch"] if wuensche_neu is None else saubere_liste(wuensche_neu, MAX_WUNSCH)
    if vorhanden_neu is not None:
        felder[FELD_VORHANDEN] = v
        # Erfüllte Wünsche verschwinden von der Wunschliste, sobald sie als
        # vorhanden eingetragen werden (egal über welchen Pfad).
        namen_v = {name(e).lower() for e in v}
        w = [e for e in w if name(e).lower() not in namen_v]
        if w != cache["wunsch"]:
            felder[FELD_WUNSCH] = w
    if wuensche_neu is not None:
        felder[FELD_WUNSCH] = w
    # Preise nur für Wünsche, die bleiben; ein neu vorhandener Gegenstand räumt
    # auch einen gleichnamigen GEWÄHRTEN Wunsch ab (er ist dann da).
    preise_neu = saubere_preise(cache.get("preise") or {}, w)
    if preise_neu != (cache.get("preise") or {}):
        felder[FELD_PREISE] = preise_neu
    gewaehrt_neu = list(cache.get("gewaehrt") or [])
    if vorhanden_neu is not None:
        namen_v = {name(e).lower() for e in v}
        gewaehrt_neu = [e for e in gewaehrt_neu if name(e).lower() not in namen_v]
        if gewaehrt_neu != (cache.get("gewaehrt") or []):
            felder[FELD_GEWAEHRT] = gewaehrt_neu
    alte_wuensche = {e.lower() for e in cache["wunsch"]}
    if felder:
        await qdrant.patch_profile_fields(_profil_user_id(), felder)
        cache["vorhanden"], cache["wunsch"] = v, w
        cache["preise"], cache["gewaehrt"] = preise_neu, gewaehrt_neu
    if wuensche_neu is not None and NEUER_WUNSCH_HOOK is not None:
        for e in w:
            if e.lower() not in alte_wuensche:
                try:
                    await NEUER_WUNSCH_HOOK(e)
                except Exception:
                    logger.exception("Neuer-Wunsch-Hook fehlgeschlagen (%s)", e[:40])
    return list(v), list(w)


# ---------------------------------------------------------------------------
# Eingabe-Flow (/inventar)
# ---------------------------------------------------------------------------

def parse_eingabe(text: str) -> tuple[str, str] | None:
    """Erkennt eine Pflege-Eingabe. Rückgabe (op, nutzlast):
      ("add", "Gerte (…)")  ("add_wunsch", "Käfig")  ("del", "3"|"w2")  ("da", "w2")
    None = keine Pflege-Eingabe (z. B. normaler Chat-Text)."""
    t = " ".join((text or "").split()).strip()
    if not t:
        return None
    if t[0] in "+＋":
        rest = t[1:].strip()
        if not rest:
            return None
        if _WUNSCH_PRAEFIX_RE.match(rest):
            nutz = _WUNSCH_PRAEFIX_RE.sub("", rest, count=1).strip()
            return ("add_wunsch", nutz) if nutz else None
        return ("add", rest)
    if t[0] in "-−–—":
        ref = t[1:].strip().lower().replace(" ", "")
        return ("del", ref) if _REF_RE.match(ref) else None
    m = _WUNSCH_PRAEFIX_RE.match(t)
    if m:
        nutz = t[m.end():].strip()
        return ("add_wunsch", nutz) if nutz else None
    teile = t.split(None, 1)
    if len(teile) == 2 and teile[0].lower() in _DA_WORTE:
        ref = teile[1].strip().lower().replace(" ", "")
        if _REF_RE.match(ref):
            return ("da", ref)
    return None


def _ref_aufloesen(ref: str) -> tuple[str, int] | None:
    """'3' → ('vorhanden', 2), 'w2' → ('wunsch', 1); None bei Formfehler/außerhalb."""
    m = _REF_RE.match((ref or "").strip().lower().replace(" ", ""))
    if not m:
        return None
    liste = "wunsch" if m.group(1) else "vorhanden"
    idx = int(m.group(2)) - 1
    if idx < 0 or idx >= len(_aktueller_cache()[liste]):
        return None
    return liste, idx


async def hinzufuegen(text: str, wunsch: bool = False) -> tuple[bool, str]:
    """Eintrag anhängen. (ok, grund) – grund ∈ {"", "leer", "doppelt", "voll"}."""
    eintrag = " ".join((text or "").split())[:MAX_LAENGE].strip()
    if not eintrag:
        return False, "leer"
    cache = _aktueller_cache()
    liste = "wunsch" if wunsch else "vorhanden"
    limit = MAX_WUNSCH if wunsch else MAX_VORHANDEN
    if eintrag.lower() in {e.lower() for e in cache[liste]}:
        return False, "doppelt"
    if len(cache[liste]) >= limit:
        return False, "voll"
    if wunsch:
        await setze(wuensche_neu=cache["wunsch"] + [eintrag])
    else:
        await setze(vorhanden_neu=cache["vorhanden"] + [eintrag])
    return True, ""


async def entfernen(ref: str) -> str | None:
    """Eintrag per Anzeige-Nummer löschen ('3' bzw. 'w2'). Gibt den Text zurück."""
    pos = _ref_aufloesen(ref)
    if not pos:
        return None
    liste, idx = pos
    cache = _aktueller_cache()
    neu = list(cache[liste])
    eintrag = neu.pop(idx)
    if liste == "wunsch":
        await setze(wuensche_neu=neu)
    else:
        await setze(vorhanden_neu=neu)
    return eintrag


async def angeschafft(ref: str) -> tuple[str | None, str]:
    """Wunsch 'wN' erfüllt → nach vorhanden verschieben. (text, grund) –
    grund ∈ {"", "unbekannt", "kein_wunsch", "voll"}."""
    pos = _ref_aufloesen(ref)
    if not pos:
        return None, "unbekannt"
    liste, idx = pos
    if liste != "wunsch":
        return None, "kein_wunsch"
    cache = _aktueller_cache()
    if len(cache["vorhanden"]) >= MAX_VORHANDEN:
        return None, "voll"
    eintrag = cache["wunsch"][idx]
    await setze(vorhanden_neu=cache["vorhanden"] + [eintrag],
                wuensche_neu=[e for i, e in enumerate(cache["wunsch"]) if i != idx])
    return eintrag, ""


def anzeige(vorh: list[str], wuen: list[str], preise_map: dict | None = None,
            ziel: str | None = None, gewaehrt: list[str] | None = None) -> tuple[str, str]:
    """Nummerierte Listen für die Anzeige ('1. Gerte …' / 'w1. Käfig · 300 P 🎯').
    preise_map/ziel/gewaehrt optional (Sparziel-Anzeige)."""
    v = "\n".join(f"{i + 1}. {e}" for i, e in enumerate(vorh)) or "–"
    pm = {k.lower(): p for k, p in (preise_map or {}).items()}
    zeilen = []
    for i, e in enumerate(wuen):
        zeile = f"w{i + 1}. {e}"
        p = pm.get(e.lower())
        if p:
            zeile += f" · {p} P"
        if ziel and e.lower() == ziel.lower():
            zeile += " 🎯"
        zeilen.append(zeile)
    for e in gewaehrt or []:
        zeilen.append(f"✅ {e} (gewährt)")
    w = "\n".join(zeilen) or "–"
    return v, w


# ---------------------------------------------------------------------------
# Ausstattungs-Impuls
# ---------------------------------------------------------------------------

def waehle_impuls(vorhanden_liste: list[str], zuletzt_liste: list[str],
                  zufall=random.random, wahl=random.choice) -> str | None:
    """Reine Auswahl (testbar): ein Gegenstand, der nicht unter den zuletzt
    gewählten ist; sind alle verbraucht, nur der allerletzte ausgeschlossen."""
    if not vorhanden_liste:
        return None
    gesperrt = set(zuletzt_liste or [])
    kandidaten = [e for e in vorhanden_liste if e not in gesperrt]
    if not kandidaten:
        letzter = (zuletzt_liste or [None])[0]
        kandidaten = [e for e in vorhanden_liste if e != letzter] or list(vorhanden_liste)
    return wahl(kandidaten)


async def impuls_wahl(chance: float | None = None) -> str | None:
    """Würfelt den AUSSTATTUNGS-IMPULS für einen Generator-Lauf: None bei leerer
    Liste, Abwesenheit oder verlorenem Chance-Wurf. Der gewählte Gegenstand
    wandert in die Sperrliste (persistiert best-effort, Cache in jedem Fall)."""
    from bot.services import persona_config  # lazy: zirkelfrei
    liste = vorhanden()
    if not liste:
        return None
    if persona_config.ist_abwesend():
        return None
    chance = config.INVENTAR_IMPULS_CHANCE if chance is None else chance
    if chance <= 0 or random.random() >= chance:
        return None
    wahl = waehle_impuls(liste, zuletzt())
    if not wahl:
        return None
    cache = _aktueller_cache()
    neu = ([wahl] + [z for z in cache["zuletzt"] if z != wahl])[:ZULETZT_MERKEN]
    cache["zuletzt"] = neu
    try:
        await qdrant.patch_profile_fields(_profil_user_id(), {FELD_ZULETZT: neu})
    except Exception:
        logger.warning("Inventar-Sperrliste nicht persistiert (Cache aktuell)", exc_info=True)
    logger.info("Ausstattungs-Impuls aktiv: %s", name(wahl)[:40])
    return wahl
