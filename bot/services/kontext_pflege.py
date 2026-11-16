"""
Pflege von Verlaufs- und Rückmelde-Daten, BEVOR sie in einen Prompt gehen.

Prompt-Review Durchgang 10 (16.11.2026): die Anweisungen in den Prompts waren
in Ordnung, die eingespielten Daten nicht – monatealte Einträge ohne Alters-
angabe und unsortiert, rohe Steuer-Tags und Markdown aus alten Antworten,
Antworten, die heutige Regeln verletzen (und als Vorbild dienten), sowie
Rückmeldungen, die keine sind (eine Ziffer, ein einzelnes Wort). Alles hier ist
deterministisch und ohne LLM.
"""
import re
from datetime import datetime, timezone

# [AUFGABE: …] / [SPRACHNACHRICHT: …] aus gespeicherten Coach-Antworten: im
# Kontext sind sie Rauschen (derselbe Inhalt stand bis zu dreimal da) und laden
# das Modell ein, Tags zu setzen, wo keine hingehören.
_STEUER_TAG_RE = re.compile(r"\[(?:AUFGABE|SPRACHNACHRICHT|STRAFE|KEYWORD)[^\]]*\]", re.I | re.S)

# Antworten der Herrin, die behaupten, etwas sei eingetragen/gespeichert – die
# aktuelle Regel verbietet das; als Verlaufs-Beispiel schlagen sie die Regel.
_PROFIL_BEHAUPTUNG_RE = re.compile(
    r"\b(notiert|verankert|eingetragen|hinzugef[üu]gt|gespeichert|vermerkt|merke ich mir|"
    r"ab (sofort|jetzt) geh[öo]rt)\b", re.I)

# Rückmeldungen ohne Aussage (Live-Befund: aus einer Rückfrage wurde im Dossier
# eine Charakter-Eigenschaft, eine Sterne-Ziffer stand als „Gefühl" im Prompt).
_GEFUEHL_LEER = {"alles", "nichts", "nix", "ja", "nein", "ok", "okay", "gut", "passt",
                 "weiß nicht", "weiss nicht", "keine ahnung", "egal"}


def alter_tage(datum) -> int | None:
    """Alter eines ISO-Datums in Tagen; unparsebar/leer → None (nicht filtern)."""
    try:
        d = datetime.fromisoformat(str(datum).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - d).days


def alters_hinweis(datum, ab_tagen: int = 14) -> str:
    """„, vor 5 Wochen" / „, vor 4 Monaten" für ältere Einträge, sonst leer –
    ein nacktes Datum rechnet das Modell nicht zuverlässig in Abstand um."""
    tage = alter_tage(datum)
    if tage is None or tage < ab_tagen:
        return ""
    if tage < 60:
        return f", vor {tage // 7} Wochen"
    return f", vor {tage // 30} Monaten"


def ohne_steuer_tags(text: str) -> str:
    """Steuer-Tags und Doppelstern-Markdown aus gespeichertem Text entfernen."""
    t = _STEUER_TAG_RE.sub("", text or "").replace("**", "")
    t = re.sub(r"[ \t]+\n", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def ist_kommando(text: str) -> bool:
    return (text or "").lstrip().startswith("/")


def gefuehl_brauchbar(text) -> bool:
    """True, wenn eine Rückmeldung etwas aussagt: kein reiner Zahlenwert, kein
    Einwort-Füller, keine bloße Rückfrage."""
    t = " ".join(str(text or "").split())
    if len(t) < 6 or not re.search(r"[A-Za-zÄÖÜäöüß]{3}", t):
        return False
    # Kurze Rückfrage statt Rückmeldung („Wie bitte?", „Warum das?")
    if t.endswith("?") and len(t.split()) <= 4:
        return False
    kern = re.sub(r"[^\wäöüß ]", "", t.lower()).strip()
    if kern in _GEFUEHL_LEER or re.sub(r"^\d+\s*", "", kern) in _GEFUEHL_LEER | {""}:
        return False
    # Ein einzelnes Wort zählt, wenn es ein richtiges Wort ist („langweilig",
    # „erniedrigt") – nur Füller und Kürzel fallen raus.
    return len(kern.split()) >= 2 or len(kern) >= 8


def brauchbare_gefuehle(texte, limit: int | None = None) -> list[str]:
    out = [str(t).strip() for t in (texte or []) if gefuehl_brauchbar(t)]
    return out[:limit] if limit else out


def verlauf_bereinigt(entries: list[dict], max_alter_tage: int | None = None,
                      schluessel: str = "zusammenfassung") -> list[dict]:
    """Verlaufs-Einträge für einen Prompt: zu alte raus, Doppelte raus (gleicher
    Textanfang), chronologisch sortiert (ältester zuerst – so liest das Modell
    den jüngsten Stand zuletzt)."""
    gesehen, out = set(), []
    for e in entries or []:
        tage = alter_tage(e.get("datum", ""))
        if max_alter_tage is not None and tage is not None and tage > max_alter_tage:
            continue
        norm = " ".join(str(e.get(schluessel, "")).lower().split())[:80]
        if norm and norm in gesehen:
            continue
        gesehen.add(norm)
        out.append(e)
    return sorted(out, key=lambda e: str(e.get("datum", "")))


def behauptet_profil_aenderung(text: str) -> bool:
    return bool(_PROFIL_BEHAUPTUNG_RE.search(text or ""))


async def letzte_aufgaben(limit: int = 5, max_tage: int = 45, quelle: str | None = None) -> list[str]:
    """Die zuletzt gestellten Aufgaben als „vor N Tagen: Text"-Zeilen für einen
    Generator-Prompt (Abwechslung). Nur Aufgaben der letzten `max_tage`;
    best-effort – bei DB-Fehler eine leere Liste."""
    try:
        from bot.services import qdrant, zeiten   # lazy: das Modul bleibt sonst ohne DB-Abhängigkeit
        tasks = await qdrant.get_tasks_by_status(
            ["erledigt", "offen", "gefragt", "nicht_erledigt"], limit=limit, sort_by_datum=True,
            **({"quelle": quelle} if quelle else {}))
    except Exception:
        return []
    zeilen = []
    for task in tasks:
        text = " ".join((task.get("aufgabe") or "").split())
        tage = alter_tage(task.get("erteilt_am", ""))
        if len(text) < 8 or (tage is not None and tage > max_tage):
            continue
        zeilen.append(zeiten.mit_alter_label(text[:90], task.get("erteilt_am", "")))
    return zeilen
