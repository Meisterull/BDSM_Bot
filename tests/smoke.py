"""Schneller Harness-Smoke-Test ohne teure LLM-Pfade: /hilfe, Guards, /stats."""
import asyncio

from tests.harness import Harness


async def main():
    h = Harness()
    await h.start()
    await h.send("domina", "/hilfe")
    await h.send("sklave", "/hilfe")
    await h.send("sklave", "/stats")
    await h.send("domina", "/aufgaben")
    await h.send("sklave", "/wuerfel")   # Guard: sollte still bleiben
    print(f"SMOKE OK – {len(h.fehler_liste)} Fehler", flush=True)
    if h.fehler_liste:
        print(h.fehler_liste[0], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
