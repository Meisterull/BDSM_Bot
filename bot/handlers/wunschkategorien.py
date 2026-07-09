"""
Wunschkategorien Handler – Sklave wählt bis zu 3 Lieblings-Kategorien.

Diese fließen als 'soft preference' in die Tiny-Task-Generierung ein,
ohne dass die Domina sich daran halten muss.

Auswahl per Nummer aus dem Kategorien-Pool (Katalog + eigene Kategorien) ODER
als Freitext – unbekannter Freitext legt eine EIGENE Kategorie an
(`eigene_kategorien` im Profil, fließt über kategorie_logik.alle_kategorien in
Generierung/Klassifikation/Anzeige ein).
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot import config, state
from bot.services import paare
from bot.services import qdrant, kategorie_logik
from bot.messages import t

logger = logging.getLogger(__name__)

MAX_WUNSCHKATEGORIEN = 3


def _parse_auswahl(text: str, pool: list[str]) -> tuple[list[str], list[str], int | None]:
    """Auswahl parsen: Zahlen wählen per Nummer aus dem Pool, alles andere ist
    eine Kategorie als Freitext (Leerzeichen → `_` wie im Katalog-Stil); kennt
    der Pool sie nicht, wird sie als neue eigene Kategorie angelegt.
    Returns (gewaehlte, neue_eigene, ungueltige_nummer)."""
    gewaehlte: list[str] = []
    neue: list[str] = []
    pool_lower = {k.lower(): k for k in pool}
    for item in text.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        if item.isdigit():
            n = int(item)
            if n < 1 or n > len(pool):
                return [], [], n
            kat = pool[n - 1]
        else:
            kat = "_".join(item.split())
            if kat.lower() in pool_lower:
                kat = pool_lower[kat.lower()]  # exakte Pool-Schreibweise übernehmen
            else:
                neue.append(kat)
                pool_lower[kat.lower()] = kat
        if kat not in gewaehlte:
            gewaehlte.append(kat)
    return gewaehlte, neue, None


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/wunschkategorien – zeigt aktuelle Wahl, lässt neue setzen."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.sub_chat_id():
        return

    profile = await qdrant.get_user_profile("sklave") or {}
    aktuelle = profile.get("wunsch_kategorien", [])

    # anzeige_name/-liste: Unterstriche in Kategorienamen brechen Legacy-Markdown;
    # Eingaben in Anzeige-Form ("Buttplug Tragen") joint _parse_auswahl wieder mit "_".
    aktuelle_str = kategorie_logik.anzeige_liste(aktuelle) if aktuelle else "keine"

    # Nummerierte Liste des Pools (Katalog + eigene Kategorien) anzeigen
    katalog = "\n".join(
        f"{i+1}. {kategorie_logik.anzeige_name(kat)}"
        for i, kat in enumerate(kategorie_logik.alle_kategorien(profile))
    )

    state.set_mode(chat_id, "wunschkategorien_wahl")

    await update.message.reply_text(
        t("WUNSCHKAT_MENU", aktuell=aktuelle_str, max=MAX_WUNSCHKATEGORIEN, katalog=katalog),
        parse_mode="Markdown",
    )


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verarbeitet die Auswahl-Eingabe des Sklaven."""
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()

    profile = await qdrant.get_user_profile("sklave") or {}
    neue_eigene: list[str] = []

    if text.lower() == "keine":
        gewaehlte = []
    else:
        pool = kategorie_logik.alle_kategorien(profile)
        gewaehlte, neue_eigene, ungueltig = _parse_auswahl(text, pool)

        if ungueltig is not None:
            await update.message.reply_text(
                t("WUNSCHKAT_BEREICH", n=ungueltig, max=len(pool)),
            )
            return

        if not gewaehlte:
            await update.message.reply_text(t("WUNSCHKAT_KEINE_NUMMERN"))
            return

        if len(gewaehlte) > MAX_WUNSCHKATEGORIEN:
            await update.message.reply_text(
                t("WUNSCHKAT_MAX", max=MAX_WUNSCHKATEGORIEN, anzahl=len(gewaehlte)),
            )
            return
    # Gewählte Katalog-Wünsche additiv in die frei formulierten `vorlieben` spiegeln,
    # damit beide zusammenwachsen (Datenplan: nicht-löschender Merge). Das Alt-Feld
    # `wunsch_kategorien` bleibt für die bestehenden Commands erhalten.
    # patch_profile_fields statt Full-Upsert: überschreibt keine parallel
    # gepatchten Felder (punkte/streak) mit dem stale Read.
    bestehende_vorlieben = profile.get("vorlieben") or []
    vorlieben = bestehende_vorlieben + [k for k in gewaehlte if k not in bestehende_vorlieben]
    felder = {
        "wunsch_kategorien": gewaehlte,
        "vorlieben": vorlieben,
    }
    if neue_eigene:
        bestehende_eigene = profile.get("eigene_kategorien") or []
        felder["eigene_kategorien"] = bestehende_eigene + [
            k for k in neue_eigene if k not in bestehende_eigene
        ]
    await qdrant.patch_profile_fields("sklave", felder)

    state.set_mode(chat_id, "chat")

    if gewaehlte:
        antwort = t("WUNSCHKAT_GESPEICHERT", liste=kategorie_logik.anzeige_liste(gewaehlte))
        if neue_eigene:
            antwort += t("WUNSCHKAT_EIGENE_NEU", liste=kategorie_logik.anzeige_liste(neue_eigene))
        await update.message.reply_text(antwort, parse_mode="Markdown")
    else:
        await update.message.reply_text(t("WUNSCHKAT_ZURUECKGESETZT"))

    logger.debug("Sklave hat Wunschkategorien gesetzt: %s (neu angelegt: %s)", gewaehlte, neue_eigene)
