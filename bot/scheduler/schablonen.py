"""
Deterministische Text-Detektoren für Vorschläge: Schablonen, Formeln, Zutaten-Sperrliste, Diversität.

Aus bot/scheduler/followup.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
"""
import logging
import random
import re
from bot.services import embeddings, kategorie_logik
from bot.messages import t

logger = logging.getLogger(__name__)


# Mehrstufige Programm-Struktur (Phase 1/Stufe 1/Schritt 1 …) in Vorschlags-Texten.
_STUFEN_RE = re.compile(r"(phase|stufe|schritt)\s*[123]|(drei|zwei) (phasen|stufen|schritten)", re.I)


def _vorschlag_anfang(inhalt: str) -> str:
    """Erster Satz eines Vorschlags (max. 90 Zeichen) – für die
    VERBRAUCHTE-ANFÄNGE-Sperrliste im Generator-Prompt."""
    saetze = re.split(r"(?<=[.!?:])\s+", (inhalt or "").strip())
    return saetze[0][:90] if saetze and saetze[0] else ""


def _vorschlag_abschluss(inhalt: str) -> str:
    """Letzter Satz eines Vorschlags (max. 90 Zeichen) – für die
    VERBRAUCHTE-ABSCHLÜSSE-Sperrliste (D9/DIV3): Abschluss-Fragen recyceln
    genauso wie Opener („Wie lange willst du …?" in 4 von 5 Folgetagen)."""
    saetze = [s_ for s_ in re.split(r"(?<=[.!?])\s+", (inhalt or "").strip()) if s_]
    return saetze[-1][:90] if saetze else ""


# Deterministischer Schablonen-Detektor (D9/DIV1/3/5): die Prompt-Verbote in
# _formel_verbot verlieren gegen die Modell-Präferenz (Diversitäts-Messung
# 15.08.: „Das passt …, weil" trotz Verbot in ~60 % der Outputs, „Wie wär's"
# rutschte am 5. Folgetag wieder durch). Bekanntes Lernmuster: Prompt-Regeln
# brauchen einen Detektor + Retry (wie _ist_echo/_ist_spiegel_anfang im Chat).
_PASST_WEIL_RE = re.compile(r"\bpasst\b[^.!?\n]{0,60}\bweil\b", re.IGNORECASE)

# Ausweich-Variante (Live 18.09.2026): nach dem „passt …, weil"-Retry schrieb das
# Modell „Das kommt jetzt genau richtig, weil …" – derselbe Satzbau, anderes Verb.
_JETZT_RICHTIG_WEIL_RE = re.compile(
    r"\b(?:kommt|sitzt|ist)\b[^.!?\n]{0,40}\b(?:genau\s+richtig|gerade\s+richtig|goldrichtig|"
    r"wie\s+gerufen|(?:jetzt|heute)\s+(?:genau\s+)?(?:richtig|dran|passend))\b[^.!?\n]{0,40}\bweil\b",
    re.IGNORECASE)

# „Hey, wie wär's …" steht wörtlich im Verbot, rutschte aber durch: „Hey" sind
# Wortzeichen, das alte \W-Fenster sah nur Satzzeichen (Live 18.09., Rollenspiel-Idee).
_WIE_WAERS_RE = re.compile(r"^\W{0,8}(?:hey\W{1,3})?wie\s+w[äa]r['’`]?s\b", re.IGNORECASE)

# Ohne ?-Fenster (Nachtest 15.08.: lange Varianten wie „…, bevor du
# entscheidest, ob's reicht …?" lagen außerhalb des 80-Zeichen-Fensters).
# Die Frage an die Domina ist praktisch immer der Schluss-Satz – ein seltener
# False Positive kostet nur einen harmlosen Retry.
_WIE_LANGE_RE = re.compile(
    r"\bwie\s+lange\s+(willst|magst|l[äa]sst)\s+du\b", re.IGNORECASE)

# Nachfolger-Schablonen (Live-Messung 06.09.): das Modell wich dem
# „passt…weil"-Verbot in der Mehrzahl der gemessenen Vorschläge auf „Das
# holt/zieht/drängt genau seine …-Rolle raus" aus und erzählte dabei die
# Vorlieben-Liste UND seine interne Absetz-Logik nach („ohne dass es wieder
# nur X wird") – obwohl beides im Prompt verboten ist. Gleiche Mechanik:
# Detektor + Retry statt Prompt-Hoffnung.
_TREFFER_RE = re.compile(
    r"\b(das|es|was)\s+(holt|zieht|dr[äa]ngt|trifft|kippt|bringt)\b[^.!?\n]{0,80}\bgenau\b",
    re.IGNORECASE)

_GENAU_PROFIL_RE = re.compile(
    r"\bgenau\b[^.!?\n]{0,40}\b(vorlieb\w*|ding|rolle|seite|ecke|lust|kink\w*|level|antreibt|braucht)\b",
    re.IGNORECASE)

_ABSETZ_META_RE = re.compile(
    r"\bohne dass\b[^.!?\n]{0,80}\b(wieder|üblich\w*|alten?|abdriftet|versteck\w*)\b",
    re.IGNORECASE)

# Ausweich-Varianten (Live-Beobachtung 07.09.): das „passt…weil"-Verbot wurde
# mit „Passt perfekt zu deiner … , wenn …" umgangen (kein „weil" → Regex oben
# griff nicht), und die Tarnung des Steuerwissens mit „…, den du magst".
_PASST_ZU_RE = re.compile(
    r"\bpasst\b\s+(?:\w+\s+){0,2}zu\s+(dir|dein\w*|ihm|ihr\w*|sein\w*|euch|eure\w*)\b",
    re.IGNORECASE)

_MAGST_RE = re.compile(
    r"\b(den|die|das|was)\s+(du|er|sie)\s+(?:so\s+|ja\s+)?"
    r"(magst|mag|liebst|liebt|brauchst|braucht|genie[ßs]t)\b",
    re.IGNORECASE)


def _begruendungs_formel_entfernen(text: str) -> str:
    """Notbremse (Live 17.09.2026): Retry schlechter als das Original → das
    Original ging MIT „Das passt jetzt gut, weil …" raus. Die Begründung ist
    genau ein Satz und verzichtbar – Sätze mit der passt-Formel fallen weg,
    solange danach noch Text bleibt."""
    saetze = re.split(r"(?<=[.!?…])(\s+)", text or "")
    behalten = []
    for teil in saetze:
        if teil.strip() and (_PASST_WEIL_RE.search(teil) or _PASST_ZU_RE.search(teil)
                             or _JETZT_RICHTIG_WEIL_RE.search(teil)):
            if behalten and not behalten[-1].strip():
                behalten.pop()  # Trennzeichen vor dem entfernten Satz mit weg
            continue
        behalten.append(teil)
    rest = "".join(behalten).strip()
    rest = re.sub(r"\n{3,}", "\n\n", rest)
    return rest if len(rest) >= 40 else (text or "")


# ---------- Meta-Rückfragen über den Vorschlag selbst ----------
# Live 17./18.09.2026: „Wie fühlst du dich bei der Vorstellung, ihm das so zu
# schreiben?", „Klingt genau nach …, oder?" – Marotte des Modells, keine echte
# Gesprächsfrage. Echte Anschlussfragen („Wie hat er zuletzt reagiert?") bleiben.
_META_RUECKFRAGE_RE = re.compile(
    r"(fühlst du dich|wie klingt|klingt (?:das|dir|genau|nach)|machbar|bei der vorstellung|"
    r"was (?:meinst|sagst|hältst) du|passt (?:das|dir)|zu hart|zu viel|,\s*oder\s*\?\s*$)",
    re.IGNORECASE)


def _meta_rueckfrage_entfernen(text: str) -> str:
    """Schneidet im LETZTEN Absatz Fragesätze ab, die den Vorschlag selbst zum
    Thema machen. Bleibt danach zu wenig übrig, bleibt der Text unverändert."""
    t = (text or "").strip()
    kopf, trenner, letzter = t.rpartition("\n\n")
    saetze = re.split(r"(?<=[.!?…])\s+", letzter)
    behalten = [s for s in saetze
                if not (s.rstrip().endswith("?") and _META_RUECKFRAGE_RE.search(s))]
    if len(behalten) == len(saetze):
        return t
    rest = (kopf + trenner + " ".join(behalten)).strip() if behalten else kopf.strip()
    return rest if len(rest) >= 40 else t


def _formel_verstoesse(text: str) -> list[str]:
    funde = []
    if _PASST_WEIL_RE.search(text or "") or _JETZT_RICHTIG_WEIL_RE.search(text or ""):
        funde.append('Begründungs-Formel „passt/kommt genau richtig …, weil"')
    elif _PASST_ZU_RE.search(text or ""):
        funde.append('Begründungs-Formel „passt (perfekt) zu dir/ihm …"')
    if _WIE_WAERS_RE.search(text or ""):
        funde.append('Einstieg „Wie wär\'s …"')
    if _WIE_LANGE_RE.search(text or ""):
        funde.append('Abschluss-Frage „Wie lange willst du …?"')
    if _TREFFER_RE.search(text or "") or _GENAU_PROFIL_RE.search(text or ""):
        funde.append('Profil-Treffer-Meldung („holt/trifft genau seine …")')
    if _ABSETZ_META_RE.search(text or ""):
        funde.append('Absetz-Meta-Kommentar („ohne dass es wieder …")')
    if _MAGST_RE.search(text or ""):
        funde.append('Profil-Treffer-Meldung („…, den du/er magst")')
    return funde


# ---------------------------------------------------------------------------
# Zutaten-Sperrliste (Live-Befund 06.09.): die Kategorien ROTIEREN sauber, aber
# dieselben zwei, drei Beiwerk-Praktiken garnierten fast jeden der gemessenen
# Vorschläge, egal welche Kategorie gewählt war. Die Label-/Kategorien-
# Sperrlisten sehen nur das Hauptthema – Beiwerk erkennt nur ein Text-Scan der
# letzten Volltexte. Je Zutat: Anzeige-Label, Erkennungs-Regex,
# Kategorien, die sie als HAUPTthema legitimieren (heute gewählt → kein Verbot,
# sonst stünden zwei Absolutanweisungen gegeneinander, Lektion D7/B4).
_ZUTATEN_MUSTER: list[tuple[str, re.Pattern, frozenset]] = [
    ("Facesitting / aufs Gesicht setzen",
     re.compile(r"facesit|(auf|über) (sein|ihr|dein)e?m? gesicht|aufs gesicht|gesicht (sitz|setz)", re.I),
     frozenset({"Facesitting", "Smothering", "Arschanbetung", "Muschianbetung"})),
    ("Lecken/Auslecken",
     re.compile(r"leck", re.I),
     frozenset({"Arschanbetung", "Muschianbetung", "Facesitting", "Toiletten_Sklave", "Creampie_Cleanup"})),
    ("Spucken/Speichel",
     re.compile(r"spuck|speichel", re.I),
     frozenset({"Speichelspiel"})),
    ("Edging / kurz vorm Höhepunkt stoppen",
     re.compile(r"\bedg|kurz vorm (abspritzen|kommen|h[öo]hepunkt)", re.I),
     frozenset({"Orgasmusverweigerung", "Ruiniertes_Orgasmen"})),
    ("Strapon/Pegging",
     re.compile(r"strap.?on|pegg", re.I),
     frozenset({"Strap_on", "Pegging"})),
    ("Fesseln",
     re.compile(r"fessel|hogtie|\bseil", re.I),
     frozenset({"Klassische_Fesselspiele"})),
    ("Plug",
     re.compile(r"\bplug", re.I),
     frozenset({"Buttplug_Tragen", "Analdehnung", "Analeingangstraining"})),
    ("Jammern-Motiv",
     re.compile(r"jammer", re.I),
     frozenset()),
]


def _verbrauchte_zutaten(volltexte: list, heutige_kategorien: list,
                         kombi_vorlieben: list | None = None) -> list[str]:
    """Beiwerk-Praktiken, die in den letzten Vorschlags-Volltexten vorkamen –
    als Sperrliste für den heutigen Prompt. Befreit sind Zutaten, deren
    Kategorie heute bewusst gewählt ist oder die in einer Kombi-Vorliebe
    stecken (kein Widerspruch zwischen Absolutanweisungen)."""
    heutige = set(heutige_kategorien or [])
    kombi = [k for k in (kombi_vorlieben or []) if k]
    # Kombi-Zeilen befreien doppelt: direkter Text-Match UND ihre gemappten
    # Kategorien (eine „Facesitting…"-Kombi impliziert Lecken – das darf dann
    # nicht zugleich als Zutat gesperrt sein).
    kombi_kats: set = set()
    for k in kombi:
        kombi_kats |= kategorie_logik.kategorien_in_text(k)
    out = []
    for label, rx, legitimiert in _ZUTATEN_MUSTER:
        if legitimiert & (heutige | kombi_kats):
            continue
        if any(rx.search(k) for k in kombi):
            continue
        if any(rx.search(v or "") for v in volltexte):
            out.append(label)
    return out


# Alltags-/Ritual-Anker in Freitext-Vorlieben (Zeilen der Form „beim …", „nach
# dem …", „wenn sie …") – bevorzugte Bühne für die Kombi.
_SITUATIV_RE = re.compile(
    r"\b(beim|nach dem|wenn|während|dusch\w*|föhn\w*|frühstück\w*|abends?|morgens?)\b", re.I)


def _vorlieben_cluster(text: str) -> set[int]:
    """Themen-Cluster-Indizes, die eine Freitext-Vorliebe berührt."""
    cluster: set[int] = set()
    for k in kategorie_logik.kategorien_in_text(text):
        cluster |= kategorie_logik.zu_cluster().get(k, set())
    return cluster


def _kombi_vorlieben_wahl(vorlieben: list, dislikes: list | None = None) -> list | None:
    """Zwei Freitext-Vorlieben für den KOMBI-IMPULS (Nutzer-Wunsch 06.09.):
    bevorzugt eine situative Zeile (Alltags-Ritual als Bühne) als Anker plus
    einen Partner aus einem ANDEREN Themen-Cluster – die Kategorie-Rotation
    erzeugt solche Querverbindungen sonst nie. Zeilen, die auf Dislike-
    Kategorien mappen, sind ausgeschlossen. Zeilen bleiben wörtlich erhalten
    (Richtungs-/Bedingungs-Zusätze dürfen nicht abhandenkommen)."""
    dislikes_set = set(dislikes or [])
    kandidaten = [
        v for v in (vorlieben or [])
        if isinstance(v, str) and len(v.strip()) >= 3
        and not (kategorie_logik.kategorien_in_text(v) & dislikes_set)
    ]
    if len(kandidaten) < 2:
        return None
    situativ = [v for v in kandidaten if _SITUATIV_RE.search(v)]
    anker = random.choice(situativ or kandidaten)
    anker_cluster = _vorlieben_cluster(anker)
    fremd = [v for v in kandidaten
             if v != anker and not (_vorlieben_cluster(v) & anker_cluster)]
    rest = [v for v in kandidaten if v != anker]
    partner = random.choice(fremd or rest)
    return [anker, partner]


def _cos(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


async def _diversester_kandidat(kandidaten: list[str], referenzen: list[str]) -> str:
    """Wählt aus mehreren Kandidaten den mit der geringsten maximalen
    Embedding-Ähnlichkeit zu den letzten Vorschlägen (D9/DIV2): der
    Reasoning-Pfad konvergiert sonst hart auf ein Rezept (Messung 15.08.:
    3/3 Wochenend-Vorschläge fast identisch, Cosine bis 0.886)."""
    kandidaten = [k for k in kandidaten if k]
    if len(kandidaten) <= 1 or not referenzen:
        return kandidaten[0] if kandidaten else ""
    try:
        ref_vecs = [await embeddings.get_embedding(r) for r in referenzen[:5]]
        best, best_score = kandidaten[0], None
        for k in kandidaten:
            v = await embeddings.get_embedding(k)
            score = max((_cos(v, rv) for rv in ref_vecs), default=0.0)
            if best_score is None or score < best_score:
                best, best_score = k, score
        logger.info("Diversitäts-Wahl: %d Kandidaten, gewählte max-Ähnlichkeit %.3f.",
                    len(kandidaten), best_score or 0.0)
        return best
    except Exception:
        logger.exception("Kandidaten-Wahl fehlgeschlagen – nehme ersten.")
        return kandidaten[0]
