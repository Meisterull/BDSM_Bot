"""
Präferenz-Detektor – pflegt Vorlieben & No-Gos im Gespräch statt per Hand-Liste.

Läuft best-effort nach jeder normalen Chat-Nachricht (Sklave und Domina). Erkennt,
wenn jemand eine Vorliebe oder eine Grenze/ein No-Go äußert, formuliert daraus einen
Profil-Patch und schickt ihn als ✅/🗑-Vorschlag – nie still angewendet:

  Sklave äußert etwas  → Vorschlag geht an den SKLAVEN (er bestätigt selbst),
                         die Domina bekommt nach Bestätigung eine Info.
  Domina äußert etwas  → Vorschlag geht an die DOMINA.

Sicherheit:
  - Vorlieben werden gegen Hard-Limits/Grenzen gefiltert (nichts Grenzverletzendes
    wird als Vorliebe verankert).
  - No-Gos landen ADD-ONLY im Grenzen-Feld (hard_limits / grenzen) – das Modell kann
    eine Grenze nur ergänzen, niemals entfernen (apply_profile_patch / "limit_add").
  - AUSDRÜCKLICH geäußerte Ausnahmen ("Öffentlichkeit ist No-Go, aber Plug tragen
    wäre ok") werden als ANNOTATION an die bestehende Grenze gehängt ("limit_refine":
    'Öffentlichkeit' → 'Öffentlichkeit (Ausnahme: Plug tragen)') – der Basis-Begriff
    bleibt erhalten, die Output-Validierung (limits_check) bleibt strikt; nur die
    Prompts sehen die Ausnahme und dürfen differenziert formulieren.

Gating wie bei dossier.erfasse_wunsch_aus_chat: nur bei Signalwörtern oder langen,
inhaltlichen Nachrichten – sonst löst jede Nachricht einen Grok-Call aus.
"""
import difflib
import logging
import re

from bot.services import qdrant, grok, limits_check
from bot.prompts import followup as fp

logger = logging.getLogger(__name__)

# Vorliebe vs. No-Go-Feld pro Rolle.
_VORLIEBE_FELD = {"sklave": "vorlieben", "domina": "interessen"}
_NOGO_FELD = {"sklave": "hard_limits", "domina": "grenzen"}

# Signalwörter, die ein Präferenz-/Grenz-Signal nahelegen. Großzügig, aber nicht
# allumfassend – der Längen-Fallback (>=120) fängt den Rest, dann entscheidet Grok.
_SIGNALE = (
    "mag", "liebe", "lieb es", "steh auf", "stehe auf", "gern", "gerne", "geil",
    "fantasie", "fetisch", "turn", "anmach", "erregt", "reizt",
    "hasse", "ekel", "ekl", "abtörn", "abturn", "kann nicht ab", "nicht mein",
    "no-go", "nogo", "no go", "tabu", "grenze", "limit", "niemals", "auf keinen fall",
    "geht gar nicht", "will nicht", "möchte nicht", " unwohl", "angst vor",
    "darf nie", "absolut nicht", "verabscheue", "nicht ausstehen",
)

_MIN_LEN_FALLBACK = 120


def _gated(text: str) -> bool:
    tl = (text or "").lower()
    return any(s in tl for s in _SIGNALE) or len(text.strip()) >= _MIN_LEN_FALLBACK


def _norm(x: str) -> str:
    return " ".join((x or "").lower().split())


def _ist_neu(wert: str, bestand: list[str]) -> bool:
    """True, wenn `wert` nicht (annähernd) schon in `bestand` steht."""
    wn = _norm(wert)
    if len(wn) < 3:
        return False
    return not any(difflib.SequenceMatcher(None, wn, _norm(b)).ratio() > 0.85 for b in bestand)


def _basis(eintrag: str) -> str:
    """Limit-Eintrag ohne '(Ausnahme: ...)'-Annotation."""
    idx = eintrag.lower().find("(ausnahme")
    return eintrag[:idx].strip() if idx > 0 else eintrag


def _enthaelt_als_wort(kurz: str, lang: str) -> bool:
    """Teilstring-Match nur an Wortgrenzen: 'nadeln' trifft 'nadeln im gesicht',
    aber ein Bestand 'nied…' trifft NICHT 'erniedrigung' (Review D6 – nackter
    Substring annotierte Ausnahmen an die falsche Grenze). Gleiche Match-Klasse
    wie die gehärteten limits_check-Begriffe."""
    return re.search(rf"(?<!\w){re.escape(kurz)}(?!\w)", lang) is not None


def _nicht_nur_fehlgriff(nogo: str, text: str) -> bool:
    """True, wenn der No-Go-Kandidat aus einer 'nicht nur X'-Konstruktion stammt:
    'Ich will nicht nur X' heißt MEHR als X wollen, nicht X ablehnen
    (Live-Fehlgriff 15.07.: aus genau so einer Äußerung wurde ein
    '🚫 No-Go: nur X'-Vorschlag). Deterministisches Netz unter der Prompt-Regel
    in _SYSTEM."""
    tn = _norm(text)
    nn = _norm(nogo).removeprefix("nur ").strip()
    kernwoerter = [w for w in nn.split() if len(w) > 3] or nn.split()
    if not kernwoerter:
        return False
    for m in re.finditer(r"\bnich?t (nur|bloss|bloß)\b", tn):
        fenster = tn[m.end(): m.end() + 60]
        if all(w in fenster for w in kernwoerter):
            return True
    return False


def _finde_bestand(wert: str, bestand: list[str]) -> str | None:
    """Bestehenden Grenzen-Eintrag finden, dem `wert` entspricht (fuzzy wie _ist_neu;
    vorhandene Ausnahme-Annotationen im Bestand werden beim Vergleich ignoriert)."""
    wn = _norm(wert)
    if len(wn) < 3:
        return None
    for b in bestand:
        if not isinstance(b, str):
            continue
        bn = _norm(_basis(b))
        if wn == bn or _enthaelt_als_wort(wn, bn) or _enthaelt_als_wort(bn, wn) \
                or difflib.SequenceMatcher(None, wn, bn).ratio() > 0.85:
            return b
    return None


_SYSTEM = (
    "Du pflegst ein BDSM-Präferenzprofil. Analysiere AUSSCHLIESSLICH die zitierte "
    "Nachricht und extrahiere, was die Person über SICH SELBST an Vorlieben oder "
    "Grenzen/No-Gos ausdrückt.\n"
    "- vorlieben: Dinge, die sie mag / gern hätte / erregend findet (knappe Stichpunkte, "
    "max. 8 Wörter, aus ihrer Perspektive).\n"
    "- nogos: Dinge, die sie ausdrücklich NICHT will / ekelhaft / angstbesetzt / als Grenze "
    "benennt (knappe Stichpunkte). ACHTUNG: 'nicht nur X' / 'nicht bloß X' heißt, die Person "
    "will MEHR als X – das ist WEDER ein No-Go zu X noch eine Grenze. Nur ausdrückliche "
    "Ablehnung von X selbst zählt.\n"
    "- ausnahmen: NUR wenn die Person AUSDRÜCKLICH eine Ausnahme/Einschränkung zu einem "
    'No-Go formuliert ("X ist tabu, ABER Y wäre ok"): Objekte {"grenze": "X", "ausnahme": "Y"} '
    "(beides knappe Stichpunkte). X dann NICHT zusätzlich unter nogos wiederholen.\n"
    "Nur, was WIRKLICH und EINDEUTIG geäußert wird. Keine Interpretation, keine Erfindung, "
    "nichts aus früheren Nachrichten. Im Zweifel leer lassen.\n"
    "Antworte als reines JSON, KEIN Markdown:\n"
    '{"vorlieben": [], "nogos": [], "ausnahmen": []}'
)


async def erkenne_und_schlage_vor(bot, rolle: str, text: str) -> bool:
    """Best-effort: erkennt Vorlieben/No-Gos in `text` und schickt ggf. einen
    ✅/🗑-Profilvorschlag. Gibt True zurück, wenn ein Vorschlag gesendet wurde.
    Wirft nie – Fehler werden geloggt (darf den Chat nie blockieren)."""
    if rolle not in _VORLIEBE_FELD or not _gated(text):
        return False
    try:
        return await _verarbeite(bot, rolle, text)
    except Exception:
        logger.exception("Präferenz-Detektor fehlgeschlagen (rolle=%s)", rolle)
        return False


async def _verarbeite(bot, rolle: str, text: str) -> bool:
    antwort = await grok.simple(
        fp.nutzer_text("Nachricht", text[:600]), system=_SYSTEM, temperature=0,
    )  # Extraktion: deterministisch
    try:
        parsed = grok.parse_json(antwort)
    except Exception:
        logger.debug("Präferenz-Detektor: Grok-Antwort nicht parsebar: %s", antwort[:200])
        return False
    if not isinstance(parsed, dict):
        return False

    vorlieben_roh = [v for v in (parsed.get("vorlieben") or []) if isinstance(v, str) and v.strip()]
    nogos_roh = [v for v in (parsed.get("nogos") or []) if isinstance(v, str) and v.strip()]
    ausnahmen_roh = [
        a for a in (parsed.get("ausnahmen") or [])
        if isinstance(a, dict)
        and isinstance(a.get("grenze"), str) and a["grenze"].strip()
        and isinstance(a.get("ausnahme"), str) and a["ausnahme"].strip()
    ]
    if not vorlieben_roh and not nogos_roh and not ausnahmen_roh:
        return False

    profil = await qdrant.get_user_profile(rolle) or {}
    vorlieben_feld = _VORLIEBE_FELD[rolle]
    nogo_feld = _NOGO_FELD[rolle]
    bestand_vorlieben = profil.get(vorlieben_feld) or []
    bestand_nogos = profil.get(nogo_feld) or []

    # Hard-Limits/Grenzen BEIDER Rollen einmal vorab laden (nichts Grenzverletzendes
    # verankern; explizite Listen ersparen verletzungen() den Profil-Load pro Kandidat).
    andere_rolle = "domina" if rolle == "sklave" else "sklave"
    anderes_profil = await qdrant.get_user_profile(andere_rolle) or {}
    sk_profil = profil if rolle == "sklave" else anderes_profil
    do_profil = profil if rolle == "domina" else anderes_profil
    sk_hl = sk_profil.get("hard_limits") or []
    do_gr = do_profil.get("grenzen") or []

    changes: list[dict] = []

    # Vorlieben → list_add (grenz-gefiltert, dedupliziert)
    neue_vorlieben: list[str] = []
    for v in vorlieben_roh:
        v = v.strip()
        if not _ist_neu(v, bestand_vorlieben + neue_vorlieben):
            continue
        # Perspektive: die Vorliebe ist aus Sicht der äußernden Person formuliert –
        # beim Sklaven meint "ihre/deine X" die Seite der Herrin (Richtungs-Limits, s. limits_check).
        if await limits_check.verletzungen(
                v, sk_hl, do_gr, sprecher="sub" if rolle == "sklave" else "herrin"):
            logger.info("Vorliebe grenzverletzend, verworfen: %s", v)
            continue
        neue_vorlieben.append(v)
    if neue_vorlieben:
        changes.append({"feld": vorlieben_feld, "operation": "list_add", "wert": neue_vorlieben})

    # No-Gos → limit_add (add-only, dedupliziert)
    neue_nogos: list[str] = []
    for n in nogos_roh:
        n = n.strip()
        if _nicht_nur_fehlgriff(n, text):
            logger.info("No-Go-Kandidat stammt aus 'nicht nur X' – verworfen: %s", n)
            continue
        if _ist_neu(n, bestand_nogos + neue_nogos):
            neue_nogos.append(n)

    # Ausnahmen → limit_refine an bestehender Grenze (Basis bleibt erhalten);
    # steht die Grenze noch gar nicht im Profil, direkt annotiert via limit_add.
    refine_paare: list[dict] = []
    for a in ausnahmen_roh:
        grenze = a["grenze"].strip()
        ausnahme = a["ausnahme"].strip()
        bestehend = _finde_bestand(grenze, bestand_nogos)
        if bestehend is None:
            kombiniert = f"{grenze} (Ausnahme: {ausnahme})"
            if _ist_neu(kombiniert, bestand_nogos + neue_nogos):
                neue_nogos.append(kombiniert)
            continue
        if _norm(ausnahme) in _norm(bestehend):
            continue  # Ausnahme steht schon im Eintrag
        if "(ausnahme" in bestehend.lower() and bestehend.rstrip().endswith(")"):
            neu = bestehend.rstrip()[:-1] + f"; {ausnahme})"
        else:
            neu = f"{bestehend} (Ausnahme: {ausnahme})"
        refine_paare.append({"alt": bestehend, "neu": neu})

    if neue_nogos:
        changes.append({"feld": nogo_feld, "operation": "limit_add", "wert": neue_nogos})
    if refine_paare:
        changes.append({"feld": nogo_feld, "operation": "limit_refine", "wert": refine_paare})

    if not changes:
        return False

    patch = {"changes": changes}
    diff = _format_diff(neue_vorlieben, neue_nogos, refine_paare)

    # Pending-Eintrag (Träger des Patches) – user_id="domina" hält die Lern-Liste
    # konsistent; profile_user steuert das Ziel-Profil und den Bestätigungs-Empfänger.
    from bot.handlers import coach_regeln as _cr
    from bot.messages import t
    from bot.services import telegram_helper
    point_id = await qdrant.save_coach_regel(
        user_id="domina",
        text=f"Präferenz ({rolle}) aus Chat: {diff}",
        typ="profil_update",
        status="pending",
        quelle="chat_praeferenz",
        kontext="aus dem Gespräch",
        profile_user=rolle,
        profile_patch=patch,
    )
    nachricht = t("PRAEFERENZ_VORSCHLAG", diff=diff)
    buttons = _cr.vorschlag_buttons(point_id)
    if rolle == "sklave":
        await telegram_helper.send_sklave(bot, nachricht, parse_mode="Markdown", reply_markup=buttons)
    else:
        await telegram_helper.send_domina(bot, nachricht, parse_mode="Markdown", reply_markup=buttons)
    logger.info("Präferenz-Vorschlag gesendet (rolle=%s): %s", rolle, diff)
    return True


def _format_diff(vorlieben: list[str], nogos: list[str], refine_paare: list[dict] | None = None) -> str:
    zeilen = [f"➕ Vorliebe: {v}" for v in vorlieben]
    zeilen += [f"🚫 No-Go: {n}" for n in nogos]
    zeilen += [f"✏️ No-Go präzisiert: {p['alt']} → {p['neu']}" for p in (refine_paare or [])]
    return "\n".join(zeilen)
