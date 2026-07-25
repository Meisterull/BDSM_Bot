"""
Hard-Limits / Grenzen Output-Validierung.

Pruefen KI-generierte Texte gegen:
  • die Hard-Limits-Liste des Sklaven (absolut, sicherheitsrelevant)
  • die Grenzen-Liste der Domina (persoenliche Grenzen)

Falls eine Verletzung erkannt wird, wird der Text NICHT gesendet, sondern
muss vom Aufrufer behandelt werden (Re-Generation mit verschaerftem Prompt
oder Abbruch).

Erkennung:
  1) Normalisiertes Substring-Match auf den Original-Begriff
  2) Wortstamm-Match (Endungen abgeschnitten)
  3) Synonym-Map (verwandte Worte / typische Umschreibungen)

⚠️ SPRACH-ABHÄNGIGKEIT (sicherheitsrelevant): Die Synonym-Map (_SYNONYME) deckt
DEUTSCH und (seit dem Multiuser-Ausbau) ENGLISCH ab – englische Limit-Keys plus
englische Stämme in den sicherheitskritischen deutschen Einträgen. Bei anderen
Sprachen greifen nur (1) und (2), also das wörtliche Matching der hinterlegten
Limit-Begriffe – Umschreibungen werden dann NICHT erkannt. /einstellungen warnt
deshalb beim Setzen einer anderen Sprache; Hard Limits sollten zusätzlich in
der Zielsprache hinterlegt werden.
"""
import logging
import re
import unicodedata

from bot.services import qdrant

logger = logging.getLogger(__name__)


# Synonym-Map: Schluessel ist ein Begriff aus den Limit-Listen (lowercase, ohne
# Umlaute), Wert ist eine Liste verwandter Worte/Stämme, die im Text NICHT
# vorkommen duerfen, wenn der Schluessel ein Limit ist.
# Liberal pflegen: lieber zu viele Synonyme als zu wenige.
_SYNONYME: dict[str, list[str]] = {
    "spucken":       ["spuck", "speichel", "spei", "sabber", "rotz"],
    "speichel":      ["speichel", "spuck", "spei", "sabber"],
    "spuck":         ["spuck", "speichel", "spei", "sabber"],
    "blut":          ["blut", "ritz", "schneid", "messer", "wund", "blutig", "blood", "bleed", "cut"],
    "schneiden":     ["schneid", "ritz", "messer", "blut", "wund"],
    "atemkontrolle": ["atem", "luft abdr", "luft abdrueck", "wuerg", "erstick", "choke", "atemnot"],
    "wuergen":       ["wuerg", "atem", "luft abdr", "erstick", "chok", "strangl"],
    "ersticken":     ["erstick", "atem", "wuerg", "luft abdr"],
    "atem":          ["atem", "wuerg", "erstick", "luft abdr", "choke"],
    "urin":          ["urin", "piss", "natursekt", "ns ", "pinkel", "ns,", "ns.", "pee", "golden shower"],
    "piss":          ["piss", "urin", "natursekt", "pinkel"],
    "kot":           ["kot", "scheis", "schmutz", "kaviar", "kv ", "kv,", "scat", "poop", "shit"],
    "kaviar":        ["kaviar", "kot", "scheis", "kv ", "kv,"],
    "scheisse":      ["scheis", "kot", "kaviar", "kv "],
    "sperma":        ["sperma", "samen", "ejakul", "wichse", "ejakulat", "cum", "semen", "sperm"],
    "schlucken":     ["schluck"],
    "kinder":        ["kind ", "kinder", "minderj", "schul", "child", "minor"],
    "tiere":         ["tier", "hund", "katze", "pferd", "animal", "dog", "cat", "horse"],
    "feuer":         ["feuer", "kerzen wachs", "wachs", "verbrenn", "branden"],
    "wachs":         ["wachs", "kerze", "verbrenn"],
    "fisting":       ["fist", "faust", "ganze hand"],
    "nadeln":        ["nadel", "stich", "piercing", "pin "],
    "elektro":       ["elektr", "strom", "tens", "violet wand"],
    "schmerz":       ["schmerz", "weh", "qual", "peinig", "schlag", "hau"],
    "schlagen":      ["schlag", "hau", "klatsch", "klaps"],
    "ohrfeige":      ["ohrfeig", "klatsch", "backpfeif"],
    "gesichtsfick":  ["gesichtsfick", "facefuck", "face fuck", "rachenfick"],
    "deepthroat":    ["deepthroat", "deep throat", "tief in den hals", "rachen"],
    "fesseln":       ["fessel", "bondage", "binden", "seile", "seil"],
    "demuetigung":   ["demueti", "erniedrig", "beschimpf", "beleidig"],
    "erniedrigung":  ["erniedrig", "demueti", "beschimpf"],
    "verbal":        ["beschimpf", "beleidig", "demueti", "erniedrig"],
    "anal":          ["anal", "arsch", "po ", "rektal", "darm"],
    "fisting_anal":  ["fist", "faust", "ganze hand", "anal", "arsch"],
    "vaginal":       ["vaginal", "vagina", "muschi"],
    "oeffentlich":   ["oeffent", "in der oeffent", "draussen", "park", "restaurant", "bus"],
    "draussen":      ["draussen", "oeffent", "park", "wald", "strasse"],
    "alkohol":       ["alkohol", "bier", "wein", "schnaps", "saufen"],
    "drogen":        ["drog", "kokain", "weed", "cannabis", "kiff"],
    "rasieren":      ["rasier", "glatt", "schamhaar"],
    "rauchen":       ["rauch", "zigarette", "kippe", "asche"],
    "fussanbetung":  ["fussanbetung", "fuss", "fuesse", "fuessen", "zehen", "foot", "feet"],
    "haltungstraining": ["haltungstraining", "haltungsuebung", "haltung halten",
                         "position halten", "strammstehen", "posture"],

    # --- Englische Limit-Begriffe (englischsprachige Paare) -----------------
    # SICHERHEIT: Werte mischen englische UND deutsche Stämme, damit auch
    # gemischtsprachige Texte matchen. Liberal pflegen (siehe oben).
    "spitting":      ["spit", "saliva", "drool", "spuck", "speichel"],
    "saliva":        ["saliva", "spit", "drool", "speichel", "spuck"],
    "blood":         ["blood", "bleed", "cut", "knife", "razor", "blut", "ritz", "messer"],
    "cutting":       ["cut", "knife", "razor", "blade", "blood", "schneid", "ritz", "blut"],
    "breath":        ["breath", "chok", "strangl", "suffocat", "airway", "atem", "wuerg", "erstick"],
    "breathplay":    ["breath", "chok", "strangl", "suffocat", "atem", "wuerg"],
    "choking":       ["chok", "strangl", "breath", "suffocat", "wuerg", "atem"],
    "strangling":    ["strangl", "chok", "breath", "wuerg", "erstick"],
    "urine":         ["urine", "piss", "pee", "golden shower", "watersports", "natursekt"],
    "watersports":   ["watersports", "urine", "piss", "pee", "golden shower", "natursekt"],
    "scat":          ["scat", "feces", "faeces", "poop", "shit", "kot", "kaviar"],
    "feces":         ["feces", "faeces", "scat", "poop", "shit", "kot"],
    "semen":         ["semen", "sperm", "cum", "ejacul", "sperma"],
    "swallow":       ["swallow", "schluck"],
    "children":      ["child", "minor", "school", "kind ", "kinder", "minderj", "schul"],
    "minors":        ["minor", "child", "school", "minderj", "kind "],
    "animals":       ["animal", "dog", "cat", "horse", "tier", "hund", "katze", "pferd"],
    "fire":          ["fire", "burn", "candle", "wax", "feuer", "wachs", "verbrenn"],
    "needles":       ["needle", "piercing", "pin ", "nadel", "stich"],
    "electric":      ["electr", "shock", "tens", "violet wand", "strom"],
    "pain":          ["pain", "hurt", "torment", "schmerz", "qual", "weh", "schlag"],
    "hitting":       ["hit ", "hitting", "slap", "spank", "beat", "strike", "schlag", "hau", "klatsch"],
    "slapping":      ["slap", "hit ", "backhand", "ohrfeig", "klatsch", "backpfeif"],
    "spanking":      ["spank", "paddle", "slap", "schlag", "klaps"],
    "facefucking":   ["facefuck", "face fuck", "throatfuck", "gesichtsfick", "rachen"],
    "bondage":       ["bondage", "tie", "tied", "rope", "restrain", "fessel", "seil", "binden"],
    "rope":          ["rope", "tie", "bondage", "seil", "fessel"],
    "humiliation":   ["humiliat", "degrad", "insult", "demueti", "erniedrig", "beschimpf"],
    "degradation":   ["degrad", "humiliat", "insult", "erniedrig", "demueti"],
    "public":        ["public", "outdoor", "outside", "park", "restaurant", "bus", "oeffent", "draussen"],
    "outdoor":       ["outdoor", "outside", "public", "park", "draussen", "oeffent"],
    "alcohol":       ["alcohol", "beer", "wine", "booze", "drunk", "alkohol", "bier", "wein"],
    "drugs":         ["drug", "weed", "cannabis", "cocaine", "drog", "kiff"],
    "shaving":       ["shav", "razor", "rasier", "glatt"],
    "smoking":       ["smok", "cigarette", "ash", "rauch", "zigarette", "asche"],
}


def _normalisiere(text: str) -> str:
    """Lowercase, Umlaute auf ue/oe/ae/ss, Whitespace komprimiert, Emoji-Prefixe entfernt.

    WICHTIG: Umlaute werden zu ue/oe/ae aufgelöst (NICHT via NFKD zu u/o/a), damit
    Text und Limit-Begriffe dieselbe Schreibweise wie die _SYNONYME-Map (ue/oe/ae)
    haben – sonst matchte die komplette Synonym-Ebene für Umlaut-Begriffe
    (wuergen, demuetigung, oeffentlich, …) NIE."""
    text = text.lower()
    text = (text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
                .replace("ß", "ss"))
    # Restliche kombinierende Diakritika (z.B. é, ñ) per NFKD entfernen.
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Emoji-Prefixe entfernen (z.B. "🚫 Anal" → "Anal")
    text = re.sub(r"^[^\w]+\s*", "", text)
    return re.sub(r"\s+", " ", text)


def _stamm(begriff: str) -> str:
    """Wortstamm fuer robustere Matches (z.B. 'Spucken' -> 'spuck')."""
    norm = _normalisiere(begriff)
    if len(norm) > 5:
        return norm[: max(5, len(norm) - 2)]
    return norm


def _ohne_ausnahme(limit: str) -> str:
    """Limit-Begriff ohne '(Ausnahme: ...)'-Annotation (aus limit_refine).

    Suchbegriffe werden NUR aus dem Basis-Begriff erzeugt – aus zwei Gründen:
    1. Die Output-Validierung bleibt strikt: Ausnahmen lockern den Check NICHT,
       sie informieren nur die Prompts (LLM darf differenziert formulieren).
    2. Die Ausnahme-Wörter dürfen keine fremden Synonym-Listen aktivieren –
       sonst würde z.B. 'Öffentlichkeit (Ausnahme: Analplug)' über den Key
       'anal' plötzlich ALLES Anale als Öffentlichkeits-Verletzung melden."""
    idx = limit.lower().find("(ausnahme")
    return limit[:idx].strip() if idx > 0 else limit


def _suchbegriffe_fuer(limit: str) -> list[str]:
    """Erzeugt fuer einen Limit-Begriff die vollstaendige Liste an Suchstrings:
    Original, Stamm, alle bekannten Synonyme. Alles normalisiert."""
    limit = _ohne_ausnahme(limit)
    norm = _normalisiere(limit)
    stamm = _stamm(limit)
    treffer = {norm, stamm}

    # Direkter Lookup in der Synonym-Map (auf normalisierter Form).
    # `key in norm` (der Limit-Text enthält einen bekannten Synonym-Key, z.B.
    # "oeffentliche demuetigung" enthält "demuetigung") ist immer sicher.
    # Die Rückrichtung (Limit ist Teilstring eines längeren Keys) wird nur ab
    # Länge 5 zugelassen – sonst zieht ein kurzes Limit wie "anal" via
    # "anal" in "vaginal"/"fisting_anal" fremde Synonym-Listen mit (Over-Blocking).
    for key, syns in _SYNONYME.items():
        if key == norm or key in norm:
            treffer.update(_normalisiere(s) for s in syns)
        elif len(norm) >= 5 and norm in key:
            treffer.update(_normalisiere(s) for s in syns)
        elif stamm and len(stamm) >= 5 and (stamm in key or key in stamm):
            treffer.update(_normalisiere(s) for s in syns)

    # Leere strings raus
    return [t for t in treffer if t]


# Kurze Limit-Stämme, die häufig harmlose Wörter als Prefix treffen → Ausnahme-Suffix.
# Form: stamm -> Regex-Suffix-Alternativen, die NICHT folgen dürfen. Liberal-vorsichtig erweitern.
_FALSE_FRIEND_SUFFIX: dict[str, str] = {
    "wund": "er",       # "wund" ok (Wunde, wundliegen), aber NICHT "wunder/wunderbar/wundervoll"
    "schul": "ter|d",   # "Schule/Schulkind" ja, aber NICHT "Schulter(n)" (Massage!) / "Schuld"
    "hund": "ert",      # "Hund" ja, aber NICHT "hundert"
    "fuss": "boden|ball|matte|fessel|note|abdruck",  # Fuß ja, aber NICHT Fußboden (knien!) / Fußfesseln (Bondage)
    "foot": "ball",     # foot ja, aber NICHT football
}

# Häufige deutsche Verb-Präfixe: Limit-Stämme tauchen oft NACH einem Präfix auf
# ("auspeitschen", "angespuckt", "gefesselt", "abgewürgt", "vollgepisst") – das
# reine \b-Wortanfangs-Matching würde diese Formen verpassen (False Negative,
# sicherheitskritisch). Bewusst MINIMALE Liste: Präpositional-Präfixe wie "vor"/
# "auf"/"zu" fehlen absichtlich, sonst matchen Alltagswörter ("Vorschlag" →
# Limit "schlagen"). Bis zu zwei stapelbare Präfixe ("voll"+"ge"). Trade-off:
# seltene False Positives (z.B. "Anschlag") kosten nur eine Re-Generierung,
# False Negatives wären eine Grenzverletzung.
_VERB_PRAEFIXE = r"(?:aus|an|be|ab|ein|ver|voll|durch|ge){0,2}"


def _such_pattern(suchwort: str) -> str:
    """Regex für ein Suchwort: Wortanfang (\\b), optionale Verb-Präfixe (nur für
    rein alphabetische Stämme ab 4 Zeichen – kurze Stämme wie 'hau' und Phrasen
    mit Leerzeichen bleiben beim strikten \\b-Match), Negative-Lookahead für
    bekannte False Friends (z.B. 'wund' nicht in 'wunderbar', auch nicht als
    'verwundert')."""
    nl = _FALSE_FRIEND_SUFFIX.get(suchwort)
    lookahead = rf"(?!(?:{nl}))" if nl else ""
    if re.fullmatch(r"[a-z]{4,}", suchwort):
        return rf"\b{_VERB_PRAEFIXE}{re.escape(suchwort)}{lookahead}"
    return rf"\b{re.escape(suchwort)}{lookahead}"


# --- Richtungs-Qualifizierer -------------------------------------------------
# Limits können eine RICHTUNG tragen ("X des Subs" = X vom SUB ist tabu,
# dasselbe X von der Herrin kann ausdrücklich Vorliebe sein). Das
# Synonym-Matching ist richtungs-blind und fraß deshalb legitime
# Wünsche/Aufgaben in Gegenrichtung (Live-Befund 15.07.2026 – gleiche
# Bug-Klasse wie der Richtungs-Bug vom 29.06.).
#
# Regel (fail-closed): NUR wenn das LIMIT einen expliziten Richtungs-
# Qualifizierer trägt UND die Textstelle die Praktik EINDEUTIG der anderen
# Person zuschreibt, zählt der Treffer nicht. Ohne Qualifizierer im Limit oder
# bei mehrdeutiger Zuschreibung im Text bleibt es eine Verletzung.
#
# ⚠️ Possessiv-Marker sind PERSPEKTIVEN-ABHÄNGIG: In generiertem Herrin-Output
# ("sprecher='herrin'") heißt "dein X" = das des Subs; in einer Sub-Nachricht
# ("sprecher='sub'") heißt "dein X" = das der Herrin. Aufrufer, die
# SUB-verfasste Texte prüfen (Wunsch-/Präferenz-Erfassung), MÜSSEN
# sprecher="sub" übergeben; Default bleibt "herrin" (= generierter Output).

_LIMIT_RICHTUNG_SUB = r"\b(des (subs?|sklaven)|vom (sub|sklaven)|of the (sub|slave))\b"
_LIMIT_RICHTUNG_DOM = r"\b(der (herrin|domina|domme)|von der (herrin|domina)|of the (domme|mistress))\b"

# Zuschreibungs-Marker im geprüften Text, je Sprecher-Perspektive:
# wem "gehört" die Praktik an dieser Textstelle? (Bare "sein" fehlt bewusst –
# kollidiert mit dem Verb; entgangene Formen bleiben fail-closed Treffer.)
_ATTR = {
    "herrin": {  # sie spricht den Sub an (generierte Aufgaben/Antworten)
        "domina": r"\b(mein\w*|der (herrin|domina)|von mir|aus mir)\b",
        "sub":    r"\b(dein\w*|seine[mnrs]?|eigen\w*|des (subs?|sklaven)|vom (sub|sklaven)|von dir)\b",
    },
    "sub": {     # er schreibt (Chat-Nachricht, extrahierter Wunsch)
        "domina": r"\b(dein\w*|ihre[mnrs]?|der (herrin|domina)|von (dir|ihr)|aus (dir|ihr))\b",
        "sub":    r"\b(mein\w*|eigen\w*|von mir|des (subs?|sklaven))\b",
    },
}


def _limit_richtung(limit: str) -> str | None:
    """Erkennt einen expliziten Richtungs-Qualifizierer im Limit-Begriff.
    Kein Qualifizierer (oder unbekannte Formulierung) → None = richtungslos,
    Matching bleibt strikt wie bisher."""
    ln = _normalisiere(_ohne_ausnahme(limit))
    if re.search(_LIMIT_RICHTUNG_SUB, ln):
        return "sub"
    if re.search(_LIMIT_RICHTUNG_DOM, ln):
        return "domina"
    return None


def _eindeutig_andere_person(text_norm: str, start: int, ende: int,
                             richtung: str, sprecher: str) -> bool:
    """True, wenn das Fenster um die Fundstelle die Praktik EINDEUTIG der Person
    zuschreibt, die vom Limit NICHT gemeint ist (z.B. Limit 'X des Subs',
    Sub schreibt 'dein X'). Fenster beidseitig, weil deutsche Possessive
    vor ('ihr X') und nach ('X der Herrin') dem Begriff stehen können."""
    attr = _ATTR.get(sprecher)
    if not attr:
        return False
    fenster = text_norm[max(0, start - 30):start] + " " + text_norm[ende:ende + 25]
    andere = "domina" if richtung == "sub" else "sub"
    return (re.search(attr[andere], fenster) is not None
            and re.search(attr[richtung], fenster) is None)


def _prufe_liste(text_norm: str, limits: list[str], quelle: str,
                 sprecher: str = "herrin") -> list[dict]:
    """Pruefe gegen eine Liste, gib detaillierte Treffer zurueck."""
    if not limits:
        return []
    treffer: list[dict] = []
    schon_gemeldet: set[str] = set()
    for limit in limits:
        if not limit:
            continue
        if limit in schon_gemeldet:
            continue
        richtung = _limit_richtung(limit)
        for suchwort in _suchbegriffe_fuer(limit):
            if not suchwort:
                continue
            # Wortanfangs-Grenze (\b) statt reinem Substring: verhindert Mid-Word-
            # Fehltreffer (z.B. "Quartier" -> "tier", "Entschuldigung" -> "schul")
            # OHNE echte Treffer zu verlieren (Flexion bleibt: blut->blutig, blutet;
            # wund->Wunde). Präfix-Formen (auspeitschen, gefesselt) deckt
            # _such_pattern über _VERB_PRAEFIXE ab.
            gefunden = False
            for m in re.finditer(_such_pattern(suchwort), text_norm):
                if richtung and _eindeutig_andere_person(
                        text_norm, m.start(), m.end(), richtung, sprecher):
                    continue  # Fundstelle gehört eindeutig der anderen Person
                gefunden = True
                break
            if gefunden:
                treffer.append({
                    "limit": limit,
                    "quelle": quelle,
                    "matched_via": suchwort,
                })
                schon_gemeldet.add(limit)
                break
    return treffer


async def verletzungen(
    text: str,
    sklave_hard_limits: list[str] | None = None,
    domina_grenzen: list[str] | None = None,
    sprecher: str = "herrin",
) -> list[dict]:
    """Hauptfunktion. Prueft Text gegen beide Listen.

    `sprecher`: Perspektive des geprüften Textes – "herrin" für generierten
    Output (Default, strengste Lesart), "sub" für vom Sklaven verfasste Texte
    (Wunsch-/Präferenz-Erfassung). Nur relevant für Limits mit Richtungs-
    Qualifizierer ("X des Subs"), siehe _ATTR.

    Returns: Liste von Treffern, jeweils {"limit", "quelle", "matched_via"}.
             Leere Liste = Text ist sauber.
    """
    # Kein Text (z.B. grok lieferte None/"" bei Refusal/Fallback-Fehler) → nichts
    # zu prüfen. Fail-safe: niemals an _normalisiere(None) crashen lassen, sonst
    # propagiert eine Exception aus dem Sicherheits-Gate.
    if not text:
        return []
    # Defaults aus Profilen ziehen wenn nicht uebergeben
    if sklave_hard_limits is None:
        sklave_profile = await qdrant.get_user_profile("sklave") or {}
        sklave_hard_limits = sklave_profile.get("hard_limits", []) or []
    if domina_grenzen is None:
        domina_profile = await qdrant.get_user_profile("domina") or {}
        domina_grenzen = domina_profile.get("grenzen", []) or []

    if not sklave_hard_limits and not domina_grenzen:
        return []

    text_norm = _normalisiere(text)
    treffer = []
    treffer.extend(_prufe_liste(text_norm, sklave_hard_limits, "sklave_hard_limit", sprecher))
    treffer.extend(_prufe_liste(text_norm, domina_grenzen, "domina_grenze", sprecher))
    return treffer


async def generate_mit_limit_retry(
    prompt: str | tuple,
    sklave_hard_limits: list[str] | None = None,
    domina_grenzen: list[str] | None = None,
    system: str = "",
    **grok_kwargs,
) -> str | None:
    """Generiert via grok.simple und prueft den Output gegen die Limits.

    Bei einer Verletzung wird genau EINMAL mit verschaerftem Prompt re-generiert.
    Returns: sauberen Text, oder None wenn auch der zweite Versuch verletzt
    (Aufrufer entscheidet ueber Fallback/Abbruch). grok_kwargs (z.B.
    reasoning=True) werden an beide Versuche durchgereicht.

    `prompt` darf ein `(system, user)`-Tupel sein (Rueckgabeformat der
    Prompt-Builder); die Verschaerfung haengt dann am User-Teil.
    """
    from bot.services import grok  # lokal: zirkulaeren Import vermeiden

    if isinstance(prompt, tuple):
        builder_system, prompt = prompt
        system = system or builder_system

    # Eingetragene Abwesenheit (/abwesend) zentral an ALLE Aufgaben-/Vorschlags-
    # Generatoren durchreichen: Jobs laufen waehrend einer Abwesenheit bewusst
    # weiter, aber die Vorschlaege muessen wissen, dass er/sie nicht zu Hause ist.
    from bot.services import persona_config
    hinweis = persona_config.abwesenheit_hinweis()
    if hinweis:
        system = (system or "") + hinweis

    text = await grok.simple(prompt, system=system, **grok_kwargs)
    treffer = await verletzungen(text, sklave_hard_limits, domina_grenzen)
    if not treffer:
        return text

    # Nur Anzahl/Quelle loggen – die konkreten Limit-Begriffe (intim) gehören nicht in die Logs.
    _quellen = sorted({tr["quelle"] for tr in treffer})
    logger.warning("LLM-Output verletzt %d Grenze(n) [%s] – re-generiere.", len(treffer), ", ".join(_quellen))
    verboten = begriffe_zum_verbieten(treffer)
    verschaerft = (
        prompt + "\n\nWICHTIG: Der vorherige Vorschlag enthielt VERBOTENE BEGRIFFE: "
        + ", ".join(verboten)
        + ". Diese Begriffe, ihre Synonyme und alles thematisch Verwandte sind ABSOLUT TABU. "
        "Persoenliche Grenzen der Domina sind genauso unantastbar wie Hard Limits des Sklaven."
    )
    text = await grok.simple(verschaerft, system=system, **grok_kwargs)
    if await verletzungen(text, sklave_hard_limits, domina_grenzen):
        logger.error("LLM-Output auch nach Re-Generierung Grenzen-verletzend – verworfen.")
        return None
    return text


# ---------------------------------------------------------------------------
# Hilfsfunktionen fuer Aufrufer
# ---------------------------------------------------------------------------

def format_verletzungen(treffer: list[dict]) -> str:
    """Lesbare Zusammenfassung fuer Logs/Prompt-Verschaerfung."""
    if not treffer:
        return ""
    teile = []
    for t in treffer:
        qkurz = "Sklave-Hard-Limit" if t["quelle"] == "sklave_hard_limit" else "Domina-Grenze"
        teile.append(f"{t['limit']} ({qkurz}, ueber '{t['matched_via']}')")
    return "; ".join(teile)


def begriffe_zum_verbieten(treffer: list[dict]) -> list[str]:
    """Liste aller getroffenen Limit-Begriffe + ihrer Match-Phrasen zur
    Re-Generation. Dem LLM zu sagen 'verbiete X und alles thematisch Verwandte'."""
    aus = set()
    for t in treffer:
        aus.add(t["limit"])
        aus.add(t["matched_via"])
    return sorted(aus)
