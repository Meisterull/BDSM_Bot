"""
Inspirations Handler – /inspiration gibt 3 Aufgaben-Ideen passend zum Level.
Nur für Domina. Mit kinderfreien Zeiten und aktueller Uhrzeit im Kontext.
"""
import uuid
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes
from qdrant_client import models as qm

from bot import config, state
from bot.services import paare
from bot.services import qdrant, grok, embeddings as emb, synonyme, kategorie_logik, telegram_helper, zeiten
from bot.messages import t

logger = logging.getLogger(__name__)


def _zeit_kontext(kinderfreie_zeiten: list, kind_anzahl: int | None = None) -> str:
    """Erstellt Zeitkontext für den Prompt."""
    jetzt = datetime.now(ZoneInfo(config.TIMEZONE))
    uhrzeit = jetzt.strftime("%H:%M")
    wochentag = ["Montag","Dienstag","Mittwoch","Donnerstag",
                 "Freitag","Samstag","Sonntag"][jetzt.weekday()]

    n = kind_anzahl if isinstance(kind_anzahl, int) else None
    if n == 0:
        # Keine Kinder – keine Diskretions-Constraint
        return f"Aktuelle Uhrzeit: {uhrzeit} ({wochentag})"

    anzahl_str = (
        f"{n} Kind{'er' if (n or 0) != 1 else ''} im Haus"
        if isinstance(n, int) else "Kinder im Haus"
    )
    if kinderfreie_zeiten:
        zeiten_str = ", ".join(kinderfreie_zeiten)
        return (
            f"Aktuelle Uhrzeit: {uhrzeit} ({wochentag})\n"
            f"{anzahl_str}. Kinderfreie Zeiten heute: {zeiten_str}\n"
            f"→ Außerhalb der kinderfreien Zeiten nur diskrete, leise, kinderfreie Aufgaben."
        )
    return (
        f"Aktuelle Uhrzeit: {uhrzeit} ({wochentag})\n"
        f"{anzahl_str} – alle Aufgaben müssen diskret, leise und kinderfrei bleiben."
    )


def _parse_vorschlaege(text: str) -> list[str]:
    import re
    parts = re.split(r'\n(?=\d+\.)', text.strip())
    vorschlaege = []
    for part in parts:
        part = part.strip()
        if part and part[0].isdigit():
            vorschlaege.append(part)
    if not vorschlaege:
        vorschlaege = [text.strip()]
    return vorschlaege[:3]


async def _generate_vorschlaege(
    domina_profile: dict,
    sklave_profile: dict,
    feedback: str = "",
    iteration: int = 1,
    letzte_aufgaben: list = None,
) -> tuple[str, list[str]]:
    kontext = await _lade_generierungs_kontext(domina_profile.get("aktuelles_level", 1))
    # Frisch geladenes Sklaven-Profil verwenden – Persönlichkeits-Tags und
    # Reaktionsmuster ändern sich laufend.
    sklave_profile = kontext["sklave_profile"]

    system, prompt = _baue_vorschlags_prompt(
        domina_profile, sklave_profile, kontext, feedback, letzte_aufgaben
    )
    # Kategorien wählt das LLM erst beim Generieren → alle vorhandenen Wissens-Briefe beilegen.
    from bot.prompts import coach_persona
    skill_block = await coach_persona.skill_kontext_block()
    if skill_block:
        prompt += "\n\n" + skill_block
    raw = await grok.simple(prompt, system=system, reasoning=True)

    sk_hl = sklave_profile.get("hard_limits", []) or []
    do_gr = domina_profile.get("grenzen", []) or []
    return await _limits_gefilterte_vorschlaege(raw, system, prompt, sk_hl, do_gr)


async def _lade_generierungs_kontext(level: int) -> dict:
    """Lädt Gesprächs-Kontext, letzte Inspirationen, Stimmung, Bewertungs-Kontext
    und das frische Sklaven-Profil für den Vorschlags-Prompt."""
    query_vector = await emb.get_embedding(f"Aufgabe Inspiration Level {level} BDSM")
    ctx_entries = await qdrant.get_hybrid_conversation_context("domina", query_vector, limit=5,
                                                                felder=qdrant.KONTEXT_FELDER)
    stimmung_entry = await qdrant.get_latest_stimmung("sklave")
    return {
        "ctx_entries": ctx_entries,
        "letzte_inspirationen": await qdrant.get_recent_inspirationen(limit=5),
        "stimmung": stimmung_entry.get("zusammenfassung", "") if stimmung_entry else "",
        "bewertungs_kontext": await qdrant.get_bewertungs_kontext("sklave"),
        "sklave_profile": await qdrant.get_user_profile("sklave") or {},
    }


def _baue_vorschlags_prompt(
    domina_profile: dict,
    sklave_profile: dict,
    kontext: dict,
    feedback: str,
    letzte_aufgaben: list,
) -> tuple[str, str]:
    """Baut den Inspirations-Prompt aus Profilen und geladenem Kontext (rein, kein I/O).
    Gibt (system, user) zurück: Anweisung/Persona/Format als System, Daten als User."""
    level = domina_profile.get("aktuelles_level", 1)
    zeit_str = _zeit_kontext(
        domina_profile.get("kinderfreie_zeiten", []),
        domina_profile.get("kind_anzahl"),
    )
    ctx_entries = kontext["ctx_entries"]
    letzte_inspirationen = kontext["letzte_inspirationen"]
    stimmung = kontext["stimmung"]
    bewertungs_kontext = kontext["bewertungs_kontext"]

    feedback_str = ""
    if feedback:
        from bot.prompts import followup as fp
        feedback_str = (
            f"\n{fp.nutzer_text('Die Domina hat vorherige Vorschläge abgelehnt, ihre Begründung', feedback)}\n"
            f"Berücksichtige das – aber bleibe beim Level {level}.\n"
        )

    nicht_wiederholen_str = ""
    if letzte_inspirationen:
        ideen_liste = "\n".join(f"- {idea}" for idea in letzte_inspirationen)
        nicht_wiederholen_str = f"\nBereits vorgeschlagene Ideen (NICHT wiederholen):\n{ideen_liste}\n"

    abwechslung_str = ""
    if letzte_aufgaben:
        liste = "\n".join(f"- {a}" for a in letzte_aufgaben)
        abwechslung_str = (
            f"\nWICHTIG – Abwechslung:\n"
            f"Folgende Aufgaben wurden zuletzt vorgeschlagen oder erledigt – schlage\n"
            f"komplett andere Themen und Kategorien vor:\n"
            f"{liste}\n"
            f"Wiederholungen des gleichen Themas sind NICHT akzeptabel.\n"
            f"Die 3 Vorschläge müssen aus verschiedenen Kategorien stammen "
            f"(verfügbar: {', '.join(kategorie_logik.alle_kategorien(sklave_profile))}).\n"
        )

    kontext_str = ""
    if ctx_entries:
        themen = [e.get("thema", "") for e in ctx_entries if e.get("thema")]
        if themen:
            kontext_str = f"\nAktuelle Gesprächsthemen der Domina: {', '.join(dict.fromkeys(themen))}\n"

    stimmung_str = ""
    if stimmung:
        stimmung_str = (
            f"\nAktuelle Stimmung des Sklaven: {stimmung}\n"
            f"→ Bei schlechter Stimmung eher sanftere, aufbauende Aufgaben vorschlagen. "
            f"Bei guter Stimmung darf es anspruchsvoller sein.\n"
        )

    bewertung_str = f"\n{bewertungs_kontext}" if bewertungs_kontext else ""

    from bot.prompts import coach_persona

    system = f"""Du sprichst mit der Domina – schlag ihr drei konkrete, unterschiedliche Aufgaben-Ideen für ihren Sklaven vor.

{coach_persona.fuer_aufgaben_vorschlag()}

Die 3 Ideen sollen:
- Aus unterschiedlichen Kategorien stammen (verfügbar: {', '.join(kategorie_logik.alle_kategorien(sklave_profile))})
- Zum Level {level} und zur aktuellen Uhrzeit passen
- Konkret und direkt umsetzbar sein
- Jeweils einen kurzen "Warum gut" Hinweis haben, der sich auf SEIN Profil bezieht (nicht generisch)

Format:
1. *[Aufgaben-Name]*
   Beschreibung: …
   Warum gut: …

2. *[Aufgaben-Name]*
   Beschreibung: …
   Warum gut: …

3. *[Aufgaben-Name]*
   Beschreibung: …
   Warum gut: …

Kein [AUFGABE: ...] Tag – das sind nur Vorschläge."""
    user = f"""{zeit_str}
{kontext_str}
{bewertung_str}
Profil der Domina:
  {coach_persona.level_zeile(level)}
  Interessen: {', '.join(domina_profile.get('interessen', [])) or 'nicht angegeben'}
  Ziele: {domina_profile.get('ziele', 'nicht angegeben')}
{coach_persona.sklaven_kontext_block(sklave_profile, domina_profile.get('grenzen', []) or [])}
{nicht_wiederholen_str}{stimmung_str}{feedback_str}{abwechslung_str}"""
    return system, user


async def _limits_gefilterte_vorschlaege(
    raw: str, system: str, prompt: str, sk_hl: list, do_gr: list
) -> tuple[str, list[str]]:
    """Limits-Check (beide Profile). Bei Verletzung: einmal mit verschaerftem
    Prompt re-generieren, danach Verletzer-Vorschlaege herausfiltern."""
    from bot.services import limits_check
    treffer = await limits_check.verletzungen(raw, sk_hl, do_gr)
    if treffer:
        verboten = limits_check.begriffe_zum_verbieten(treffer)
        verschaerft = (
            prompt + "\n\nWICHTIG: Der vorherige Output enthielt VERBOTENE BEGRIFFE: "
            + ", ".join(verboten)
            + ". Diese Begriffe, ihre Synonyme und alles thematisch Verwandte sind ABSOLUT TABU."
        )
        raw = await grok.simple(verschaerft, system=system, reasoning=True)

    vorschlaege = _parse_vorschlaege(raw)
    # Pro-Vorschlag-Filter: einzelne verletzende Vorschlaege verwerfen
    saubere = [v for v in vorschlaege if not await limits_check.verletzungen(v, sk_hl, do_gr)]
    if len(saubere) < len(vorschlaege):
        logger.warning("Inspiration: %d/%d Vorschlaege wegen Grenzen verworfen.",
                       len(vorschlaege) - len(saubere), len(vorschlaege))
    return raw, saubere or vorschlaege  # fallback um Leere zu vermeiden


async def _save_vorschlaege(
    vorschlaege: list[str],
    iteration: int = 1,
    feedback: str = "",
) -> list[str]:
    from bot.services import embeddings as emb
    from bot.services import labels
    point_ids = []
    for v in vorschlaege:
        point_id = str(uuid.uuid4())
        # Kurzlabel fürs "NICHT wiederholen"-Listing – Volltexte im Listenformat
        # ("3. **[Titel]** … Warum gut: …") leaken sonst ein Fremd-Format in die
        # Generator-Prompts und ankern die Formel (Review D7, B1).
        kurzlabel = await labels.kurzlabel(v)
        vector = await emb.get_embedding(v)
        await qdrant.run_io(
            qdrant.client.upsert,
            collection_name="knowledge_base",
            points=[qm.PointStruct(
                id=point_id,
                vector={"text": vector},
                payload={
                    "user_id": qdrant.mandanten_key("domina"),
                    "kategorie": "inspiration",
                    "inhalt": v,
                    "kurzlabel": kurzlabel,
                    "status": "vorgeschlagen",
                    "iteration": iteration,
                    "feedback_kontext": feedback,
                    "erstellt_am": datetime.now(timezone.utc).isoformat(),
                    "qdrant_point_id": point_id,
                },
            )],
        )
        point_ids.append(point_id)
    return point_ids


async def _update_vorschlag_status(point_id: str, status: str, extra: dict = None) -> None:
    payload = {"status": status}
    if extra:
        payload.update(extra)
    await qdrant.run_io(
        qdrant.client.set_payload,
        collection_name="knowledge_base",
        payload=payload,
        points=[point_id],
    )


async def _save_as_vorlage(vorschlag_text: str, point_id: str) -> None:
    import re
    from bot.services import embeddings as emb
    name_match = re.search(r'\*(.+?)\*', vorschlag_text)
    name = name_match.group(1) if name_match else vorschlag_text[:40]

    vorlage_id = str(uuid.uuid4())
    vector = await emb.get_embedding(vorschlag_text)
    await qdrant.run_io(
        qdrant.client.upsert,
        collection_name="knowledge_base",
        points=[qm.PointStruct(
            id=vorlage_id,
            vector={"text": vector},
            payload={
                "user_id": qdrant.mandanten_key("domina"),
                "kategorie": "vorlage",
                "name": name,
                "inhalt": vorschlag_text,
                "quelle": "inspiration",
                "inspiration_point_id": point_id,
                "erstellt_am": datetime.now(timezone.utc).isoformat(),
                "qdrant_point_id": vorlage_id,
            },
        )],
    )
    # point_id kann None sein (Vorschlag ohne gespeicherten Rückverweis, s.
    # _handle_nummer) – dann entfällt nur das Status-Update. Vorher versprach
    # das der Kommentar am Callsite, der Code hätte aber gecrasht (Review D8/A3).
    if point_id:
        await _update_vorschlag_status(point_id, "gemerkt")


async def _lade_letzte_aufgaben_kontext() -> list[str]:
    """Letzte 5 Tasks + abgelehnte Inspirationen als Kontext für Abwechslung."""
    tasks = await qdrant.get_tasks_by_status(
        ["erledigt", "offen", "nicht_erledigt"], limit=5, sort_by_datum=True
    )
    # Mit Zeitabstand – undatierte Listen verleiten das Modell zu erfundenen
    # Zeitbezügen ("gestern" über 4 Tage alte Aufgaben), s. zeiten.alter_label.
    letzte = [
        zeiten.mit_alter_label(t.get("aufgabe", "")[:80], t.get("erteilt_am", ""))
        for t in tasks if t.get("aufgabe")
    ]

    abgelehnte_results, _ = await qdrant.run_io(
        qdrant.client.scroll,
        collection_name="knowledge_base",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=qdrant.mandanten_key("domina"))),
            qm.FieldCondition(key="kategorie", match=qm.MatchValue(value="inspiration")),
            qm.FieldCondition(key="status", match=qm.MatchValue(value="abgelehnt")),
        ]),
        limit=5,
        # Serverseitig sortieren (Review D8/M4): sonst liefert scroll ab >5
        # abgelehnten Einträgen eine willkürliche statt der neuesten Teilmenge.
        order_by=qm.OrderBy(key="erstellt_am", direction="desc"),
        with_payload=True,
        with_vectors=False,
    )
    abgelehnte = [r.payload for r in abgelehnte_results]
    letzte += [
        zeiten.mit_alter_label(p.get("inhalt", "")[:80], p.get("erstellt_am", ""))
        for p in abgelehnte if p.get("inhalt")
    ]
    return letzte


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return

    await update.message.reply_text(t("INSPIRATION_WARTE"))

    domina_profile = await qdrant.get_user_profile("domina") or {}
    sklave_profile = await qdrant.get_user_profile("sklave") or {}
    letzte_aufgaben = await _lade_letzte_aufgaben_kontext()

    raw, vorschlaege = await _generate_vorschlaege(
        domina_profile, sklave_profile, letzte_aufgaben=letzte_aufgaben
    )
    point_ids = await _save_vorschlaege(vorschlaege, iteration=1)

    s = state.get(chat_id)
    s["inspiration_vorschlaege"] = vorschlaege
    s["inspiration_point_ids"] = point_ids
    s["inspiration_iteration"] = 1
    s["inspiration_feedback"] = ""
    state.set_mode(chat_id, "inspiration_wahl")

    await telegram_helper.reply_markdown_safe(update.message, t("INSPIRATION_VORSCHLAEGE", raw=raw))


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    s = state.get(chat_id)
    text = update.message.text.strip().lower()
    mode = s.get("mode")

    if text in ("abbrechen", "/abbrechen"):
        state.set_mode(chat_id, "chat")
        _clear_inspiration_state(s)
        await update.message.reply_text(t("COMMON_ABGEBROCHEN"))
        return

    if mode == "inspiration_wahl":
        await _handle_wahl(update, context, s, text)
    elif mode == "inspiration_nummer":
        await _handle_nummer(update, context, s, text)
    elif mode == "inspiration_feedback":
        await _handle_feedback(update, context, s, text)


async def _handle_wahl(update, context, s, text):
    chat_id = str(update.effective_chat.id)
    if text in synonyme.JA:
        state.set_mode(chat_id, "inspiration_nummer")
        await update.message.reply_text(t("INSPIRATION_NUMMER_FRAGE"))
    elif text in synonyme.NEIN:
        state.set_mode(chat_id, "inspiration_feedback")
        await update.message.reply_text(t("INSPIRATION_FEEDBACK_FRAGE"))
    else:
        await update.message.reply_text(t("COMMON_JA_NEIN"))


async def _handle_nummer(update, context, s, text):
    chat_id = str(update.effective_chat.id)
    vorschlaege = s.get("inspiration_vorschlaege", [])
    point_ids = s.get("inspiration_point_ids", [])

    if text not in ("1", "2", "3"):
        await update.message.reply_text(t("INSPIRATION_NUR_123"))
        return

    idx = int(text) - 1
    if idx >= len(vorschlaege):
        await update.message.reply_text(t("INSPIRATION_UNGUELTIGE_NUMMER"))
        return

    gewaehlter = vorschlaege[idx]
    # point_id kann None sein, wenn _save_vorschlaege für genau diesen Vorschlag
    # scheiterte (z.B. Embedding-Fehler) – der Vorschlag EXISTIERT trotzdem und
    # muss wählbar bleiben (Review D6); die Vorlage wird dann ohne Rückverweis
    # gespeichert, nur das Status-Update entfällt.
    point_id = point_ids[idx] if idx < len(point_ids) else None
    if not point_id:
        logger.warning("Inspiration %s ohne point_id gewählt – Vorlage ohne Rückverweis.", text)

    await _save_as_vorlage(gewaehlter, point_id)
    for i, pid in enumerate(point_ids):
        if i != idx and pid:
            await _update_vorschlag_status(pid, "nicht_gewaehlt")

    state.set_mode(chat_id, "chat")
    _clear_inspiration_state(s)
    await update.message.reply_text(t("INSPIRATION_VORLAGE_GESPEICHERT", nummer=text))


async def _handle_feedback(update, context, s, text):
    chat_id = str(update.effective_chat.id)
    point_ids = s.get("inspiration_point_ids", [])
    iteration = s.get("inspiration_iteration", 1)

    for pid in point_ids:
        if pid:
            await _update_vorschlag_status(pid, "abgelehnt", {"ablehnungsgrund": text})

    domina_profile = await qdrant.get_user_profile("domina") or {}
    level = domina_profile.get("aktuelles_level", 1)

    from bot.prompts import coach_persona, followup as fp
    erklaerung_system = f"""Die Domina hat deine Vorschläge gerade abgelehnt. Reagiere darauf – wie eine vertraute Freundin, nicht wie ein Lehrer der erklären will warum sie unrecht hat.

{coach_persona.fuer_coach_prompt()}

Ein bis drei Sätze. Nimm ihre Begründung ernst, kein Belehren. Wenn dir auffällt was sie hinter ihrer Ablehnung wirklich umtreibt, sprich das an – sonst zeig einfach dass du sie gehört hast.
Kein [AUFGABE: ...] Tag."""
    erklaerung_prompt = (
        f"{fp.nutzer_text('Ihre Begründung', text)}\n"
        f"Ihr aktuelles Level: {level}"
    )

    try:
        erklaerung = await grok.simple(erklaerung_prompt, system=erklaerung_system, reasoning=True)
        if point_ids:
            await qdrant.run_io(
                qdrant.client.set_payload,
                collection_name="knowledge_base",
                payload={"coach_erklaerung": erklaerung},
                points=[point_ids[-1]],
            )
        await update.message.reply_text(t("INSPIRATION_COACH_HINWEIS", erklaerung=erklaerung))
    except Exception as e:
        logger.error("Fehler bei Coach-Erklärung: %s", e)

    await update.message.reply_text(t("INSPIRATION_NEU_GENERIEREN"))

    sklave_profile = await qdrant.get_user_profile("sklave") or {}
    letzte_aufgaben = await _lade_letzte_aufgaben_kontext()
    new_iteration = iteration + 1
    raw, neue_vorschlaege = await _generate_vorschlaege(
        domina_profile, sklave_profile, feedback=text, iteration=new_iteration,
        letzte_aufgaben=letzte_aufgaben,
    )
    new_point_ids = await _save_vorschlaege(neue_vorschlaege, new_iteration, text)

    s["inspiration_vorschlaege"] = neue_vorschlaege
    s["inspiration_point_ids"] = new_point_ids
    s["inspiration_iteration"] = new_iteration
    s["inspiration_feedback"] = text
    state.set_mode(chat_id, "inspiration_wahl")

    await telegram_helper.reply_markdown_safe(update.message, t("INSPIRATION_NEUE_VORSCHLAEGE", raw=raw))


def _clear_inspiration_state(s: dict) -> None:
    for key in ("inspiration_vorschlaege", "inspiration_point_ids",
                "inspiration_iteration", "inspiration_feedback"):
        s.pop(key, None)