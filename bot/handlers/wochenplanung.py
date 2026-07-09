"""
Wochenplanung Handler – /wochenplanung Befehl und automatischer Sonntags-Job.
"""
import difflib
import logging
import re
import uuid

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, kategorie_logik, telegram_helper, limits_check
from bot.prompts import followup as fp
from bot.messages import t

logger = logging.getLogger(__name__)

_WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

# Der Generator-Prompt verlangt deutsche Wochentags-Labels (Daten-IDs!) – falls
# das LLM bei nicht-deutscher Antwortsprache trotzdem englische liefert, fängt
# der Parser sie ab und normalisiert auf die deutschen Labels (i18n-Fallback,
# sonst bricht der Plan-Import still).
_WOCHENTAGE_EN = {
    "Monday": "Montag", "Tuesday": "Dienstag", "Wednesday": "Mittwoch",
    "Thursday": "Donnerstag", "Friday": "Freitag", "Saturday": "Samstag",
    "Sunday": "Sonntag",
}


def _parse_wochenplan(plan: str) -> list[dict]:
    """Parst den generierten Wochenplan-Text in [{tag, kategorie, aufgabe}, ...]."""
    entries: list[dict] = []
    cur: dict | None = None
    tag_re = re.compile(
        r"^(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag"
        r"|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b\s*[–\-:]*\s*(.*)$"
    )
    aufgabe_re = re.compile(r"^\**\s*(?:Aufgabe|Task)\**\s*:\s*(.*)$", re.IGNORECASE)
    for raw in (plan or "").splitlines():
        line = raw.strip().lstrip("*").strip()
        m = tag_re.match(line)
        if m:
            if cur and cur["aufgabe"]:
                entries.append(cur)
            kat = m.group(2).strip().strip("[]*_ ").strip()
            tag = _WOCHENTAGE_EN.get(m.group(1), m.group(1))  # en → de (Daten-ID)
            cur = {"tag": tag, "kategorie": kat, "aufgabe": ""}
            continue
        if cur is not None:
            am = aufgabe_re.match(line)
            if am:
                cur["aufgabe"] = am.group(1).strip()
    if cur and cur["aufgabe"]:
        entries.append(cur)
    return entries


def _normalisiere_kategorie(entry: dict, pool: list[str] | None = None) -> str:
    """Gültige Pool-Kategorie für einen Plan-Eintrag.

    Reihenfolge (streng): Keyword aus dem Aufgabentext -> exaktes Plan-Label ->
    case-/unterstrich-insensitiver Treffer -> nächstliegende gültige Kategorie ->
    erst dann "allgemein". So landen erfundene Labels (z. B. "Anal_Cleanup",
    "Impact_Spezial") auf der echten Pool-Kategorie statt im "allgemein"-Topf.
    """
    pool = pool or config.AUFGABEN_KATEGORIEN
    kw = kategorie_logik.keyword_match(entry.get("aufgabe", ""))
    if kw != "allgemein":
        return kw
    label = (entry.get("kategorie") or "").strip()
    if label in pool:
        return label
    norm = label.lower().replace(" ", "_").replace("-", "_")
    for cat in pool:
        if cat.lower() == norm:
            return cat
    nahe = difflib.get_close_matches(norm, [c.lower() for c in pool], n=1, cutoff=0.7)
    if nahe:
        return next(c for c in pool if c.lower() == nahe[0])
    return "allgemein"


def _plan_buttons(nonce: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t("BUTTON_WOCHENPLAN_ALLE"), callback_data=f"wochenplan:alle:{nonce}"),
        InlineKeyboardButton(t("BUTTON_WOCHENPLAN_VERWERFEN"), callback_data=f"wochenplan:verwerfen:{nonce}"),
    ]])


async def sende_plan(bot, chat_id: str, plan: str, kopf: str) -> None:
    """Sendet den Plan; hängt 'Alle erstellen'-Buttons an, wenn er parsebar ist."""
    entries = _parse_wochenplan(plan)
    if entries:
        s = state.get(str(chat_id))
        s["wochenplan_entries"] = entries
        # Nonce in die Callback-Daten: ein liegengebliebener Button eines
        # FRÜHEREN Plans darf nicht die neuesten State-Einträge erstellen.
        nonce = uuid.uuid4().hex[:8]
        s["wochenplan_nonce"] = nonce
        await bot.send_message(chat_id=chat_id, text=f"{kopf}\n\n{plan}", reply_markup=_plan_buttons(nonce))
    else:
        await bot.send_message(chat_id=chat_id, text=f"{kopf}\n\n{plan}")


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Startet die Wochenplanung – fragt nach Thema/Fokus."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return
    state.set_mode(chat_id, "wochenplanung_thema")

    await update.message.reply_text(t("WOCHENPLAN_THEMA_FRAGE"), parse_mode="Markdown")


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet die Themen-Eingabe und generiert den Wochenplan."""
    chat_id = str(update.effective_chat.id)
    thema = update.message.text.strip()

    state.set_mode(chat_id, "chat")

    # Getipptes "abbrechen" nicht als Wochen-Thema interpretieren.
    if thema.lower() in ("abbrechen", "/abbrechen"):
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return

    await update.message.reply_text(t("WOCHENPLAN_WARTE"))

    domina_profile = await qdrant.get_user_profile("domina") or {}
    sklave_profile = await qdrant.get_user_profile("sklave") or {}
    letzte_kategorien = await qdrant.get_recent_task_kategorien("sklave", limit=7)
    bewertungs_kontext = await qdrant.get_bewertungs_kontext("sklave")

    try:
        plan = await _generiere_wochenplan(
            domina_profile=domina_profile,
            sklave_profile=sklave_profile,
            thema=thema,
            letzte_kategorien=letzte_kategorien,
            bewertungs_kontext=bewertungs_kontext,
        )
        await sende_plan(context.bot, chat_id, plan, t("WOCHENPLAN_TITEL"))
    except Exception as e:
        logger.error("Fehler beim Wochenplan: %s", e)
        await update.message.reply_text(t("WOCHENPLAN_FEHLER"))


async def _generiere_wochenplan(
    domina_profile: dict,
    sklave_profile: dict,
    thema: str,
    letzte_kategorien: list,
    bewertungs_kontext: str,
) -> str:
    interessen = domina_profile.get("interessen", [])
    hard_limits = sklave_profile.get("hard_limits", []) or []
    domina_grenzen = domina_profile.get("grenzen", []) or []

    from bot.prompts import coach_persona

    system = f"""Du erstellst der Domina einen Wochenplan für ihren Sklaven – Mo bis So, eine Aufgabe pro Tag.

{coach_persona.fuer_aufgaben_vorschlag()}

Format (REINER TEXT, kein Markdown, keine Sterne, keine Backticks):
🗓 Wochenplan

Montag – [Kategorie]
Aufgabe: <konkrete Aufgabe in einem Satz, die zu IHM passt – kein generischer 101-Task>
Warum: <kurze Begründung – warum gerade für ihn, nicht "weil Abwechslung wichtig ist">

Dienstag – [Kategorie]
…

(für alle 7 Tage Mo bis So)

Am Ende: zwei bis drei Sätze locker geschrieben – wie eine Freundin den Plan zusammenfasst. Was ist der rote Faden, worauf solltest du achten. Danach NICHTS mehr anhängen – keine Kategorie-Namen, keine Tags, keine losen Wörter.
Kein [AUFGABE: ...] Tag – das sind Vorschläge, keine automatischen Aufgaben.
Maximale Länge: {config.WOCHENPLAN_WORTLIMIT} Wörter.

[Kategorie] AUSSCHLIESSLICH exakt aus dieser Liste wählen (genau so geschrieben, mit Unterstrich; nicht übersetzen, nicht erfinden):
{', '.join(config.AUFGABEN_KATEGORIEN)}

Logische Schlüssigkeit ist PFLICHT – jede Aufgabe muss in sich stimmig sein UND wirklich zur gewählten Kategorie passen:
- Man kann nur reinigen/aufnehmen, was vorher real entstanden ist. "Creampie_Cleanup" verlangt eine ECHTE Ejakulation IN die zu reinigende Öffnung – ein Strapon/Dildo erzeugt KEINEN Creampie. Also niemals "Sperma aus dem Arsch lecken" nach Strapon-Pegging o. Ä.
- Keine widersprüchlichen oder physisch unmöglichen Kombinationen.

WICHTIG: Die Struktur-Wörter sind festes Format-Protokoll und bleiben IMMER exakt so –
auch wenn du in einer anderen Sprache antwortest: die Wochentags-Labels (Montag bis Sonntag)
sowie die Zeilen-Präfixe "Aufgabe:" und "Warum:". Nur die Inhalte dahinter folgen der Sprache."""
    prompt = f"""Thema/Fokus der Woche: {thema if thema else 'abwechslungsreich'}
{coach_persona.level_zeile(domina_profile.get('aktuelles_level', 1))}
Interessen der Domina: {', '.join(interessen) if interessen else 'nicht angegeben'}
Letzte Aufgaben-Kategorien (zur Abwechslung): {', '.join(letzte_kategorien) if letzte_kategorien else 'keine'}
Was der Domina gut gefiel: {bewertungs_kontext if bewertungs_kontext else 'keine Daten'}
{coach_persona.sklaven_kontext_block(sklave_profile, domina_grenzen)}"""
    # Kategorien wählt das LLM erst beim Generieren → alle vorhandenen Wissens-Briefe beilegen.
    skill_block = await coach_persona.skill_kontext_block()
    if skill_block:
        prompt += "\n\n" + skill_block

    # Limits-Check: wenn der Plan Grenzen verletzt, einmal verschärft neu generieren.
    plan = await limits_check.generate_mit_limit_retry(
        prompt, hard_limits, domina_grenzen, system=system, reasoning=True,
    )
    if plan is None:
        raise ValueError("Wochenplan auch nach Re-Generierung Grenzen-verletzend – verworfen.")
    return plan


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wochenplan-Buttons: alle Tage als Aufgaben-Serie erstellen oder nur als Vorschlag belassen."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    if len(parts) < 2:
        return
    action = parts[1]
    nonce = parts[2] if len(parts) > 2 else ""
    await query.edit_message_reply_markup(reply_markup=None)

    s = state.get(paare.dom_chat_id())

    # Veralteter Button (anderer/kein Nonce): NICHT die neuesten State-Einträge
    # erstellen – der Button gehört zu einem früheren Plan.
    if nonce != s.get("wochenplan_nonce"):
        await query.message.reply_text(t("WOCHENPLAN_NICHT_IM_SPEICHER"))
        return

    entries = s.pop("wochenplan_entries", None)
    s.pop("wochenplan_nonce", None)

    if action == "verwerfen":
        await query.message.reply_text(t("WOCHENPLAN_NUR_VORSCHLAG"))
        return
    if not entries:
        await query.message.reply_text(t("WOCHENPLAN_NICHT_IM_SPEICHER"))
        return

    sklave_profil = await qdrant.get_user_profile("sklave") or {}
    domina_profil = await qdrant.get_user_profile("domina") or {}
    hard_limits = sklave_profil.get("hard_limits", []) or []
    domina_grenzen = domina_profil.get("grenzen", []) or []
    level = domina_profil.get("aktuelles_level", 1)

    serie_id = str(uuid.uuid4())
    gesamt = len(entries)
    pool = kategorie_logik.alle_kategorien(sklave_profil)

    erstellt = 0
    uebersprungen = 0
    erste_aufgabe = None
    for i, e in enumerate(entries):
        aufgabe = e.get("aufgabe", "")
        if not aufgabe or await limits_check.verletzungen(aufgabe, hard_limits, domina_grenzen):
            uebersprungen += 1
            continue
        await qdrant.erstelle_task(
            aufgabe, _normalisiere_kategorie(e, pool), level,
            status="offen" if erstellt == 0 else "serie_wartend",
            quelle="wochenplan",
            followup_in_tagen=i + 1,
            extra={"serie_id": serie_id, "serie_tag": i + 1, "serie_gesamt": gesamt},
        )
        if erste_aufgabe is None:
            erste_aufgabe = aufgabe
        erstellt += 1

    if erste_aufgabe:
        try:
            anweisung = await grok.simple(fp.aufgabe_an_sklaven(erste_aufgabe), max_tokens=250)
        except Exception as ex:
            logger.error("Wochenplan: aufgabe_an_sklaven fehlgeschlagen: %s", ex)
            anweisung = erste_aufgabe
        await telegram_helper.send_sklave(context.bot, anweisung, voice_text=anweisung)

    msg = t("WOCHENPLAN_ERSTELLT", anzahl=erstellt)
    if uebersprungen:
        msg += t("WOCHENPLAN_UEBERSPRUNGEN", anzahl=uebersprungen)
    await query.message.reply_text(msg)
    logger.info("Wochenplan als Serie erstellt: %d Aufgaben (serie_id=%s), %d übersprungen.",
                erstellt, serie_id, uebersprungen)
