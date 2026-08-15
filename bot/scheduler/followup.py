"""
APScheduler Jobs – Follow-up, Tiny Task, Stimmung, Ziel-Erinnerung, Training.
"""
import functools
import logging
import random
import re
import uuid
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from telegram import Bot
from qdrant_client import models as qm
from bot.services.qdrant import client

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, telegram_helper, embeddings, privileg_effekte, limits_check, kategorie_logik, labels, zeiten
from bot.services import sticker_reaktionen
from bot.prompts import followup as fp
from bot.prompts import coach_persona
from bot.prompts import domina_coach
from bot.messages import t

logger = logging.getLogger(__name__)


def _zweiwochen_takt() -> bool:
    """True in geraden ISO-Wochen – deterministischer 2-Wochen-Takt für die
    cron-registrierten Jobs (lernkurve/coach_reflexion/profil_pflege).
    Ersetzt IntervalTrigger(weeks=2): der verlor bei jedem Deploy seine Phase
    (Anker = nächstes Wochentags-Vorkommen → bei häufigen Deploys faktisch
    wöchentlich) und drifte nach jeder DST-Umstellung um eine Stunde."""
    return datetime.now(ZoneInfo(config.TIMEZONE)).isocalendar().week % 2 == 0


def _job_guard(fn):
    """Scheduler-Job absichern: (1) bei aktiver Safeword-Pause gar nicht laufen
    (kein automatischer Versand, solange pausiert), (2) Qdrant-/LLM-Fehler dürfen
    den Job nicht ungebremst durchschlagen lassen (Traceback-Log).
    Eine Abwesenheit (/abwesend) pausiert BEWUSST nichts – alle Jobs laufen
    weiter, der Zeitraum fließt nur als Fakt in die Generier-Prompts ein
    (abwesenheit.prompt_hinweis in limits_check/Chat-Prompts)."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        if state.is_paused():
            logger.info("Scheduler-Job '%s' übersprungen – System per Safeword pausiert", fn.__name__)
            return
        try:
            return await fn(*args, **kwargs)
        except Exception:
            logger.exception("Scheduler-Job '%s' fehlgeschlagen", fn.__name__)
    return wrapper


def _job_fangnetz(fn):
    """Nur Fehler-Fangnetz OHNE Pause-Check – für Jobs, die auch während einer
    Safeword-Pause weiterlaufen sollen (Backups: eine tagelange Pause darf
    keine Backup-Lücke reißen; kein User-facing Versand)."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except Exception:
            logger.exception("Scheduler-Job '%s' fehlgeschlagen", fn.__name__)
    return wrapper


def _parse_datum(val: str) -> datetime:
    """Parse ISO datetime string, handling timezone-naive and aware formats."""
    if not val:
        return datetime(2000, 1, 1, tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return datetime(2000, 1, 1, tzinfo=timezone.utc)


async def _arc_tag_anpassen(aufgabe: str, stimmung: str, sklave_profile: dict, domina_profile: dict) -> str | None:
    """Passt einen vorgenerierten Arc-Tag ans letzte (negative) Gefühl an.
    Gibt den angepassten Text zurück oder None (dann Original verwenden)."""
    try:
        prompt = fp.kette_anpassung(aufgabe, f"Storyline – sein letztes Gefühl war: {stimmung}", stimmung)
        angepasst = grok.clean_text(await grok.simple(prompt))
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


def _flow_aktiv(chat_id: str, job_name: str) -> bool:
    """Gemeinsamer Guard vor Scheduler-Sends: nicht in einen aktiven UI-Flow
    hineinplatzen. Räumt vorher liegengebliebene (stale) Flows auf, damit ein
    vergessener Dialog die Jobs nicht dauerhaft blockiert."""
    if state.is_paused():
        logger.info("%s übersprungen – System per Safeword pausiert", job_name)
        return True
    state.clear_if_stale(chat_id)
    mode = state.get_mode(chat_id)
    if mode not in ("chat", None):
        logger.info("%s übersprungen – Chat in Mode '%s'", job_name, mode)
        return True
    return False


def _nach_llm_verworfen(chat_id: str, job_name: str) -> bool:
    """Re-Check NACH einem LLM-Await (TOCTOU): im Generierungs-Fenster kann ein
    Safeword oder ein neuer UI-Flow gekommen sein – dann nicht mehr senden.
    Gegenstück zu _flow_aktiv (das VOR der Generierung prüft); Muster wie in
    followup_job/serie (Trace 06.07., Kleinkram)."""
    if state.is_paused() or state.get_mode(chat_id) not in ("chat", None):
        logger.info("%s nach Generierung verworfen – Pause/Mode im LLM-Fenster geändert.", job_name)
        return True
    return False


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
        titel=titel, kategorie=kat, aufgabe=aufgabe, hinweis=angepasst_hinweis,
    )
    if _nach_llm_verworfen(paare.dom_chat_id(), "Arc-Tag-Vorschlag"):
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
    basis = domina_profile.get("aufgaben_schwierigkeit", "normal")
    # Reihenfolge wichtig: die NIEDRIG-Schwelle gilt für JEDE Basis – stünde der
    # hoch→normal-Zweig zuerst, käme Basis "hoch" bei Score < 25 nie auf "niedrig".
    if score < config.VERTRAUEN_SCHWELLE_NIEDRIG:
        effektiv = "niedrig"
    elif score < config.VERTRAUEN_SCHWELLE_SENKEN and basis == "hoch":
        effektiv = "normal"
    else:
        effektiv = basis

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


# Mehrstufige Programm-Struktur (Phase 1/Stufe 1/Schritt 1 …) in Vorschlags-Texten.
_STUFEN_RE = re.compile(r"(phase|stufe|schritt)\s*[123]|(drei|zwei) (phasen|stufen|schritten)", re.I)


def _vorschlag_anfang(inhalt: str) -> str:
    """Erster Satz eines Vorschlags (max. 90 Zeichen) – für die
    VERBRAUCHTE-ANFÄNGE-Sperrliste im Generator-Prompt."""
    saetze = re.split(r"(?<=[.!?:])\s+", (inhalt or "").strip())
    return saetze[0][:90] if saetze and saetze[0] else ""


def _vorschlag_abschluss(inhalt: str) -> str:
    """Letzter Satz eines Vorschlags (max. 90 Zeichen) – für die
    VERBRAUCHTE-ABSCHLÜSSE-Sperrliste (D9/DIV3): Abschluss-Fragen recyceln
    genauso wie Opener („Wie lange willst du …?" in 4 von 5 Folgetagen)."""
    saetze = [s_ for s_ in re.split(r"(?<=[.!?])\s+", (inhalt or "").strip()) if s_]
    return saetze[-1][:90] if saetze else ""


# Deterministischer Schablonen-Detektor (D9/DIV1/3/5): die Prompt-Verbote in
# _formel_verbot verlieren gegen die Modell-Präferenz (Diversitäts-Messung
# 15.08.: „Das passt …, weil" trotz Verbot in ~60 % der Outputs, „Wie wär's"
# rutschte am 5. Folgetag wieder durch). Bekanntes Lernmuster: Prompt-Regeln
# brauchen einen Detektor + Retry (wie _ist_echo/_ist_spiegel_anfang im Chat).
_PASST_WEIL_RE = re.compile(r"\bpasst\b[^.!?\n]{0,60}\bweil\b", re.IGNORECASE)
_WIE_WAERS_RE = re.compile(r"^\W{0,8}wie\s+w[äa]r['’`]?s\b", re.IGNORECASE)
# Ohne ?-Fenster (Nachtest 15.08.: lange Varianten wie „…, bevor du
# entscheidest, ob's reicht …?" lagen außerhalb des 80-Zeichen-Fensters).
# Die Frage an die Domina ist praktisch immer der Schluss-Satz – ein seltener
# False Positive kostet nur einen harmlosen Retry.
_WIE_LANGE_RE = re.compile(
    r"\bwie\s+lange\s+(willst|magst|l[äa]sst)\s+du\b", re.IGNORECASE)


def _formel_verstoesse(text: str) -> list[str]:
    funde = []
    if _PASST_WEIL_RE.search(text or ""):
        funde.append('Begründungs-Formel „passt …, weil"')
    if _WIE_WAERS_RE.search(text or ""):
        funde.append('Einstieg „Wie wär\'s …"')
    if _WIE_LANGE_RE.search(text or ""):
        funde.append('Abschluss-Frage „Wie lange willst du …?"')
    return funde


def _cos(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


async def _diversester_kandidat(kandidaten: list[str], referenzen: list[str]) -> str:
    """Wählt aus mehreren Kandidaten den mit der geringsten maximalen
    Embedding-Ähnlichkeit zu den letzten Vorschlägen (D9/DIV2): der
    Reasoning-Pfad konvergiert sonst hart auf ein Rezept (Messung 15.08.:
    3/3 Wochenend-Vorschläge fast identisch, Cosine bis 0.886)."""
    kandidaten = [k for k in kandidaten if k]
    if len(kandidaten) <= 1 or not referenzen:
        return kandidaten[0] if kandidaten else ""
    try:
        ref_vecs = [await embeddings.get_embedding(r) for r in referenzen[:5]]
        best, best_score = kandidaten[0], None
        for k in kandidaten:
            v = await embeddings.get_embedding(k)
            score = max((_cos(v, rv) for rv in ref_vecs), default=0.0)
            if best_score is None or score < best_score:
                best, best_score = k, score
        logger.info("Diversitäts-Wahl: %d Kandidaten, gewählte max-Ähnlichkeit %.3f.",
                    len(kandidaten), best_score or 0.0)
        return best
    except Exception:
        logger.exception("Kandidaten-Wahl fehlgeschlagen – nehme ersten.")
        return kandidaten[0]


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
        a for a in (_vorschlag_anfang(v) for v in letzte_tiny_volltexte[:4]) if a
    ))
    # D9/DIV3: Abschluss-Sätze genauso sperren wie Opener.
    verbrauchte_abschluesse = list(dict.fromkeys(
        a for a in (_vorschlag_abschluss(v) for v in letzte_tiny_volltexte[:4]) if a
    ))
    mehrstufig_bremse = sum(
        1 for v in letzte_tiny_volltexte[:3] if _STUFEN_RE.search(v)
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

    # D9/DIV4: Interessen pro Lauf subsampeln – das Modell ankerte sonst auf
    # einer einzelnen dominanten Zeile („Mag es wenn er jammert …" prägte 11
    # von 14 gemessenen Outputs als immergleiches Motiv). Max. 6 zufällige
    # Einträge steuern weiter, variieren aber den täglichen Fokus.
    interessen = list(domina_profile.get("interessen", []) or [])
    if len(interessen) > 6:
        interessen = random.sample(interessen, 6)

    return dict(
        erfahrungsstand=domina_profile.get("erfahrungsstand", "Anfänger"),
        level=domina_profile.get("aktuelles_level", 1),
        interessen=interessen,
        sklave_vorlieben=sklave_profile.get("vorlieben", []),
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
    )


async def _send_tiny_task_vorschlag(bot: Bot) -> None:
    try:
        if _flow_aktiv(paare.dom_chat_id(), "Tiny-Task-Vorschlag"):
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

        async def _generiere() -> str | None:
            return await limits_check.generate_mit_limit_retry(
                prompt, sk_hl, do_gr, system=system, reasoning=ist_wochenende,
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
                vorschlag = await _diversester_kandidat(kandidaten, referenz_texte)
            else:
                vorschlag = None
        else:
            vorschlag = await _generiere()
        if vorschlag is None:
            logger.error("Tiny-Task auch nach Re-Generierung Grenzen-verletzend – verworfen.")
            return

        # D9/DIV1/3/5: Schablonen-Detektor + genau EIN Retry (Muster Anti-Echo
        # im Sklave-Chat) – das reine Prompt-Verbot verlor in ~60 % der Outputs.
        funde = _formel_verstoesse(vorschlag)
        if funde:
            logger.info("Vorschlag nutzt verbotene Schablonen (%s) – generiere einmal neu.",
                        "; ".join(funde))
            retry_prompt = (
                prompt + "\n\nACHTUNG: Dein letzter Entwurf hat diese VERBOTENEN "
                "Schablonen benutzt: " + "; ".join(funde) + ". Formuliere den Vorschlag "
                "neu – der Inhalt darf bleiben, aber Einstieg, Begründung und Schluss "
                "müssen ohne diese Muster auskommen (Begründung ohne 'passt…weil'-Bau, "
                "Schluss ohne 'Wie lange…?'-Frage)."
            )
            neu = await limits_check.generate_mit_limit_retry(
                retry_prompt, sk_hl, do_gr, system=system, reasoning=ist_wochenende,
            )
            if neu and len(_formel_verstoesse(neu)) <= len(funde):
                vorschlag = neu
                if _formel_verstoesse(neu):
                    logger.info("Auch der Retry nutzt Schablonen – akzeptiere best-effort.")

        # Kürzen falls zu lang (Telegram Limit 4096, Prefix ~50 Zeichen)
        if len(vorschlag) > 4000:
            vorschlag = vorschlag[:3997] + "..."

        if _nach_llm_verworfen(paare.dom_chat_id(), "Tiny-Task-Vorschlag"):
            return
        await telegram_helper.send_domina(bot, t(nachricht_key, vorschlag=vorschlag), parse_mode="Markdown")
        await _save_tiny_task(vorschlag, gewaehlte_kategorien)
        # Privileg jetzt erst verbrauchen – der Vorschlag ist raus.
        if wunsch_aktiv and sklave_profile.get("wunsch_kategorien"):
            await privileg_effekte.verbrauche_wirkung("naechste_aus_wunsch")
        logger.info("Task Vorschlag gesendet (%s, Kategorien: %s).", "ausführlich" if ist_wochenende else "tiny", gewaehlte_kategorien)
    except Exception as e:
        logger.exception("Fehler beim Task Vorschlag")


async def _letzte_domina_aktivitaet() -> datetime | None:
    """Jüngster Zeitpunkt aus: neueste erteilte Aufgabe + neueste Domina-Conversation.
    Basis der Lücken-Erkennung (kein Treffer = nie etwas → None)."""
    stempel = []
    # Inkl. Zwischenzustände gefragt/gefuehl_pending (Review D8/M9): ein gestern
    # erteilter Task, der gerade auf die Sklaven-Antwort wartet, ist Aktivität –
    # ohne ihn kam der Lücken-Vorschlag trotz frischer Aufgabe.
    tasks = await qdrant.get_tasks_by_status(
        ["offen", "gefragt", "gefuehl_pending", "erledigt", "nicht_erledigt",
         "serie_wartend", "kette_wartend", "pausiert", "geplant"],
        limit=5, sort_by_datum=True,
    )
    # Auto-Content zählt NICHT als Domina-Aktivität (D9/N11, Owner-Entscheid
    # 15.08.): Blitz-/Advent-Tasks erteilt der Bot selbst – bei aktivem Blitz
    # oder Adventskalender käme der Lücken-Vorschlag sonst nie, obwohl die
    # Domina längst ausgestiegen ist. limit=5, damit nach dem Filter noch ein
    # echter Kandidat übrig ist.
    tasks = [t_ for t_ in tasks if t_.get("quelle") not in ("blitz", "advent")]
    if tasks:
        stempel.append(_parse_datum(tasks[0].get("erteilt_am", "")))
    jetzt = datetime.now(timezone.utc)
    conv = await qdrant.get_conversations_in_range(
        "domina", (jetzt - timedelta(days=30)).isoformat(), jetzt.isoformat(), limit=1,
    )
    if conv:
        stempel.append(_parse_datum(conv[-1].get("datum", "")))
    return max(stempel) if stempel else None


@_job_guard
async def luecken_check_job(bot: Bot) -> None:
    """Schlägt der Domina nach LUECKEN_INTERVALL_TAGE Tagen ohne Aufgaben-/Szenen-
    Aktivität EINEN Task-Vorschlag vor – nur bei Opt-in (sie gibt jeden frei)."""
    domina_profile = await qdrant.get_user_profile("domina") or {}
    if not domina_profile.get("luecken_vorschlag_aktiv", False):
        return
    if _flow_aktiv(paare.dom_chat_id(), "Lücken-Check"):
        return

    intervall = timedelta(days=config.LUECKEN_INTERVALL_TAGE)
    jetzt = datetime.now(timezone.utc)

    # Throttle: nach einem Vorschlag erst nach dem Intervall erneut fragen (kein
    # tägliches Drängeln, auch wenn sie den Vorschlag ignoriert).
    # 1h-Toleranz (D9/N9): die Throttle-Marke wird Sekunden NACH der Cron-Zeit
    # geschrieben – ohne Toleranz war "jetzt − Marke" am Tag +INTERVALL immer
    # knapp unter dem Intervall und der Vorschlag kam systematisch einen Tag
    # später (Millisekunden-Kanten-Klasse, wie der Ketten-Sweep 30.07.).
    letzter_vorschlag = _parse_datum(domina_profile.get("luecke_letzter_vorschlag_am", ""))
    if jetzt - letzter_vorschlag < intervall - timedelta(hours=1):
        return

    letzte_aktivitaet = await _letzte_domina_aktivitaet()
    if letzte_aktivitaet and jetzt - letzte_aktivitaet < intervall - timedelta(hours=1):
        return

    tage = (jetzt - letzte_aktivitaet).days if letzte_aktivitaet else config.LUECKEN_INTERVALL_TAGE
    from bot.handlers import luecke  # lazy: zirkulären Import vermeiden
    await luecke.sende_vorschlag(bot, max(tage, config.LUECKEN_INTERVALL_TAGE))


@_job_guard
async def luecken_zustellung_job(bot: Bot) -> None:
    """Stellt geplante Aufgaben (status='geplant') zu, sobald ihr `zustellung_ab`
    erreicht ist: Lücken-'heute Abend' UND Termin-Aufgaben ("Aufgabe für Tag X",
    quelle='termin'). Restart-sicher (Qdrant-persistiert)."""
    geplant = await qdrant.get_tasks_by_status(["geplant"], limit=20)
    if not geplant:
        return
    # Nicht in einen aktiven Sklaven-Flow platzen – nächster Lauf (Intervall) holt's nach.
    if _flow_aktiv(paare.sub_chat_id(), "Lücken-Zustellung"):
        return

    jetzt = datetime.now(timezone.utc)
    for task in geplant:
        if _parse_datum(task.get("zustellung_ab", "")) > jetzt:
            continue
        point_id = task.get("qdrant_point_id")
        if not point_id:
            continue
        try:
            anweisung = await grok.simple(fp.aufgabe_an_sklaven(task.get("aufgabe", "")), max_tokens=250)
        except Exception:
            logger.exception("aufgabe_an_sklaven (Zustellung) fehlgeschlagen – Rohtext")
            anweisung = task.get("aufgabe", "")
        # Re-Check NACH dem LLM-Await (Review D8/M8, TOCTOU wie _process_serie_tasks):
        # ein im LLM-Fenster gesetztes Safeword/ein gestarteter Flow darf die
        # Aufgabe nicht mehr erhalten – nächster 15-Min-Lauf holt sie nach.
        if state.is_paused() or state.get_mode(paare.sub_chat_id()) not in ("chat", None):
            logger.info("Zustellung nach Generierung verworfen – Pause/Mode im LLM-Fenster geändert.")
            break
        # Erst Status flippen (verhindert Doppel-Zustellung beim nächsten Lauf), dann
        # senden – mit Rollback bei Sendefehler, sonst gilt eine nie zugestellte
        # Aufgabe als offen und das Followup fragt danach (Trace 06.07., Lücke 5).
        # Kein set_followup_task – Followup läuft später über follow_up_datum (wie /wuerfel).
        await qdrant.update_task(point_id, {"status": "offen"})
        try:
            await telegram_helper.send_sklave(bot, anweisung, voice_text=anweisung)
        except Exception:
            await qdrant.update_task(point_id, {"status": "geplant"})
            raise
        logger.info("Geplante Aufgabe zugestellt (quelle=%s, point_id=%s).",
                    task.get("quelle", "?"), point_id)


@_job_guard
async def event_check_job(bot: Bot) -> None:
    """Startet geplante Event-Arcs 🎂, deren Startfenster erreicht ist
    (Finale = Event-Tag; Logik in handlers/event_arc.starte_faellige)."""
    from bot.handlers import event_arc  # lazy: zirkulären Import vermeiden
    await event_arc.starte_faellige(bot)


@_job_guard
async def dauer_job(bot: Bot) -> None:
    """Dauer-Anweisungen 🕰: Enden nachfragen (→ normaler Followup-Flow) +
    zufällige Zwischen-Checks (Logik in handlers/dauer.pruefe_laufende)."""
    from bot.handlers import dauer  # lazy: zirkulären Import vermeiden
    await dauer.pruefe_laufende(bot)


@_job_guard
async def kalender_job(bot: Bot) -> None:
    """Adventskalender 🎄: öffnet das heutige Türchen (handlers/advent)."""
    from bot.handlers import advent  # lazy: zirkulären Import vermeiden
    await advent.oeffne_tuerchen(bot)


@_job_guard
async def blitz_check_job(bot: Bot) -> None:
    """Entscheidet zufällig, ob JETZT eine Blitzaufgabe ⚡ kommt (Opt-in via
    /blitz). Fenster: BLITZ_FENSTER ∩ kinderfreie Zeiten; Throttle über
    BLITZ_MIN_ABSTAND_TAGE; nie eine zweite offene Blitzaufgabe."""
    domina_profile = await qdrant.get_user_profile("domina") or {}
    if not domina_profile.get("blitz_aktiv", False):
        return
    if _flow_aktiv(paare.sub_chat_id(), "Blitz-Check"):
        return

    jetzt_lokal = datetime.now(ZoneInfo(config.TIMEZONE))
    if not zeiten.ist_im_fenster(jetzt_lokal, [config.BLITZ_FENSTER]):
        return
    # kinderfreie_zeiten = Fenster in denen sie UNGESTÖRT ist (leer = immer).
    if not zeiten.ist_im_fenster(jetzt_lokal, domina_profile.get("kinderfreie_zeiten", []) or []):
        return

    jetzt = datetime.now(timezone.utc)
    letzte = _parse_datum(domina_profile.get("blitz_letzte_am", ""))
    if jetzt - letzte < timedelta(days=config.BLITZ_MIN_ABSTAND_TAGE):
        return

    # Serverseitig im quelle-Index zählen statt 50 Voll-Payloads laden (D8/N5).
    if await qdrant.count_tasks_by_status(["offen"], quelle="blitz") > 0:
        return

    if random.random() >= config.BLITZ_CHANCE:
        return

    from bot.handlers import blitz  # lazy: zirkulären Import vermeiden
    await blitz.sende_blitz(bot)


@_job_guard
async def blitz_ablauf_job(bot: Bot) -> None:
    """Markiert Blitzaufgaben mit abgelaufenem Countdown als verpasst –
    restart-sicher (Deadline liegt Qdrant-persistiert am Task).

    Läuft alle 5 Min (288×/Tag/Paar) – darum billiger count als Vorprüfung
    und der quelle-Filter serverseitig im Index statt in Python (D8/N5)."""
    if await qdrant.count_tasks_by_status(["offen"], quelle="blitz") == 0:
        return
    offene = await qdrant.get_tasks_by_status(["offen"], limit=50, quelle="blitz")
    jetzt_iso = datetime.now(timezone.utc).isoformat()
    from bot.handlers import blitz  # lazy
    for task in offene:
        deadline = task.get("blitz_deadline", "")
        if deadline and jetzt_iso > deadline:
            try:
                await blitz.markiere_verpasst(bot, task)
            except Exception:
                logger.exception("Blitz-Ablauf-Markierung fehlgeschlagen (point_id=%s)",
                                 task.get("qdrant_point_id"))


async def _process_serie_tasks(bot: Bot) -> None:
    """Aktiviert fällige Serie-Tasks."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        results, _ = await qdrant.run_io(client.scroll, 
            collection_name="tasks",
            scroll_filter=qm.Filter(must=[
                # Mandanten-Filter: läuft pro Paar (followup_job) – ohne ihn
                # würde Paar 1 die Serie-Tasks ALLER Paare aktivieren und in
                # seinem Kontext zustellen (Trace 06.07., Lücke 10).
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key("sklave"))),
                qm.FieldCondition(key="status", match=qm.MatchValue(value="serie_wartend")),
                qm.FieldCondition(key="follow_up_datum", range=qm.DatetimeRange(lte=now_iso)),
            ]),
            # Aufsteigend nach Fälligkeit (Review D8/M11): sind nach Downtime
            # mehrere Serien-Tage fällig, wird sonst pro Lauf ein willkürlicher
            # aktiviert – Tag 3 könnte vor Tag 2 kommen, obwohl Serien als
            # aufbauender Bogen generiert werden.
            order_by=qm.OrderBy(key="follow_up_datum", direction="asc"),
            limit=50, with_payload=True, with_vectors=False,
        )
        tasks = [r.payload for r in results]

        for task in tasks:
            point_id = task.get("qdrant_point_id")
            aufgabe = task.get("aufgabe", "")
            tag = task.get("serie_tag", "?")
            gesamt = task.get("serie_gesamt", "?")

            # Mode-Check: nicht senden wenn Sklave in aktivem Flow
            sklave_mode = state.get_mode(paare.sub_chat_id())
            if sklave_mode not in ("chat", None):
                logger.info("Serie Task übersprungen – Sklave in Mode '%s'", sklave_mode)
                break

            anweisung = await grok.simple(fp.aufgabe_an_sklaven(aufgabe), max_tokens=250)
            # Re-Check NACH dem LLM-Await (TOCTOU): Pause/Flow kann sich im
            # Fenster geändert haben.
            if state.is_paused() or state.get_mode(paare.sub_chat_id()) not in ("chat", None):
                logger.info("Serie Task nach Generierung verworfen – Pause/Mode im LLM-Fenster geändert.")
                break
            await telegram_helper.send_sklave(bot, anweisung, voice_text=anweisung)
            # Status/State erst NACH erfolgreichem Senden – sonst landet der Sklave
            # im Followup-Mode für eine Aufgabe, die er (bei Sende-/LLM-Fehler) nie erhält.
            sklave_chat = paare.sub_chat_id()
            await qdrant.update_task(point_id, {"status": "offen"})
            # Returnwert beachten (Review D6): schlägt set_followup_task fehl
            # (Mode-Wechsel im Sende-Fenster), zeigt der In-Memory-State nicht
            # auf den Task und die Antwort würde als Freitext geroutet –
            # restore_state beim nächsten Start bzw. followup_job (status offen +
            # follow_up_datum) holen die Frage dann nach.
            if not state.set_followup_task(sklave_chat, point_id):
                logger.warning("Serie Task %s gesendet, aber Followup-State blockiert – "
                               "Nachfrage kommt über den regulären Followup-Zyklus.", point_id)
            logger.info("Serie Task aktiviert: Tag %s/%s", tag, gesamt)
    except Exception as e:
        logger.exception("Fehler bei Serie Tasks")


# Hängende Ketten: so lange wartet der Sweep ab der ersten Beobachtung (bzw.
# zwischen zwei Nachfragen), bevor er die Domina (erneut) fragt. Puffer, damit
# frische Weiter/Abbruch- und Anpassungs-Entscheidungen nicht überfahren werden.
_KETTE_SWEEP_WARTE_TAGE = 2

# Glied-Status, bei denen die Kette normal weiterläuft (kein Eingriff nötig).
_KETTE_AKTIVE_STATUS = ("offen", "gefragt", "gefuehl_pending")


async def _process_kette_tasks(bot: Bot) -> None:
    """Fangnetz für hängende Ketten (Review D8/H3): der Ketten-Fortschritt ist
    rein event-getrieben (Erledigt-Pfad, Fehlschlag-Buttons) – wird ein Glied
    gelöscht oder eine Entscheidungsfrage nie beantwortet, wartete der Rest
    vorher für immer auf `kette_wartend` (Live-Fall: Kette hing seit 17.05.).

    Vorgehen konservativ („nichts wird still angewendet"): eine Kette, deren
    niedrigstes wartendes Glied nur noch terminale Vorgänger hat, wird erst
    per `kette_sweep_am` beobachtet; besteht der Zustand nach
    _KETTE_SWEEP_WARTE_TAGE weiter, bekommt die Domina die bestehende
    Weiter/Abbruch-Buttonfrage (kettefail-Callback, inkl. Doppel-Tap-Guard)."""
    try:
        wartende = await qdrant.get_tasks_by_status(["kette_wartend"])
        ketten: dict[str, dict] = {}
        for task in wartende:
            kid = task.get("kette_id")
            if not kid:
                continue
            bisher = ketten.get(kid)
            if bisher is None or task.get("kette_position", 0) < bisher.get("kette_position", 0):
                ketten[kid] = task

        now = datetime.now(timezone.utc)
        from bot.handlers import kette_adaptiv  # lazy (Handler-Import im Scheduler)
        for kid, glied in ketten.items():
            alle = await qdrant.get_gruppen_glieder("kette_id", kid)
            pos = glied.get("kette_position", 0)
            vorgaenger_aktiv = any(
                g.get("kette_position", 0) < pos and g.get("status") in _KETTE_AKTIVE_STATUS
                for g in alle
            )
            if vorgaenger_aktiv:
                continue  # Kette läuft normal – Erledigt-/Fehlschlag-Pfad übernimmt

            gid = glied.get("qdrant_point_id")
            if not gid:
                continue
            sweep_am = glied.get("kette_sweep_am")
            if sweep_am:
                sweep_dt = _parse_datum(sweep_am)
                if now - sweep_dt < timedelta(days=_KETTE_SWEEP_WARTE_TAGE):
                    continue  # Beobachtungs-/Entscheidungsfenster läuft noch

            if not sweep_am:
                # Erst nur beobachten – die Domina kann gerade mitten in einer
                # frischen Button-Entscheidung stecken.
                await qdrant.update_task(gid, {"kette_sweep_am": now.isoformat()})
                continue

            # Nicht in einen aktiven Domina-Flow platzen (D9/N10): Frage einfach
            # beim nächsten Lauf stellen – sweep_am bleibt stehen, es geht
            # nichts verloren (kettefail-Buttons sind ohnehin status-geguardet).
            if state.get_mode(paare.dom_chat_id()) not in ("chat", None):
                logger.info("KETTE_HAENGT_FRAGE vertagt – Domina in aktivem Flow.")
                continue

            await telegram_helper.send_domina(
                bot,
                t("KETTE_HAENGT_FRAGE",
                  pos=glied.get("kette_position", "?"),
                  gesamt=glied.get("kette_gesamt", "?"),
                  naechste=glied.get("aufgabe", "")[:200]),
                parse_mode="Markdown",
                reply_markup=kette_adaptiv._fehlschlag_buttons(gid),
            )
            # Throttle: erst nach erneuter Wartezeit wieder nachfragen.
            await qdrant.update_task(gid, {"kette_sweep_am": now.isoformat()})
            logger.info("Kette %s hängt (Glied %s wartet, keine aktiven Vorgänger) – "
                        "Weiter/Abbruch-Frage an Domina gesendet.", kid, gid)
    except Exception:
        logger.exception("Fehler beim Ketten-Fangnetz")


@_job_guard
async def followup_job(bot: Bot) -> None:
    logger.info("Follow-up Job gestartet: %s", datetime.now(timezone.utc).isoformat())

    # Liegengebliebenen UI-Flow auto-zurücksetzen, sonst blockiert er den ganzen Job (eval-6)
    state.clear_if_stale(paare.sub_chat_id())

    # Serie Tasks aktivieren
    await _process_serie_tasks(bot)

    # Hängende Ketten einsammeln (Review D8/H3)
    await _process_kette_tasks(bot)

    # Follow-up an Sklave – Existenz-Check zuerst, der Kontext (Streak/Stimmung/
    # nicht_erledigt) wird nur geladen, wenn es überhaupt offene Followups gibt.
    tasks = await qdrant.get_open_followup_tasks()
    if not tasks:
        logger.info("Keine offenen Follow-up Tasks.")
    else:
        sklave_profil = await qdrant.get_user_profile("sklave") or {}
        streak = sklave_profil.get("streak", 0)
        sieben_tage_ago = datetime.now(timezone.utc) - timedelta(days=7)
        nicht_erledigt_tasks = await qdrant.get_tasks_by_status(["nicht_erledigt"])
        letzte_nicht_erledigt = sum(
            1 for t in nicht_erledigt_tasks
            if _parse_datum(t.get("follow_up_datum", "")) >= sieben_tage_ago
        )
        aktuelle_stimmung = _stimmung_aktuell(await qdrant.get_latest_stimmung("sklave"))

        sklave_chat = paare.sub_chat_id()
        # BEWUSST nur EINE automatische Followup-Frage pro Tag: nach dem ersten Send
        # setzt set_followup_task den Mode auf "followup", wodurch der Mode-Check unten
        # die Schleife abbricht. Das hält den Abend ruhig (kein Verhör). Weitere offene
        # Aufgaben kann der Sklave selbst über /meineaufgaben (meine_aufgaben.py) ansehen
        # und abschließen – jeweils mit derselben Followup-Frage.
        for task in tasks:
            point_id = task.get("qdrant_point_id")
            aufgabe = task.get("aufgabe", "")
            if not point_id or not aufgabe:
                continue
            # Mode-Check: nicht senden wenn Sklave in aktivem Flow (bricht nach der
            # ersten gestellten Frage ab – siehe Kommentar oben).
            sklave_mode = state.get_mode(sklave_chat)
            if sklave_mode not in ("chat", None):
                logger.info("Follow-up übersprungen – Sklave in Mode '%s'", sklave_mode)
                break
            try:
                # Zeitbezug: an welchem Tag war die Aufgabe gedacht? (Followup kommt meist
                # Folgetag.) Kalendertage in Bot-Zeitzone – UTC-Tage würden eine nach
                # Mitternacht erteilte Aufgabe fälschlich als "gestern" labeln.
                tz = ZoneInfo(config.TIMEZONE)
                erteilt = _parse_datum(task.get("erteilt_am", ""))
                _tage = (datetime.now(tz).date() - erteilt.astimezone(tz).date()).days
                tage_her = _tage if 0 <= _tage <= 30 else 1  # unplausibel (z.B. leeres Datum) -> Default 1
                prompt = fp.followup_frage(
                    aufgabe, streak=streak, letzte_nicht_erledigt=letzte_nicht_erledigt,
                    stimmung=aktuelle_stimmung, tage_her=tage_her,
                )
                frage = await grok.simple(prompt, max_tokens=250)
                # Re-Check NACH dem LLM-Await (TOCTOU): im Fenster kann der Sklave
                # einen Flow begonnen oder ein Safeword gesendet haben.
                if state.is_paused() or state.get_mode(sklave_chat) not in ("chat", None):
                    logger.info("Follow-up nach Generierung verworfen – Pause/Mode im LLM-Fenster geändert.")
                    break
                from bot.handlers import followup_response
                # „Ich sehe alles"-Sticker gelegentlich vor der Kontroll-Frage
                await sticker_reaktionen.sende_sklave(bot, sticker_reaktionen.AUGE, chance=0.35)
                await bot.send_message(
                    chat_id=sklave_chat, text=frage,
                    reply_markup=followup_response.frage_buttons(point_id),
                )
                # Status/State erst NACH erfolgreichem Senden – sonst holt
                # get_open_followup_tasks (filtert nur status=offen) den Task bei
                # einem Sendefehler nie wieder ab.
                await qdrant.update_task(point_id, {"status": "gefragt"})
                state.set_followup_task(sklave_chat, point_id)
                logger.info("Follow-up gesendet für Task: %s", point_id)
            except Exception as e:
                logger.exception("Fehler beim Follow-up für Task %s", point_id)

    # Tiny Task für Domina läuft als separater Abend-Job (siehe tiny_task_vorschlag_job)


@_job_guard
async def tiny_task_vorschlag_job(bot: Bot) -> None:
    """Abendlicher Tiny-Task-Vorschlag an die Domina."""
    logger.info("Tiny-Task-Vorschlag-Job gestartet.")
    await _send_tiny_task_vorschlag(bot)


@_job_guard
async def rollenspiel_vorschlag_job(bot: Bot) -> None:
    """Fr+Sa 18:00 – schlägt passendes Rollenspiel-Szenario vor wenn keines aktiv."""
    from bot.handlers.rollenspiel import SZENARIEN_BIBLIOTHEK
    domina_chat = paare.dom_chat_id()
    s = state.get(domina_chat)

    # Kein Vorschlag, wenn ein Rollenspiel wirklich LEBT (Mode aktiv und nicht
    # stale; das Fenster zählt seit D9/M1 ab der letzten Nachricht, 3 Tage –
    # s. state.STALE_ROLLENSPIEL_SECONDS). Ein eingeschlafenes Spiel wird hier
    # sauber beendet + gemeldet; verwaiste szenario_*-Keys ohne lebenden Mode
    # (Alt-Bestand/Kanten) ebenso – sonst unterdrücken sie jeden Fr/Sa-Vorschlag
    # für immer still (Trace 06.07.).
    if s.get("szenario_name"):
        verwaist_name = s.get("szenario_name", "")
        stale = state.clear_if_stale(domina_chat)  # räumt Mode + szenario_*-Keys
        if not stale and state.get_mode(domina_chat) == "rollenspiel_aktiv":
            logger.info("Rollenspiel bereits aktiv – kein Vorschlag.")
            return
        from bot.handlers.rollenspiel import _clear_rollenspiel_state
        _clear_rollenspiel_state(s)
        logger.info("Eingeschlafenes/verwaistes Rollenspiel '%s' auto-beendet.", verwaist_name)
        try:
            await telegram_helper.send_domina(bot, t("ROLLENSPIEL_AUTO_BEENDET", name=verwaist_name))
        except Exception:
            logger.exception("Auto-Beendet-Hinweis nicht zustellbar – Vorschlag läuft trotzdem.")

    if _flow_aktiv(domina_chat, "Rollenspiel-Vorschlag"):
        return

    domina_profile = await qdrant.get_user_profile("domina") or {}
    interessen = [i.lower() for i in domina_profile.get("interessen", [])]

    # Passendes Szenario suchen (kat.lower(): die Szenario-Kategorien sind
    # kapitalisiert, die Interessen lowercased – ohne lower() matchte hier NIE
    # etwas und es kam immer das Zufalls-Szenario).
    passende = [
        sz for sz in SZENARIEN_BIBLIOTHEK.values()
        if any(kat.lower() in interessen for kat in sz["aufgaben_kategorien"])
    ]
    szenario = random.choice(passende) if passende else random.choice(list(SZENARIEN_BIBLIOTHEK.values()))

    system = (
        f"Schlag der Domina ein Rollenspiel-Szenario für heute Abend vor – wie eine vertraute Freundin, die eine Idee hat.\n\n"
        f"{coach_persona.fuer_coach_prompt()}\n\n"
        f"Drei bis vier Sätze, locker. Erwähne dass sie es mit /rollenspiel starten kann."
    )
    prompt = (
        f"Szenario: '{szenario['name']}' – {szenario['beschreibung']}\n"
        f"Ton: {szenario['ton']}"
    )

    try:
        vorschlag = await grok.simple(prompt, system=system)
        if _nach_llm_verworfen(domina_chat, "Rollenspiel-Vorschlag"):
            return
        await telegram_helper.send_domina(bot, t("ROLLENSPIEL_IDEE", vorschlag=vorschlag), parse_mode="Markdown")
        logger.info("Rollenspiel-Vorschlag gesendet: %s", szenario["name"])
    except Exception as e:
        logger.exception("Fehler beim Rollenspiel-Vorschlag")


@_job_guard
async def lernkurve_job(bot: Bot) -> None:
    """Alle 2 Wochen (gerade ISO-Wochen) – Analyse der Lernkurve an Domina."""
    if not _zweiwochen_takt():
        logger.info("Lernkurve-Job übersprungen – ungerade ISO-Woche (2-Wochen-Takt).")
        return
    if _flow_aktiv(paare.dom_chat_id(), "Lernkurve"):
        return
    logger.info("Lernkurve-Job gestartet.")
    domina_profile = await qdrant.get_user_profile("domina") or {}
    level = domina_profile.get("aktuelles_level", 1)
    daten = await qdrant.get_lernkurve_daten("sklave")

    # Leer-Guard (Review D8/N6, wie die anderen Verdichtungs-Jobs): bei null
    # Aktivität in 2 Wochen weder Reasoning-Call noch Analyse-über-nichts senden.
    if daten["erledigt"] + daten["nicht_erledigt"] == 0:
        logger.info("Lernkurve-Job übersprungen – keine Task-Aktivität im Zeitraum.")
        return

    system = f"""Du schaust mit der Domina auf die letzten 2 Wochen – wie eine vertraute Freundin, die ihr ehrliches Feedback gibt.

{coach_persona.fuer_coach_prompt()}

Inhaltlich: was lief gut, was nicht, was wäre als nächstes dran. Aber NICHT als nummerierte Liste mit "1. Was gut lief: …". Lass es fließen wie ein Gespräch. 4-6 Sätze insgesamt. Konkret, nicht hohl. Kein [AUFGABE: ...] Tag."""
    prompt = f"""{coach_persona.level_zeile(level)}
Erledigte Tasks: {daten['erledigt']}
Nicht erledigte Tasks: {daten['nicht_erledigt']}
Kategorien: {daten['kategorien']}
Durchschnittliche Bewertung: {daten['avg_bewertung']}★
Beispiel-Tasks: {', '.join(daten['task_details'])}"""

    try:
        analyse = await grok.simple(prompt, system=system, reasoning=True)
        if _nach_llm_verworfen(paare.dom_chat_id(), "Lernkurve"):
            return
        await telegram_helper.send_domina(bot, t("LERNKURVE_PREFIX", analyse=analyse), parse_mode="Markdown")
    except Exception as e:
        logger.exception("Fehler beim Lernkurve-Job")


@_job_guard
async def training_job(bot: Bot) -> None:
    """Tägliches Psycho-Training – 5 Minuten nach Follow-up."""
    from bot.handlers.training import daily_training
    logger.info("Training-Job gestartet.")
    await daily_training(bot)


@_job_guard
async def stimmung_job(bot: Bot) -> None:
    from bot.handlers.stimmung import frage_stellen
    logger.info("Stimmungs-Job gestartet.")
    await frage_stellen(bot)


@_job_guard
async def ziel_erinnerung_job(bot: Bot) -> None:
    from bot.handlers.ziele import send_ziel_erinnerung
    # Bisher der einzige Domina-Send-Job ganz ohne Flow-Check (Trace 06.07.)
    if _flow_aktiv(paare.dom_chat_id(), "Ziel-Erinnerung"):
        return
    logger.info("Ziel-Erinnerungs-Job gestartet.")
    await send_ziel_erinnerung(bot)


@_job_guard
async def geheimnis_job(bot: Bot) -> None:
    """Alle 30 Minuten – enthüllt fällige Geheimnisse an den Sklaven."""
    geheimnisse = await qdrant.get_faellige_geheimnisse()
    for g in geheimnisse:
        point_id = g.get("qdrant_point_id")
        text = g.get("text", "")

        # Nicht in einen aktiven Sklaven-Flow hineinplatzen.
        if state.get_mode(paare.sub_chat_id()) not in ("chat", None):
            logger.info("Geheimnis-Enthüllung übersprungen – Sklave in aktivem Flow.")
            break

        # ZUERST als enthüllt markieren: schlägt das fehl, überspringen wir (kein
        # Senden) und versuchen es nächsten Lauf erneut. So wird das Geheimnis nie
        # alle 30 Min erneut enthüllt, falls das Status-Update nach dem Senden scheitert.
        try:
            await qdrant.run_io(client.set_payload, 
                collection_name="geheimnisse",
                payload={"status": "enthuellt"},
                points=[point_id],
            )
        except Exception:
            logger.exception("Geheimnis-Status konnte nicht gesetzt werden – überspringe (kein Spam)")
            continue

        from bot.prompts import persona
        system = f"""Du bist die Herrin. Enthülle deinem Sklaven jetzt das folgende Geheimnis – als bedeutungsvolle Enthüllung, persönlich und direkt.

{persona.fuer_sklaven_prompt()}

Zwei bis vier Sätze. Kein [AUFGABE: ...] Tag."""

        try:
            nachricht = await grok.simple(f"Geheimnis: {text}", system=system)
            # Re-Check NACH dem LLM-Await (TOCTOU): Pause/Flow kann sich im Fenster
            # geändert haben → Status zurück auf 'wartend', nächster Lauf probiert erneut.
            if state.is_paused() or state.get_mode(paare.sub_chat_id()) not in ("chat", None):
                logger.info("Geheimnis-Enthüllung nach Generierung verworfen – Pause/Mode geändert.")
                raise RuntimeError("Pause/Mode im LLM-Fenster geändert")
            await telegram_helper.send_sklave(bot, t("GEHEIMNIS_PREFIX", nachricht=nachricht), parse_mode="Markdown")
        except Exception:
            # Send/LLM fehlgeschlagen → nichts ist beim Sklaven angekommen. Status auf
            # 'wartend' zurücksetzen, damit die Enthüllung (transiente Grok-Ausfälle)
            # nächsten Lauf erneut versucht wird – statt dauerhaft verloren zu gehen.
            logger.exception("Fehler bei Geheimnis-Enthüllung – Status zurück auf 'wartend' für Retry")
            try:
                await qdrant.run_io(client.set_payload,
                    collection_name="geheimnisse",
                    payload={"status": "wartend"},
                    points=[point_id],
                )
            except Exception:
                logger.exception("Geheimnis-Status-Rücksetzung fehlgeschlagen")


@_job_guard
async def wochenplanung_job(bot: Bot) -> None:
    """Sonntags 10:00 – automatischer Wochenplan für die Domina."""
    from bot.handlers import wochenplanung
    logger.info("Wochenplanung-Job gestartet.")
    if _flow_aktiv(paare.dom_chat_id(), "Wochenplanung"):
        return
    domina_profile = await qdrant.get_user_profile("domina") or {}
    sklave_profile = await qdrant.get_user_profile("sklave") or {}
    letzte_kategorien = await qdrant.get_recent_task_kategorien("sklave", limit=7)
    bewertungs_kontext = await qdrant.get_bewertungs_kontext("sklave")

    try:
        plan = await wochenplanung._generiere_wochenplan(
            domina_profile=domina_profile,
            sklave_profile=sklave_profile,
            thema="",
            letzte_kategorien=letzte_kategorien,
            bewertungs_kontext=bewertungs_kontext,
        )
        if _nach_llm_verworfen(paare.dom_chat_id(), "Wochenplanung"):
            return
        await wochenplanung.sende_plan(
            bot, paare.dom_chat_id(), plan, t("WOCHENPLAN_TITEL")
        )
        logger.info("Wochenplan gesendet.")
    except Exception as e:
        logger.exception("Fehler beim Wochenplan")


@_job_guard
async def kommentar_analyse_job(bot: Bot) -> None:
    """Wöchentlich – analysiert Domina-Kommentare und aktualisiert Persönlichkeitsprofil."""
    if _flow_aktiv(paare.dom_chat_id(), "Kommentar-Analyse"):
        return
    logger.info("Kommentar-Analyse-Job gestartet.")
    sieben_tage_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    try:
        results, _ = await qdrant.run_io(client.scroll, 
            collection_name="tasks",
            scroll_filter=qm.Filter(
                must=[
                    qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key("sklave"))),
                    qm.FieldCondition(key="status", match=qm.MatchValue(value="erledigt")),
                ],
                # should = OR: maßgeblich ist, WANN kommentiert wurde – eine vor
                # 10 Tagen erteilte, diese Woche kommentierte Aufgabe fiel mit dem
                # reinen erteilt_am-Filter für immer durch (Trace 06.07.). Der
                # erteilt_am-Zweig bleibt als Fallback für Bestand ohne kommentar_am.
                should=[
                    qm.FieldCondition(key="kommentar_am", range=qm.DatetimeRange(gte=sieben_tage_ago)),
                    qm.FieldCondition(key="erteilt_am", range=qm.DatetimeRange(gte=sieben_tage_ago)),
                ],
            ),
            limit=50, order_by=qm.OrderBy(key="erteilt_am", direction="desc"),
            with_payload=True, with_vectors=False,
        )
        kommentare = [
            {"aufgabe": r.payload.get("aufgabe", ""), "kategorie": r.payload.get("kategorie", ""),
             "kommentar": r.payload.get("domina_kommentar", ""), "datum": r.payload.get("erteilt_am", "")}
            for r in results
            if r.payload.get("domina_kommentar")
        ]
        if not kommentare:
            logger.info("Keine neuen Kommentare diese Woche.")
            return

        kommentar_text = "\n".join(
            f"- Kategorie {k['kategorie']}: \"{k['kommentar']}\""
            for k in kommentare
        )
        system = f"""Du schaust mit der Domina auf ihre Kommentare zu Aufgaben der letzten Woche.

{coach_persona.fuer_coach_prompt()}

3-4 Sätze. Was lief gut, was nicht, was wäre nächste Woche dran. Lass es fließen wie ein Gespräch, nicht als nummerierte Liste. Kein [AUFGABE: ...] Tag."""
        prompt = f"Kommentare nach Kategorie:\n{kommentar_text}"

        analyse = await grok.simple(prompt, system=system)
        if _nach_llm_verworfen(paare.dom_chat_id(), "Kommentar-Analyse"):
            return
        await telegram_helper.send_domina(bot, t("KOMMENTAR_ANALYSE_PREFIX", analyse=analyse), parse_mode="Markdown")
        logger.info("Kommentar-Analyse gesendet.")
    except Exception as e:
        logger.exception("Fehler beim Kommentar-Analyse-Job")


@_job_guard
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
        if _flow_aktiv(paare.dom_chat_id(), "Tiny-Task-Feedback-Frage"):
            return

        await tiny_task_feedback.frage_stellen(bot, neuester)
        logger.info("Tiny-Task-Feedback-Frage gesendet (point_id=%s)", neuester.get("qdrant_point_id"))
    except Exception as e:
        logger.exception("Fehler beim Tiny-Task-Feedback-Job")


@_job_guard
async def resurface_job(bot: Bot) -> None:
    """Wöchentlich – holt einen positiv bewerteten Task aus ~3 Monaten zurück."""
    try:
        # Fenster: RESURFACE_TAGE_MIN bis RESURFACE_TAGE_MAX Tage alt
        ende = (datetime.now(timezone.utc) - timedelta(days=config.RESURFACE_TAGE_MIN)).isoformat()
        start = (datetime.now(timezone.utc) - timedelta(days=config.RESURFACE_TAGE_MAX)).isoformat()
        results, _ = await qdrant.run_io(client.scroll, 
            collection_name="tasks",
            scroll_filter=qm.Filter(must=[
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key("sklave"))),
                qm.FieldCondition(key="status", match=qm.MatchValue(value="erledigt")),
                qm.FieldCondition(key="erteilt_am", range=qm.DatetimeRange(gte=start, lte=ende)),
            ]),
            limit=100, with_payload=True, with_vectors=False,
        )

        # Nur sehr gut bewertete (4-5 Sterne)
        kandidaten = [
            r.payload for r in results
            if (r.payload.get("domina_bewertung") or 0) >= 4
        ]
        if not kandidaten:
            logger.info("Resurface-Job: keine geeigneten alten Tasks gefunden.")
            return

        # Zufälligen aussuchen
        task = random.choice(kandidaten)
        task_id = task.get("qdrant_point_id", "")
        aufgabe = task.get("aufgabe", "")
        kategorie = task.get("kategorie", "?")
        bewertung = task.get("domina_bewertung", 0)
        datum = task.get("erteilt_am", "")[:10]

        # Speichere ID im Domina-State für Callback
        # Mode-Check: nicht senden wenn Domina in aktivem Flow
        if _flow_aktiv(paare.dom_chat_id(), "Resurface"):
            return
        domina_s = state.get(paare.dom_chat_id())
        domina_s["resurface_task_id"] = task_id

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(t("BUTTON_ERNEUT_ERTEILEN"), callback_data=f"resurface:erteilen:{task_id}"),
            InlineKeyboardButton(t("BUTTON_DIESE_WOCHE_NICHT"), callback_data="resurface:skip"),
        ]])
        await telegram_helper.send_domina(
            bot,
            t(
                "RESURFACE_VORSCHLAG", datum=datum,
                kategorie=kategorie_logik.anzeige_name(kategorie),
                sterne="⭐" * bewertung, aufgabe=aufgabe[:300],
            ),
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        logger.info("Resurface gesendet: Task %s (Bewertung %d)", task_id, bewertung)
    except Exception as e:
        logger.exception("Fehler beim Resurface-Job")


# Die Sonntag-Abend-Kaskade (profil_pflege 21:00, coach_reflexion 22:00,
# lerntagebuch 23:00) lädt überlappende Conversation-Zeiträume. Ein Ladevorgang
# des 14-Tage-Maximalfensters wird gecacht und lokal zugeschnitten; die TTL von
# 2,5h deckt genau die Kaskade ab, danach wird wieder frisch geladen.
# Achtung Datenlücke: ein 21:00-Snapshot enthält keine späteren Gespräche.
# Für die 14-Tage-Aggregate (profil_pflege/coach_reflexion) ist 2h Staleness
# egal – das WÖCHENTLICHE Lerntagebuch lädt deshalb mit frisch=True, sonst
# fehlten die Sonntag-21-23-Uhr-Gespräche dauerhaft (nächstes Fenster beginnt
# erst 23:00).
# Cache PRO PAAR: get_conversations_in_range("domina") löst den Mandanten über
# den Paar-Kontext auf – ein globaler Cache würde bei ≥2 Paaren innerhalb der
# TTL die Gespräche von Paar 1 in die Sonntags-Jobs von Paar 2 spülen
# (Cross-Mandanten-Leak, Trace 06.07., Lücke 9).
_CONV_CACHES: dict[str, dict] = {}
_CONV_CACHE_TTL = 2.5 * 3600


def _conv_cache() -> dict:
    return _CONV_CACHES.setdefault(paare.aktueller_kontext(), {"ts": 0.0, "entries": []})


def vergiss_paar(paar_id: str) -> None:
    """Cache-Eintrag eines gelöschten Paares entfernen (Hermes-Review H9) –
    sonst bleiben bis zu 300 intime Konversations-Payloads bis zum Neustart
    im Prozess-Speicher."""
    _CONV_CACHES.pop(paar_id, None)


async def _domina_conversations(start_iso: str, end_iso: str, limit: int,
                                frisch: bool = False) -> list[dict]:
    import time as _time
    cache = _conv_cache()
    now = _time.time()
    if frisch or now - cache["ts"] > _CONV_CACHE_TTL:
        ende = datetime.now(timezone.utc)
        start = ende - timedelta(days=14)
        cache["entries"] = await qdrant.get_conversations_in_range(
            "domina", start.isoformat(), ende.isoformat(), limit=300,
        )
        cache["ts"] = now
    start_dt, end_dt = _parse_datum(start_iso), _parse_datum(end_iso)
    gefiltert = [
        e for e in cache["entries"]
        if start_dt <= _parse_datum(e.get("datum", "")) <= end_dt
    ]
    # Cache ist chronologisch (asc) → die neuesten `limit` Gespräche des Fensters
    # behalten, weiterhin in Zeitreihenfolge.
    return gefiltert[-limit:]


async def generiere_lerntagebuch(days: int = 7, min_eintraege: int = 3) -> dict:
    """Erzeugt + speichert ein Lerntagebuch-Eintrag für die letzten `days` Tage.

    Returns:
        {"status": "ok", "zeitraum": ..., "eintraege": int, "inhalt": str} bei Erfolg
        {"status": "leer", "zeitraum": ..., "eintraege": int} wenn zu wenig Gespräche
        {"status": "fehler", "fehler": str} bei Exception
    """
    try:
        ende = datetime.now(timezone.utc)
        start = ende - timedelta(days=days)
        zeitraum = f"{start.date().isoformat()} – {ende.date().isoformat()}"

        # frisch=True: nicht auf dem 21:00-Kaskaden-Snapshot arbeiten (s.o.)
        entries = await _domina_conversations(start.isoformat(), ende.isoformat(), limit=200, frisch=True)
        if len(entries) < min_eintraege:
            return {"status": "leer", "zeitraum": zeitraum, "eintraege": len(entries)}

        rohtext_zeilen = []
        for e in entries:
            datum = e.get("datum", "")[:16].replace("T", " ")
            themen = e.get("themen") or ([e.get("thema")] if e.get("thema") else [])
            themen_str = ", ".join(t for t in themen if t)
            d = e.get("domina_nachricht", "") or ""
            c = e.get("coach_antwort", "") or ""
            rohtext_zeilen.append(
                f"[{datum}] ({themen_str})\n  Domina: {d[:600]}\n  Coach: {c[:600]}"
            )
        rohtext = "\n\n".join(rohtext_zeilen)
        if len(rohtext) > 30000:
            rohtext = rohtext[:30000] + "\n\n[... weitere Gespräche gekürzt ...]"

        system = """Du verdichtest die Coach-Gespräche mit der Domina zu einem internen Lerntagebuch-Eintrag (wird im Coach-Prompt als Langzeit-Wissen wiederverwendet, geht nicht direkt an sie).

Erstelle aus den Gesprächen eine VERDICHTETE Zusammenfassung als strukturiertes Langzeit-Wissen,
damit du in zukünftigen Gesprächen jederzeit darauf zurückgreifen kannst. Das ist eine INTERNE
Notiz an dich selbst – sie geht NICHT an die Domina.

Format (interne Stichpunkte über sie, NICHT als Anrede an sie formuliert):

🎯 Hauptthemen dieses Zeitraums:
- (max. 6 Bulletpoints, konkret)

💡 Erkenntnisse & Entscheidungen:
- (was wurde besprochen / entschieden / geplant?)

🌱 Fortschritte & Gefühle:
- (Stimmungen, Wachstum, Sorgen, Erfolge)

🔁 Wiederkehrende Muster:
- (was kommt immer wieder hoch, was sollte ich mir merken?)

📌 Offene Fragen / nächste Schritte:
- (was wartet noch auf Klärung, woran soll weitergearbeitet werden?)

Konkret, ohne Wiederholung. Keine Floskeln. Kein [AUFGABE: ...] Tag."""
        prompt = (
            f"Hier sind alle Coach-Gespräche der letzten {days} Tage ({zeitraum}):\n\n"
            f"{rohtext}"
        )

        zusammenfassung = await grok.simple(prompt, system=system, reasoning=True)
        await qdrant.save_lerntagebuch("domina", zeitraum, zusammenfassung)
        logger.info("Lerntagebuch gespeichert (%d Gespräche, Zeitraum %s).", len(entries), zeitraum)
        return {
            "status": "ok",
            "zeitraum": zeitraum,
            "eintraege": len(entries),
            "inhalt": zusammenfassung,
        }
    except Exception as e:
        logger.exception("Fehler beim Lerntagebuch")
        return {"status": "fehler", "fehler": str(e)}


@_job_guard
async def lerntagebuch_job(bot: Bot) -> None:
    """Wöchentlich – verdichtet die Coach-Gespräche der letzten 7 Tage."""
    result = await generiere_lerntagebuch(days=7)
    if result["status"] == "leer":
        logger.info("Lerntagebuch übersprungen – nur %d Gespräche in der Woche.", result["eintraege"])


async def generiere_profil_vorschlaege(bot: Bot, days: int = 14) -> dict:
    """Auto-Profil-Updater (Ebene 4).

    Analysiert die letzten `days` Tage und schlaegt Profil-Aenderungen vor.
    Speichert jeden Vorschlag als pending in coach_regeln (typ='profil_update')
    und schickt ihn der Domina mit ✅/🗑-Buttons. Wendet NICHTS automatisch an.

    Returns: {"status": "ok"|"leer"|"fehler", "vorschlaege": int, "zeitraum": str}
    """
    import json
    from bot.handlers import coach_regeln as _cr
    try:
        ende = datetime.now(timezone.utc)
        start = ende - timedelta(days=days)
        zeitraum = f"{start.date().isoformat()} – {ende.date().isoformat()}"

        domina_profile = await qdrant.get_user_profile("domina") or {}
        sklave_profile = await qdrant.get_user_profile("sklave") or {}

        # Signale sammeln
        gespraeche = await _domina_conversations(start.isoformat(), ende.isoformat(), limit=200)
        if len(gespraeche) < 5:
            return {"status": "leer", "vorschlaege": 0, "zeitraum": zeitraum,
                    "info": f"nur {len(gespraeche)} Gespraeche"}

        # 5-Sterne-Tasks der letzten `days` Tage – sort_by_datum holt die NEUESTEN
        # erledigten Tasks (sonst arbiträre Teilmenge bei > limit).
        erledigt = await qdrant.get_tasks_by_status(["erledigt"], sort_by_datum=True)
        top_tasks = sorted(
            [t for t in erledigt
             if (t.get("domina_bewertung") or 0) >= 4
             and t.get("erteilt_am", "") >= start.isoformat()],
            key=lambda x: x.get("erteilt_am", ""), reverse=True,
        )[:10]

        # Bereits aktive UND noch unbestätigte (pending) Regeln/Notizen als
        # "nicht doppeln"-Kontext – ohne pending schlägt der Job denselben
        # Punkt erneut vor, solange die Domina den letzten Vorschlag noch
        # nicht beantwortet hat (Trace 06.07., Kleinkram).
        aktive_regeln = (await qdrant.get_active_coach_regeln("domina")
                         + await qdrant.get_pending_coach_regeln("domina"))

        # Roh-Material
        gesp_text = []
        for e in gespraeche[-40:]:
            datum = e.get("datum", "")[:10]
            d = e.get("domina_nachricht", "") or ""
            gesp_text.append(f"[{datum}] {d[:400]}")
        gesp_rohtext = "\n".join(gesp_text)

        tasks_text = "\n".join(
            f"- {t.get('aufgabe','')[:200]} (Kategorie: {t.get('kategorie','?')}, "
            f"{t.get('domina_bewertung','?')}★)"
            for t in top_tasks
        ) or "(keine)"

        regeln_text = "\n".join(f"- {r.get('text','')}" for r in aktive_regeln) or "(keine)"

        domina_str = json.dumps({
            "erfahrungsstand": domina_profile.get("erfahrungsstand"),
            "interessen": domina_profile.get("interessen", []),
            "ziele": domina_profile.get("ziele", ""),
        }, ensure_ascii=False, indent=2)
        sklave_str = json.dumps({
            "erfahrungsstand": sklave_profile.get("erfahrungsstand"),
            "vorlieben": sklave_profile.get("vorlieben", []),
            "wunsch_kategorien": sklave_profile.get("wunsch_kategorien", []),
            "persoenlichkeit_tags": sklave_profile.get("persoenlichkeit_tags", []),
        }, ensure_ascii=False, indent=2)

        if len(gesp_rohtext) > 20000:
            gesp_rohtext = gesp_rohtext[:20000] + "\n[... gekuerzt ...]"

        # Limit-Listen für Prompt + Nachfilter (Test-Befund F4: eine Kategorie
        # wurde als Domina-Interesse vorgeschlagen, weil sie im Chat oft vorkam –
        # dort aber nur, weil sie als Hard Limit GEBLOCKT wurde).
        hard_limits = sklave_profile.get("hard_limits", []) or []
        grenzen = domina_profile.get("grenzen", []) or []
        hard_limits_text = ", ".join(hard_limits) or "(keine)"
        grenzen_text = ", ".join(grenzen) or "(keine)"

        system = f"""Du bist ein BDSM Coach und pflegst die Profile von Domina und Sklave.

WICHTIG – TABU-FELDER (nie aendern, nicht vorschlagen):
- hard_limits (sicherheitsrelevant)
- kinderfreie_zeiten (Lebenssituation)

WICHTIG – KEINE LIMIT-THEMEN ALS VORLIEBE/INTERESSE:
Leite NIEMALS Interessen oder Vorlieben aus Themen ab, die in den Gespraechen
als Grenze oder Hard Limit abgelehnt/blockiert wurden – auch nicht, wenn sie
oft erwaehnt werden (haeufige Erwaehnung heisst dort Ablehnung, nicht Interesse).
Hard Limits des Sklaven: {hard_limits_text}
Grenzen der Domina: {grenzen_text}

ZUGRIFF ERLAUBT:
- domina.interessen        (list_add – neue Interessen aus oft besprochenen Themen)
- domina.ziele             (text_replace – nur wenn Ziele klar verschoben/verfeinert)
- domina.erfahrungsstand   (text_replace – nur bei klarer Belegsignifikanz)
- sklave.vorlieben         (list_add – aus 5-Sterne-Tasks oder positiven Reaktionen)
- sklave.wunsch_kategorien (list_add – nur wenn explizit gewuenscht im Chat)
- sklave.persoenlichkeit_tags (list_add – z.B. "willig", "rebellisch", "kreativ")
- sklave.erfahrungsstand   (text_replace)

AUFGABE:
Schlage maximal 4 konkrete Profil-Aenderungen vor, die GUT BELEGT sind.
Liefere die Antwort als reines JSON in folgendem Format – KEIN Markdown, KEINE Kommentare:

{{"changes": [
  {{"user": "domina", "feld": "interessen", "operation": "list_add", "wert": ["Bondage"], "begruendung": "kurzer Beleg"}},
  {{"user": "sklave", "feld": "vorlieben", "operation": "list_add", "wert": ["langsamer Aufbau"], "begruendung": "..."}}
]}}

Wenn nichts solide belegbar ist, antworte EXAKT:
{{"changes": []}}

Keine Erfindung. Keine Tabu-Felder. Maximal 4 Eintraege."""
        prompt = f"""AKTUELLE PROFILE (nur die Felder, die du anfassen darfst):

Domina:
{domina_str}

Sklave:
{sklave_str}

BEREITS AKTIVE COACH-REGELN (zur Info, nicht doppeln):
{regeln_text}

SIGNALE DER LETZTEN {days} TAGE ({zeitraum}):

Domina-Aussagen (Auszug):
{gesp_rohtext}

Hoch bewertete Aufgaben (Indizien fuer Sklaven-Vorlieben):
{tasks_text}"""

        antwort = await grok.simple(prompt, system=system, reasoning=True, temperature=0)  # JSON-Patch: deterministisch
        try:
            parsed = grok.parse_json(antwort)
        except json.JSONDecodeError as e:
            # Rohantwort nur auf DEBUG – generierte intime Inhalte (D9/S8).
            logger.error("Profil-Pflege: JSON-Parse-Fehler – %s (Antwort: %d Zeichen)", e, len(antwort or ""))
            logger.debug("Profil-Pflege-Rohantwort: %s", antwort[:300])
            return {"status": "fehler", "vorschlaege": 0, "zeitraum": zeitraum,
                    "info": f"Grok-Antwort nicht parsebar: {e}"}

        changes = parsed.get("changes") if isinstance(parsed, dict) else None
        if not changes or not isinstance(changes, list):
            return {"status": "leer", "vorschlaege": 0, "zeitraum": zeitraum,
                    "info": "Grok hat keine Aenderungen vorgeschlagen"}

        # Pro user-Gruppe einen Vorschlag zusammenstellen (max 2 Gruppen)
        per_user: dict = {"domina": [], "sklave": []}
        for ch in changes[:6]:
            u = ch.get("user")
            if u not in per_user or not ch.get("feld") or not ch.get("operation"):
                continue
            # Nachfilter (F4): list_add-Werte, die mit Hard Limits / Grenzen
            # kollidieren, werden NIE als Interesse/Vorliebe vorgeschlagen –
            # egal was das Modell aus den Gesprächen ableitet.
            if ch.get("operation") == "list_add":
                werte = ch.get("wert") if isinstance(ch.get("wert"), list) else [ch.get("wert")]
                sauber = []
                for w in werte:
                    if w and not await limits_check.verletzungen(str(w), hard_limits, grenzen):
                        sauber.append(w)
                if not sauber:
                    logger.info("Profil-Pflege: Vorschlag %s.%s verworfen (Limit-Kollision: %r)",
                                u, ch.get("feld"), ch.get("wert"))
                    continue
                ch["wert"] = sauber
            per_user[u].append({k: ch[k] for k in ("feld", "operation", "wert", "begruendung") if k in ch})

        # TOCTOU-Re-Check nach dem langen Reasoning-Await (D9/N8): Safeword/Flow
        # im Fenster → pending-Vorschläge heute nicht mehr senden (Buttons wären
        # zwar pause_guard-gedeckt, aber der Send in die Pause bricht die Konvention).
        if _nach_llm_verworfen(paare.dom_chat_id(), "Profil-Pflege"):
            return {"status": "verworfen", "vorschlaege": 0, "zeitraum": zeitraum,
                    "info": "Pause/Flow im LLM-Fenster"}

        gesendet = 0
        for profile_user, gruppe in per_user.items():
            if not gruppe:
                continue
            patch = {"changes": gruppe}
            text = _kurzbeschreibung_patch(profile_user, gruppe)
            try:
                point_id = await qdrant.save_coach_regel(
                    user_id="domina",
                    text=text,
                    typ="profil_update",
                    status="pending",
                    quelle="auto_profil_pflege",
                    kontext=f"Profil-Pflege {zeitraum}",
                    profile_user=profile_user,
                    profile_patch=patch,
                )
                await _cr.sende_profil_vorschlag(
                    bot, point_id, profile_user, patch,
                    kontext=f"Profil-Pflege {zeitraum}",
                )
                gesendet += 1
            except Exception as e:
                logger.error("Profil-Vorschlag konnte nicht gesendet werden: %s", e)

        logger.info("Profil-Pflege: %d Vorschlaege gesendet (%s).", gesendet, zeitraum)
        return {"status": "ok", "vorschlaege": gesendet, "zeitraum": zeitraum}
    except Exception as e:
        logger.exception("Fehler beim Profil-Pflege-Job")
        return {"status": "fehler", "vorschlaege": 0, "zeitraum": "?", "info": str(e)}


def _kurzbeschreibung_patch(profile_user: str, gruppe: list) -> str:
    """Kurze Text-Beschreibung des Patches fuer Speicherung."""
    teile = []
    for ch in gruppe:
        op = ch.get("operation")
        feld = ch.get("feld")
        wert = ch.get("wert")
        if op == "list_add":
            werte = ", ".join(wert) if isinstance(wert, list) else str(wert)
            teile.append(f"{feld}+={werte}")
        elif op == "text_replace":
            teile.append(f"{feld}:={str(wert)[:60]}")
    return f"Profil ({profile_user}): " + "; ".join(teile)


@_job_guard
async def profil_pflege_job(bot: Bot) -> None:
    """Alle 2 Wochen (gerade ISO-Wochen) – Auto-Profil-Pflege."""
    if not _zweiwochen_takt():
        logger.info("Profil-Pflege-Job übersprungen – ungerade ISO-Woche (2-Wochen-Takt).")
        return
    # Nicht in einen aktiven Domina-Flow platzen (D9/N8); der manuelle
    # /profilpflege-Pfad (coach_regeln) bleibt bewusst ungeguardet.
    if _flow_aktiv(paare.dom_chat_id(), "Profil-Pflege"):
        return
    await generiere_profil_vorschlaege(bot, days=14)


@_job_guard
async def coach_reflexion_job(bot: Bot) -> None:
    """Alle 2 Wochen – Coach reflektiert ueber sich selbst.

    Schaut sich die Domina-Coach-Gespraeche der letzten 14 Tage an, sucht
    Muster (kurze Folge-Antworten, Korrekturen, wiederkehrende Bitten) und
    schlaegt 1–3 neue Regeln vor, die im Coach-Prompt aktiv werden sollen.

    Die Vorschlaege werden als 'pending' gespeichert und der Domina mit
    Ja/Nein-Buttons zur Bestaetigung geschickt – keine stillen Updates.
    Läuft nur in geraden ISO-Wochen (2-Wochen-Takt)."""
    if not _zweiwochen_takt():
        logger.info("Coach-Reflexion-Job übersprungen – ungerade ISO-Woche (2-Wochen-Takt).")
        return
    if _flow_aktiv(paare.dom_chat_id(), "Coach-Reflexion"):  # D9/N8
        return
    from bot.handlers import coach_regeln as _cr
    try:
        ende = datetime.now(timezone.utc)
        start = ende - timedelta(days=14)
        zeitraum = f"{start.date().isoformat()} – {ende.date().isoformat()}"

        entries = await _domina_conversations(start.isoformat(), ende.isoformat(), limit=300)
        if len(entries) < 8:
            logger.info("Coach-Reflexion uebersprungen – nur %d Gespraeche.", len(entries))
            return

        # Existierende Regeln/Notizen mitgeben – verhindert Duplikate. AUCH die
        # pending-Vorschläge: sonst schlägt die Reflexion denselben Punkt erneut
        # vor, solange die Domina den letzten noch nicht beantwortet hat.
        aktive_regeln = (await qdrant.get_active_coach_regeln("domina")
                         + await qdrant.get_pending_coach_regeln("domina"))
        existing_text = "\n".join(f"- {r.get('text','')}" for r in aktive_regeln) or "(keine)"

        rohtext_zeilen = []
        for e in entries:
            datum = e.get("datum", "")[:16].replace("T", " ")
            d = e.get("domina_nachricht", "") or ""
            c = e.get("coach_antwort", "") or ""
            rohtext_zeilen.append(f"[{datum}]\n  Domina: {d[:500]}\n  Coach: {c[:500]}")
        rohtext = "\n\n".join(rohtext_zeilen)
        if len(rohtext) > 30000:
            rohtext = rohtext[:30000] + "\n\n[... weitere Gespraeche gekuerzt ...]"

        system = f"""Du bist ein BDSM Coach und reflektierst ueber deinen eigenen Stil.
Du hast in den letzten 14 Tagen ({zeitraum}) mit der Domina gesprochen.

Analysiere folgende Signale in den Gespraechen:
- Kurze, abrupte Domina-Antworten nach laengeren Coach-Texten ("ok", "ja", "passt")
- Korrekturen oder Wiederholungen der gleichen Frage durch die Domina
- Wechsel des Themas direkt nach deiner Antwort
- Aktive positive Reaktionen (laengere, engagierte Antworten)
- Wiederkehrende Bitten ("mach das kuerzer", "sei haerter", "weniger Emojis", ...)

Schlage 0–3 NEUE Regeln vor, an die du dich kuenftig halten solltest, damit der
Stil besser zur Domina passt. Eine Regel pro Zeile, max. ein Satz, in du-Form
("Antworte ...", "Vermeide ...", "Frage erst nach ...").

Wenn nichts Neues ableitbar ist, antworte nur mit: KEINE_REGEL

Sonst NUR die Regeln, jeweils auf einer eigenen Zeile, ohne Nummerierung,
ohne Erklaerung, ohne Anfuehrungszeichen."""
        prompt = f"""Diese Regeln gelten bereits (NICHT wiederholen):
{existing_text}

Hier die Gespraeche:

{rohtext}"""

        antwort = await grok.simple(prompt, system=system, reasoning=True)
        antwort = (antwort or "").strip()
        if not antwort or antwort.upper().startswith("KEINE_REGEL"):
            logger.info("Coach-Reflexion: keine neuen Regeln.")
            return

        vorschlaege = [
            z.strip(" -•\t").strip('"').strip("'")
            for z in antwort.splitlines() if z.strip()
        ]
        # Filter: nicht zu kurz, nicht das KEINE_REGEL-Token, max 3
        vorschlaege = [v for v in vorschlaege if len(v) > 8 and "KEINE_REGEL" not in v.upper()][:3]
        if not vorschlaege:
            return

        # TOCTOU-Re-Check nach dem LLM-Await (D9/N8).
        if _nach_llm_verworfen(paare.dom_chat_id(), "Coach-Reflexion"):
            return

        # Erst ALLE Regeln speichern, dann Intro + Vorschläge senden – sonst
        # bekommt die Domina eine Ankündigung, hinter der bei Speicherfehlern
        # gar keine Vorschläge stehen (Trace 06.07., Kleinkram).
        gespeichert = []
        for v in vorschlaege:
            try:
                point_id = await qdrant.save_coach_regel(
                    user_id="domina",
                    text=v,
                    typ="regel",
                    status="pending",
                    quelle="abgeleitet_reflexion",
                    kontext=f"Reflexion {zeitraum}",
                )
                gespeichert.append((point_id, v))
            except Exception as e:
                logger.error("Reflexions-Regel konnte nicht gespeichert werden: %s", e)
        if not gespeichert:
            logger.error("Coach-Reflexion: keine Regel speicherbar – kein Intro gesendet.")
            return

        intro = t("REFLEXION_INTRO", zeitraum=zeitraum, anzahl=len(gespeichert))
        try:
            await bot.send_message(chat_id=paare.dom_chat_id(), text=intro, parse_mode="Markdown")
        except Exception as e:
            logger.error("Reflexion-Intro fehlgeschlagen: %s", e)

        for point_id, v in gespeichert:
            try:
                await _cr.sende_vorschlag(bot, point_id, v, kontext="Reflexion ueber die letzten 14 Tage")
            except Exception as e:
                logger.error("Reflexions-Vorschlag konnte nicht gesendet werden: %s", e)
        logger.info("Coach-Reflexion: %d Vorschlaege gesendet.", len(gespeichert))
    except Exception as e:
        logger.exception("Fehler beim Coach-Reflexions-Job")


@_job_guard
async def training_erinnerung_job(bot: Bot) -> None:
    """Täglich – erinnert die Domina wenn seit 4 Tagen kein Task zugewiesen wurde."""
    try:
        vier_tage_ago = (datetime.now(timezone.utc) - timedelta(days=config.TRAINING_ERINNERUNG_TAGE)).isoformat()
        results, _ = await qdrant.run_io(client.scroll, 
            collection_name="tasks",
            scroll_filter=qm.Filter(must=[
                qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key("sklave"))),
                qm.FieldCondition(key="erteilt_am", range=qm.DatetimeRange(gte=vier_tage_ago)),
            ]),
            limit=1, with_payload=False, with_vectors=False,
        )
        if results:
            return
        logger.info("Training-Erinnerung: Kein Task seit 4 Tagen – sende Erinnerung.")
        await telegram_helper.send_domina(
            bot, t("ERINNERUNG_KEINE_AUFGABE", tage=config.TRAINING_ERINNERUNG_TAGE),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.exception("Fehler beim Training-Erinnerungs-Job")

@_job_fangnetz
async def backup_job(bot: Bot) -> None:
    """Tägliches Qdrant-Backup (JSON-Export + native Snapshots). Meldet Fehler an die Domina."""
    from bot.services import backup
    try:
        bericht = await backup.run_backup()
        logger.info("backup_job ok: %s", bericht)
    except Exception:
        logger.exception("backup_job fehlgeschlagen")
        try:
            await telegram_helper.send_domina(bot, t("BACKUP_FEHLGESCHLAGEN"))
        except Exception:
            logger.exception("Konnte Backup-Fehler nicht an Domina melden")


@_job_guard
async def sklave_dossier_job(bot: Bot) -> None:
    """Wöchentlich – Charakteristiken (Sklave + Domina) verdichten und die
    Kategorie-Reaktionen sanft altern lassen (Recency)."""
    from bot.handlers import dossier
    try:
        text = await dossier.aktualisiere_dossier()
        logger.info("Sklaven-Dossier: %s", "aktualisiert" if text else "zu wenig Material")
    except Exception:
        logger.exception("sklave_dossier_job (Sklave) fehlgeschlagen")
    try:
        dtext = await dossier.aktualisiere_domina_dossier()
        logger.info("Domina-Dossier: %s", "aktualisiert" if dtext else "zu wenig Material")
    except Exception:
        logger.exception("sklave_dossier_job (Domina) fehlgeschlagen")
    try:
        n = await kategorie_logik.decay_profil_reaktionen("sklave")
        logger.info("Kategorie-Reaktionen gealtert (Recency): %d Kategorien aktiv.", n)
    except Exception:
        logger.exception("sklave_dossier_job (Decay) fehlgeschlagen")


@_job_guard
async def offene_faeden_job(bot: Bot) -> None:
    """Täglich – extrahiert offene Fäden aus jüngsten Gesprächen, damit die Herrin
    von sich aus darauf zurückkommt."""
    from bot.handlers import dossier
    try:
        faeden = await dossier.aktualisiere_offene_faeden()
        logger.info("Offene Fäden aktualisiert: %d", len(faeden))
    except Exception:
        logger.exception("offene_faeden_job fehlgeschlagen")
