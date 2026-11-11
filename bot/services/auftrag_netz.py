"""Prüf-Netz für Coach-Weitergaben an den Sub ([SPRACHNACHRICHT:]-Tag).

Ein LLM-Coach (lokales Modell wie Grok) schmückt Aufträge gern aus: erfindet
Uhrzeiten, ändert „morgen" in „heute", hängt Zusatzaufgaben an (Tag-Test
09.11.2026 mit einem lokalen Modell). Der Tag-Inhalt geht ungefragt an den Sub – deshalb
wird er hier deterministisch gegen den Wortlaut der Dom-Seite (plus die
letzten Chat-Nachrichten, z. B. ein Coach-Vorschlag, auf den sie sich bezieht)
abgeglichen. Was nicht gedeckt ist, landet in der Liste; der Handler fragt
dann vor dem Zustellen nach.

Bewusst ohne LLM und eher streng: ein Fehltreffer kostet die Dom-Seite einen
Tipp, eine durchgerutschte Erfindung landet beim Sub.
"""
from __future__ import annotations

import re

_ZAHLWORT = {
    "ein": 1, "eine": 1, "einen": 1, "einmal": 1, "zwei": 2, "zweimal": 2, "drei": 3, "dreimal": 3,
    "vier": 4, "viermal": 4, "fünf": 5, "fünfmal": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9,
    "zehn": 10, "zehnmal": 10, "elf": 11, "zwölf": 12, "fünfzehn": 15, "zwanzig": 20, "dreißig": 30,
    "vierzig": 40, "fünfzig": 50, "hundert": 100, "halbe": 30, "halb": 30, "viertel": 15,
}
# „ein/eine" nur als Zahl werten, wenn eine Einheit folgt – sonst Artikel.
_EINHEIT = r"(?:mal|x|minuten?|min|stunden?|std|sekunden?|sek|hiebe?|schläge?|fotos?|bilder?|videos?|tage?|runden?|uhr)"

_TAGE = ("übermorgen", "morgen", "heute", "montag", "dienstag", "mittwoch", "donnerstag", "freitag",
         "samstag", "sonntag", "wochenende", "nächste woche")
_TAGESZEIT = {"früh": "morgens", "morgens": "morgens", "vormittag": "morgens", "mittag": "mittags",
              "mittags": "mittags", "nachmittag": "nachmittags", "nachmittags": "nachmittags",
              "abend": "abends", "abends": "abends", "nacht": "nachts", "nachts": "nachts"}

# Rahmen-/Stimmwörter, die der Coach beim Ausformulieren frei verwenden darf.
_STOP = set("""
kleiner kleine kleines sklave sklaven herrin domina meine meiner meinen meinem deine deiner deinen deinem
dich dir mich mir sich ihm ihr ihn wirst werden wird musst müssen muss darfst dürfen darf sollst sollen soll
kannst können kann willst wollen will heute morgen abend abends nachher später gleich jetzt sofort danach dann
dabei genau schon wieder immer einfach wirklich richtig ganz noch nicht keine kein keinen nichts alles jeden jede
jeder verstanden klar bitte natürlich vergiss vergessen denk denke daran erwarte erwartet erwarten freue freu
freust aufgabe aufgaben nachricht wette wetten heißt also diese dieser dieses deshalb damit sonst außerdem
während bevor nachdem sobald solange bis weil wenn dass warten wartet schön brav gehorsam artig liebe lieber
bekommst bekommt bekommen kriegst gibt geben gebe lass lassen lässt machen macht machst mache tust tun
""".split())


def _norm(t: str) -> str:
    return (t or "").lower().replace("ß", "ss")


def _zahlen(t: str) -> set[int]:
    t = _norm(t)
    out = {int(z) for z in re.findall(r"\d+", t)}
    for w, n in _ZAHLWORT.items():
        w = w.replace("ß", "ss")
        if w in ("ein", "eine", "einen", "halbe", "halb", "viertel"):
            if re.search(rf"\b{w}\s+{_EINHEIT}\b", t):
                out.add(n)
        elif re.search(rf"\b{w}\b", t):
            out.add(n)
    return out


def _zeiten(t: str) -> set[str]:
    t = _norm(t)
    out = set()
    rest = t
    for tag in _TAGE:
        tag = tag.replace("ß", "ss")
        # „morgen früh"/„morgen Abend" = Tag; „am Morgen"/„morgens" = Tageszeit
        if tag == "morgen":
            if re.search(r"(?<!am )(?<!jeden )\bmorgen\b(?!s)", rest):
                out.add("morgen")
            continue
        if re.search(rf"\b{tag}s?\b", rest):
            out.add(tag)
            rest = re.sub(rf"\b{tag}s?\b", " ", rest)
    for w, k in _TAGESZEIT.items():
        if re.search(rf"\b{w}\b", t):
            out.add(k)
    if re.search(r"\b(am|jeden) morgen\b", t):
        out.add("morgens")
    return out


_ENDUNGEN = ("ungen", "ung", "est", "st", "en", "em", "er", "es", "et", "e", "n", "t", "s")


def _stamm(w: str) -> str:
    """Grobe Stammform: Umlaute flach, eine Flexionsendung ab, 5 Zeichen
    („schlägt"/„schlagen" → „schla", „legst"/„legen" → „leg")."""
    w = w.translate(str.maketrans("äöü", "aou"))
    for e in _ENDUNGEN:
        if w.endswith(e) and len(w) - len(e) >= 3:
            w = w[: -len(e)]
            break
    return w[:5]


def _inhaltswoerter(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-zäöü]{5,}", _norm(t)) if w not in _STOP}


def pruefe(inhalt: str, *quellen: str) -> list[str]:
    """Liste der Stellen in `inhalt`, die von den `quellen` (Dom-Nachricht +
    jüngster Verlauf) nicht gedeckt sind. Leer = treu."""
    quelle = " \n".join(q for q in quellen if q)
    funde: list[str] = []
    zq = _zahlen(quelle)
    for z in sorted(_zahlen(inhalt) - zq):
        funde.append(f"Zahl {z}")
    tq = _zeiten(quelle)
    for z in sorted(_zeiten(inhalt) - tq):
        funde.append(f"Zeit „{z}“")
    stq = {_stamm(w) for w in _inhaltswoerter(quelle)}
    neu = sorted({w for w in _inhaltswoerter(inhalt) if _stamm(w) not in stq})
    iw = _inhaltswoerter(inhalt)
    # Umformulierungen sind normal – erst ab 2 neuen Inhaltswörtern bzw. >30 % auffällig.
    if len(neu) >= 3 or (len(neu) >= 2 and len(neu) > 0.3 * max(1, len(iw))):
        funde.append("neu: " + ", ".join(neu[:6]))
    return funde
