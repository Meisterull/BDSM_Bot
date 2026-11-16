"""
Prompts für das Psycho-Training der Dom-Seite (handlers/training, Mini-App).

Eine Übung ist eine fertige Nachricht: Sie muss beim Lesen etwas bringen und
darf keine Antwort voraussetzen. Wer das Training bekommt, liest in der Regel
mit, antwortet aber selten – eine Übung, die erst mit einer Antwort Sinn
ergibt („Wie würdest du reagieren?"), läuft dann ins Leere.
"""
import re

from bot.prompts import coach_persona, rollen
from bot.prompts.followup.bausteine import nutzer_text
from bot.services import kontext_pflege, zeiten

TYPEN = ("mindset", "situation", "reflexion", "challenge", "worte")

# Namen der Übungsarten in den Prompt-Listen (die Oberfläche nimmt die Locale-Texte).
# „mindset" heißt „Auftreten", nicht „Haltung": das kollidiert mit der Kategorie
# „Haltungstraining" (Posen der Sub-Seite), die als Abneigung in den Grenzen stehen kann.
TYP_NAMEN = {
    "mindset": "Auftreten",
    "situation": "Situation",
    "reflexion": "Reflexion",
    "challenge": "Challenge",
    "worte": "Worte für die Szene",
}

# Übungsarten, die mit dem Lesen abgeschlossen sind: eine angehängte Frage am
# Ende wird abgeschnitten (ohne_schlussfrage). „reflexion" IST eine Frage, bei
# „worte" stehen die Beispielsätze am Schluss und dürfen Fragen sein.
OHNE_SCHLUSSFRAGE = frozenset({"mindset", "situation", "challenge"})


def _auftrag(typ: str) -> str:
    """Der Auftrag der gewählten Übungsart – genau einer pro Prompt."""
    d, s = rollen.dom(), rollen.sub()
    ihr = d["poss"][:-1]          # „ihr" / „sein" (Stamm für ihrem/ihrer)
    auftraege = {
        "mindset": (
            f"ÜBUNGSART AUFTRETEN: Gib {d['dat']} EINEN konkreten Handgriff für {ihr} Auftreten mit – etwas an "
            f"Körper, Stimme, Tempo oder Blick, das {d['nom']} beim nächsten Mal mit {s['dat']} ohne "
            f"Vorbereitung einsetzt. Sag in einem Satz dazu, was es bei {s['dat']} auslöst. Keine "
            "Affirmationen, keine Visualisierung, kein Mantra und kein Satz zum Aufsagen vor dem Spiegel."
        ),
        "situation": (
            f"ÜBUNGSART SITUATION: Schildere in zwei, drei Sätzen einen Moment mit {s['dat']}, wie er bei den "
            f"beiden wirklich vorkommen kann und in dem {d['nom']} kurz entscheiden muss, wie es weitergeht. "
            f"Dann löst du selbst auf: was du an {ihr}er Stelle tun oder sagen würdest und warum das wirkt. "
            "Die Übung ist mit dem Lesen fertig – stell keine Frage, auf die du eine Antwort brauchst."
        ),
        "reflexion": (
            f"ÜBUNGSART REFLEXION: Gib {d['dat']} EINE Frage zum Mitnehmen über {d['poss']} Rolle – "
            f"festgemacht an etwas Konkretem aus den Daten (was zuletzt lief, was {d['nom']} sich vorgenommen "
            f"hat, was {d['dat']} noch schwerfällt), nicht allgemein. Ein, zwei Sätze Anlauf, dann die Frage. "
            "Es ist eine Frage zum Nachdenken: Bitte nicht um eine Antwort und kündige keine Auswertung an."
        ),
        "challenge": (
            f"ÜBUNGSART CHALLENGE: Gib {d['dat']} EINE kleine Sache mit, die {d['nom']} beim nächsten Mal mit "
            f"{s['dat']} ausprobiert – etwas an {ihr}em eigenen Verhalten (wie {d['nom']} etwas fordert, "
            f"abwartet, durchzieht oder beendet), keine neue Aufgabe für {s['akk']}. Sag dazu, woran "
            f"{d['nom']} merkt, dass es wirkt."
        ),
        "worte": (
            f"ÜBUNGSART WORTE FÜR DIE SZENE: Gib {d['dat']} zwei oder drei fertige Sätze, die {d['nom']} "
            f"{s['dat']} wörtlich sagen kann – in {ihr}em Ton und für Momente, die bei den beiden wirklich "
            "vorkommen. Jeder Satz steht in einer eigenen Zeile in Anführungszeichen, davor ein Halbsatz, "
            f"wann er passt. Kurz und direkt, so dass {d['nom']} sie ohne Überwindung ausspricht: kein "
            "Theater-Deutsch, kein Pathos, keine Schachtelsätze."
        ),
    }
    return auftraege.get(typ, auftraege["mindset"])


def _alter(datum) -> str:
    """„heute" / „vor 4 Tagen" / „vor 3 Wochen" / „vor 5 Monaten" – leer, wenn
    kein Datum lesbar ist."""
    tage = kontext_pflege.alter_tage(datum)
    if tage is None:
        return ""
    if tage < 14:
        return zeiten.alter_label(str(datum))
    if tage < 60:
        return f"vor {tage // 7} Wochen"
    return f"vor {tage // 30} Monaten"


def _einzeilig(text, limit: int) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0] + " …"


def uebungs_zeilen(eintraege: list[dict], limit: int = 6, max_tage: int = 120) -> list[str]:
    """Frühere Übungen als Zeilen für den Generator („nicht wiederholen"),
    neueste zuerst; hat die Dom-Seite geantwortet, hängt die Antwort an.
    Nur echte Übungen – Kategorie-Trainings liegen in derselben Sammlung."""
    d = rollen.dom()
    zeilen = []
    for e in eintraege or []:
        typ = e.get("typ")
        text = _einzeilig(e.get("uebung"), 220)
        if typ not in TYPEN or len(text) < 20:
            continue
        tage = kontext_pflege.alter_tage(e.get("datum", ""))
        if tage is not None and tage > max_tage:
            continue
        alter = _alter(e.get("datum", ""))
        zeile = f"{alter + ' · ' if alter else ''}{TYP_NAMEN[typ]}: {text}"
        antwort = e.get("antwort")
        if kontext_pflege.gefuehl_brauchbar(antwort):
            zeile += f" → {d['poss']} Antwort darauf: {_einzeilig(antwort, 200)}"
        zeilen.append(zeile)
        if len(zeilen) >= limit:
            break
    return zeilen


def antwort_zeilen(eintraege: list[dict], limit: int = 4, max_tage: int = 180) -> list[str]:
    """Was die Dom-Seite im Training über sich geschrieben hat – für ihr
    Dossier. Mit Alter und dem Anfang der Übung, damit die Antwort lesbar bleibt.
    Nur das letzte halbe Jahr: eine Charakteristik soll den heutigen Stand zeigen."""
    zeilen = []
    for e in eintraege or []:
        typ, antwort = e.get("typ"), e.get("antwort")
        if typ not in TYPEN or not kontext_pflege.gefuehl_brauchbar(antwort):
            continue
        tage = kontext_pflege.alter_tage(e.get("datum", ""))
        if tage is not None and tage > max_tage:
            continue
        alter = _alter(e.get("datum", ""))
        zeilen.append(
            f"{alter + ', ' if alter else ''}{TYP_NAMEN[typ]} (Übung: {_einzeilig(e.get('uebung'), 140)}): "
            f"{_einzeilig(antwort, 300)}")
        if len(zeilen) >= limit:
            break
    return zeilen


def uebung(typ: str, domina_profile: dict | None, *, letzte_aufgaben: list[str] | None = None,
           fruehere_uebungen: list[str] | None = None,
           sub_hard_limits: list[str] | None = None) -> tuple[str, str]:
    """(system, user) für eine Übung der Art `typ`."""
    d, s = rollen.dom(), rollen.sub()
    ihr = d["poss"][:-1]
    anfaenger = "eine Anfängerin" if d["nom"] == "sie" else "einen Anfänger"
    system = f"""Du gibst {d['real_dat']} heute ein kleines Training mit – wie eine vertraute Freundin, die dasselbe Hobby teilt und {d['dat']} etwas Brauchbares zusteckt.

{coach_persona.fuer_strukturierten_output()}

FÜR DIESEN AUFTRAG:
- Du lieferst EINE fertige Nachricht, kein Gespräch. Sie muss beim bloßen Lesen etwas bringen: {d['nom']} liest mit, antwortet aber oft nicht – das ist in Ordnung und nie ein Thema. Die Stil-Regel zum Weiterreden gilt hier NICHT: keine angehängte Frage, keine Bitte um Rückmeldung, kein „erzähl mir, wie es war".
- Auf {d['akk']} zugeschnitten: Nimm Stand, Ziele und das, was {d['dat']} laut Charakteristik noch schwerfällt, ernst. Erkläre nichts, was {d['nom']} auf {ihr}em Level längst kann, und behandle {d['akk']} nicht wie {anfaenger}.
- Konkret statt allgemein: festgemacht an dem, was die beiden wirklich tun (siehe Daten). Spielst du auf etwas Vergangenes an, dann beiläufig – ohne Datum, ohne „neulich" oder „letzte Woche".
- Neu: Die früheren Übungen unten wiederholst du nicht – anderes Thema, anderer Einstieg, andere Sätze.
- Was unten als Grenze steht, kommt in keiner Übung vor.
- Format: reiner Text ohne Markdown, ohne Überschrift, ohne Begrüßung und ohne Schlussformel. Höchstens 6 Sätze (Beispielsätze zählen mit). Kein [AUFGABE: ...]-Tag.

{_auftrag(typ)}"""

    profil = domina_profile or {}
    interessen = [i.strip() for i in (profil.get("interessen") or []) if isinstance(i, str) and i.strip()]
    zeilen = [
        f"Über {d['real_akk']}:",
        f"  Erfahrungsstand: {profil.get('erfahrungsstand') or 'unbekannt'}",
        f"  {coach_persona.level_zeile(profil.get('aktuelles_level', 1))}",
        f"  Interessen: {'; '.join(interessen) if interessen else 'nicht angegeben'}",
        f"  Ziele: {profil.get('ziele') or 'nicht angegeben'}",
    ]
    dossier = coach_persona.dossier_gekuerzt(profil.get("domina_dossier") or "")
    if dossier:
        zeilen.append(f"  Charakteristik (wer {d['nom']} als {d['label']} ist): {dossier}")
    if letzte_aufgaben:
        zeilen.append(f"\nWas zuletzt zwischen den beiden lief (Aufgaben an {s['akk']} – Anknüpfungspunkt, "
                      "kein Pflichtbezug; die Angabe vorn sagt, wann die Aufgabe gestellt wurde, ein "
                      "„heute\" oder „morgen\" im Text meint jenen Tag):")
        zeilen.extend(f"  - {a}" for a in letzte_aufgaben[:4])
    if fruehere_uebungen:
        zeilen.append("\nFrühere Übungen (NICHT wiederholen):")
        zeilen.extend(f"  - {u}" for u in fruehere_uebungen[:6])
    grenzen = [g for g in (profil.get("grenzen") or []) if isinstance(g, str) and g.strip()]
    zeilen.append(f"\nHarte Grenzen {s['label_gen']} (nie Thema einer Übung): "
                  f"{', '.join(sub_hard_limits) if sub_hard_limits else 'keine'}")
    zeilen.append(f"Grenzen {d['real_gen']} (nie Thema einer Übung): {', '.join(grenzen) if grenzen else 'keine'}")
    kinder = coach_persona.kinder_zeile(profil)
    if kinder:
        zeilen.append(kinder)
    inventar = coach_persona.inventar_block(perspektive="coach", kompakt=True, mit_wuenschen=False)
    if inventar:
        zeilen.append(inventar)
    return system, "\n".join(zeilen)


def feedback(typ: str, uebung_text: str, antwort: str, level: int) -> tuple[str, str]:
    """(system, user) für die Rückmeldung des Coach auf eine Antwort zur Übung."""
    d = rollen.dom()
    artikel = "Die" if d["nom"] == "sie" else "Der"
    system = f"""{artikel} {d['real']} hat auf eine Trainingsübung geantwortet. Reagiere konkret darauf – wie eine vertraute Freundin.

{coach_persona.fuer_coach_prompt()}

2-4 Sätze, Fließtext ohne Liste. Geh auf das ein, was {d['nom']} geschrieben hat – nicht generisch ermutigen. Je nach Übungsart: bei Reflexion ein weiterführender Gedanke, bei Situation ein konkreter Hinweis, bei Auftreten und Challenge eine knappe Beobachtung, bei Worten für die Szene ein Satz, der an {d['poss']} Wortwahl anknüpft.
Kein [AUFGABE: ...] Tag."""
    prompt = f"""Übungsart: {TYP_NAMEN.get(typ, typ)}
Übung: {uebung_text}
{nutzer_text(d['poss'].capitalize() + ' Antwort', antwort)}
{coach_persona.level_zeile(level)}"""
    return system, prompt


_SATZENDE_RE = re.compile(r"[.!?…][\"“”«»'’)]*\s+")


def ohne_schlussfrage(text: str) -> str:
    """Schneidet am Textende angehängte Fragen ab („Wie würdest du reagieren?",
    „Probierst du es aus?"). Fragen in Anführungszeichen (Beispielsätze) und
    Fragen mitten im Text bleiben. Bliebe zu wenig übrig, bleibt der Text."""
    t = (text or "").rstrip()
    while t.endswith("?"):
        enden = list(_SATZENDE_RE.finditer(t[:-1]))
        if not enden:
            break
        kandidat = t[:enden[-1].end()].rstrip()
        if len(kandidat) < 40:
            break
        t = kandidat
    return t
