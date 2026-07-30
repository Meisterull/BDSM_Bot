"""
Reaktions-Sticker der Herrin 🎭

Ein festes Set wiederverwendbarer Sticker als nonverbale Reaktion an den
Sklaven (Lob, Spott, Strafe, …). Das Mapping Tag → Telegram-file_id liegt in
REAKTIONS_STICKER_FILE (erstellt via scripts/sticker_upload.py); file_ids sind
bot-weit gültig, ein Set bedient also alle Paare.

Best-effort wie domina_relay: fehlende Datei, unbekannter Tag oder ein
Sende-Fehler sind still – ein Sticker blockiert nie den Hauptpfad. Ein neu
hochgeladenes Set greift ohne Neustart (mtime-Check beim Zugriff).
"""
import json
import logging
import os
import random

from telegram import Bot

from bot import config

logger = logging.getLogger(__name__)

# Tags = Dateinamen im Sticker-Set (siehe Manifest in scripts/sticker_upload.py)
LOB = "lob"                  # Aufgabe erledigt, zufriedene Herrin
SPOTT = "spott"              # schwache Ausrede, Jammern
STRENG = "streng"            # Regelverstoß, Ton daneben
STRAFE = "strafe"            # Deadline gerissen, Strafe folgt
WARTEN = "warten"            # Erinnerung, ungeduldiges Warten
BEFEHL = "befehl"            # neue Aufgabe / Anweisung
AUGE = "auge"                # Kontrolle, "ich sehe alles"
GNADE = "gnade"              # Belohnung, Privileg gewährt
AUGENROLLEN = "augenrollen"  # Nachverhandeln
SCHICKSAL = "schicksal"      # Würfel, Roulette, Wette

_cache: dict[str, str] = {}
_cache_mtime: float | None = None


def _mapping() -> dict[str, str]:
    """Lädt das Tag→file_id-Mapping, gecacht über den Datei-mtime."""
    global _cache, _cache_mtime
    try:
        mtime = os.path.getmtime(config.REAKTIONS_STICKER_FILE)
    except OSError:
        return {}
    if mtime != _cache_mtime:
        try:
            with open(config.REAKTIONS_STICKER_FILE, encoding="utf-8") as f:
                daten = json.load(f)
            _cache = {k: v for k, v in daten.get("sticker", {}).items()
                      if isinstance(k, str) and isinstance(v, str) and v}
            _cache_mtime = mtime
            logger.info("Reaktions-Sticker geladen: %d Tags", len(_cache))
        except Exception:
            logger.exception("Reaktions-Sticker-Datei unlesbar: %s",
                             config.REAKTIONS_STICKER_FILE)
            return {}
    return _cache


def verfuegbar(tag: str) -> bool:
    return tag in _mapping()


async def sende_sklave(bot: Bot, tag: str, chance: float = 1.0) -> bool:
    """Sendet den Reaktions-Sticker `tag` an die Sub-Seite des aktiven Paares.

    `chance` < 1.0 hält häufige Trigger (tägliches Lob) unregelmäßig – ein
    Sticker bei JEDER Erledigung würde schnell mechanisch wirken.
    True nur, wenn tatsächlich gesendet wurde.
    """
    file_id = _mapping().get(tag)
    if not file_id:
        return False
    if chance < 1.0 and random.random() > chance:
        return False
    try:
        from bot.services import paare  # lazy: zirkelfreier Modul-Import
        chat_id = paare.paar_im_kontext().chat_id(paare.ROLLE_SUB)
        await bot.send_sticker(chat_id=chat_id, sticker=file_id)
        return True
    except Exception:
        logger.warning("Reaktions-Sticker %s nicht sendbar", tag, exc_info=True)
        return False
