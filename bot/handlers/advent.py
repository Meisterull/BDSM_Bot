"""
Adventskalender 🎄 – 24 Türchen vom 1. bis 24. Dezember.

/adventskalender [Thema] (Domina) plant den Kalender für den kommenden
Dezember; der kalender_job öffnet dann jeden Morgen EIN Türchen: eine
tagesweise generierte Mini-Aufgabe (limit-geprüft, steigende Intensität
Richtung Heiligabend, Türchen 24 = Finale). Tagesweise statt 24 auf einmal –
so fließen Feedback/Profil-Änderungen bis Dezember mit ein.

Das Anlegen IST das Opt-in (Direkt-Zustellung wie Blitzaufgaben, limits_check
schützt jedes Türchen). /adventskalender stop bricht ab.

Persistenz: progress-Collection, typ='adventskalender',
status geplant → aktiv → fertig/gestoppt; `letzte_tuer` verhindert Doppel.
"""
import logging
import uuid
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes
from zoneinfo import ZoneInfo

from bot import config
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, limits_check, kategorie_logik, embeddings
from bot.prompts import followup as fp
from bot.prompts import coach_persona
from qdrant_client import models as qm
from bot.services.qdrant import client
from bot.messages import t

logger = logging.getLogger(__name__)


def _heute():
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


def _ziel_jahr() -> int:
    """Der kommende (oder laufende) Dezember."""
    heute = _heute()
    return heute.year if (heute.month, heute.day) <= (12, 24) else heute.year + 1


async def _aktueller() -> dict | None:
    results, _ = await qdrant.run_io(client.scroll,
        collection_name="progress",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="typ", match=qm.MatchValue(value="adventskalender")),
        ]),
        limit=10, with_payload=True, with_vectors=False,
    )
    for r in sorted(results, key=lambda x: x.payload.get("erstellt_am", ""), reverse=True):
        if r.payload.get("status") in ("geplant", "aktiv"):
            return r.payload
    return None


async def _save(plan: dict) -> None:
    point_id = plan.get("qdrant_point_id") or str(uuid.uuid4())
    plan["qdrant_point_id"] = point_id
    plan["typ"] = "adventskalender"
    plan["user_id"] = qdrant.mandanten_key("domina")
    vector = await embeddings.get_embedding(f"Adventskalender {plan.get('thema', '')}")
    await qdrant.run_io(client.upsert,
        collection_name="progress",
        points=[qm.PointStruct(id=point_id, vector={"text": vector}, payload=plan)],
    )


async def command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/adventskalender [Thema] – planen; 'stop' – abbrechen; ohne Args – Status."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    args = list(context.args or [])
    plan = await _aktueller()

    if args and args[0].lower() == "stop":
        if not plan:
            await update.message.reply_text(t("ADVENT_KEINER"))
            return
        plan["status"] = "gestoppt"
        await _save(plan)
        await update.message.reply_text(t("ADVENT_GESTOPPT"))
        return

    if plan:
        letzte = plan.get("letzte_tuer", 0)
        await telegram_helper.reply_markdown_safe(
            update.message,
            t("ADVENT_STATUS", jahr=plan.get("jahr", "?"), thema=plan.get("thema", "?"),
              letzte=letzte),
        )
        return

    thema = " ".join(args).strip() or t("ADVENT_DEFAULT_THEMA")
    jahr = _ziel_jahr()
    await _save({
        "kalender_id": str(uuid.uuid4()),
        "jahr": jahr,
        "thema": thema,
        "status": "geplant",
        "letzte_tuer": 0,
        "erstellt_am": datetime.now(timezone.utc).isoformat(),
    })
    await telegram_helper.reply_markdown_safe(
        update.message, t("ADVENT_GEPLANT", jahr=jahr, thema=thema))


# ---------------------------------------------------------------------------
# Scheduler-Integration (kalender_job, täglich morgens)
# ---------------------------------------------------------------------------

def _intensitaet(tuer: int) -> str:
    if tuer >= 24:
        return "das FINALE – Höhepunkt des ganzen Kalenders, besonders und intensiv"
    if tuer >= 17:
        return "deutlich intensiver – die letzte Woche vor dem Finale"
    if tuer >= 9:
        return "spürbar, aber alltagstauglich – die Spannung steigt"
    return "leicht und verspielt – der Kalender fängt sanft an"


async def oeffne_tuerchen(bot) -> None:
    """Öffnet das heutige Türchen (vom kalender_job gerufen)."""
    plan = await _aktueller()
    if not plan:
        return
    heute = _heute()
    if heute.year != plan.get("jahr") or heute.month != 12 or heute.day > 24:
        return
    tuer = heute.day
    if plan.get("letzte_tuer", 0) >= tuer:
        return  # heute schon geöffnet

    sklave_profile = await qdrant.get_user_profile("sklave") or {}
    domina_profile = await qdrant.get_user_profile("domina") or {}
    sk_hl = sklave_profile.get("hard_limits", []) or []
    do_gr = domina_profile.get("grenzen", []) or []

    import random
    pool = kategorie_logik.alle_kategorien(sklave_profile)
    dislikes = kategorie_logik.dislike_kategorien(sklave_profile)
    kategorie = random.choice([k for k in pool if k not in dislikes] or pool)

    system = (
        f"Du öffnest Türchen {tuer} von 24 im Adventskalender für ihren Sklaven "
        f"(Thema: {plan.get('thema', 'Adventskalender')}).\n\n"
        f"{coach_persona.fuer_aufgaben_vorschlag()}\n\n"
        f"Das heutige Türchen soll {_intensitaet(tuer)} sein, im Laufe des Tages "
        f"machbar, aus der vorgegebenen Kategorie, konkret zu IHM passend.\n"
        f"Ein dezenter Advents-/Winterbezug ist willkommen, aber kein Kitsch-Zwang.\n\n"
        f"Antworte NUR mit dem reinen Aufgaben-Text (1-3 Sätze), keine Einleitung, "
        f"kein Markdown, keine Anführungszeichen."
    )
    prompt = (
        f"Pflicht-Kategorie: {kategorie}\n"
        f"{coach_persona.sklaven_kontext_block(sklave_profile, do_gr)}"
    )
    try:
        text = await limits_check.generate_mit_limit_retry(prompt, sk_hl, do_gr, system=system)
        if not text:
            logger.error("Advent-Türchen %d: Generierung limit-blockiert – nächster Lauf", tuer)
            return
        text = grok.clean_text(text)
    except Exception:
        logger.exception("Advent-Türchen %d fehlgeschlagen", tuer)
        return

    try:
        anweisung = await grok.simple(fp.aufgabe_an_sklaven(text), max_tokens=250)
    except Exception:
        logger.exception("aufgabe_an_sklaven (Advent) fehlgeschlagen – Rohtext")
        anweisung = text

    # Task-Anlage direkt vor dem Send, mit Rollback bei Sendefehler – ein nie
    # zugestelltes Türchen darf kein Followup triggern (Trace 06.07., Lücke 5).
    level = domina_profile.get("aktuelles_level", 3)
    point_id = await qdrant.erstelle_task(text, kategorie, level, quelle="advent")
    try:
        await telegram_helper.send_sklave(
            bot, t("ADVENT_TUERCHEN", tuer=tuer, anweisung=anweisung),
            parse_mode="Markdown", voice_text=anweisung,
        )
    except Exception:
        await qdrant.loesche_task(point_id)
        raise

    plan["letzte_tuer"] = tuer
    plan["status"] = "fertig" if tuer >= 24 else "aktiv"
    await _save(plan)
    try:
        await telegram_helper.send_domina(
            bot, t("ADVENT_INFO_DOMINA", tuer=tuer, aufgabe=text))
    except Exception:
        logger.exception("Advent-Info an Domina fehlgeschlagen")
    if tuer >= 24:
        logger.info("Adventskalender abgeschlossen 🎄")
    else:
        logger.info("Advent-Türchen %d geöffnet (Kategorie %s)", tuer, kategorie)
