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
from bot.services import persona_config, telegram_helper, modi
from bot.prompts import persona_presets, rollen
from bot.handlers import stille_checkin
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
    "4": ("bot_name", "Bot-Name", ""),                  # Hinweis wird dynamisch gebaut (Rollenwort je Modus)
    "5": ("sklave_anrede", "Anrede", ""),               # Hinweis wird dynamisch gebaut
    "6": ("setup_kontext", "Setup-Kontext",
          "Beschreibe eure reale Konstellation (Anatomie/Rollen/Ausstattung), damit der Bot nicht rät.\n"
          "Text eingeben – oder `-` zum Entfernen."),
    "7": ("zeitplan", "Tages-Zeiten", ""),               # Hinweis wird dynamisch gebaut
    "8": ("safeword", "Safeword", ""),                   # Hinweis wird dynamisch gebaut
    "9": ("coach_ruhe", "Coach-Ruhe / Zuschauer-Modus", ""),  # Hinweis wird dynamisch gebaut
    "10": ("modus", "Paar-Modus", ""),                   # Hinweis wird dynamisch gebaut
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
    if modi.ist_vanilla():
        return "Wer soll der Bot für euch sein? Schreibe die Nummer:\n" + persona_presets.stil_hinweis()
    return "Welchen Stil soll die Bot-Stimme haben? Schreibe die Nummer:\n" + persona_presets.stil_hinweis()


def _bot_name_hinweis() -> str:
    if modi.ist_vanilla():
        return "Wie soll die Stimme des Bots heißen?\nName eingeben – oder `-` für keinen Namen."
    d = rollen.dom()
    return (f"Wie soll die Bot-{d['label']} heißen?\nName eingeben – oder `-` für keinen Namen "
            f"(„{d['anrede']}“).")


def _anrede_hinweis() -> str:
    if modi.ist_vanilla():
        return ("Wie soll der Bot euch ansprechen? Schreibe *beide Anreden durch Komma getrennt* – "
                "zuerst die Person, die den Bot eingerichtet hat, dann die eingeladene Person, "
                "z.B. `Anna, Jonas`. `-` = keine Anreden.")
    s = rollen.sub()
    return f"Wie soll {s['label_nom']} angesprochen werden?\nAnrede eingeben – oder `-` für neutral."


def _anreden_anzeige() -> str:
    """Menü-Zeile 5 im Vanilla-Modus: beide Anreden."""
    return (f"{persona_config.dom_anrede() or '—'} / {persona_config.sklave_anrede() or '—'}")


def _modus_hinweis() -> str:
    return ("Wofür nutzt ihr den Bot? Schreibe die Nummer:\n" + modi.hinweis()
            + "\n\n_Bestimmt Wortwahl, Aufgaben-Katalog und Stil-Auswahl. Profil, Punkte und "
            "Verlauf bleiben erhalten; der Stil springt auf den Standard des neuen Modus._")


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
    stil_label = persona_presets.aktuelles_label()
    setup = persona_config.setup_kontext()
    setup_kurz = (setup[:60] + "…") if len(setup) > 60 else setup
    zeilen = [
        "⚙️ *Einstellungen*",
        "",
        f"1️⃣ Sprache: {esc(persona_config.sprache() or 'Deutsch (Standard)')}",
        (f"2️⃣ Stimme: {esc(stil_label)}" if modi.ist_vanilla()
         else f"2️⃣ Persönlichkeits\\-Stil: {esc(stil_label)}"),
        f"3️⃣ Rollen\\-Konstellation: {esc(rollen.aktuelle_kombi_label())}",
        (f"4️⃣ Bot\\-Name: {esc(persona_config.bot_name() or '—')}" if modi.ist_vanilla()
         else f"4️⃣ Bot\\-Name: {esc(persona_config.bot_name() or '— („' + rollen.dom()['anrede'] + '“)')}"),
        (f"5️⃣ Anreden: {esc(_anreden_anzeige())}" if modi.ist_vanilla()
         else f"5️⃣ Anrede \\({esc(rollen.sub()['label'])}\\): {esc(persona_config.sklave_anrede() or '— (neutral)')}"),
        f"6️⃣ Setup\\-Kontext: {esc(setup_kurz or '—')}",
        f"7️⃣ Tages\\-Zeiten: Follow\\-up {esc(persona_config.zeit('followup_time'))} u\\.a\\.",
        f"8️⃣ Safeword: {esc(persona_config.safeword())} / {esc(persona_config.resume_wort())}",
        f"9️⃣ Coach\\-Ruhe: {esc(stille_checkin.ruhe_status_text() or 'aus')}",
        f"🔟 Paar\\-Modus: {esc(modi.label().split(' – ')[0])}",
        "",
        "✏️ Was möchtest du ändern\\?",
        "Schreibe die Nummer \\(1\\-10\\) oder /abbrechen",
    ]
    return "\n".join(zeilen)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    # Vanilla: beide Seiten dürfen einstellen (gleichberechtigt); sonst Dom-only.
    if chat_id != paare.dom_chat_id() and not (modi.gleichberechtigt() and chat_id == paare.sub_chat_id()):
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
        elif feld_key == "coach_ruhe":
            hinweis = stille_checkin.einstellungs_hinweis()
        elif feld_key == "modus":
            hinweis = _modus_hinweis()
        elif feld_key == "bot_name":
            hinweis = _bot_name_hinweis()
        elif feld_key == "sklave_anrede":
            hinweis = _anrede_hinweis()
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
            await persona_config.set_persona_stil("" if key == persona_presets.default_key() else key)
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
            if modi.ist_vanilla():
                teile = [x.strip() for x in wert.split(",")] if wert else ["", ""]
                if len(teile) != 2:
                    await update.message.reply_text(
                        t("EINSTELLUNGEN_STIL_UNBEKANNT", hinweis=_anrede_hinweis()), parse_mode="Markdown")
                    return
                await persona_config.set_dom_anrede(teile[0][:40])
                await persona_config.set_sklave_anrede(teile[1][:40])
            else:
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
        elif feld_key == "modus":
            m = modi.fuer_eingabe(text)
            if m is None:
                await update.message.reply_text(
                    t("EINSTELLUNGEN_STIL_UNBEKANNT", hinweis=_modus_hinweis()),
                    parse_mode="Markdown",
                )
                return
            await persona_config.set_modus("" if m == modi.BDSM else m)
            # Stil-Key eines anderen Modus wäre nur ein stiller Fallback – explizit
            # auf den Modus-Standard zurücksetzen, damit das Menü die Wahrheit zeigt.
            if persona_config.persona_stil() not in persona_presets.presets_fuer_modus():
                await persona_config.set_persona_stil("")
            try:
                from bot.handlers import pairing
                await pairing._setze_menues(context, paare.paar_im_kontext())
            except Exception:
                logger.exception("Menüs nach Moduswechsel nicht neu setzbar (greift beim Neustart)")
            # Vanilla hat einen eigenen Job-Satz (Stimmung an beide + Impuls) –
            # Crons sofort neu planen, der _job_guard gatet den Rest.
            try:
                from bot import main as main_mod
                main_mod.plane_zeit_jobs(context.bot, paare.paar_im_kontext())
            except Exception:
                logger.exception("Zeit-Jobs nach Moduswechsel nicht umplanbar (greift beim Neustart)")
        elif feld_key == "coach_ruhe":
            if not stille_checkin.einstellung_anwenden(text):
                await update.message.reply_text(
                    t("EINSTELLUNGEN_STIL_UNBEKANNT", hinweis=stille_checkin.einstellungs_hinweis()),
                    parse_mode="Markdown",
                )
                return
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
