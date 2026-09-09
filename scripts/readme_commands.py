#!/usr/bin/env python3
"""
Befehlstabellen der READMEs aus dem Command-Katalog rendern.

    python3 scripts/readme_commands.py          # README.md (en) + README.de.md (de) aktualisieren
    python3 scripts/readme_commands.py --check  # nur prüfen, Exit 1 wenn veraltet (tests/test_readme_commands.py)

Einzige Datenquelle ist bot/commands_katalog.py (+ bot/locales/commands_en.py
für die englischen Aliase/Beschreibungen) – dieselbe Quelle wie /hilfe und
das Telegram-Menü. Geschrieben wird nur der Block zwischen den Markern
<!-- commands:start --> und <!-- commands:end -->; alles andere bleibt Handarbeit.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bot import commands_katalog as ck  # noqa: E402
from bot.locales import commands_en as en  # noqa: E402

START, END = "<!-- commands:start -->", "<!-- commands:end -->"

ZIELE = {
    "README.md": "en",
    "README.de.md": "de",
}

TEXTE = {
    "en": {
        "dom": "Dominant partner",
        "sub": "Submissive partner",
        "cmd": "Command",
        "was": "What it does",
        "n": "{n} commands",
        "hinweis": ("German command names are the originals; the English aliases shown here "
                    "are registered in every deployment, and so are the German ones – "
                    "`/tasks` and `/aufgaben` both work."),
    },
    "de": {
        "dom": "Dominante Seite",
        "sub": "Devote Seite",
        "cmd": "Befehl",
        "was": "Was er tut",
        "n": "{n} Befehle",
        "hinweis": ("Die deutschen Befehlsnamen sind die Originale; die englischen Aliase "
                    "(z. B. `/tasks` für `/aufgaben`) sind in jeder Installation ebenfalls registriert."),
    },
}


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _zeile(eintrag: ck.Eintrag, sprache: str) -> tuple[str, str]:
    if sprache == "en":
        name = en.ALIASES.get(eintrag.command, eintrag.command)
        kurz_en, lang_en = en.BESCHREIBUNGEN.get(eintrag.command, ("", None))
        text = lang_en if lang_en is not None else (kurz_en or eintrag.hilfe_text)
    else:
        name, text = eintrag.command, eintrag.hilfe_text
    return f"`/{name}`", _md(text)


def _gruppe_titel(titel: str, sprache: str) -> str:
    return en.GRUPPEN.get(titel, titel) if sprache == "en" else titel


def _rolle(gruppen, label: str, sprache: str) -> list[str]:
    t = TEXTE[sprache]
    # Menü ODER Hilfe: /hilfe blendet Opt-in-Schalter (luecken, blitz) aus, die
    # README soll aber jeden tippbaren Befehl zeigen.
    sichtbar = [(name, [e for e in eintraege if e.in_hilfe or e.im_menue])
                for name, eintraege in gruppen]
    anzahl = sum(len(e) for _, e in sichtbar)
    out = ["<details>", f"<summary><b>{label}</b> – {t['n'].format(n=anzahl)}</summary>", ""]
    for name, eintraege in sichtbar:
        if not eintraege:
            continue
        out += [f"**{_gruppe_titel(name, sprache)}**", "", f"| {t['cmd']} | {t['was']} |", "|---|---|"]
        for e in eintraege:
            cmd, text = _zeile(e, sprache)
            out.append(f"| {cmd} | {text} |")
        out.append("")
    out += ["</details>", ""]
    return out


def render(sprache: str) -> str:
    t = TEXTE[sprache]
    zeilen = [START, ""]
    zeilen += _rolle(ck.DOMINA_GRUPPEN, t["dom"], sprache)
    zeilen += _rolle(ck.SKLAVE_GRUPPEN, t["sub"], sprache)
    zeilen += [t["hinweis"], "", END]
    return "\n".join(zeilen)


def eingesetzt(inhalt: str, block: str) -> str:
    a, b = inhalt.find(START), inhalt.find(END)
    if a < 0 or b < 0 or b < a:
        raise SystemExit(f"Marker {START} / {END} fehlen oder stehen falsch herum")
    return inhalt[:a] + block + inhalt[b + len(END):]


def main(argv: list[str]) -> int:
    check = "--check" in argv
    veraltet = []
    for datei, sprache in ZIELE.items():
        pfad = os.path.join(ROOT, datei)
        alt = open(pfad, encoding="utf-8").read()
        neu = eingesetzt(alt, render(sprache))
        if neu == alt:
            continue
        if check:
            veraltet.append(datei)
        else:
            open(pfad, "w", encoding="utf-8").write(neu)
            print(f"aktualisiert: {datei}")
    if check and veraltet:
        print("Befehlstabellen veraltet – bitte `python3 scripts/readme_commands.py` ausführen: "
              + ", ".join(veraltet))
        return 1
    if check:
        print("✅ Befehlstabellen in README.md / README.de.md sind aktuell")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
