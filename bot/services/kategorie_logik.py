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
import re
import random

from bot import config
from bot.services import grok, kataloge, modi

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

# Woran man eine Kategorie im Text ERKENNT – für den Beleg-Prüfer
# (kategorien_im_text_belegt). Der bekam bisher nur die Namen und urteilte bei
# Grenzfällen mal so, mal so (Live-Befund 17.11.2026: ein Vorschlag, der seine
# Kategorie nur über Kleidung und Auftreten umsetzte, blieb ohne Kategorie). Rollen-neutral
# formuliert; Kategorien ohne Eintrag (eigene, andere Modi) gehen nur mit Namen
# in die Prüfung.
_MERKMALE: dict[str, str] = {
    "Anal":                 "anale Berührung oder Penetration der Sub-Seite (Finger, Zunge, Spielzeug)",
    "Schlucken":            "die Sub-Seite schluckt etwas auf Anweisung",
    "Psycho":               "Kopfspiel ohne Körperpraktik: nachdenken, aufschreiben, bekennen, mentale Aufgabe",
    "Analdehnung":          "der Anus der Sub-Seite wird schrittweise geweitet (größere Plugs oder Dildos)",
    "Analeingangstraining": "Training nur am Schließmuskel: an- und entspannen, kurze Reize am Eingang",
    "Buttplug_Tragen":      "die Sub-Seite trägt über eine Zeit einen Plug",
    "Dildo_Training":       "die Sub-Seite übt mit einem Dildo (aufnehmen, reiten, halten)",
    "Pegging":              "die Dom-Seite penetriert die Sub-Seite anal mit dem Strapon",
    "Strap_on":             "ein Strapon ist im Einsatz (tragen, lutschen lassen, penetrieren)",
    "Blowjob_Training":     "die Sub-Seite übt Oralsex an Penis, Strapon oder Dildo",
    "Deepthroat_Training":  "tiefes Aufnehmen in den Rachen wird geübt",
    "Gesichtsfick":         "Mund oder Rachen der Sub-Seite wird aktiv gestoßen",
    "Enema_Play":           "Einlauf oder Klistier",
    "Creampie_Cleanup":     "die Sub-Seite leckt Sperma von oder aus der Dom-Seite auf",
    "Fisting":              "mehrere Finger bis zur ganzen Hand werden eingeführt",
    "Prostatamassage":      "die Prostata der Sub-Seite wird massiert oder gemolken",
    "Spanking":             "Schläge auf den Hintern",
    "Sperma_Schlucken":     "die Sub-Seite nimmt Sperma in den Mund und schluckt es",
    "Impact":               "Schlagspiel am Körper (Hand, Gerte, Flogger, Paddle)",
    "Peitsche":             "Peitsche oder Flogger wird eingesetzt",
    "Klassische_Fesselspiele": "die Sub-Seite wird gefesselt oder fixiert (Seil, Manschetten)",
    "Paddle_Training":      "Schläge mit dem Paddle",
    "Piss_Play":            "Urin im Spiel (anpinkeln, trinken, einnässen)",
    "Toiletten_Sklave":     "die Sub-Seite dient als Toilette oder säubert nach dem Toilettengang",
    "Arschanbetung":        "die Sub-Seite verehrt, küsst oder leckt den Hintern der Dom-Seite",
    "Muschianbetung":       "die Sub-Seite verehrt, küsst oder leckt die Vulva der Dom-Seite",
    "Orgasmusverweigerung": "die Sub-Seite darf nicht kommen (Edging, Keuschheit, Abbruch vor dem Höhepunkt)",
    "Ruiniertes_Orgasmen":  "der Höhepunkt der Sub-Seite wird absichtlich verdorben",
    "Sissy_Training":       "die Sub-Seite wird als Mädchen hergerichtet oder darin geübt: Damenwäsche, "
                            "Schminke, weibliche Stimme, Gang, Haltung, Benehmen",
    "Feminisierung":        "weibliche Kleidung, Make-up, weiblicher Name oder weibliche Rolle für die Sub-Seite",
    "Facesitting":          "die Dom-Seite sitzt auf dem Gesicht der Sub-Seite",
    "Smothering":           "der Sub-Seite wird mit dem Körper der Dom-Seite kurz die Luft genommen",
    "Schmerz":              "gezielter Schmerz ohne Schläge: Klemmen, Wachs, Kneifen, Kratzen",
    "Speichelspiel":        "Speichel im Spiel: anspucken, in den Mund spucken, sabbern lassen",
    "Pet_Play":             "die Sub-Seite SPIELT ein Tier: Halsband und Leine, auf allen Vieren, Napf, "
                            "Schwanz-Plug (ein Tiername als Kosewort genügt nicht)",
    "Demütigung":           "die Sub-Seite wird bloßgestellt oder klein gemacht (peinliche Aufgabe, Vorführen)",
    "Verbale_Demütigung":   "Beschimpfen, Auslachen, herabsetzende Worte",
    "Erniedrigung":         "erniedrigende Handlungen oder Stellungen (kriechen, betteln, zu Füßen liegen)",
    "Objektifizierung":     "die Sub-Seite wird als Gegenstand benutzt (Möbel, Ablage, Halter)",
    "Dienst":               "die Sub-Seite bedient oder arbeitet für die Dom-Seite (Haushalt, Servieren, "
                            "Massage, Besorgungen)",
    "Bestrafung":           "eine Strafe für ein Vergehen",
}


def merkmal(kategorie: str) -> str:
    """Erkennungs-Merkmal einer Kategorie für den Beleg-Prüfer ('' = keins hinterlegt)."""
    return _MERKMALE.get(kategorie, "")


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
    nb = nachbarn()
    for kat in kategorien:
        out.update(nb.get(kat, set()))
    return out - set(kategorien)


def _cluster_von(kategorien) -> set[int]:
    """Cluster-Indizes, zu denen die gegebenen Kategorien thematisch gehören."""
    out: set[int] = set()
    zc = zu_cluster()
    for kat in kategorien:
        out |= zc.get(kat, set())
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


# ---------------------------------------------------------------------------
# Modus-Map (Umbau "nicht nur BDSM", 05.11.2026): je Paar-Modus (modi.py) ein
# Satz Katalog/Keywords/Cluster/Voraussetzungen. BDSM = die Tabellen oben
# (Bestandsverhalten, auch weiterhin als Modul-Attribute erreichbar); Kink und
# Vanilla kommen aus services/kataloge.py. Alle Leser gehen über die Accessor-
# Funktionen, die den Modus des Kontext-Paares auflösen.
# ---------------------------------------------------------------------------

def _zu_cluster_aus(cluster: list[list[str]]) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for i, c in enumerate(cluster):
        for kat in c:
            out.setdefault(kat, set()).add(i)
    return out


def _nachbarn_aus(cluster: list[list[str]]) -> dict[str, set[str]]:
    nb: dict[str, set[str]] = {}
    for c in cluster:
        for kat in c:
            nb.setdefault(kat, set()).update(x for x in c if x != kat)
    return nb


_MODUS_TABELLEN: dict[str, dict] = {
    modi.BDSM: {
        "katalog": lambda: config.AUFGABEN_KATEGORIEN,  # lazy: Tests patchen config
        "keywords": _KEYWORDS,
        "nachbarn": KATEGORIE_NACHBARN,
        "zu_cluster": KATEGORIE_ZU_CLUSTER,
        "voraussetzung": _KATEGORIE_VORAUSSETZUNG,
    },
    modi.KINK: {
        "katalog": lambda: kataloge.KINK_KATEGORIEN,
        "keywords": kataloge.KINK_KEYWORDS,
        "nachbarn": _nachbarn_aus(kataloge.KINK_CLUSTER),
        "zu_cluster": _zu_cluster_aus(kataloge.KINK_CLUSTER),
        "voraussetzung": kataloge.KINK_VORAUSSETZUNG,
    },
    modi.VANILLA: {
        "katalog": lambda: kataloge.VANILLA_KATEGORIEN,
        "keywords": kataloge.VANILLA_KEYWORDS,
        "nachbarn": _nachbarn_aus(kataloge.VANILLA_CLUSTER),
        "zu_cluster": _zu_cluster_aus(kataloge.VANILLA_CLUSTER),
        "voraussetzung": kataloge.VANILLA_VORAUSSETZUNG,
    },
}


def _tabellen() -> dict:
    return _MODUS_TABELLEN[modi.aktuell()]


def keywords() -> dict[str, list[str]]:
    """Keyword-Tabelle des aktuellen Modus (BDSM = _KEYWORDS)."""
    return _tabellen()["keywords"]


def nachbarn() -> dict[str, set[str]]:
    """Nachbar-Map des aktuellen Modus (BDSM = KATEGORIE_NACHBARN)."""
    return _tabellen()["nachbarn"]


def zu_cluster() -> dict[str, set[int]]:
    """Kategorie→Cluster-Map des aktuellen Modus (BDSM = KATEGORIE_ZU_CLUSTER)."""
    return _tabellen()["zu_cluster"]


def _konstellation_erlaubt(kategorie: str) -> bool:
    check = _tabellen()["voraussetzung"].get(kategorie)
    if check is None:
        return True
    from bot.prompts import rollen
    return check(rollen.dom_geschlecht(), rollen.sub_geschlecht())


def katalog_kategorien() -> list[str]:
    """Fester Katalog DES MODUS, gefiltert auf die konfigurierte Rollen-
    Konstellation (BDSM-F/M-Default = kompletter config.AUFGABEN_KATEGORIEN).
    Basis für alle_kategorien und das vielfalt_alle-Abzeichen (unerreichbare
    Kategorien zählen nicht als Ziel)."""
    return [k for k in _tabellen()["katalog"]() if _konstellation_erlaubt(k)]


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

# Alltagswörter, die ein Keyword nur als Teilwort enthalten und sonst falsch
# einsortieren (Live-Befund 12.11.2026: ein Wochentag im Titel einer Aufgabe
# genügte für die Kategorie Dienst). Werden vor dem Matching aus dem Text
# genommen; das Matching selbst bleibt ein Teilwort-Vergleich, weil deutsche
# Vorsilben-Formen (ausgepeitscht, gefesselt) sonst nicht mehr träfen.
_FALSCHE_FREUNDE = re.compile(
    r"\bdienstags?\b|\bregelm(?:ä|ae)(?:ss|ß)ig\w*|\bin der regel\b|\berwart\w*|\babwart\w*",
    re.IGNORECASE,
)


def _match_text(text: str) -> str:
    return _FALSCHE_FREUNDE.sub(" ", (text or "").lower())


def keyword_match(text: str) -> str:
    """Reines Keyword-Matching. Gibt 'allgemein' zurück, wenn nichts passt."""
    text_lower = _match_text(text)
    for kat, kws in keywords().items():
        if any(kw in text_lower for kw in kws):
            return kat
    return "allgemein"


def kategorien_in_text(text: str) -> set[str]:
    """ALLE Kategorien, deren Keywords im Text vorkommen (keyword_match liefert
    nur den ersten Treffer – für Ausschluss-Logik braucht es alle, z.B. beim
    Mapping von Freitext-Vorlieben auf Kategorien)."""
    text_lower = _match_text(text)
    return {kat for kat, kws in keywords().items()
            if any(kw in text_lower for kw in kws)}


# Kürzel und Einzelwörter, wie sie in Freitext-VORLIEBEN stehen. Bewusst NICHT
# in _KEYWORDS: dort ist das Matching ein reines `in` über beliebige Texte –
# zweibuchstabige Kürzel oder ein nacktes „lecken" würden Aufgaben reihenweise
# falsch einsortieren. Hier mit Wortgrenzen und nur für Vorlieben-Zeilen.
# (Daten-Review 16.11.2026: mehrere Vorlieben trafen kein Stichwort und fehlten
# damit in der Basis der Kategorie-Auswahl.)
_VORLIEBEN_ALIASE: list[tuple[re.Pattern, tuple[str, ...]]] = [
    (re.compile(r"\b(ns|natursekt|golden shower)\b", re.I), ("Piss_Play",)),
    (re.compile(r"\b(kv|kaviar|scat)\b", re.I), ("Toiletten_Sklave",)),
    (re.compile(r"\b(milking|melken|gemolken|abmelken)\b", re.I), ("Prostatamassage",)),
    (re.compile(r"\b(lecken|oral verwöhnen)\b", re.I), ("Arschanbetung", "Muschianbetung")),
]
# „Arsch/Hintern DER Dom-Seite" meint nicht die Anal-Kategorie (die beschreibt,
# was an der Sub-Seite geschieht), sondern Anbetung/Dienst an ihr.
_DOM_HINTERN_RE = re.compile(
    r"(arsch|hintern|po)\s+(der|des|meiner|meines|von der|vom)\s+(domina|herrin|dom|herrn|meister\w*)", re.I)


def vorliebe_kategorien(text: str) -> set[str]:
    """Kategorien einer Freitext-VORLIEBE: Stichwort-Treffer plus Kürzel-Aliase;
    nennt die Zeile den Hintern der Dom-Seite, wird der breite „Anal"-Treffer
    durch „Arschanbetung" ersetzt."""
    kats = set(kategorien_in_text(text))
    for rx, ziele in _VORLIEBEN_ALIASE:
        if rx.search(text or ""):
            kats |= set(ziele)
    if "Anal" in kats and _DOM_HINTERN_RE.search(text or ""):
        kats.discard("Anal")
        kats.add("Arschanbetung")
    pool = set(keywords())
    return {k for k in kats if k in pool}


# Folge-Klauseln einer Aufgabe: was auf diese Marker folgt, beschreibt die
# angedrohte Strafe, die Belohnung oder eine Bedingung – nicht die Aufgabe.
# Die Einsortierung richtete sich vorher nach dem ERSTEN Stichwort im ganzen
# Text; stand das in der Folge-Klausel, landete die Aufgabe in der Kategorie
# der Strafe statt in der ihrer Haupt-Handlung (Daten-Review 16.11.2026) – und
# an der Kategorie hängen Reaktionen, gelernte Intensität, Abneigungen und die
# Rotation.
_FOLGE_RE = re.compile(
    r"(?:\s*[,;:.–—\-(]\s*|\s+)(?:"
    r"bei (?:unzufriedenheit|missfallen|missachtung|verst[oö](?:ß|ss)\w*|versagen|verlust|genuss|"
    r"wiederholung|widerworten|widerstand|ungehorsam|patzern?|fehlern?)\b"
    r"|verst[öo](?:ß|ss)e?\s*=|patzer\s*=|als belohnung\b|zur belohnung\b|belohnung(?: ist\b| gibt\b| dafür\b|:)"
    r"|zur strafe\b|als strafe\b|sonst (?:gibt|setzt|folg)\w*|andernfalls\b|gewinnst du\b|verlierst du\b"
    r"|wenn du das gut machst\b"
    # Zähl-Strafen als Beiwerk: „… mit 5 Hieben pro Laut", „10 Schläge für jeden Fehler"
    r"|(?:mit\s+)?\d+\s+\w*(?:hieb\w*|schl[aä]g\w*)\s+(?:pro|je|für jede[nsmr]?)\b"
    r"|if (?:you|he|she) (?:fail|disobey|complain)\w*\b|as (?:a )?(?:reward|punishment)\b|otherwise\b"
    r").*", re.I | re.S)

# Verben, die den Hintern/Schoß der Dom-Seite zum Ziel haben (Anbetung, nicht Anal)
_DOM_KOERPER_RE = re.compile(
    r"(?:leck\w*|küss\w*|kuess\w*|anbet\w*|bete\w*|verwöhn\w*)\b.{0,40}\b(?:meine[nmr]?|deine[nmr]?|ihre[nmr]?)\s+(?:arsch|hintern|po)\b"
    r"|\b(?:meine[nmr]?|deine[nmr]?|ihre[nmr]?)\s+(?:arsch|hintern|po)\b.{0,40}(?:leck|küss|kuess|anbet|bete)", re.I | re.S)


def haupt_handlung(text: str) -> str:
    """Aufgabentext ohne Folge-Klausel (Strafe/Belohnung/Bedingung). Bleibt
    danach zu wenig übrig, gilt der ganze Text."""
    kern = _FOLGE_RE.sub("", text or "").strip(" ,;:–—-(")
    return kern if len(kern) >= 12 else (text or "")


def _stichwort_kategorie(kern: str) -> str:
    """Rückfall ohne LLM: erstes Stichwort der Haupt-Handlung (Tabellen-
    Reihenfolge); der breite „Anal"-Treffer weicht, wenn der Text den Körper
    der Dom-Seite zum Ziel hat."""
    treffer = kategorien_in_text(kern)
    if "Anal" in treffer and (_DOM_HINTERN_RE.search(kern) or _DOM_KOERPER_RE.search(kern)):
        treffer.discard("Anal")
        if "Arschanbetung" in keywords():
            treffer.add("Arschanbetung")
    for kat in keywords():
        if kat in treffer:
            return kat
    return "allgemein"


async def klassifiziere(text: str) -> str:
    """Klassifiziert eine Aufgabe in eine Pool-Kategorie – nach ihrer
    HAUPT-Handlung.

    Das LLM entscheidet (temperature 0) über den Text ohne Folge-Klausel; die
    Stichwort-Treffer gehen nur als Hinweis mit. Das Ergebnis wird hart gegen
    den Pool (Katalog + eigene Kategorien) validiert – niemals eine Phantom-
    Kategorie. Fällt das LLM aus oder antwortet unbrauchbar, entscheidet das
    erste Stichwort der Haupt-Handlung.
    """
    if not text or not text.strip():
        return "allgemein"
    kern = haupt_handlung(text)
    pool = await alle_kategorien_async()
    im_text = kategorien_in_text(kern)
    hinweis = [k for k in keywords() if k in im_text]
    regeln = [
        "- Angedrohte Strafen, Belohnungen, Bedingungen und Wetteinsätze zählen NICHT – sie beschreiben "
        "nur die Folge, nicht die Aufgabe. Auch Rahmen-Elemente (sich bedanken, knien, nackt sein, zählen) "
        "bestimmen die Kategorie nicht, wenn daneben eine konkrete Praktik steht.",
    ]
    if "Anal" in pool and "Arschanbetung" in pool:
        regeln.append(
            "- Was am Hintern der Sub-Seite geschieht (fingern, dehnen, Plug, Strapon), gehört in die "
            "Anal-Kategorien. Geht es um den Körper der Dom-Seite (ihren Hintern oder Schoß lecken, küssen, "
            "anbeten, unter ihr liegen), ist das Arschanbetung, Muschianbetung oder Facesitting – nie \"Anal\".")
    if "Dienst" in pool:
        regeln.append(
            "- Dienst nur, wenn Bedienen, Verwöhnen, Massieren, Haushalt oder das Einhalten von "
            "Regeln/Anreden selbst die Haupt-Handlung ist.")
    kategorien_liste = "\n".join(f"- {k}" for k in pool)
    system = (
        f"Ordne die folgende {modi.thema()}-Aufgabe GENAU EINER Kategorie aus der Liste zu – nach ihrer "
        "HAUPT-Handlung: dem, was die Sub-Seite tatsächlich tut oder was mit ihr getan wird.\n"
        + "\n".join(regeln) + "\n"
        "Antworte NUR mit dem exakten Kategorie-Namen aus der Liste (inkl. Unterstriche), "
        "sonst mit dem Wort allgemein. Keine Erklärung, kein Markdown.\n"
        "Die Kategorie-Namen sind feste Daten-IDs – exakt so zurückgeben, auch wenn "
        "die Aufgabe in einer anderen Sprache formuliert ist.\n\n"
        f"Kategorien:\n{kategorien_liste}"
    )
    user = f"Aufgabe: {kern[:600]}"
    if hinweis:
        user += f"\n\nStichwort-Treffer im Text (nur Hinweis): {', '.join(hinweis)}"
    try:
        # temperature=0: Klassifikation soll deterministisch sein (gleiche Aufgabe -> gleiche Kategorie).
        raw = (await grok.simple(user, system=system, temperature=0)).strip().strip(".").strip()
        if raw in pool:
            return raw
        # case-/whitespace-toleranter Match
        norm = raw.lower().replace(" ", "_")
        for k in pool:
            if k.lower() == norm:
                return k
        if norm != "allgemein":
            logger.warning("Kategorie-Klassifikation: Antwort nicht im Pool – Stichwort-Rückfall.")
    except Exception:
        logger.exception("Grok-Kategorie-Klassifikation fehlgeschlagen – Stichwort-Rückfall")
    return _stichwort_kategorie(kern)


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


# Kategorien, deren Praktik ohne die Dom-Seite nicht stattfinden kann (sie
# schlägt, benutzt, fesselt, gibt oder ist selbst das Ziel). Für Generatoren,
# deren Aufgabe die Sub-Seite ALLEIN erledigt – Blitzaufgabe, Adventskalender.
# Eigene (frei angelegte) Kategorien sind unbekannt und bleiben erlaubt; der
# Prompt verlangt dort zusätzlich „allein machbar".
NUR_ZU_ZWEIT = frozenset({
    "Strap_on", "Pegging", "Muschianbetung", "Arschanbetung", "Facesitting", "Smothering", "Gesichtsfick",
    "Spanking", "Impact", "Peitsche", "Paddle_Training", "Fisting", "Toiletten_Sklave", "Piss_Play",
    "Enema_Play", "Creampie_Cleanup", "Speichelspiel", "Klassische_Fesselspiele", "Objektifizierung",
})


def allein_machbar(kategorien: list[str]) -> list[str]:
    """Die Kategorien ohne die, die die Dom-Seite brauchen (Reihenfolge bleibt)."""
    return [k for k in (kategorien or []) if k not in NUR_ZU_ZWEIT]


# „Selten statt nie" (Owner-Entscheidung 16.11.2026): eine Kategorie, die die
# Sub-Seite wenig mag, die Dom-Seite aber selbst gern vergibt, war für
# Vorschläge an die Dom-Seite komplett gesperrt – der Bot schlug nie vor, was
# sie am häufigsten tut. Kategorien im Profil-Feld `selten_kategorien` bleiben
# eine Abneigung, dürfen in Vorschlägen an die Dom-Seite aber gelegentlich
# vorkommen (stark gedämpftes Auswahl-Gewicht, im Prompt als „selten" markiert).
# Alle Pfade, die direkt an die Sub-Seite gehen oder eine Pflicht-Kategorie
# würfeln, nutzen weiter dislike_kategorien und sperren sie ganz.
FELD_SELTEN = "selten_kategorien"
_W_SELTEN_FAKTOR = 0.5


def selten_kategorien(sklave_profil: dict | None) -> list[str]:
    """Abgelehnte Kategorien, die laut Profil trotzdem selten vorkommen dürfen."""
    profil = sklave_profil or {}
    erlaubt = [k for k in (profil.get(FELD_SELTEN) or []) if isinstance(k, str)]
    if not erlaubt:
        return []
    dislikes = set(dislike_kategorien(profil))
    return [k for k in erlaubt if k in dislikes]


def gedaempfte_kategorien(sklave_profil: dict | None) -> list[str]:
    """ALLE Kategorien aus `selten_kategorien` – auch solche, die keine Abneigung
    der Sub-Seite sind (z. B. ein Themenfeld, das die Dom-Seite nicht reizt und
    das die Auswahl trotzdem häufiger zog als die eigentlichen Vorlieben). Sie
    bleiben wählbar, bekommen aber das gedämpfte Auswahl-Gewicht. Der Prompt-
    Hinweis „Selten" gilt nur für die Abneigungen darunter (selten_kategorien)."""
    return [k for k in ((sklave_profil or {}).get(FELD_SELTEN) or []) if isinstance(k, str)]


def gesperrte_kategorien(sklave_profil: dict | None) -> list[str]:
    """dislike_kategorien ohne die „selten"-Ausnahmen – für Vorschläge an die Dom-Seite."""
    selten = set(selten_kategorien(sklave_profil))
    return [k for k in dislike_kategorien(sklave_profil or {}) if k not in selten]


def waehlbare_kategorien(sklave_profil: dict | None = None) -> list[str]:
    """alle_kategorien OHNE die gesperrten – für Prompts, die dem Modell eine
    Auswahl-Liste geben. Vorher stand eine gesperrte Kategorie als wählbar in
    der Liste und ein paar Zeilen später unter NIEMALS (Review D10)."""
    gesperrt = set(gesperrte_kategorien(sklave_profil or {}))
    return [k for k in alle_kategorien(sklave_profil) if k not in gesperrt]


def dislike_kategorien(sklave_profil: dict, schwelle: int = 2) -> list[str]:
    """Kategorien, die der Sklave wiederholt negativ erlebt (≥ schwelle negativ
    UND mehr negativ als positiv) – plus gelernte `mag_nicht_<Kategorie>`-Tags.

    Die Tags sind das Langzeitgedächtnis: die Zähler in `kategorie_reaktionen`
    altern nach 28 Tagen ohne Signal weg (decay_reaktionen), der Tag bleibt. Ohne
    ihn war eine gelernte Abneigung nach ein paar ruhigen Wochen wieder frei
    wählbar (Live-Befund 12.11.2026).
    Ein Tag zählt nicht, wenn die aktuellen Zähler netto positiv sind oder die
    Kategorie ausdrücklich auf der Wunschliste steht."""
    profil = sklave_profil or {}
    reaktionen = profil.get("kategorie_reaktionen", {}) or {}
    aus = [
        k for k, v in reaktionen.items()
        if v.get("negativ", 0) >= schwelle and v.get("negativ", 0) > v.get("positiv", 0)
    ]
    wunsch = set(profil.get("wunsch_kategorien", []) or [])
    for tag in profil.get("persoenlichkeit_tags", []) or []:
        if not isinstance(tag, str) or not tag.startswith("mag_nicht_"):
            continue
        k = tag[len("mag_nicht_"):]
        v = reaktionen.get(k) or {}
        if (k and k not in aus and k not in wunsch
                and v.get("positiv", 0) <= v.get("negativ", 0)):
            aus.append(k)
    return aus


# Verfall gelernter Abneigungs-Tags. Seit die Tags als Dislike zählen, ist die
# Kategorie hart ausgeschlossen – es kommen also keine neuen Reaktionen mehr,
# die den Tag wieder lösen könnten. Darum bekommt jeder mag_nicht_-Tag einen
# Zeitstempel (Profil-Feld `tag_seit`) und fällt nach DISLIKE_TAG_TAGE ohne
# neues negatives Signal weg: die Kategorie darf dann wieder vorkommen, zwei
# frische negative Reaktionen sperren sie sofort erneut (Zähler-Dislike).
DISLIKE_TAG_TAGE = 180
FELD_TAG_SEIT = "tag_seit"


def tags_pflegen(tags: list, tag_seit: dict, jetzt=None,
                 tage: int = DISLIKE_TAG_TAGE) -> tuple[list, dict, list]:
    """Reine Pflege-Logik: stempelt ungestempelte mag_nicht_-Tags mit `jetzt`,
    entfernt abgelaufene Tags und räumt Stempel verschwundener Tags weg.
    Rückgabe: (tags_neu, tag_seit_neu, verfallen)."""
    from datetime import datetime, timezone
    jetzt = jetzt or datetime.now(timezone.utc)
    seit = dict(tag_seit or {})
    bleiben, verfallen = [], []
    for tag in tags or []:
        if not isinstance(tag, str) or not tag.startswith("mag_nicht_"):
            bleiben.append(tag)
            continue
        try:
            ts = datetime.fromisoformat(seit.get(tag) or "")
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            ts = None
        if ts is None:
            seit[tag] = jetzt.isoformat()   # Alt-Bestand: Frist läuft ab jetzt
            bleiben.append(tag)
        elif (jetzt - ts).days >= tage:
            verfallen.append(tag)
            seit.pop(tag, None)
        else:
            bleiben.append(tag)
    seit = {k: v for k, v in seit.items() if k in bleiben}
    return bleiben, seit, verfallen


async def pflege_dislike_tags(user_id: str = "sklave") -> list[str]:
    """Wöchentlich (Wartungs-Job): Tags stempeln/verfallen lassen. Gibt die
    verfallenen Tags zurück."""
    from bot.services import qdrant
    prof = await qdrant.get_user_profile(user_id) or {}
    alt_tags = list(prof.get("persoenlichkeit_tags", []) or [])
    alt_seit = dict(prof.get(FELD_TAG_SEIT, {}) or {})
    tags, seit, verfallen = tags_pflegen(alt_tags, alt_seit)
    if tags != alt_tags or seit != alt_seit:
        await qdrant.patch_profile_fields(
            user_id, {"persoenlichkeit_tags": tags, FELD_TAG_SEIT: seit})
    return verfallen


# Marker für den Dislike-Detektor auf FERTIGEN Vorschlagstexten. Die allgemeine
# Keyword-Tabelle taugt dafür nicht überall: sie ist fürs Einsortieren gebaut und
# bei manchen Kategorien bewusst breit ("gehorsam", "erlaubnis", "warten" unter
# Dienst träfen fast jeden Vorschlag). Hier stehen je Kategorie nur eindeutige
# Regex-Marker; Kategorien ohne Eintrag fallen auf die Keyword-Tabelle zurück.
_DISLIKE_MARKER: dict[str, list[str]] = {
    "Dienst": [
        r"\bdienst(?!ag)", r"\bdiener", r"\bbedien", r"\bservier", r"\bbutler",
        r"\bkellner", r"\bzofe", r"\bputz(?:t|en|e|st)\b", r"\bkoch(?:t|en|st)?\b", r"\baufr(?:ä|ae)um",
        r"\bhaushalt", r"\bstaubsaug", r"\babwasch", r"\bsp(?:ü|ue)lmaschine",
        # bewusst OHNE "massage/massier" (Massager = Spielzeug) und ohne bloßes
        # "putz" (bildhafte Komposita) – beides gab Fehlalarme auf echten Vorschlägen.
        # "holt dir einen Kaffee" / "bringt ihr ein Getränk"
        r"\b(?:bring\w*|hol\w*)\b[^.!?\n]{0,40}\b(?:trinken|getr(?:ä|ae)nk\w*|kaffee|tee|drink)\b",
    ],
}


def dislike_treffer(text: str, dislikes) -> list[str]:
    """Welche der abgelehnten Kategorien bedient der fertige Vorschlagstext?
    Wortanfangs-Matching (kein Mitten-im-Wort-Treffer). Reihenfolge wie `dislikes`."""
    t = (text or "").lower()
    if not t or not dislikes:
        return []
    kw_tabelle = keywords()
    treffer = []
    for kat in dislikes:
        muster = _DISLIKE_MARKER.get(kat)
        if muster is None:
            muster = [r"\b" + re.escape(kw.lower()) for kw in kw_tabelle.get(kat, [])]
        if any(re.search(m, t) for m in muster):
            treffer.append(kat)
    return treffer


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


# Alterung beim LESEN (nicht destruktiv): ein Domina-Signal gilt
# DOMINA_SIGNAL_FRISCH_TAGE lang voll, danach verliert es je weiterem
# angefangenem Zeitraum einen Punkt Richtung 0. Live-Befund 12.11.2026: ein
# einzelnes, Monate altes "gefiel"-Signal stand ungewichtet als "zuletzt gern
# übernommen" im Prompt und gab der Kategorie den vollen Auswahl-Bonus.
DOMINA_SIGNAL_FRISCH_TAGE = 60
_DOMINA_SIGNAL_VOLL = 3   # ab diesem Netto wirkt der volle Auswahl-Bonus/-Malus


def domina_netto_effektiv(v: dict, jetzt=None) -> int:
    """Netto-Signal (positiv − negativ) einer Kategorie nach Alterung. Ohne
    Zeitstempel (Alt-Daten, Tests) bleibt das Netto unverändert."""
    from datetime import datetime, timezone
    v = v or {}
    netto = int(v.get("positiv", 0)) - int(v.get("negativ", 0))
    ts = v.get("letztes_signal")
    if not netto or not ts:
        return netto
    try:
        letztes = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return netto
    if letztes.tzinfo is None:
        letztes = letztes.replace(tzinfo=timezone.utc)
    tage = ((jetzt or datetime.now(timezone.utc)) - letztes).days
    abzug = max(0, tage // DOMINA_SIGNAL_FRISCH_TAGE)
    if netto > 0:
        return max(0, netto - abzug)
    return min(0, netto + abzug)


# Themen-Pause: Lehnt die Dom-Seite einen Vorschlag mit „Thema reizt mich
# nicht" ab, ist das mehr als ein Minuspunkt. Bisher zählte es wie jede
# Ablehnung (−1) und hob bestenfalls ein älteres Plus auf – fünf Tage später
# galt dieselbe Kategorie wieder als „frisches Thema, heute bevorzugen"
# (Live-Befund 17.11.2026). Deshalb ruht eine so abgelehnte Kategorie für
# THEMA_PAUSE_TAGE in der Auswahl; ein späteres positives Signal hebt die
# Pause wieder auf.
THEMA_PAUSE_TAGE = 28
FELD_PAUSE_BIS = "pause_bis"
# Dasselbe Thema unter zwei Namen: Die Pause der einen Kategorie gilt auch der
# anderen – sonst kommt das abgelehnte Thema am nächsten Tag unter dem
# Schwester-Etikett wieder (in der Probe wählte die Auswahl genau das als
# „frisches Thema"). Bewusst nur echte Doppel, keine ganzen Cluster.
_GLEICHES_THEMA: tuple[frozenset, ...] = (
    frozenset({"Sissy_Training", "Feminisierung"}),
    frozenset({"Demütigung", "Erniedrigung"}),
    frozenset({"Pegging", "Strap_on"}),
)


def _mit_gleichem_thema(kategorie: str) -> list[str]:
    """Die Kategorie selbst und ihre Doppel (gleiches Thema), Kategorie zuerst."""
    doppel = sorted(k for gruppe in _GLEICHES_THEMA if kategorie in gruppe for k in gruppe if k != kategorie)
    return [kategorie] + doppel


def thema_pausiert(praeferenzen: dict | None, jetzt=None) -> list[str]:
    """Kategorien, deren Themen-Pause noch läuft (sortiert)."""
    from datetime import datetime, timezone
    jetzt = jetzt or datetime.now(timezone.utc)
    aus = []
    for kat, v in (praeferenzen or {}).items():
        bis = (v or {}).get(FELD_PAUSE_BIS) if isinstance(v, dict) else None
        if not bis:
            continue
        try:
            ende = datetime.fromisoformat(str(bis))
        except (ValueError, TypeError):
            continue
        if ende.tzinfo is None:
            ende = ende.replace(tzinfo=timezone.utc)
        if ende > jetzt:
            aus.append(kat)
    return sorted(aus)


def domina_praeferenz_rangliste(praeferenzen: dict, jetzt=None) -> tuple[list[str], list[str]]:
    """(gern, ungern) – nur Kategorien mit verbleibendem Signal, stärkstes zuerst."""
    eff = {k: domina_netto_effektiv(v, jetzt) for k, v in (praeferenzen or {}).items()}
    gern = sorted((k for k, n in eff.items() if n > 0), key=lambda k: (-eff[k], k))
    ungern = sorted((k for k, n in eff.items() if n < 0), key=lambda k: (eff[k], k))
    return gern, ungern


def _anrede_neutral(text: str) -> str:
    """Ersetzt den Kosenamen der Sub-Seite durch eine neutrale Bezeichnung –
    samt gebeugter Formen. Ein bildhafter Kosename (Tier, Gegenstand …) ließ den
    Beleg-Prüfer sonst die dazu passende Kategorie als „im Text umgesetzt"
    werten, obwohl nur der Name fiel (Live-Befund 16.11.2026)."""
    try:
        from bot.services import persona_config  # lazy: zirkelfrei
        anrede = (persona_config.sklave_anrede() or "").strip()
    except Exception:
        anrede = ""
    woerter = [w for w in anrede.split() if w]
    if not woerter:
        return text
    muster = r"\b" + r"\s+".join(re.escape(w[:max(4, len(w) - 2)]) + r"\w*" for w in woerter) + r"\b"
    return re.sub(muster, "die Sub-Seite", text, flags=re.I)


_BELEG_ZEILE_RE = re.compile(r"^\W*([\wäöüÄÖÜß]+)\W*:\W*(ja|nein)\b", re.IGNORECASE | re.MULTILINE)


async def kategorien_im_text_belegt(inhalt: str, kategorien: list) -> list[str]:
    """Welche der ETIKETT-Kategorien eines Vorschlags kommen in seinem Text
    wirklich vor? Die Etiketten stammen aus der Auswahl VOR der Generierung; der
    fertige Text setzt oft nur einen Teil davon um. Ein „Thema reizt mich nicht"
    darf deshalb nicht pauschal alle Etiketten belasten – sonst sammelt eine
    Kategorie Ablehnungen für Vorschläge, in denen sie gar nicht vorkam.
    Ein LLM-Durchlauf (temperature 0) mit dem Erkennungs-Merkmal je Kategorie
    und einem Urteil JE Kategorie (JA/NEIN) – ein gemeinsames „KEINE" ließ das
    Modell Grenzfälle mal so, mal so entscheiden. Streng gegen die Etiketten
    validiert und um wörtliche Keyword-Treffer ergänzt; bei Fehler oder
    unlesbarer Antwort der Keyword-Abgleich allein.
    Ohne Text gibt es nichts zu prüfen → Etiketten unverändert."""
    kategorien = [k for k in (kategorien or []) if k]
    if not kategorien or not (inhalt or "").strip():
        return kategorien
    system = (
        f"Du prüfst einen {modi.thema()}-Aufgabenvorschlag gegen eine Liste von Kategorien. "
        "Entscheide für JEDE Kategorie einzeln, ob ihre Praktik im Text stattfindet.\n"
        "JA: Die Beteiligten tun im Text etwas, das zum Merkmal der Kategorie passt – auch als "
        "Teil der Szene und auch, wenn das Wort selbst nicht fällt.\n"
        "NEIN: Die Praktik kommt nicht vor. Eine Anrede, ein Kosename oder ein bildhaftes Wort "
        "belegt nichts – es zählt nur, was die Beteiligten tatsächlich TUN. Was der Text "
        "ausdrücklich ausschließt („kein …“, „… gibt es nicht“), zählt ebenfalls nicht.\n"
        "Antworte NUR mit einer Zeile je Kategorie, genau so: Name: JA oder Name: NEIN. "
        "Keine Erklärung."
    )
    zeilen = [f"- {k}" + (f" – Merkmal: {merkmal(k)}" if merkmal(k) else "") for k in kategorien]
    prompt = "Kategorien:\n" + "\n".join(zeilen) + f"\n\nVorschlag:\n{_anrede_neutral(inhalt)[:1200]}"
    try:
        raw = grok.clean_text(await grok.simple(prompt, system=system, temperature=0)) or ""
        urteile = {name.lower(): urteil.lower() == "ja" for name, urteil in _BELEG_ZEILE_RE.findall(raw)}
        bekannt = {k: urteile[k.lower()] for k in kategorien if k.lower() in urteile}
        if bekannt:
            # Eindeutige Wort-Treffer ergänzen: das LLM übersieht gelegentlich
            # eine Kategorie, die wörtlich im Text steht. Auch hier ohne den
            # Kosenamen – sonst belegt ein Tiername als Anrede die Kategorie
            # über das Stichwort.
            im_text = kategorien_in_text(_anrede_neutral(inhalt))
            return [k for k in kategorien if bekannt.get(k) or k in im_text]
        logger.warning("Kategorie-Beleg: Antwort nicht lesbar – Keyword-Abgleich.")
    except Exception:
        logger.exception("Kategorie-Beleg fehlgeschlagen – Keyword-Abgleich")
    im_text = kategorien_in_text(_anrede_neutral(inhalt))
    return [k for k in kategorien if k in im_text]


async def record_domina_praeferenz(kategorien: list, signal: str, thema_pause: bool = False) -> None:
    """Verbucht die Reaktion der DOMINA auf die Kategorien eines Vorschlags als
    eigenes Signal – NICHT in kategorie_reaktionen (das bleibt die Reaktion des
    Sklaven). So lassen sich Domina-Vorlieben und Sklaven-Erleben später getrennt
    gewichten. `signal`: 'genutzt' | 'gut' | 'abgelehnt'.
    thema_pause=True (nur bei 'abgelehnt'): Der Grund war das Thema selbst – die
    Kategorien ruhen THEMA_PAUSE_TAGE lang in der Auswahl (thema_pausiert)."""
    from bot.services import qdrant
    from datetime import datetime, timedelta, timezone
    eintrag = _DOMINA_SIGNAL.get(signal)
    if not eintrag or not kategorien:
        return
    bucket, amount = eintrag
    prof = await qdrant.get_user_profile("domina") or {}
    praef = dict(prof.get("kategorie_praeferenzen", {}) or {})
    jetzt = datetime.now(timezone.utc)
    pause_bis = (jetzt + timedelta(days=THEMA_PAUSE_TAGE)).isoformat()
    for kat in kategorien:
        e = dict(praef.get(kat) or {"positiv": 0, "negativ": 0})
        e[bucket] = int(e.get(bucket, 0)) + amount
        e["letztes_signal"] = jetzt.isoformat()
        praef[kat] = e
        # Die Pause (setzen wie aufheben) gilt dem Thema – also auch der
        # Kategorie, die dasselbe unter anderem Namen führt. Das Signal selbst
        # bleibt bei der Kategorie, zu der die Rückmeldung kam.
        for betroffen in _mit_gleichem_thema(kat):
            if bucket == "positiv":           # sie mochte es doch → Pause vorbei
                if betroffen in praef:
                    praef[betroffen] = {k: v for k, v in praef[betroffen].items() if k != FELD_PAUSE_BIS}
            elif thema_pause:
                praef[betroffen] = dict(praef.get(betroffen) or {"positiv": 0, "negativ": 0},
                                        **{FELD_PAUSE_BIS: pause_bis})
    await qdrant.patch_profile_fields("domina", {"kategorie_praeferenzen": praef})
    logger.info("Domina-Präferenz: %s +%d für %s%s", bucket, amount, ", ".join(kategorien),
                f" (Themen-Pause {THEMA_PAUSE_TAGE} Tage)" if thema_pause and bucket == "negativ" else "")


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
    # Nicht wählbar: gelernte Abneigungen der Sub-Seite UND Themen, die die
    # Dom-Seite gerade ausdrücklich nicht reizen (Themen-Pause).
    dislikes = set(gesperrte_kategorien(sklave_profil)) | set(thema_pausiert(domina_praeferenzen))
    selten = set(gedaempfte_kategorien(sklave_profil))
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
        vorlieben_kats |= vorliebe_kategorien(v)
    # Wunsch-Einträge können veraltet sein → gegen den Pool schneiden
    basis = (wunsch | tops | vorlieben_kats) & set(pool) - dislikes
    exploration = (_nachbarn_von(basis) & set(pool)) - basis - dislikes
    wildcard = {k for k in pool if k not in basis and k not in exploration and k not in dislikes}
    zc = zu_cluster()

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
        elif mit_recency and zc.get(kat, set()) & recent_cluster:
            # Thema kam zuletzt dran (aber andere Kategorie) → milder dämpfen
            w = max(1.0, w - _W_CLUSTER_MALUS)
        # Domina-Signal (getrennt vom Sklaven-Erleben): netto positiv → Bonus,
        # netto negativ → Malus. Eigenes Gewicht, unabhängig von wunsch/tops.
        dp = domina_praeferenzen.get(kat)
        if dp:
            # Gealtertes Netto, nach Stärke skaliert: ein einzelnes Signal wiegt
            # ein Drittel, ab _DOMINA_SIGNAL_VOLL gilt der volle Bonus/Malus.
            netto = domina_netto_effektiv(dp)
            anteil = _W_DOMINA_PRAEFERENZ * min(abs(netto), _DOMINA_SIGNAL_VOLL) / _DOMINA_SIGNAL_VOLL
            if netto > 0:
                w += anteil
            elif netto < 0:
                w = max(1.0, w - anteil)
        if kat in selten:
            w *= _W_SELTEN_FAKTOR
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
            if not (zc.get(k, set()) & tabu_cluster) and k not in vertraut
        }
        if not fremd:  # Vorlieben decken fast alle Themen ab → mildere Definition
            fremd = {
                k for k in (exploration | wildcard)
                if not (zc.get(k, set()) & basis_cluster)
            }
        # bevorzugt ein fremdes Thema, das auch nicht zuletzt schon dran war
        fremd_frisch = {k for k in fremd if not (zc.get(k, set()) & recent_cluster)}
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
