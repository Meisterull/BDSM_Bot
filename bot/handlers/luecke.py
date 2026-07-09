"""
Lücken-Füller: Wenn die Domina länger keine Aufgabe/Szene erteilt hat, schlägt
der Bot ihr (nur bei Opt-in via /luecken) EINEN limit-sauberen Task für den
Sklaven vor. Sie gibt jeden Vorschlag explizit frei – nichts geht ohne ihr OK an
den Sklaven:

    [✅ Jetzt senden]  [🌙 Heute Abend]
    [🔄 Anderer]       [🚫 Heute nicht]

"Heute Abend" legt den Task als status='geplant' mit `zustellung_ab` an; der
`luecken_zustellung_job` (Scheduler) stellt ihn im Abendfenster zu. Restart-sicher,
weil in Qdrant persistiert (nicht im flüchtigen Job-Queue-State).
"""
import logging
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, limits_check
from bot.prompts import followup as fp
from bot.messages import t

logger = logging.getLogger(__name__)

_STATE_KEYS = ("luecke_aufgabe", "luecke_kategorie", "luecke_level", "luecke_nonce")


async def _generiere_vorschlag(domina_profile: dict, sklave_profile: dict) -> tuple[str | None, list[str]]:
    """Erzeugt EINEN limit-sauberen Tiny-Task-Vorschlag (gleiche Pipeline wie der
    abendliche Tiny-Task-Job). Gibt (text, kategorien) zurück; text=None bei
    Grenzverletzung/Fehler."""
    from bot.scheduler import followup as sched  # lazy: zirkulären Import vermeiden
    try:
        kwargs = await sched._vorschlag_kontext(domina_profile, sklave_profile, False)
        gewaehlte = kwargs.get("gewaehlte_kategorien", []) or []
        system, prompt = fp.tiny_task_vorschlag(**kwargs)
        sk_hl = sklave_profile.get("hard_limits", []) or []
        do_gr = domina_profile.get("grenzen", []) or []
        text = await limits_check.generate_mit_limit_retry(prompt, sk_hl, do_gr, system=system)
        if text and len(text) > 4000:
            text = text[:3997] + "..."
        return (text or None), gewaehlte
    except Exception:
        logger.exception("Lücken-Vorschlag-Generierung fehlgeschlagen")
        return None, []


def _keyboard(nonce: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t("BUTTON_LUECKE_JETZT"),   callback_data=f"luecke:jetzt:{nonce}"),
         InlineKeyboardButton(t("BUTTON_LUECKE_ABEND"),   callback_data=f"luecke:abend:{nonce}")],
        [InlineKeyboardButton(t("BUTTON_LUECKE_ANDERER"), callback_data=f"luecke:anderer:{nonce}"),
         InlineKeyboardButton(t("BUTTON_LUECKE_HEUTE_NICHT"), callback_data=f"luecke:heute_nicht:{nonce}")],
    ])


def _merke_vorschlag(s: dict, text: str, kategorie: str, level: int) -> str:
    """Vorschlag + frischen Nonce in den Domina-State legen, Nonce zurückgeben.
    Nonce verhindert, dass ein liegengebliebener Button einer ÄLTEREN Runde den
    neuesten State-Inhalt erteilt (gleiches Muster wie wuerfel)."""
    nonce = uuid.uuid4().hex[:8]
    s["luecke_aufgabe"] = text
    s["luecke_kategorie"] = kategorie
    s["luecke_level"] = level
    s["luecke_nonce"] = nonce
    return nonce


async def sende_vorschlag(bot, tage: int) -> bool:
    """Generiert einen Vorschlag und schickt ihn der Domina mit Freigabe-Buttons.
    Wird vom Lücken-Check-Job aufgerufen. Returns True bei erfolgreichem Versand."""
    domina_profile = await qdrant.get_user_profile("domina") or {}
    sklave_profile = await qdrant.get_user_profile("sklave") or {}
    text, kategorien = await _generiere_vorschlag(domina_profile, sklave_profile)
    if not text:
        logger.info("Lücken-Vorschlag verworfen (kein sauberer Text) – kein Versand.")
        return False

    # Re-Check NACH der LLM-Generierung (TOCTOU): der Lücken-Check prüfte den
    # Flow nur am Jobanfang – im Generierungs-Fenster kann die Domina einen
    # Flow begonnen oder ein Safeword gesendet haben (Trace 06.07., Kleinkram).
    if state.is_paused() or state.get_mode(paare.dom_chat_id()) not in ("chat", None):
        logger.info("Lücken-Vorschlag nach Generierung verworfen – Pause/Mode geändert.")
        return False

    kategorie = kategorien[0] if kategorien else "allgemein"
    level = domina_profile.get("aktuelles_level", 3)
    s = state.get(paare.dom_chat_id())
    nonce = _merke_vorschlag(s, text, kategorie, level)

    await telegram_helper.send_domina(
        bot, t("LUECKE_VORSCHLAG", tage=tage, vorschlag=text),
        parse_mode="Markdown", reply_markup=_keyboard(nonce),
    )
    # Throttle-Marke erst NACH erfolgreichem Versand setzen (sonst verhungert die
    # nächste Runde, obwohl gar nichts rausging).
    await qdrant.patch_profile_fields(
        "domina", {"luecke_letzter_vorschlag_am": datetime.now(timezone.utc).isoformat()}
    )
    logger.info("Lücken-Vorschlag an Domina gesendet (Kategorie: %s, %d Tage Ruhe).", kategorie, tage)
    return True


def _heute_abend_iso() -> str:
    """Zustell-Zeitpunkt für 'Heute Abend' als UTC-ISO (Abend-Zeit DES PAARES,
    /einstellungen Feld 7). Liegt das Abendfenster heute schon in der
    Vergangenheit, wird sofort zugestellt (jetzt)."""
    from bot.services import persona_config
    h, m = config.hm(persona_config.zeit("luecken_abend_time"))
    tz = ZoneInfo(config.TIMEZONE)
    jetzt = datetime.now(tz)
    ziel = jetzt.replace(hour=h, minute=m, second=0, microsecond=0)
    if ziel <= jetzt:
        ziel = jetzt
    return ziel.astimezone(timezone.utc).isoformat()


async def _erteile_jetzt(bot, aufgabe: str, kategorie: str, level: int) -> None:
    """Sofort: echten Task anlegen + als Befehl an den Sklaven senden. KEIN
    set_followup_task – das Followup kommt später über den Scheduler (follow_up_datum),
    genau wie bei /wuerfel; sonst würde die nächste Sklaven-Nachricht verschluckt."""
    try:
        anweisung = await grok.simple(fp.aufgabe_an_sklaven(aufgabe), max_tokens=250)
    except Exception:
        logger.exception("aufgabe_an_sklaven fehlgeschlagen – sende Rohtext")
        anweisung = aufgabe
    # Task-Anlage direkt vor dem Send, mit Rollback bei Sendefehler – sonst
    # fragt das Followup nach einer nie zugestellten Aufgabe (Trace 06.07., Lücke 5).
    point_id = await qdrant.erstelle_task(aufgabe, kategorie, level, quelle="luecke")
    try:
        await telegram_helper.send_sklave(bot, anweisung, voice_text=anweisung)
    except Exception:
        await qdrant.loesche_task(point_id)
        raise


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Freigabe-Buttons der Domina."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    nonce = parts[2] if len(parts) > 2 else ""

    s = state.get(paare.dom_chat_id())

    # Veralteter Button (anderer Nonce) – nichts erteilen.
    if nonce != s.get("luecke_nonce"):
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(t("LUECKE_STATE_WEG"))
        return

    # "Anderer Vorschlag": neu generieren, Nachricht + Buttons aktualisieren, Dialog
    # bleibt offen (neuer Nonce).
    if action == "anderer":
        domina_profile = await qdrant.get_user_profile("domina") or {}
        sklave_profile = await qdrant.get_user_profile("sklave") or {}
        text, kategorien = await _generiere_vorschlag(domina_profile, sklave_profile)
        if not text:
            await query.message.reply_text(t("LUECKE_KEIN_VORSCHLAG"))
            return
        level = domina_profile.get("aktuelles_level", 3)
        neuer_nonce = _merke_vorschlag(s, text, kategorien[0] if kategorien else "allgemein", level)
        try:
            await query.edit_message_text(
                t("LUECKE_VORSCHLAG_NEU", vorschlag=text),
                parse_mode="Markdown", reply_markup=_keyboard(neuer_nonce),
            )
        except Exception:
            # Markdown-Parsefehler o.ä. → ohne parse_mode erneut versuchen.
            await query.edit_message_text(
                t("LUECKE_VORSCHLAG_NEU", vorschlag=text), reply_markup=_keyboard(neuer_nonce),
            )
        return

    # Alle anderen Aktionen schließen den Dialog → Buttons entfernen.
    await query.edit_message_reply_markup(reply_markup=None)
    aufgabe = s.get("luecke_aufgabe", "")
    kategorie = s.get("luecke_kategorie", "allgemein")
    level = s.get("luecke_level", 3)

    if action == "heute_nicht":
        for k in _STATE_KEYS:
            s.pop(k, None)
        await query.message.reply_text(t("LUECKE_HEUTE_NICHT"))
        return

    if not aufgabe:
        await query.message.reply_text(t("LUECKE_STATE_WEG"))
        return

    if action == "jetzt":
        await _erteile_jetzt(context.bot, aufgabe, kategorie, level)
        await query.message.reply_text(t("LUECKE_GESENDET_JETZT"))
    elif action == "abend":
        await qdrant.erstelle_task(
            aufgabe, kategorie, level, status="geplant", quelle="luecke",
            extra={"zustellung_ab": _heute_abend_iso()},
        )
        await query.message.reply_text(t("LUECKE_GEPLANT_ABEND"))

    for k in _STATE_KEYS:
        s.pop(k, None)


async def toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/luecken – Domina schaltet den Lücken-Füller an/aus (Opt-in)."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return
    profil = await qdrant.get_user_profile("domina") or {}
    neu = not profil.get("luecken_vorschlag_aktiv", False)
    await qdrant.patch_profile_fields("domina", {"luecken_vorschlag_aktiv": neu})
    await update.message.reply_text(
        t("LUECKE_TOGGLE_AN") if neu else t("LUECKE_TOGGLE_AUS"), parse_mode="Markdown",
    )
