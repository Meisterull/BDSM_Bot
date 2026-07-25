"""
Abwesenheit des Sklaven (Dienstreise, Urlaub, Reise): /abwesend – für BEIDE Rollen.

Hintergrund (Live-Befund 25.07.): eine mehrwöchige Abwesenheit existierte nur
als Freitext im Verlauf – die Herrin "wusste" es mal, mal nicht (Retrieval-
Zufall), und die Aufgaben-Generatoren wussten gar nichts davon. Jetzt ist die
Abwesenheit ein Zustand mit Datum (persona_config, pro Paar):

  * /abwesend                      – Status anzeigen
  * /abwesend 20.07.-02.08. Text   – Zeitraum setzen (Rest = Grund)
  * /abwesend 2 wochen Dienstreise – Dauer ab heute
  * /abwesend ende                 – aufheben (früher zurück)

Wirkung: BEWUSST kein Pausieren – alle Jobs (Tiny-Task, Follow-ups, Stimmung,
Blitz …) laufen weiter. Der Zeitraum fließt nur als harter Fakt in die Prompts
ein: Herrin- und Coach-Chat direkt (persona_config.abwesenheit_hinweis) und
alle Aufgaben-Generatoren zentral über limits_check.generate_mit_limit_retry –
Vorschläge berücksichtigen die Abwesenheit (nichts, was Anwesenheit zu Hause
erfordert), statt auszufallen. Läuft am bis-Datum von selbst aus.
"""
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from bot.services import datum_erkennung, paare, persona_config, telegram_helper
from bot.messages import t

logger = logging.getLogger(__name__)

_ENDE_WORTE = {"ende", "aufheben", "zurueck", "zurück", "vorbei", "beenden",
               "end", "clear", "back", "over", "cancel"}

# Wörter, die zur Zeitraum-Angabe gehören und nicht in den Grund sollen.
_FUELLER = {"ab", "vom", "von", "bis", "zum", "am", "für", "fuer",
            "from", "until", "till", "to", "for"}


def _grund_aus(text: str) -> str:
    """Freitext minus Datums-/Dauer-Ausdrücke und Füllwörter = Grund."""
    rest = datum_erkennung._DATUM_RE.sub(" ", text)
    rest = datum_erkennung._DAUER_RE.sub(" ", rest)
    rest = datum_erkennung._UEBERMORGEN_RE.sub(" ", rest)
    rest = datum_erkennung._MORGEN_RE.sub(" ", rest)
    rest = datum_erkennung._WOCHENTAG_RE.sub(" ", rest)
    woerter = [w for w in re.split(r"[\s\-–,]+", rest) if w and w.lower() not in _FUELLER]
    return " ".join(woerter)[:100].strip()


def _zeitraum_text(von, bis) -> str:
    return f"{von.strftime('%d.%m.%Y')} – {bis.strftime('%d.%m.%Y')}"


async def _informiere_partner(bot, rolle: str, text: str) -> None:
    """Die jeweils andere Seite über die Änderung informieren – best-effort,
    ein Sendefehler darf die Bestätigung an den Setzenden nicht verhindern."""
    try:
        if rolle == paare.ROLLE_SUB:
            await telegram_helper.send_domina(bot, text)
        else:
            await telegram_helper.send_sklave(bot, text)
    except Exception:
        logger.exception("Abwesenheit: Partner-Info nicht zustellbar")


async def command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    ctx = paare.resolve(chat_id)
    if ctx is None:
        return
    _, rolle = ctx

    args_text = " ".join(context.args or []).strip()

    if not args_text:
        a = persona_config.abwesenheit()
        if a:
            von, bis, grund = a
            await update.message.reply_text(t(
                "ABWESEND_STATUS_AKTIV",
                zeitraum=_zeitraum_text(von, bis),
                grund=f" ({grund})" if grund else "",
            ))
        else:
            await update.message.reply_text(t("ABWESEND_STATUS_KEINE"))
        return

    if args_text.lower().rstrip(".!") in _ENDE_WORTE:
        hatte = persona_config.abwesenheit() is not None
        await persona_config.set_abwesenheit(None, None)
        await update.message.reply_text(t("ABWESEND_AUFGEHOBEN"))
        if hatte:
            await _informiere_partner(context.bot, rolle, t("ABWESEND_PARTNER_AUFGEHOBEN"))
        return

    zeitraum = datum_erkennung.finde_zeitraum(args_text)
    if not zeitraum:
        await update.message.reply_text(t("ABWESEND_UNVERSTANDEN"))
        return

    von, bis = zeitraum
    grund = _grund_aus(args_text)
    await persona_config.set_abwesenheit(von, bis, grund)
    logger.info("Abwesenheit gesetzt (%s): %s bis %s%s", rolle, von, bis,
                f" ({grund})" if grund else "")
    info = t("ABWESEND_GESETZT", zeitraum=_zeitraum_text(von, bis),
             grund=f" ({grund})" if grund else "")
    await update.message.reply_text(info)
    await _informiere_partner(
        context.bot, rolle,
        t("ABWESEND_PARTNER_GESETZT", zeitraum=_zeitraum_text(von, bis),
          grund=f" ({grund})" if grund else ""),
    )


def prompt_hinweis() -> str:
    """Alias auf persona_config.abwesenheit_hinweis – der Baustein lebt dort,
    damit limits_check (services-Ebene) ihn ohne Handler-Import einspeisen kann."""
    return persona_config.abwesenheit_hinweis()
