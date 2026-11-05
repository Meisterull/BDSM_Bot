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
milder in Wortwahl und Katalog. Vanilla (Etappe 2) ist GLEICHBERECHTIGT: eine
neutrale dritte Stimme (prompts/vanilla.py) spricht mit beiden, beide bekommen
Stimmungsfrage und Impulse, Strafen sind aus (strafen_aktiv()). Intern bleiben
die zwei Slots dom/sub (Qdrant-Keys unverändert) – der Dom-Slot ist die Person,
die den Bot eingerichtet hat.
"""

BDSM = "bdsm"
KINK = "kink"
VANILLA = "vanilla"

ALLE = (BDSM, KINK, VANILLA)
# Im Menü anbietbar. Vanilla seit Etappe 2 (gleichberechtigtes Rollenmodell:
# beide Chats sprechen mit derselben neutralen Stimme, s. handlers/vanilla.py).
WAEHLBAR = (BDSM, KINK, VANILLA)

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


def gleichberechtigt() -> bool:
    """True, wenn es keine führende Rolle gibt (Vanilla): beide Chats laufen über
    dieselbe neutrale Stimme, Rollen-Gates gelten für beide Seiten gleich."""
    return ist_vanilla()


def strafen_aktiv() -> bool:
    """Strafen, Abzüge, Strafen-Roulette und Versäumnis-Mechanik gibt es nur mit
    Machtgefälle (BDSM/Kink). Im Vanilla-Modus bleiben Währung, Shop, Sparziel
    und Wetten als reines Belohnungssystem (Entscheidung 05.11.2026)."""
    return not ist_vanilla()
