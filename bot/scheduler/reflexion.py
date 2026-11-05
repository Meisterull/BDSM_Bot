"""
Sonntags-Kaskade: Gesprächs-Cache, Lerntagebuch, Profil-Pflege, Coach-Reflexion.

Aus bot/scheduler/followup.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
"""
import logging
from datetime import datetime, timezone, timedelta
from telegram import Bot
from bot.services import paare
from bot.services import qdrant, grok, limits_check
from bot.messages import t
from bot.scheduler import basis

logger = logging.getLogger(__name__)


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
    start_dt, end_dt = basis._parse_datum(start_iso), basis._parse_datum(end_iso)
    gefiltert = [
        e for e in cache["entries"]
        if start_dt <= basis._parse_datum(e.get("datum", "")) <= end_dt
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


@basis._job_guard
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
        if basis._nach_llm_verworfen(paare.dom_chat_id(), "Profil-Pflege"):
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


@basis._job_guard
@basis._nachholbar(max_stunden=26)
async def profil_pflege_job(bot: Bot) -> None:
    """Alle 2 Wochen (gerade ISO-Wochen) – Auto-Profil-Pflege."""
    if not basis._zweiwochen_takt():
        logger.info("Profil-Pflege-Job übersprungen – ungerade ISO-Woche (2-Wochen-Takt).")
        return
    # Nicht in einen aktiven Domina-Flow platzen (D9/N8); der manuelle
    # /profilpflege-Pfad (coach_regeln) bleibt bewusst ungeguardet.
    if basis._flow_aktiv(paare.dom_chat_id(), "Profil-Pflege"):
        return
    await generiere_profil_vorschlaege(bot, days=14)


@basis._job_guard
@basis._nachholbar(max_stunden=26)
async def coach_reflexion_job(bot: Bot) -> None:
    """Alle 2 Wochen – Coach reflektiert ueber sich selbst.

    Schaut sich die Domina-Coach-Gespraeche der letzten 14 Tage an, sucht
    Muster (kurze Folge-Antworten, Korrekturen, wiederkehrende Bitten) und
    schlaegt 1–3 neue Regeln vor, die im Coach-Prompt aktiv werden sollen.

    Die Vorschlaege werden als 'pending' gespeichert und der Domina mit
    Ja/Nein-Buttons zur Bestaetigung geschickt – keine stillen Updates.
    Läuft nur in geraden ISO-Wochen (2-Wochen-Takt)."""
    if not basis._zweiwochen_takt():
        logger.info("Coach-Reflexion-Job übersprungen – ungerade ISO-Woche (2-Wochen-Takt).")
        return
    if basis._flow_aktiv(paare.dom_chat_id(), "Coach-Reflexion"):  # D9/N8
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
        if basis._nach_llm_verworfen(paare.dom_chat_id(), "Coach-Reflexion"):
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
