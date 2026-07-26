"""
/stats Command – Punkte, Streak, Abzeichen, Privilegien, Storylines des Sklaven.
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes

from bot import config
from bot.services import paare
from bot.services import qdrant, kategorie_logik
from bot.services.punkte import format_abzeichen, SKLAVE_ABZEICHEN
from bot.messages import t

logger = logging.getLogger(__name__)


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)

    # Nur für Sklave – Hinweis kommt zentral aus main.falsche_rolle_hinweis (F8),
    # sonst bekäme die Domina ihn doppelt.
    if chat_id != paare.sub_chat_id():
        return

    profil = await qdrant.get_user_profile("sklave") or {}

    punkte = profil.get("punkte", 0)
    streak = profil.get("streak", 0)
    streak_max = profil.get("streak_max", 0)
    abzeichen_ids = profil.get("abzeichen", [])
    tasks_gesamt = await qdrant.get_completed_task_count("sklave")
    score_data = await qdrant.get_vertrauens_score("sklave")
    kategorien_set = await qdrant.get_completed_kategorien_set("sklave")
    wunsch_kategorien = profil.get("wunsch_kategorien", [])
    aktive_privilegien = [
        p for p in profil.get("aktive_privilegien", [])
        if p.get("domina_bestaetigt") and not p.get("verbraucht")
    ]
    privilegien_eingeloest = profil.get("privilegien_eingeloest", 0)
    arcs_abgeschlossen = profil.get("arcs_abgeschlossen", 0)

    # Vorschlags-Übernahme-Quote
    uebernommen, abgelehnt = await _vorschlags_quote()

    # Nächstes Abzeichen berechnen
    naechstes = _naechstes_abzeichen(
        punkte, streak, tasks_gesamt, len(kategorien_set),
        privilegien_eingeloest, arcs_abgeschlossen, set(abzeichen_ids),
    )

    text = (
        f"📊 *Deine Statistiken*\n\n"
        f"⭐ Punkte: *{punkte}*\n"
        f"🔥 Aktueller Streak: *{streak}*\n"
        f"🏆 Bester Streak: *{streak_max}*\n"
        f"✅ Erledigte Tasks: *{tasks_gesamt}*\n"
        f"🎨 Kategorien-Vielfalt: *{len(kategorien_set)} / {len(kategorie_logik.alle_kategorien(profil))}*\n"
        f"🤝 Vertrauens-Score: *{score_data['score']}/100* ({score_data['stufe']})\n"
    )

    wette = profil.get("wette") or {}
    if wette.get("einsatz"):
        text += f"🎰 Aktive Wette: *{wette['einsatz']} Punkte* – doppelt oder nichts\n"

    if uebernommen + abgelehnt > 0:
        quote = round(100 * uebernommen / (uebernommen + abgelehnt))
        text += f"📥 Vorschlag-Quote: *{uebernommen} übernommen / {abgelehnt} abgelehnt* ({quote}%)\n"

    if wunsch_kategorien:
        # Kein Italic um Kategorienamen: Unterstriche in Namen (Buttplug_Tragen,
        # eigene Kategorien) würden das Legacy-Markdown-Parsing brechen.
        text += f"\n💚 *Deine Wunsch-Kategorien:*\n{kategorie_logik.anzeige_liste(wunsch_kategorien)}\n"

    # Gelernte Intensitäts-Level (nur Kategorien, die vom Normal abweichen)
    kategorie_level = profil.get("kategorie_level", {}) or {}
    abweichend = {
        k: v for k, v in kategorie_level.items()
        if int(v) != kategorie_logik.LEVEL_DEFAULT
    }
    if abweichend:
        zeilen = ", ".join(
            f"{kategorie_logik.anzeige_name(k)}: {kategorie_logik.level_label(v)}"
            for k, v in sorted(abweichend.items(), key=lambda x: -int(x[1]))
        )
        text += f"\n📐 *Gelernte Intensität:* {zeilen}\n"

    if aktive_privilegien:
        text += "\n🎁 *Aktive Privilegien:*\n"
        for p in aktive_privilegien:
            tll = p.get("gueltig_bis", "")
            zusatz = f" (gültig bis {tll[:10]})" if tll else ""
            text += f"  • {p.get('name','?')}{zusatz}\n"

    if privilegien_eingeloest > 0 or arcs_abgeschlossen > 0:
        text += (
            f"\n📈 *Lifetime:* {privilegien_eingeloest} Privilegien eingelöst, "
            f"{arcs_abgeschlossen} Storylines abgeschlossen\n"
        )

    # Zähler nur über den sichtbaren Katalog – verdiente geheime Abzeichen kommen
    # als "+N 🤫" dazu (deutet an, dass es Verstecktes gibt, ohne Ziele zu verraten).
    sichtbare_ids = {a["id"] for a in SKLAVE_ABZEICHEN}
    sichtbar_verdient = sum(1 for a in abzeichen_ids if a in sichtbare_ids)
    geheim_verdient = len(abzeichen_ids) - sichtbar_verdient
    geheim_zusatz = f" +{geheim_verdient} 🤫" if geheim_verdient > 0 else ""
    text += (f"\n🎖 *Abzeichen ({sichtbar_verdient}/{len(SKLAVE_ABZEICHEN)}{geheim_zusatz}):*\n"
             f"{format_abzeichen(abzeichen_ids)}\n")

    if naechstes:
        text += f"\n🎯 *Nächstes Abzeichen:*\n{naechstes['emoji']} {naechstes['name']} – {naechstes['hinweis']}"

    await update.message.reply_text(text, parse_mode="Markdown")


async def _vorschlags_quote() -> tuple[int, int]:
    """Zählt übernommene vs abgelehnte Tiny-Tasks – als count-Queries statt
    200er-Scroll (Review D8/M4: exakt statt ungeordneter Teilmenge, und ohne
    Payload-Transfer)."""
    try:
        from qdrant_client import models as qm
        from bot.services.qdrant import client

        async def _count(status: str) -> int:
            res = await qdrant.run_io(client.count,
                collection_name="knowledge_base",
                count_filter=qm.Filter(must=[
                    qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key("domina"))),
                    qm.FieldCondition(key="typ", match=qm.MatchValue(value="tiny_task")),
                    qm.FieldCondition(key="status", match=qm.MatchValue(value=status)),
                ]),
                exact=True,
            )
            return res.count

        return await _count("uebernommen"), await _count("abgelehnt")
    except Exception as e:
        logger.warning("Vorschlags-Quote-Berechnung fehlgeschlagen: %s", e)
        return 0, 0


_ABZEICHEN_SCHWELLEN = {
    "erster_task":     lambda p, s, t, k, pr, ar: (t >= 1, f"Noch {max(0, 1 - t)} Task(s) erledigen"),
    "streak_5":        lambda p, s, t, k, pr, ar: (s >= 5, f"Noch {max(0, 5 - s)} Tage Streak"),
    "streak_10":       lambda p, s, t, k, pr, ar: (s >= 10, f"Noch {max(0, 10 - s)} Tage Streak"),
    "streak_30":       lambda p, s, t, k, pr, ar: (s >= 30, f"Noch {max(0, 30 - s)} Tage Streak"),
    "punkte_100":      lambda p, s, t, k, pr, ar: (p >= 100, f"Noch {max(0, 100 - p)} Punkte"),
    "punkte_500":      lambda p, s, t, k, pr, ar: (p >= 500, f"Noch {max(0, 500 - p)} Punkte"),
    "tasks_25":        lambda p, s, t, k, pr, ar: (t >= 25, f"Noch {max(0, 25 - t)} Tasks erledigen"),
    "tasks_100":       lambda p, s, t, k, pr, ar: (t >= 100, f"Noch {max(0, 100 - t)} Tasks erledigen"),
    "vielfalt_5":      lambda p, s, t, k, pr, ar: (k >= 5, f"Noch {max(0, 5 - k)} verschiedene Kategorien"),
    "vielfalt_15":     lambda p, s, t, k, pr, ar: (k >= 15, f"Noch {max(0, 15 - k)} verschiedene Kategorien"),
    "privileg_erstes": lambda p, s, t, k, pr, ar: (pr >= 1, "Erstes Privileg einlösen mit /privileg"),
    "privileg_5":      lambda p, s, t, k, pr, ar: (pr >= 5, f"Noch {max(0, 5 - pr)} Privilegien einlösen"),
    "arc_erste":       lambda p, s, t, k, pr, ar: (ar >= 1, "Erste Storyline abschließen"),
}


def _naechstes_abzeichen(
    punkte: int,
    streak: int,
    tasks_gesamt: int,
    kategorien_count: int,
    privilegien_eingeloest: int,
    arcs_abgeschlossen: int,
    vorhandene: set,
) -> dict | None:
    """Gibt das nächste erreichbare Abzeichen mit Fortschrittshinweis zurück."""
    for abzeichen in SKLAVE_ABZEICHEN:
        aid = abzeichen["id"]
        if aid in vorhandene:
            continue
        check = _ABZEICHEN_SCHWELLEN.get(aid)
        if not check:
            continue
        fertig, hinweis = check(
            punkte, streak, tasks_gesamt, kategorien_count,
            privilegien_eingeloest, arcs_abgeschlossen,
        )
        if not fertig:
            return {**abzeichen, "hinweis": hinweis}
    return None
