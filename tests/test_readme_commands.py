"""
README-Konsistenz (2026-09-10): die Befehlstabellen in README.md (en) und
README.de.md (de) werden aus bot/commands_katalog.py gerendert
(scripts/readme_commands.py) und müssen mit dem Katalog übereinstimmen –
sonst veraltet die Doku still, sobald ein Command dazukommt. Zusätzlich:
beide READMEs verlinken sich gegenseitig und tragen die Marker.

    python3 tests/test_readme_commands.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_tabellen_aktuell():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "readme_commands.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_marker_und_sprachlinks():
    en = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    de = open(os.path.join(ROOT, "README.de.md"), encoding="utf-8").read()
    for text in (en, de):
        assert "<!-- commands:start -->" in text and "<!-- commands:end -->" in text
    assert "README.de.md" in en, "englische README muss auf die deutsche verlinken"
    assert "README.md" in de, "deutsche README muss auf die englische verlinken"
    # Jeder Katalog-Command mit in_hilfe steht in beiden Tabellen (de: Original, en: Alias)
    from bot import commands_katalog as ck
    from bot.locales import commands_en as cen
    for gruppen in (ck.DOMINA_GRUPPEN, ck.SKLAVE_GRUPPEN):
        for _, eintraege in gruppen:
            for e in eintraege:
                if not (e.in_hilfe or e.im_menue):
                    continue
                assert f"`/{e.command}`" in de, f"/{e.command} fehlt in README.de.md"
                assert f"`/{cen.ALIASES.get(e.command, e.command)}`" in en, f"/{e.command} fehlt in README.md"


def _run():
    test_tabellen_aktuell()
    test_marker_und_sprachlinks()
    print("✅ Alle README-Tests bestanden")


if __name__ == "__main__":
    _run()
