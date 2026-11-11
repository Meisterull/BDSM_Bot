"""
Tiny-Task-/Aufgaben-Vorschlag: Kontext-Rendering, Kategorien-Wahl, Machbarkeit, Versand, Feedback-Job.

Aus bot/scheduler/followup.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
"""
import logging
import random
import uuid
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from telegram import Bot
from qdrant_client import models as qm
from bot.services.qdrant import client
from bot import config
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, embeddings, privileg_effekte, limits_check, kategorie_logik, labels, zeiten
from bot.prompts import followup as fp
from bot.prompts import coach_persona
from bot.prompts import domina_coach
from bot.messages import t
from bot.scheduler import basis, schablonen

logger = logging.getLogger(__name__)


async def _arc_tag_anpassen(aufgabe: str, stimmung: str, sklave_profile: dict, domina_profile: dict) -> str | None:
    """Passt einen vorgenerierten Arc-Tag ans letzte (negative) Gefühl an.
    Gibt den angepassten Text zurück oder None (dann Original verwenden)."""
    try:
        prompt = fp.kette_anpassung(aufgabe, f"Storyline – sein letztes Gefühl war: {stimmung}", stimmung)
        angepasst = grok.clean_text(await grok.simple(prompt, reasoning=True))
        if not angepasst or len(angepasst) < 5:
            return None
        hl = sklave_profile.get("hard_limits", []) or []
        gr = domina_profile.get("grenzen", []) or []
        if await limits_check.verletzungen(angepasst, hl, gr):
            logger.warning("Arc-Tag-Anpassung grenzverletzend – Original verwenden.")
            return None
        return angepasst
    except Exception:
        logger.exception("Arc-Tag-Anpassung fehlgeschlagen")
        return None


async def _save_tiny_task(vorschlag: str, kategorien: list[str]) -> None:
    """Speichert einen gesendeten TinyTask-Vorschlag in der knowledge_base."""
    point_id = str(uuid.uuid4())
    # Vollständig speichern (kein Abschneiden) – der Text wird in der Rückfrage 1:1
    # wieder angezeigt. Cap nur auf Telegram-Limit, damit nichts unsinnig Großes reingeht.
    inhalt = vorschlag.strip()[:4000]
    try:
        # Kurzlabel fürs "NICHT wiederholen"-Listing in den Generator-Prompts –
        # Volltexte dort ankern das Modell auf die eigene Formel (Review D7, B1).
        kurzlabel = await labels.kurzlabel(inhalt)
        vector = await embeddings.get_embedding(inhalt)
        await qdrant.run_io(client.upsert,
            collection_name="knowledge_base",
            points=[qm.PointStruct(
                id=point_id,
                vector={"text": vector},
                payload={
                    "user_id": qdrant.mandanten_key("domina"),
                    "typ": "tiny_task",
                    "status": "vorgeschlagen",
                    "kategorien": kategorien,
                    "kategorie": kategorien[0] if kategorien else "allgemein",
                    "inhalt": inhalt,
                    "kurzlabel": kurzlabel,
                    "erstellt_am": datetime.now(timezone.utc).isoformat(),
                    "qdrant_point_id": point_id,
                },
            )],
        )
    except Exception as e:
        logger.exception("Fehler beim Speichern des TinyTask-Vorschlags")


# Konstantes Query-Embedding für den Hybrid-Kontext – einmal berechnen, dann cachen
# (der Text ändert sich nie, das Embedding bei jedem Job-Lauf neu zu holen ist verschenkt).
_TINY_QUERY_VECTOR: list | None = None


async def _tiny_query_vector() -> list:
    global _TINY_QUERY_VECTOR
    if _TINY_QUERY_VECTOR is None:
        _TINY_QUERY_VECTOR = await embeddings.get_embedding("aktuelle aufgaben und fortschritt")
    return _TINY_QUERY_VECTOR


async def _sende_arc_vorschlag(bot: Bot, arc_tag: dict, domina_profile: dict, sklave_profile: dict) -> None:
    """Aktive Storyline: vorgenerierten Arc-Tag (ggf. ans letzte negative Gefühl
    angepasst) als Tagesvorschlag an die Domina senden und den Arc voranschreiten."""
    from bot.handlers import arc as arc_handler
    from bot.services import punkte
    tag_nr = arc_tag.get("arc_tag", 1)
    arc_gesamt = arc_tag.get("arc_gesamt", 1)
    arc_thema = arc_tag.get("arc_thema", "")
    kat = arc_tag.get("kategorie") or "allgemein"
    titel = arc_tag.get("titel", "")
    aufgabe = arc_tag.get("aufgabe", "")

    # Adaptiv: war das letzte Gefühl negativ, den (vorgenerierten) Arc-Tag vor
    # dem Anzeigen ans Feedback anpassen. Domina sieht ohnehin nur den Vorschlag.
    erledigte = await qdrant.get_tasks_by_status(["erledigt"], limit=1, sort_by_datum=True)
    letzte_stimmung = erledigte[0].get("stimmung_klassifikation") if erledigte else None
    angepasst_hinweis = ""
    if letzte_stimmung in ("langweilig", "überfordert", "abgelehnt"):
        angepasst = await _arc_tag_anpassen(aufgabe, letzte_stimmung, sklave_profile, domina_profile)
        if angepasst:
            aufgabe = angepasst
            angepasst_hinweis = t("ARC_TAG_ANGEPASST", stimmung=letzte_stimmung)

    vorschlag = t(
        "ARC_TAG_VORSCHLAG", thema=arc_thema, tag=tag_nr, gesamt=arc_gesamt,
        titel=titel, kategorie=kategorie_logik.anzeige_name(kat), aufgabe=aufgabe,
        hinweis=angepasst_hinweis,
    )
    if basis._nach_llm_verworfen(paare.dom_chat_id(), "Arc-Tag-Vorschlag"):
        return
    await telegram_helper.send_domina(bot, vorschlag, parse_mode="Markdown")
    # Nur den Aufgaben-Kern speichern (nicht die gerenderte Template-Nachricht):
    # get_recent_tiny_tasks legt die Inhalte als "NICHT wiederholen"-Liste in die
    # Generator-Prompts – Template-Boilerplate verwässert das und frisst Tokens.
    await _save_tiny_task(f"{titel}: {aufgabe}" if titel else aufgabe, [kat])

    arc_abgeschlossen = await arc_handler.arc_tag_voranschreiten()
    if arc_abgeschlossen:
        neue = await punkte.arc_abgeschlossen()
        abgeschluss = t(
            "ARC_ABGESCHLOSSEN",
            thema=arc_abgeschlossen.get("thema", "?"),
            tage=arc_abgeschlossen.get("tage_gesamt", 0),
        )
        if neue:
            abgeschluss += t("ARC_NEUE_ABZEICHEN", liste=", ".join(
                f"{a['emoji']} {a['name']}" for a in neue
            ))
        await telegram_helper.send_domina(bot, abgeschluss, parse_mode="Markdown")
    logger.info("Arc-Tag %d/%d gesendet (Kategorie: %s)", tag_nr, arc_gesamt, kat)


async def _schwierigkeit_effektiv(domina_profile: dict, score: int) -> str:
    """Konfigurierte Schwierigkeit um Vertrauens-Score und Easy-Mode-Privileg korrigieren."""
    grundstufe = domina_profile.get("aufgaben_schwierigkeit", "normal")
    # Reihenfolge wichtig: die NIEDRIG-Schwelle gilt für JEDE Basis – stünde der
    # hoch→normal-Zweig zuerst, käme Basis "hoch" bei Score < 25 nie auf "niedrig".
    if score < config.VERTRAUEN_SCHWELLE_NIEDRIG:
        effektiv = "niedrig"
    elif score < config.VERTRAUEN_SCHWELLE_SENKEN and grundstufe == "hoch":
        effektiv = "normal"
    else:
        effektiv = grundstufe

    # Privileg 'easy_mode_3tage' überschreibt Schwierigkeit
    if await privileg_effekte.aktiver_easy_mode():
        effektiv = "niedrig"
    return effektiv


async def _waehle_kategorien(sklave_profile: dict, letzte_tiny_kategorien: list,
                             langeweile: bool, wunsch_aktiv: bool,
                             domina_praeferenzen: dict | None = None) -> tuple[list, str | None]:
    """Kategorien für den Vorschlag: Privileg 'naechste_aus_wunsch' (wunsch_aktiv)
    erzwingt die Wunschkategorien, sonst gewichtete Auswahl (bei Langeweile Richtung
    Wünsche, Domina-Präferenz als eigenes Gewicht). Der Privileg-Verbrauch erfolgt
    erst nach erfolgreichem Send. Gibt (kategorien, cross_kategorie) zurück –
    cross_kategorie ist der Cross-Cluster-Slot (frisches Thema) oder None."""
    sklave_wunsch_kategorien = sklave_profile.get("wunsch_kategorien", [])
    if wunsch_aktiv and sklave_wunsch_kategorien:
        logger.info("Tiny-Task-Kategorien aus Wunschliste übernommen (Privileg).")
        return sklave_wunsch_kategorien[:3], None

    gewaehlte_kategorien, cross_kategorie = kategorie_logik.gewichtete_auswahl(
        sklave_profile,
        letzte_kategorien=letzte_tiny_kategorien,
        count=3,
        langeweile=langeweile,
        domina_praeferenzen=domina_praeferenzen,
        mit_cross_info=True,
    )
    if langeweile:
        logger.info("Langeweile erkannt – Auswahl Richtung Wunsch-Kategorien gewichtet.")
    return gewaehlte_kategorien, cross_kategorie


def _kategorie_level_hinweis(sklave_profile: dict, kategorien: list) -> str:
    """Progressive Steigerung: gelerntes Intensitäts-Level je gewählter Kategorie."""
    if not kategorien:
        return ""
    teile = [
        f"{k}: {kategorie_logik.level_label(kategorie_logik.kategorie_level(sklave_profile, k))}"
        for k in kategorien
    ]
    return "Gelerntes Intensitäts-Level je Kategorie (aus seinem Feedback): " + ", ".join(teile)


def _eintrag_alter_tage(datum: str) -> int:
    """Alter eines Konversations-Eintrags in Tagen; unparsebar → 0 (nicht filtern)."""
    try:
        d = datetime.fromisoformat(datum)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days
    except (ValueError, TypeError):
        return 0


def _stimmung_aktuell(entry: dict | None, max_stunden: int = 24) -> str:
    """Stimmungs-Text nur, wenn der Eintrag frisch ist. Die Prompts labeln ihn
    als 'Aktuelle Stimmung' – eine Tage alte Antwort würde Tiny-Task/Followup
    auf eine verjährte Gefühlslage lenken. Frisch = die Antwort auf die heutige
    16:00-Stimmungsfrage (oder gestern Abend); älter → weglassen."""
    if not entry:
        return ""
    try:
        d = datetime.fromisoformat(entry.get("datum", ""))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ""
    if datetime.now(timezone.utc) - d > timedelta(hours=max_stunden):
        return ""
    return entry.get("zusammenfassung", "")


async def _vorschlag_kontext(domina_profile: dict, sklave_profile: dict, wunsch_aktiv: bool = False) -> dict:
    """Sammelt den kompletten Prompt-Kontext (Task-Historie, Tiny-Tasks, Inspirationen,
    Gespräch, Stimmung, Bewertung, Vertrauen, Kategorien-Auswahl, Persönlichkeit)
    als kwargs für fp.tiny_task_vorschlag / fp.ausfuehrlicher_task_vorschlag.
    wunsch_aktiv = 'naechste_aus_wunsch'-Privileg vorhanden (Detektion, kein Verbrauch)."""
    alle_tasks = await qdrant.get_tasks_by_status(
        ["erledigt", "offen", "nicht_erledigt"], sort_by_datum=True
    )
    # Mit Zeitabstand ("vor 4 Tagen: …"): eine undatierte "letzte Aufgaben"-Liste
    # datiert das Modell selbst und erfindet Zeitbezüge – "gestern" über eine
    # mehrere Tage alte Aufgabe (Live-Befund 08.08.).
    letzte_aufgaben = [
        zeiten.mit_alter_label(t.get("aufgabe", "")[:80], t.get("erteilt_am", ""))
        for t in alle_tasks[:5] if t.get("aufgabe")
    ]

    # Letzte TinyTask-Vorschläge + Kategorien in einem Qdrant-Request laden.
    # Volltexte nur für die deterministischen Wiederholungs-Detektoren unten –
    # in den Prompt gehen ausschließlich Kurzlabels (Review D7, B1).
    letzte_tiny_tasks, letzte_tiny_kategorien, letzte_tiny_volltexte = \
        await qdrant.get_recent_tiny_tasks(limit=7)

    # Opener-/Struktur-Wiederholung deterministisch sperren (Live-Befund 16.07.:
    # derselbe "Da du heute mehr Zeit hast …"-Einstieg stand an zwei Tagen fast
    # wortgleich, und drei Vorschläge in Folge waren als Phasen/Stufen-Programm
    # gebaut). Kurzlabels dedupen nur den INHALT – Einstiegssatz und Aufbau
    # sieht die "NICHT wiederholen"-Liste nicht.
    verbrauchte_anfaenge = list(dict.fromkeys(
        a for a in (schablonen._vorschlag_anfang(v) for v in letzte_tiny_volltexte[:4]) if a
    ))
    # D9/DIV3: Abschluss-Sätze genauso sperren wie Opener.
    verbrauchte_abschluesse = list(dict.fromkeys(
        a for a in (schablonen._vorschlag_abschluss(v) for v in letzte_tiny_volltexte[:4]) if a
    ))
    mehrstufig_bremse = sum(
        1 for v in letzte_tiny_volltexte[:3] if schablonen._STUFEN_RE.search(v)
    ) >= 2

    # Letzte Inspirationen für Cross-Kontext laden
    letzte_inspirationen = await qdrant.get_recent_inspirationen(limit=5)

    # Conversation context (hybrid). Altersfilter (Review D7, B6): ein >7 Tage
    # altes Gespräch ist kein "aktueller Kontext" für den Tages-Vorschlag –
    # weglassen, statt das Modell auf verjährte Themen zu lenken.
    query_vector = await _tiny_query_vector()
    ctx_entries = await qdrant.get_hybrid_conversation_context("domina", query_vector, limit=3,
                                                                felder=qdrant.KONTEXT_FELDER)
    ctx_entries = [e for e in ctx_entries if _eintrag_alter_tage(e.get("datum", "")) <= 7]
    conversation_context = domina_coach.format_context(ctx_entries)

    # Stimmung des Sklaven – nur wenn frisch (heutige Stimmungsfrage läuft um
    # 16:00, der Tiny-Task-Vorschlag um 18:00 verarbeitet die Antwort direkt)
    stimmung = _stimmung_aktuell(await qdrant.get_latest_stimmung("sklave"))

    bewertungs_kontext = await qdrant.get_bewertungs_kontext("sklave")

    # Vertrauens-Score → Schwierigkeit automatisch anpassen
    score_data = await qdrant.get_vertrauens_score("sklave")
    score = score_data.get("score", 50)
    vertrauens_kontext = (
        f"Vertrauens-Score des Sklaven: {score}/100 ({score_data.get('stufe', '')}), "
        f"Erledigungsquote: {score_data.get('quote', 0)}%\n"
    )

    abgelehnte_tiny_tasks = await qdrant.get_recent_rejected_tiny_tasks(limit=5)

    # Langeweile-Signal: war die zuletzt erledigte Aufgabe langweilig/überfordernd?
    # Dann lenkt die gewichtete Auswahl stärker Richtung Wunsch-Kategorien.
    letzte_erledigt = next((t for t in alle_tasks if t.get("status") == "erledigt"), None)
    langeweile = bool(
        letzte_erledigt
        and letzte_erledigt.get("stimmung_klassifikation") in ("langweilig", "überfordert")
    )
    gewaehlte_kategorien, cross_kategorie = await _waehle_kategorien(
        sklave_profile, letzte_tiny_kategorien, langeweile, wunsch_aktiv,
        domina_praeferenzen=domina_profile.get("kategorie_praeferenzen", {}))

    # KOMBI-IMPULS (Nutzer-Wunsch 06.09.): an ~jedem dritten Tag zwei Vorlieben
    # aus verschiedenen Themen zu EINER Szene verweben (gern ein Alltags-Ritual
    # als Bühne). Nicht am Wunsch-Privileg-Tag – der gehört den explizit
    # gewünschten Kategorien.
    kombi_vorlieben = None
    if not wunsch_aktiv and random.random() < config.KOMBI_IMPULS_CHANCE:
        kombi_vorlieben = schablonen._kombi_vorlieben_wahl(
            sklave_profile.get("vorlieben", []),
            dislikes=kategorie_logik.dislike_kategorien(sklave_profile),
        )
        if kombi_vorlieben:
            logger.info("Kombi-Impuls aktiv: %s + %s",
                        kombi_vorlieben[0][:40], kombi_vorlieben[1][:40])

    # Beiwerk der letzten 3 Vorschläge sperren (Zutaten-Sperrliste, s. Detektor).
    verbrauchte_zutaten = schablonen._verbrauchte_zutaten(
        letzte_tiny_volltexte[:3], gewaehlte_kategorien, kombi_vorlieben)

    # AUSSTATTUNGS-IMPULS (Inventar, /inventar): an manchen Tagen gezielt EIN
    # vorhandenes Spielzeug tragend einbauen. Der Kombi-Impuls hat Vorrang (nie
    # zwei Impulse in einem Prompt), am Wunsch-Privileg-Tag ebenfalls nicht.
    inventar_impuls = None
    if not wunsch_aktiv and not kombi_vorlieben:
        from bot.services import inventar  # lazy wie kategorie_logik-Nachbarn
        inventar_impuls = await inventar.impuls_wahl()

    # D9/DIV4: Interessen pro Lauf subsampeln – das Modell ankerte sonst auf
    # einer einzelnen dominanten Zeile, die fast jeden gemessenen Output als
    # immergleiches Motiv prägte. Max. 6 zufällige Einträge steuern weiter,
    # variieren aber den täglichen Fokus.
    interessen = list(domina_profile.get("interessen", []) or [])
    if len(interessen) > 6:
        interessen = random.sample(interessen, 6)

    # DIV4-Analogon für die Vorlieben (Live-Befund 06.09.): die komplette Liste
    # ankerte jeden Vorschlag auf denselben Top-Einträgen, egal welche
    # Kategorie gewählt war. Max. 8 zufällige
    # Zeilen pro Lauf variieren den Tagesfokus; Kombi-Zeilen bleiben immer drin,
    # Original-Reihenfolge erhalten (Zeilen wörtlich, Richtungs-Zusätze intakt).
    alle_vorlieben = list(sklave_profile.get("vorlieben", []) or [])
    if len(alle_vorlieben) > 8:
        muss = [v for v in (kombi_vorlieben or []) if v in alle_vorlieben]
        rest = [v for v in alle_vorlieben if v not in muss]
        auswahl = set(muss) | set(random.sample(rest, 8 - len(muss)))
        vorlieben_anzeige = [v for v in alle_vorlieben if v in auswahl]
    else:
        vorlieben_anzeige = alle_vorlieben

    return dict(
        erfahrungsstand=domina_profile.get("erfahrungsstand", "Anfänger"),
        level=domina_profile.get("aktuelles_level", 1),
        interessen=interessen,
        sklave_vorlieben=vorlieben_anzeige,
        sklave_hard_limits=sklave_profile.get("hard_limits", []),
        # Kategorie-Dislikes aus Persönlichkeitsprofil (zentrale Logik)
        sklave_dislike_kategorien=kategorie_logik.dislike_kategorien(sklave_profile),
        letzte_aufgaben=letzte_aufgaben,
        letzte_tiny_tasks=letzte_tiny_tasks,
        verbrauchte_anfaenge=verbrauchte_anfaenge,
        verbrauchte_abschluesse=verbrauchte_abschluesse,
        mehrstufig_bremse=mehrstufig_bremse,
        letzte_inspirationen=letzte_inspirationen,
        gewaehlte_kategorien=gewaehlte_kategorien,
        cross_kategorie=cross_kategorie,
        sklave_wunsch_kategorien=sklave_profile.get("wunsch_kategorien", []),
        abgelehnte_tiny_tasks=abgelehnte_tiny_tasks,
        conversation_context=conversation_context,
        stimmung=stimmung,
        bewertungs_kontext=bewertungs_kontext,
        vertrauens_kontext=vertrauens_kontext,
        schwierigkeit=await _schwierigkeit_effektiv(domina_profile, score),
        kategorie_level_hinweis=_kategorie_level_hinweis(sklave_profile, gewaehlte_kategorien),
        # Persönlichkeits-Kontext (Dossier/Reaktionsmuster/Fäden) – ohne den kann
        # der Prompt seine eigene Personalisierungs-Anforderung nicht erfüllen.
        dossier=sklave_profile.get("dossier", ""),
        offene_faeden=sklave_profile.get("offene_faeden", []),
        kategorie_reaktionen=sklave_profile.get("kategorie_reaktionen", {}),
        # Domina-Signal auch fürs LLM sichtbar machen – es steuert sonst nur die
        # Kategorie-Auswahl, ohne dass der Prompt es erklärt (Review D7, B7).
        domina_kategorie_praeferenzen=domina_profile.get("kategorie_praeferenzen", {}),
        verbrauchte_zutaten=verbrauchte_zutaten,
        kombi_vorlieben=kombi_vorlieben,
        inventar_impuls=inventar_impuls,
    )


async def _machbarkeits_maengel(vorschlag: str) -> list[str]:
    """Zweiter Durchlauf (18.09.2026): körperliche Machbarkeit + richtige
    Geräte-Benutzung des fertigen Vorschlags – Details in services/machbarkeit."""
    from bot.services import machbarkeit  # lazy wie die Nachbarn
    return await machbarkeit.maengel(vorschlag, art="aufgabe")


async def _machbarkeit_sichern(vorschlag: str, prompt: str, system: str,
                               sk_hl: list, do_gr: list, lokal: bool = False) -> str:
    """Prüfung + genau EIN neuer Entwurf mit der Mängelliste. Der neue Entwurf
    gewinnt, wenn er nicht mehr Mängel hat als der alte (er kennt sie ja);
    sonst bleibt das Original."""
    maengel = await _machbarkeits_maengel(vorschlag)
    if not maengel:
        return vorschlag
    logger.info("Vorschlag körperlich nicht stimmig (%s) – generiere einmal neu.", "; ".join(maengel))
    retry_prompt = (
        prompt + "\n\nACHTUNG: Dein letzter Entwurf war körperlich nicht stimmig:\n"
        + "\n".join(f"- {m}" for m in maengel)
        + "\n\nDer Entwurf:\n\"\"\"\n" + vorschlag[:1500] + "\n\"\"\"\n"
        "Schreib den Vorschlag neu – die Idee darf bleiben, aber jeder Schritt muss machbar "
        "sein und jedes Gerät so benutzt werden, wie es im Inventar steht. Lieber eine "
        "Handlung weniger als eine verkeilte Szene."
    )
    neu = await limits_check.generate_mit_limit_retry(
        retry_prompt, sk_hl, do_gr, system=system, reasoning=True, lokal=lokal)
    if not neu:
        logger.info("Machbarkeits-Retry leer – Original bleibt.")
        return vorschlag
    if schablonen._formel_verstoesse(neu):
        neu = schablonen._begruendungs_formel_entfernen(neu)
    rest = await _machbarkeits_maengel(neu)
    if len(rest) <= len(maengel):
        if rest:
            logger.info("Auch der Retry hat Mängel (%s) – akzeptiere best-effort.", "; ".join(rest))
        return neu
    logger.info("Machbarkeits-Retry hat mehr Mängel – Original bleibt.")
    return vorschlag


FEST_DATEI = "data/tiny_task_fest.json"


async def _sende_festen_vorschlag(bot: Bot) -> bool:
    import json, os
    if not os.path.exists(FEST_DATEI):
        return False
    try:
        with open(FEST_DATEI, encoding="utf-8") as f:
            fest = json.load(f)
        heute = datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()
        if fest.get("datum") != heute or not (fest.get("vorschlag") or "").strip():
            if (fest.get("datum") or "") < heute:
                os.remove(FEST_DATEI)  # abgelaufen
            return False
        vorschlag = fest["vorschlag"].strip()[:3997]
        await telegram_helper.send_domina(bot, t("TINYTASK_PREFIX_TINY", vorschlag=vorschlag), parse_mode="Markdown")
        await _save_tiny_task(vorschlag, list(fest.get("kategorien") or []))
        os.remove(FEST_DATEI)
        logger.info("Fest vorgegebener Tiny-Task gesendet (%s).", fest.get("kategorien"))
        return True
    except Exception:
        logger.exception("Fester Tiny-Task-Vorschlag fehlgeschlagen – generiere normal.")
        return False


async def _send_tiny_task_vorschlag(bot: Bot) -> None:
    try:
        if basis._flow_aktiv(paare.dom_chat_id(), "Tiny-Task-Vorschlag"):
            return

        # Vor allem anderen: Cleanup verbrauchter/abgelaufener Privilegien
        # (mit bot: unentschiedene Einlösungen werden erstattet + gemeldet)
        await privileg_effekte.cleanup(bot)

        # Privileg 'pause_tag': Skip heute komplett wenn aktiv. Verbrauch erst NACH
        # erfolgreichem Versand der Pause-Meldung (sonst bei Sendefehler verbrannt).
        if await privileg_effekte.hat_pause_tag():
            await telegram_helper.send_domina(bot, t("TINYTASK_PAUSE_TAG"))
            await privileg_effekte.verbrauche_wirkung("skip_next_task")
            logger.info("Tiny-Task übersprungen wegen 'pause_tag'-Privileg.")
            return

        domina_profile = await qdrant.get_user_profile("domina") or {}
        sklave_profile = await qdrant.get_user_profile("sklave") or {}

        # Fest vorgegebener Vorschlag für heute (data/tiny_task_fest.json, einmalig von Hand
        # gelegt, z. B. ein Probe-Vorschlag, der gefiel): wird statt einer Neugenerierung
        # gesendet und danach gelöscht. Gilt nur am eingetragenen Datum.
        if await _sende_festen_vorschlag(bot):
            return

        # Arc-Check zuerst: Eine aktive Storyline übernimmt die Vorschlags-Generierung
        # komplett – die teure Kontext-Vorbereitung (Embedding + ~10 Qdrant-Queries)
        # in _vorschlag_kontext wird dann gar nicht gebraucht.
        from bot.handlers import arc as arc_handler
        arc_tag = await arc_handler.get_aktueller_arc_tag()
        if arc_tag:
            await _sende_arc_vorschlag(bot, arc_tag, domina_profile, sklave_profile)
            return

        # 'naechste_aus_wunsch' nur DETEKTIEREN – Verbrauch erst nach erfolgreichem Send.
        wunsch_aktiv = await privileg_effekte.hat_naechste_aus_wunsch()
        kwargs = await _vorschlag_kontext(domina_profile, sklave_profile, wunsch_aktiv)
        gewaehlte_kategorien = kwargs["gewaehlte_kategorien"]

        wochentag = datetime.now(ZoneInfo(config.TIMEZONE)).weekday()
        # 4=Freitag, 5=Samstag, 6=Sonntag
        ist_wochenende = wochentag in (4, 5, 6)

        if ist_wochenende:
            system, prompt = fp.ausfuehrlicher_task_vorschlag(**kwargs)
            nachricht_key = "TINYTASK_PREFIX_AUSFUEHRLICH"
        else:
            system, prompt = fp.tiny_task_vorschlag(**kwargs)
            nachricht_key = "TINYTASK_PREFIX_TINY"

        # Entdeckte Wünsche als optionalen Kontext beilegen – das LLM webt einen nur
        # ein, wenn er zum heutigen Vorschlag passt (nicht generell).
        from bot.handlers import dossier as _dossier
        _wh = await _dossier.wunsch_kontext_hinweis(sklave_profile.get("entdeckte_wuensche"))
        if _wh:
            prompt += "\n\n" + _wh

        # Kuratiertes Wissen (/lerne) zu den heute gewählten Kategorien beilegen.
        _sb = await coach_persona.skill_kontext_block(gewaehlte_kategorien)
        if _sb:
            prompt += "\n\n" + _sb

        # Limits-Check (Sklave-Hard-Limits + Domina-Grenzen) mit einmaliger Re-Generierung
        sk_hl = sklave_profile.get("hard_limits", []) or []
        do_gr = domina_profile.get("grenzen", []) or []

        # Seit 11.11.2026 laufen ALLE Vorschläge übers Reasoning-Modell (Owner-
        # Entscheid nach Probe mit grok-4.7) – vorher nur der Wochenend-Vorschlag.
        async def _generiere() -> str | None:
            return await limits_check.generate_mit_limit_retry(
                prompt, sk_hl, do_gr, system=system, reasoning=True, lokal=True,
            )

        if ist_wochenende:
            # D9/DIV2: der Reasoning-Pfad konvergiert hart auf ein Rezept
            # (Messung 15.08.: 3/3 identischer Aufbau, Cosine bis 0.886) –
            # zwei Kandidaten generieren und den per Embedding unähnlicheren
            # zu den letzten Vorschlägen nehmen.
            import asyncio as _asyncio
            k1, k2 = await _asyncio.gather(_generiere(), _generiere())
            kandidaten = [k for k in (k1, k2) if k]
            if kandidaten:
                _, _, referenz_texte = await qdrant.get_recent_tiny_tasks(limit=5)
                vorschlag = await schablonen._diversester_kandidat(kandidaten, referenz_texte)
            else:
                vorschlag = None
        else:
            vorschlag = await _generiere()
        if vorschlag is None:
            logger.error("Tiny-Task auch nach Re-Generierung Grenzen-verletzend – verworfen.")
            return

        # D9/DIV1/3/5: Schablonen-Detektor + genau EIN Retry (Muster Anti-Echo
        # im Sklave-Chat) – das reine Prompt-Verbot verlor in ~60 % der Outputs.
        funde = schablonen._formel_verstoesse(vorschlag)
        if funde:
            logger.info("Vorschlag nutzt verbotene Schablonen (%s) – generiere einmal neu.",
                        "; ".join(funde))
            retry_prompt = (
                prompt + "\n\nACHTUNG: Dein letzter Entwurf hat diese VERBOTENEN "
                "Schablonen benutzt: " + "; ".join(funde) + ". Formuliere den Vorschlag "
                "neu – der Inhalt darf bleiben, aber ohne diese Muster: Begründung ohne "
                "'passt…weil'/'kommt genau richtig, weil'/'passt (perfekt) zu …'-Bau und ohne Profil-Abgleich "
                "('holt/trifft genau seine …', '…, den du magst'), "
                "kein Kommentar, wovon du dich absetzt ('ohne dass es wieder …'), "
                "Schluss ohne 'Wie lange…?'-Frage."
            )
            neu = await limits_check.generate_mit_limit_retry(
                retry_prompt, sk_hl, do_gr, system=system, reasoning=True, lokal=True,
            )
            if neu and len(schablonen._formel_verstoesse(neu)) <= len(funde):
                vorschlag = neu
                if schablonen._formel_verstoesse(neu):
                    logger.info("Auch der Retry nutzt Schablonen – akzeptiere best-effort.")
            else:
                logger.info("Retry %s – Original bleibt.",
                            "leer" if not neu else "hat mehr Schablonen")
            geschnitten = schablonen._begruendungs_formel_entfernen(vorschlag)
            if geschnitten != vorschlag:
                logger.info("Begründungs-Formel deterministisch entfernt.")
                vorschlag = geschnitten

        # Körperliche Machbarkeit als zweiter Durchlauf, danach Meta-Rückfragen kappen.
        vorschlag = await _machbarkeit_sichern(vorschlag, prompt, system, sk_hl, do_gr, lokal=True)
        gekappt = schablonen._meta_rueckfrage_entfernen(vorschlag)
        if gekappt != vorschlag:
            logger.info("Meta-Rückfrage am Schluss entfernt.")
            vorschlag = gekappt

        # Kürzen falls zu lang (Telegram Limit 4096, Prefix ~50 Zeichen)
        if len(vorschlag) > 4000:
            vorschlag = vorschlag[:3997] + "..."

        if basis._nach_llm_verworfen(paare.dom_chat_id(), "Tiny-Task-Vorschlag"):
            return
        await telegram_helper.send_domina(bot, t(nachricht_key, vorschlag=vorschlag), parse_mode="Markdown")
        await _save_tiny_task(vorschlag, gewaehlte_kategorien)
        # Privileg jetzt erst verbrauchen – der Vorschlag ist raus.
        if wunsch_aktiv and sklave_profile.get("wunsch_kategorien"):
            await privileg_effekte.verbrauche_wirkung("naechste_aus_wunsch")
        logger.info("Task Vorschlag gesendet (%s, Kategorien: %s).", "ausführlich" if ist_wochenende else "tiny", gewaehlte_kategorien)
    except Exception as e:
        logger.exception("Fehler beim Task Vorschlag")


@basis._job_guard
@basis._nachholbar(max_stunden=5)
async def tiny_task_vorschlag_job(bot: Bot) -> None:
    """Abendlicher Tiny-Task-Vorschlag an die Domina."""
    logger.info("Tiny-Task-Vorschlag-Job gestartet.")
    await _send_tiny_task_vorschlag(bot)


@basis._job_guard
async def tiny_task_feedback_job(bot: Bot) -> None:
    """Abends – fragt die Domina nach dem Grund wenn heutige Tiny-Task Vorschläge nicht übernommen wurden."""
    try:
        pending = await qdrant.get_pending_tiny_tasks_for_feedback(hours_back=24)
        if not pending:
            return

        # Nur die neueste – sonst spammt der Bot
        neuester = pending[0]

        # Skip falls Domina gerade in einem anderen Flow ist
        from bot.handlers import tiny_task_feedback
        if basis._flow_aktiv(paare.dom_chat_id(), "Tiny-Task-Feedback-Frage"):
            return

        await tiny_task_feedback.frage_stellen(bot, neuester)
        logger.info("Tiny-Task-Feedback-Frage gesendet (point_id=%s)", neuester.get("qdrant_point_id"))
    except Exception as e:
        logger.exception("Fehler beim Tiny-Task-Feedback-Job")
