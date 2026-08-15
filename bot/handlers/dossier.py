"""
Sklaven-Dossier – verdichtete, erzählerische Charakteristik des Sklaven.

Synthetisiert aus dem strukturierten Lern-Wissen (Vorlieben, persoenlichkeit_tags,
kategorie_reaktionen, kategorie_level, wunsch_kategorien), den letzten echten
Gefühls-Antworten und vergangenen Gesprächen ein kurzes Prosa-Profil ("wer ist er,
was bewegt ihn, wie führt man ihn"). Wird im Sklaven-Profil als `dossier` gespeichert
und fließt in den Sklaven-Prompt (Herrin kennt ihn) UND in den Coach-Prompt ein.

Trigger:
- Wochen-Job `sklave_dossier_job` (So 23:30)
- Manuell: /dossier (Domina)
"""
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from bot import config
from bot.services import paare
from bot.services import qdrant, grok, embeddings as emb, kategorie_logik, persona_config
from bot.messages import t

logger = logging.getLogger(__name__)


def _stimmung_mit_alter(entry: dict | None) -> str:
    """Stimmungs-Zeile fürs Dossier – MIT Altersangabe, wenn der Eintrag nicht
    frisch ist. Ohne die Angabe stünde eine Tage alte Antwort als 'Aktuelle
    Stimmung' im Wochen-Dossier und würde 7 Tage lang alle Prompts prägen
    (Trace 06.07., gleiche Klasse wie der Tiny-Task-Stimmungs-Fix)."""
    if not entry or not entry.get("zusammenfassung"):
        return "Aktuelle Stimmung: unbekannt"
    text = entry["zusammenfassung"]
    try:
        d = datetime.fromisoformat(entry.get("datum", ""))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        tage = (datetime.now(timezone.utc) - d).days
    except (ValueError, TypeError):
        tage = 0
    if tage <= 1:
        return f"Aktuelle Stimmung: {text}"
    return f"Letzte bekannte Stimmung (vor {tage} Tagen, evtl. überholt): {text}"


def _reaktionen_str(profile: dict) -> str:
    reaktionen = profile.get("kategorie_reaktionen", {}) or {}
    teile = []
    for kat, v in reaktionen.items():
        if v and (v.get("positiv", 0) + v.get("neutral", 0) + v.get("negativ", 0)) > 0:
            teile.append(f"{kat} ({v.get('positiv',0)}+/{v.get('neutral',0)}~/{v.get('negativ',0)}-)")
    return ", ".join(teile) if teile else "noch keine Daten"


async def _baue_dossier_text() -> str | None:
    """Erzeugt den Dossier-Text via Grok. None bei Fehler/zu wenig Daten."""
    profile = await qdrant.get_user_profile("sklave") or {}

    vorlieben = profile.get("vorlieben", []) or []
    tags = profile.get("persoenlichkeit_tags", []) or []
    wunsch = profile.get("wunsch_kategorien", []) or []
    entdeckte = profile.get("entdeckte_wuensche", []) or []
    reaktionen_str = _reaktionen_str(profile)

    # Intensitäts-Level je Kategorie
    levels = profile.get("kategorie_level", {}) or {}
    level_str = ", ".join(
        f"{k}: {kategorie_logik.level_label(v)}" for k, v in levels.items()
    ) or "noch keine"

    # Letzte echte Gefühle
    gefuehle = []
    try:
        erledigt = await qdrant.get_tasks_by_status(["erledigt"], sort_by_datum=True)
        # Schleifenvariable NICHT `t` nennen: Datei importiert messages.t –
        # ein späterer t("…")-Aufruf in dieser Funktion gäbe UnboundLocalError.
        for task in erledigt[:8]:
            g = (task.get("gefuehl") or "").strip()
            if g:
                kat = task.get("kategorie", "")
                gefuehle.append(f"{kat}: {g[:120]}" if kat else g[:120])
    except Exception as e:
        logger.error("Dossier: Gefühle laden fehlgeschlagen: %s", e)
    gefuehle_str = "\n".join(f"  • {g}" for g in gefuehle) or "noch keine"

    # Vergangene Gespräche (hybrid über generischen Query)
    gespraeche_str = "keine"
    try:
        qv = await emb.get_embedding("wer ist er, was bewegt ihn, wünsche, gefühle, muster")
        entries = await qdrant.get_hybrid_conversation_context("sklave", qv, limit=8)
        zeilen = [e.get("zusammenfassung", "")[:200] for e in entries if e.get("zusammenfassung")]
        if zeilen:
            gespraeche_str = "\n".join(f"  • {z}" for z in zeilen[:8])
    except Exception as e:
        logger.error("Dossier: Gespräche laden fehlgeschlagen: %s", e)

    stimmung_zeile = _stimmung_mit_alter(await qdrant.get_latest_stimmung("sklave"))

    # Genug Signal, um etwas Sinnvolles zu schreiben?
    if not (vorlieben or tags or wunsch or gefuehle or reaktionen_str != "noch keine Daten"):
        return None

    system = """Erstelle eine knappe, einfühlsame Charakteristik des Sklaven – als internes Profil für seine Herrin, damit sie ihn wirklich kennt und gezielt führen kann.

Schreibe 4–6 Sätze Fließtext, Deutsch, KEINE Aufzählung, kein Markdown, keine Anführungszeichen.
Beschreibe: was ihn antreibt und erregt, worauf er positiv bzw. negativ reagiert, seine (heimlichen) Wünsche, sein emotionales Muster und wie man ihn am besten führt.
NUR aus den Daten unten – erfinde nichts dazu."""
    prompt = f"""Vorlieben: {', '.join(vorlieben) if vorlieben else 'keine'}
Charakter-Muster (Tags): {', '.join(tags) if tags else 'keine'}
Kategorie-Reaktionen: {reaktionen_str}
Wunsch-Kategorien: {', '.join(wunsch) if wunsch else 'keine'}
Im Gespräch geäußerte Wünsche / zum Ausprobieren: {'; '.join(entdeckte) if entdeckte else 'keine'}
Gelernte Intensität je Kategorie: {level_str}
{stimmung_zeile}
Letzte Gefühls-Antworten (seine eigenen Worte):
{gefuehle_str}
Aus früheren Gesprächen:
{gespraeche_str}"""

    try:
        text = grok.clean_text(await grok.simple(prompt, system=system + persona_config.sprache_anweisung(), reasoning=True))
        return text or None
    except Exception:
        logger.exception("Dossier: Grok-Synthese fehlgeschlagen")
        return None


async def aktualisiere_dossier() -> str | None:
    """Baut das Dossier und speichert es im Sklaven-Profil. Gibt den Text zurück."""
    text = await _baue_dossier_text()
    if not text:
        return None
    try:
        await qdrant.patch_profile_fields("sklave", {
            "dossier": text,
            "dossier_am": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        logger.exception("Dossier: Speichern fehlgeschlagen")
    return text


async def wunsch_kontext_hinweis(wuensche: list | None = None) -> str:
    """Gibt eine Prompt-Instruktion zurück, die die entdeckten Wünsche des Sklaven als
    OPTIONALEN Kontext bereitstellt – das LLM erwähnt sie NUR, wenn sie thematisch
    gerade passen (nicht generell, nicht erzwungen). Leerer String wenn keine da."""
    try:
        if wuensche is None:
            wuensche = (await qdrant.get_user_profile("sklave") or {}).get("entdeckte_wuensche", []) or []
        if not wuensche:
            return ""
        liste = "; ".join(wuensche[:6])
        return (
            f"Wünsche, die der Sklave mal angedeutet hat: {liste}.\n"
            f"Erwähne EINEN davon NUR, wenn er thematisch WIRKLICH zum aktuellen Thema passt – "
            f"dann beiläufig (z.B. 'vielleicht wünscht er sich ja mal …'). Im Zweifel oder wenn "
            f"nichts passt: gar nicht erwähnen, nicht erzwingen."
        )
    except Exception:
        return ""


async def aktualisiere_offene_faeden() -> list[str]:
    """Extrahiert aus den jüngsten Sklaven-Gesprächen + Gefühlen 'offene Fäden' –
    Dinge, auf die eine aufmerksame Herrin von sich aus zurückkommen würde
    (erwähnter Stress, ein Wunsch, ein Ereignis, eine Sorge). Speichert sie als
    Profilfeld `offene_faeden`. Liste wird jedes Mal NEU erzeugt (Erledigtes fällt
    so von selbst raus, sobald es nicht mehr in den jüngsten Gesprächen auftaucht)."""
    # Jüngste Gespräche + Gefühle als Quelle
    gespraeche = []
    try:
        qv = await emb.get_embedding("was hat er zuletzt erwähnt, wunsch, sorge, ereignis, stress")
        entries = await qdrant.get_hybrid_conversation_context("sklave", qv, limit=10)
        # NUR die Sklaven-Seite einspeisen. Die Zusammenfassung enthält auch die
        # Herrin-Antwort – daraus destillierte das LLM deren eigene Dauer-Ansagen
        # (Muster: "X bleibt, bis ich Y sage") als 'offene Fäden', die dann per
        # Prompt wieder eingespielt wurden: Themen-Feedback-Schleife (Befund 02.07.).
        import re as _re
        for e in entries:
            text = (e.get("sklave_nachricht") or "").strip()
            if not text:
                z = e.get("zusammenfassung", "")
                text = _re.split(r"\n?\s*Herrin:", z)[0].removeprefix("Sklave:").strip()
            if text:
                gespraeche.append(text[:250])
    except Exception as e:
        logger.error("Offene Fäden: Gespräche laden fehlgeschlagen: %s", e)

    gefuehle = []
    try:
        for task in (await qdrant.get_tasks_by_status(["erledigt"], sort_by_datum=True))[:5]:
            g = (task.get("gefuehl") or "").strip()
            if g:
                gefuehle.append(g[:120])
    except Exception as e:
        logger.error("Offene Fäden: Gefühle laden fehlgeschlagen: %s", e)

    if not gespraeche and not gefuehle:
        return []

    system = """Aus den jüngsten Äußerungen des Sklaven: Worauf würde eine aufmerksame, ihn gut kennende Herrin von sich aus zurückkommen? Gemeint sind OFFENE FÄDEN – erwähnter Stress/Termin, eine Sorge, ein geäußerter Wunsch, ein Ereignis, etwas Unabgeschlossenes, auch geäußerte Langeweile oder ein Wunsch nach Abwechslung/etwas Neuem.

Gib MAXIMAL 4 sehr kurze Stichpunkte (je max. 14 Wörter), jeweils so, dass man konkret darauf zurückkommen kann. Nur was wirklich aus dem Material hervorgeht – nichts erfinden. Nur was ER geäußert hat zählt – Ansagen, Drohungen oder Szenen-Ideen der Herrin sind KEINE offenen Fäden.
DESTILLIERE, zitiere nicht: formuliere in dritter Person über ihn ("will …", "wünscht sich …", "hatte Stress mit …"), übernimm NIE seinen Wortlaut 1:1.
RICHTUNG/ROLLEN IMMER MITNEHMEN: Legt seine Äußerung fest, WER etwas tut oder bekommt oder unter welcher Bedingung ("von der Herrin", "nur bei ihr", "an ihm"), muss der Stichpunkt diese Richtung explizit nennen ("will auch X von der Herrin", nicht nur "will auch X"). Bei Themen, die je nach Richtung Wunsch oder absolutes Tabu sind, ist ein richtungsloser Stichpunkt gefährlich mehrdeutig – im Zweifel die Richtung aus dem Kontext übernehmen oder den Faden weglassen. Der Verlauf einer laufenden Rollenspiel-Szene (was gerade konkret passiert ist oder verlangt wurde) ist KEIN offener Faden – nur, was darüber hinaus offen bleibt. Wenn es nichts Offenes gibt, antworte NUR mit: KEINE.
Ein Stichpunkt pro Zeile, ohne Nummerierung, ohne Markdown."""
    prompt = f"""Jüngste Gespräche:
{chr(10).join('- ' + g for g in gespraeche) or '(keine)'}

Letzte Gefühle:
{chr(10).join('- ' + g for g in gefuehle) or '(keine)'}"""

    try:
        raw = (await grok.simple(prompt, system=system + persona_config.sprache_anweisung(), temperature=0)).strip()  # Extraktion: deterministisch
    except Exception:
        logger.exception("Offene Fäden: Grok fehlgeschlagen")
        return []
    if raw.upper().startswith("KEINE"):
        faeden = []
    else:
        # Zitat-Netz unter der Destillat-Regel (Live-Befund 16.07.: wörtliche
        # Szenen-Äußerungen standen 1:1 als Fäden im Profil und wurden als solche
        # in die Prompts injiziert): wörtliche Übernahmen aus dem Quellmaterial
        # verwerfen – lieber ein Faden weniger als Roh-Szenen-Zitate als "offene Fäden".
        quellen_norm = " | ".join(" ".join(q.lower().split()) for q in gespraeche + gefuehle)
        faeden = []
        for zeile in raw.splitlines():
            z = zeile.strip().lstrip("-•*0123456789. ").strip()
            if not z or z.upper() == "KEINE":
                continue
            if len(z) > 12 and " ".join(z.lower().split()) in quellen_norm:
                logger.info("Offener Faden war wörtliches Zitat – verworfen: %s", z[:60])
                continue
            faeden.append(z[:120])
        faeden = faeden[:4]
    try:
        await qdrant.patch_profile_fields("sklave", {"offene_faeden": faeden})
    except Exception:
        logger.exception("Offene Fäden: Speichern fehlgeschlagen")
    return faeden


async def aktualisiere_domina_dossier() -> str | None:
    """Erzeugt/aktualisiert eine Charakteristik der DOMINA – internes Profil für den
    Coach (beste Freundin), damit er sie als Herrin gut kennt. Speichert als
    Domina-Profilfeld `domina_dossier`."""
    profile = await qdrant.get_user_profile("domina") or {}
    interessen = profile.get("interessen", []) or []
    ziele = profile.get("ziele", "")
    erfahrung = profile.get("erfahrungsstand", "")
    level = profile.get("aktuelles_level", 1)
    grenzen = profile.get("grenzen", []) or []

    if not (interessen or ziele or erfahrung):
        return None

    bewertungs_kontext = ""
    try:
        bewertungs_kontext = await qdrant.get_bewertungs_kontext("sklave") or ""
    except Exception:
        pass
    letzte_kategorien = []
    try:
        letzte_kategorien = await qdrant.get_recent_task_kategorien("sklave", limit=8) or []
    except Exception:
        pass
    gespraeche = []
    try:
        qv = await emb.get_embedding("wie führt sie, ihr stil, was sie reizt, ihre ziele und entwicklung")
        entries = await qdrant.get_hybrid_conversation_context("domina", qv, limit=8)
        gespraeche = [e.get("zusammenfassung", "")[:200] for e in entries if e.get("zusammenfassung")]
    except Exception as e:
        logger.error("Domina-Dossier: Gespräche laden fehlgeschlagen: %s", e)

    system = """Erstelle eine knappe, warmherzige Charakteristik der Domina – als internes Profil für ihren Coach (eine vertraute beste Freundin), damit er sie als Herrin wirklich kennt und gezielt begleiten kann.

Schreibe 4–6 Sätze Fließtext, Deutsch, KEINE Aufzählung, kein Markdown, keine Anführungszeichen.
Beschreibe: ihren Stil als Herrin, was sie reizt und interessiert, wie sie führt (sanft/streng/spielerisch …), ihre Ziele und ihre Entwicklung, und worauf der Coach bei ihr achten sollte. NUR aus den Daten – nichts erfinden."""
    prompt = f"""Erfahrungsstand: {erfahrung or 'unbekannt'}
Level: {level}
Interessen: {', '.join(interessen) if interessen else 'keine'}
Ziele: {ziele or 'keine'}
Persönliche Grenzen: {', '.join(grenzen) if grenzen else 'keine'}
Was ihr an Aufgaben gut gefiel: {bewertungs_kontext or 'keine Daten'}
Zuletzt vergebene Kategorien: {', '.join(letzte_kategorien) if letzte_kategorien else 'keine'}
Aus früheren Coach-Gesprächen:
{chr(10).join('  • ' + g for g in gespraeche) or '  (keine)'}"""

    try:
        text = grok.clean_text(await grok.simple(prompt, system=system + persona_config.sprache_anweisung(), reasoning=True))
    except Exception:
        logger.exception("Domina-Dossier: Grok-Synthese fehlgeschlagen")
        return None
    if not text:
        return None
    try:
        await qdrant.patch_profile_fields("domina", {
            "domina_dossier": text,
            "domina_dossier_am": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        logger.exception("Domina-Dossier: Speichern fehlgeschlagen")
    return text


# Signalwörter, die auf einen geäußerten Wunsch / "mal ausprobieren" hindeuten.
# Nur bei Treffer wird ein (kostenpflichtiger) Grok-Extraktor angeworfen.
_WUNSCH_SIGNALE = (
    "würde gern", "würde gerne", "möchte gern", "möchte mal", "ausprobier", "probier",
    "fantasie", "wünsch", "hätte gern", "hätte lust", "lust auf", "reizt mich",
    "neugierig", "mal testen", "träum", "vorstellen", "stelle mir vor", "interessier",
    "freuen würde", "würde mich freuen", "freuen", "schön wäre", "wäre schön",
    "darf ich", "dürfen", "ich will", "ich mag", "sehne", "gefallen würde",
)


async def erfasse_wunsch_aus_chat(text: str) -> str | None:
    """Erkennt im freien Sklaven-Chat einen geäußerten Wunsch / etwas zum Ausprobieren
    und speichert ihn dauerhaft im Profilfeld `entdeckte_wuensche`.
    Gated über Signalwörter (spart Grok-Calls). Hard-Limit-verletzende Wünsche werden
    NICHT gespeichert. Gibt den neuen Wunsch zurück oder None."""
    tl = (text or "").lower()
    # Erkennen über Signalwörter ODER bei wirklich langen, inhaltlichen Nachrichten –
    # dann entscheidet der Extraktor selbst (antwortet "KEINE" wenn kein Wunsch).
    # Schwelle bewusst hoch (120): bei 25 löste praktisch jede Nachricht einen
    # zusätzlichen Grok-Call aus und das Signalwort-Gating war wirkungslos.
    if not (any(s in tl for s in _WUNSCH_SIGNALE) or len(text.strip()) >= 120):
        return None

    from bot.prompts import followup as fp
    system = (
        "Hat der Sklave in dieser Nachricht einen WUNSCH oder etwas geäußert, das er gern "
        "ausprobieren würde? Wenn ja, gib es als EINEN kurzen Stichpunkt zurück (max. 12 Wörter, "
        "aus seiner Perspektive, z.B. 'würde gern mal X ausprobieren'). Hat die Praktik eine "
        "RICHTUNG (wer gibt, wer empfängt), benenne sie ausdrücklich mit ('ihren …', 'von der "
        "Herrin', 'eigenen …') statt sie wegzulassen – die Richtung entscheidet über Limits. "
        "Wenn nein oder unklar, antworte NUR mit KEINE. Kein Markdown, keine Anführungszeichen."
    )
    try:
        w = grok.clean_text(await grok.simple(
            fp.nutzer_text("Nachricht", text[:500]), system=system, temperature=0,
        ))  # Extraktion: deterministisch
    except Exception:
        logger.exception("Wunsch-Extraktion (Grok) fehlgeschlagen")
        return None
    if not w or w.upper().startswith("KEINE") or len(w) < 4:
        return None

    prof = await qdrant.get_user_profile("sklave") or {}
    # Hard-Limits + Domina-Grenzen niemals als Wunsch verankern
    from bot.services import limits_check
    hl = prof.get("hard_limits", []) or []
    gr = (await qdrant.get_user_profile("domina") or {}).get("grenzen", []) or []
    # sprecher="sub": der Wunsch ist aus SEINER Perspektive formuliert – "ihr X"
    # ist die Seite der Herrin und verletzt ein "X des Subs"-Limit NICHT
    # (Live-Befund 15.07.: ein legitimer Wunsch wurde richtungs-blind verworfen).
    if await limits_check.verletzungen(w, hl, gr, sprecher="sub"):
        logger.info("Entdeckter Wunsch grenzverletzend – nicht gespeichert.")
        return None

    bestehende = prof.get("entdeckte_wuensche", []) or []
    # Dedup: normalisierte Ähnlichkeit (fängt auch "an Anus" vs "an deinem Anus")
    import difflib
    def _norm(x: str) -> str:
        return " ".join(x.lower().split())
    wn = _norm(w)
    if any(difflib.SequenceMatcher(None, wn, _norm(b)).ratio() > 0.8 for b in bestehende):
        return None
    bestehende = (bestehende + [w])[-15:]
    try:
        await qdrant.patch_profile_fields("sklave", {"entdeckte_wuensche": bestehende})
    except Exception:
        logger.exception("Wunsch speichern fehlgeschlagen")
        return None
    return w


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/dossier – Domina: aktualisiert und zeigt die Charakteristik des Sklaven."""
    chat_id = str(update.effective_chat.id)
    if chat_id != paare.dom_chat_id():
        return
    await update.message.reply_text(t("DOSSIER_WARTE"))
    text = await aktualisiere_dossier()
    if not text:
        await update.message.reply_text(t("DOSSIER_ZU_WENIG"))
        return
    from bot.services import telegram_helper
    await telegram_helper.send_domina(context.bot, t("DOSSIER_PREFIX", text=text), parse_mode="Markdown")
