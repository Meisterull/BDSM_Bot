"""
Führt ALLE tests/test_*.py nacheinander als eigene Prozesse aus und fasst
zusammen. Neue Testdateien werden automatisch gefunden – nichts mehr im
Makefile nachtragen.

    python3 tests/run_alle.py            # alle
    python3 tests/run_alle.py waehrung   # nur Dateien, deren Name das enthält
"""
import glob
import os
import subprocess
import sys
import time

HIER = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    filter_ = sys.argv[1:] or [""]
    dateien = sorted(glob.glob(os.path.join(HIER, "test_*.py")))
    dateien = [d for d in dateien if any(f in os.path.basename(d) for f in filter_)]
    rot: list[tuple[str, str]] = []
    start = time.time()
    for pfad in dateien:
        name = os.path.basename(pfad)
        t0 = time.time()
        p = subprocess.run([sys.executable, pfad], capture_output=True, text=True,
                           cwd=os.path.dirname(HIER))
        dauer = time.time() - t0
        if p.returncode == 0:
            print(f"✅ {name:40s} {dauer:5.1f}s")
        else:
            print(f"❌ {name:40s} {dauer:5.1f}s  (rc={p.returncode})")
            rot.append((name, (p.stdout + p.stderr)[-3000:]))
    print(f"\n{len(dateien) - len(rot)}/{len(dateien)} Testdateien grün "
          f"in {time.time() - start:.1f}s")
    for name, ausgabe in rot:
        print(f"\n===== {name} =====\n{ausgabe}")
    return 1 if rot else 0


if __name__ == "__main__":
    sys.exit(main())
