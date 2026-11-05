"""
Punkte-Nachbereitung: Rang-/Schwellen-Meldungen, Bot-Handle, Profil-Felder.

Herausgelöst aus bot/handlers/waehrung.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.handlers.waehrung.
"""
import logging
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from bot.services import inventar, qdrant, telegram_helper, waehrung
from bot.messages import t

logger = logging.getLogger(__name__)


logger = logging.getLogger(__name__)

_BOT = None                      # Bot-Handle für Hook (neuer Wunsch) – main.start()

FELD_WETTE = "herrin_wette"      # Sub-Profil (die EINE offene/letzte Wette)

FELD_WETT_VERLAUF = "herrin_wetten_verlauf"   # Sub-Profil: die letzten Wetten mit Ausgang

WETT_VERLAUF_MAX = 5             # so viele fließen in den nächsten Vorschlag ein

FELD_SCHWELLE = "punkte_schwelle_gemeldet"

FELD_RUHE = "sparziel_ruhe"      # Dom-Profil: {wunsch_lower: iso}

FELD_FRAGE_AM = "sparziel_frage_am"

_OFFEN_MAX = 10


def start(bot) -> None:
    """Beim Bot-Start: Bot-Handle merken und den Neuer-Wunsch-Hook einhängen."""
    from bot.handlers.waehrung import sparziel  # lazy: Zyklus kern<->sparziel
    global _BOT
    _BOT = bot
    inventar.NEUER_WUNSCH_HOOK = sparziel.frage_wunschpreis


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _parse(iso: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(iso)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Zentrale Nachbereitung jeder Punkteänderung
# ---------------------------------------------------------------------------

async def nach_punkteaenderung(bot, alt: int, neu: int, *, push: bool = True) -> None:
    """Rang (auf/ab) melden, Schwellen-Push (nur bei push=True und aufwärts),
    Sparziel prüfen. Jeder Teil best-effort."""
    from bot.handlers.waehrung import sparziel  # lazy: Zyklus kern<->sparziel
    try:
        await _rang_melden(bot, alt, neu)
    except Exception:
        logger.exception("Rang-Meldung fehlgeschlagen")
    if push:
        try:
            await _schwelle_melden(bot, alt, neu)
        except Exception:
            logger.exception("Schwellen-Push fehlgeschlagen")
    try:
        await sparziel.sparziel_pruefen(bot, neu)
    except Exception:
        logger.exception("Sparziel-Prüfung fehlgeschlagen")


def rang_zeile(punkte: int) -> str:
    """„Diener (500) · nächster Rang Leibdiener ab 1000" für /stats & Co."""
    _, titel = waehrung.rang(punkte)
    naechster = waehrung.naechster_rang(punkte)
    if naechster:
        return t("RANG_ZEILE_MIT_NAECHSTEM", titel=titel, naechster=naechster[1], ab=naechster[0])
    return t("RANG_ZEILE_HOECHSTER", titel=titel)


def sparziel_zeile(punkte: int) -> str:
    """„Käfig · 210/300 P" oder leer."""
    ziel = inventar.sparziel()
    if not ziel:
        return ""
    return t("SPARZIEL_ZEILE", wunsch=ziel[0], stand=min(punkte, ziel[1]), preis=ziel[1])


async def _rang_melden(bot, alt: int, neu: int) -> None:
    wechsel = waehrung.rang_wechsel(alt, neu)
    if not wechsel:
        return
    _, titel = waehrung.rang(neu)
    key = "RANG_AUF" if wechsel == "auf" else "RANG_AB"
    await telegram_helper.send_sklave(bot, t(key, titel=titel, stand=neu))
    logger.info("Rangwechsel %s → %s (%d → %d Punkte)", wechsel, titel, alt, neu)


async def _schwelle_melden(bot, alt: int, neu: int) -> None:
    schwelle = waehrung.schwelle_ueberschritten(alt, neu)
    if not schwelle:
        return
    profil = await qdrant.get_user_profile("sklave") or {}
    if int(profil.get(FELD_SCHWELLE, 0) or 0) >= schwelle:
        return
    from bot.handlers.privileg import PRIVILEGIEN  # lazy (Zyklus)
    leistbar = sorted((p for p in PRIVILEGIEN if p["kosten"] <= neu),
                      key=lambda p: p["kosten"], reverse=True)[:2]
    zeilen = [t("SCHWELLE_KOPF", schwelle=schwelle, stand=neu, rang=waehrung.rang(neu)[1])]
    sz = sparziel_zeile(neu)
    if sz:
        zeilen.append(t("SCHWELLE_SPARZIEL", zeile=sz))
    markup = None
    if leistbar:
        zeilen.append(t("SCHWELLE_SHOP"))
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"🎁 {p['name']} ({p['kosten']}P)",
                                 callback_data=f"privileg:einloesen:{p['id']}")
        ] for p in leistbar])
    await telegram_helper.send_sklave(bot, "\n".join(zeilen), reply_markup=markup)
    await qdrant.patch_profile_fields("sklave", {FELD_SCHWELLE: schwelle})
    logger.info("Schwellen-Push %d (Stand %d, %d Privileg-Buttons)", schwelle, neu, len(leistbar))
