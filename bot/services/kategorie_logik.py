"""
Zentrale Kategorie-Logik für das Lern-System.

Drei Aufgaben, die vorher verstreut/dupliziert waren:
  1. Klassifikation neuer Aufgaben (Keyword + Grok-Fallback) — verhindert, dass
     Feedback unter "allgemein" statt der echten Kategorie akkumuliert (giftiger
     Feedback-Loop, siehe QDRANT_AUDIT).
  2. Ableitung von Dislike-/Top-Kategorien aus dem Sklaven-Profil (war 4x kopiert).
  3. Auswahl von Vorschlags-Kategorien im 60/30/10-Mix (Basis = Wunsch/Top,
     Exploration = Cluster-Nachbarn, Wildcard = Rest) statt rein zufällig –
     Dislikes immer ausgeschlossen, Wiederholungen gedämpft.
"""
import logging
import random

from bot import config
from bot.services import grok

logger = logging.getLogger(__name__)

# Reaktions-Buckets (für Decay/Top/Dislike-Auswertung)
_BUCKETS = ("positiv", "neutral", "negativ")

# ---------------------------------------------------------------------------
# Keyword-Klassifikation — NUR Kategorien aus config.AUFGABEN_KATEGORIEN.
# Mehrdeutiges überlässt man bewusst dem Grok-Fallback (klassifiziere()).
# ---------------------------------------------------------------------------
_KEYWORDS: dict[str, list[str]] = {
    "Analdehnung":          ["dehnung", "weiten", "weitung"],
    "Analeingangstraining": ["eingang", "sphinkter", "sphincter"],
    "Buttplug_Tragen":      ["buttplug", "butt plug", "plug"],
    "Dildo_Training":       ["dildo"],
    "Pegging":              ["pegging"],
    "Strap_on":             ["strap-on", "strapon", "strap on"],
    "Blowjob_Training":     ["blowjob", "blasen"],
    "Deepthroat_Training":  ["deepthroat", "deep throat", "tief schlucken"],
    "Gesichtsfick":         ["gesichtsfick", "facefuck", "face fuck"],
    "Enema_Play":           ["enema", "einlauf", "klistier"],
    "Creampie_Cleanup":     ["creampie", "cleanup", "clean-up"],
    "Fisting":              ["fist"],
    "Prostatamassage":      ["prostata"],
    "Sperma_Schlucken":     ["sperma", "abspritz", "ejakulat"],
    "Schlucken":            ["schluck"],
    "Spanking":             ["spanking", "versohl", "po-hieb"],
    "Impact":               ["impact"],
    "Peitsche":             ["peitsch", "flogger"],
    "Paddle_Training":      ["paddle", "paddel"],
    "Klassische_Fesselspiele": ["fessel", "bondage", "binden", "gefesselt"],
    "Piss_Play":            ["piss", "urin", "natursekt", "nass machen"],
    "Toiletten_Sklave":     ["toilette", "klo "],
    "Arschanbetung":        ["arschanbet", "po anbet", "arsch anbet"],
    "Muschianbetung":       ["muschianbet", "muschi anbet", "pussy worship"],
    "Orgasmusverweigerung": ["orgasmusverweigerung", "nicht kommen", "keuschheit", "chastity", "edging"],
    "Ruiniertes_Orgasmen":  ["ruiniert"],
    "Sissy_Training":       ["sissy"],
    "Feminisierung":        ["feminisier", "crossdress", "frauenkleider", "schminke"],
    "Facesitting":          ["facesitting", "gesicht sitzen"],
    "Smothering":           ["smothering", "ersticken lassen"],
    "Schmerz":              ["schmerz", "trampling", "wachs", "nadeln", "klammern"],
    "Speichelspiel":        ["speichel", "spuck", "sabber"],
    "Pet_Play":             ["pet play", "petplay", "welpe", "hündchen", "ponyplay"],
    "Verbale_Demütigung":   ["verbal", "beschimpf", "auslachen"],
    "Demütigung":           ["demütig"],
    "Erniedrigung":         ["erniedrig"],
    "Objektifizierung":     ["objekt", "möbel", "ding sein"],
    "Psycho":               ["fühlen", "nachdenken", "reflektieren", "mindset", "gedanken", "tagebuch"],
    "Dienst":               ["dienen", "dienst", "service", "putzen", "kochen", "aufräumen", "massage",
                              "gehorsam", "regel", "befolgen", "warten", "schweigen", "erlaubnis"],
    "Bestrafung":           ["strafe", "bestraf", "konsequenz"],
    "Anal":                 ["anal", "hintern", "arsch"],  # bewusst zuletzt — sehr breit
}

# Englische Keywords, additiv zur deutschen Referenz (Veröffentlichungs-Schritt 2):
# Matching läuft immer gegen die UNION beider Sprachen – deutsche Texte treffen
# deutsche Begriffe, englische englische; kein Locale-Plumbing nötig und das
# Bestandsverhalten kann sich nicht ändern. Viele Kategorien brauchen nichts,
# weil der deutsche Eintrag schon englisch ist (plug, pegging, spanking, …).
# ACHTUNG Substring-Fallen bei neuen Einträgen: Matching ist ein reines `in` –
# "rope" steckt in "Europe", "spit" in "hospital", "ass" in "password",
# "clean" in "cleanup" (würde Dienst mit Creampie_Cleanup kreuzen).
_KEYWORDS_EN: dict[str, list[str]] = {
    "Analdehnung":          ["stretch"],
    "Prostatamassage":      ["prostate"],
    "Sperma_Schlucken":     ["semen", "ejaculat"],
    "Schlucken":            ["swallow"],
    "Peitsche":             ["whip"],
    "Klassische_Fesselspiele": ["shibari", "restrain", "bound"],
    "Piss_Play":            ["golden shower", "watersports"],
    "Toiletten_Sklave":     ["toilet"],
    "Arschanbetung":        ["ass worship", "butt worship"],
    "Orgasmusverweigerung": ["denial", "orgasm control"],
    "Ruiniertes_Orgasmen":  ["ruined"],
    "Feminisierung":        ["feminiz", "feminis"],
    "Facesitting":          ["face sitting", "queening"],
    "Schmerz":              ["pain", "wax", "needle", "clamp"],
    "Speichelspiel":        ["saliva", "drool"],
    "Pet_Play":             ["puppy", "pony", "kitten"],
    "Verbale_Demütigung":   ["insult"],
    "Demütigung":           ["humiliat"],
    "Erniedrigung":         ["degrad"],
    "Objektifizierung":     ["furniture"],
    "Psycho":               ["reflect", "journal"],
    "Dienst":               ["obey", "chore", "serve"],
    "Bestrafung":           ["punish", "consequence"],
}
for _kat, _en in _KEYWORDS_EN.items():
    _KEYWORDS[_kat] = _KEYWORDS[_kat] + [k for k in _en if k not in _KEYWORDS[_kat]]

# ---------------------------------------------------------------------------
# Thematische Cluster → Nachbar-Kategorien (Horizont-Erweiterung).
# Nachbarn werden symmetrisch aus den Clustern abgeleitet.
# ---------------------------------------------------------------------------
_CLUSTER: list[list[str]] = [
    # Anal / Penetration
    ["Anal", "Analdehnung", "Analeingangstraining", "Buttplug_Tragen", "Dildo_Training",
     "Pegging", "Strap_on", "Fisting", "Prostatamassage", "Creampie_Cleanup", "Enema_Play"],
    # Oral
    ["Schlucken", "Blowjob_Training", "Deepthroat_Training", "Gesichtsfick",
     "Sperma_Schlucken", "Speichelspiel"],
    # Impact / Schmerz
    ["Spanking", "Impact", "Peitsche", "Paddle_Training", "Schmerz"],
    # Toilette / Natursekt
    ["Piss_Play", "Toiletten_Sklave", "Enema_Play"],
    # Anbetung / Smother
    ["Arschanbetung", "Muschianbetung", "Facesitting", "Smothering"],
    # Orgasmus-Kontrolle
    ["Orgasmusverweigerung", "Ruiniertes_Orgasmen"],
    # Sissy / Feminisierung
    ["Sissy_Training", "Feminisierung"],
    # Psychologisch / Demütigung
    ["Psycho", "Demütigung", "Verbale_Demütigung", "Erniedrigung", "Objektifizierung", "Pet_Play"],
    # Dienst / Disziplin
    ["Dienst", "Bestrafung"],
]


def _build_nachbarn() -> dict[str, set[str]]:
    nachbarn: dict[str, set[str]] = {}
    for cluster in _CLUSTER:
        for kat in cluster:
            nachbarn.setdefault(kat, set()).update(c for c in cluster if c != kat)
    return nachbarn


KATEGORIE_NACHBARN: dict[str, set[str]] = _build_nachbarn()

# Kategorie → Cluster-Indizes (mehrfach möglich, z. B. Enema_Play in Anal + Toilette).
# Für Themen-Vielfalt: „gehört das zum selben Thema wie zuletzt/wie die Basis?“
KATEGORIE_ZU_CLUSTER: dict[str, set[int]] = {}
for _i, _cluster in enumerate(_CLUSTER):
    for _kat in _cluster:
        KATEGORIE_ZU_CLUSTER.setdefault(_kat, set()).add(_i)


def _nachbarn_von(kategorien) -> set[str]:
    out: set[str] = set()
    for kat in kategorien:
        out.update(KATEGORIE_NACHBARN.get(kat, set()))
    return out - set(kategorien)


def _cluster_von(kategorien) -> set[int]:
    """Cluster-Indizes, zu denen die gegebenen Kategorien thematisch gehören."""
    out: set[int] = set()
    for kat in kategorien:
        out |= KATEGORIE_ZU_CLUSTER.get(kat, set())
    return out


# ---------------------------------------------------------------------------
# Kategorien-Pool (Doppelgleisigkeit)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Konstellations-Filter (Veröffentlichungs-Schritt 1, Rest-Item): Kategorien,
# die eine bestimmte Rollen-Konstellation voraussetzen. Prädikat erhält
# (dom_geschlecht, sub_geschlecht); der Default F/M erfüllt ALLE Bedingungen
# (Bestandsverhalten unverändert, Nummern bleiben stabil).
# ---------------------------------------------------------------------------

_KATEGORIE_VORAUSSETZUNG = {
    "Muschianbetung":   lambda d, s: d == "frau",
    "Pegging":          lambda d, s: d == "frau" and s == "mann",
    "Strap_on":         lambda d, s: d == "frau",
    "Prostatamassage":  lambda d, s: s == "mann",
    "Sissy_Training":   lambda d, s: s == "mann",
    "Feminisierung":    lambda d, s: s == "mann",
    # Reale Sperma-Quelle nötig – F/F hat keine (GRUNDIERUNG verbietet erfundenes Sperma)
    "Creampie_Cleanup": lambda d, s: not (d == "frau" and s == "frau"),
    "Sperma_Schlucken": lambda d, s: not (d == "frau" and s == "frau"),
}


def _konstellation_erlaubt(kategorie: str) -> bool:
    check = _KATEGORIE_VORAUSSETZUNG.get(kategorie)
    if check is None:
        return True
    from bot.prompts import rollen
    return check(rollen.dom_geschlecht(), rollen.sub_geschlecht())


def katalog_kategorien() -> list[str]:
    """Fester Katalog, gefiltert auf die konfigurierte Rollen-Konstellation
    (F/M-Default = kompletter Katalog). Basis für alle_kategorien und das
    vielfalt_alle-Abzeichen (unerreichbare Kategorien zählen nicht als Ziel)."""
    return [k for k in config.AUFGABEN_KATEGORIEN if _konstellation_erlaubt(k)]


def alle_kategorien(sklave_profil: dict | None = None) -> list[str]:
    """Dynamischer Kategorien-Pool: statischer Katalog (config.AUFGABEN_KATEGORIEN)
    + im Profil gepflegte EIGENE Kategorien (`eigene_kategorien`, via /wunschkategorien
    als Freitext angelegt). Katalog-Reihenfolge bleibt stabil (Nummern-Auswahl!),
    eigene werden hinten angehängt, case-insensitiv dedupliziert.

    Doppelgleisigkeit: dieser Pool steuert Generierung, Klassifikation und Anzeige –
    Abzeichen (punkte.vielfalt_alle) bleiben bewusst auf dem festen Katalog, damit
    sich Achievement-Ziele nicht rückwirkend verschieben."""
    # Konstellations-Filter: Kategorien, die anatomisch/rollenseitig nicht zur
    # konfigurierten Kombi passen, fliegen raus. Eigene Kategorien bleiben
    # ungefiltert (der Nutzer kennt seine Konstellation).
    pool = katalog_kategorien()
    vorhanden = {k.lower() for k in pool}
    for k in (sklave_profil or {}).get("eigene_kategorien") or []:
        if isinstance(k, str) and k.strip() and k.strip().lower() not in vorhanden:
            pool.append(k.strip())
            vorhanden.add(k.strip().lower())
    return pool


def anzeige_name(kategorie: str) -> str:
    """Anzeige-Form eines Kategorienamens für Telegram-Nachrichten: Unterstriche
    durch Leerzeichen ersetzen ("Buttplug_Tragen" -> "Buttplug Tragen").
    Grund: Legacy-Markdown kennt kein Escaping – Unterstriche in Namen paaren
    sich sonst zu Italic-Spans oder brechen das Parsing (BadRequest).
    Round-trip-sicher: _parse_auswahl in /wunschkategorien joint Leerzeichen
    wieder mit "_", Eingaben in Anzeige-Form treffen also denselben Eintrag."""
    return str(kategorie).replace("_", " ")


def anzeige_liste(kategorien) -> str:
    """Komma-Liste in Anzeige-Form (siehe anzeige_name)."""
    return ", ".join(anzeige_name(k) for k in kategorien)


async def alle_kategorien_async() -> list[str]:
    """Pool inkl. eigener Kategorien, lädt das Sklaven-Profil selbst (für Callsites
    ohne bereits geladenes Profil). Best-effort: bei DB-Fehler nur der Katalog."""
    try:
        from bot.services import qdrant
        profil = await qdrant.get_user_profile("sklave") or {}
    except Exception:
        logger.exception("alle_kategorien_async: Profil-Load fehlgeschlagen – nur Katalog")
        profil = {}
    return alle_kategorien(profil)


# ---------------------------------------------------------------------------
# Klassifikation
# ---------------------------------------------------------------------------

def keyword_match(text: str) -> str:
    """Reines Keyword-Matching. Gibt 'allgemein' zurück, wenn nichts passt."""
    text_lower = (text or "").lower()
    for kat, keywords in _KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return kat
    return "allgemein"


def kategorien_in_text(text: str) -> set[str]:
    """ALLE Kategorien, deren Keywords im Text vorkommen (keyword_match liefert
    nur den ersten Treffer – für Ausschluss-Logik braucht es alle, z.B. beim
    Mapping von Freitext-Vorlieben auf Kategorien)."""
    text_lower = (text or "").lower()
    return {kat for kat, keywords in _KEYWORDS.items()
            if any(kw in text_lower for kw in keywords)}


async def klassifiziere(text: str) -> str:
    """Klassifiziert eine Aufgabe in eine Pool-Kategorie.

    Erst Keyword-Match (schnell, kostenlos). Greift das nicht, klassifiziert Grok
    gegen den Kategorien-Pool (Katalog + eigene Kategorien). Validiert das Ergebnis
    hart gegen den Pool — niemals eine Phantom-Kategorie zurückgeben.
    """
    kat = keyword_match(text)
    if kat != "allgemein":
        return kat
    if not text or not text.strip():
        return "allgemein"

    pool = await alle_kategorien_async()
    kategorien_liste = "\n".join(f"- {k}" for k in pool)
    system = (
        "Ordne die folgende BDSM-Aufgabe GENAU EINER Kategorie aus der Liste zu.\n"
        "Antworte NUR mit dem exakten Kategorie-Namen aus der Liste (inkl. Unterstriche), "
        "sonst mit dem Wort allgemein. Keine Erklärung, kein Markdown.\n"
        "Die Kategorie-Namen sind feste Daten-IDs – exakt so zurückgeben, auch wenn "
        "die Aufgabe in einer anderen Sprache formuliert ist.\n\n"
        f"Kategorien:\n{kategorien_liste}"
    )
    try:
        # temperature=0: Klassifikation soll deterministisch sein (gleiche Aufgabe -> gleiche Kategorie).
        raw = (await grok.simple(f"Aufgabe: {text[:500]}", system=system, temperature=0)).strip().strip(".").strip()
        # exakter Match
        if raw in pool:
            return raw
        # case-/whitespace-toleranter Match
        norm = raw.lower().replace(" ", "_")
        for k in pool:
            if k.lower() == norm:
                return k
    except Exception:
        logger.exception("Grok-Kategorie-Klassifikation fehlgeschlagen")
    return "allgemein"


# ---------------------------------------------------------------------------
# Profil-Ableitungen (vorher 4x dupliziert)
# ---------------------------------------------------------------------------

def reaktions_spitzen(sklave_profil: dict) -> str:
    """Komma-Liste „kategorie: top-reaktion“ aus kategorie_reaktionen – als
    Prompt-Baustein. Leerer String, wenn keine Daten vorliegen."""
    reaktionen = (sklave_profil or {}).get("kategorie_reaktionen", {}) or {}
    spitzen = []
    for kat, v in reaktionen.items():
        if not v:
            continue
        buckets = {b: v.get(b, 0) for b in ("positiv", "neutral", "negativ")}
        if sum(buckets.values()) > 0:
            spitzen.append(f"{kat}: {max(buckets, key=buckets.get)}")
        # Ohne Zähl-Signal keine Spitze – v enthält sonst nur Metadaten
        # (letztes_signal, *_count), und die gehören nicht in den Prompt.
    return ", ".join(spitzen)


def dislike_kategorien(sklave_profil: dict, schwelle: int = 2) -> list[str]:
    """Kategorien, die der Sklave wiederholt negativ erlebt (≥ schwelle negativ
    UND mehr negativ als positiv)."""
    reaktionen = (sklave_profil or {}).get("kategorie_reaktionen", {}) or {}
    return [
        k for k, v in reaktionen.items()
        if v.get("negativ", 0) >= schwelle and v.get("negativ", 0) > v.get("positiv", 0)
    ]


# ---------------------------------------------------------------------------
# Progressive Steigerung: Intensitäts-Level PRO Kategorie (statt global)
# 1 = niedrig (sanft), 2 = normal, 3 = hoch (intensiv)
# ---------------------------------------------------------------------------
LEVEL_MIN, LEVEL_MAX, LEVEL_DEFAULT = 1, 3, 2
_LEVEL_LABEL = {1: "niedrig", 2: "normal", 3: "hoch"}


def level_label(n: int) -> str:
    return _LEVEL_LABEL.get(int(n), "normal")


def kategorie_level(sklave_profil: dict, kat: str) -> int:
    """Aktuelles Intensitäts-Level einer Kategorie (Default normal)."""
    levels = (sklave_profil or {}).get("kategorie_level", {}) or {}
    return int(levels.get(kat, LEVEL_DEFAULT))


def naechstes_level(aktuell: int, stimmung: str) -> int:
    """Steigert/senkt das Kategorie-Level anhand des Gefühls:
    begeistert/langweilig (= will mehr / zu leicht) -> +1, überfordert -> -1."""
    delta = 0
    if stimmung in ("begeistert", "langweilig"):
        delta = 1
    elif stimmung == "überfordert":
        delta = -1
    return max(LEVEL_MIN, min(LEVEL_MAX, int(aktuell) + delta))


# Staleness-Schwelle: eine Kategorie altert NUR, wenn seit so vielen Tagen kein
# neues Signal kam. Sonst verdampfen seltene, aber echte Vorlieben (z. B. ein
# einzelnes "begeistert"), während nur die häufigste (meist negative) Kategorie
# überlebt – genau der Kollaps, den wir vermeiden wollen.
_DECAY_STALE_DAYS = 28


def _ist_frisch(v: dict, jetzt, stale_days: int) -> bool:
    """True, wenn die Kategorie innerhalb von `stale_days` ein Signal bekam."""
    from datetime import datetime
    ts = (v or {}).get("letztes_signal")
    if not ts:
        return False  # ohne Zeitstempel (Alt-Daten) → darf altern
    try:
        letztes = datetime.fromisoformat(ts)
        return (jetzt - letztes).days < stale_days
    except (ValueError, TypeError):
        return False


def decay_reaktionen(reaktionen: dict, amount: int = 1,
                     stale_days: int = _DECAY_STALE_DAYS, jetzt=None) -> dict:
    """Altert die Reaktions-Zähler – aber staleness-basiert: nur Kategorien OHNE
    frisches Signal (älter als `stale_days`) werden um `amount` gesenkt (Floor 0),
    aktiv gepflegte bleiben unangetastet. Metadaten (z. B. `letztes_signal`)
    bleiben erhalten. Kategorien ohne verbleibendes Signal fallen raus.
    Hält das Lern-Bild aktuell, ohne seltene echte Vorlieben wegzuräumen."""
    from datetime import datetime, timezone
    jetzt = jetzt or datetime.now(timezone.utc)
    neu: dict = {}
    for kat, v in (reaktionen or {}).items():
        v = v or {}
        if _ist_frisch(v, jetzt, stale_days):
            neu[kat] = dict(v)  # aktiv gepflegt → nicht altern
            continue
        meta = {k: val for k, val in v.items() if not isinstance(val, (int, float))}
        gekuerzt = {}
        for k, val in v.items():
            if isinstance(val, (int, float)):
                nv = max(0, int(val) - amount)
                if nv:
                    gekuerzt[k] = nv
        if sum(gekuerzt.get(b, 0) for b in _BUCKETS) > 0:
            gekuerzt.update(meta)  # Zeitstempel & Co. behalten
            neu[kat] = gekuerzt
    return neu


async def decay_profil_reaktionen(user_id: str, amount: int = 1) -> int:
    """Lädt das Profil, altert `kategorie_reaktionen` und speichert. Gibt Anzahl
    noch aktiver Kategorien zurück."""
    from bot.services import qdrant
    prof = await qdrant.get_user_profile(user_id) or {}
    alt = prof.get("kategorie_reaktionen", {}) or {}
    if not alt:
        return 0
    neu = decay_reaktionen(alt, amount)
    await qdrant.patch_profile_fields(user_id, {"kategorie_reaktionen": neu})
    return len(neu)


# ---------------------------------------------------------------------------
# Domina-Präferenz-Signal (GETRENNT von der Sklaven-Reaktion kategorie_reaktionen).
# Speist sich aus dem Tiny-Task-Feedback der Domina (genutzt / gefiel / abgelehnt)
# und landet als `kategorie_praeferenzen` auf dem Domina-Profil.
# ---------------------------------------------------------------------------
_DOMINA_SIGNAL = {
    "genutzt":   ("positiv", 2),  # tatsächlich als Aufgabe übernommen → stark
    "gut":       ("positiv", 1),  # gefiel ihr, heute nur nicht genutzt → mild
    "abgelehnt": ("negativ", 1),
}


async def record_domina_praeferenz(kategorien: list, signal: str) -> None:
    """Verbucht die Reaktion der DOMINA auf die Kategorien eines Vorschlags als
    eigenes Signal – NICHT in kategorie_reaktionen (das bleibt die Reaktion des
    Sklaven). So lassen sich Domina-Vorlieben und Sklaven-Erleben später getrennt
    gewichten. `signal`: 'genutzt' | 'gut' | 'abgelehnt'."""
    from bot.services import qdrant
    from datetime import datetime, timezone
    eintrag = _DOMINA_SIGNAL.get(signal)
    if not eintrag or not kategorien:
        return
    bucket, amount = eintrag
    prof = await qdrant.get_user_profile("domina") or {}
    praef = dict(prof.get("kategorie_praeferenzen", {}) or {})
    jetzt = datetime.now(timezone.utc).isoformat()
    for kat in kategorien:
        e = dict(praef.get(kat) or {"positiv": 0, "negativ": 0})
        e[bucket] = int(e.get(bucket, 0)) + amount
        e["letztes_signal"] = jetzt
        praef[kat] = e
    await qdrant.patch_profile_fields("domina", {"kategorie_praeferenzen": praef})
    logger.info("Domina-Präferenz: %s +%d für %s", bucket, amount, ", ".join(kategorien))


def top_kategorien(sklave_profil: dict, min_signal: int = 2) -> list[str]:
    """Kategorien, die der Sklave klar positiv erlebt (Mehrheit positiv bei
    mindestens min_signal Reaktionen)."""
    reaktionen = (sklave_profil or {}).get("kategorie_reaktionen", {}) or {}
    out = []
    for k, v in reaktionen.items():
        total = v.get("positiv", 0) + v.get("neutral", 0) + v.get("negativ", 0)
        if total >= min_signal and v.get("positiv", 0) > v.get("negativ", 0):
            out.append(k)
    return out


# ---------------------------------------------------------------------------
# Bucket-Auswahl im 60/30/10-Mix
# ---------------------------------------------------------------------------

# Mix-Verhältnis der Ziehung (pro Slot wird erst der Bucket gewürfelt):
#   basis       = bekannte Vorlieben (Wunsch- + Top-Kategorien)
#   exploration = Cluster-Nachbarn der Basis (verwandte Themen)
#   wildcard    = Rest des Pools (Überraschung – Dislikes bleiben ausgeschlossen)
_MIX = {"basis": 60, "exploration": 30, "wildcard": 10}
_MIX_LANGEWEILE = {"basis": 80, "exploration": 15, "wildcard": 5}

# Gewichte INNERHALB eines Buckets (relativ; Summe egal, nur Verhältnis zählt)
_W_BASIS = 10
_W_WUNSCH = 15             # war 40: Wunsch-Kategorien sollen die (jetzt breite) Basis
                           # nicht mehr dominieren, nur leicht vorne liegen (02.07.)
_W_TOP = 30
_W_NEU = 15
_W_LANGEWEILE_WUNSCH = 50   # bei Langeweile zusätzlich stark Richtung Wunsch
_W_RECENT_MALUS = 40        # kürzlich genutzt → dämpfen (exakte Kategorie). 40 statt 25:
                            # eine Wunsch-Kategorie wiegt 50-65 – mit -25 gewann sie trotz
                            # Malus fast täglich (Befund 02.07.: dieselbe Kategorie an 10 von 15 Tagen)
_W_CLUSTER_MALUS = 18       # Thema/Cluster kam zuletzt dran → ganzes Thema dämpfen
_W_DOMINA_PRAEFERENZ = 20   # eigenes Gewicht des Domina-Signals (kategorie_praeferenzen):
                            #   netto positiv → Bonus, netto negativ → Malus


def gewichtete_auswahl(
    sklave_profil: dict,
    letzte_kategorien: list[str] | None = None,
    count: int = 3,
    langeweile: bool = False,
    domina_praeferenzen: dict | None = None,
    mit_cross_info: bool = False,
) -> list[str] | tuple[list[str], str | None]:
    """Wählt `count` Kategorien im 60/30/10-Mix (pro Slot, ohne Zurücklegen):

    - Basis (60 %): bekannte Vorlieben = Wunsch- + Top-Kategorien
    - Exploration (30 %): Cluster-Nachbarn der Basis (verwandte Themen)
    - Wildcard (10 %): Rest des Pools (Überraschung, aber nie Dislikes)

    Innerhalb der Buckets wirken weiter die Lern-Gewichte (nie erlebte bekommen
    einen Bonus, kürzlich genutzte werden gedämpft – exakte Kategorie UND das
    ganze Thema/Cluster, aus dem die letzten Vorschläge kamen). Dislikes sind
    überall hart ausgeschlossen. langeweile=True verschiebt den Mix Richtung
    Basis (80/15/5) und gewichtet Wunsch-Kategorien zusätzlich hoch. Leere
    Buckets fallen weg (Mix wird über die übrigen renormalisiert) – ohne bekannte
    Vorlieben läuft alles über Wildcard, also reine Exploration.

    Cross-Cluster-Garantie: Sind alle Basis-Vorlieben im selben Thema (z. B. rein
    anal), würden Basis (60 %) + deren Cluster-Nachbarn/Exploration (30 %) = 90 %
    im selben Cluster landen. Darum wird – außer bei Langeweile – mindestens ein
    Slot für ein Thema AUSSERHALB der Basis-Cluster reserviert.

    mit_cross_info=True liefert zusätzlich, WELCHE Kategorie der Cross-Cluster-
    Slot ist (oder None) – der Prompt-Builder markiert sie als "frisches Thema,
    bevorzugt heute", damit ABWECHSLUNG und Pflicht-Kategorien nicht gegeneinander
    laufen (Review D7, B3).
    """
    letzte_kategorien = letzte_kategorien or []
    reaktionen = (sklave_profil or {}).get("kategorie_reaktionen", {}) or {}
    domina_praeferenzen = domina_praeferenzen or {}

    wunsch = set(sklave_profil.get("wunsch_kategorien", []) or []) if sklave_profil else set()
    dislikes = set(dislike_kategorien(sklave_profil))
    tops = set(top_kategorien(sklave_profil))
    erlebt = set(reaktionen.keys())
    # count*4 statt count*2: bei 3 Vorschlägen/Tag deckte das alte Fenster nur
    # ~2 Tage ab – eine Kategorie kam am 3. Tag wieder mit vollem Gewicht dran.
    recent = set(letzte_kategorien[: count * 4])
    # Fix 1: nicht nur exakte Kategorien, sondern das ganze Thema der letzten
    # Vorschläge dämpfen – sonst rotiert das System endlos im selben Cluster
    # (Enema→Plug→Prostata…), ohne je eine exakte Kategorie zu wiederholen.
    recent_cluster = _cluster_von(recent)

    pool = alle_kategorien(sklave_profil)
    # Basis = ALLE bekannten Vorlieben: Freitext-Vorlieben auf Kategorien gemappt
    # + Wunsch-Kategorien + gelernte Tops. Vorher nur wunsch|tops – bei leeren
    # Reaktions-Tops war die Basis 3 Kategorien groß und der Basis-Mix nagelte
    # die Vorschläge täglich auf dieselben Wünsche fest (Nutzer-Feedback 02.07.).
    vorlieben_kats: set[str] = set()
    for v in (sklave_profil or {}).get("vorlieben") or []:
        vorlieben_kats |= kategorien_in_text(v)
    # Wunsch-Einträge können veraltet sein → gegen den Pool schneiden
    basis = (wunsch | tops | vorlieben_kats) & set(pool) - dislikes
    exploration = (_nachbarn_von(basis) & set(pool)) - basis - dislikes
    wildcard = {k for k in pool if k not in basis and k not in exploration and k not in dislikes}

    def _gewicht(kat: str, mit_recency: bool = True) -> float:
        w = _W_BASIS
        if kat in wunsch:
            w += _W_WUNSCH
            if langeweile:
                w += _W_LANGEWEILE_WUNSCH
        if kat in tops:
            w += _W_TOP
        if kat not in erlebt:
            w += _W_NEU
        if mit_recency and kat in recent:
            w = max(1.0, w - _W_RECENT_MALUS)
        elif mit_recency and KATEGORIE_ZU_CLUSTER.get(kat, set()) & recent_cluster:
            # Thema kam zuletzt dran (aber andere Kategorie) → milder dämpfen
            w = max(1.0, w - _W_CLUSTER_MALUS)
        # Domina-Signal (getrennt vom Sklaven-Erleben): netto positiv → Bonus,
        # netto negativ → Malus. Eigenes Gewicht, unabhängig von wunsch/tops.
        dp = domina_praeferenzen.get(kat)
        if dp:
            netto = int(dp.get("positiv", 0)) - int(dp.get("negativ", 0))
            if netto > 0:
                w += _W_DOMINA_PRAEFERENZ
            elif netto < 0:
                w = max(1.0, w - _W_DOMINA_PRAEFERENZ)
        return float(w)

    buckets = {
        name: {k: _gewicht(k) for k in kategorien}
        for name, kategorien in (("basis", basis), ("exploration", exploration), ("wildcard", wildcard))
    }
    if not any(buckets.values()):  # alles disliked → Fallback auf gesamten Pool
        fallback = random.sample(pool, min(count, len(pool)))
        return (fallback, None) if mit_cross_info else fallback

    mix = _MIX_LANGEWEILE if langeweile else _MIX
    ausgewaehlt: list[str] = []
    cross_pick: str | None = None

    # Fix 2: Cross-Cluster-Garantie – einen Slot für ein Thema außerhalb der
    # Basis-Cluster reservieren. Nicht bei Langeweile (die will bewusst Wunsch-Nähe)
    # und nur wenn mehr als ein Slot vergeben wird.
    basis_cluster = _cluster_von(basis)
    if not langeweile and basis_cluster and count > 1:
        # "Frisches Thema" heißt: nichts, was er ohnehin ständig bekommt. Auch
        # seine Freitext-Vorlieben zählen als vertraut – sonst wählt der Slot
        # ausgerechnet sein Dauer-Motiv als Überraschung (Befund 02.07.: der
        # Cross-Slot wählte die meistgenannte Freitext-Vorliebe als "Neues").
        vertraut = basis | vorlieben_kats
        tabu_cluster = basis_cluster | _cluster_von(vertraut)
        fremd = {
            k for k in (exploration | wildcard)
            if not (KATEGORIE_ZU_CLUSTER.get(k, set()) & tabu_cluster) and k not in vertraut
        }
        if not fremd:  # Vorlieben decken fast alle Themen ab → mildere Definition
            fremd = {
                k for k in (exploration | wildcard)
                if not (KATEGORIE_ZU_CLUSTER.get(k, set()) & basis_cluster)
            }
        # bevorzugt ein fremdes Thema, das auch nicht zuletzt schon dran war
        fremd_frisch = {k for k in fremd if not (KATEGORIE_ZU_CLUSTER.get(k, set()) & recent_cluster)}
        kandidaten = fremd_frisch or fremd
        if kandidaten:
            gew = {k: _gewicht(k) for k in kandidaten}
            pick = random.choices(list(gew.keys()), weights=list(gew.values()), k=1)[0]
            ausgewaehlt.append(pick)
            cross_pick = pick
            for b in buckets.values():  # aus allen Buckets raus → keine Doppelwahl
                b.pop(pick, None)

    def _frische(name: str) -> float:
        """Verhältnis aktuelles Bucket-Gewicht / Gewicht ohne Recency-Malus (0-1).
        Nichts kürzlich genutzt → 1.0, der dokumentierte 60/30/10-Mix gilt exakt;
        abgegraster Bucket → anteilig weniger Zug-Wahrscheinlichkeit."""
        b = buckets[name]
        if not b:
            return 1.0
        voll = sum(_gewicht(k, mit_recency=False) for k in b)
        return (sum(b.values()) / voll) if voll else 1.0

    while len(ausgewaehlt) < count and any(buckets.values()):
        offen = [name for name, b in buckets.items() if b]
        # Bucket-Wahl mit Mix × Frische-Faktor statt Mix allein: mit fixen 60 %
        # kam die Basis täglich dran, egal wie stark der Recent-Malus sie
        # innerhalb des Buckets gedämpft hatte (Befund 02.07.) – bei nur 3
        # Wunsch-Kategorien landeten so zwangsläufig jeden Tag dieselben im
        # Vorschlag. So verliert ein frisch abgegraster Bucket auch an
        # Zug-Wahrscheinlichkeit (v.a. Richtung thematisch verwandter
        # Exploration) und erholt sich, sobald seine Kategorien aus dem
        # Recent-Fenster fallen.
        bucket = buckets[random.choices(
            offen, weights=[mix[o] * _frische(o) for o in offen], k=1,
        )[0]]
        kat = random.choices(list(bucket.keys()), weights=list(bucket.values()), k=1)[0]
        ausgewaehlt.append(kat)
        del bucket[kat]
    return (ausgewaehlt, cross_pick) if mit_cross_info else ausgewaehlt
