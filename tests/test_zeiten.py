"""
Regressions-Tests für die Zeitfenster-Prüfung (Befund 03.09.2026: die
kinderfreien Zeiten standen als Freitext 'Täglich ab 20:00' im Profil,
_parse_fenster verstand nur 'HH:MM-HH:MM' → ist_im_fenster war IMMER False,
Spiel-Impuls und Blitz konnten nie senden — fail-closed, aber lautlos).

  Z1 – Die klassischen 'HH:MM-HH:MM'-Formen funktionieren weiter,
       inklusive Über-Mitternacht.
  Z2 – Halboffene Freitext-Formen: 'ab HH[:MM]' (bis 23:59) und
       'bis HH[:MM]' (ab 00:00), mit Präfixen wie 'Täglich' und 'Uhr'.
  Z3 – 'abends' wird NICHT als 'ab ends' gelesen; Murks bleibt Murks
       (und sperrt als einziger Eintrag weiterhin alles).

Läuft lokal ohne Bot-Deps:
    python3 tests/test_zeiten.py
"""
import os
import sys
from datetime import datetime, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.services import zeiten  # noqa: E402


def um(hh, mm=0):
    return datetime(2026, 9, 3, hh, mm)


fehler = 0


def pruefe(name, bedingung):
    global fehler
    if not bedingung:
        fehler += 1
    print(f"  {'✓' if bedingung else '✗'} {name}")


print("Z1 – klassische Fenster:")
pruefe("'20:00-23:00' parst", zeiten._parse_fenster("20:00-23:00") == (time(20), time(23)))
pruefe("drin um 20:30", zeiten.ist_im_fenster(um(20, 30), ["20:00-23:00"]))
pruefe("draußen um 19:59", not zeiten.ist_im_fenster(um(19, 59), ["20:00-23:00"]))
pruefe("über Mitternacht: 23:30 in '21:00-06:00'",
       zeiten.ist_im_fenster(um(23, 30), ["21:00-06:00"]))

print("Z2 – halboffene Freitext-Formen:")
pruefe("'Täglich ab 20:00' parst als (20:00, 23:59)",
       zeiten._parse_fenster("Täglich ab 20:00") == (time(20), time(23, 59)))
pruefe("'ab 20 Uhr' ohne Minuten", zeiten._parse_fenster("ab 20 Uhr") == (time(20), time(23, 59)))
pruefe("'bis 07:00' parst als (00:00, 07:00)",
       zeiten._parse_fenster("bis 07:00") == (time(0), time(7)))
pruefe("drin um 20:30 bei 'Täglich ab 20:00'",
       zeiten.ist_im_fenster(um(20, 30), ["Täglich ab 20:00"]))
pruefe("draußen um 17:48 bei 'Täglich ab 20:00'",
       not zeiten.ist_im_fenster(um(17, 48), ["Täglich ab 20:00"]))
pruefe("drin um 06:00 bei 'bis 07:00'", zeiten.ist_im_fenster(um(6), ["bis 07:00"]))

print("Z3 – Murks bleibt Murks:")
pruefe("'abends' parst NICHT", zeiten._parse_fenster("abends") is None)
pruefe("'ab 25:00' parst nicht", zeiten._parse_fenster("ab 25:00") is None)
pruefe("Murks als einziger Eintrag sperrt weiterhin",
       not zeiten.ist_im_fenster(um(20, 30), ["irgendwann mal"]))
pruefe("leere Liste bleibt immer frei", zeiten.ist_im_fenster(um(3), []))

print(f"\n{'Alles grün' if fehler == 0 else f'{fehler} Fehler'}")
sys.exit(1 if fehler else 0)
