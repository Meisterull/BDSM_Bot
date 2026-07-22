"""
/einstellungen – zentrales Einstellungsmenü (Domina-only).

Bündelt Sprache, Persona-Stil und die bestehenden Persona-Settings
(/botname, /sklavenname, /setup) an einem Ort. Muster wie profil.py:
nummerierte Wahl (Mode einstellungen_wahl) → Wert-Eingabe (einstellungen_eingabe).
"""
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import persona_config, telegram_helper
from bot.prompts import persona_presets, rollen
from bot.messages import t

logger = logging.getLogger(__name__)

# Nummer -> (feld_key, Label, Eingabe-Hinweis)
FELDER = {
    "1": ("sprache", "Sprache",
          "In welcher Sprache soll der Bot antworten?\n"
          "z.B. *Deutsch*, *Englisch*, *Französisch* – oder `-` für Standard (Deutsch).\n\n"
          "_Hinweis: gilt für alle generierten Antworten. Menüs, Buttons und "
          "Befehls-Beschreibungen bleiben vorerst deutsch._"),
    "2": ("persona_stil", "Persönlichkeits-Stil", ""),  # Hinweis wird dynamisch gebaut
    "3": ("rollen", "Rollen-Konstellation", ""),        # Hinweis wird dynamisch gebaut
    "4": ("bot_name", "Bot-Name",
          "Wie soll deine Bot-Herrin heißen?\nName eingeben – oder `-` für keinen Namen („deine Herrin“)."),
    "5": ("sklave_anrede", "Sklaven-Anrede",
          "Wie soll der Sklave angesprochen werden?\nAnrede eingeben – oder `-` für neutral."),
    "6": ("setup_kontext", "Setup-Kontext",
          "Beschreibe eure reale Konstellation (Anatomie/Rollen/Ausstattung), damit der Bot nicht rät.\n"
          "Text eingeben – oder `-` zum Entfernen."),
    "7": ("zeitplan", "Tages-Zeiten", ""),               # Hinweis wird dynamisch gebaut
    "8": ("safeword", "Safeword", ""),                   # Hinweis wird dynamisch gebaut
}

# Reihenfolge + Labels der pro Paar konfigurierbaren Zeiten (persona_config.ZEIT_FELDER)
ZEIT_REIHENFOLGE = [
    ("followup_time", "Follow-up-Nachfrage (täglich)"),
    ("tiny_task_time", "Tiny-Task-Vorschlag (täglich)"),
    ("tiny_task_feedback_time", "Tiny-Task-Feedback (täglich)"),
    ("training_erinnerung_time", "Trainings-Erinnerung (täglich)"),
    ("luecken_check_time", "Lücken-Check (täglich)"),
    ("stimmung_time", "Stimmungs-Check (täglich)"),
    ("ziel_erinnerung_time", "Ziel-Erinnerung (montags)"),
    ("rollenspiel_vorschlag_time", "Rollenspiel-Vorschlag (Fr+Sa)"),
    ("wochenplanung_time", "Wochenplanung (sonntags)"),
    ("luecken_abend_time", "Lücken-Zustellung „heute Abend“"),
    ("termin_zustellung_time", "Termin-Aufgaben-Zustellung (am Zieltag)"),
]

# Wie config.validate(): Stunden strikt 00-23, Minuten 00-59
_ZEIT_RE = re.compile(r"(?:[01]?\d|2[0-3]):[0-5]\d")


def _stil_hinweis() -> str:
    return "Welchen Stil soll die Herrin haben? Schreibe die Nummer:\n" + persona_presets.stil_hinweis()


def _rollen_hinweis() -> str:
    return ("Welche Konstellation spielt ihr? Schreibe die Nummer:\n" + rollen.kombi_hinweis()
            + "\n\n_Bestimmt Anrede, Pronomen und die Anatomie-Logik der generierten Texte._")


def _safeword_hinweis() -> str:
    return (f"Aktuell: Safeword *{persona_config.safeword()}*, "
            f"Aufheben mit *{persona_config.resume_wort()}*.\n\n"
            "Schreibe *beide Wörter* (zuerst Safeword, dann Aufheb-Wort), "
            "z.B. `red green` – oder `-` für den Standard.\n\n"
            "_Das Safeword pausiert sofort alles; das zweite Wort hebt die Pause auf. "
            "Beide gelten nur für euer Paar._")


def _zeitplan_hinweis() -> str:
    zeilen = ["Wann soll was passieren? Schreibe *Nummer und Uhrzeit*, "
              "z.B. `1 19:30` – oder `1 -` für den Standard.\n"]
    for i, (feld, label) in enumerate(ZEIT_REIHENFOLGE, 1):
        eigene = persona_config._aktueller_cache().get(feld, "")
        anzeige = persona_config.zeit(feld) + ("" if eigene else " (Standard)")
        zeilen.append(f"{i}. {label}: {anzeige}")
    return "\n".join(zeilen)


def _menu_text() -> str:
    esc = telegram_helper.escape_md
    stil_key = persona_config.persona_stil() or persona_presets.DEFAULT
    stil_label = persona_presets.PRESETS.get(stil_key, persona_presets.PRESETS[persona_presets.DEFAULT])["label"]
    setup = persona_config.setup_kontext()
    setup_kurz = (setup[:60] + "…") if len(setup) > 60 else setup
    zeilen = [
        "⚙️ *Einstellungen*",
        "",
        f"1️⃣ Sprache: {esc(persona_config.sprache() or 'Deutsch (Standard)')}",
        f"2️⃣ Persönlichkeits\\-Stil: {esc(stil_label)}",
        f"3️⃣ Rollen\\-Konstellation: {esc(rollen.aktuelle_kombi_label())}",
        f"4️⃣ Bot\\-Name: {esc(persona_config.bot_name() or '— („deine Herrin“)')}",
        f"5️⃣ Sklaven\\-Anrede: {esc(persona_config.sklave_anrede() or '— (neutral)')}",
        f"6️⃣ Setup\\-Kontext: {esc(setup_kurz or '—')}",
        f"7️⃣ Tages\\-Zeiten: Follow\\-up {esc(persona_config.zeit('followup_time'))} u\\.a\\.",
        f"8️⃣ Safeword: {esc(persona_config.safeword())} / {esc(persona_config.resume_wort())}",
        "",
        "✏️ Was möchtest du ändern\\?",
        "Schreibe die Nummer \\(1\\-8\\) oder /abbrechen",
    ]
    return "\n".join(zeilen)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return
    state.set_mode(chat_id, "einstellungen_wahl")
    await update.message.reply_text(_menu_text(), parse_mode="MarkdownV2")


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)
    text = update.message.text.strip()
    mode = s.get("mode")

    if text.lower() in ("/abbrechen", "abbrechen"):
        from bot.handlers.profil import abbrechen_command
        await abbrechen_command(update, context)
        return

    if mode == "einstellungen_wahl":
        if text not in FELDER:
            await update.message.reply_text(t("EINSTELLUNGEN_ZAHL_HINWEIS"))
            return
        feld_key, label, hinweis = FELDER[text]
        s["einstellungen_feld"] = feld_key
        state.set_mode(chat_id, "einstellungen_eingabe")
        if feld_key == "persona_stil":
            hinweis = _stil_hinweis()
        elif feld_key == "rollen":
            hinweis = _rollen_hinweis()
        elif feld_key == "zeitplan":
            hinweis = _zeitplan_hinweis()
        elif feld_key == "safeword":
            hinweis = _safeword_hinweis()
        await update.message.reply_text(
            t("EINSTELLUNGEN_FELD_PROMPT", label=label, hinweis=hinweis), parse_mode="Markdown"
        )
        return

    if mode == "einstellungen_eingabe":
        feld_key = s.get("einstellungen_feld")
        if not feld_key:
            state.set_mode(chat_id, "chat")
            return

        wert = "" if text == "-" else text

        if feld_key == "sprache":
            if wert.lower() in ("deutsch", "standard", ""):
                wert = ""
            await persona_config.set_sprache(wert[:40])
            # UI-Locale (Menüs/Festtexte) folgt der Sprache automatisch, sofern
            # ein Katalog existiert (de/en) – Menüs sofort neu setzen.
            await persona_config.set_ui_locale(persona_config.locale_fuer_sprache(wert))
            try:
                from bot.handlers import pairing
                await pairing._setze_menues(context, paare.paar_im_kontext())
            except Exception:
                logger.exception("Menüs nach Sprachwechsel nicht neu setzbar (greift beim Neustart)")
            # Sicherheits-Hinweis: limits_check nutzt eine DEUTSCHE Synonym-Liste –
            # bei anderer Sprache greift nur das wörtliche Limit-Matching.
            if wert:
                await update.message.reply_text(
                    t("EINSTELLUNGEN_SPRACHE_LIMITS_WARNUNG", sprache=wert[:40]),
                    parse_mode="Markdown",
                )
        elif feld_key == "persona_stil":
            key = persona_presets.stil_key_fuer_eingabe(text)
            if key is None:
                await update.message.reply_text(
                    t("EINSTELLUNGEN_STIL_UNBEKANNT", hinweis=_stil_hinweis())
                )
                return
            await persona_config.set_persona_stil("" if key == persona_presets.DEFAULT else key)
        elif feld_key == "rollen":
            kombi = rollen.kombi_fuer_eingabe(text)
            if kombi is None:
                await update.message.reply_text(
                    t("EINSTELLUNGEN_STIL_UNBEKANNT", hinweis=_rollen_hinweis())
                )
                return
            dom, sub = kombi
            # Default-Kombi (F/M) als leere Felder persistieren = Bestandsverhalten.
            if (dom, sub) == rollen.KOMBIS[0]:
                dom = sub = ""
            await persona_config.set_rollen(dom, sub)
        elif feld_key == "bot_name":
            await persona_config.set_bot_name(wert[:40])
        elif feld_key == "sklave_anrede":
            await persona_config.set_sklave_anrede(wert[:40])
        elif feld_key == "setup_kontext":
            await persona_config.set_setup_kontext(wert[:600])
        elif feld_key == "safeword":
            if text == "-":
                await persona_config.set_safeword("", "")
            else:
                teile = text.lower().split()
                # 2 verschiedene Einzel-Wörter, keine Commands, nicht absurd lang
                if (len(teile) != 2 or teile[0] == teile[1]
                        or any(w.startswith("/") or len(w) > 30 for w in teile)):
                    await update.message.reply_text(
                        t("EINSTELLUNGEN_STIL_UNBEKANNT", hinweis=_safeword_hinweis()),
                        parse_mode="Markdown",
                    )
                    return
                await persona_config.set_safeword(teile[0], teile[1])
        elif feld_key == "zeitplan":
            teile = text.split()
            nummern = {str(i): feld for i, (feld, _) in enumerate(ZEIT_REIHENFOLGE, 1)}
            if len(teile) != 2 or teile[0] not in nummern or not (
                    teile[1] == "-" or _ZEIT_RE.fullmatch(teile[1])):
                await update.message.reply_text(
                    t("EINSTELLUNGEN_STIL_UNBEKANNT", hinweis=_zeitplan_hinweis()),
                    parse_mode="Markdown",
                )
                return
            zeit_feld = nummern[teile[0]]
            await persona_config.set_zeit(zeit_feld, "" if teile[1] == "-" else teile[1])
            # Cron-Jobs dieses Paares sofort auf die neue Zeit umplanen
            try:
                from bot import main as main_mod
                main_mod.plane_zeit_jobs(context.bot, paare.paar_im_kontext())
            except Exception:
                logger.exception("Zeit-Jobs nicht umplanbar (greift spätestens beim Neustart)")
        else:
            s.pop("einstellungen_feld", None)  # Rest-State nicht liegen lassen (Review D6)
            state.set_mode(chat_id, "chat")
            return

        s.pop("einstellungen_feld", None)
        state.set_mode(chat_id, "einstellungen_wahl")
        await update.message.reply_text(
            t("EINSTELLUNGEN_GESPEICHERT") + "\n\n" + _menu_text(), parse_mode="MarkdownV2"
        )
