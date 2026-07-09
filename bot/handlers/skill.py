"""
Skill-Handler – Wissens-Bibliothek pro Kategorie.

Grok generiert beim ersten Aufruf einen kuratierten Wissens-Eintrag (Anatomie,
Sicherheit, Progression, Tools, häufige Fehler). Die Domina kann ihn überschreiben.

Commands (nur Domina):
  /lerne <kategorie>            – zeigt den Eintrag (generiert ihn beim ersten Mal)
  /lerne_neu <kategorie>        – erzwingt eine neue Grok-Generierung (alter wird ersetzt)
  /skill_bearbeiten <kategorie> – setzt State 'skill_edit'; die naechste Nachricht
                                    wird als neuer Text gespeichert
  /skills                       – Liste aller vorhandenen Eintraege
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper
from bot.messages import t

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _resolve_kategorie(arg: str) -> str | None:
    """Mappe Nutzereingabe auf einen Kategorie-Namen aus dem Pool (Katalog +
    eigene Kategorien). Erlaubt: Originalname, Lowercase, kat_to_cmd-Form,
    Leer-/Bindestriche."""
    if not arg:
        return None
    from bot.services import kategorie_logik
    norm = arg.strip().lower().replace(" ", "_").replace("-", "_")
    for kat in await kategorie_logik.alle_kategorien_async():
        if kat.lower() == norm:
            return kat
        if config.kat_to_cmd(kat) == norm:
            return kat
    return None


def _format_skill(eintrag: dict) -> str:
    kat = eintrag.get("kategorie", "?")
    inhalt = eintrag.get("inhalt", "")
    source = eintrag.get("source", "grok")
    aktualisiert = (eintrag.get("aktualisiert_am") or "")[:10]
    header = t("SKILL_HEADER", kategorie=kat, source=source, stand=aktualisiert)
    text = header + inhalt
    if len(text) > 4000:
        text = text[:3996] + "\n…"
    return text


async def _generiere(kategorie: str) -> str:
    system = f"""Du bist ein erfahrener BDSM-Coach. Erstelle einen praxisnahen
Wissens-Brief fuer die Domina zur angegebenen Kategorie.

Struktur (Markdown, direkt an die Domina, du-Form):

🧠 *Worum es geht*
2–3 Saetze Einordnung – was diese Kategorie ausmacht, was sie kann, fuer wen
sie sich eignet.

🔬 *Anatomie & Basics*
Max. 5 Bulletpoints. Was muss man koerperlich/technisch wissen, um das ueberhaupt
sicher und schoen zu spielen? Konkret, nicht abstrakt.

⚠️ *Sicherheit & rote Linien*
Hygiene, anatomische Risiken, gefaehrliche Stellen, was man NIE tut. Ehrlich,
deutlich, kein Verharmlosen. Wenn die Kategorie ernsthaft riskant ist (z.B.
Atemkontrolle, Fisting, hartes Impact, Piss/Enema, Bondage mit Nervenrisiko):
das ausdruecklich sagen und konkrete Schutzmassnahmen nennen.

📈 *Progression*
3–4 Stufen vom ersten Mal bis fortgeschritten. Was lernt man wann?

🛠 *Tools & Material*
Nur wenn relevant. Was braucht man? Worauf beim Kauf achten? Keine Marken-Werbung.

💡 *Haeufige Fehler*
3–5 typische Anfaengerfehler – kurz, ehrlich.

🎯 *Einstiegs-Uebung*
EINE konkrete, sichere Mini-Uebung, die heute machbar ist.

Schreibe praktisch und konkret, nicht akademisch. Keine Disclaimer-Floskeln.
Sicherheitsrelevante Punkte deutlich, aber ohne Angstmacherei. Kein Markdown
ueber Ueberschriften-Ebene hinaus (kein ##, nur *fett*). Kein [AUFGABE: ...] Tag."""
    return await grok.simple(f'Kategorie: "{kategorie}"', system=system, reasoning=True)


_KURZFASSUNG_SYSTEM = (
    "Kondensiere den folgenden BDSM-Wissens-Brief für einen Aufgaben-Generator-Prompt. "
    "Übernimm NUR: (1) Sicherheit & rote Linien – was man NIE tut, konkrete Risiken und "
    "Schutzmaßnahmen; (2) Progression – die Stufen vom Einstieg bis fortgeschritten. "
    "Maximal 500 Zeichen, knappe Stichpunkte, keine Überschriften, kein Markdown, "
    "keine Einleitung. Nichts erfinden, was nicht im Brief steht."
)


async def _kurzfassung(kategorie: str, inhalt: str) -> str:
    """Einmalige Kondensierung fürs Generator-Prompt-Feld `kurzfassung` (Speicherzeitpunkt,
    nicht pro Generierung). Best-effort: leerer String bei Fehler – skill_kontext_block
    fällt dann auf die ⚠️-Sektion des Volltexts zurück."""
    from bot.prompts import followup as fp
    try:
        kurz = await grok.simple(
            fp.nutzer_text(f"Wissens-Brief ({kategorie})", inhalt[:6000]),
            system=_KURZFASSUNG_SYSTEM, temperature=0,
        )
        return (kurz or "").strip()[:800]
    except Exception as e:
        logger.warning("Kurzfassung für Skill %s fehlgeschlagen: %s", kategorie, e)
        return ""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def lerne(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    arg = " ".join(context.args) if context.args else ""
    kat = await _resolve_kategorie(arg)
    if not kat:
        await update.message.reply_text(t("SKILL_LERNE_USAGE"), parse_mode="Markdown")
        return

    eintrag = await qdrant.get_skill(kat)
    if eintrag:
        await telegram_helper.reply_markdown_safe(update.message, _format_skill(eintrag))
        # Alt-Eintrag ohne Kurzfassung (oder fehlgeschlagene Kondensierung beim
        # Speichern): jetzt nachholen – best-effort, nach der Antwort (keine Latenz).
        if not (eintrag.get("kurzfassung") or "").strip():
            kurz = await _kurzfassung(kat, eintrag.get("inhalt", ""))
            if kurz and await qdrant.update_skill_fields(kat, {"kurzfassung": kurz}):
                logger.info("Kurzfassung für Skill %s nachgeneriert.", kat)
        return

    # Neu generieren
    await update.message.reply_text(t("SKILL_GENERIERE", kategorie=kat), parse_mode="Markdown")
    try:
        inhalt = await _generiere(kat)
        await qdrant.save_skill(kat, inhalt, source="grok",
                                kurzfassung=await _kurzfassung(kat, inhalt))
        eintrag = {"kategorie": kat, "inhalt": inhalt, "source": "grok",
                   "aktualisiert_am": ""}
        await telegram_helper.reply_markdown_safe(update.message, _format_skill(eintrag))
        await update.message.reply_text(
            t("SKILL_BEARBEITEN_HINWEIS", kategorie=kat), parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Fehler beim Skill-Generieren (%s): %s", kat, e)
        await update.message.reply_text(t("SKILL_GENERIEREN_FEHLER"))


async def lerne_neu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    arg = " ".join(context.args) if context.args else ""
    kat = await _resolve_kategorie(arg)
    if not kat:
        await update.message.reply_text(t("SKILL_LERNE_NEU_USAGE"), parse_mode="Markdown")
        return

    await update.message.reply_text(t("SKILL_GENERIERE_NEU", kategorie=kat), parse_mode="Markdown")
    try:
        inhalt = await _generiere(kat)
        await qdrant.save_skill(kat, inhalt, source="grok",
                                kurzfassung=await _kurzfassung(kat, inhalt))
        eintrag = await qdrant.get_skill(kat) or {"kategorie": kat, "inhalt": inhalt,
                                             "source": "grok", "aktualisiert_am": ""}
        await telegram_helper.reply_markdown_safe(update.message, _format_skill(eintrag))
    except Exception as e:
        logger.error("Fehler beim Skill-Re-Generieren (%s): %s", kat, e)
        await update.message.reply_text(t("SKILL_GENERIEREN_FEHLER"))


async def skill_bearbeiten(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    arg = " ".join(context.args) if context.args else ""
    kat = await _resolve_kategorie(arg)
    if not kat:
        await update.message.reply_text(t("SKILL_BEARBEITEN_USAGE"), parse_mode="Markdown")
        return

    eintrag = await qdrant.get_skill(kat)
    s = state.get(chat_id)
    s["skill_edit_kategorie"] = kat
    state.set_mode(chat_id, "skill_edit")

    aktuell = eintrag.get("inhalt", "") if eintrag else "(noch kein Eintrag)"
    aktuell_preview = aktuell if len(aktuell) <= 3500 else aktuell[:3500] + "\n…"

    await telegram_helper.reply_markdown_safe(
        update.message,
        t("SKILL_EDIT_START", kategorie=kat, aktuell=aktuell_preview),
    )


async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wird vom main.py-Router aufgerufen, wenn mode == 'skill_edit'."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return
    text = update.message.text.strip()
    s = state.get(chat_id)
    kat = s.get("skill_edit_kategorie")

    if not kat:
        state.set_mode(chat_id, "chat")
        return

    if text.lower() in ("/abbrechen", "abbrechen"):
        state.set_mode(chat_id, "chat")
        s.pop("skill_edit_kategorie", None)
        await update.message.reply_text(t("SKILL_EDIT_ABGEBROCHEN"))
        return

    if len(text) < 30:
        await update.message.reply_text(t("SKILL_EDIT_ZU_KURZ"))
        return

    try:
        await qdrant.save_skill(kat, text, source="manuell",
                                kurzfassung=await _kurzfassung(kat, text))
        state.set_mode(chat_id, "chat")
        s.pop("skill_edit_kategorie", None)
        await update.message.reply_text(
            t("SKILL_GESPEICHERT", kategorie=kat),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Fehler beim Skill-Speichern (%s): %s", kat, e)
        await update.message.reply_text(t("SKILL_SPEICHERN_FEHLER"))


async def skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/skills – Liste aller vorhandenen Wissens-Eintraege."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    alle = await qdrant.list_skills()
    if not alle:
        await update.message.reply_text(t("SKILL_KEINE"), parse_mode="Markdown")
        return

    zeilen = [t("SKILL_LISTE_TITEL")]
    for e in alle:
        kat = e.get("kategorie", "?")
        src = e.get("source", "?")
        akt = (e.get("aktualisiert_am") or "")[:10]
        marker = "✏️" if src == "manuell" else "🤖"
        zeilen.append(f"{marker} `/lerne {kat}` – {akt}")
    zeilen.append(t("SKILL_LISTE_LEGENDE"))
    await update.message.reply_text("\n".join(zeilen), parse_mode="Markdown")
