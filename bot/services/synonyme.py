"""Zentrale Ja/Nein-Synonyme für Bestätigungs-Eingaben.

Vorher in 5+ Handlern einzeln definiert. Kontextspezifische Extras (z.B. "✅",
"erledigt", "ok") werden bei Bedarf an JA/NEIN angehängt – so bleibt das
Matching identisch zur bisherigen lokalen Definition.
"""

JA = ("ja", "j", "yes", "y")
NEIN = ("nein", "n", "no")
# Ketten-Abschluss (Review D8/N8): EN-Paare sollen die Kette auch
# mit "done" abschließen können – "fertig" war hartkodiert.
FERTIG = ("fertig", "done", "finished")

import re as _re


def ja_nein(text: str) -> str | None:
    """Tolerante Ja/Nein-Erkennung für Antwort-Sätze wie "ja, habe ich erledigt"
    oder "nein, habe ich nicht geschafft" (Test-Befund F2: exaktes Matching ließ
    den Sklaven in der Followup-Schleife hängen).

    Verneinung wird ZUERST geprüft, sonst matcht "nicht erledigt" auf "erledigt".
    Rückgabe: "ja" / "nein" / None (unklar -> Aufrufer fragt nach).
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    if t.startswith("✅"):
        return "ja"
    if t.startswith("❌"):
        return "nein"
    erstes = _re.split(r"[\s,.!:;–—-]+", t, 1)[0]
    # Verneinung ZUERST (de + en) – sonst matcht "not done" auf "done".
    if (erstes in NEIN
            or "nicht erledigt" in t or "nicht geschafft" in t or "nicht gemacht" in t
            or "not done" in t or "did not" in t or "didn't" in t
            or "could not" in t or "couldn't" in t or "haven't" in t):
        return "nein"
    if (erstes in JA or "erledigt" in t or "geschafft" in t
            or "done" in t or "did it" in t or "finished" in t or "completed" in t):
        return "ja"
    return None
