"""
Arc / Storyline Handler – mehrtägige zusammenhängende Aufgaben-Arcs.

Die Domina startet einen Arc mit einem Thema. Die KI generiert daraus eine
3-7-tägige Storyline. Pro Tag wird ein Arc-Tag aktiviert (statt zufälliger
Kategorie-Rotation). Nach Abschluss aller Tage gilt der Arc als 'abgeschlossen'.

Datenmodell: Arcs werden als Einträge in der `progress` Collection gespeichert
mit `typ: "arc"`. Der aktive Arc wird im Domina-Profil unter `aktiver_arc_id` referenziert.
"""
import logging
import uuid
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from bot import config
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, embeddings, kategorie_logik
from qdrant_client import models as qm
from bot.services.qdrant import client
from bot.messages import t

logger = logging.getLogger(__name__)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/arc – zeigt aktiven Arc oder lässt einen neuen starten."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    aktiver_arc = await _get_aktiver_arc()

    if aktiver_arc:
        tag_aktuell = aktiver_arc.get("tag_aktuell", 1)
        tage_gesamt = aktiver_arc.get("tage_gesamt", 0)
        thema = aktiver_arc.get("thema", "?")
        tage_text = "\n".join(
            f"{i+1}. {tag.get('titel','?')} – _{tag.get('kategorie','?')}_"
            + ("  ✅" if i + 1 < tag_aktuell else ("  ▶️" if i + 1 == tag_aktuell else ""))
            for i, tag in enumerate(aktiver_arc.get("tage", []))
        )
        await telegram_helper.reply_markdown_safe(
            update.message,
            t("ARC_STATUS", thema=thema, tag_aktuell=tag_aktuell,
              tage_gesamt=tage_gesamt, tage_text=tage_text),
        )
        return

    await update.message.reply_text(t("ARC_HILFE"), parse_mode="Markdown")


async def starten(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/arc_starten <thema> – generiert eine neue Storyline."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    # Optionale Tage-Anzahl als erstes Argument (3-7), z.B. `/arc_starten 7 <thema>` –
    # die UI verspricht 3-7 Tage, der Prompt erzwang vorher immer genau 5.
    args = list(context.args or [])
    anzahl = 5
    if args and args[0].isdigit() and 3 <= int(args[0]) <= 7:
        anzahl = int(args.pop(0))
    thema = " ".join(args).strip()
    if not thema:
        await update.message.reply_text(t("ARC_THEMA_FEHLT"), parse_mode="Markdown")
        return

    # Läuft schon ein Arc, nicht kommentarlos umbiegen – der alte bliebe als
    # status="aktiv"-Datenleiche liegen und die Domina merkt nichts davon.
    aktiver = await _get_aktiver_arc()
    if aktiver:
        await update.message.reply_text(
            t("ARC_BEREITS_AKTIV", thema=aktiver.get("thema", "?")), parse_mode="Markdown"
        )
        return

    sklave_profile = await qdrant.get_user_profile("sklave") or {}
    domina_profile = await qdrant.get_user_profile("domina") or {}

    await telegram_helper.reply_markdown_safe(update.message, t("ARC_GENERIERE", thema=thema))

    tage, fehler_key, fehler_kwargs = await generiere_storyline(
        thema, anzahl, sklave_profile, domina_profile)
    if tage is None:
        await update.message.reply_text(t(fehler_key, **fehler_kwargs))
        return

    await aktiviere_arc(thema, tage)

    uebersicht = "\n".join(
        f"  {i+1}. {t.get('titel','?')} ({t.get('kategorie','?')})"
        for i, t in enumerate(tage)
    )
    await telegram_helper.reply_markdown_safe(
        update.message,
        t("ARC_GESTARTET", thema=thema, uebersicht=uebersicht),
    )


async def beenden(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/arc_beenden – beendet die aktive Storyline vorzeitig."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    aktiver_arc = await _get_aktiver_arc()
    if not aktiver_arc:
        await update.message.reply_text(t("ARC_KEINE_AKTIV"))
        return

    # Erst die Profil-Referenz lösen (gezielt, kein Full-Upsert), dann den Arc
    # markieren – so liest der Scheduler den Arc nie mehr als aktiv.
    await qdrant.patch_profile_fields("domina", {"aktiver_arc_id": None})
    await _arc_status_setzen(aktiver_arc, "abgebrochen")

    await telegram_helper.reply_markdown_safe(
        update.message,
        t("ARC_BEENDET", thema=aktiver_arc.get("thema", "?")),
    )


# ---------------------------------------------------------------------------
# Storyline-Generierung + Aktivierung (geteilt mit event_arc.py)
# ---------------------------------------------------------------------------

async def generiere_storyline(
    thema: str,
    anzahl: int,
    sklave_profile: dict,
    domina_profile: dict,
    event_hinweis: str = "",
) -> tuple[list | None, str, dict]:
    """Generiert die Tage-Liste einer Storyline (limit-geprüft, JSON-validiert).
    `event_hinweis` verschiebt das Finale auf den Event-Tag (Event-Arcs).
    Returns (tage, "", {}) bei Erfolg, sonst (None, fehler_key, fehler_kwargs)."""
    from bot.prompts import coach_persona
    from bot.services import limits_check
    sk_hl = sklave_profile.get('hard_limits', []) or []
    do_gr = domina_profile.get('grenzen', []) or []

    if event_hinweis:
        bogen = (
            f"Die Storyline führt auf ein reales Ereignis zu: {event_hinweis}\n"
            f"Der LETZTE Tag (Tag {anzahl}) IST der Event-Tag – dort liegt der Höhepunkt/das Finale.\n"
            f"  Tag 1: Einstieg, Vorfreude aufbauen\n"
            f"  mittlere Tage: Vertiefung, Steigerung, wachsende Spannung auf den Event-Tag\n"
            f"  Tag {anzahl}: Höhepunkt/Finale AM Event-Tag selbst\n"
        )
    else:
        bogen = (
            f"Die {anzahl} Aufgaben sollen einen erkennbaren narrativen Bogen bilden:\n"
            f"  Tag 1: Einstieg, niedrigschwellig\n"
            f"  mittlere Tage: Vertiefung, Steigerung\n"
            f"  Tag {anzahl - 1}: Höhepunkt\n"
            f"  Tag {anzahl}: Reflexion, Integration\n"
        )

    system = (
        f"Du baust eine zusammenhängende Storyline aus {anzahl} Aufgaben zum vorgegebenen Thema.\n\n"
        f"{coach_persona.fuer_aufgaben_vorschlag()}\n\n"
        f"Verfügbare Kategorien (jede Aufgabe muss aus einer dieser stammen):\n"
        f"{', '.join(kategorie_logik.alle_kategorien(sklave_profile))}\n\n"
        f"{bogen}\n"
        f"Jede Aufgabe muss konkret zu IHM passen – kein generischer 101-Task ('20 Kniebeugen', 'kalt duschen').\n\n"
        f"Antworte NUR als JSON-Array mit genau {anzahl} Objekten in dieser Form:\n"
        f"[{{\"titel\":\"...\",\"aufgabe\":\"...\",\"kategorie\":\"...\",\"level\":3}},...]\n"
        f"Kein Text außerhalb des JSON. Keine Markdown-Codeblöcke."
    )
    prompt = (
        f"Thema der Storyline: '{thema}'\n\n"
        f"{coach_persona.sklaven_kontext_block(sklave_profile, do_gr)}"
    )
    # Kategorie wählt das LLM erst beim Generieren → alle vorhandenen Wissens-Briefe beilegen.
    skill_block = await coach_persona.skill_kontext_block()
    if skill_block:
        prompt += "\n\n" + skill_block

    antwort = None
    try:
        # Limits-Check auf gesamter Antwort, bei Verletzung einmal verschärft re-generieren.
        antwort = await limits_check.generate_mit_limit_retry(prompt, sk_hl, do_gr, system=system, reasoning=True)
        if antwort is None:
            return None, "ARC_LIMIT_ABBRUCH", {}

        tage = grok.parse_json(antwort)
        if not isinstance(tage, list) or len(tage) < 3:
            raise ValueError("Ungültige Storyline-Antwort")

        # Pro-Tag-Check als zusaetzliche Sicherheit
        verletzte_tage = []
        for i, tag in enumerate(tage, 1):
            tag_text = f"{tag.get('titel','')} {tag.get('aufgabe','')}"
            if await limits_check.verletzungen(tag_text, sk_hl, do_gr):
                verletzte_tage.append(i)
        if verletzte_tage:
            logger.error("Storyline enthielt einzelne Grenzen-verletzende Tage %s – verworfen.", verletzte_tage)
            return None, "ARC_TAGE_VERLETZT", {"tage": verletzte_tage}
        return tage, "", {}
    except Exception as e:
        # Rohantwort nur auf DEBUG – sie enthält generierte intime Inhalte (D9/S8).
        logger.error("Fehler beim Generieren der Storyline: %s (Antwort: %d Zeichen)", e, len(antwort or ""))
        logger.debug("Storyline-Rohantwort: %s", antwort)
        return None, "ARC_FEHLER", {}


async def aktiviere_arc(thema: str, tage: list, extra: dict | None = None) -> str:
    """Speichert die Storyline als aktiven Arc + setzt die Profil-Referenz.
    `extra` ergänzt Arc-Felder (z.B. event_datum bei Event-Arcs)."""
    arc_id = str(uuid.uuid4())
    arc_data = {
        "arc_id": arc_id,
        "thema": thema,
        "tage": tage,
        "tage_gesamt": len(tage),
        "tag_aktuell": 1,
        "status": "aktiv",
        "erstellt_am": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }
    await _save_arc(arc_data)
    # Im Domina-Profil als aktiv markieren – gezielt patchen, kein Full-Upsert
    # mit einem (LLM-Generierung alten) Read.
    await qdrant.patch_profile_fields("domina", {"aktiver_arc_id": arc_id})
    return arc_id


# ---------------------------------------------------------------------------
# Public Helpers für Scheduler-Integration
# ---------------------------------------------------------------------------

async def get_aktueller_arc_tag() -> dict | None:
    """Gibt den aktuellen Arc-Tag (oder None) zurück. Wird vom Scheduler genutzt."""
    arc = await _get_aktiver_arc()
    if not arc:
        return None
    tag_idx = arc.get("tag_aktuell", 1) - 1
    tage = arc.get("tage", [])
    if tag_idx < 0 or tag_idx >= len(tage):
        return None
    eintrag = dict(tage[tag_idx])
    eintrag["arc_thema"] = arc.get("thema", "")
    eintrag["arc_id"] = arc.get("arc_id", "")
    eintrag["arc_tag"] = tag_idx + 1
    eintrag["arc_gesamt"] = arc.get("tage_gesamt", len(tage))
    return eintrag


async def arc_tag_voranschreiten() -> dict | None:
    """Inkrementiert tag_aktuell. Schließt Arc ab wenn alle Tage durch sind. Returns abgeschlossener Arc-Eintrag oder None."""
    arc = await _get_aktiver_arc()
    if not arc:
        return None
    neuer_tag = arc.get("tag_aktuell", 1) + 1
    if neuer_tag > arc.get("tage_gesamt", 0):
        # Arc komplett – Status auf abgeschlossen
        arc["status"] = "abgeschlossen"
        arc["abgeschlossen_am"] = datetime.now(timezone.utc).isoformat()
        await _save_arc(arc)

        await qdrant.patch_profile_fields("domina", {"aktiver_arc_id": None})
        logger.info("Arc abgeschlossen: %s", arc.get("thema"))
        return arc

    arc["tag_aktuell"] = neuer_tag
    await _save_arc(arc)
    return None


# ---------------------------------------------------------------------------
# Interne Persistenz – Arcs werden in 'progress' Collection abgelegt
# ---------------------------------------------------------------------------

async def _get_aktiver_arc() -> dict | None:
    """Holt den aktuell aktiven Arc."""
    domina_profile = await qdrant.get_user_profile("domina") or {}
    arc_id = domina_profile.get("aktiver_arc_id")
    if not arc_id:
        return None

    results, _ = await qdrant.run_io(client.scroll,
        collection_name="progress",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="typ", match=qm.MatchValue(value="arc")),
            qm.FieldCondition(key="arc_id", match=qm.MatchValue(value=arc_id)),
            # Defense-in-Depth (Review D8/M3): arc_id ist eine profil-referenzierte
            # UUID, der Mandanten-Filter schützt zusätzlich wie überall sonst.
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key("domina"))),
        ]),
        limit=1, with_payload=True, with_vectors=False,
    )
    if not results:
        return None
    arc = results[0].payload
    # Referenz im Profil kann kurz auf einen schon beendeten Arc zeigen
    # (beenden/abschliessen schreiben zwei Stellen) – nie als aktiv zurückgeben.
    if arc.get("status") not in (None, "aktiv"):
        return None
    return arc


async def _save_arc(arc_data: dict) -> None:
    """Upsert eines Arcs in die progress collection."""
    arc_id = arc_data["arc_id"]
    text = f"Arc {arc_data.get('thema','')}"
    vector = await embeddings.get_embedding(text)

    point_id = arc_data.get("qdrant_point_id") or str(uuid.uuid4())
    arc_data["qdrant_point_id"] = point_id
    arc_data["typ"] = "arc"
    arc_data["user_id"] = qdrant.mandanten_key("domina")

    await qdrant.run_io(client.upsert, 
        collection_name="progress",
        points=[qm.PointStruct(
            id=point_id,
            vector={"text": vector},  # progress-Collection nutzt named vectors
            payload=arc_data,
        )],
    )


async def _arc_status_setzen(arc: dict, status: str) -> None:
    """Setzt den Status auf dem ÜBERGEBENEN Arc (kein Re-Fetch über das Profil –
    der Aufrufer hat die Referenz dort ggf. schon gelöst)."""
    arc["status"] = status
    arc[f"{status}_am"] = datetime.now(timezone.utc).isoformat()
    await _save_arc(arc)
