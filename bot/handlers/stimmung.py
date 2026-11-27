"""
Stimmungs-Tracking.
Sklave kann jederzeit /stimmung schreiben wenn er sich mitteilen möchte.
Kein täglicher aufdringlicher Job mehr – nur wenn STIMMUNG_ENABLED=true.

OHNE CHAT-MODE (06.10.2026): Die Frage setzte früher den Mode 'stimmung' und
hielt ihn bis zu 2 h. Blieb sie unbeantwortet, galt der Sub-Chat so lange als
belegt – der Spiel-Impuls fiel jeden Nachmittag aus, das Follow-up des Tages
auch. Jetzt trägt die Frage fünf Stimmungs-Knöpfe (ein Tipp genügt), und für
eine GETIPPTE Antwort gibt es nur noch eine Merkmarke im State
(`stimmung_offen_bis`): Die nächste freie Nachricht im Fenster zählt wie
bisher als Stimmung, aber kein Job sieht den Chat mehr als belegt an.

ANTWORTEN PASSEND ZUR FRAGE (17.11.2026): Die Frage wird täglich frisch und
oft bildhaft formuliert – auf ein „eher stiller See oder voller Bahnhof?"
passt keine feste Skala von mies bis aufgedreht, und ohne passenden Knopf
bleibt so eine Frage unbeantwortet. Jetzt erzeugt ein zweiter,
kurzer LLM-Aufruf zu jeder Frage vier Antworten zum Antippen samt Klartext
(prompts/followup.stimmung_antworten). Angezeigt wird die Antwort, gespeichert
der Klartext – der steht später als „Aktuelle Stimmung" in anderen Prompts und
muss ohne die Frage verständlich sein. Die fünf festen Knöpfe bleiben als
Rückfall (Standardfrage, LLM-Fehler, unbrauchbare Ausgabe).

KLARTEXT BLEIBT BEIM GESAGTEN (21.11.2026): Der Klartext stammt vom Modell,
die Person sieht ihn nie – er darf deshalb nicht mehr sagen als das Etikett.
Prompt-Regel in stimmung_antworten, darunter ein Wort-Netz (`_sagt_mehr`) mit
einem Nachbesserungs-Versuch; hilft der nicht, fällt der überzogene Klartext
weg und gespeichert wird das Etikett. Der Hinweis an die andere Seite zeigt
Frage und Antwort wörtlich (`_hinweis_text`), nicht den Klartext; der Gedanke
darunter läuft durch `schablonen.mitteilung_bereinigen` (keine behaupteten
Gewohnheiten, keine Rückfrage am Ende).

KLARTEXT NENNT DEN BEZUG (24.11.2026): Bewertet eine Antwort nur, was die
Frage beschreibt („passt", „stimmt nicht"), stand als Klartext eine
bloße Bewertung da – ohne die Frage unverständlich und von den Lesern als
Aussage über die Person gedeutet. Jetzt verlangt der Auftrag den Bezug, ein
Netz (`_klartext_bezugslos`) bessert einmal nach, und ohne brauchbaren Klartext
steht die Frage hinter der Antwort (`_mit_frage`, auch bei getippten
Antworten). Die Frage selbst soll nichts mehr feststellen, worauf sich nur
reagieren lässt (`_stellt_etwas_fest`).

REAKTION WECHSELT DIE FORM (22.11.2026): Die Reaktion der Herrin auf die
Antwort war jeden Tag gleich gebaut und kündigte meist etwas an, das nie kam.
Jetzt wählt der Code eine von fünf Bauformen (nie eine der letzten drei), der
Auftrag nennt die letzten Reaktionen und verbietet Ankündigungen; darunter ein
Netz (`_reaktion_maengel`) mit einem Nachbesserungs-Versuch. Die Reaktion liegt
samt Bauform am Stimmungs-Eintrag (`reaktion`, `reaktion_form`).
"""
import difflib
import logging
import random
import re
import time
import uuid
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot import config, state
from bot.services import paare, modi
from bot.services import qdrant, grok, telegram_helper
from bot.handlers.sklave import _normalisiere
from bot.prompts import followup as fp
from bot.messages import t

# Vanilla (Etappe 2): die Stimmungsfrage geht an BEIDE Personen, Reaktion und
# Hinweis kommen von der neutralen Stimme (prompts/vanilla.py). `rolle` ist
# überall der Slot (paare.ROLLE_*), im BDSM/Kink-Modus immer der Sub.

logger = logging.getLogger(__name__)

# Kein Modul-Level t() (Review D8/N8): beim Import gibt es keinen Paar-
# Kontext – ein EN-Zweitpaar bekäme sonst die eingefrorene DE-Fassung.


def _zu_aehnlich(frage: str, vorherige: list[str]) -> bool:
    """Deterministischer Wiederholungs-Detektor: gleicher Satzanfang oder hohe
    Gesamt-Ähnlichkeit zu einer der letzten Fragen. Die Sperr-Liste im Prompt
    allein reicht nicht – Muster wie beim Sklave-Chat (_ist_echo, Befund
    02.07.): Prompt-Regeln verlieren gegen die Lieblings-Formulierung."""
    n = _normalisiere(frage)
    if not n:
        return False
    for v in vorherige:
        vn = _normalisiere(v)
        if not vn:
            continue
        if n.split()[:3] == vn.split()[:3]:
            return True
        if difflib.SequenceMatcher(None, n, vn).ratio() >= 0.7:
            return True
    return False


_VERSUCHE = 3

# Ein an die Frage gehängtes „…, du?" – Ersatz-Anrede, wenn der Prompt die
# eigentliche Anrede aussetzt (seit 16.11.2026 fast täglich so gesendet).
_ANGEHAENGTES_DU_RE = re.compile(r",\s*du(?=\s*[?.!…]*\s*$)", re.I)


def _ohne_angehaengtes_du(frage: str) -> str:
    return _ANGEHAENGTES_DU_RE.sub("", frage or "")


# Andere Ersatz-Anreden („…, mein Süßer?", „Schatz, …"): Die Persona kennt
# genau EINE Anrede; setzt der Prompt sie aus, weicht das Modell gern auf einen
# Kosenamen aus (live 20.11.2026). Bewusst eine kurze Liste gängiger Kosewörter –
# ein allgemeines „Komma + Wort am Satzende" träfe auch echte Satzteile.
_KOSEWORT = (r"(?:mein(?:e[rs]?)?\s+)?(?:lieb(?:e[rs]?|ste[rs]?|ling)|kleine[rs]?|süße[rs]?|suesse[rs]?|"
             r"schatz|schätzchen|hübsche[rs]?|freundchen|sklav(?:e|in))(?:\s+[a-zäöüß]+)?")
_KOSE_AM_ENDE_RE = re.compile(rf",\s*(?P<wort>{_KOSEWORT})(?=\s*[?.!…]*\s*$)", re.I)
_KOSE_AM_ANFANG_RE = re.compile(rf"^\s*(?P<wort>{_KOSEWORT})\s*,\s*", re.I)
_KOSE_MITTEN_RE = re.compile(rf",\s*(?P<wort>{_KOSEWORT})\s*(?=,)", re.I)


def _ohne_ersatz_anrede(frage: str, anrede: str = "") -> str:
    """Entfernt einen Kosenamen, der nicht die eingestellte Anrede ist – am
    Satzende, am Anfang oder zwischen zwei Kommas. Die eingestellte Anrede
    selbst bleibt unangetastet."""
    text = frage or ""
    eigene = " ".join((anrede or "").lower().split())

    def _weg(m: re.Match) -> str:
        return m.group(0) if " ".join(m.group("wort").lower().split()) == eigene else ""

    text = _KOSE_MITTEN_RE.sub(_weg, text)
    text = _KOSE_AM_ENDE_RE.sub(_weg, text)
    neu = _KOSE_AM_ANFANG_RE.sub(_weg, text)
    if neu != text and neu:
        neu = neu[:1].upper() + neu[1:]
    return neu

# Eine Frage, die erst etwas über den Tag oder die Person feststellt und dann nur
# wissen will, wie sich „das" anfühlt: Darauf lässt sich bloß mit „passt" oder
# „stimmt nicht" antworten (Live-Befund 24.11.2026). Erkannt an einem
# Aussagesatz vor der Frage und einem Rückbezug in der Frage selbst.
_RUECKBEZUG_RE = re.compile(r"(?<![\wäöüß])(?:das|dies|damit|dabei|darin|davon|dazu|daran)(?![\wäöüß])", re.I)


def _stellt_etwas_fest(frage: str) -> bool:
    saetze = [x.strip() for x in re.split(r"(?<=[.!?…])\s+", (frage or "").strip()) if x.strip()]
    if len(saetze) < 2 or not saetze[-1].endswith("?"):
        return False
    return any(x.endswith(".") for x in saetze[:-1]) and bool(_RUECKBEZUG_RE.search(saetze[-1]))


# Merkmarke statt Mode: bis wann eine getippte Nachricht als Stimmungs-Antwort
# zählt (Dauer = STALE_STIMMUNG_SECONDS, wie früher der Mode).
_OFFEN = "stimmung_offen_bis"
# Wortlaut der offenen Frage – eine getippte Antwort wird damit gedeutet.
_OFFEN_FRAGE = "stimmung_offen_frage"
_STUFEN = ("1", "2", "3", "4", "5")
# Längere Etiketten schneidet Telegram auf dem Handy ab (ein Knopf je Zeile).
_KNOPF_MAX = 40
_MIN_OPTIONEN = 2
_OPTIONEN_VERSUCHE = 2
_KLARTEXT_SUBJEKT_RE = re.compile(
    r"^(?:sie|er|die person|der sub|die sub)\s+(?:ist|wirkt|fühlt sich|fühlt|spürt|hat|empfindet)\s+", re.I)


def _tasten(frage_id: str | None = None, optionen: list[dict] | None = None) -> InlineKeyboardMarkup:
    """Antworten zum Antippen. Mit `optionen` (zur Frage erzeugt): ein Knopf je
    Zeile, callback `stimmung:w:<frage_id>:<nr>`. Sonst die fünf festen
    Stimmungen von unten nach oben (`stimmung:<n>`). Gespeichert wird in
    beiden Fällen nicht das Etikett, sondern der ausführlichere Klartext – der
    steht später in den Prompts (Tiny-Task, Dossier) und soll für sich
    verständlich sein."""
    if frage_id and optionen:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(o["knopf"], callback_data=f"stimmung:w:{frage_id}:{i}")]
            for i, o in enumerate(optionen)])
    knoepfe = [InlineKeyboardButton(t(f"BUTTON_STIMMUNG_{n}"), callback_data=f"stimmung:{n}") for n in _STUFEN]
    return InlineKeyboardMarkup([knoepfe[:3], knoepfe[3:]])


def _optionen_lesen(roh: str) -> list[dict]:
    """LLM-Ausgabe → [{"knopf", "klartext"}]. Unbrauchbares fällt raus (zu lang
    für einen Knopf, ohne Klartext, doppelt); bleiben weniger als zwei
    Antworten, gibt es keine – dann stehen die festen Knöpfe unter der Frage."""
    try:
        daten = grok.parse_json(roh)
    except Exception:
        return []
    eintraege = daten.get("antworten") if isinstance(daten, dict) else daten
    if not isinstance(eintraege, list):
        return []
    optionen, gesehen = [], set()
    for e in eintraege:
        if not isinstance(e, dict):
            continue
        knopf = " ".join(str(e.get("knopf") or "").split()).strip(" \"'„“”").rstrip(".").strip()
        klartext = " ".join(str(e.get("klartext") or "").split()).strip(" \"'„“”").rstrip(".").strip()
        # Der Klartext steht später als „Aktuelle Stimmung: …" in anderen Prompts:
        # ein führendes „Sie ist/Er fühlt sich" (mit geratenem Geschlecht) fällt weg.
        klartext = _KLARTEXT_SUBJEKT_RE.sub("", klartext)
        schluessel = _normalisiere(knopf)
        if not (2 <= len(knopf) <= _KNOPF_MAX) or len(klartext) < 3 or not schluessel or schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        optionen.append({"knopf": knopf, "klartext": klartext[:160]})
        if len(optionen) >= fp.ANTWORT_ANZAHL:
            break
    return optionen if len(optionen) >= _MIN_OPTIONEN else []


# --- Netz unter der Klartext-Regel -------------------------------------------
# Die Person tippt nur das Etikett an und sieht den Klartext nie; gespeichert
# und weitergereicht wird aber der Klartext. Sagt er mehr als das Etikett –
# einen Befund oder eine Verstärkung, die weder auf dem Knopf noch in der Frage
# steht –, landet in den Prompts und im Hinweis an die andere Seite etwas, das
# niemand gesagt hat (Live-Befund 21.11.2026: aus einer unbestimmten Antwort
# wurde ein Befund, der noch den Aufgaben-Vorschlag des Abends einfärbte).
# Erkannt wird das an Wörtern, nicht am Sinn: lieber einmal zu oft nachbessern
# lassen als einen Befund durchwinken. Alltägliche Stimmungswörter (müde,
# unruhig, angespannt …) stehen bewusst NICHT hier – ohne sie ließe sich ein
# Bild nicht übersetzen. Je Familie: gilt als gedeckt, wenn Knopf oder Frage
# ein Wort derselben Familie enthalten. Die meisten Stämme zählen auch mitten
# im Wort (Zusammensetzungen); wo das falsche Freunde träfe („gemeinsam",
# „unbelastet", „Fürsorge"), nur am Wortanfang (_WA) bzw. ohne die Vorsilbe.
_WA = r"(?<![\wäöüß])"
_BEFUND_FAMILIEN: tuple[re.Pattern, ...] = tuple(
    re.compile(muster, re.I) for muster in (
        r"[üu]e?berforder|[üu]e?berlast|[üu]e?berw[äa]e?ltig|overwhelm",
        (r"(?<!für)(?<!vor)(?<!ver)(?<!ent)(?<!be)sorge(?!n?frei)|" + _WA + r"besorgt|" + _WA + r"besorgnis|"
         r"kummer|" + _WA + r"worr(?:y|ie)"),
        (r"angst(?!frei)|" + _WA + r"(?:ver)?(?:ä|ae)ngst|panik|" + _WA + r"panisch|" + _WA + r"anxi|"
         + _WA + r"panic"),
        r"krise|verzweif|ausgebrannt|burn.?out|crisis|desperat|despair",
        r"depress|deprimi|niedergeschlagen|apath",
        r"hoffnungslos|hilflos|ziellos|orientierungslos|" + _WA + r"mutlos|hopeless|helpless|aimless",
        _WA + r"(?:ver)?einsam|" + _WA + r"lonel",
        _WA + r"traurig|" + _WA + r"trauer|" + _WA + r"grie[fv]",
        r"(?<!un)belast|" + _WA + r"distress",
    ))
_STEIGERUNG = (r"sehr|stark(?:e[rsnm]?)?|st[äa]e?rker\w*|extrem\w*|v[öo]e?llig\w*|total\w*|massiv\w*|heftig\w*|"
               r"akut\w*|enorm\w*|hochgradig\w*|[äa]e?u(?:ß|ss)erst|zutiefst|"
               r"very|extremely|totally|completely|deeply|strongly|severe\w*|utterly|intense\w*|highly")
_STEIGERUNG_RE = re.compile(_WA + r"(?:" + _STEIGERUNG + r")(?![\wäöüß])", re.I)
# Auf dem Knopf zählt auch die Umgangssprache als Verstärkung – wer „gar
# nichts" oder „voll durch" antippt, hat selbst zugespitzt.
_KNOPF_STEIGERUNG_RE = re.compile(
    _WA + r"(?:" + _STEIGERUNG + r"|mega|richtig|ziemlich|komplett\w*|absolut\w*|voll\w*|ganz|riesig\w*|"
    r"riesen\w*|randvoll|nur noch|gar|[üu]e?berhaupt|kaum|null|nichts mehr|am ende|echt|super\w*|wahnsinnig\w*|"
    r"really)(?![\wäöüß])", re.I)
# Eine Verneinung davor im selben Satzteil dreht den Befund um („keine Sorgen"),
# ebenso eine direkt hinter dem Doppelpunkt der Form „Thema: Aussage".
_VERNEINT_RE = re.compile(
    _WA + r"(?:kein\w*|nicht|ohne|nie|kaum|wenig\w*|frei von|weder|no|not|without|hardly|free of)"
    r"(?![\wäöüß])[^,;:–—/]*$", re.I)
_VERNEINT_DANACH_RE = re.compile(
    r"^[\wäöüß]*\s*:\s*(?:kein\w*|nicht|ohne|nie|kaum|wenig\w*|gering\w*|null|none|no|not|low|little)"
    r"(?![\wäöüß])", re.I)


def _sagt_mehr(knopf: str, klartext: str, frage: str = "") -> list[str]:
    """Wörter im Klartext, die über Knopf und Frage hinausgehen: ein Befund,
    den keiner von beiden nennt, oder eine Verstärkung, die der Knopf nicht
    hat. Leer = der Klartext bleibt beim Gesagten (soweit an Wörtern prüfbar)."""
    gedeckt = f"{knopf or ''} {frage or ''}"
    klartext = klartext or ""
    funde: list[str] = []
    for familie in _BEFUND_FAMILIEN:
        if familie.search(gedeckt):
            continue
        for m in familie.finditer(klartext):
            if _VERNEINT_RE.search(klartext[:m.start()]) or _VERNEINT_DANACH_RE.match(klartext[m.end():]):
                continue
            funde.append(_ganzes_wort(klartext, m.start(), m.end()))
    if not _KNOPF_STEIGERUNG_RE.search(knopf or ""):
        funde.extend(m.group(0) for m in _STEIGERUNG_RE.finditer(klartext))
    return list(dict.fromkeys(funde))


_WORTZEICHEN_RE = re.compile(r"[\wäöüß]")


def _ganzes_wort(text: str, start: int, ende: int) -> str:
    """Das Wort um einen Treffer herum – der Stamm allein („sorge" aus einer
    Zusammensetzung) sagt dem Modell beim Nachbessern nicht, was gemeint ist."""
    while start > 0 and _WORTZEICHEN_RE.match(text[start - 1]):
        start -= 1
    while ende < len(text) and _WORTZEICHEN_RE.match(text[ende]):
        ende += 1
    return text[start:ende]


# --- Netz: Klartext ohne Bezug -------------------------------------------------
# Bewertet eine Antwort nur, was die Frage beschreibt („passt", „stimmt
# nicht"), und steht im Klartext bloß ein allgemeines Thema mit dieser
# Bewertung dahinter, dann ist er ohne die Frage nicht zu verstehen. Gespeichert
# stand so „Thema: falsch" als aktuelle Stimmung – Reaktion und Vorschlag lasen
# das als Aussage über die Person (Live-Befund 24.11.2026).
# Als benannter Bezug gilt ein Thema mit wenigstens einem eigenen Inhaltswort
# („Volles Haus: passt nicht") – Füllwörter, Bewertungswörter und die allgemeinen
# Stimmungs-Hauptwörter zählen dafür nicht („Passt zu Kopf: ja" bleibt bezugslos).
_BEWERTUNGS_KERN = (
    r"ja|nein|doch|jein|passt|passend|unpassend|stimmt|stimmig|unstimmig|trifft\s+(?:es\s+)?(?:nicht\s+)?zu|"
    r"richtig|falsch|beides|weder\s+noch|keins?\s+von\s+beide[mn]|das\s+(?:erste|zweite|letzte|eine|andere)|"
    r"ersteres|letzteres|genau(?:\s+so)?|genauso|ebenso|auch(?:\s+so)?|dasselbe|das\s+gleiche|gleich|"
    r"unver(?:ä|ae)ndert|wie\s+immer|(?:irgendwo\s+)?dazwischen|mittendrin|kommt\s+drauf\s+an|"
    r"yes|no|both|neither|fits|wrong|right|same"
)
_BEWERTUNG_RE = re.compile(
    r"^(?:(?:es|das)\s+)?(?:(?:f(?:ü|ue)hlt\s+sich|ist|wirkt|feels|is)\s+)?"
    r"(?:(?:eher|ganz|gar|(?:ü|ue)berhaupt|absolut|voll|total|ziemlich|so|kaum|nicht|gut|genau|irgendwie|not|quite)\s+)*"
    r"(?:" + _BEWERTUNGS_KERN + r")"
    r"(?:\s+(?:nicht|gut|genau|zu\s+mir|f(?:ü|ue)r\s+mich|an|so|eher|schon|auch|at\s+all))*$", re.I)
_THEMA_OHNE_GEWICHT = frozenset((
    # Füllwörter
    "der die das den dem des ein eine einen einem einer zu zum zur auf in im an am mit und oder für von vom bei "
    "aus heute heutig heutige heutiger heutiges heutigen gerade jetzt noch schon eher ganz sehr mein meine dein "
    "deine the a of to my today "
    # Bewertungswörter
    "passt stimmt fühlt trifft fits feels "
    # allgemeine Stimmungs-Hauptwörter
    "stimmung gefühl gefühle befinden laune lage zustand tag kopf gedanken körper kraft energie lust antwort "
    "frage moment mood feeling day head"
).split())
_FRAGE_IM_EINTRAG_MAX = 200


def _klartext_bezugslos(klartext: str) -> bool:
    """Bloße Bewertung hinter einem Thema ohne eigenes Inhaltswort?"""
    thema, trenner, aussage = (klartext or "").partition(":")
    if not trenner:
        thema, aussage = "", klartext or ""
    aussage = _normalisiere(aussage)
    if not aussage or not _BEWERTUNG_RE.match(aussage):
        return False
    return not any(len(w) >= 3 and w not in _THEMA_OHNE_GEWICHT for w in _normalisiere(thema).split())


def _ohne_bezug(optionen: list[dict]) -> list[str]:
    """Die Klartexte, die offen lassen, worauf sich ihre Antwort bezieht."""
    return [o["klartext"] for o in optionen if _klartext_bezugslos(o.get("klartext", ""))]


def _mit_frage(antwort: str, frage: str) -> str:
    """Eine Antwort ohne Klartext so, dass sie für sich steht: mit ihrer Frage
    dahinter. Rund zehn Stellen lesen `zusammenfassung` als „Aktuelle Stimmung"
    und kennen die Frage nicht – ein „passt schon" allein sagt ihnen nichts."""
    frage = " ".join((frage or "").split())
    if not frage:
        return antwort
    if len(frage) > _FRAGE_IM_EINTRAG_MAX:
        frage = frage[:_FRAGE_IM_EINTRAG_MAX - 1].rstrip() + "…"
    return f"{antwort} (Antwort auf die Frage: {frage})"


def _zu_viel_deutung(optionen: list[dict], frage: str) -> list[str]:
    """Alle Wörter über alle Antworten, die mehr sagen als ihr Knopf."""
    funde: list[str] = []
    for o in optionen:
        funde.extend(_sagt_mehr(o.get("knopf", ""), o.get("klartext", ""), frage))
    return list(dict.fromkeys(funde))


def _klartext_unbrauchbar(o: dict, frage: str) -> bool:
    return bool(_sagt_mehr(o.get("knopf", ""), o.get("klartext", ""), frage)) \
        or _klartext_bezugslos(o.get("klartext", ""))


def _ohne_ueberzogenen_klartext(optionen: list[dict], frage: str) -> list[dict]:
    """Letzter Halt nach dem Nachbessern: Der Knopf bleibt, ein Klartext, der
    immer noch mehr sagt als er oder seinen Bezug nicht nennt, fällt weg.
    Gespeichert wird dann, was die Person angetippt hat – ihre Worte, mit der
    Frage dahinter (`_mit_frage`)."""
    return [{**o, "klartext": ""} if _klartext_unbrauchbar(o, frage) else o for o in optionen]


async def _antwort_optionen(frage: str) -> list[dict]:
    """Antworten zum Antippen, passend zu dieser Frage. Leer bei der
    statischen Standardfrage, bei LLM-Fehlern und bei unbrauchbarer Ausgabe –
    der Aufrufer zeigt dann die festen Knöpfe. Ein Satz Antworten, dessen
    Klartext mehr sagt als die Knöpfe, wird einmal nachgebessert; hilft das
    nicht, bleiben die Knöpfe und die überzogenen Klartexte fallen weg."""
    if not frage or frage == _fallback_frage():
        return []
    brauchbar: list[dict] = []      # formal in Ordnung, einzelne Klartexte evtl. überzogen oder ohne Bezug
    fehl_bisher: int | None = None
    zu_viel: list[str] = []
    ohne_bezug: list[str] = []
    try:
        for versuch in range(_OPTIONEN_VERSUCHE):
            roh = await grok.simple(
                fp.stimmung_antworten(frage, nachbessern=versuch > 0, zu_viel=zu_viel, ohne_bezug=ohne_bezug),
                max_tokens=400)
            optionen = _optionen_lesen(roh)
            if not optionen:
                logger.info("Antwort-Knöpfe zur Stimmungsfrage unbrauchbar (Versuch %d).", versuch + 1)
                zu_viel, ohne_bezug = [], []
                continue
            zu_viel = _zu_viel_deutung(optionen, frage)
            ohne_bezug = _ohne_bezug(optionen)
            if not zu_viel and not ohne_bezug:
                return optionen
            # Es gilt der Satz mit den wenigsten unbrauchbaren Klartexten (der frühere bei Gleichstand).
            fehl = sum(1 for o in optionen if _klartext_unbrauchbar(o, frage))
            if fehl_bisher is None or fehl < fehl_bisher:
                brauchbar, fehl_bisher = optionen, fehl
            if zu_viel:
                logger.info("Klartext der Antwort-Knöpfe sagt mehr als die Knöpfe (Versuch %d): %s",
                            versuch + 1, ", ".join(zu_viel))
            if ohne_bezug:
                logger.info("Klartext der Antwort-Knöpfe nennt seinen Bezug nicht (Versuch %d): %s",
                            versuch + 1, "; ".join(ohne_bezug))
    except Exception as e:
        logger.warning("Antwort-Knöpfe zur Stimmungsfrage fehlgeschlagen%s: %s",
                       "" if brauchbar else " – feste Knöpfe", e)
    return _ohne_ueberzogenen_klartext(brauchbar, frage)


def _merke_offen(chat_id: str, frage: str = "") -> None:
    s = state.get(chat_id)
    s[_OFFEN] = time.time() + state.STALE_STIMMUNG_SECONDS
    s[_OFFEN_FRAGE] = "" if frage == _fallback_frage() else frage


def wartet_auf_antwort(chat_id: str) -> bool:
    """Steht eine Stimmungsfrage offen, deren Fenster noch läuft? Für die
    Nachrichten-Weiche in main.py (nur im freien Chat, nach allen Modes)."""
    s = state.get(chat_id)
    bis = s.get(_OFFEN)
    if not bis:
        return False
    if time.time() > bis:
        s.pop(_OFFEN, None)
        s.pop(_OFFEN_FRAGE, None)
        return False
    return True


def _richtungs_kandidaten(verbrauchte: set[str]) -> list[str]:
    """Bis zu _VERSUCHE Richtungen OHNE Zurücklegen, zuerst die zuletzt nicht
    benutzten. Vorher wurde pro Versuch neu gewürfelt – ein Retry konnte damit
    dieselbe Richtung ziehen, die gerade zur Wiederholung geführt hatte
    (Befund 27.08.2026: vier Tage in Folge das Wetter-Bild). Reichen die
    frischen nicht, wird mit den verbrauchten aufgefüllt – sonst stünde nach
    ein paar Tagen gar keine Auswahl mehr zur Verfügung."""
    alle = fp.stimmung_richtungen()
    frisch = [r for r in alle if r not in verbrauchte]
    rest = [r for r in alle if r in verbrauchte]
    random.shuffle(frisch)
    random.shuffle(rest)
    return (frisch + rest)[:_VERSUCHE]


def _rolle_von(chat_id: str) -> str:
    return paare.ROLLE_DOM if chat_id == paare.dom_chat_id() else paare.ROLLE_SUB


def _darf(chat_id: str) -> bool:
    """Sub immer; die Dom-Seite nur im gleichberechtigten Modus."""
    return chat_id == paare.sub_chat_id() or (modi.gleichberechtigt() and chat_id == paare.dom_chat_id())


def _fallback_frage() -> str:
    return t("STIMMUNG_FRAGE_VANILLA") if modi.ist_vanilla() else t("STIMMUNG_FRAGE")


async def _frage_text(rolle: str = paare.ROLLE_SUB) -> tuple[str, str]:
    """Jedes Mal frisch per LLM formulierte Stimmungs-Frage (Nutzer-Feedback:
    der immergleiche statische Text wirkt mechanisch). Die letzten Fragen gehen
    als Sperr-Liste in den Prompt; wird es trotzdem die gleiche Formulierung,
    greift ein Retry mit ANDERER Richtung. Fällt bei LLM-Fehlern – und wenn
    alle Versuche zu ähnlich bleiben – auf den statischen Standardtext zurück.
    Gibt (Frage, benutzte Richtung) zurück; die Richtung landet in der
    Sperr-Liste, damit sie morgen nicht sofort wieder dran ist."""
    try:
        letzte = await qdrant.get_recent_stimmung_eintraege(limit=5, user_id=rolle)
    except Exception as e:
        logger.warning("Letzte Stimmungs-Fragen nicht ladbar (Sperr-Liste leer): %s", e)
        letzte = []
    vorherige = [e.get("zusammenfassung", "") for e in letzte]
    verbrauchte = {e.get("richtung") for e in letzte if e.get("richtung")}
    kandidaten = _richtungs_kandidaten(verbrauchte)

    # Arbeitskopie: verworfene Kandidaten wandern in die Sperr-Liste des
    # NÄCHSTEN Versuchs. Sonst bekommt der Retry exakt dieselbe Vorgabe wie der
    # gescheiterte Versuch – und damit oft denselben Lieblings-Satzanfang.
    sperre = list(vorherige)
    try:
        for versuch, richtung in enumerate(kandidaten, start=1):
            if modi.ist_vanilla():
                from bot.prompts import vanilla as vp
                prompt = vp.stimmung_frage(rolle, vermeiden=sperre, richtung=richtung)
            else:
                prompt = fp.stimmung_abfragen(vermeiden=sperre, richtung=richtung)
            text = _ohne_angehaengtes_du((await grok.simple(prompt, max_tokens=120)).strip())
            if not modi.ist_vanilla():
                # Vanilla hat keine Anrede-Regel; dort darf ein Kosename stehen.
                from bot.services import persona_config
                text = _ohne_ersatz_anrede(text, persona_config.sklave_anrede() or "")
            vorgabe = bool(text) and _stellt_etwas_fest(text)
            if text and not vorgabe and not _zu_aehnlich(text, vorherige):
                return text, richtung
            logger.info("Stimmungs-Frage %s (Versuch %d, Richtung '%s') – Retry.",
                        "stellt etwas fest, statt zu fragen" if vorgabe else "zu ähnlich zu den letzten",
                        versuch, richtung)
            if text:
                sperre.append(text)
    except Exception as e:
        logger.warning("Stimmungs-Frage per LLM fehlgeschlagen – Standardtext: %s", e)
    else:
        # KEIN Ausweichen auf den letzten Kandidaten: der ist als zu ähnlich
        # ERKANNT worden. Genau der ging bisher trotzdem raus – am 26./27.08.2026
        # zeichengleich an zwei Tagen hintereinander.
        logger.warning("Alle %d Stimmungs-Versuche verworfen (zu ähnlich oder mit Vorgabe statt Frage) – "
                       "statischer Standardtext.", len(kandidaten))
    return _fallback_frage(), ""


async def _merke_frage(frage: str, richtung: str = "", rolle: str = paare.ROLLE_SUB,
                       frage_id: str | None = None, optionen: list[dict] | None = None) -> None:
    """Gesendete Frage in die Sperr-Liste (typ=stimmung_frage) – NACH dem
    erfolgreichen Senden aufrufen, sonst blockt eine nie zugestellte
    Formulierung künftige Fragen (Trace 06.07., Lücke 6). Mit `frage_id` und
    `optionen` liegen auch die Antworten zum Antippen am Eintrag: Die Knöpfe
    tragen nur ID + Nummer, der Tipp schlägt Etikett und Klartext hier nach
    (übersteht einen Neustart)."""
    if frage == _fallback_frage():
        return  # statischer Fallback gehört nicht in die Sperr-Liste
    daten = {"typ": "stimmung_frage", "zusammenfassung": frage, "richtung": richtung}
    if optionen:
        daten["antworten"] = optionen
    try:
        if frage_id:
            await qdrant.save_training(rolle, daten, point_id=frage_id)
        else:
            await qdrant.save_training(rolle, daten)
    except Exception as e:
        logger.warning("Stimmungs-Frage nicht als Sperr-Listen-Eintrag gespeichert: %s", e)


async def _frage_mit_tasten(rolle: str) -> tuple[str, str, str | None, list[dict]]:
    """Frage + passende Antworten: (Frage, Richtung, Frage-ID, Optionen).
    Ohne brauchbare Optionen sind ID None und die Liste leer."""
    frage, richtung = await _frage_text(rolle)
    optionen = await _antwort_optionen(frage)
    return frage, richtung, (str(uuid.uuid4()) if optionen else None), optionen


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stimmung – Sklave (Vanilla: beide) initiiert selbst eine Stimmungsmeldung."""
    chat_id = str(update.effective_chat.id)
    if not _darf(chat_id):
        return
    rolle = _rolle_von(chat_id)

    s = state.get(chat_id)
    if s.get("mode", "chat") != "chat":
        await update.message.reply_text(t("COMMON_BESCHAEFTIGT"))
        return

    async with telegram_helper.typing_action(context.bot, chat_id):
        frage, richtung, frage_id, optionen = await _frage_mit_tasten(rolle)
    await update.message.reply_text(frage, reply_markup=_tasten(frage_id, optionen))
    # Merkmarke erst NACH erfolgreichem Senden – ein Sendefehler darf keine
    # offene Frage hinterlassen, die es nie gab (Trace 06.07., Lücke 6).
    _merke_offen(chat_id, frage)
    # In die Chat-History – sonst kennt die Herrin im nächsten freien Chat-Turn
    # ihre eigene Frage nicht und deutet seine Folge-Nachricht gegen den Kontext
    # des VORTAGES (Befund 02.07.).
    state.add_message(chat_id, "assistant", frage)
    await _merke_frage(frage, richtung, rolle, frage_id, optionen)


async def frage_stellen(bot: Bot) -> None:
    """Vom Scheduler aufgerufen – nur wenn STIMMUNG_ENABLED=true. Vanilla:
    an beide Personen (nacheinander, je mit eigener Sperr-Liste)."""
    if not config.STIMMUNG_ENABLED:
        return
    rollen = (paare.ROLLE_DOM, paare.ROLLE_SUB) if modi.ist_vanilla() else (paare.ROLLE_SUB,)
    for rolle in rollen:
        try:
            await _frage_an(bot, rolle)
        except Exception:
            logger.exception("Stimmungsfrage (%s) fehlgeschlagen", rolle)


async def _frage_an(bot: Bot, rolle: str) -> None:
    chat = paare.paar_im_kontext().chat_id(rolle)
    state.clear_if_stale(chat)  # liegengebliebenen UI-Flow nicht ewig blockieren lassen
    s = state.get(chat)

    if s.get("mode", "chat") != "chat":
        logger.info("Stimmungsfrage (%s) übersprungen – aktiver State: %s", rolle, s.get("mode"))
        return

    frage, richtung, frage_id, optionen = await _frage_mit_tasten(rolle)
    # Re-Check NACH dem LLM-Await (TOCTOU): im Fenster kann die Person einen
    # Flow begonnen oder ein Safeword gesendet haben.
    if state.is_paused() or s.get("mode", "chat") != "chat":
        logger.info("Stimmungsfrage nach Generierung verworfen – Pause/Mode geändert.")
        return
    await bot.send_message(chat_id=chat, text=frage, reply_markup=_tasten(frage_id, optionen))
    # Merkmarke/Sperr-Liste erst NACH erfolgreichem Senden (Trace 06.07.,
    # Lücke 6). BEWUSST kein set_mode: Der Chat bleibt frei, Spiel-Impuls und
    # Follow-up laufen weiter (s. Modulkopf).
    _merke_offen(chat, frage)
    state.add_message(chat, "assistant", frage)  # s. Kommentar in start()
    await _merke_frage(frage, richtung, rolle, frage_id, optionen)
    logger.info("Stimmungsfrage gesendet (%s, %s).", rolle,
                f"{len(optionen)} passende Antworten" if optionen else "feste Knöpfe")


def _etikett_aus_nachricht(query) -> str:
    """Etikett des gedrückten Knopfs aus der Nachricht selbst – Rückfall, wenn
    der Eintrag zur Frage nicht (mehr) auffindbar ist."""
    try:
        for zeile in query.message.reply_markup.inline_keyboard:
            for knopf in zeile:
                if knopf.callback_data == query.data and isinstance(knopf.text, str):
                    return knopf.text
    except Exception:
        pass
    return ""


async def _auswahl_zeigen(query, frage: str, etikett: str) -> None:
    """Knöpfe weg und die gewählte Antwort unter die Frage schreiben – sonst
    steht nach dem Tipp nirgends im Chat, was gewählt wurde."""
    if frage and etikett:
        try:
            await query.edit_message_text(f"{frage}\n\n➜ {etikett}")
            return
        except Exception:
            pass
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


async def callback_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ein Tipp auf eine Antwort unter der Stimmungsfrage – eine zur Frage
    erzeugte (`stimmung:w:<frage_id>:<nr>`) oder eine der fünf festen
    (`stimmung:<n>`)."""
    query = update.callback_query
    await query.answer()
    chat_id = str(update.effective_chat.id)
    if not _darf(chat_id):
        return
    teile = query.data.split(":")
    frage_text = query.message.text if isinstance(query.message.text, str) else ""
    frage = klartext = ""
    if len(teile) == 2 and teile[1] in _STUFEN:
        etikett = t(f"BUTTON_STIMMUNG_{teile[1]}")
        antwort = t(f"STIMMUNG_WERT_{teile[1]}")
    elif len(teile) == 4 and teile[1] == "w" and teile[3].isdigit():
        try:
            frage_id = str(uuid.UUID(teile[2]))
        except ValueError:
            return
        eintrag = await qdrant.get_training_by_id(frage_id, user_id=_rolle_von(chat_id)) or {}
        optionen = eintrag.get("antworten") if eintrag.get("typ") == "stimmung_frage" else None
        nr = int(teile[3])
        if isinstance(optionen, list) and nr < len(optionen) and isinstance(optionen[nr], dict):
            etikett = str(optionen[nr].get("knopf") or "")
            klartext = str(optionen[nr].get("klartext") or "")
        else:
            etikett = _etikett_aus_nachricht(query)
        if not etikett:
            return
        antwort = etikett
        frage = eintrag.get("zusammenfassung") or frage_text
    else:
        return
    # Doppel-Tipp: Telegram kann den zweiten Tap noch zustellen, bevor die
    # Knöpfe weg sind – je Frage-Nachricht nur EINE Stimmung verbuchen.
    s = state.get(chat_id)
    if s.get("stimmung_knopf_mid") == query.message.message_id:
        return
    s["stimmung_knopf_mid"] = query.message.message_id
    await _auswahl_zeigen(query, frage or frage_text, etikett)
    await _antwort_verarbeiten(context.bot, chat_id, antwort, query.message.reply_text,
                               frage=frage, klartext=klartext)
    logger.info("Stimmung per Knopf erfasst (%s).",
                f"Stufe {teile[1]}" if len(teile) == 2 else "Antwort zur Frage")


async def handle_antwort(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet eine getippte Stimmungsantwort (Merkmarke offen, s. main.py)."""
    chat_id = str(update.effective_chat.id)
    frage = state.get(chat_id).get(_OFFEN_FRAGE) or ""
    await _antwort_verarbeiten(context.bot, chat_id, update.message.text.strip(), update.message.reply_text,
                               frage=frage)


def _hinweis_bereinigt(hinweis: str) -> str:
    """Der Gedanke unter dem Kopf ohne behauptete Gewohnheiten („das hilft
    meistens") und ohne Rückfrage am Ende – die Prompt-Regeln dazu hält das
    Modell nur teilweise ein (Echt-Test 21.11.2026)."""
    from bot.scheduler import schablonen  # lazy: der Scheduler lädt seinerseits Handler
    sauber = schablonen.mitteilung_bereinigen(hinweis)
    if sauber.split() != (hinweis or "").split():       # reine Leerraum-Glättung nicht melden
        logger.info("Stimmungs-Hinweis bereinigt (Gewohnheit/Rückfrage): %d → %d Zeichen.",
                    len(hinweis or ""), len(sauber))
    return sauber


def _spricht_ueber_dom(text: str) -> bool:
    """Hinweis an die Dom-Seite, der von ihr in der dritten Person redet (Log
    26.11.2026, Muster „… lässt sie ihn …, während sie …“ –
    die Du-Regel im Prompt allein hält das Modell nicht immer ein). Das Pronomen
    prüfen wir nur, wenn es sich von dem der devoten Seite unterscheidet."""
    from bot.prompts import rollen
    d = rollen.dom()
    t = text or ""
    namen = "|".join(re.escape(n) for n in {d["label"], d["real"]})
    if re.search(rf"\b(die|der|deine?|seine[mnrs]?|ihre[mnrs]?)\s+({namen})\b", t):
        return True
    if rollen.dom_geschlecht() == rollen.sub_geschlecht():
        return False
    # „sie lässt/liest/sitzt …“ (Verb in der 3. Person) und „ihre/ihren …“ –
    # das bloße „ihr“ bleibt frei, es ist hier meist die Anrede an beide.
    nom = d["nom"]
    if re.search(rf"\b{nom}\s+\w+t\b", t, re.IGNORECASE):
        return True
    # umgestellt: „vielleicht lässt sie ihn …“, „dann kann sie sich …“
    if re.search(rf"\b\w+t\s+{nom}\s+(ihn|ihm|sich)\b", t, re.IGNORECASE):
        return True
    # Modalverb in der 3. Person: „das kann sie heute nutzen“ („du kannst“ endet anders)
    if re.search(rf"\b(kann|könnte|will|soll|sollte|darf|muss|mag|möchte)\s+{nom}\b", t, re.IGNORECASE):
        return True
    stamm = d["poss"].removesuffix("e")          # „ihre“ → „ihr“ + Endung
    return bool(re.search(rf"\b{stamm}(e|en|em|er|es)\b", t, re.IGNORECASE))


async def _hinweis_an_dom(daten, system: str) -> str:
    """Hinweis-Gedanke für die Dom-Seite; spricht der Entwurf von ihr in der
    dritten Person, gibt es einen zweiten Versuch mit genau diesem Mangel."""
    hinweis = _hinweis_bereinigt(await grok.simple(daten, system=system))
    if not _spricht_ueber_dom(hinweis):
        return hinweis
    logger.info("Stimmungs-Hinweis spricht von ihr in der dritten Person – noch einmal.")
    nachtrag = ("\n\nDein letzter Entwurf sprach von ihr in der dritten Person (\"sie …\", \"ihre …\"). "
                "Die Nachricht geht an SIE selbst: sprich sie durchgehend mit du/dir/dich/dein an.")
    try:
        zweiter = _hinweis_bereinigt(await grok.simple(daten, system=system + nachtrag))
    except Exception:
        logger.warning("Stimmungs-Hinweis: zweiter Versuch fehlgeschlagen – der erste gilt.", exc_info=True)
        return hinweis
    return zweiter if zweiter and not _spricht_ueber_dom(zweiter) else (zweiter or hinweis)


def _hinweis_text(schluessel: str, hinweis: str, antwort: str, frage: str = "", klartext: str = "",
                  **platzhalter) -> str:
    """Nachricht an die andere Seite: Kopf plus Gedanke dazu. Gab es eine Frage,
    stehen Frage und Antwort wörtlich im Kopf (Live-Befund 21.11.2026: dort
    stand nur der Klartext, also die Deutung des Modells – was gefragt und was
    angetippt wurde, war nicht zu sehen). Ohne Frage (feste Knöpfe, Meldung aus
    eigenem Antrieb) bleibt es bei der einen Zeile."""
    sicher = telegram_helper.md_einbett_sicher
    if (frage or "").strip():
        return t(f"{schluessel}_FRAGE", frage=sicher(" ".join(frage.split())),
                 antwort=sicher(" ".join((antwort or "").split())), hinweis=hinweis, **platzhalter)
    return t(schluessel, antwort=sicher(klartext or antwort), hinweis=hinweis, **platzhalter)


# --- Reaktion auf die Antwort -------------------------------------------------
# Ohne Vorgabe baute das Modell die Reaktion Tag für Tag gleich (Antwort
# wiederholen, deuten, zum Erzählen auffordern) und kündigte dabei regelmäßig
# etwas für „gleich" oder den Abend an, das nie kam – auf diese Nachricht folgt
# ja nichts (Log-Durchsicht 22.11.2026). Deshalb wählt der Code die Bauform und
# kennt die letzten Reaktionen (sie liegen am Stimmungs-Eintrag); darunter ein
# Netz mit einem Nachbesserungs-Versuch. Erkannt wird an Wörtern, nicht am Sinn.
_REAKTION_VERSUCHE = 3
_REAKTION_MERKE = 4          # so viele letzte Reaktionen zählen als „zuletzt"
_REAKTION_FORM_PAUSE = 3     # eine Bauform kommt frühestens nach so vielen anderen wieder
_REAKTION_REST_MIN = 15      # so viel muss bleiben, wenn ein Ankündigungs-Satz gestrichen wird
_WE = r"(?![\wäöüß])"
_BALD_RE = re.compile(
    _WA + r"(?:gleich|nachher|sp(?:ä|ae)ter|bald|demn(?:ä|ae)chst|in\s+k(?:ü|ue)rze|heute\s+(?:abend|nacht|noch)|"
    r"noch\s+heute|soon|later|tonight|shortly|in\s+a\s+(?:moment|minute|bit))" + _WE, re.I)
_ICH_RE = re.compile(_WA + r"(?:ich|i(?:'ll|'m)?)(?![\wäöüß'])", re.I)
_DU_RE = re.compile(_WA + r"(?:du|you)" + _WE, re.I)
# Was ihm „gleich" geschieht – eine Ankündigung auch ohne „ich".
_DIR_GESCHIEHT_RE = re.compile(
    _WA + r"(?:bekommst|kriegst|erlebst|sp(?:ü|ue)rst|merkst|lernst|wirst|wird|bist\s+du\s+(?:dran|f(?:ä|ae)llig)|"
    r"gibt\s+es|gibt's|you(?:'ll|\s+will)|you(?:'re|\s+are)\s+going\s+to)" + _WE, re.I)
# Futur mit ihm als Ziel, auch ohne Zeitwort. Bewusst eng: „da werde ich
# neugierig" oder „ich werde nicht schlau aus dir" sind keine Ankündigung.
_ICH_WERDE_DICH_RE = re.compile(
    _WA + r"(?:ich\s+werde|werde\s+ich)\s+(?:dich|dir|dein\w*)" + _WE
    + r"|" + _WA + r"ich\s+(?:dich|dir)" + _WE + r"[^.!?]*" + _WA + r"werde" + _WE
    + r"|" + _WA + r"(?:i'll|i\s+will|i'm\s+going\s+to|i\s+am\s+going\s+to)" + _WE, re.I)
# „heute" allein ist kein Zeitwort der Ankündigung („wie war dein Tag heute?").
# Es zählt erst mit ihr als Handelnder und ihm als Ziel im selben Teilsatz –
# und nicht in der Vergangenheit („ich habe dich heute vermisst").
_HEUTE_RE = re.compile(_WA + r"(?:heute|today)" + _WE, re.I)
_DICH_RE = re.compile(_WA + r"(?:dich|dir|you)" + _WE, re.I)
_UM_DICH_RE = re.compile(_WA + r"um\s+(?:dich|dir)" + _WE, re.I)
_VERGANGEN_RE = re.compile(_WA + r"(?:habe|hab|hatte|war|gewesen|gehabt|was|were|have|had)" + _WE, re.I)
# „…, das gleich zu ändern": Ankündigung als Infinitiv, ohne „ich".
_ZU_INFINITIV_RE = re.compile(_WA + r"(?:zu\s+[a-zäöüß]+e?n|[a-zäöüß]+zu[a-zäöüß]+e?n)" + _WE, re.I)
_SATZ_TRENNER_RE = re.compile(r"(?<=[.!?…])\s+")
_TEIL_TRENNER_RE = re.compile(r"[,;:]|\s[–—-]\s")


def _saetze(text: str) -> list[str]:
    return [x.strip() for x in _SATZ_TRENNER_RE.split((text or "").strip()) if x.strip()]


def _ankuendigungen(text: str) -> list[str]:
    """Sätze, die etwas für gleich, nachher oder heute noch in Aussicht stellen.
    Ein Zeitwort zählt nur zusammen mit ihr als Handelnder (oder mit dem, was
    ihm geschieht) im selben Teilsatz – „ob du gleich noch so redest" ist keine
    Ankündigung. Steht das Zeitwort für sich („Heute Abend, wenn …, zeige ich"),
    genügt ein „ich" irgendwo im Satz."""
    aus = []
    for satz in _saetze(text):
        if _ICH_WERDE_DICH_RE.search(satz):
            aus.append(satz)
            continue
        for teil in _TEIL_TRENNER_RE.split(satz):
            if _BALD_RE.search(teil):
                if _ICH_RE.search(teil) or _DIR_GESCHIEHT_RE.search(teil) or (
                        not _DU_RE.search(teil) and _ICH_RE.search(satz)) or (
                        _ZU_INFINITIV_RE.search(teil) and not _DU_RE.search(satz)):
                    aus.append(satz)
                    break
            elif _HEUTE_RE.search(teil) and not _VERGANGEN_RE.search(teil) and (
                    _UM_DICH_RE.search(teil) or (_ICH_RE.search(teil) and _DICH_RE.search(teil))):
                aus.append(satz)
                break
    return aus


def _ohne_ankuendigung(text: str) -> str:
    """Letzter Halt: streicht die Ankündigungs-Sätze, wenn danach noch etwas
    Tragfähiges übrig ist – sonst bleibt der Text, wie er ist."""
    treffer = set(_ankuendigungen(text))
    if not treffer:
        return text
    rest = " ".join(x for x in _saetze(text) if x not in treffer).strip()
    return rest if len(rest) >= _REAKTION_REST_MIN else text


_AUSRUFE = frozenset({"ah", "aha", "ach", "oh", "oha", "och", "na", "nun", "so", "soso", "hm", "tja", "also",
                      "well"})


def _wiederholt_antwort(reaktion: str, antwort: str) -> bool:
    """Steigt die Reaktion damit ein, die Antwort im Wortlaut zu wiederholen
    (höchstens ein Ausruf davor)? Gilt ab zwei Wörtern Antwort, verglichen
    werden ihre ersten bis zu drei Wörter. Ein einzelnes Wort aufzugreifen oder
    die Antwort mitten im Satz zu zitieren ist erwünscht und zählt nicht."""
    kern = _normalisiere(antwort).split()[:3]
    if len(kern) < 2:
        return False
    r = _normalisiere(reaktion).split()
    if r and r[0] in _AUSRUFE and r[:len(kern)] != kern:
        r = r[1:]
    return r[:len(kern)] == kern


def _wortfolgen(text: str, n: int = 3) -> list[str]:
    w = _normalisiere(text).split()
    return [" ".join(w[i:i + n]) for i in range(len(w) - n + 1)]


def _reaktion_maengel(text: str, vorherige: list[str], antwort: str, frage: str = "") -> list[str]:
    """Woran die Reaktion nachgebessert werden muss – in Worten, die so auch im
    Nachbesserungs-Auftrag stehen können. Leer = in Ordnung."""
    maengel = [f"kündigt etwas an („{x[:70]}“) – nichts, was du vorhast, und kein Zeitwort wie gleich, nachher, "
               f"später oder heute noch" for x in _ankuendigungen(text)]
    if _wiederholt_antwort(text, antwort):
        maengel.append("steigt mit der Antwort im Wortlaut ein")
    alte = {a for v in vorherige for a in fp.teilsatz_anfaenge(v)}
    gleiche = [a for a in fp.teilsatz_anfaenge(text) if a in alte]
    if gleiche:
        maengel.append("beginnt wie zuletzt mit " + ", ".join(f"„{g}“" for g in gleiche))
    # Wendungen von zuletzt mitten im Satz: drei Wörter am Stück, die schon in
    # einer der letzten Reaktionen standen. Was aus seiner Antwort oder der
    # Frage stammt, zählt nicht – das darf sie aufgreifen.
    eigene = set(_wortfolgen(antwort)) | set(_wortfolgen(frage))
    alt_folgen = {f for v in vorherige for f in _wortfolgen(v)} - eigene
    # … und Lieblingswendungen: zwei Wörter am Stück, die in mindestens zwei der
    # letzten Reaktionen standen (reine Füllwort-Paare zählen nicht).
    je_reaktion = [set(_wortfolgen(v, 2)) for v in vorherige]
    eigene_paare = set(_wortfolgen(antwort, 2)) | set(_wortfolgen(frage, 2))
    alt_folgen |= {f for paare_ in je_reaktion for f in paare_
                   if sum(1 for andere in je_reaktion if f in andere) >= 2
                   and any(len(w) >= 5 for w in f.split()) and f not in eigene_paare}
    wieder = [f for f in dict.fromkeys(_wortfolgen(text) + _wortfolgen(text, 2)) if f in alt_folgen
              and not any(f.startswith(g) or g.startswith(f) for g in gleiche)]
    if wieder:
        maengel.append("greift Wendungen von zuletzt wieder auf („" + "“, „".join(wieder[:3]) + "“)")
    elif not gleiche and _zu_aehnlich(text, vorherige):
        maengel.append("ist einer der letzten Reaktionen zu ähnlich")
    return maengel


def _bauform_wahl(letzte_formen: list[str]) -> str:
    """Eine Bauform, die unter den letzten _REAKTION_FORM_PAUSE Reaktionen nicht
    vorkam (jüngste zuerst übergeben)."""
    alle = fp.reaktion_bauformen()
    gesperrt = {f for f in letzte_formen[:_REAKTION_FORM_PAUSE] if f}
    return random.choice([f for f in alle if f not in gesperrt] or alle)


def _ohne_anrede(text: str, anrede: str) -> str:
    """Nimmt die eingestellte Anrede heraus, wo sie als Anrede steht – hinter
    einem Komma oder am Satzanfang vor einem Komma. Für die Tage, an denen sie
    aussetzt; mitten im Satz ohne Kommas bleibt sie stehen."""
    anrede = " ".join((anrede or "").split())
    if not anrede or not text:
        return text or ""
    a = r"\s+".join(re.escape(w) for w in anrede.split())
    neu = re.sub(rf",\s*{a}(?=\s*(?:[,.!?…;:]|[–—-]\s|$))", "", text, flags=re.I)
    neu = re.sub(rf"(^|(?<=[.!?…]\s)){a}\s*,\s*(\w)", lambda m: m.group(1) + m.group(2).upper(), neu, flags=re.I)
    return neu


async def _reaktion_text(rolle: str, antwort: str, frage: str, klartext: str) -> tuple[str, str]:
    """Reaktion der Herrin auf die Stimmungs-Antwort: (Text, Bauform). Scheitert
    schon der erste Aufruf, geht der Fehler an den Aufrufer (Textkonserve)."""
    try:
        letzte = await qdrant.get_recent_stimmung_reaktionen(limit=_REAKTION_MERKE, user_id=rolle)
    except Exception as e:
        logger.warning("Letzte Stimmungs-Reaktionen nicht ladbar (keine Abwechslungs-Vorgabe): %s", e)
        letzte = []
    vorherige = [e.get("reaktion", "") for e in letzte if e.get("reaktion")]
    bauform = _bauform_wahl([e.get("form", "") for e in letzte])
    from bot.services import persona_config
    anrede = (persona_config.sklave_anrede() or "").strip()
    anrede_aus = bool(anrede) and sum(1 for v in vorherige if anrede.lower() in v.lower()) >= 2

    beste = None                    # (Text, Mängel, Bauform) des bisher besten Versuchs
    hinweis, form = None, bauform
    for versuch in range(1, _REAKTION_VERSUCHE + 1):
        # Letzter Versuch nach lauter Ankündigungen: andere Bauform. In mancher Form
        # (ein einzelner Satz) kam das Modell im Echt-Test nicht davon los – und
        # dort bleibt nach dem Streichen des Satzes nichts übrig.
        if versuch == _REAKTION_VERSUCHE and versuch > 1 and beste and _ankuendigungen(beste[0]):
            form = _bauform_wahl([form] + [e.get("form", "") for e in letzte])
        try:
            text = (await grok.simple(
                fp.reaktion_auf_stimmung(antwort, frage, klartext, vorherige=vorherige, bauform=form,
                                         anrede_aus=anrede_aus, nachbessern=hinweis),
                max_tokens=250)).strip()
        except Exception:
            if beste is None:
                raise
            logger.warning("Stimmungs-Reaktion: Nachbesserung fehlgeschlagen – der bisher beste Versuch gilt.",
                           exc_info=True)
            break
        text = " ".join(text.split())     # ein, zwei Sätze: Zeilenumbrüche des Modells sind hier nur Rauschen
        if anrede_aus:
            text = _ohne_angehaengtes_du(_ohne_ersatz_anrede(_ohne_anrede(text, anrede), anrede))
        maengel = _reaktion_maengel(text, vorherige, antwort, frage) if text else ["leer"]
        if beste is None or len(maengel) < len(beste[1]):
            beste = (text, maengel, form)
        if not maengel:
            break
        logger.info("Stimmungs-Reaktion mit Mängeln (Versuch %d, Form '%s': %s)%s", versuch, form,
                    "; ".join(maengel), " – noch einmal." if versuch < _REAKTION_VERSUCHE else ".")
        hinweis = maengel
    if not beste or not beste[0]:
        raise ValueError("Stimmungs-Reaktion leer")
    return _ohne_ankuendigung(beste[0]), beste[2]


async def _antwort_verarbeiten(bot, chat_id: str, antwort: str, antworten, *,
                               frage: str = "", klartext: str = "") -> None:
    """Gemeinsamer Weg für Knopf und Text: speichern, In-Persona-Reaktion,
    Hinweis an die Dom-Seite. `antworten` ist reply_text der passenden Nachricht.
    `antwort` = was die Person gesagt hat (getippter Text oder Knopf-Etikett),
    `frage` = die Frage dazu, `klartext` = Bedeutung einer angetippten Antwort."""
    s = state.get(chat_id)
    s.pop(_OFFEN, None)
    s.pop(_OFFEN_FRAGE, None)
    if state.get_mode(chat_id) == "stimmung":      # Altbestand aus der Zeit mit Mode
        state.set_mode(chat_id, "chat")
    state.add_message(chat_id, "user", antwort)
    rolle = _rolle_von(chat_id)

    # Stimmung dauerhaft speichern, damit sie in Prompts/Dossier einfließt.
    # `zusammenfassung` lesen ~10 Stellen als „Aktuelle Stimmung": für eine
    # angetippte Antwort steht dort der Klartext, nicht das Etikett. Gibt es
    # keinen (getippte Antwort, Klartext verworfen), steht die Frage dahinter.
    daten = {"typ": "stimmung", "zusammenfassung": klartext or _mit_frage(antwort, frage)}
    if frage:
        daten["frage"] = frage
    if klartext:
        daten["knopf"] = antwort
    punkt_id = None
    try:
        punkt_id = await qdrant.save_training(rolle, daten)
    except Exception as e:
        logger.error("Fehler beim Speichern der Stimmung: %s", e)

    if modi.ist_vanilla():
        await _vanilla_reaktion_und_hinweis(bot, chat_id, rolle, antwort, antworten, frage, klartext)
        return

    # In-Persona-Reaktion an den Sklaven (statt Textkonserve)
    from bot.prompts import followup as fp
    bauform = ""
    try:
        reaktion, bauform = await _reaktion_text(rolle, antwort, frage, klartext)
    except Exception as e:
        logger.error("Fehler bei Stimmungs-Reaktion: %s", e)
        reaktion = t("FALLBACK_STIMMUNG_REAKTION")
    await antworten(reaktion)
    state.add_message(chat_id, "assistant", reaktion)
    # Reaktion an den Stimmungs-Eintrag: die nächsten Reaktionen setzen sich
    # davon ab. Die Textkonserve (kein Modell) gehört nicht dazu.
    if punkt_id and bauform:
        try:
            await qdrant.update_training(punkt_id, {"reaktion": reaktion, "reaktion_form": bauform})
        except Exception as e:
            logger.warning("Stimmungs-Reaktion nicht am Eintrag gemerkt: %s", e)

    # Natürlicher Hinweis an die Domina (Coach-Stimme, kein Bericht-Format, kein Pathos)
    from bot.prompts import coach_persona
    anlass = ("Der Sklave hat auf die Frage nach seiner Stimmung geantwortet." if frage
              else "Der Sklave hat sich von sich aus mit seiner Stimmung gemeldet.")
    system = (
        f"{anlass}\n\n"
        f"{coach_persona.fuer_coach_prompt()}\n\n"
        f"Sag der Domina locker Bescheid, wie seine Stimmung ist, und gib ihr einen kurzen, "
        f"konkreten Gedanken, wie sie darauf eingehen könnte. 1-3 Sätze, du-Form, "
        f"kein Bericht-Format, keine Floskeln.\n"
        f"{fp.STIMMUNG_BEIM_GESAGTEN}\n{fp.MITTEILUNG_OHNE_RUECKFRAGE}"
    )
    try:
        hinweis = await _hinweis_an_dom(
            fp.stimmungs_daten("Seine Stimmungs-Nachricht", antwort, frage, klartext,
                               frage_label="Die Frage an ihn"),
            system)
        await telegram_helper.send_domina(
            bot, _hinweis_text("STIMMUNG_HINWEIS_AN_DOMINA", hinweis, antwort, frage, klartext),
            parse_mode="Markdown",
        )
        logger.info("Stimmungs-Hinweis an Domina gesendet.")
    except Exception as e:
        logger.error("Fehler beim Stimmungs-Hinweis: %s", e)

async def _vanilla_reaktion_und_hinweis(bot, chat_id: str, rolle: str, antwort: str, antworten,
                                        frage: str = "", klartext: str = "") -> None:
    """Vanilla: Reaktion der neutralen Stimme an die Person, kurzer Hinweis an
    die andere Person (beide sehen den Stand des anderen – Entscheidung 05.11.2026,
    im Onboarding angekündigt)."""
    from bot.prompts import vanilla as vp
    try:
        system, user = vp.stimmung_reaktion(rolle, antwort, frage, klartext)
        reaktion = await grok.simple(user, system=system, max_tokens=250)
    except Exception as e:
        logger.error("Fehler bei Stimmungs-Reaktion (Vanilla): %s", e)
        reaktion = t("FALLBACK_STIMMUNG_REAKTION")
    await antworten(reaktion)
    state.add_message(chat_id, "assistant", reaktion)

    andere = vp.andere_rolle(rolle)
    try:
        system, user = vp.stimmung_hinweis(rolle, antwort, frage, klartext)
        hinweis = _hinweis_bereinigt(await grok.simple(user, system=system))
        ich = vp.person(rolle)
        von = ich["anrede"] or ("deine Partnerin" if ich["fem"] else "dein Partner")
        await telegram_helper.send_an(
            bot, paare.paar_im_kontext(), andere,
            _hinweis_text("STIMMUNG_HINWEIS_AN_PARTNER", hinweis, antwort, frage, klartext,
                          name=telegram_helper.escape_md(von)),
            parse_mode="Markdown",
        )
        logger.info("Stimmungs-Hinweis (Vanilla) an %s gesendet.", andere)
    except Exception as e:
        logger.error("Fehler beim Stimmungs-Hinweis (Vanilla): %s", e)
