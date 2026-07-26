"""
Aufgaben Handler – Historie anzeigen + Aufgabe pausieren/löschen.
Nur für Domina. Mit Kategorie-Filter.
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes
from bot import config, state
from bot.services import paare
from bot.services import qdrant, synonyme, telegram_helper, kategorie_logik
from bot.messages import t

logger = logging.getLogger(__name__)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE,
               behalte_filter: bool = False) -> None:
    """/aufgaben – zeigt erledigte Aufgaben mit optionalem Kategorie-Filter.

    behalte_filter=True nur beim Aufruf über show_kategorie; das nackte
    /aufgaben setzt einen früher gesetzten Filter zurück (Test-Befund F5:
    der Filter aus /aufgaben_<kategorie> klebte sonst für immer)."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    s = state.get(chat_id)
    if not behalte_filter:
        s.pop("aufgaben_kategorie_filter", None)
    aktive_kategorie = s.get("aufgaben_kategorie_filter", None)

    # sort_by_datum (Review D8/M4): Limit 100 bleibt als Headroom für den
    # Kategorie-Filter, aber serverseitig sortiert sind es ab >100 Tasks
    # wenigstens die NEUESTEN 100 statt einer willkürlichen Teilmenge.
    tasks = await qdrant.get_tasks_by_status(["erledigt"], sort_by_datum=True)
    if not tasks:
        await update.message.reply_text(t("AUFGABEN_KEINE_ERLEDIGT"))
        return

    # Kategorie-Filter anwenden
    if aktive_kategorie:
        tasks = [t for t in tasks if t.get("kategorie", "allgemein") == aktive_kategorie]

    tasks_sorted = sorted(tasks, key=lambda t: t.get("erteilt_am", ""), reverse=True)
    top = tasks_sorted[:10]

    filter_hint = f" _(Filter: {aktive_kategorie})_" if aktive_kategorie else ""
    lines = [t("AUFGABEN_LISTE_TITEL", filter=filter_hint)]

    for i, task in enumerate(top, 1):
        aufgabe = task.get("aufgabe", "–")
        gefuehl = task.get("gefuehl") or "nicht angegeben"
        erteilt = task.get("erteilt_am", "")[:10]
        kategorie = task.get("kategorie", "allgemein")
        serie = " 🔄" if task.get("serie_id") else ""
        lines.append(t(
            "AUFGABEN_EINTRAG", nr=i, aufgabe=aufgabe, serie=serie,
            erteilt=erteilt, kategorie=kategorie, gefuehl=gefuehl,
        ))

    # Kategorie-Filter Buttons (Pool: Katalog + eigene Kategorien)
    lines.append(t("AUFGABEN_FILTER_KOPF"))
    for kat in await kategorie_logik.alle_kategorien_async():
        lines.append(f"`/aufgaben_{config.kat_to_cmd(kat)}` = {kat}")

    await telegram_helper.reply_markdown_safe(update.message, "\n".join(lines))


async def show_kategorie_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Router für /aufgaben_<kategorie> – löst die Kategorie dynamisch gegen den
    Pool (Katalog + eigene Kategorien) auf, statt für jede Kategorie einen eigenen
    Handler zu registrieren (eigene Kategorien brauchen sonst einen Bot-Neustart)."""
    if str(update.effective_chat.id) != paare.dom_chat_id():
        return
    cmd = (update.message.text or "").split()[0].lstrip("/").split("@")[0]
    ziel = cmd.removeprefix("aufgaben_")
    if ziel == "alle":
        await show_kategorie(update, context, "alle")
        return
    for kat in await kategorie_logik.alle_kategorien_async():
        if config.kat_to_cmd(kat) == ziel:
            await show_kategorie(update, context, kat)
            return
    await update.message.reply_text(t("AUFGABEN_KATEGORIE_UNBEKANNT", kategorie=ziel))


async def show_kategorie(update: Update, context: ContextTypes.DEFAULT_TYPE, kategorie: str) -> None:
    """Zeigt Aufgaben einer bestimmten Kategorie."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    s = state.get(chat_id)
    if kategorie == "alle":
        s.pop("aufgaben_kategorie_filter", None)
    else:
        s["aufgaben_kategorie_filter"] = kategorie

    await show(update, context, behalte_filter=True)


# Status, aus denen /loeschen pausieren/löschen darf (Review D8/H4): vorher
# standen hier die toten Werte `reaktion_pending`/`serie_aktiv` (werden nie
# als Task-Status geschrieben), während die echten Wartestatus fehlten –
# laufende Serien/Ketten und geparkte Tasks waren über die UI unerreichbar.
_LOESCHBARE_STATUS = ("offen", "gefragt", "gefuehl_pending",
                      "serie_wartend", "kette_wartend", "geplant", "pausiert")

# Status, in denen Folge-Glieder beim Serie/Kette-Stopp (`s`) mitverworfen
# werden. `gefuehl_pending` bewusst NICHT: da ist die Aufgabe schon erledigt,
# nur die Gefühls-Antwort steht aus – die Daten sollen nicht verschwinden.
_STOPPBARE_GLIED_STATUS = ["offen", "gefragt", "serie_wartend",
                           "kette_wartend", "geplant", "pausiert"]


async def show_loeschen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/loeschen – zeigt offene Aufgaben zum Pausieren oder Löschen."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    tasks = await qdrant.get_tasks_by_status(list(_LOESCHBARE_STATUS))
    if not tasks:
        await update.message.reply_text(t("AUFGABEN_KEINE_OFFEN"))
        return

    s = state.get(chat_id)
    state.set_mode(chat_id, "aufgabe_loeschen")
    s["loeschen_tasks"] = {
        str(i): task.get("qdrant_point_id")
        for i, task in enumerate(tasks, 1)
    }

    lines = [t("AUFGABEN_LOESCHEN_TITEL")]
    for i, task in enumerate(tasks, 1):
        aufgabe = task.get("aufgabe", "–")
        status = task.get("status", "–")
        serie = " 🔄" if (task.get("serie_id") or task.get("kette_id")) else ""
        lines.append(f"{i}. {aufgabe}{serie} _(Status: {status})_")

    lines.append(t("AUFGABEN_LOESCHEN_FUSS"))

    await telegram_helper.reply_markdown_safe(update.message, "\n".join(lines))


async def handle_loeschen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet Pausieren/Löschen Eingabe."""
    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)
    text = update.message.text.strip().lower()

    if text in ("abbrechen", "/abbrechen"):
        state.set_mode(chat_id, "chat")
        s.pop("loeschen_tasks", None)
        s.pop("loeschen_bestaetigung_id", None)  # sonst löscht nächstes /loeschen+ja den alten Task
        s.pop("loeschen_serie_stopp", None)
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return

    # Bestätigung für Löschen / Serie-Stopp (de+en Ja-Token, s. synonyme.JA)
    if s.get("loeschen_bestaetigung_id"):
        if text in synonyme.JA:
            point_id = s.pop("loeschen_bestaetigung_id")
            serie_stopp = s.pop("loeschen_serie_stopp", None)
            state.set_mode(chat_id, "chat")
            s.pop("loeschen_tasks", None)
            if serie_stopp:
                # Alle nicht-terminalen Glieder derselben Serie/Kette verwerfen.
                # Status je Glied frisch prüfen: zwischen `s`-Eingabe und `ja`
                # kann ein Glied erledigt worden sein – das bleibt erhalten.
                anzahl = 0
                for gid in serie_stopp:
                    glied = await qdrant.get_task(gid)
                    if glied and glied.get("status") in _STOPPBARE_GLIED_STATUS:
                        await qdrant.update_task(gid, {"status": "geloescht"})
                        anzahl += 1
                await update.message.reply_text(t("AUFGABEN_SERIE_GESTOPPT", anzahl=anzahl))
            else:
                await qdrant.update_task(point_id, {"status": "geloescht"})
                await update.message.reply_text(t("AUFGABEN_GELOESCHT"))
        elif text in ("nein", "n"):
            s.pop("loeschen_bestaetigung_id", None)
            s.pop("loeschen_serie_stopp", None)
            await update.message.reply_text(t("COMMON_ABGEBROCHEN_AUFGABE_BLEIBT"))
            state.set_mode(chat_id, "chat")
            s.pop("loeschen_tasks", None)
        else:
            await update.message.reply_text(t("COMMON_JA_NEIN"), parse_mode="Markdown")
        return

    parts = text.split()
    if len(parts) != 2 or parts[0] not in s.get("loeschen_tasks", {}) or parts[1] not in ("p", "x", "s"):
        await update.message.reply_text(t("AUFGABEN_UNGUELTIG"), parse_mode="Markdown")
        return

    nummer, aktion = parts
    point_id = s["loeschen_tasks"].get(nummer)
    if not point_id:
        # Stale State – ID-Tabelle existiert nicht mehr, Liste neu aufbauen
        logger.warning("Stale loeschen_tasks state – Nummer %s nicht gefunden", nummer)
        state.set_mode(chat_id, "chat")
        s.pop("loeschen_tasks", None)
        await update.message.reply_text(t("AUFGABEN_LISTE_VERALTET"))
        return

    # Prüfen ob Task noch in einem der Status ist, aus denen die Liste gebaut
    # wurde – ein zwischenzeitlich ERLEDIGTER (oder gelöschter) Task darf nicht
    # mehr überschrieben werden, sonst verschwindet der Erledigt-Eintrag samt
    # Gefühl aus Statistik und Streak.
    task_check = await qdrant.get_task(point_id)
    if task_check and task_check.get("status") not in _LOESCHBARE_STATUS:
        state.set_mode(chat_id, "chat")
        s.pop("loeschen_tasks", None)
        await update.message.reply_text(
            t("AUFGABEN_BEREITS_MARKIERT", status=task_check.get("status"))
        )
        return

    aufgabe_text = (task_check or {}).get("aufgabe", "?")

    if aktion == "p":
        # status_vor_pause="pausiert" (Review D8/M7): safeword._resume stellt
        # alle pausierten Tasks auf status_vor_pause zurück – für die MANUELL
        # geparkte Aufgabe heißt das idempotent "bleibt pausiert", statt dass
        # jeder Safeword-Zyklus sie wieder auf "offen" zieht.
        await qdrant.update_task(point_id, {"status": "pausiert",
                                            "status_vor_pause": "pausiert"})
        state.set_mode(chat_id, "chat")
        s.pop("loeschen_tasks", None)
        await update.message.reply_text(t("AUFGABEN_PAUSIERT"))
    elif aktion == "s":
        # Ganze Serie/Kette stoppen (Review D8/H4): alle nicht-terminalen
        # Glieder einsammeln und nach Bestätigung verwerfen.
        gruppen_feld = None
        for feld in ("kette_id", "serie_id"):
            if (task_check or {}).get(feld):
                gruppen_feld = feld
                break
        if not gruppen_feld:
            await update.message.reply_text(t("AUFGABEN_KEINE_SERIE"))
            return
        glieder = await qdrant.get_gruppen_glieder(
            gruppen_feld, task_check[gruppen_feld], _STOPPBARE_GLIED_STATUS
        )
        s["loeschen_bestaetigung_id"] = point_id
        s["loeschen_serie_stopp"] = [g.get("qdrant_point_id") for g in glieder]
        await update.message.reply_text(
            t("AUFGABEN_SERIE_STOPP_BESTAETIGUNG",
              aufgabe=aufgabe_text, anzahl=len(glieder)),
            parse_mode="Markdown"
        )
    else:
        s["loeschen_bestaetigung_id"] = point_id
        await update.message.reply_text(
            t("AUFGABEN_LOESCHEN_BESTAETIGUNG", aufgabe=aufgabe_text),
            parse_mode="Markdown"
        )