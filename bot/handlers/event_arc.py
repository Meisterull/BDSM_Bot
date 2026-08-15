"""
Event-Arcs 🎂 – geplante Storylines mit Ziel-Datum (Geburtstag, Jahrestag,
Adventsauftakt …).

Die Domina plant mit `/event <TT.MM.[JJJJ]> [Tage] <Thema>` ein Ereignis. Der
tägliche Check (event_check_job) startet die Storyline automatisch so, dass
ihr FINALE genau am Event-Tag liegt (generiere_storyline mit event_hinweis –
letzter Tag = Höhepunkt statt Reflexion). Läuft am Starttag noch ein anderer
Arc, wartet der Event und die verbleibenden Tage schrumpfen mit (Minimum 3,
sonst Meldung an die Domina).

Persistenz: progress-Collection, typ='event_arc_plan',
status geplant → gestartet / verpasst; /event listet, /event_loeschen räumt.
"""
import logging
import re
import uuid
from datetime import date, datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes
from zoneinfo import ZoneInfo

from bot import config
from bot.services import paare
from bot.services import qdrant, telegram_helper, embeddings
from qdrant_client import models as qm
from bot.services.qdrant import client
from bot.messages import t

logger = logging.getLogger(__name__)

_DATUM_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})?$")
MIN_TAGE, MAX_TAGE, DEFAULT_TAGE = 3, 7, 5


def _heute() -> date:
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


def parse_datum(text: str) -> date | None:
    """'24.12.' / '24.12.2026' → date; ohne Jahr das NÄCHSTE Vorkommen."""
    m = _DATUM_RE.match((text or "").strip())
    if not m:
        return None
    tag, monat, jahr = int(m.group(1)), int(m.group(2)), m.group(3)
    try:
        if jahr:
            return date(int(jahr), monat, tag)
        heute = _heute()
        kandidat = date(heute.year, monat, tag)
        return kandidat if kandidat >= heute else date(heute.year + 1, monat, tag)
    except ValueError:
        return None


async def _geplante() -> list[dict]:
    results, _ = await qdrant.run_io(client.scroll,
        collection_name="progress",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="typ", match=qm.MatchValue(value="event_arc_plan")),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="geplant")),
            # Mandanten-Filter (Review D8/M3): ohne ihn sähe bei ≥2 Paaren
            # jedes Paar ALLE Pläne und _save_plan würde Fremd-Pläne beim
            # Re-Save auf den falschen Mandanten umschreiben.
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key("domina"))),
        ]),
        limit=20, with_payload=True, with_vectors=False,
    )
    return sorted((r.payload for r in results), key=lambda e: e.get("datum", ""))


async def _save_plan(plan: dict) -> None:
    point_id = plan.get("qdrant_point_id") or str(uuid.uuid4())
    plan["qdrant_point_id"] = point_id
    plan["typ"] = "event_arc_plan"
    plan["user_id"] = qdrant.mandanten_key("domina")
    vector = await embeddings.get_embedding(f"Event {plan.get('thema', '')}")
    await qdrant.run_io(client.upsert,
        collection_name="progress",
        points=[qm.PointStruct(id=point_id, vector={"text": vector}, payload=plan)],
    )


def _liste_text(plaene: list[dict]) -> str:
    zeilen = []
    for i, p in enumerate(plaene, 1):
        datum = p.get("datum", "?")
        try:
            datum = date.fromisoformat(datum).strftime("%d.%m.%Y")
        except ValueError:
            pass
        zeilen.append(f"{i}. *{datum}* – {p.get('thema', '?')} ({p.get('tage_anzahl', '?')} Tage)")
    return "\n".join(zeilen)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/event – ohne Argumente: Liste; mit Argumenten: Event planen."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    args = list(context.args or [])
    if not args:
        plaene = await _geplante()
        if plaene:
            await telegram_helper.reply_markdown_safe(
                update.message, t("EVENT_LISTE", liste=_liste_text(plaene)))
        else:
            await update.message.reply_text(t("EVENT_HILFE"), parse_mode="Markdown")
        return

    datum = parse_datum(args[0])
    if not datum:
        await update.message.reply_text(t("EVENT_DATUM_UNVERSTANDEN"), parse_mode="Markdown")
        return
    args.pop(0)

    tage = DEFAULT_TAGE
    if args and args[0].isdigit() and MIN_TAGE <= int(args[0]) <= MAX_TAGE:
        tage = int(args.pop(0))
    thema = " ".join(args).strip()
    if not thema:
        await update.message.reply_text(t("EVENT_THEMA_FEHLT"), parse_mode="Markdown")
        return

    heute = _heute()
    if datum <= heute:
        await update.message.reply_text(t("EVENT_ZU_SPAET"))
        return

    plan = {
        "event_id": str(uuid.uuid4()),
        "datum": datum.isoformat(),
        "thema": thema,
        "tage_anzahl": tage,
        "status": "geplant",
        "erstellt_am": datetime.now(timezone.utc).isoformat(),
    }
    await _save_plan(plan)
    start = max((datum - heute).days - (tage - 1), 0)
    await telegram_helper.reply_markdown_safe(
        update.message,
        t("EVENT_GEPLANT", datum=datum.strftime("%d.%m.%Y"), thema=thema, tage=tage,
          start_in=start),
    )


async def loeschen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/event_loeschen <nummer> – geplantes Event verwerfen (Nummer aus /event)."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return
    args = context.args or []
    plaene = await _geplante()
    if not (args and args[0].isdigit() and 1 <= int(args[0]) <= len(plaene)):
        if plaene:
            await telegram_helper.reply_markdown_safe(
                update.message, t("EVENT_LOESCHEN_HINWEIS", liste=_liste_text(plaene)))
        else:
            await update.message.reply_text(t("EVENT_KEINE_GEPLANT"))
        return
    plan = plaene[int(args[0]) - 1]
    plan["status"] = "geloescht"
    await _save_plan(plan)
    await update.message.reply_text(t("EVENT_GELOESCHT", thema=plan.get("thema", "?")))


# ---------------------------------------------------------------------------
# Scheduler-Integration (event_check_job)
# ---------------------------------------------------------------------------

async def starte_faellige(bot) -> None:
    """Startet Event-Arcs, deren Startfenster erreicht ist (Finale = Event-Tag).
    Läuft täglich; blockiert ein aktiver Arc, wird am Folgetag mit einem Tag
    weniger gestartet (Minimum 3 Tage, sonst Meldung + Status 'verpasst')."""
    from bot.handlers import arc  # lazy: zirkulären Import vermeiden
    # Guards (D9/N7, Muster luecke/serie): nicht in einen aktiven Domina-Flow
    # oder die Safeword-Pause hineinstarten; lazy wegen Zirkular-Import.
    from bot.scheduler.followup import _flow_aktiv, _nach_llm_verworfen
    if _flow_aktiv(paare.dom_chat_id(), "Event-Arc-Start"):
        return
    plaene = await _geplante()
    if not plaene:
        return
    heute = _heute()

    for plan in plaene:
        try:
            datum = date.fromisoformat(plan.get("datum", ""))
        except ValueError:
            logger.warning("Event-Plan mit kaputtem Datum – übersprungen: %r", plan.get("datum"))
            continue
        verbleibend = (datum - heute).days + 1  # inkl. Event-Tag
        if verbleibend > plan.get("tage_anzahl", DEFAULT_TAGE):
            continue  # noch zu früh
        if verbleibend < MIN_TAGE:
            plan["status"] = "verpasst"
            await _save_plan(plan)
            await telegram_helper.send_domina(
                bot, t("EVENT_VERPASST", thema=plan.get("thema", "?")), parse_mode="Markdown")
            continue

        if await arc._get_aktiver_arc():
            # Anderer Arc läuft – morgen erneut (verbleibend schrumpft mit).
            await telegram_helper.send_domina(
                bot, t("EVENT_WARTET", thema=plan.get("thema", "?")), parse_mode="Markdown")
            continue

        tage_anzahl = min(verbleibend, plan.get("tage_anzahl", DEFAULT_TAGE))
        sklave_profile = await qdrant.get_user_profile("sklave") or {}
        domina_profile = await qdrant.get_user_profile("domina") or {}
        hinweis = f"{plan.get('thema', '')} am {datum.strftime('%d.%m.%Y')}."
        tage, fehler_key, _kw = await arc.generiere_storyline(
            plan.get("thema", ""), tage_anzahl, sklave_profile, domina_profile,
            event_hinweis=hinweis,
        )
        if tage is None:
            logger.error("Event-Arc-Generierung fehlgeschlagen (%s) – nächster Versuch morgen", fehler_key)
            continue

        # Längen-Check (D9/N7): generiere_storyline validiert nur >=3. Liefert
        # das LLM MEHR Tage, läge das Finale nach dem Event-Datum (der Arc
        # rückt 1 Tag/Tag vor) – kürzen und das Finale (letzter Tag) behalten.
        # Bei WENIGER Tagen: verwerfen, morgen mit kleinerem Fenster erneut.
        if len(tage) > tage_anzahl:
            logger.warning("Event-Storyline mit %d statt %d Tagen – kürze (Finale bleibt).",
                           len(tage), tage_anzahl)
            tage = tage[:tage_anzahl - 1] + [tage[-1]]
        elif len(tage) < tage_anzahl:
            logger.error("Event-Storyline mit nur %d statt %d Tagen – nächster Versuch morgen.",
                         len(tage), tage_anzahl)
            continue

        # TOCTOU-Re-Check nach dem (langsamen) Reasoning-Await (D9/N7):
        # Safeword/Flow im Fenster → heute nicht mehr aktivieren/senden.
        if _nach_llm_verworfen(paare.dom_chat_id(), "Event-Arc-Start"):
            return

        await arc.aktiviere_arc(plan.get("thema", ""), tage, extra={
            "event_datum": plan.get("datum"),
            "event_id": plan.get("event_id"),
        })
        plan["status"] = "gestartet"
        await _save_plan(plan)

        uebersicht = "\n".join(
            f"  {i+1}. {tag.get('titel', '?')} ({tag.get('kategorie', '?')})"
            for i, tag in enumerate(tage)
        )
        await telegram_helper.send_domina(
            bot,
            t("EVENT_GESTARTET", thema=plan.get("thema", "?"),
              datum=datum.strftime("%d.%m.%Y"), uebersicht=uebersicht),
            parse_mode="Markdown",
        )
        logger.info("Event-Arc gestartet: %s (Finale %s, %d Tage)",
                    plan.get("thema"), datum, len(tage))
        return  # max. einen pro Tag starten (es kann eh nur ein Arc aktiv sein)