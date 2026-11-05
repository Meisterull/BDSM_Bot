"""
Onboarding Handler für Domina und Sklave.
Geführtes, erklärendes Schritt-für-Schritt Onboarding.
"""
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, telegram_helper, synonyme, zeiten, persona_config, modi
from bot.prompts import persona_presets, rollen
from bot.messages import t, t_modus

logger = logging.getLogger(__name__)

_CANCEL_KEYWORDS = ("/abbrechen", "abbrechen", "stop", "abbruch")


async def _handle_cancel(update, chat_id: str, s: dict) -> bool:
    """Prüft ob User abbrechen will. Gibt True zurück wenn abgebrochen."""
    text = (update.message.text or "").strip().lower()
    if text in _CANCEL_KEYWORDS:
        state.set_mode(chat_id, "chat")
        s.pop("onboarding_schritt", None)
        daten = s.pop("onboarding_daten", None) or {}
        if "_modus_vorher" in daten:  # Modus-Schritt hatte den Cache schon umgestellt
            persona_config._aktueller_cache()["modus"] = daten["_modus_vorher"]
        # Fallback: auch Rolle zurücksetzen falls im Sklave-Onboarding
        s.pop("onboarding_rolle", None)
        await update.message.reply_text(t("ONBOARDING_ABGEBROCHEN"))
        return True
    return False


_DOMINA_ERFAHRUNG_MAP = {
    "1": "Anfänger",
    "2": "etwas Erfahrung",
    "3": "erfahren",
}

# Vanilla (Etappe 2): Schritt 5 fragt statt des Erfahrungsstands, wie lebendig
# das Liebesleben gerade ist – gespeichert im selben Feld `erfahrungsstand`.
_VANILLA_ERFAHRUNG_MAP = {
    "1": "eher eingeschlafen",
    "2": "geht so",
    "3": "lebendig",
}

_DOMINA_TEMPO_MAP = {
    "1": "langsam",
    "2": "normal",
    "3": "schnell",
}


# ---------------------------------------------------------------------------
# Öffentliche API
# ---------------------------------------------------------------------------

async def start_if_needed(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
) -> bool:
    """
    Prüft ob Onboarding nötig ist.
    Returns True wenn Onboarding aktiv ist (handle soll aufgerufen werden).
    Returns False wenn Onboarding gerade gestartet wurde oder bereits abgeschlossen ist.
    """
    profile = await qdrant.get_user_profile(user_id)
    if profile and profile.get("onboarding_abgeschlossen"):
        return False

    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)

    # Die Person im Sub-Slot wartet, bis die Einrichter-Seite (Dom-Slot) den
    # Paar-Modus gewählt hat – sonst liefe ihr Onboarding in der falschen
    # Wortwahl (Vanilla kennt keine „Herrin“). Bestandspaare mit fertigen
    # Profilen betrifft das nicht.
    if user_id == "sklave" and s.get("mode") != "onboarding":
        dom = await qdrant.get_user_profile("domina")
        if not (dom and dom.get("onboarding_abgeschlossen")):
            await update.message.reply_text(t("ONBOARDING_WARTE_AUF_EINRICHTER"))
            return False

    if s.get("mode") != "onboarding":
        # Onboarding starten
        state.set_mode(chat_id, "onboarding")
        s["onboarding_schritt"] = 0
        s["onboarding_rolle"] = user_id
        s["onboarding_daten"] = {}

        if user_id == "domina":
            await update.message.reply_text(t("ONBOARDING_DOMINA_BEGRUESSUNG"), parse_mode="Markdown")
        else:
            # Sklave: Begrüßung + erste Frage zusammen
            s["onboarding_schritt"] = 1
            await update.message.reply_text(t_modus("ONBOARDING_SKLAVE_BEGRUESSUNG"), parse_mode="Markdown")

        return False  # Noch keine Eingabe zu verarbeiten

    return True  # Onboarding läuft, handle aufrufen


async def handle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
) -> None:
    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)

    # Abbruch-Wörter ("abbrechen"/"stop"/...) jederzeit im Onboarding erlauben.
    if await _handle_cancel(update, chat_id, s):
        return

    schritt = s.get("onboarding_schritt", 0)
    text = update.message.text.strip()

    if user_id == "domina":
        await _handle_domina(update, context, s, chat_id, schritt, text)
    else:
        await _handle_sklave(update, context, s, chat_id, schritt, text)


# ---------------------------------------------------------------------------
# Domina Flow
# ---------------------------------------------------------------------------

async def _handle_domina(
    update: Update,
    context,
    s: dict,
    chat_id: str,
    schritt: int,
    text: str,
) -> None:

    # Schritt 0: Warten auf "ja"
    if schritt == 0:
        if text.lower() in synonyme.JA + ("ok", "los"):
            s["onboarding_schritt"] = 1
            await update.message.reply_text(
                t("ONBOARDING_DOMINA_SCHRITT_SPRACHE"), parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                t("ONBOARDING_BEREIT_HINWEIS"), parse_mode="Markdown"
            )
        return

    # Schritt 1: Sprache (Setup-Wizard, Veröffentlichungs-Schritt 3)
    if schritt == 1:
        if text == "1" or text.lower() in ("deutsch", "standard"):
            sprache = ""
        elif text == "2":
            sprache = "Englisch"
        elif len(text) >= 2 and not text.isdigit():
            sprache = text[:40]
        else:
            await update.message.reply_text(t("ONBOARDING_SPRACHE_WAHL"))
            return
        s["onboarding_daten"]["sprache"] = sprache
        s["onboarding_schritt"] = 2
        await update.message.reply_text(
            t("ONBOARDING_DOMINA_SCHRITT_MODUS", liste=modi.hinweis()),
            parse_mode="Markdown",
        )
        return

    # Schritt 2: Paar-Modus (BDSM / Kink …) – bestimmt Wortwahl, Katalog und
    # Stil-Liste der folgenden Schritte, deshalb sofort in den Cache (persistiert
    # wird im Abschluss; Abbruch stellt den alten Wert zurück).
    if schritt == 2:
        m = modi.fuer_eingabe(text)
        if m is None:
            await update.message.reply_text(
                t("ONBOARDING_MODUS_WAHL", liste=modi.hinweis()), parse_mode="Markdown"
            )
            return
        wert = "" if m == modi.BDSM else m
        s["onboarding_daten"].setdefault("_modus_vorher", persona_config.modus())
        s["onboarding_daten"]["modus"] = wert
        persona_config._aktueller_cache()["modus"] = wert
        s["onboarding_schritt"] = 3
        await update.message.reply_text(
            t_modus("ONBOARDING_DOMINA_SCHRITT_ROLLEN", liste=rollen.kombi_hinweis()),
            parse_mode="Markdown",
        )
        return

    # Schritt 3: Rollen-Konstellation
    if schritt == 3:
        kombi = rollen.kombi_fuer_eingabe(text)
        if kombi is None:
            await update.message.reply_text(
                t("ONBOARDING_ROLLEN_WAHL", liste=rollen.kombi_hinweis()), parse_mode="Markdown"
            )
            return
        dom, sub = kombi
        # Default-Kombi (F/M) als leere Felder persistieren = Bestandsverhalten.
        if (dom, sub) == rollen.KOMBIS[0]:
            dom = sub = ""
        s["onboarding_daten"]["dom_geschlecht"] = dom
        s["onboarding_daten"]["sub_geschlecht"] = sub
        s["onboarding_schritt"] = 4
        await update.message.reply_text(
            t_modus("ONBOARDING_DOMINA_SCHRITT_STIL", liste=persona_presets.stil_hinweis()),
            parse_mode="Markdown",
        )
        return

    # Schritt 4: Persona-Stil
    if schritt == 4:
        key = persona_presets.stil_key_fuer_eingabe(text)
        if key is None:
            await update.message.reply_text(
                t("ONBOARDING_STIL_WAHL", liste=persona_presets.stil_hinweis()), parse_mode="Markdown"
            )
            return
        s["onboarding_daten"]["persona_stil"] = "" if key == persona_presets.default_key() else key
        s["onboarding_schritt"] = 5
        await update.message.reply_text(t_modus("ONBOARDING_DOMINA_SCHRITT_ERFAHRUNG"), parse_mode="Markdown")
        return

    # Schritt 5: Erfahrungsstand (Vanilla: wie lebendig ist das Liebesleben)
    if schritt == 5:
        mapped = (_VANILLA_ERFAHRUNG_MAP if modi.ist_vanilla() else _DOMINA_ERFAHRUNG_MAP).get(text)
        if not mapped:
            # Nur sinnvolle Freitext-Eingaben erlauben
            if len(text.strip()) < 2:
                await update.message.reply_text(t("ONBOARDING_ERFAHRUNG_WAHL"))
                return
            # Plausibilitäts-Check: keine Zahlen > 99, keine Sonderzeichen-only
            cleaned = ''.join(c for c in text.strip() if not c.isdigit() and c not in '.-,_')
            if len(cleaned) < 2:
                await update.message.reply_text(t("ONBOARDING_ERFAHRUNG_NUR_ZAHLEN"))
                return
            mapped = text.strip()
        s["onboarding_daten"]["erfahrungsstand"] = mapped
        s["onboarding_schritt"] = 6
        await update.message.reply_text(t_modus("ONBOARDING_DOMINA_SCHRITT_INTERESSEN"), parse_mode="Markdown")
        return

    # Schritt 6: Interessen
    if schritt == 6:
        interessen = [x.strip() for x in text.split(",") if x.strip()]
        s["onboarding_daten"]["interessen"] = interessen
        s["onboarding_schritt"] = 7
        await update.message.reply_text(t_modus("ONBOARDING_DOMINA_SCHRITT_GRENZEN"), parse_mode="Markdown")
        return

    # Schritt 7: Grenzen
    if schritt == 7:
        if text.lower() == "keine":
            grenzen = []
        else:
            grenzen = [x.strip() for x in text.split(",") if x.strip()]
        s["onboarding_daten"]["grenzen"] = grenzen
        s["onboarding_schritt"] = 8
        await update.message.reply_text(t_modus("ONBOARDING_DOMINA_SCHRITT_ZIELE"), parse_mode="Markdown")
        return

    # Schritt 8: Ziele
    if schritt == 8:
        s["onboarding_daten"]["ziele"] = text
        s["onboarding_schritt"] = 9
        await update.message.reply_text(t_modus("ONBOARDING_DOMINA_SCHRITT_TEMPO"), parse_mode="Markdown")
        return

    # Schritt 9: Tempo
    if schritt == 9:
        mapped = _DOMINA_TEMPO_MAP.get(text)
        if not mapped:
            mapped = text.lower() if text.lower() in ("langsam", "normal", "schnell") else "normal"
        s["onboarding_daten"]["tempo"] = mapped
        s["onboarding_schritt"] = 10
        await update.message.reply_text(t("ONBOARDING_DOMINA_SCHRITT_ZEITEN"), parse_mode="Markdown")
        return

    # Schritt 10: Kinderfreie Zeiten → Abschluss
    if schritt == 10:
        kinderfreie_zeiten = zeiten.parse_kinderfreie_zeiten(text)
        if kinderfreie_zeiten is None:
            await update.message.reply_text(
                t("COMMON_ZEITEN_UNVERSTANDEN"), parse_mode="Markdown",
            )
            return
        s["onboarding_daten"]["kinderfreie_zeiten"] = kinderfreie_zeiten

        await _abschluss_domina(update, context, s, chat_id)


async def _abschluss_domina(update: Update, context, s: dict, chat_id: str) -> None:
    d = s["onboarding_daten"]
    interessen = d.get("interessen", [])
    grenzen = d.get("grenzen", [])
    kinderfreie_zeiten = d.get("kinderfreie_zeiten", [])

    profile = {
        "user_id": "domina",
        "rolle": "domina",
        "erfahrungsstand": d.get("erfahrungsstand", "Anfänger"),
        "interessen": interessen,
        "grenzen": grenzen,
        "ziele": d.get("ziele", ""),
        "tempo": d.get("tempo", "normal"),
        "kinderfreie_zeiten": kinderfreie_zeiten,
        # Setup-Wizard (Veröffentlichungs-Schritt 3): persona_config-Felder
        # landen im selben Profil und werden unten via load() in den Cache geholt.
        "sprache": d.get("sprache", ""),
        # UI-Locale (Menüs/Festtexte) folgt der gewählten Sprache automatisch
        "bot_locale": persona_config.locale_fuer_sprache(d.get("sprache", "")),
        "persona_stil": d.get("persona_stil", ""),
        "modus": d.get("modus", ""),
        "dom_geschlecht": d.get("dom_geschlecht", ""),
        "sub_geschlecht": d.get("sub_geschlecht", ""),
        "telegram_chat_id": paare.dom_chat_id(),
        "onboarding_abgeschlossen": True,
        "aktuelles_level": 1,
        "aufgaben_schwierigkeit": "normal",
        "erstellt_am": datetime.now(timezone.utc).isoformat(),
    }

    try:
        await qdrant.upsert_user_profile("domina", profile)
    except Exception as e:
        # KEINE Erfolgs-Zusammenfassung bei Speicher-Fehler: das Onboarding
        # startete sonst beim nächsten Text kommentarlos von vorn. State bleibt
        # stehen → die letzte Antwort erneut senden wiederholt den Abschluss.
        logger.error("Fehler beim Speichern des Domina-Profils: %s", e)
        await update.message.reply_text(t("FEHLER_ALLGEMEIN"))
        return

    # Wizard-Felder in den persona_config-Cache übernehmen (liest das frisch
    # gespeicherte Profil – ab jetzt wirken Sprache/Rollen/Stil sofort).
    await persona_config.load()

    state.set_mode(chat_id, "chat")
    s.pop("onboarding_schritt", None)
    s.pop("onboarding_rolle", None)
    s.pop("onboarding_daten", None)

    stil_label = persona_presets.aktuelles_label()
    zusammenfassung = t_modus(
        "ONBOARDING_DOMINA_ZUSAMMENFASSUNG",
        sprache=d.get("sprache") or "Deutsch",
        modus=modi.label(),
        rollen=rollen.aktuelle_kombi_label(),
        stil=stil_label,
        erfahrungsstand=d.get("erfahrungsstand", "–"),
        interessen=", ".join(interessen) if interessen else "–",
        grenzen=", ".join(grenzen) if grenzen else "keine",
        ziele=d.get("ziele", "–"),
        tempo=d.get("tempo", "normal"),
        zeiten=", ".join(kinderfreie_zeiten) if kinderfreie_zeiten else "immer frei",
    )

    await telegram_helper.reply_markdown_safe(update.message, zusammenfassung)

    # limits_check matcht mit einer DEUTSCHEN Synonym-Liste – bei anderer
    # Antwortsprache greift nur das wörtliche Matching (gleiche Warnung wie
    # in /einstellungen).
    if d.get("sprache"):
        await update.message.reply_text(
            t("EINSTELLUNGEN_SPRACHE_LIMITS_WARNUNG", sprache=d["sprache"]),
            parse_mode="Markdown",
        )

    # Sklaven informieren falls Profil vorhanden – sonst einladen, jetzt das
    # eigene Profil anzulegen (die Sub-Seite wartete auf die Modus-Wahl).
    try:
        sklave_profil = await qdrant.get_user_profile("sklave")
        if sklave_profil and sklave_profil.get("onboarding_abgeschlossen"):
            await telegram_helper.send_sklave(context.bot, t_modus("ONBOARDING_SKLAVE_INFO_AKTIV"))
        else:
            await telegram_helper.send_sklave(context.bot, t_modus("ONBOARDING_PARTNER_EINLADUNG"))
    except Exception as e:
        logger.error("Fehler bei Sklave-Benachrichtigung: %s", e)


# ---------------------------------------------------------------------------
# Sklave Flow
# ---------------------------------------------------------------------------

async def _handle_sklave(
    update: Update,
    context,
    s: dict,
    chat_id: str,
    schritt: int,
    text: str,
) -> None:

    # Schritt 1: Absolute Grenzen
    if schritt == 1:
        if text.lower() == "keine":
            hard_limits = []
        else:
            hard_limits = [x.strip() for x in text.split(",") if x.strip()]
        s["onboarding_daten"]["hard_limits"] = hard_limits
        s["onboarding_schritt"] = 2
        await update.message.reply_text(t_modus("ONBOARDING_SKLAVE_SCHRITT_2"), parse_mode="Markdown")
        return

    # Schritt 2: Vorlieben
    if schritt == 2:
        vorlieben = [x.strip() for x in text.split(",") if x.strip()]
        s["onboarding_daten"]["vorlieben"] = vorlieben
        s["onboarding_schritt"] = 3
        await update.message.reply_text(t_modus("ONBOARDING_SKLAVE_SCHRITT_3"), parse_mode="Markdown")
        return

    # Schritt 3: Erfahrungsstand → Abschluss
    if schritt == 3:
        s["onboarding_daten"]["erfahrungsstand"] = text
        await _abschluss_sklave(update, context, s, chat_id)


async def _abschluss_sklave(update: Update, context, s: dict, chat_id: str) -> None:
    d = s["onboarding_daten"]
    hard_limits = d.get("hard_limits", [])
    vorlieben = d.get("vorlieben", [])

    profile = {
        "user_id": "sklave",
        "rolle": "sklave",
        "erfahrungsstand": d.get("erfahrungsstand", ""),
        "hard_limits": hard_limits,
        "vorlieben": vorlieben,
        "telegram_chat_id": paare.sub_chat_id(),
        "onboarding_abgeschlossen": True,
        "punkte": 0,
        "streak": 0,
        "streak_max": 0,
        "abzeichen": [],
        "erstellt_am": datetime.now(timezone.utc).isoformat(),
    }

    try:
        await qdrant.upsert_user_profile("sklave", profile)
    except Exception as e:
        # KEIN "gespeichert" bei Speicher-Fehler (siehe Domina-Pfad oben).
        logger.error("Fehler beim Speichern des Sklave-Profils: %s", e)
        await update.message.reply_text(t("FEHLER_ALLGEMEIN"))
        return

    state.set_mode(chat_id, "chat")
    s.pop("onboarding_schritt", None)
    s.pop("onboarding_rolle", None)
    s.pop("onboarding_daten", None)

    await update.message.reply_text(t_modus("ONBOARDING_SKLAVE_GESPEICHERT"), parse_mode="Markdown")

    # Domina informieren
    try:
        domina_profil = await qdrant.get_user_profile("domina")
        if domina_profil and domina_profil.get("onboarding_abgeschlossen"):
            await telegram_helper.send_domina(
                context.bot,
                t_modus(
                    "ONBOARDING_DOMINA_INFO_SKLAVE_FERTIG",
                    limits=", ".join(hard_limits) if hard_limits else "keine",
                    vorlieben=", ".join(vorlieben) if vorlieben else "–",
                    erfahrungsstand=d.get("erfahrungsstand", "–"),
                ),
            )
    except Exception as e:
        logger.error("Fehler bei Domina-Benachrichtigung: %s", e)
