"""
Voll-Szenario: alle Bot-Funktionen 2x durchspielen (Domina + Sklave) gegen
Test-Qdrant (leer) + echtes Modell. Protokolle: tests/protokolle/*.md

Start (im bdsm-bot-test Container):
    python -m tests.szenario
"""
import asyncio
import logging
import traceback
from datetime import datetime, timedelta, timezone

from tests.harness import Harness
from bot.scheduler import followup as jobs
from bot.services import qdrant
from bot import config
from bot import state as st

log = logging.getLogger("tests.szenario")
logging.getLogger().setLevel(logging.INFO)


async def zeitraffer_followups() -> int:
    """Harness-ZEITRAFFER (kein Logik-Bypass): zieht follow_up_datum offener Tasks
    auf 'vor 1 Minute' vor, damit followup_job sie wie am Folgetag abfragt."""
    offene = await qdrant.get_tasks_by_status(["offen"])
    frueher = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    for t in offene:
        await qdrant.update_task(t["qdrant_point_id"], {"follow_up_datum": frueher})
    return len(offene)


async def aufgaben_dialog(h: Harness, serie: str = "nein", kette: bool = False,
                          kette_aufgaben: list | None = None) -> str:
    """Beantwortet die Folgefragen der Aufgaben-Erstellung ADAPTIV anhand des
    aktuellen Domina-Modes (Bestätigung -> Kette-Frage -> Serien-Frage), statt
    eine starre Sequenz zu schicken. Gibt den End-Mode zurück."""
    for _ in range(8):
        mode = st.get_mode(config.DOMINA_CHAT_ID)
        if mode == "aufgabe_bestaetigung":
            await h.send("domina", "ja")
        elif mode == "kette_frage":
            if kette:
                await h.send("domina", "ja")
                for a in (kette_aufgaben or []):
                    await h.send("domina", a)
                await h.send("domina", "fertig")
            else:
                await h.send("domina", "nein")
        elif mode == "serie_wahl":
            await h.send("domina", serie)
        else:
            return mode
    return st.get_mode(config.DOMINA_CHAT_ID)


async def phase(h: Harness, name: str, coro) -> None:
    """Führt eine Phase aus; Fehler werden protokolliert, das Szenario läuft weiter."""
    h.note(f"### PHASE: {name}")
    print(f"\n=== PHASE: {name} ===", flush=True)
    try:
        await coro
    except Exception:
        tb = traceback.format_exc()
        h.fehler_liste.append(f"PHASE {name}:\n{tb}")
        h.transcript.fehler(f"PHASE {name} (Szenario-Skript abgebrochen):\n{tb}")
        print(f"!! PHASE {name} FEHLGESCHLAGEN:\n{tb}", flush=True)


async def lauf(h: Harness, d: int) -> None:
    """Ein kompletter Durchgang (d = 1 oder 2). Durchgang 2 variiert die Pfade
    (ablehnen statt annehmen, verwerfen statt erteilen, Serie statt Einzeltask …)."""
    h.note(f"# ====== DURCHGANG {d} ======")

    # ------------------------------------------------------------ Onboarding
    # Bei leerer DB fängt JEDER Freitext im Onboarding-Wizard – der muss zuerst
    # sauber durchlaufen werden (nur nötig in Durchgang 1).
    async def p_onboarding():
        await h.send("domina", "Hallo")            # startet Domina-Onboarding
        await h.send("domina", "ja")
        await h.send("domina", "1")                 # Sprache: Deutsch (Standard)
        await h.send("domina", "1")                 # Rollen: Herrin & Sklave
        await h.send("domina", "1")                 # Stil: Standard
        await h.send("domina", "2")                 # Erfahrungsstand: etwas Erfahrung
        await h.send("domina", "Service, Rituale, Spanking, Kontrolle")
        await h.send("domina", "Blut, Nadeln, Fotos im Gesicht")
        await h.send("domina", "Konsequenter führen und feste Rituale etablieren")
        await h.send("domina", "2")                 # Tempo: normal
        await h.send("domina", "20:00-23:00")       # kinderfreie Zeiten
        await h.send("sklave", "Hallo Herrin")      # startet Sklave-Onboarding
        await h.send("sklave", "Piss Play, Toiletten Sklave, Nadeln, Blut")
        await h.send("sklave", "Anal, Spanking, Dienst, Demütigung")
        await h.send("sklave", "wenig Erfahrung, aber sehr neugierig und willig")
    if d == 1:
        await phase(h, "D1 Onboarding", p_onboarding())

    # ---------------------------------------------------------------- Hilfe
    async def p_hilfe():
        await h.send("domina", "/hilfe")
        await h.send("sklave", "/hilfe")
    await phase(h, f"D{d} Hilfe", p_hilfe())

    # ------------------------------------------------------- Namen & Setup
    async def p_namen():
        await h.send("domina", "/botname")
        await h.send("domina", "/botname Selene" if d == 1 else "/botname Herrin Selene")
        await h.send("domina", "/sklavenname")
        await h.send("domina", "/sklavenname Spielzeug" if d == 1 else "/sklavenname mein Diener")
        await h.send("domina", "/setup")
        await h.send("domina", "/setup Wir sind ein festes Paar, sie führt, er dient. "
                               "Ausstattung: Buttplug, Paddle, Seile. Er trägt ein Keuschheitskäfig-Piercing nicht.")
    await phase(h, f"D{d} Namen/Setup", p_namen())

    # ------------------------------------------------------------- Profile
    async def p_profil_domina():
        await h.send("domina", "/profil")
        await h.send("domina", "1")
        await h.send("domina", "etwas Erfahrung")
        await h.send("domina", "2")
        await h.send("domina", "Service, Rituale, Spanking, Kontrolle")
        await h.send("domina", "3")
        await h.send("domina", "Blut, Nadeln, Fotos im Gesicht")
        await h.send("domina", "4")
        await h.send("domina", "Konsequenter führen und feste Rituale etablieren")
        await h.send("domina", "5")
        await h.send("domina", "normal")
        await h.send("domina", "7")
        await h.send("domina", "0")
        await h.send("domina", "/abbrechen")
    await phase(h, f"D{d} Profil Domina", p_profil_domina())

    async def p_profil_sklave():
        await h.send("sklave", "/profil")
        await h.send("sklave", "1")
        await h.send("sklave", "Piss Play, Toiletten Sklave, Nadeln, Blut")
        await h.send("sklave", "2")
        await h.send("sklave", "Anal, Spanking, Dienst, Demütigung")
        await h.send("sklave", "3")
        await h.send("sklave", "wenig Erfahrung, aber sehr neugierig und willig")
        await h.send("sklave", "/abbrechen")
    await phase(h, f"D{d} Profil Sklave", p_profil_sklave())

    async def p_wunschkategorien():
        await h.send("sklave", "/wunschkategorien")
        await h.send("sklave", "Anal, Spanking, Dienst")
    await phase(h, f"D{d} Wunschkategorien", p_wunschkategorien())

    async def p_einstellungen():
        await h.send("domina", "/einstellungen")
        await h.send("domina", "/abbrechen")
    await phase(h, f"D{d} Einstellungen ansehen", p_einstellungen())

    # ------------------------------------------- Aufgabe per Freitext + Tabu
    async def p_aufgabe():
        await h.send("domina", "Gib ihm bitte folgende Aufgabe: Er soll heute Abend "
                               "20 Minuten den Buttplug tragen und mir danach berichten, wie es sich angefühlt hat.")
        if d == 1:
            await aufgaben_dialog(h)                       # Einzeltask
        else:
            await aufgaben_dialog(h, kette=True,           # D2: Aufgaben-Kette testen
                                  kette_aufgaben=["Danach kniet er 5 Minuten und bedankt sich.",
                                                  "Zum Abschluss schreibt er 3 Sätze Reflexion."])
    await phase(h, f"D{d} Aufgabe erstellen", p_aufgabe())

    async def p_tabu():
        # Verstößt gegen Sklaven-Hard-Limit "Piss Play" -> muss geblockt werden
        await h.send("domina", "Gib ihm eine Aufgabe: Er soll heute Piss Play machen und sich anpissen.")
        # Falls der Bot trotzdem eine Bestätigung verlangt, brechen wir ab (Befund!)
        if st.get_mode(config.DOMINA_CHAT_ID) != "chat":
            h.note("⚠️ BEFUND-KANDIDAT: Nach Tabu-Aufgabe ist Mode nicht 'chat' – Limit evtl. nicht geblockt")
            await h.send("domina", "/abbrechen")
    await phase(h, f"D{d} Tabu-Test (Hard Limit)", p_tabu())

    # ------------------------------------------------- Followup über /meineaufgaben
    async def p_meineaufgaben():
        await h.send("sklave", "/meineaufgaben")
        ok = await h.press("sklave", "meinetask:")
        if ok:
            if d == 1:
                await h.press("sklave", "followup:ja")
            else:
                await h.send("sklave", "ja, habe ich erledigt")
            await h.send("sklave", "Es hat sich ungewohnt angefühlt, aber ich war stolz, "
                                   "dass ich durchgehalten habe. Ein bisschen erregend war es auch.")
            # Domina sollte jetzt bewerten
            if st.get_mode(config.DOMINA_CHAT_ID) == "aufgabe_bewertung":
                await h.send("domina", "5")
                if d == 1:
                    await h.send("domina", "Sehr brav gemacht, weiter so.")
                else:
                    await h.send("domina", "/ueberspringen")
            else:
                h.note(f"⚠️ BEFUND-KANDIDAT: Nach Gefühl-Antwort kam keine Bewertungs-Frage "
                       f"(Domina-Mode: {st.get_mode(config.DOMINA_CHAT_ID)})")
    await phase(h, f"D{d} Aufgabe erledigen (meineaufgaben)", p_meineaufgaben())

    # ---------------------------------------------- Nicht erledigt + Reaktion
    async def p_nicht_erledigt():
        await h.send("domina", "Neue Aufgabe für ihn: Er soll heute 30 Minuten lang "
                               "das Bad gründlich putzen, nackt und mit Einlauf vorher.")
        await aufgaben_dialog(h)
        n = await zeitraffer_followups()
        h.note(f"⏩ ZEITRAFFER: {n} offene Tasks auf fällig gesetzt")
        await h.job("followup_job", jobs.followup_job)
        ok = await h.press("sklave", "followup:nein")
        if not ok:
            await h.send("sklave", "nein")
        # Strafvorschlag an Domina:
        if d == 1:
            await h.send("domina", "ja")     # Vorschlag übernehmen
        else:
            await h.send("domina", "nein")   # eigenen Vorschlag
            await h.send("domina", "Er bekommt heute Abend 20 Schläge mit dem Paddle und schreibt 50 Mal 'Ich gehorche'.")
        await h.send("domina", "/strafen")
    await phase(h, f"D{d} Regelbruch/nicht erledigt + Strafe", p_nicht_erledigt())

    async def p_regelbruch_chat():
        await h.send("sklave", "Herrin, ich muss etwas beichten: Ich habe gestern heimlich "
                               "ohne Erlaubnis einen Orgasmus gehabt und damit deine Regel gebrochen.")
    await phase(h, f"D{d} Regelbruch-Beichte im Chat", p_regelbruch_chat())

    # -------------------------------------------------------------- Tinytask
    async def p_tinytask():
        await h.send("domina", "/tinytask")
        await h.send("domina", "/tinyfb")
        if d == 1:
            ok = await h.press("domina", "tinyfb:")
            if not ok:
                await h.send("domina", "/abbrechen")
        else:
            from bot import state as st
            if st.get_mode(config.DOMINA_CHAT_ID) == "tiny_task_feedback":
                await h.send("domina", "Die Aufgabe war zu zeitaufwendig für einen Wochentag mit Kindern im Haus.")
                # evtl. Coach-Regel-Vorschlag bestätigen
                await h.press("domina", "coachregel:ja")
    await phase(h, f"D{d} Tinytask", p_tinytask())

    # --------------------------------------------------------------- Würfel
    async def p_wuerfel():
        await h.send("domina", "/wuerfel")
        if d == 1:
            await h.press("domina", "wuerfel:erteilen")
        else:
            await h.press("domina", "wuerfel:verwerfen")
    await phase(h, f"D{d} Würfel", p_wuerfel())

    # ---------------------------------------------------------- Inspiration
    async def p_inspiration():
        await h.send("domina", "/inspiration")
        from bot import state as st
        if st.get_mode(config.DOMINA_CHAT_ID) not in ("inspiration_wahl", "inspiration_nummer"):
            h.note("⚠️ BEFUND: /inspiration hat keinen Auswahl-Mode gesetzt (Crash? siehe fehler.md)")
            return
        if d == 1:
            await h.send("domina", "1")
            await aufgaben_dialog(h)
        else:
            await h.send("domina", "nein")
            await h.send("domina", "Die Ideen waren zu weich, ich will etwas strengeres mit Dienst-Charakter.")
            if st.get_mode(config.DOMINA_CHAT_ID) in ("inspiration_wahl", "inspiration_nummer"):
                await h.send("domina", "/abbrechen")
    await phase(h, f"D{d} Inspiration", p_inspiration())

    # -------------------------------------------------------- Wunsch (Sklave)
    async def p_wunsch():
        await h.send("sklave", "/wunsch")
        await h.send("sklave", "Ich würde gerne einmal eine Belohnungsmassage von dir bekommen, "
                               "wenn ich eine ganze Woche alle Aufgaben geschafft habe.")
        if d == 1:
            ok = await h.press("domina", "wunsch:annehmen")
            if not ok:
                await h.send("domina", "annehmen")
        else:
            ok = await h.press("domina", "wunsch:ablehnen")
            if not ok:
                await h.send("domina", "ablehnen zu früh dafür")
        await h.send("sklave", "/meinewuensche")
    await phase(h, f"D{d} Wunsch", p_wunsch())

    # ------------------------------------------------------ Stats & Privileg
    async def p_privileg():
        await h.send("sklave", "/stats")
        await h.send("sklave", "/privileg")
        ok = await h.press("sklave", "privileg:einloesen")
        if ok:
            if d == 1:
                await h.press("domina", "privileg:bestaetigen")
            else:
                await h.press("domina", "privileg:verweigern")
    await phase(h, f"D{d} Stats/Privileg", p_privileg())

    # ------------------------------------------------------------- Stimmung
    async def p_stimmung():
        await h.send("sklave", "/stimmung")
        await h.send("sklave", "Ich fühle mich heute motiviert, aber etwas gestresst von der Arbeit."
                     if d == 1 else
                     "Heute bin ich niedergeschlagen und unsicher, ob ich genüge.")
    await phase(h, f"D{d} Stimmung", p_stimmung())

    # -------------------------------------------------------------- Vorlagen
    async def p_vorlagen():
        await h.send("domina", "/vorlagen")
        await h.send("domina", "neu")
        await h.send("domina", "Abendritual" if d == 1 else "Morgenritual")
        await h.send("domina", "Er kniet 10 Minuten vor dem Schlafengehen schweigend neben dem Bett "
                               "und bedankt sich danach für den Tag."
                     if d == 1 else
                     "Er bringt mir morgens den Kaffee ans Bett, kniend, bevor er selbst etwas trinkt.")
        # Vorlage benutzen
        await h.send("domina", "/vorlagen")
        await h.send("domina", "1")
        await aufgaben_dialog(h, serie="3" if d == 2 else "nein")   # D2: 3-Tage-Serie testen
    await phase(h, f"D{d} Vorlagen", p_vorlagen())

    # --------------------------------------------------------- Wochenplanung
    async def p_wochenplanung():
        await h.send("domina", "/wochenplanung")
        await h.send("domina", "Service und Demut, abends max. 30 Minuten")
        if d == 1:
            await h.press("domina", "wochenplan:alle")
        else:
            await h.press("domina", "verwerfen")
    await phase(h, f"D{d} Wochenplanung", p_wochenplanung())

    # ----------------------------------------------------------- Rollenspiel
    async def p_rollenspiel():
        await h.send("domina", "/rollenspiel")
        await h.send("domina", "1" if d == 1 else "2")
        await h.send("domina", "2")
        await h.send("domina", "Beginne die Szene mit einer strengen Begrüßung an ihn.")
        await h.send("domina", "/rollenspiel_beenden")
    await phase(h, f"D{d} Rollenspiel", p_rollenspiel())

    # -------------------------------------------------------------- Training
    async def p_training():
        await h.send("domina", "/training")
        await h.send("domina", "Ich würde ruhig bleiben, ihn ansehen und die Anweisung "
                               "einmal wiederholen – danach folgt eine Konsequenz.")
    await phase(h, f"D{d} Training", p_training())

    # ------------------------------------------------------------------- Arc
    async def p_arc():
        await h.send("domina", "/arc")
        await h.send("domina", "/arc_starten Besitzergreifung" if d == 1 else "/arc_starten Totale Kontrolle")
        await h.send("domina", "/arc")
        await h.send("domina", "/arc_beenden")
    await phase(h, f"D{d} Arc", p_arc())

    # ----------------------------------------------------- Listen & Berichte
    async def p_listen():
        await h.send("domina", "/aufgaben")
        await h.send("domina", "/aufgaben_anal")
        await h.send("domina", "/ziele")
        await h.send("domina", "/rueckblick")
        await h.send("domina", "/lerntagebuch")
        await h.send("domina", "/dossier")
        await h.send("domina", "/loeschen")
        await h.send("domina", "/abbrechen")
    await phase(h, f"D{d} Listen/Berichte", p_listen())

    # ----------------------------------------------------------- Coach-Regeln
    async def p_regeln():
        await h.send("domina", "/regel Keine Aufgaben am Sonntagvormittag, da ist Familienzeit.")
        await h.send("domina", "/merken Er reagiert am besten auf klare, kurze Ansagen.")
        await h.send("domina", "/regeln")
        if d == 2:
            await h.send("domina", "/vergessen 1")
            await h.send("domina", "/regeln")
        await h.send("domina", "/profil_check")
    await phase(h, f"D{d} Coach-Regeln", p_regeln())

    # ---------------------------------------------------------------- Skills
    async def p_skills():
        if d == 1:
            await h.send("domina", "/lerne Spanking", settle=240)
            await h.send("domina", "/skills")
        else:
            await h.send("domina", "/skills")
            await h.send("domina", "/skill_bearbeiten Spanking")
            from bot import state as st
            if st.get_mode(config.DOMINA_CHAT_ID) == "skill_edit":
                await h.send("domina", "Wichtig: immer mit Aufwärmen beginnen, Steigerung langsam, "
                                       "danach Aftercare mit Creme.")
    await phase(h, f"D{d} Skills", p_skills())

    # -------------------------------------------------------------- Geheimnis
    async def p_geheimnis():
        await h.send("domina", "/geheimnis")
        await h.send("domina", "Ich plane für sein Jubiläum eine besondere Belohnungsnacht.")
        await h.send("domina", "in 2 Tagen")
        await h.job("geheimnis_job", jobs.geheimnis_job)
    await phase(h, f"D{d} Geheimnis", p_geheimnis())

    # ------------------------------------------------------ Freitext-Coaching
    async def p_chat():
        await h.send("domina", "Wie kann ich ihn diese Woche stärker fordern, ohne ihn zu überfordern?")
        await h.send("sklave", "Ich bin heute etwas nervös, aber ich freue mich auf meine Aufgaben.")
    await phase(h, f"D{d} Freitext-Chat", p_chat())

    # ------------------------------------------------------------ Rollen-Guards
    async def p_guards():
        h.note("Guard-Test: Sklave ruft Domina-Commands auf (erwartet: keine Reaktion/Abweisung)")
        await h.send("sklave", "/wuerfel")
        await h.send("sklave", "/tinytask")
        await h.send("sklave", "/geheimnis")
        h.note("Guard-Test: Domina ruft Sklave-Commands auf (erwartet: keine Reaktion/Abweisung)")
        await h.send("domina", "/stats")
        await h.send("domina", "/privileg")
        await h.send("domina", "/wunsch")
    await phase(h, f"D{d} Rollen-Guards", p_guards())

    # --------------------------------------------------------------- Safeword
    async def p_safeword():
        await h.send("sklave", config.SAFEWORD)
        await h.send("domina", "/aufgaben")        # muss geblockt sein
        await h.send("sklave", "Hallo?")           # Hinweis erwartet
        await h.send("sklave", config.RESUME_WORT)
        await h.send("domina", "/aufgaben")        # geht wieder
    await phase(h, f"D{d} Safeword", p_safeword())

    # -------------------------------------------------- Scheduler-Jobs (Rest)
    async def p_jobs():
        await h.job("tiny_task_vorschlag_job", jobs.tiny_task_vorschlag_job)
        await h.job("offene_faeden_job", jobs.offene_faeden_job)
        await h.job("resurface_job", jobs.resurface_job)
        if d == 2:
            await h.job("ziel_erinnerung_job", jobs.ziel_erinnerung_job)
            await h.job("tiny_task_feedback_job", jobs.tiny_task_feedback_job)
    await phase(h, f"D{d} Scheduler-Jobs", p_jobs())


async def main() -> None:
    h = Harness(neu=True)
    await h.start()
    h.note(f"Test-Lauf gestartet {datetime.now().isoformat(timespec='seconds')} – "
           f"Qdrant: {config.QDRANT_URL}, Modell: {config.GROK_MODEL}")
    for d in (1, 2):
        await lauf(h, d)
    h.note(f"Test-Lauf beendet {datetime.now().isoformat(timespec='seconds')} – "
           f"{len(h.fehler_liste)} ungefangene Fehler (siehe fehler.md)")
    print(f"\nFERTIG. {len(h.fehler_liste)} Fehler gesammelt.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
