"""
Bestrafungs-Prompts.
"""
from bot.prompts import coach_persona, rollen


def bestrafungsvorschlag(
    aufgabe: str,
    streak_vorher: int,
    sklave_hard_limits: list = None,
    sklave_vorlieben: list = None,
    kategorie_reaktionen: dict = None,
    letzte_strafen: list = None,
    dossier: str = "",
) -> tuple[str, str]:
    s, d = rollen.sub(), rollen.dom()
    streak_info = (
        f"{s['label_nom'][:1].upper()}{s['label_nom'][1:]} hatte einen Streak von {streak_vorher} Tagen – "
        f"dieser wurde durch die Nicht-Erledigung unterbrochen.\n"
        if streak_vorher > 0
        else ""
    )
    limits_str = ""
    if sklave_hard_limits:
        limits_str = (
            f"\nABSOLUTE GRENZEN – NIEMALS vorschlagen:\n"
            + "\n".join(f"🚫 {l}" for l in sklave_hard_limits)
            + "\n"
        )
    vorlieben_str = ""
    if sklave_vorlieben:
        vorlieben_str = (
            f"\nVorlieben {s['label_gen']} (als Hebel nutzbar – Entzug einer Vorliebe, "
            f"oder eine Aufgabe die {s['poss']}e Vorliebe in einen Service umkehrt):\n"
            f"{', '.join(sklave_vorlieben)}\n"
        )
    reaktionen_str = ""
    if kategorie_reaktionen:
        from bot.services import kategorie_logik
        spitzen = kategorie_logik.reaktions_spitzen({"kategorie_reaktionen": kategorie_reaktionen})
        if spitzen:
            reaktionen_str = (
                f"\nKategorie-Reaktionsmuster (was bei {s['dat']} landet):\n"
                f"{spitzen}\n"
            )
    historie_str = ""
    if letzte_strafen:
        historie_str = (
            f"\nLetzte Strafen (NICHT direkt wiederholen):\n"
            + "\n".join(f"- {s[:120]}" for s in letzte_strafen[:5])
            + "\n"
        )
    dossier_str = ""
    if dossier:
        dossier_str = f"\nWas du über {s['label_akk']} weißt (Dossier):\n{dossier[:600]}\n"
    system = f"""{coach_persona.fuer_aufgaben_vorschlag()}

{'Eine' if s['label'].endswith('in') else 'Ein'} {s['label']} hat eine Aufgabe nicht erledigt. Schlage {d['real_dat']} eine angemessene Bestrafung vor.

Die Bestrafung soll:
- Zur Schwere der Nicht-Erledigung passen
- Erzieherisch und nicht destruktiv sein
- Konkret und umsetzbar sein (eine eindeutige Handlung, keine vage Anweisung)
- Den Streak-Verlust berücksichtigen wenn vorhanden
(Anti-Klischee + Personalisierung: siehe oben.)

Formuliere direkt an {d['real_akk']} (du-Form, {d['nom']} ist {'die Empfängerin' if d['nom'] == 'sie' else 'der Empfänger'} dieses Vorschlags).
Frage am Ende ob {d['nom']} diese Bestrafung anordnen möchte oder eine andere bevorzugt.
KEIN [AUFGABE: ...] Tag. Kein Markdown."""
    user = (
        f"{streak_info}Nicht erledigte Aufgabe: {aufgabe}\n"
        f"{limits_str}{vorlieben_str}{reaktionen_str}{historie_str}{dossier_str}"
    )
    return system, user


def eskalations_vorschlag(
    aufgabe: str,
    streak: int,
    sklave_hard_limits: list = None,
    dossier: str = "",
    letzte_strafen: list = None,
) -> tuple[str, str]:
    s, d = rollen.sub(), rollen.dom()
    limits_str = ""
    if sklave_hard_limits:
        limits_str = (
            f"\nABSOLUTE GRENZEN – NIEMALS vorschlagen:\n"
            + "\n".join(f"🚫 {l}" for l in sklave_hard_limits)
            + "\n"
        )
    dossier_str = ""
    if dossier:
        dossier_str = f"\nWas du über {s['label_akk']} weißt (Dossier):\n{dossier[:600]}\n"
    historie_str = ""
    if letzte_strafen:
        historie_str = (
            f"\nLetzte Strafen (zur Einordnung, nicht wiederholen):\n"
            + "\n".join(f"- {s[:120]}" for s in letzte_strafen[:5])
            + "\n"
        )
    system = f"""{coach_persona.fuer_strukturierten_output()}

Du redest mit {d['real_dat']} wie {d['poss']} vertraute beste Freundin. {(d['poss'][:-1] + ('e' if s['label'].endswith('in') else '')).capitalize()} {s['label']} hat jetzt {streak} Aufgaben in Folge nicht erledigt – das ist ein Muster, keine Ausnahme.

Sag {d['dat']} locker und direkt, dass dir das Muster auffällt, und gib {d['dat']} EINEN konkreten Anstoß, wie {d['nom']} es mit {s['dat']} ansprechen könnte – so wie du es einer Freundin sagen würdest, die das gleiche Hobby teilt.

STRIKT:
- KEINE Markdown-Überschriften, KEINE Bullet-Listen, KEIN Ratgeber-Aufbau ("Analyse des Musters", "Mögliche Gründe", "Vorgeschlagenes Gespräch") – das ist ein Gespräch, kein Dokument.
- Maximal 4-5 Sätze, ein zusammenhängender Gedanke.
- Kein [AUFGABE: ...] Tag."""
    user = (
        f"Letzte nicht erledigte Aufgabe: {aufgabe}\n"
        f"{limits_str}{dossier_str}{historie_str}"
    )
    return system, user


