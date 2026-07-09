"""
Profil Handler – /profil Befehl zum Anzeigen und Bearbeiten des Profils.
Funktioniert für Domina und Sklave.
"""
import re

from telegram import Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, telegram_helper, zeiten
from bot.messages import t

DOMINA_FELDER = {
    "1": ("erfahrungsstand",    "Erfahrungsstand (Anfänger / etwas Erfahrung / erfahren)"),
    "2": ("interessen",         "Interessen (kommagetrennt, z.B. Service, Rituale)"),
    "3": ("grenzen",            "Grenzen (kommagetrennt)"),
    "4": ("ziele",              "Ziele (Freitext)"),
    "5": ("tempo",              "Tempo (langsam / normal / schnell)"),
    "6": ("kinderfreie_zeiten", "Kinderfreie Zeiten (kommagetrennt, z.B. 07:00-08:00, 20:00-23:00)"),
    "7": ("kind_anzahl",        "Anzahl Kinder im Haushalt (Zahl; 0 wenn keine)"),
}

SKLAVE_FELDER = {
    "1": ("hard_limits",     "Absolute Grenzen (kommagetrennt)"),
    "2": ("vorlieben",       "Vorlieben (kommagetrennt)"),
    "3": ("erfahrungsstand", "Erfahrungsstand (Freitext)"),
}

# Aus der Profil-Anzeige zurückkopierte Nummerierungs-/Label-Prefixe
# ("1️⃣ Absolute Grenzen: ..., ..." / "2. Vorlieben: ...") – landeten sonst
# wortwörtlich als erster Listeneintrag im Profil und brechen das Limit-Matching.
_LISTEN_PREFIX_RE = re.compile(
    r"^\s*(?:\d+️?⃣\s*(?:[^:,]{0,40}:)?|\d+[.)]\s*[^:,]{0,40}:)\s*"
)


def _parse_liste(text: str) -> list[str]:
    """Kommagetrennte Eingabe in eine saubere Liste (Label-Prefixe abgestreift).
    Kommas INNERHALB von Klammern trennen nicht – sonst zerfällt
    'Analspiele (Strapon, Plug, fingern)' in drei kaputte Fragmente."""
    items, buf, tiefe = [], [], 0
    for zeichen in text + ",":
        if zeichen == "(":
            tiefe += 1
        elif zeichen == ")":
            tiefe = max(0, tiefe - 1)
        if zeichen == "," and tiefe == 0:
            item = _LISTEN_PREFIX_RE.sub("", "".join(buf).strip()).strip()
            if item:
                items.append(item)
            buf = []
        else:
            buf.append(zeichen)
    return items


def _format_profil(profile: dict, rolle: str) -> str:
    esc = telegram_helper.escape_md
    if rolle == "domina":
        zeiten = profile.get("kinderfreie_zeiten", [])
        return t(
            "PROFIL_DOMINA",
            erfahrungsstand=esc(profile.get("erfahrungsstand", "–")),
            interessen=esc(", ".join(profile.get("interessen", [])) or "–"),
            grenzen=esc(", ".join(profile.get("grenzen", [])) or "–"),
            ziele=esc(profile.get("ziele", "–")),
            tempo=esc(profile.get("tempo", "–")),
            zeiten=esc(", ".join(zeiten) if zeiten else "nicht angegeben"),
            kinder=esc(str(profile.get("kind_anzahl", "–"))),
            level=profile.get("aktuelles_level", 1),
        )
    else:
        return t(
            "PROFIL_SKLAVE",
            hard_limits=esc(", ".join(profile.get("hard_limits", [])) or "–"),
            vorlieben=esc(", ".join(profile.get("vorlieben", [])) or "–"),
            erfahrungsstand=esc(profile.get("erfahrungsstand", "–")),
        )


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    # Auth-Guard: /profil zeigt hochintime Daten – Fremde bekommen NICHTS
    # (ohne Guard würde jeder fremde Chat als rolle="sklave" das Profil sehen).
    if not paare.ist_autorisiert(chat_id):
        return
    rolle = "domina" if chat_id == paare.dom_chat_id() else "sklave"

    profile = await qdrant.get_user_profile(rolle)
    if not profile:
        await update.message.reply_text(t("PROFIL_KEIN"))
        return

    s = state.get(chat_id)
    state.set_mode(chat_id, "profil_wahl")
    s["profil_edit_rolle"] = rolle

    await update.message.reply_text(
        _format_profil(profile, rolle),
        parse_mode="MarkdownV2",
    )


async def abbrechen_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bricht jeden aktiven State ab – räumt alle möglichen Keys auf."""
    chat_id = str(update.effective_chat.id)
    if not paare.ist_autorisiert(chat_id):
        return
    s = state.get(chat_id)

    # Alle möglichen State Keys aufräumen – zentrale Liste (state.FLOW_STATE_KEYS)
    # wunsch_id sichern, BEVOR die Keys gelöscht werden – sonst ist der Zweig unten tot.
    wunsch_id = s.get("wunsch_id")
    state.clear_flow_keys(chat_id)

    # Wunsch ausstehend? → direkt in Entscheidungs-Flow
    if wunsch_id:
        s["wunsch_id"] = wunsch_id  # wiederherstellen, der Entscheidungs-Flow braucht ihn
        state.set_mode(chat_id, "wunsch_entscheidung")
        await update.message.reply_text(
            t("COMMON_ABGEBROCHEN") + t("PROFIL_WUNSCH_WARTET"),
            parse_mode="Markdown",
        )
        return

    state.set_mode(chat_id, "chat")
    await update.message.reply_text(t("COMMON_ABGEBROCHEN"))


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)
    text = update.message.text.strip()
    mode = s.get("mode")
    rolle = s.get("profil_edit_rolle", "")

    if text.lower() in ("/abbrechen", "abbrechen"):
        await abbrechen_command(update, context)
        return

    felder = DOMINA_FELDER if rolle == "domina" else SKLAVE_FELDER

    if mode == "profil_wahl":
        if text not in felder:
            max_num = len(felder)
            await update.message.reply_text(t("PROFIL_ZAHL_BEREICH", max=max_num))
            return

        feld_key, feld_label = felder[text]
        s["profil_edit_feld"] = feld_key
        state.set_mode(chat_id, "profil_eingabe")
        await update.message.reply_text(
            t("PROFIL_NEUER_WERT", feld=telegram_helper.escape_md(feld_label)),
            parse_mode="MarkdownV2",
        )

    elif mode == "profil_eingabe":
        feld_key = s.get("profil_edit_feld")
        if not feld_key:
            state.set_mode(chat_id, "chat")
            return

        profile = await qdrant.get_user_profile(rolle) or {}

        listen_felder = {"interessen", "grenzen", "hard_limits", "vorlieben"}
        int_felder = {"kind_anzahl"}
        if feld_key == "kinderfreie_zeiten":
            neuer_wert = zeiten.parse_kinderfreie_zeiten(text)
            if neuer_wert is None:
                await update.message.reply_text(
                    t("COMMON_ZEITEN_UNVERSTANDEN"), parse_mode="Markdown",
                )
                return
        elif feld_key in listen_felder:
            neuer_wert = _parse_liste(text)
        elif feld_key in int_felder:
            try:
                neuer_wert = int(text.strip())
            except ValueError:
                await update.message.reply_text(t("PROFIL_GANZE_ZAHL"))
                return
        else:
            neuer_wert = text

        profile[feld_key] = neuer_wert  # lokales Abbild für den Anzeige-Fallback unten
        # Gezielt patchen (kein Full-Upsert mit dem stale Read von oben);
        # erlaube_geschuetzt: der manuelle Owner-Edit darf auch hard_limits/
        # kinderfreie_zeiten setzen – automatische Schreiber weiterhin nicht.
        await qdrant.patch_profile_fields(rolle, {feld_key: neuer_wert}, erlaube_geschuetzt=True)

        state.set_mode(chat_id, "profil_wahl")
        s.pop("profil_edit_feld", None)

        updated_profile = await qdrant.get_user_profile(rolle) or profile
        await update.message.reply_text(
            t("PROFIL_GESPEICHERT_PREFIX") + _format_profil(updated_profile, rolle),
            parse_mode="MarkdownV2",
        )