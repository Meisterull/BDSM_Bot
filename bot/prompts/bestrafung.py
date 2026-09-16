"""
Bestrafungs-Prompts.
"""
from bot.prompts import coach_persona, rollen


def _strafen_kontext(
    sklave_hard_limits: list = None,
    sklave_vorlieben: list = None,
    kategorie_reaktionen: dict = None,
    letzte_strafen: list = None,
    dossier: str = "",
) -> str:
    """Gemeinsamer Personalisierungs-Block aller Strafen-Generatoren: Grenzen,
    Vorlieben (je Zeile, Richtungs-Regel), Reaktionsmuster, Historie, Dossier,
    Inventar."""
    s = rollen.sub()
    limits_str = ""
    if sklave_hard_limits:
        limits_str = (
            f"\nABSOLUTE GRENZEN – NIEMALS vorschlagen:\n"
            + "\n".join(f"🚫 {l}" for l in sklave_hard_limits)
            + "\n"
        )
    vorlieben_str = ""
    if sklave_vorlieben:
        # Je Zeile statt komma-verkettet (D9/M10): Richtungs-/Bedingungs-Zusätze
        # in Klammern fragmentieren sonst und werden vom Modell verdreht.
        vorlieben_str = (
            f"\nVorlieben {s['label_gen']} (als Hebel nutzbar – Entzug einer Vorliebe, "
            f"oder eine Aufgabe die {s['poss']}e Vorliebe in einen Service umkehrt; "
            f"in der Vorliebe genannte Richtungen/Rollen NIEMALS umkehren):\n"
            + "\n".join(f"- {v}" for v in sklave_vorlieben)
            + "\n"
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
    # Inventar: Wissen + Verbot wie bei den Aufgaben-Generatoren, optional der
    # Ausstattungs-Impuls (eine Strafe mit vorhandenem Gerät statt erfundenem).
    inventar_str = coach_persona.inventar_block(perspektive="coach")
    if inventar_str:
        inventar_str = "\n" + inventar_str + "\n"
    return f"{limits_str}{vorlieben_str}{reaktionen_str}{historie_str}{dossier_str}{inventar_str}"


def bestrafungsvorschlag(
    aufgabe: str,
    streak_vorher: int,
    sklave_hard_limits: list = None,
    sklave_vorlieben: list = None,
    kategorie_reaktionen: dict = None,
    letzte_strafen: list = None,
    dossier: str = "",
    inventar_impuls: str = None,
) -> tuple[str, str]:
    s, d = rollen.sub(), rollen.dom()
    streak_info = (
        f"{s['label_nom'][:1].upper()}{s['label_nom'][1:]} hatte einen Streak von {streak_vorher} Tagen – "
        f"dieser wurde durch die Nicht-Erledigung unterbrochen.\n"
        if streak_vorher > 0
        else ""
    )
    kontext = _strafen_kontext(sklave_hard_limits, sklave_vorlieben, kategorie_reaktionen,
                               letzte_strafen, dossier)
    impuls_str = coach_persona.inventar_impuls_block(inventar_impuls) if inventar_impuls else ""
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
        f"{kontext}{impuls_str}"
    )
    return system, user


def ablehnungs_strafen(
    wett_idee: str,
    sklave_hard_limits: list = None,
    sklave_vorlieben: list = None,
    kategorie_reaktionen: dict = None,
    letzte_strafen: list = None,
    dossier: str = "",
) -> tuple[str, str]:
    """Wette abgelehnt (handlers/waehrung): DREI kurze Strafen, aus denen die
    Dom-Seite per Knopf eine wählt. Gleicher Personalisierungs-Block wie
    bestrafungsvorschlag; kurz, weil sie nummeriert über den Knöpfen stehen,
    und im Aufgabenlisten-Stil, weil die gewählte als Aufgabe angelegt wird."""
    s, d = rollen.sub(), rollen.dom()
    sub_gross = s["label_nom"][:1].upper() + s["label_nom"][1:]
    kontext = _strafen_kontext(sklave_hard_limits, sklave_vorlieben, kategorie_reaktionen,
                               letzte_strafen, dossier)
    system = f"""{coach_persona.fuer_aufgaben_vorschlag()}

{sub_gross} hat eine Wette abgelehnt, die {d['real']} {s['dat']} angeboten hat. Schlag {d['real_dat']} DREI verschiedene Strafen dafür vor – {d['nom']} wählt eine davon aus.

Was hier eine gute Strafe ist:
- Verhältnismäßig: es geht um eine abgelehnte Wette, nicht um ein schweres Vergehen – spürbar und ein bisschen fies, aber kein Großereignis.
- GENAU EINE Handlung pro Strafe – keine Kette aus mehreren Praktiken („… und danach … und anschließend …").
- Eine Strafe ist keine Belohnung: was {s['nom']} laut Vorlieben genießt, taugt höchstens als ENTZUG (eine begrenzte Zeit verwehrt), nie als die Strafe selbst.
- Die drei kommen aus drei verschiedenen Richtungen: (1) Verzicht – etwas, das {s['nom']} mag, bleibt höchstens drei Tage verwehrt; (2) Dienst – etwas, das {s['nom']} für {d['real_akk']} erledigt; (3) eine kurze spürbare oder beschämende Handlung, höchstens 30 Minuten.
- Konkret und heute oder morgen erledigbar; ein Satz, höchstens 160 Zeichen, im Stil eines Eintrags auf einer Aufgabenliste – ohne Anrede, ohne „du"/„er" (z. B. „Den Abend über … ohne …").
- Grenzen und die Richtung jeder Vorliebe gelten strikt. Spielzeug/Strapon ejakuliert nie – kein „Creampie" oder Sauberlecken, wo körperlich keine Flüssigkeit entstehen kann.
(Anti-Klischee + Personalisierung: siehe oben.)

Antworte NUR als JSON: {{"strafen": ["…", "…", "…"]}}
Kein Text außerhalb des JSON, kein Markdown."""
    user = f"Abgelehnte Wette: {wett_idee}\n{kontext}"
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


