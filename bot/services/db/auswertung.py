"""
Auswertungen über Aufgaben: Lernkurve, Level-Score, Streak, Bewertungs-Kontext.

Aus bot/services/qdrant.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
Öffentlicher Zugriff weiterhin über die Fassade bot.services.qdrant.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from qdrant_client import models as qm
from bot.services.db import kern, profile, progress

logger = logging.getLogger(__name__)



async def get_lernkurve_daten(user_id: str) -> dict:
    """Auswertung der letzten 2 Wochen für die Lernkurven-Analyse."""
    zwei_wochen_ago = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()

    erledigt_filter = qm.Filter(must=[
        qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
        qm.FieldCondition(key="status", match=qm.MatchValue(value="erledigt")),
        qm.FieldCondition(key="erteilt_am", range=qm.DatetimeRange(gte=zwei_wochen_ago)),
    ])
    import asyncio
    # Scroll + beide Counts parallel (D9/A2). Zählung serverseitig –
    # len(tasks) wäre bei >50 erledigten Tasks zu niedrig.
    (tasks, _), erledigt_result, nicht_erledigt_result = await asyncio.gather(
        kern._aio(kern.client.scroll,
            collection_name="tasks",
            scroll_filter=erledigt_filter,
            # order_by: bei >50 Treffern die NEUESTEN Details statt willkürlicher Teilmenge
            limit=50, order_by=qm.OrderBy(key="erteilt_am", direction="desc"),
            with_payload=True, with_vectors=False,
        ),
        kern._aio(kern.client.count,
            collection_name="tasks",
            count_filter=erledigt_filter,
        ),
        kern._aio(kern.client.count,
            collection_name="tasks",
            count_filter=qm.Filter(must=[
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
                qm.FieldCondition(key="status", match=qm.MatchValue(value="nicht_erledigt")),
                qm.FieldCondition(key="erteilt_am", range=qm.DatetimeRange(gte=zwei_wochen_ago)),
            ]),
        ),
    )

    task_payloads = [r.payload for r in tasks]
    kategorien: dict = {}
    for t in task_payloads:
        kat = t.get("kategorie", "allgemein")
        kategorien[kat] = kategorien.get(kat, 0) + 1

    bewertungen = [t.get("domina_bewertung", 0) for t in task_payloads if t.get("domina_bewertung")]
    avg_bewertung = sum(bewertungen) / len(bewertungen) if bewertungen else 0

    return {
        "erledigt": erledigt_result.count,
        "nicht_erledigt": nicht_erledigt_result.count,
        "kategorien": kategorien,
        "avg_bewertung": round(avg_bewertung, 1),
        "task_details": [t.get("aufgabe", "")[:80] for t in task_payloads[:5]],
    }


async def get_level_score(user_id: str) -> dict:
    """Berechnet einen Gesamt-Score für das dynamische Level-System."""
    task_count = await progress.get_completed_task_count(user_id)

    results, _ = await kern._aio(kern.client.scroll,
        collection_name="tasks",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="erledigt")),
        ]),
        # order_by desc: bei >100 erledigten Tasks deterministisch die neuesten 100
        # (statt arbiträrer Punkt-ID-Teilmenge) für Vielfalt/Bewertungs-Schnitt.
        order_by=qm.OrderBy(key="erteilt_am", direction="desc"),
        limit=100, with_payload=["kategorie", "domina_bewertung"], with_vectors=False,
    )
    payloads = [r.payload for r in results]
    kategorien = set(p.get("kategorie", "allgemein") for p in payloads)
    vielfalt_score = min(len(kategorien), 5)  # gecappt auf 0-5 (fließt *2 ins Level-System)

    # Profil unter demselben Mandanten-Key wie die Tasks (Legacy: "sklave") –
    # vorher hart "sklave", was bei weiteren Paaren das falsche Profil gezogen hätte.
    sklave_profil = await profile.get_user_profile(user_id) or {}
    streak_max = sklave_profil.get("streak_max", 0)
    streak_score = min(streak_max // 5, 5)  # 0-5, alle 5 Streak-Tage 1 Punkt

    bewertungen = [p["domina_bewertung"] for p in payloads if p.get("domina_bewertung")]
    # Nur bewertete Tasks zählen – kein default=3.0 mehr (verzerrt Richtung "normal")
    if bewertungen:
        avg_bewertung = sum(bewertungen) / len(bewertungen)
        bewertungs_score = round(avg_bewertung)  # 1-5
    else:
        bewertungs_score = 3  # Neutral-Fallback nur bei 0 Bewertungen

    gesamt = task_count + (vielfalt_score * 2) + (streak_score * 2) + bewertungs_score

    return {
        "gesamt": gesamt,
        "task_count": task_count,
        "vielfalt": vielfalt_score,
        "streak": streak_score,
        "bewertung": bewertungs_score,
    }


async def get_nicht_erledigt_streak(user_id: str) -> int:
    """Zählt wie viele der letzten ENTSCHIEDENEN Tasks in Folge nicht erledigt
    wurden. Nur erledigt/nicht_erledigt zählen (D9/M6): Warte-/Verwaltungsstatus
    (kette_wartend/serie_wartend/geplant/geloescht/offen/gefragt …) sind keine
    Evidenz in beide Richtungen – ohne den Filter brach z. B. ein frisch
    gestarteter Ketten-Stapel (Glieder 2..n kette_wartend, gleicher erteilt_am)
    die Zählung und die Eskalations-Tonlage feuerte nie."""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="tasks",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
            qm.FieldCondition(key="status", match=qm.MatchAny(any=["erledigt", "nicht_erledigt"])),
        ]),
        limit=10, order_by=qm.OrderBy(key="erteilt_am", direction="desc"),
        with_payload=True, with_vectors=False,
    )
    payloads = [r.payload for r in results]
    streak = 0
    for p in payloads:
        if p.get("status") == "nicht_erledigt":
            streak += 1
        else:
            break
    return streak


BEWERTUNG_NIEDRIG_MIN = 2     # so viele 1-2★-Wertungen je Kategorie braucht es
BEWERTUNG_NIEDRIG_TAGE = 60   # … innerhalb dieses Fensters


def _juenger_als(iso: str, tage: int, jetzt=None) -> bool:
    """True, wenn der ISO-Zeitstempel höchstens `tage` Tage alt ist. Unlesbar/leer
    → False (ohne Datum kein belastbares Signal)."""
    from datetime import datetime, timezone
    try:
        ts = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ((jetzt or datetime.now(timezone.utc)) - ts).days <= tage


async def get_bewertungs_kontext(user_id: str) -> str:
    """Liefert einen Kontext-String über gut/schlecht bewertete Aufgaben-Kategorien."""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="tasks",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="erledigt")),
        ]),
        # order_by desc: bei >20 erledigten Tasks die NEUESTEN nehmen, nicht eine
        # arbiträre Teilmenge in Punkt-ID-Reihenfolge.
        order_by=qm.OrderBy(key="erteilt_am", direction="desc"),
        limit=20, with_payload=True, with_vectors=False,
    )
    payloads = [r.payload for r in results if r.payload.get("domina_bewertung")]
    if not payloads:
        return ""

    # "allgemein" ist die Pseudo-Kategorie des Klassifikations-Fallbacks – als
    # Vorlieben-Signal im Prompt nutzlos (Review D7, B4).
    hoch = [k for p in payloads if p.get("domina_bewertung", 0) >= 4
            if (k := p.get("kategorie", "")) and k != "allgemein"]
    # Niedrig-Signal nur, wenn es belastbar ist (Live-Befund 12.11.2026: eine
    # einzelne, Monate alte Wertung bremste dauerhaft eine Wunsch-Kategorie):
    # mindestens BEWERTUNG_NIEDRIG_MIN Wertungen derselben Kategorie
    # innerhalb der letzten BEWERTUNG_NIEDRIG_TAGE Tage.
    niedrig_zahl: dict[str, int] = {}
    for p in payloads:
        k = p.get("kategorie", "")
        if (p.get("domina_bewertung", 0) <= 2 and k and k != "allgemein"
                and _juenger_als(p.get("erteilt_am", ""), BEWERTUNG_NIEDRIG_TAGE)):
            niedrig_zahl[k] = niedrig_zahl.get(k, 0) + 1
    niedrig = sorted(k for k, n in niedrig_zahl.items() if n >= BEWERTUNG_NIEDRIG_MIN)

    kontext = ""
    if hoch:
        kontext += f"Aufgaben die der Domina gut gefielen (4-5★): {', '.join(set(hoch))}\n"
    if niedrig:
        # "der Domina" ausdrücklich: ohne Zuschreibung las das Modell die Zeile
        # im Sklaven-Kontext als SEINE Abneigung.
        kontext += f"Aufgaben die der Domina zuletzt mehrfach weniger gefielen (1-2★): {', '.join(niedrig)}\n"
    return kontext


async def get_recent_task_kategorien(user_id: str, limit: int = 5) -> list[str]:
    """Kategorien der letzten Tasks (erledigt oder offen) für Ausgleichs-Prüfung."""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="tasks",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id))),
            qm.FieldCondition(key="status", match=qm.MatchAny(any=["erledigt", "offen"])),
        ]),
        limit=20,
        order_by=qm.OrderBy(key="erteilt_am", direction="desc"),
        with_payload=["kategorie"],  # nur das gebrauchte Feld (D9/A3)
        with_vectors=False,
    )
    payloads = [r.payload for r in results]
    return [p.get("kategorie", "allgemein") for p in payloads[:limit]]
