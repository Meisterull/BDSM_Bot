"""
Aufgaben-Serien Handler.
Domina kann eine Aufgabe als Serie für 3/7/14 Tage markieren.
Bot erstellt täglich automatisch Follow-ups.
"""
import re
import uuid
import logging
from telegram import Update
from telegram.ext import ContextTypes
from bot import config, state
from bot.services import qdrant, telegram_helper, grok, kategorie_logik, limits_check
from bot.prompts import followup as fp
from bot.messages import t

logger = logging.getLogger(__name__)


def _parse_variationen(raw: str, tage: int) -> list[str] | None:
    """Parst die nummerierten Tagesaufgaben. Gibt None zurück, wenn die Anzahl
    nicht passt (dann fällt der Aufrufer auf Wiederholung zurück)."""
    bereinigt = []
    for zeile in (raw or "").splitlines():
        z = zeile.strip()
        if not z:
            continue
        z = re.sub(r"^\s*\d+[.\)]\s*", "", z)   # "1." / "1)"
        z = re.sub(r"^[-•*]\s*", "", z)            # Aufzählungszeichen
        if z:
            bereinigt.append(z)
    return bereinigt if len(bereinigt) == tage else None


async def frage_serie(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    task_text: str,
    profile: dict,
    level: int,
    kategorie: str = "allgemein",
) -> None:
    """
    Fragt nach Bestätigung ob Aufgabe als Serie erteilt werden soll.
    Wird von domina.py nach der normalen Bestätigung aufgerufen.
    """
    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)
    s["serie_task_text"] = task_text
    s["serie_task_level"] = level
    s["serie_task_profile"] = profile
    s["serie_task_kategorie"] = kategorie
    state.set_mode(chat_id, "serie_wahl")

    optionen = " / ".join(str(o) for o in config.SERIE_OPTIONEN)
    await update.message.reply_text(
        t("SERIE_FRAGE", optionen=optionen), parse_mode="Markdown"
    )


async def handle_serie_wahl(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Verarbeitet die Serie-Wahl der Domina."""
    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)
    text = update.message.text.strip().lower()

    task_text = s.get("serie_task_text", "")
    level = s.get("serie_task_level", 1)
    profile = s.get("serie_task_profile", {})
    kategorie = s.get("serie_task_kategorie", "allgemein")

    def _aufraeumen() -> None:
        for key in ("serie_task_text", "serie_task_level", "serie_task_profile", "serie_task_kategorie"):
            s.pop(key, None)

    if text in ("nein", "n", "no"):
        # Einmalige Aufgabe speichern
        _aufraeumen()
        state.set_mode(chat_id, "chat")
        await _save_single_task(update, context, task_text, profile, level, kategorie)
        return

    # Tage parsen – bei ungültiger Eingabe State NICHT aufräumen, damit der
    # nächste (gültige) Versuch nicht mit leerem Aufgabentext speichert.
    try:
        tage = int(text)
        if tage not in config.SERIE_OPTIONEN:
            raise ValueError
    except ValueError:
        optionen = " / ".join(str(o) for o in config.SERIE_OPTIONEN)
        await update.message.reply_text(t("SERIE_OPTIONEN_HINWEIS", optionen=optionen))
        return

    # Validierung erfolgreich → jetzt erst aufräumen
    _aufraeumen()
    state.set_mode(chat_id, "chat")

    # Dislike-Check: Warnung wenn Kategorie auf Dislike-Liste
    sklave_profil = await qdrant.get_user_profile("sklave") or {}
    kategorie_reaktionen = sklave_profil.get("kategorie_reaktionen", {})
    dislike_kategorien = kategorie_logik.dislike_kategorien(sklave_profil)
    if kategorie in dislike_kategorien:
        await telegram_helper.reply_markdown_safe(update.message, t(
            "SERIE_DISLIKE_WARNUNG", kategorie=kategorie,
            anzahl=kategorie_reaktionen[kategorie].get("negativ", 0),
        ))

    await _save_serie_tasks(update, context, task_text, profile, level, kategorie, tage)


async def _save_single_task(
    update, context, task_text, profile, level, kategorie
) -> None:
    """Speichert eine einmalige Aufgabe."""
    await qdrant.erstelle_task(task_text, kategorie, level)

    try:
        anweisung = await grok.simple(fp.aufgabe_an_sklaven(task_text), max_tokens=250)
    except Exception as e:
        logger.error("aufgabe_an_sklaven fehlgeschlagen, sende Roh-Text: %s", e)
        anweisung = task_text
    await telegram_helper.send_sklave(context.bot, anweisung, voice_text=anweisung)
    await update.message.reply_text(t("SERIE_EINMALIG"))


async def _save_serie_tasks(
    update, context, task_text, profile, level, kategorie, tage
) -> None:
    """Speichert alle Aufgaben einer Serie – als aufbauender Bogen statt Wiederholung."""
    serie_id = str(uuid.uuid4())

    # Variationen erzeugen (Bogen). Fällt bei Fehler/Mismatch auf Wiederholung zurück.
    variationen = None
    try:
        raw = await grok.simple(fp.serie_variationen(task_text, tage, kategorie), reasoning=True)
        variationen = _parse_variationen(raw, tage)
    except Exception as e:
        logger.error("Serie-Variationen-Generierung fehlgeschlagen: %s", e)
    variiert = bool(variationen)
    if not variationen:
        variationen = [task_text] * tage

    # Limits-Check je Tag (beide Profile); bei Treffer sicherer Rückfall auf Basis-Text.
    sklave_profil = await qdrant.get_user_profile("sklave") or {}
    domina_profil = await qdrant.get_user_profile("domina") or {}
    hard_limits = sklave_profil.get("hard_limits", []) or []
    domina_grenzen = domina_profil.get("grenzen", []) or []
    for i, v in enumerate(variationen):
        if await limits_check.verletzungen(v, hard_limits, domina_grenzen):
            logger.warning("Serie Tag %d grenzverletzend – Rückfall auf Basis-Aufgabe.", i + 1)
            variationen[i] = task_text

    for tag in range(tage):
        await qdrant.erstelle_task(
            variationen[tag], kategorie, level,
            status="offen" if tag == 0 else "serie_wartend",
            followup_in_tagen=tag + 1,
            extra={"serie_id": serie_id, "serie_tag": tag + 1, "serie_gesamt": tage},
        )

    try:
        anweisung = await grok.simple(fp.aufgabe_an_sklaven(variationen[0]), max_tokens=250)
    except Exception as e:
        logger.error("aufgabe_an_sklaven fehlgeschlagen, sende Roh-Text: %s", e)
        anweisung = variationen[0]
    await telegram_helper.send_sklave(context.bot, anweisung, voice_text=anweisung)
    hinweis = t("SERIE_HINWEIS_BOGEN") if variiert else t("SERIE_HINWEIS_TAEGLICH")
    await update.message.reply_text(t("SERIE_GESPEICHERT", tage=tage, hinweis=hinweis))
    logger.info("Serie %s erstellt: %s Tage (variiert=%s), Basis: %s",
                serie_id, tage, variiert, task_text[:50])