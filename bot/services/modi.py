"""
Paar-Modus: BDSM / Kink / Vanilla (Umbau "nicht nur BDSM", 05.11.2026).

Der Modus ist ein Persona-Feld PRO PAAR (persona_config.modus, persistiert im
Dom-Profil wie Sprache/Stil/Rollen) und steuert drei Schichten:

  - Rollen-Wortwahl (prompts/rollen.py): Herrin/Sklave vs. Partnerin/Partner
  - Aufgaben-Katalog + Keywords + Cluster (services/kategorie_logik.py ←
    services/kataloge.py)
  - Stil-Presets (prompts/persona_presets.py): je Modus eigener Satz

Leer/unbekannt = BDSM = Bestandsverhalten (byte-identische Prompts, siehe
tests/render_baseline.py). Kink bleibt asymmetrisch (eine Person führt), nur
milder in Wortwahl und Katalog. Vanilla (gleichberechtigt, neutrale dritte
Stimme) ist hier schon definiert, aber erst ab Etappe 2 wählbar.
"""

BDSM = "bdsm"
KINK = "kink"
VANILLA = "vanilla"

ALLE = (BDSM, KINK, VANILLA)
# Im Menü anbietbar – Vanilla kommt mit dem gleichberechtigten Rollenmodell
# (Etappe 2) dazu; bis dahin würde ein gesetzter Vanilla-Modus nur Katalog und
# Wortwahl umstellen, nicht die Chat-Rollen.
WAEHLBAR = (BDSM, KINK)

LABELS = {
    BDSM: "BDSM – Herrin/Herr & Sklave/Sklavin, voller Katalog",
    KINK: "Kink – eine Person führt, milder Katalog ohne harte BDSM-Themen",
    VANILLA: "Vanilla – gleichberechtigt, Paar-Intimität ohne Machtgefälle",
}

# Wort für Prompt-Sätze wie "Ordne die folgende BDSM-Aufgabe …" / "Du bist ein BDSM Coach".
THEMA = {BDSM: "BDSM", KINK: "Kink", VANILLA: "Paar-Intimität"}


def normalisiere(wert: str | None) -> str:
    m = (wert or "").strip().lower()
    return m if m in ALLE else BDSM


def aktuell() -> str:
    """Modus des Kontext-Paares (leer/unbekannt → BDSM)."""
    from bot.services import persona_config
    return normalisiere(persona_config.modus())


def ist_bdsm() -> bool:
    return aktuell() == BDSM


def ist_kink() -> bool:
    return aktuell() == KINK


def ist_vanilla() -> bool:
    return aktuell() == VANILLA


def thema() -> str:
    return THEMA[aktuell()]


def label(modus: str | None = None) -> str:
    return LABELS[normalisiere(modus if modus is not None else aktuell())]


def hinweis() -> str:
    """Nummerierte Modus-Auswahl (für /einstellungen und das Onboarding)."""
    return "\n".join(f"{i}️⃣ {LABELS[m]}" for i, m in enumerate(WAEHLBAR, start=1))


def fuer_eingabe(text: str) -> str | None:
    """Akzeptiert Nummer (1..n) oder Modus-Key; None wenn ungültig."""
    text = (text or "").strip().lower()
    if text in WAEHLBAR:
        return text
    try:
        i = int(text)
        if 1 <= i <= len(WAEHLBAR):
            return WAEHLBAR[i - 1]
    except ValueError:
        pass
    return None
