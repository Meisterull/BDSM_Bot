"""
Wartungs-Jobs: Trainings-Erinnerung, Backup, Sklaven-Dossier, offene Fäden.

Aus bot/scheduler/followup.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
"""
import logging
from datetime import datetime, timezone, timedelta
from telegram import Bot
from qdrant_client import models as qm
from bot.services.qdrant import client
from bot import config
from bot.services import qdrant, telegram_helper, kategorie_logik
from bot.messages import t
from bot.scheduler import basis

logger = logging.getLogger(__name__)


@basis._job_guard
async def training_erinnerung_job(bot: Bot) -> None:
    """Täglich – erinnert die Domina wenn seit 4 Tagen kein Task zugewiesen wurde."""
    try:
        vier_tage_ago = (datetime.now(timezone.utc) - timedelta(days=config.TRAINING_ERINNERUNG_TAGE)).isoformat()
        results, _ = await qdrant.run_io(client.scroll, 
            collection_name="tasks",
            scroll_filter=qm.Filter(must=[
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key("sklave"))),
                qm.FieldCondition(key="erteilt_am", range=qm.DatetimeRange(gte=vier_tage_ago)),
            ]),
            limit=1, with_payload=False, with_vectors=False,
        )
        if results:
            return
        logger.info("Training-Erinnerung: Kein Task seit 4 Tagen – sende Erinnerung.")
        await telegram_helper.send_domina(
            bot, t("ERINNERUNG_KEINE_AUFGABE", tage=config.TRAINING_ERINNERUNG_TAGE),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.exception("Fehler beim Training-Erinnerungs-Job")


@basis._job_fangnetz
async def backup_job(bot: Bot) -> None:
    """Tägliches Qdrant-Backup (JSON-Export + native Snapshots). Meldet Fehler an die Domina."""
    from bot.services import backup
    try:
        bericht = await backup.run_backup()
        logger.info("backup_job ok: %s", bericht)
    except Exception:
        logger.exception("backup_job fehlgeschlagen")
        try:
            await telegram_helper.send_domina(bot, t("BACKUP_FEHLGESCHLAGEN"))
        except Exception:
            logger.exception("Konnte Backup-Fehler nicht an Domina melden")


@basis._job_guard
async def sklave_dossier_job(bot: Bot) -> None:
    """Wöchentlich – Charakteristiken (Sklave + Domina) verdichten und die
    Kategorie-Reaktionen sanft altern lassen (Recency)."""
    from bot.handlers import dossier
    try:
        text = await dossier.aktualisiere_dossier()
        logger.info("Sklaven-Dossier: %s", "aktualisiert" if text else "zu wenig Material")
    except Exception:
        logger.exception("sklave_dossier_job (Sklave) fehlgeschlagen")
    try:
        dtext = await dossier.aktualisiere_domina_dossier()
        logger.info("Domina-Dossier: %s", "aktualisiert" if dtext else "zu wenig Material")
    except Exception:
        logger.exception("sklave_dossier_job (Domina) fehlgeschlagen")
    try:
        n = await kategorie_logik.decay_profil_reaktionen("sklave")
        logger.info("Kategorie-Reaktionen gealtert (Recency): %d Kategorien aktiv.", n)
    except Exception:
        logger.exception("sklave_dossier_job (Decay) fehlgeschlagen")
    try:
        verfallen = await kategorie_logik.pflege_dislike_tags("sklave")
        if verfallen:
            logger.info("Abneigungs-Tags verfallen (kein Signal seit %d Tagen): %d",
                        kategorie_logik.DISLIKE_TAG_TAGE, len(verfallen))
    except Exception:
        logger.exception("sklave_dossier_job (Tag-Pflege) fehlgeschlagen")


@basis._job_guard
async def offene_faeden_job(bot: Bot) -> None:
    """Täglich – extrahiert offene Fäden aus jüngsten Gesprächen, damit die Herrin
    von sich aus darauf zurückkommt."""
    from bot.handlers import dossier
    try:
        faeden = await dossier.aktualisiere_offene_faeden()
        logger.info("Offene Fäden aktualisiert: %d", len(faeden))
    except Exception:
        logger.exception("offene_faeden_job fehlgeschlagen")
