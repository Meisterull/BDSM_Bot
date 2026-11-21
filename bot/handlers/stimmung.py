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


def _zu_viel_deutung(optionen: list[dict], frage: str) -> list[str]:
    """Alle Wörter über alle Antworten, die mehr sagen als ihr Knopf."""
    funde: list[str] = []
    for o in optionen:
        funde.extend(_sagt_mehr(o.get("knopf", ""), o.get("klartext", ""), frage))
    return list(dict.fromkeys(funde))


def _ohne_ueberzogenen_klartext(optionen: list[dict], frage: str) -> list[dict]:
    """Letzter Halt nach dem Nachbessern: Der Knopf bleibt, ein Klartext, der
    immer noch mehr sagt als er, fällt weg. Gespeichert wird dann, was die
    Person angetippt hat – ihre Worte, mit der Frage am Eintrag."""
    return [{**o, "klartext": ""} if _sagt_mehr(o.get("knopf", ""), o.get("klartext", ""), frage) else o
            for o in optionen]


async def _antwort_optionen(frage: str) -> list[dict]:
    """Antworten zum Antippen, passend zu dieser Frage. Leer bei der
    statischen Standardfrage, bei LLM-Fehlern und bei unbrauchbarer Ausgabe –
    der Aufrufer zeigt dann die festen Knöpfe. Ein Satz Antworten, dessen
    Klartext mehr sagt als die Knöpfe, wird einmal nachgebessert; hilft das
    nicht, bleiben die Knöpfe und die überzogenen Klartexte fallen weg."""
    if not frage or frage == _fallback_frage():
        return []
    brauchbar: list[dict] = []      # formal in Ordnung, Klartext evtl. überzogen
    zu_viel: list[str] = []
    try:
        for versuch in range(_OPTIONEN_VERSUCHE):
            roh = await grok.simple(
                fp.stimmung_antworten(frage, nachbessern=versuch > 0, zu_viel=zu_viel), max_tokens=400)
            optionen = _optionen_lesen(roh)
            if not optionen:
                logger.info("Antwort-Knöpfe zur Stimmungsfrage unbrauchbar (Versuch %d).", versuch + 1)
                zu_viel = []
                continue
            zu_viel = _zu_viel_deutung(optionen, frage)
            if not zu_viel:
                return optionen
            brauchbar = optionen
            logger.info("Klartext der Antwort-Knöpfe sagt mehr als die Knöpfe (Versuch %d): %s",
                        versuch + 1, ", ".join(zu_viel))
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
            if text and not _zu_aehnlich(text, vorherige):
                return text, richtung
            logger.info("Stimmungs-Frage zu ähnlich zu den letzten (Versuch %d, Richtung '%s') – Retry.",
                        versuch, richtung)
            if text:
                sperre.append(text)
    except Exception as e:
        logger.warning("Stimmungs-Frage per LLM fehlgeschlagen – Standardtext: %s", e)
    else:
        # KEIN Ausweichen auf den letzten Kandidaten: der ist als zu ähnlich
        # ERKANNT worden. Genau der ging bisher trotzdem raus – am 26./27.08.2026
        # zeichengleich an zwei Tagen hintereinander.
        logger.warning("Alle %d Stimmungs-Versuche zu ähnlich – statischer Standardtext "
                       "statt Wiederholung.", len(kandidaten))
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
    # angetippte Antwort steht dort der Klartext, nicht das Etikett.
    daten = {"typ": "stimmung", "zusammenfassung": klartext or antwort}
    if frage:
        daten["frage"] = frage
    if klartext:
        daten["knopf"] = antwort
    try:
        await qdrant.save_training(rolle, daten)
    except Exception as e:
        logger.error("Fehler beim Speichern der Stimmung: %s", e)

    if modi.ist_vanilla():
        await _vanilla_reaktion_und_hinweis(bot, chat_id, rolle, antwort, antworten, frage, klartext)
        return

    # In-Persona-Reaktion an den Sklaven (statt Textkonserve)
    from bot.prompts import followup as fp
    try:
        reaktion = await grok.simple(fp.reaktion_auf_stimmung(antwort, frage, klartext), max_tokens=250)
    except Exception as e:
        logger.error("Fehler bei Stimmungs-Reaktion: %s", e)
        reaktion = t("FALLBACK_STIMMUNG_REAKTION")
    await antworten(reaktion)
    state.add_message(chat_id, "assistant", reaktion)

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
        hinweis = _hinweis_bereinigt(await grok.simple(
            fp.stimmungs_daten("Seine Stimmungs-Nachricht", antwort, frage, klartext,
                               frage_label="Die Frage an ihn"),
            system=system))
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
