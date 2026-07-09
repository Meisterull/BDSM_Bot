"""
Render-Harness für die Literale-Migration (bot/prompts/ → rollen.py).

Rendert alle aufrufbaren Prompt-Builder der vier Migrations-Dateien mit festen
synthetischen Argumenten in der Default-Konstellation (F/M) und schreibt das
Ergebnis nach stdout. Vor der Migration als Baseline sichern, nach der
Migration diffen – F/M MUSS byte-identisch bleiben (Bestandsverhalten).

    python3 tests/render_baseline.py > /tmp/baseline.txt   # vorher
    python3 tests/render_baseline.py > /tmp/nachher.txt    # nachher
    diff /tmp/baseline.txt /tmp/nachher.txt                # leer = wortgleich

Async-Builder mit DB-Zugriff werden übersprungen (SKIP-Zeile) – die deckt der
Migrations-Review manuell ab.
"""
import inspect
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # pragma: no cover
    import telegram  # noqa: F401
except ImportError:
    from unittest.mock import MagicMock
    for _name in [
        "telegram", "telegram.ext", "telegram.constants", "telegram.error",
        "qdrant_client", "qdrant_client.models", "qdrant_client.http",
        "qdrant_client.http.models", "qdrant_client.http.exceptions",
        "apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.asyncio",
        "apscheduler.triggers", "apscheduler.triggers.cron",
        "apscheduler.triggers.interval", "httpx", "dotenv",
    ]:
        _m = MagicMock(name=_name)
        _m.__name__ = _name
        _m.__path__ = []
        sys.modules[_name] = _m
    sys.modules["dotenv"].load_dotenv = lambda *a, **k: None

from bot.prompts import followup, domina_coach, bestrafung, coach_persona  # noqa: E402


def _wert_fuer(param: inspect.Parameter):
    """Deterministischer Testwert je Parameter (Name/Annotation-Heuristik)."""
    name = param.name.lower()
    ann = param.annotation
    if param.default is not inspect.Parameter.empty:
        return param.default
    if ann in (int,) or name in ("level", "streak", "punkte", "anzahl", "offene_anzahl",
                                 "tage", "streak_vorher", "erledigte"):
        return 3
    if ann in (bool,):
        return False
    if ann in (dict,) or "profil" in name or "profile" in name:
        return {"interessen": ["Kontrolle", "Rituale"], "grenzen": ["Blut"],
                "vorlieben": ["Wachs", "Spanking"], "hard_limits": ["Blut", "Nadeln"],
                "erfahrungsstand": "etwas Erfahrung", "ziele": "Konsequenter führen",
                "aktuelles_level": 3, "aufgaben_schwierigkeit": "normal",
                "kinderfreie_zeiten": ["20:00-23:00"], "persoenlichkeit_tags": ["mag_kontrolle"]}
    if ann in (list,) or name.endswith(("liste", "listen", "kategorien", "limits", "tags",
                                        "gefuehle", "faeden", "wuensche", "aufgaben", "eintraege")):
        return ["Alpha", "Beta"]
    # Default: String
    return f"TEST-{param.name.upper()}"


# Feste kwargs für Builder mit **kwargs-Signatur (Parameter-Satz von
# scheduler._vorschlag_kontext bzw. Listen-von-Dicts-Formatter).
_SPEZIAL_KWARGS = {
    "tiny_task_vorschlag": dict(
        erfahrungsstand="etwas Erfahrung", level=3, interessen=["Kontrolle", "Rituale"],
        sklave_vorlieben=["Wachs", "Spanking"], sklave_hard_limits=["Blut"],
        sklave_dislike_kategorien=["Fisting"], letzte_aufgaben=["Alte Aufgabe A"],
        letzte_tiny_tasks=["Label A", "Label B"], letzte_inspirationen=["Insp A"],
        gewaehlte_kategorien=["Anal", "Dienst"], cross_kategorie="Psycho",
        sklave_wunsch_kategorien=["Pegging"], abgelehnte_tiny_tasks=[{"inhalt": "Abgelehnt A", "grund": "zu viel"}],
        conversation_context="TEST-KONTEXT", stimmung="TEST-STIMMUNG",
        bewertungs_kontext="TEST-BEWERTUNG", vertrauens_kontext="TEST-VERTRAUEN",
        schwierigkeit="normal", kategorie_level_hinweis="TEST-LEVELHINWEIS",
        dossier="TEST-DOSSIER", offene_faeden=["Faden A"],
        kategorie_reaktionen={"Anal": {"positiv": 3, "negativ": 0},
                              "Fisting": {"positiv": 0, "negativ": 2}},
        domina_kategorie_praeferenzen={"Anal": {"positiv": 2, "negativ": 0},
                                       "Fisting": {"positiv": 0, "negativ": 1}},
    ),
    "format_context": dict(entries=[{"beschreibung": "K1", "datum": "2026-07-01",
                                     "wichtige_punkte": ["P1"], "zusammenfassung": "Z1"}]),
    "format_lerntagebuch": dict(entries=[{"inhalt": "L1", "datum": "2026-07-01"}]),
}
_SPEZIAL_KWARGS["ausfuehrlicher_task_vorschlag"] = _SPEZIAL_KWARGS["tiny_task_vorschlag"]


def _render(modul) -> None:
    print(f"\n{'#'*70}\n# MODUL {modul.__name__}\n{'#'*70}")
    for name, fn in sorted(vars(modul).items()):
        if not callable(fn) or name.startswith("_") or inspect.isclass(fn):
            continue
        if getattr(fn, "__module__", "") != modul.__name__:
            continue
        if inspect.iscoroutinefunction(fn):
            print(f"\n=== {name} === SKIP (async/DB)")
            continue
        try:
            random.seed(42)  # Zufalls-Variationen (z.B. Frage-Rotation) deterministisch
            if name in _SPEZIAL_KWARGS:
                kwargs = _SPEZIAL_KWARGS[name]
            else:
                sig = inspect.signature(fn)
                kwargs = {p.name: _wert_fuer(p) for p in sig.parameters.values()
                          if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
            ergebnis = fn(**kwargs)
        except Exception as e:  # noqa: BLE001 – Harness: skip + sichtbar machen
            print(f"\n=== {name} === SKIP ({type(e).__name__}: {e})")
            continue
        print(f"\n=== {name} ===")
        teile = ergebnis if isinstance(ergebnis, tuple) else (ergebnis,)
        for i, teil in enumerate(teile):
            # Zeit-abhängige Zeilen normalisieren (Uhrzeit/Wochentag/Tageszeit)
            teil = re.sub(r"Aktuelle Uhrzeit: .*", "Aktuelle Uhrzeit: <NORMALISIERT>", str(teil))
            print(f"--- Teil {i} ---\n{teil}")


if __name__ == "__main__":
    for modul in (followup, domina_coach, bestrafung, coach_persona):
        _render(modul)
