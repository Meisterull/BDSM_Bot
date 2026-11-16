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

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, limits_check, kategorie_logik, embeddings
from bot.services import kontext_pflege
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
            # Mandanten-Filter (Review D8/M3): sonst öffnet Paar 1 bei ≥2
            # Paaren die Türchen des anderen Paares im falschen Chat.
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key("domina"))),
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
        # Der 24. ist Heiligabend: ein Finale mit Bedeutung statt Härte, das sich
        # diskret und ohne großen Aufwand in den Tag fügt (Review D11).
        return ("das FINALE des Kalenders – etwas Besonderes mit Bedeutung statt Härte; es ist "
                "Heiligabend, also diskret und ohne großen Aufwand machbar")
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

    # Safeword-Guard (D9/M8): während der Pause weder generieren noch zustellen.
    # letzte_tuer bleibt unberührt – hält die Pause bis zum nächsten 08:00-Lauf,
    # entfällt das Türchen (bewusst: Safeword gewinnt gegen den Kalender).
    # Ein aktiver Sklaven-UI-Flow blockt dagegen NICHT: der Job läuft nur 1×
    # täglich, das Türchen setzt keinen Mode – reines Interleaving (akzeptierte
    # Klasse), ein Skip würde das Türchen dagegen komplett verschlucken.
    if state.is_paused():
        logger.info("Advent-Türchen %d übersprungen – System per Safeword pausiert.", tuer)
        return

    sklave_profile = await qdrant.get_user_profile("sklave") or {}
    domina_profile = await qdrant.get_user_profile("domina") or {}
    sk_hl = sklave_profile.get("hard_limits", []) or []
    do_gr = domina_profile.get("grenzen", []) or []

    import random

    # Bisherige Türchen dieses Kalenders: für die Abwechslung der Kategorien
    # und als „nicht wiederholen"-Liste im Prompt (vorher kannte kein Türchen
    # die vorigen, Review D11).
    try:
        bisher = [
            x for x in await qdrant.get_tasks_by_status(
                ["erledigt", "offen", "gefragt", "nicht_erledigt"], limit=24, sort_by_datum=True, quelle="advent")
            if (kontext_pflege.alter_tage(x.get("erteilt_am", "")) or 0) <= 30
        ]
    except Exception:
        logger.exception("Advent: bisherige Türchen nicht ladbar (weiter ohne)")
        bisher = []
    bisher_zeilen = [" ".join((x.get("aufgabe") or "").split())[:90] for x in bisher if x.get("aufgabe")][:12]

    # Kategorie gewichtet wie beim Tages-Vorschlag (Vorlieben, „selten", zuletzt
    # Bedientes gedämpft) statt blind aus allen gewürfelt – und nur Kategorien,
    # die er ALLEIN umsetzen kann: das Türchen geht morgens direkt an ihn.
    kandidaten = kategorie_logik.gewichtete_auswahl(
        sklave_profile, [x.get("kategorie") for x in bisher if x.get("kategorie")], count=6,
        domina_praeferenzen=domina_profile.get("kategorie_praeferenzen", {}) or {})
    allein = kategorie_logik.allein_machbar(kandidaten)
    if not allein:
        pool = kategorie_logik.alle_kategorien(sklave_profile)
        gesperrt = set(kategorie_logik.dislike_kategorien(sklave_profile))
        allein = [random.choice(kategorie_logik.allein_machbar([k for k in pool if k not in gesperrt]) or pool)]
    kategorie = allein[0]

    system = (
        f"Du öffnest Türchen {tuer} von 24 im Adventskalender für ihren Sklaven "
        f"(Thema: {plan.get('thema', 'Adventskalender')}).\n\n"
        f"{coach_persona.fuer_aufgaben_vorschlag()}\n\n"
        f"Intensität heute: {_intensitaet(tuer)}.\n"
        f"Das Türchen ist im Laufe des Tages machbar, stammt aus der vorgegebenen Kategorie und passt konkret zu IHM.\n"
        f"ALLEIN machbar: die Dom-Seite ist tagsüber nicht dabei und muss nichts tun – nichts, wofür er sie, "
        f"ihren Körper oder ihre Mitwirkung braucht (kontrollieren darf sie später).\n"
        f"Ein dezenter Advents-/Winterbezug ist willkommen, aber kein Kitsch-Zwang.\n\n"
        f"Antworte NUR mit dem reinen Aufgaben-Text (1-3 Sätze), keine Einleitung, "
        f"kein Markdown, keine Anführungszeichen."
    )
    prompt = (
        f"Pflicht-Kategorie: {kategorie}\n"
        f"{coach_persona.sklaven_kontext_block(sklave_profile, do_gr, domina_profile=domina_profile)}"
    )
    if bisher_zeilen:
        prompt += ("\nBisherige Türchen dieses Kalenders (NICHT wiederholen, auch keine Variation):\n"
                   + "\n".join(f"- {z}" for z in bisher_zeilen))
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

    # TOCTOU-Re-Check nach den LLM-Awaits (D9/M8): Safeword im
    # Generierungs-Fenster → nichts anlegen, nichts senden.
    if state.is_paused():
        logger.info("Advent-Türchen %d nach Generierung verworfen – Safeword im LLM-Fenster.", tuer)
        return

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
