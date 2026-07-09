"""
Persona-Stil-Presets + überschreibbare Prompt-Templates – aus Markdown-Dateien.

Veröffentlichungs-Schritt 3 (TODO 🚀): die Stil-Texte liegen nicht mehr als
Python-Strings im Code, sondern als Markdown-Dateien:

  bot/prompts/presets/<key>.md            – mitgelieferte Presets (Pflicht: standard)
  bot/prompts/presets/templates/<name>.md – Default-Templates für feste
                                            Verhaltensregeln (regeln_gespraech,
                                            grundierung_zusatz)

Eigene Presets/Overrides legt der Betreiber in config.PERSONA_PRESETS_DIR ab
(Default data/persona_presets, liegt im gemounteten Volume – kein Rebuild nötig,
aber Neustart):

  <dir>/<key>.md            – eigenes Preset; gleicher key wie ein mitgeliefertes
                              überschreibt dieses
  <dir>/templates/<name>.md – überschreibt das jeweilige Verhaltensregel-Template

Dateiformat Preset:
  label: Anzeigename fürs Menü
  ## stil_kopf   – Kern-Stilbeschreibung der Herrin (Pflicht)
  ## stil_fuss   – verbotenes Vokabular/Muster + Variations-Anweisung
  ## coach_stil  – Stimme des Coachs gegenüber der Domina
Fehlende Sektionen erben vom Standard-Preset (so bleibt der Coach die vertraute
Freundin, auch wenn ein Preset nur die Herrin-Stimme ändert).

Das aktive Preset wählt die Domina über /einstellungen bzw. das Onboarding
(persistiert via persona_config.persona_stil). Custom-Dateien mit Fehlern
werden mit Warnung übersprungen (fail-soft); fehlende MITGELIEFERTE Dateien
sind ein Packaging-Fehler und knallen laut beim Import (Test-/Deploy-Gate).

ACHTUNG Namenskollision: der `persoenlichkeit`-Parameter in domina_coach.get()
ist NICHT der Bot-Stil, sondern das gelernte Persönlichkeitsprofil des Sklaven –
deshalb heißt das Feld hier durchgängig `persona_stil`.
"""
import logging
import os
import re

logger = logging.getLogger(__name__)

_BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets")
_SECTIONS = ("stil_kopf", "stil_fuss", "coach_stil")
# Mitgelieferte Presets in fester Menü-Reihenfolge (Nummern in /einstellungen
# bleiben stabil); Custom-Presets hängen alphabetisch hinten dran.
_BUILTIN_KEYS = ("standard", "streng", "verspielt")
_TEMPLATE_NAMEN = ("regeln_gespraech", "grundierung_zusatz")

DEFAULT = "standard"


def _parse_preset(text: str) -> dict:
    """Parst eine Preset-Markdown-Datei: `label:`-Kopfzeile + ##-Sektionen."""
    label = ""
    sections: dict[str, str] = {}
    aktuelle = None
    zeilen: list[str] = []

    def _abschliessen():
        if aktuelle:
            inhalt = "\n".join(zeilen).strip()
            if inhalt:
                sections[aktuelle] = inhalt

    for zeile in text.splitlines():
        m = re.match(r"^##\s+([a-z_]+)\s*$", zeile)
        if m:
            _abschliessen()
            aktuelle = m.group(1)
            zeilen = []
            continue
        if aktuelle is None:
            if zeile.lower().startswith("label:"):
                label = zeile.split(":", 1)[1].strip()
            continue
        zeilen.append(zeile)
    _abschliessen()
    return {"label": label, **{k: v for k, v in sections.items() if k in _SECTIONS}}


def _custom_dir() -> str:
    from bot import config
    return getattr(config, "PERSONA_PRESETS_DIR", "") or ""


def _lade_presets() -> dict[str, dict]:
    presets: dict[str, dict] = {}

    # 1) Mitgelieferte Presets – Pflicht, Fehler knallen (Packaging-Bug).
    for key in _BUILTIN_KEYS:
        pfad = os.path.join(_BUILTIN_DIR, f"{key}.md")
        with open(pfad, encoding="utf-8") as f:
            preset = _parse_preset(f.read())
        if key == DEFAULT and not all(preset.get(s) for s in _SECTIONS):
            raise RuntimeError(f"Preset-Datei {pfad} unvollständig – standard braucht alle Sektionen")
        presets[key] = preset

    # 2) Custom-Presets aus dem Config-Verzeichnis – fail-soft.
    custom_dir = _custom_dir()
    if custom_dir and os.path.isdir(custom_dir):
        for name in sorted(os.listdir(custom_dir)):
            if not name.endswith(".md"):
                continue
            key = name[:-3].strip().lower()
            pfad = os.path.join(custom_dir, name)
            try:
                with open(pfad, encoding="utf-8") as f:
                    preset = _parse_preset(f.read())
            except Exception:
                logger.exception("Custom-Preset %s nicht lesbar – übersprungen", pfad)
                continue
            if not preset.get("stil_kopf"):
                logger.warning("Custom-Preset %s ohne '## stil_kopf' – übersprungen", pfad)
                continue
            if key in presets:
                logger.info("Custom-Preset überschreibt mitgeliefertes Preset %r", key)
            presets[key] = preset

    # 3) Fehlende Sektionen/Labels vom Standard erben (Coach-Stimme bleibt
    #    die vertraute Freundin, wenn ein Preset nur die Herrin-Stimme ändert).
    standard = presets[DEFAULT]
    for key, preset in presets.items():
        for s in _SECTIONS:
            preset.setdefault(s, standard[s])
        if not preset.get("label"):
            preset["label"] = key.capitalize()
    return presets


def _lade_templates() -> dict[str, str]:
    """Verhaltensregel-Templates: Custom-Override vor mitgeliefertem Default."""
    templates: dict[str, str] = {}
    custom_dir = _custom_dir()
    for name in _TEMPLATE_NAMEN:
        pfad = os.path.join(_BUILTIN_DIR, "templates", f"{name}.md")
        override = os.path.join(custom_dir, "templates", f"{name}.md") if custom_dir else ""
        if override and os.path.isfile(override):
            try:
                with open(override, encoding="utf-8") as f:
                    templates[name] = f.read().strip()
                logger.info("Verhaltensregel-Template %r aus Override %s", name, override)
                continue
            except Exception:
                logger.exception("Template-Override %s nicht lesbar – nutze Default", override)
        with open(pfad, encoding="utf-8") as f:  # Pflicht – fehlend = Packaging-Bug
            templates[name] = f.read().strip()
    return templates


PRESETS: dict[str, dict] = _lade_presets()
_TEMPLATES: dict[str, str] = _lade_templates()


def reload() -> None:
    """Presets/Templates neu von Platte laden (Tests; zur Laufzeit reicht Neustart)."""
    neu = _lade_presets()
    PRESETS.clear()
    PRESETS.update(neu)
    _TEMPLATES.clear()
    _TEMPLATES.update(_lade_templates())


def template(name: str) -> str:
    """Verhaltensregel-Template (Platzhalter ersetzt der Aufrufer via rollen.py)."""
    return _TEMPLATES[name]


def aktuelles_preset() -> dict:
    """Löst das aktive Preset über persona_config auf (Fallback: standard)."""
    from bot.services import persona_config
    key = persona_config.persona_stil() or DEFAULT
    return PRESETS.get(key, PRESETS[DEFAULT])


def stil_hinweis() -> str:
    """Nummerierte Preset-Auswahl (für /einstellungen und das Onboarding)."""
    zeilen = []
    for i, (key, preset) in enumerate(PRESETS.items(), start=1):
        zeilen.append(f"{i}️⃣ {preset['label']}")
    return "\n".join(zeilen)


def stil_key_fuer_eingabe(text: str) -> str | None:
    """Akzeptiert Nummer (1..n) oder Preset-Key; None wenn ungültig."""
    text = (text or "").strip().lower()
    keys = list(PRESETS.keys())
    if text in keys:
        return text
    try:
        i = int(text)
        if 1 <= i <= len(keys):
            return keys[i - 1]
    except ValueError:
        pass
    return None
