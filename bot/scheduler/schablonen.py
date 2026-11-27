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


# --- Stimmung als Schluss-Begründung ---------------------------------------------
# Die Begründung „warum jetzt" zog das Modell fast jedes Mal aus der Stimmung und
# stellte sie als Satz ans Ende („Er ist gerade … drauf, deshalb …") – trotz der
# Sperre für verbrauchte Abschlüsse (Log-Durchsicht 24.11.2026). Stand so ein Satz
# in einem der letzten beiden Vorschläge, setzt die Stimmung als Begründung aus:
# Der Auftrag sagt es, und ein Satz, der trotzdem kommt, fällt hier weg.
_STIMMUNGS_ZEIT = (r"(?:gerade(?!\s+(?:erst|noch|so|mal|eben|dabei|dran))|momentan|im\s+Moment|zurzeit|zur\s+Zeit|"
                   r"aktuell|derzeit|right\s+now|currently)")
_STIMMUNGS_WORT = r"(?:drauf|gelaunt|gestimmt|stimmung|laune|mood)"


def _stimmungs_satz_re() -> re.Pattern:
    """Satz, der seinen Zustand im Moment feststellt: Subjekt ist die Sub-Seite
    (Pronomen oder eingestellte Anrede), dahinter „ist/wirkt gerade …" oder ein
    Stimmungs-Wort („… drauf")."""
    from bot.prompts import rollen
    try:
        from bot.services import persona_config
        anrede = " ".join((persona_config.sklave_anrede() or "").split())
    except Exception:
        anrede = ""
    subjekt = [re.escape(rollen.sub()["nom"]), "he", "she"]
    if anrede:
        subjekt.append(r"\s+".join(re.escape(w) for w in anrede.split()))
    return re.compile(
        r"^\W*(?:(?:der|die|dein|deine)\s+)?(?:" + "|".join(subjekt) + r")\s+(?:ist|wirkt|scheint|klingt|is|seems)\s+"
        r"(?:" + _STIMMUNGS_ZEIT + r"(?![\wäöüß])|[^.!?\n]{0,60}?(?<![\wäöüß])" + _STIMMUNGS_WORT + r"(?![\wäöüß]))",
        re.IGNORECASE)


def _schluss_saetze(text: str) -> list[str]:
    """Die Sätze des letzten Absatzes – dort steht die Begründung."""
    absaetze = [a for a in re.split(r"\n\s*\n", (text or "").strip()) if a.strip()]
    if not absaetze:
        return []
    return [x.strip() for x in re.split(r"(?<=[.!?…])\s+", absaetze[-1].strip()) if x.strip()]


def _stimmungs_satz(text: str) -> str:
    """Der Satz im Schluss-Absatz, der die Stimmung feststellt – leer, wenn keiner."""
    muster = _stimmungs_satz_re()
    return next((x for x in _schluss_saetze(text) if muster.search(x)), "")


def stimmung_setzt_aus(letzte_volltexte: list) -> bool:
    """Stand die Stimmung in einem der letzten beiden Vorschläge als Schluss-
    Begründung? Dann ist sie heute nicht dran (jüngster Vorschlag zuerst)."""
    return any(_stimmungs_satz(v) for v in (letzte_volltexte or [])[:2])


def _stimmungs_satz_entfernen(text: str) -> str:
    """Nimmt den Stimmungs-Satz aus dem Schluss-Absatz. Bleibt der Absatz dadurch
    leer, fällt er ganz weg; der Rest des Vorschlags bleibt unverändert."""
    satz = _stimmungs_satz(text)
    if not satz:
        return text
    kopf, trenner, schluss = (text or "").rstrip().rpartition("\n\n")
    rest = [x for x in _schluss_saetze(schluss) if x != satz]
    neu = " ".join(rest)
    if not kopf.strip() and not neu:
        return text                      # der Vorschlag bestünde nur aus diesem Satz
    return (kopf + trenner + neu).rstrip() if neu else kopf.rstrip()


# Log 26./27.11.2026: Zwei Tagesvorschläge hintereinander stützten sich auf
# dasselbe Gespräch (Muster „vor N Tagen hat er dich …“, am Folgetag N+1) – es
# war der einzige Eintrag unter den jüngsten Gesprächen. Gleiche Mechanik wie bei
# der Stimmung: Stand ein Rückblick in einem der letzten beiden Vorschläge, setzt
# er heute aus (Auftrag + Kandidaten-Wahl + ein Retry in scheduler/tiny_task).
_RUECKBLICK_RE = re.compile(
    r"(?<![\wäöüß])(?:"
    r"vor\s+(?:zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|ein\s+paar|einigen|\d+)\s+tagen"
    r"|neulich|letztens|vorgestern|letzte\s+woche|am\s+wochenende\s+hast"
    r"|(?:hast|hattest)\s+du\s+(?:[\wäöüß]+\s+){0,3}(?:gesagt|erzählt|geschrieben|gemeint|erwähnt)"
    r"|(?:hat|hatte)\s+(?:er|dich)\s+(?:[\wäöüß]+\s+){0,4}(?:gesagt|erzählt|geschrieben|gereizt|angestachelt|herausgefordert|provoziert)"
    r"|(?:\d+|two|three|four|five|a\s+few)\s+days\s+ago|the\s+other\s+day|last\s+week"
    r")(?![\wäöüß])",
    re.IGNORECASE)


def hat_rueckblick(text: str) -> bool:
    """Stützt sich der Vorschlag auf ein früheres Gespräch / Ereignis?"""
    return bool(_RUECKBLICK_RE.search(text or ""))


def rueckblick_setzt_aus(letzte_volltexte: list) -> bool:
    """Stand ein Rückblick in einem der letzten beiden Vorschläge? Dann ist heute
    keiner dran (jüngster Vorschlag zuerst)."""
    return any(hat_rueckblick(v) for v in (letzte_volltexte or [])[:2])


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


# Verrat von Sub-Wissen: die Dom-Seite soll Vorlieben/Wünsche als Einfall des
# Coach erleben, nicht als weitergetragenes Geständnis (coach_persona.tarnung()).
# Deterministisches Netz unter der Prompt-Regel – gleiche Mechanik wie oben.
# Pronomen der Sub-Seite kommen aus rollen (Konstellation ist Laufzeit-Config).
def _verrat_re() -> re.Pattern:
    from bot.prompts import rollen
    s = rollen.sub()
    nom, poss = re.escape(s["nom"]), re.escape(s["poss"].rstrip("e"))
    fuell = r"(?:ja |doch |eh |so |total |voll |richtig |echt |heimlich |insgeheim |schon lange )*"
    return re.compile(
        rf"\bw(?:ü|ue)nscht sich\b"
        rf"|\bhat sich\b[^.!?\n]{{0,40}}\bgew(?:ü|ue)nscht\b"
        rf"|\b{nom} w(?:ü|ue)rde {fuell}(?:so |zu )?gern"
        rf"|\btr(?:ä|ae)umt {fuell}(?:davon|von)\b"
        rf"|\bhat\b[^.!?\n]{{0,25}}\b(?:erz(?:ä|ae)hlt|verraten|gestanden|gebeichtet|angedeutet|anvertraut)\b"
        rf"|\b{nom} steht {fuell}(?:auf|drauf)\b(?! allen vieren| und\b| den knien)"
        rf"|\b{poss}\w* (?:geheim\w* |heimlich\w* |gr(?:ö|oe)(?:ß|ss)te\w* |neue\w* )?"
        rf"(?:wunsch|w(?:ü|ue)nsche\w*|fantasie\w*|vorlieb\w*|kinks?|traum|tr(?:ä|ae)ume\w*)\b"
        rf"|\blaut (?:{poss}\w* |dem )?(?:profil|dossier)\b"
        rf"|\b(?:in|aus) {poss}\w* (?:profil|dossier)\b"
        rf"|\bauf {poss}\w* (?:liste|wunschliste)\b",
        re.IGNORECASE)


# Nachsatz für den einen Retry nach einem Verrats-Fund (Generatoren + Chat).
TARN_NACHSATZ = (
    "\n\nACHTUNG: Dein letzter Entwurf hat verdecktes Wissen als Geständnis weitergetragen "
    "(Muster: „er wünscht sich“, „er würde gern“, „sein Wunsch“, „seine Vorliebe“, „er steht ja "
    "auf“, „hat erzählt“). Formuliere neu: der Inhalt darf bleiben, aber ausschließlich als DEIN "
    "eigener spontaner Einfall oder als Vermutung aus Beobachtbarem – nichts zitieren, nichts "
    "der Sub-Seite zuschreiben, kein Profil-Abgleich."
)


def verraet_sub_wissen(text: str) -> bool:
    """True, wenn der Text der Dom-Seite ein Sub-Geständnis weiterträgt
    („er wünscht sich …", „sein Wunsch", „er steht ja auf …", „hat mir erzählt")."""
    return _verrat_re().search(text or "") is not None


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


# ---------------------------------------------------------------------------
# Einweg-Mitteilungen (Stimmungs-Hinweis an die andere Seite): ein Gedanke zu
# EINER Antwort, auf den niemand antworten muss. Zwei Marotten des Modells
# überleben die Prompt-Regel (Echt-Test 21.11.2026) und fallen deshalb hier weg:
#  - behauptete Gewohnheiten („das hilft meistens", „wie immer", „mal wieder"):
#    der Auftrag kennt eine einzige Antwort, keine Vorgeschichte;
#  - eine Rückfrage am Ende: der Coach-Stil lädt zum Weiterreden ein, die
#    Mitteilung soll aber ohne Antwort auskommen.
_NW = r"(?<![\wäöüß])"          # kein Wortzeichen davor
_NN = r"(?![\wäöüß])"           # kein Wortzeichen danach
# Wendungen (Gruppe „wendung"): lassen sich überall herauslösen. Einzelne
# Häufigkeits-Adverbien (Gruppe „adverb") stehen im Deutschen gern an erster
# Stelle eines Hauptsatzes – dort bräche das bloße Streichen die Wortstellung.
_GEWOHNHEIT_RE = re.compile(
    _NW + r"(?:(?P<wendung>"
    r"bei\s+(?:ihm|ihr)\s+(?:meistens|meist|oft|immer|h[äa]e?ufig|normalerweise)|"
    r"(?:so\s+)?wie\s+(?:immer|so\s+oft|[üu]e?blich|gewohnt)|"
    r"mal\s+wieder|als\s+sonst|sonst\s+(?:auch|immer)|as\s+(?:always|usual)"
    r")|(?P<adverb>"
    r"erfahrungsgem[äa]e?(?:ß|ss)|normalerweise|[üu]e?blicherweise|in\s+(?:aller|der)\s+Regel|jedes\s+Mal|"
    r"meistens|meist|(?<!zu\s)(?<!so\s)(?<!wie\s)(?<!sehr\s)(?:oft|h[äa]e?ufig)|"
    r"usually|normally|typically|most\s+of\s+the\s+time|every\s+time|"
    r"(?<!too\s)(?<!so\s)(?<!how\s)often"
    r"))" + _NN, re.I)
_TRENNER = r"(?:\s*[,;:–—]\s*|\s+-\s+)"
# Angehängte Berufung auf ihre Gewohnheit oder ihr Wissen („… – so wie du das
# sonst auch machst", „…, du kennst ihn ja"): fällt als ganzer Teilsatz weg.
_WIE_DU_SONST_RE = re.compile(
    _TRENNER + r"(?:so\s+)?wie\s+du\s+(?:das|es)\s+[^,.!?–—\n]{0,40}?"
    + _NW + r"(?:sonst|immer|meistens|meist|oft|gewohnt)" + _NN + r"[^,.!?–—\n]{0,25}?"
    + _NW + r"(?:tust|machst|bist)" + _NN, re.I)
_KENNST_JA_RE = re.compile(
    _TRENNER + r"(?:du\s+kennst\s+(?:ihn|sie)\s+ja|(?:so\s+)?wie\s+du\s+(?:ihn|sie)\s+kennst)" + _NN, re.I)
_SATZANFANG_DAVOR_RE = re.compile(r"(?:\A|[.!?…][\"“”«»']?\s+|\n)[\s\"„“«»'(*_]*\Z")
_TRENNER_DAVOR_RE = re.compile(r"(?:\s*[,;:–—]|\s+-)\s*\Z")
_TEILSATZ_ENDE_RE = re.compile(r"[,;:–—]|\s-\s|[.!?…]")
_BUCHSTABE_RE = re.compile(r"[A-Za-zÄÖÜäöüß]")


def _gross_ab_erstem_buchstaben(text: str) -> str:
    m = _BUCHSTABE_RE.search(text)
    if not m:
        return text
    return text[:m.start()] + text[m.start()].upper() + text[m.start() + 1:]


def _gewohnheiten_entfernen(text: str) -> str:
    """Nimmt Häufigkeits- und Gewohnheits-Behauptungen aus einer Mitteilung.
    Mitten im Teilsatz fällt nur das Wort weg („das bringt ihn meistens zum
    Reden" → „das bringt ihn zum Reden"). Steht es am Anfang eines Satzes oder
    Teilsatzes, bräche das die Wortstellung – dort fällt der Teilsatz weg.
    Bleibt zu wenig übrig, bleibt der Text unverändert."""
    original = (text or "").strip()
    t = _KENNST_JA_RE.sub("", _WIE_DU_SONST_RE.sub("", original))
    for m in reversed(list(_GEWOHNHEIT_RE.finditer(t))):   # von hinten: Positionen bleiben gültig
        a, b = m.start(), m.end()
        davor, danach = t[:a], t[b:]
        ende = _TEILSATZ_ENDE_RE.search(danach)
        if _SATZANFANG_DAVOR_RE.search(davor):
            # Satzanfang: bis einschließlich des nächsten Trenners weg, der Rest beginnt groß
            rest = danach[ende.end():] if ende else ""
            t = davor + _gross_ab_erstem_buchstaben(rest.lstrip(" \t"))
        elif m.group("adverb") and (tr := _TRENNER_DAVOR_RE.search(davor)):
            # Adverb am Anfang eines Teilsatzes: Trenner davor und der Teilsatz weg, das Folgende bleibt
            t = davor[:tr.start()] + (danach[ende.start():] if ende else "")
        else:
            t = davor.rstrip(" \t") + " " + danach.lstrip(" \t")
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"[ \t]+([,.;:!?…])", r"\1", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t) < 40 or len(t) < 0.4 * len(original):
        return original
    return t


# Fragen, die den Gedanken selbst tragen (ein Vorschlag in Frageform), bleiben.
_VORSCHLAGS_FRAGE_RE = re.compile(
    r"^[\s\"„“«»'(*_]*(?:wie\s+w[äa]e?r|warum\s+nicht|vielleicht|was,?\s+wenn|und\s+wenn|frag\w*\s|"
    r"how\s+about|why\s+not|what\s+if|maybe|ask\s)", re.I)


def _rueckfrage_am_ende_entfernen(text: str, mindest_rest: int = 60) -> str:
    """Schneidet Fragesätze am ENDE einer Mitteilung ab, die eine Antwort der
    Empfängerin erwarten. Es bleiben: ein Vorschlag in Frageform („Wie wär's,
    wenn …?"), eine Frage in Anführungszeichen (ein Satz, den SIE sagen
    könnte) und alles, wenn danach zu wenig übrig bliebe."""
    t = (text or "").strip()
    teile = re.split(r"((?<=[.!?…])\s+)", t)        # Satz, Lücke, Satz, Lücke, …, Satz
    n = (len(teile) + 1) // 2                       # Zahl der Sätze
    behalten = n
    while behalten > 0 and not _BUCHSTABE_RE.search(teile[2 * (behalten - 1)]):
        behalten -= 1                               # Emoji/Zeichen ganz am Schluss
    gefunden = False
    while behalten > 0:
        satz = teile[2 * (behalten - 1)]
        if not re.search(r"\?[\s*_]*$", satz) or _VORSCHLAGS_FRAGE_RE.search(satz):
            break
        behalten -= 1
        gefunden = True
    if not gefunden:
        return t
    rest = "".join(teile[:2 * behalten - 1]).rstrip() if behalten else ""
    return rest if len(rest) >= mindest_rest else t


def mitteilung_bereinigen(text: str) -> str:
    """Netz für Einweg-Mitteilungen: keine behaupteten Gewohnheiten, keine
    Rückfrage am Ende. Die Prompt-Regeln dazu stehen in
    prompts/followup/bausteine (STIMMUNG_BEIM_GESAGTEN, MITTEILUNG_OHNE_RUECKFRAGE)."""
    return _rueckfrage_am_ende_entfernen(_gewohnheiten_entfernen(text))


def _eigener_name_entfernen(text: str) -> str:
    """Schneidet den eigenen Namen als Einstieg einer fertigen Coach-Nachricht
    weg („<Name>, kurz und bündig: …" → „Kurz und bündig: …"). Der Coach
    kennt seinen Namen aus dem Rahmen-Block; als erstes Wort liest er sich für
    die Dom-Seite wie eine Anrede an sie (Live-Befund 17.11.2026). Die
    Prompt-Regel dazu steht in coach_persona._namen_block – das hier ist das
    Netz darunter. Ohne Namen oder wenn danach nichts übrig bliebe: unverändert."""
    try:
        from bot.services import persona_config  # lazy: zirkelfrei
        name = (persona_config.bot_name() or "").strip()
    except Exception:
        name = ""
    t = text or ""
    if not name:
        return t
    m = re.match(rf"(\W*)(?:(?:hey|hi|hallo|na|so)[\s,]+)?{re.escape(name)}(?![\wäöüß])\s*[,:;!.–—-]*\s*",
                 t, re.IGNORECASE)
    if not m or len(t[m.end():].strip()) < 20:
        return t
    rest = t[m.end():]
    return m.group(1) + rest[:1].upper() + rest[1:]


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
    if verraet_sub_wissen(text):
        funde.append('Verrat von verdecktem Wissen („er wünscht sich / würde gern / steht auf / '
                     'sein Wunsch / hat erzählt") – nur als DEIN eigener Einfall formulieren')
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
    # Schauplatz als Zutat (Live-Befund 15.11.2026): derselbe Ort trug mehrere
    # Vorschläge in einer Woche, obwohl Kategorien und Praktiken wechselten.
    ("Dusche/Bad als Schauplatz",
     re.compile(r"dusch|badewanne|wasserstrahl", re.I),
     frozenset()),
    # Gesten als Zutat (Live-Befund 21.11.2026): Steht ein Stoff auf der
    # Sperrliste, behält das Modell gern die Geste und tauscht nur den Stoff –
    # die Wiederholung bleibt, die Liste sieht sie nicht. Solche Gesten stehen
    # deshalb selbst hier, unabhängig davon, womit sie ausgeführt werden.
    ("Tropfen aufs Gesicht (egal womit)",
     # im selben Satz – oder das Gesicht als Ziel am Satzende und die Tropfen im nächsten
     re.compile(r"tropf\w*[^.!?\n]{0,60}(?:gesicht|stirn|wange)|(?:gesicht|stirn|wange)[^.!?\n]{0,60}tropf|"
                r"(?:auf|aufs|ins|in|über)\s+(?:\w+\s+)?(?:gesicht|stirn|wange)\w*[.!?]\s+[^.!?\n]{0,40}tropf", re.I),
     frozenset({"Speichelspiel"})),
    ("Stumme Zeichen per Finger oder Antippen",
     re.compile(r"finger\w*[^.!?\n]{0,30}(?:schulter|hei(?:ß|ss)t)|finger\w*\s+heb|"
                r"(?:tipp|klopf)\w*[^.!?\n]{0,30}schulter|schulter[^.!?\n]{0,30}(?:tipp|klopf)|"
                r"fingerschnipp|schnipp\w*[^.!?\n]{0,15}finger", re.I),
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
    for k in kategorie_logik.vorliebe_kategorien(text):
        cluster |= kategorie_logik.zu_cluster().get(k, set())
    return cluster


def _kombi_vorlieben_wahl(vorlieben: list, dislikes: list | None = None,
                          volltexte: list | None = None) -> list | None:
    """Zwei Freitext-Vorlieben für den KOMBI-IMPULS (Nutzer-Wunsch 06.09.):
    bevorzugt eine situative Zeile (Alltags-Ritual als Bühne) als Anker plus
    einen Partner aus einem ANDEREN Themen-Cluster – die Kategorie-Rotation
    erzeugt solche Querverbindungen sonst nie. Zeilen, die auf Dislike-
    Kategorien mappen, sind ausgeschlossen. Zeilen bleiben wörtlich erhalten
    (Richtungs-/Bedingungs-Zusätze dürfen nicht abhandenkommen).

    `volltexte` = die letzten Vorschläge: Zeilen, deren Zutat/Schauplatz dort
    schon vorkam, setzen aus (Live-Befund 15.11.2026: es gab nur zwei situative
    Zeilen, beide mit demselben Schauplatz – jeder Kombi-Tag spielte dort, und
    die Kombi-Befreiung hebelte die Zutaten-Sperrliste gleich mit aus). Bleiben
    so weniger als zwei Zeilen übrig, gilt die ungefilterte Liste."""
    dislikes_set = set(dislikes or [])
    kandidaten = [
        v for v in (vorlieben or [])
        if isinstance(v, str) and len(v.strip()) >= 3
        and not (kategorie_logik.kategorien_in_text(v) & dislikes_set)
    ]
    if len(kandidaten) < 2:
        return None
    verbraucht = [rx for _, rx, _ in _ZUTATEN_MUSTER
                  if any(rx.search(t or "") for t in (volltexte or []))]
    frisch = [v for v in kandidaten if not any(rx.search(v) for rx in verbraucht)]
    if len(frisch) >= 2:
        kandidaten = frisch
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
