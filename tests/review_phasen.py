"""Phasen des Review-Szenarios (wird von tests/review_szenario.py geladen).

Jede Phase spielt einen Ablauf über das Testgerüst durch (h.send / h.press /
h.job). `w` ist das Hauptmodul: w.OVERRIDES nimmt einmalige LLM-Antworten auf
((Regex auf System+User, Antwort)), w.kn() zeigt die zuletzt angebotenen Knöpfe,
w.zeitraffer_followups() stellt offene Aufgaben fällig.

Neue Abläufe einfach als weitere Phase anhängen; mit dem Filter-Argument des
Sandbox-Skripts lassen sich einzelne Phasen (Präfix der Nummer) gezielt fahren.
"""
import random
from datetime import datetime, timedelta, timezone

from bot import config
from bot import state as st
from bot.scheduler import aufgaben, tagesjobs, tiny_task, wartung, reflexion, impulse, luecken
from bot.services import qdrant
from bot.services.db import tasks as db_tasks

D, S = "domina", "sklave"


def modi():
    return f"D={st.get_mode(config.DOMINA_CHAT_ID)} S={st.get_mode(config.SKLAVE_CHAT_ID)}"


async def alle(h, phase, w):

    async def dialog(max_schritte: int = 8) -> str:
        """Folgefragen der Aufgaben-Erstellung beantworten (Bestätigung, Termin, Kette, Serie)."""
        for _ in range(max_schritte):
            mode = st.get_mode(config.DOMINA_CHAT_ID)
            if mode == "aufgabe_bestaetigung":
                await h.send(D, "ja")
            elif mode == "aufgabe_termin":
                await h.send(D, "sofort")
            elif mode == "kette_frage":
                await h.send(D, "nein")
            elif mode == "serie_wahl":
                await h.send(D, "nein")
            else:
                return mode
        return st.get_mode(config.DOMINA_CHAT_ID)

    async def neue_aufgabe(text: str) -> str:
        await h.send(D, f"Gib ihm bitte folgende Aufgabe: {text}")
        return await dialog()

    async def aufraeumen():
        await h.send(D, "/abbrechen")
        await h.send(S, "/abbrechen")

    async def schliesse_offene():
        for t in await db_tasks.get_tasks_by_status(["offen", "gefragt"]):
            await db_tasks.update_task(t["qdrant_point_id"], {"status": "erledigt"})
        st.set_mode(config.SKLAVE_CHAT_ID, "chat")
        st.set_mode(config.DOMINA_CHAT_ID, "chat")

    async def faellig():
        n = await w.zeitraffer_followups()
        await h.job("followup_job", aufgaben.followup_job)
        return n

    # ------------------------------------------------ 01 Aufgabe erteilen
    async def p01():
        await aufraeumen()
        await schliesse_offene()
        await neue_aufgabe("Er soll heute Abend das Bad gründlich putzen und mir danach Bescheid geben.")
    await phase(h, "01 Aufgabe erteilen", p01)

    # ------------------------------------------------ 02 Follow-up erledigt
    async def p02():
        await faellig()
        if not await h.press(S, "followup:ja"):
            await h.send(S, "ja, habe ich erledigt")
        await h.send(S, "Es war anstrengend, aber ich habe es gern für dich gemacht und war danach stolz.")
    await phase(h, "02 Follow-up erledigt + Gefühl", p02)

    # ------------------------------------------------ 03 Bewertung
    async def p03():
        await h.send(D, "5")
        await h.send(D, "Sehr brav gemacht, genau so will ich das öfter sehen.")
        await h.press(D, "coachregel:ja")
    await phase(h, "03 Bewertung + Kommentar", p03)

    # ------------------------------------------------ 04 zweite offene Aufgabe (die echte) + niedrige Bewertung
    async def p04():
        await aufraeumen()
        await schliesse_offene()
        await neue_aufgabe("Er soll heute Abend die Wäsche zusammenlegen.")
        await faellig()
        if not await h.press(S, "followup:ja"):
            await h.send(S, "ja")
        await h.send(S, "Langweilig")
        await h.send(D, "2")
        await h.send(D, "/ueberspringen")
    await phase(h, "04 Follow-up + Gefühl negativ + Bewertung 2", p04)

    # ------------------------------------------------ 05 nicht erledigt → Strafe
    async def p05():
        await aufraeumen()
        await schliesse_offene()
        await neue_aufgabe("Er soll heute 20 Minuten lang still neben mir knien, während ich lese.")
        await faellig()
        if not await h.press(S, "followup:nein"):
            await h.send(S, "nein")
        await h.press(S, "herrin:ich")
        await h.send(D, "ja")
    await phase(h, "05 nicht erledigt + Strafe bestätigen", p05)

    async def p06():
        await aufraeumen()
        await schliesse_offene()
        await neue_aufgabe("Er soll mir heute Abend zehn Minuten die Schultern massieren.")
        await faellig()
        if not await h.press(S, "followup:nein"):
            await h.send(S, "nein")
        await h.press(S, "herrin:vergessen")
        await h.press(D, "herrinfehl:")
    await phase(h, "06 nicht erledigt, lag an der Herrin", p06)

    async def p07():
        await aufraeumen()
        await schliesse_offene()
        await neue_aufgabe("Er soll heute Abend den Tisch decken und abräumen.")
        w.OVERRIDES.append((r"JA oder NEIN", "NEIN"))   # diese Aufgabe braucht die Herrin NICHT
        await faellig()
        if not await h.press(S, "followup:nein"):
            await h.send(S, "nein")
        await h.send(D, "nein")
        await h.send(D, "Er bekommt heute Abend zwölf Hiebe mit dem Paddle und bedankt sich für jeden einzelnen.")
        await h.send(D, "/strafen")
    await phase(h, "07 nicht erledigt + eigene Strafe", p07)

    # ------------------------------------------------ 08 Tiny-Task + Feedback
    async def p08():
        await aufraeumen()
        await h.send(D, "/tinytask")
        await h.send(D, "/tinyfb")
        await h.press(D, "tinyfb:nein")
        await h.press(D, "g_thema")
    await phase(h, "08 Tiny-Task + Feedback Knopf", p08)

    async def p09():
        await aufraeumen()
        await h.send(D, "/tinytask")
        await h.send(D, "/tinyfb")
        await h.press(D, "tinyfb:nein")
        await h.press(D, "tinyfb:text")
        await h.send(D, "Die Aufgabe war zu zeitaufwendig für einen Wochentag mit Kindern im Haus.")
        await h.press(D, "coachregel:ja")
    await phase(h, "09 Tiny-Task Feedback Freitext", p09)

    # ------------------------------------------------ 10 Würfel, Roulette, Inspiration
    async def p10():
        await aufraeumen()
        await h.send(D, "/wuerfel")
        await h.press(D, "wuerfel:erteilen")
        await dialog()
    await phase(h, "10 Würfel erteilen", p10)

    async def p11():
        await aufraeumen()
        await h.send(D, "/roulette")
        await h.press(D, "roulette:erteilen")
        await h.press(D, "roulette:ja")
    await phase(h, "11 Roulette", p11)

    async def p12():
        await aufraeumen()
        await h.send(D, "/inspiration")
        await h.send(D, "nein")
        await h.send(D, "Die Ideen waren mir zu weich, ich will etwas Strengeres.")
        await h.send(D, "1")
        await dialog()
    await phase(h, "12 Inspiration + Feedback", p12)

    # ------------------------------------------------ 13 Wunsch, Privileg
    async def p13():
        await aufraeumen()
        await h.send(S, "/wunsch")
        await h.send(S, "Ich würde mir wünschen, dass wir am Wochenende einen Abend nur für uns einplanen.")
        await h.press(D, "wunsch:annehmen")
        await h.send(D, "Gern, Samstagabend gehört uns.")
        await h.send(S, "/wunsch")
        await h.send(S, "Darf ich am Samstag mit Freunden weggehen?")
        await h.press(D, "wunsch:ablehnen")
        await h.send(D, "Nein, das hast du dir noch nicht verdient.")
    await phase(h, "13 Wunsch annehmen/ablehnen", p13)

    async def p14():
        await aufraeumen()
        await h.send(S, "/privileg")
        await h.press(S, "privileg:einloesen")
        await h.press(D, "privileg:bestaetigen")
        await h.send(D, "Viel Spaß damit.")
        await h.send(S, "/privileg")
        await h.press(S, "privileg:einloesen")
        await h.press(D, "privileg:verweigern")
        await h.send(D, "Heute nicht.")
    await phase(h, "14 Privileg", p14)

    # ------------------------------------------------ 15 Wochenplan
    async def p15():
        await aufraeumen()
        await h.send(D, "/wochenplanung")
        await h.send(D, "Abwechslung, abends höchstens 30 Minuten")
        await h.press(D, "wochenplan:alle")
        await dialog()
    await phase(h, "15 Wochenplan übernehmen", p15)

    # ------------------------------------------------ 16 Rollenspiel
    async def p16():
        await aufraeumen()
        await h.send(D, "/rollenspiel")
        await h.send(D, "1")
        await h.send(D, "2")
        await h.send(D, "Beginne die Szene mit einer strengen Begrüßung an ihn.")
        # Erreicht das Rollenspiel die Sub-Seite? Chat und eine Aufgabe währenddessen.
        await schliesse_offene()
        st.set_mode(config.DOMINA_CHAT_ID, "rollenspiel_aktiv")
        await h.send(S, "Ich bin bereit und warte auf deine Fragen.")
        await h.send(D, "/wuerfel")
        await h.press(D, "wuerfel:erteilen")
        await dialog()
        await h.send(D, "/rollenspiel_beenden")
    await phase(h, "16 Rollenspiel", p16)

    # ------------------------------------------------ 17 Training, Arc, Skills, Geheimnis
    async def p17():
        await aufraeumen()
        config.TRAINING_ENABLED = True
        await h.send(D, "/training")
        await h.press(D, "training:antwort")     # Antworten geht nur noch über den Knopf
        await h.send(D, "Ich würde ruhig bleiben, ihn ansehen und die Anweisung einmal knapp wiederholen.")
    await phase(h, "17 Training", p17)

    async def p18():
        await aufraeumen()
        await h.send(D, "/arc_starten Besitzergreifung")
        await h.send(D, "/arc")
        await h.job("tiny_task_vorschlag_job", tiny_task.tiny_task_vorschlag_job)
        await h.send(D, "/arc_beenden")
    await phase(h, "18 Arc", p18)

    async def p19():
        await aufraeumen()
        await h.send(D, "/lerne Spanking", settle=240)
        await h.send(D, "/skills")
    await phase(h, "19 Wissens-Brief", p19)

    async def p20():
        await aufraeumen()
        await h.send(D, "/geheimnis")
        await h.send(D, "Ich plane für unseren Jahrestag einen besonderen Abend.")
        await h.send(D, "in 1 Tag")
        # Zeitraffer: Enthüllung fällig stellen
        from bot.services.db import kern
        pts, _ = kern.client.scroll(collection_name="geheimnisse", limit=10, with_payload=True)
        for p in pts:
            neu = {k: (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
                   for k, v in p.payload.items() if isinstance(v, str) and k not in ("text", "user_id", "status", "qdrant_point_id") and v[:4] == "2026"}
            kern.client.set_payload(collection_name="geheimnisse", payload=neu, points=[p.id])
        await h.job("geheimnis_job", tagesjobs.geheimnis_job)
    await phase(h, "20 Geheimnis", p20)

    # ------------------------------------------------ 21 Quiz
    async def p21():
        await aufraeumen()
        await h.send(S, "/quiz")
        await h.send(S, "Du magst es, wenn ich widerspreche, weil du dann einen Grund zum Strafen hast.")
    await phase(h, "21 Quiz Sub", p21)

    async def p22():
        await aufraeumen()
        w.OVERRIDES.append((r"RICHTIG.{0,40}TEILWEISE.{0,40}FALSCH", "FALSCH"))
        await h.send(S, "/quiz")
        await h.send(S, "Keine Ahnung, vielleicht Schokolade?")
    await phase(h, "22 Quiz Sub falsch", p22)

    async def p23b():
        await aufraeumen()
        await h.send(D, "/quiz sklave")
        await h.send(D, "/abbrechen")
    await phase(h, "23b Quiz Domina über den Sub", p23b)

    async def p23():
        await aufraeumen()
        await h.send(D, "/quiz")
        await h.send(D, "Vorher besprechen, zudecken, Wasser, und die Auswertung erst später machen.")
    await phase(h, "23 Quiz Domina", p23)

    # ------------------------------------------------ 24 Herrin-Wette
    async def p24():
        await aufraeumen()
        from bot.handlers.waehrung import kern as wkern
        from bot.handlers import waehrung as waehrung_h
        await qdrant.patch_profile_fields("sklave", {wkern.FELD_WETTE: {}})
        await h.send(D, "/wette")
        await h.press(D, "wettidee:senden")
        await h.press(S, "wetteantwort:annehmen")
        prof = await qdrant.get_user_profile("sklave")
        wt = dict(prof.get(wkern.FELD_WETTE) or {})
        print("   Wette nach Annahme:", wt.get("status"), "| Felder:", sorted(wt))
        wt["frist"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        await qdrant.patch_profile_fields("sklave", {wkern.FELD_WETTE: wt})
        await h.job("wette_urteil_job", waehrung_h.wette_urteil_job)
        print("   S:", w.kn(h, S), modi())
        await h.press(S, "wetteurteil:gewonnen")
        print("   nach Urteil: D", w.kn(h, D), "| S", w.kn(h, S), modi())
        await h.press(D, "wetteeinspruch:")
        print("   Ende: D", w.kn(h, D), modi())
    await phase(h, "24 Herrin-Wette annehmen + Urteil", p24)

    async def p25():
        await aufraeumen()
        from bot.handlers.waehrung import kern as wkern
        await qdrant.patch_profile_fields("sklave", {wkern.FELD_WETTE: {}})
        await h.send(D, "/wette Küche")
        await h.press(D, "wettidee:neu")
        await h.press(D, "wettidee:senden")
        await h.press(S, "wetteantwort:ablehnen")
        print("   nach Ablehnung: D", w.kn(h, D), "| S", w.kn(h, S), modi())
        await h.press(D, "wetteablehnung:strafe") or await h.press(D, "wetteablehnung:")
        print("   nach wetteablehnung: D", w.kn(h, D), modi())
        await h.send(D, "Er räumt eine Woche lang jeden Abend allein die Küche auf.")
        print("   Ende: D", w.kn(h, D), modi())
    await phase(h, "25 Herrin-Wette ablehnen + Strafe", p25)

    async def p26():
        await aufraeumen()
        await h.send(S, "/wette")
        print("   S:", w.kn(h, S), modi())
        await h.press(S, "wette:")
        print("   Ende:", w.kn(h, S), modi())
    await phase(h, "26 Punkte-Wette Sub", p26)

    # ------------------------------------------------ 27 Blitz
    async def p27():
        await aufraeumen()
        from bot.handlers import blitz
        print("   gesendet:", await blitz.sende_blitz(h.bot), "| S", w.kn(h, S))
        await h.press(S, "blitz:fertig")
        print("   zweite:", await blitz.sende_blitz(h.bot))
        for t in await db_tasks.get_tasks_by_status(["offen"]):
            if t.get("blitz_deadline"):
                await db_tasks.update_task(t["qdrant_point_id"], {
                    "blitz_deadline": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()})
        await h.job("blitz_ablauf_job", impulse.blitz_ablauf_job)
    await phase(h, "27 Blitz fertig + verpasst", p27)

    # ------------------------------------------------ 28 Dauer
    async def p28():
        await aufraeumen()
        await h.send(D, "/dauer 3 Das Halsband tragen, bis ich es abnehme")
        random.random = lambda: 0.0     # Zwischen-Check erzwingen
        await h.job("dauer_job", impulse.dauer_job)
        for t in await db_tasks.get_tasks_by_status(["offen"]):
            if t.get("dauer_bis"):
                await db_tasks.update_task(t["qdrant_point_id"], {
                    "dauer_bis": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()})
        await h.job("dauer_job", impulse.dauer_job)
        print("   Ende: S", w.kn(h, S), modi())
    await phase(h, "28 Dauer-Anweisung", p28)

    # ------------------------------------------------ 29 Sprachnachricht, Kette, Serie
    async def p29():
        await aufraeumen()
        await h.send(D, "Sag ihm bitte: Ich freue mich auf heute Abend und er soll pünktlich sein.")
        print("   nach Sag-ihm:", w.kn(h, D), modi())
        await h.press(D, "snaufgabe:")
    await phase(h, "29 Sprachnachricht", p29)

    async def p30():
        await aufraeumen()
        await schliesse_offene()
        await h.send(D, "Gib ihm bitte folgende Aufgabe: Er soll heute das Schlafzimmer vorbereiten.")
        for _ in range(8):
            mode = st.get_mode(config.DOMINA_CHAT_ID)
            if mode == "aufgabe_bestaetigung":
                await h.send(D, "ja")
            elif mode == "aufgabe_termin":
                await h.send(D, "sofort")
            elif mode == "kette_frage":
                await h.send(D, "ja")
                await h.send(D, "Danach die Kerzen anzünden und auf mich warten.")
                await h.send(D, "Zum Schluss kniet er sich neben das Bett.")
                await h.send(D, "fertig")
            elif mode == "serie_wahl":
                await h.send(D, "nein")
            else:
                break
        print("   Kette angelegt:", modi())
        await faellig()
        if not await h.press(S, "followup:ja"):
            await h.send(S, "ja")
        w.OVERRIDES.append((r"JSON", '{"stimmung": "überfordert", "intensitaet": "hoch", "kategorie": "Dienst", "tags": []}'))
        await h.send(S, "Das war mir heute viel zu viel, ich war total überfordert.")
        print("   nach negativem Gefühl: D", w.kn(h, D), modi())
        await h.press(D, "ketteanpass:")
        print("   Ende: D", w.kn(h, D), modi())
    await phase(h, "30 Kette + adaptive Anpassung", p30)

    async def p31():
        await aufraeumen()
        await h.send(D, "Gib ihm bitte folgende Aufgabe: Er soll jeden Abend zehn Minuten meine Füße eincremen.")
        for _ in range(8):
            mode = st.get_mode(config.DOMINA_CHAT_ID)
            if mode == "aufgabe_bestaetigung":
                await h.send(D, "ja")
            elif mode == "aufgabe_termin":
                await h.send(D, "sofort")
            elif mode == "kette_frage":
                await h.send(D, "nein")
            elif mode == "serie_wahl":
                await h.send(D, "3")
            else:
                break
        print("   Serie:", modi())
    await phase(h, "31 Serie", p31)

    # ------------------------------------------------ 32 Meine Aufgaben
    async def p32():
        await aufraeumen()
        await h.send(S, "/meineaufgaben")
        print("   S:", w.kn(h, S), modi())
        await h.press(S, "meinetask:")
        print("   Ende: S", w.kn(h, S), modi())
        await h.send(S, "/abbrechen")
    await phase(h, "32 Meine Aufgaben", p32)

    # ------------------------------------------------ 33 Sub-Chat: Wunsch, Vorliebe, Retries
    async def p33():
        await aufraeumen()
        await schliesse_offene()
        w.OVERRIDES.append((r"Wunsch|wünsch", "würde gern mal gemeinsam ein Wochenende wegfahren"))
        await h.send(S, "Herrin, ich würde gern mal ein ganzes Wochenende nur dir gehören, ohne Handy und ohne Ablenkung, das wünsche ich mir schon lange.")
    await phase(h, "33 Sub-Chat mit Wunsch", p33)

    async def p34():
        await aufraeumen()
        await schliesse_offene()
        w.OVERRIDES.append((r"Du sprichst direkt mit dem Sklaven", "Du willst also heute Abend früh ins Bett. Du möchtest Ruhe."))
        await h.send(S, "Ich bin heute müde und will eigentlich nur früh ins Bett, darf ich das?")
        w.OVERRIDES.append((r"Du sprichst direkt mit dem Sklaven", "Das ist notiert und fest in deinen Vorlieben verankert."))
        await h.send(S, "Kannst du bitte bei meinen Vorlieben eintragen, dass ich gern gekrault werde?")
        w.OVERRIDES.append((r"Du sprichst direkt mit dem Sklaven", "Du wirst sofort, wortlos und bedingungslos gehorchen."))
        await h.send(S, "Was hast du heute Abend mit mir vor? Ich bin schon ganz gespannt darauf.")
        await h.send(S, "Du schreibst irgendwie immer dasselbe, das wird langsam langweilig.")
    await phase(h, "34 Sub-Chat Retry-Zweige", p34)

    async def p35():
        await aufraeumen()
        w.OVERRIDES.append((r"Du begleitest eine Domina", "Er wünscht sich ja schon lange mehr Strenge, das hat er mir erzählt. Genau sein Ding."))
        await h.send(D, "Was meinst du, was ihm heute Abend gefallen würde?")
        await h.send(D, "Ich mag es übrigens sehr, wenn er mir abends unaufgefordert Tee bringt.")
        print("   Ende: D", w.kn(h, D), modi())
    await phase(h, "35 Coach-Chat Tarn-Retry + Vorliebe", p35)

    # ------------------------------------------------ 36 Limits-/Tarn-Retry im Generator
    async def p36():
        await aufraeumen()
        # Der Text muss ein im Profil eingetragenes Limit berühren (hier das häufige „Blut"),
        # sonst löst er den Limit-Retry nicht aus.
        w.OVERRIDES.append((r"Tiny Task", "Lass ihn heute bluten, ein kleiner Schnitt mit Blut reicht. Das trifft genau seine Vorliebe."))
        await h.send(D, "/tinytask")
    await phase(h, "36 Generator: Limit- und Tarn-Retry", p36)

    # ------------------------------------------------ 37 Stille-Check-in
    async def p37():
        await aufraeumen()
        from bot.handlers import stille_checkin
        async def _alt():
            return datetime.now(timezone.utc) - timedelta(days=9)
        stille_checkin.letzte_domina_eingabe = _alt
        await h.job("stille_checkin_job", luecken.stille_checkin_job)
        print("   D:", w.kn(h, D), modi())
        await h.send(D, "Ehrlich gesagt passen mir die Vorschläge oft nicht, sie sind mir zu aufwendig.")
        print("   Ende: D", w.kn(h, D), modi())
    await phase(h, "37 Stille-Check-in + Freitext", p37)

    # ------------------------------------------------ 38 Reflexions- und Analyse-Jobs
    async def p38():
        await aufraeumen()
        from bot.scheduler import basis
        basis._zweiwochen_takt = lambda: True
        for name, fn in (("lerntagebuch_job", reflexion.lerntagebuch_job),
                         ("coach_reflexion_job", reflexion.coach_reflexion_job),
                         ("profil_pflege_job", reflexion.profil_pflege_job),
                         ("lernkurve_job", tagesjobs.lernkurve_job),
                         ("kommentar_analyse_job", tagesjobs.kommentar_analyse_job),
                         ("resurface_job", tagesjobs.resurface_job)):
            vor = w.ZAEHLER["n"]
            await h.job(name, fn)
            print(f"   {name}: {w.ZAEHLER['n'] - vor} Prompts | D {w.kn(h, D)}")
        await h.press(D, "resurface:")
    await phase(h, "38 Reflexion/Analyse-Jobs", p38)

    async def p39():
        await aufraeumen()
        random.random = lambda: 0.0
        for name, fn in (("luecken_check_job", luecken.luecken_check_job),
                         ("luecken_zustellung_job", luecken.luecken_zustellung_job),
                         ("spiel_impuls_job", impulse.spiel_impuls_job),
                         ("coach_impuls_job", impulse.coach_impuls_job),
                         ("blitz_check_job", impulse.blitz_check_job),
                         ("event_check_job", impulse.event_check_job),
                         ("kalender_job", impulse.kalender_job),
                         ("tiny_task_feedback_job", tiny_task.tiny_task_feedback_job)):
            vor = w.ZAEHLER["n"]
            await h.job(name, fn)
            print(f"   {name}: {w.ZAEHLER['n'] - vor} Prompts | D {w.kn(h, D)} | S {w.kn(h, S)}")
    await phase(h, "39 Impuls-/Lücken-Jobs", p39)

    # ------------------------------------------------ 40 Mini-App-Training, Wissens-Kurzfassung
    async def p40():
        from bot.services.miniapp import training as mt
        from bot.services import paare
        from bot.handlers.training import TRAINING_TYPEN
        typ = list(TRAINING_TYPEN)[0]
        print("   Trainings-Typen:", list(TRAINING_TYPEN))
        r = await mt._psycho_antwort(paare.alle_paare()[0].paar_id, typ,
                                     "Er zögert bei einer Anweisung. Was tust du?",
                                     "Ich wiederhole sie ruhig und warte, bis er gehorcht.")
        print("   Ergebnis-Felder:", sorted(r))
    await phase(h, "40 Mini-App-Training", p40)

    # ------------------------------------------------ 41 Vanilla-Modus
    async def p41():
        from bot.services import persona_config
        from bot.scheduler import vanilla as vjobs
        await schliesse_offene()
        await persona_config.set_modus("vanilla")
        print("   Modus:", persona_config.modus())
        await h.send(D, "Ich hätte Lust, am Wochenende mal wieder etwas nur zu zweit zu machen.")
        await h.send(S, "Mir war heute alles zu viel, ich brauche einen ruhigen Abend.")
        for name, fn in (("vanilla_impuls_job", vjobs.vanilla_impuls_job),
                         ("vanilla_followup_job", vjobs.vanilla_followup_job),
                         ("vanilla_wochenplan_job", vjobs.vanilla_wochenplan_job)):
            vor = w.ZAEHLER["n"]
            await h.job(name, fn)
            print(f"   {name}: {w.ZAEHLER['n'] - vor} Prompts | D {w.kn(h, D)} | S {w.kn(h, S)}")
        for rolle in (D, S):
            for cmd in ("/wuerfel", "/quiz", "/wochenplanung", "/stimmung", "/wunsch"):
                vor = w.ZAEHLER["n"]
                await h.send(rolle, cmd)
                print(f"   {rolle} {cmd}: {w.ZAEHLER['n'] - vor} Prompts | {modi()} | {w.kn(h, rolle)[:80]}")
                await h.send(rolle, "/abbrechen")
        await h.send(S, "/stimmung")
        await h.send(S, "Ziemlich müde, aber zufrieden.")
        await persona_config.set_modus("bdsm")
    await phase(h, "41 Vanilla-Modus", p41)

    # ------------------------------------------------ 42 Kommentar der Dom-Seite + Wochen-Analyse
    async def p42():
        await aufraeumen()
        await schliesse_offene()
        for aufgabe, gefuehl, sterne, kommentar in (
            ("Er soll heute Abend das Schlafzimmer herrichten.", "Es hat mir gefallen, alles für dich vorzubereiten.",
             "4", "Das Zimmer war ordentlich, aber die Kerzen haben gefehlt."),
            ("Er soll mir einen Kaffee ans Bett bringen.", "Ich war nervös, ob alles passt.",
             "5", "Genau so, pünktlich und ohne Aufforderung."),
        ):
            await neue_aufgabe(aufgabe)
            await faellig()
            if not await h.press(S, "followup:ja"):
                await h.send(S, "ja")
            await h.send(S, gefuehl)
            await h.send(D, sterne)
            await h.send(D, kommentar)
            await h.press(D, "coachregel:nein")
            await schliesse_offene()
        await aufraeumen()
        vor = w.ZAEHLER["n"]
        await h.job("kommentar_analyse_job", tagesjobs.kommentar_analyse_job)
        print(f"   kommentar_analyse_job: {w.ZAEHLER['n'] - vor} Prompts")
        from bot.services.db import kern
        pts, _ = kern.client.scroll(collection_name="tasks", limit=300, with_payload=True)
        print("   gespeicherte Kommentare:", [p.payload.get("domina_kommentar", "")[:60] for p in pts if p.payload.get("domina_kommentar")][-3:])
    await phase(h, "42 Kommentar + Kommentar-Analyse", p42)

    # ------------------------------------------------ 43 Adventskalender
    async def p43():
        from datetime import date
        from bot.handlers import advent
        await aufraeumen()
        await schliesse_offene()
        await h.send(D, "/adventskalender Vorfreude")
        await h.send(D, "/adventskalender")
        jahr = advent._ziel_jahr()
        for tag in (3, 12, 18, 24):
            advent._heute = lambda tag=tag: date(jahr, 12, tag)
            vor = w.ZAEHLER["n"]
            await advent.oeffne_tuerchen(h.bot)
            print(f"   Türchen {tag}: {w.ZAEHLER['n'] - vor} Prompts")
        await h.send(D, "/adventskalender stop")
    await phase(h, "43 Adventskalender", p43)

    # ------------------------------------------------ 44 Event-Storyline
    async def p44():
        import json as _json
        await aufraeumen()
        await schliesse_offene()
        ziel = datetime.now() + timedelta(days=2)
        await h.send(D, f"/event {ziel.day}.{ziel.month}. 3 Unser Jahrestag")
        await h.send(D, "/event")
        w.OVERRIDES.append((r"Storyline", _json.dumps([
            {"titel": f"Tag {i}", "aufgabe": f"Platzhalter-Aufgabe für Tag {i} der Storyline.", "kategorie": "Psycho", "level": 3}
            for i in (1, 2, 3)], ensure_ascii=False)))
        vor = w.ZAEHLER["n"]
        await h.job("event_check_job", impulse.event_check_job)
        print(f"   event_check_job: {w.ZAEHLER['n'] - vor} Prompts | {modi()}")
        vor = w.ZAEHLER["n"]
        await h.job("tiny_task_vorschlag_job", tiny_task.tiny_task_vorschlag_job)
        print(f"   Storyline-Tag über tiny_task_vorschlag_job: {w.ZAEHLER['n'] - vor} Prompts")
        await h.send(D, "/arc")
        await h.send(D, "/arc_beenden")
    await phase(h, "44 Event-Storyline", p44)

    # ------------------------------------------------ 45 Psycho-Training (alle Arten, Job, Knopf, Dossier, Mini-App)
    async def p45():
        from bot.handlers import dossier, training
        from bot.services import paare
        from bot.services.miniapp import training as mt
        config.TRAINING_ENABLED = True
        await aufraeumen()
        await schliesse_offene()
        # 1) jede Übungsart einmal rendern (ohne Versand)
        for typ in training.TRAINING_TYPEN:
            await training.neue_uebung(typ)
        # 2) Zeit-Job: die Übung kommt an, ohne den Chat zu belegen
        vor = w.ZAEHLER["n"]
        await h.job("training_job", tagesjobs.training_job)
        print(f"   training_job: {w.ZAEHLER['n'] - vor} Prompts | {modi()} | Knöpfe D: {w.kn(h, D)}")
        # 3) ein Auftrag direkt danach bleibt ein Auftrag
        await h.send(D, "Gib ihm bitte folgende Aufgabe: Er soll heute Abend die Küche aufräumen.")
        print(f"   nach Auftrag: {modi()}")
        await dialog()
        await aufraeumen()
        await schliesse_offene()
        # 4) freie Nachricht ohne Knopf: der Coach kennt die Übung aus dem Verlauf
        await h.send(D, "Das mit der Übung probiere ich morgen aus.")
        # 5) Knopf → Antwort → Rückmeldung am selben Eintrag
        gedrueckt = await h.press(D, "training:antwort")
        print(f"   Knopf gedrückt: {gedrueckt} | {modi()}")
        await h.send(D, "Mir ist aufgefallen, dass ich zu schnell nachgebe, wenn er bettelt.")
        print(f"   nach Antwort: {modi()}")
        eintraege = await qdrant.get_training_entries("domina", limit=3)
        print("   jüngste Einträge (typ, Antwort?, Rückmeldung?, quelle):",
              [(e.get("typ"), bool(e.get("antwort")), bool(e.get("feedback")), e.get("quelle")) for e in eintraege])
        # 6) Übung auf Abruf
        await h.send(D, "/training")
        print(f"   nach /training: {modi()}")
        # 7) Dossier der Dom-Seite – jetzt mit ihrer Trainings-Antwort
        await dossier.aktualisiere_domina_dossier()
        # 8) Mini-App: Übung, Antwort am selben Eintrag, Kategorie-Training
        pid = paare.alle_paare()[0].paar_id
        r = await mt._psycho_uebung(pid, "")
        print("   Mini-App-Übung:", r.get("typ"), "| ID dabei:", bool(r.get("id")))
        await mt._psycho_antwort(pid, r["typ"], r["uebung"], "Das nehme ich mir für Samstag vor.", r.get("id", ""))
        await mt._training_erstellen(pid, "Spanking")
        config.TRAINING_ENABLED = False
    await phase(h, "45 Psycho-Training", p45)

    # ------------------------------------------------ 46 Stimmungsfrage mit passenden Antwort-Knöpfen
    async def p46():
        optionen = ('{"antworten": [{"knopf": "Eher ruhig", "klartext": "gelassen, wenig los"},'
                    '{"knopf": "Ziemlich voll", "klartext": "unruhig, viel im Kopf"},'
                    '{"knopf": "Mal so, mal so", "klartext": "gemischt"},'
                    '{"knopf": "Weder noch", "klartext": "müde, eher leer"}]}')
        muster = r"Antworten zum Antippen\. Du schreibst"
        frage_muster = r"Formuliere jetzt genau EINE solche Frage"
        # Eigene Fragetexte je Runde: der Platzhalter-Satz des Szenarios gilt dem
        # Wiederholungs-Detektor sonst als „zu ähnlich" (→ Standardfrage ohne Knöpfe zur Frage).
        fragen = ["Ist dein Kopf heute eher ein stiller See oder ein voller Bahnhof?",
                  "Wie viel Kraft steckt jetzt noch in dir – voller Akku oder letzter Strich?",
                  "Fühlt sich der Tag bisher leicht an oder eher wie ein Rucksack voller Steine?"]
        config.STIMMUNG_ENABLED = True
        await aufraeumen()
        await schliesse_offene()
        # 1) Frage mit vier passenden Antworten → Tipp auf eine davon
        w.OVERRIDES.append((frage_muster, fragen[0]))
        w.OVERRIDES.append((muster, optionen))
        await h.job("stimmung_job", tagesjobs.stimmung_job)
        print(f"   Frage 1: {modi()} | Knöpfe S: {w.kn(h, S)}")
        gedrueckt = await h.press(S, "Ziemlich voll")
        e = await qdrant.get_latest_stimmung("sklave") or {}
        print(f"   Tipp: {gedrueckt} | gespeichert: {e.get('zusammenfassung')!r} | Knopf: {e.get('knopf')!r} | Frage dabei: {bool(e.get('frage'))}")
        # 2) neue Frage, aber getippte Antwort
        w.OVERRIDES.append((frage_muster, fragen[1]))
        w.OVERRIDES.append((muster, optionen))
        await h.job("stimmung_job", tagesjobs.stimmung_job)
        await h.send(S, "eher müde, aber ganz zufrieden")
        e = await qdrant.get_latest_stimmung("sklave") or {}
        print(f"   getippt: {e.get('zusammenfassung')!r} | Frage dabei: {bool(e.get('frage'))} | {modi()}")
        # 3) unbrauchbare Ausgabe (Platzhalter-JSON) → Nachbesserung, dann feste Knöpfe
        w.OVERRIDES.append((frage_muster, fragen[2]))
        vor = w.ZAEHLER["n"]
        await h.job("stimmung_job", tagesjobs.stimmung_job)
        print(f"   Rückfall: {w.ZAEHLER['n'] - vor} Prompts | Knöpfe S: {w.kn(h, S)}")
        await h.press(S, "stimmung:4")
        e = await qdrant.get_latest_stimmung("sklave") or {}
        print(f"   fester Knopf: {e.get('zusammenfassung')!r}")
        config.STIMMUNG_ENABLED = False
    await phase(h, "46 Stimmungsfrage", p46)

    # ------------------------------------------------ 47 Coach kennt, was sonst im Chat der Dom-Seite steht
    async def p47():
        dom_chat = config.DOMINA_CHAT_ID

        def gemerkt() -> list[str]:
            return [" ".join(e["text"].split())[:44] for e in st.gesendet_offen(dom_chat)]

        def erwarte_vorspann(muster: str) -> int:
            """Greift nur, wenn der Vorspann mit diesem Inhalt beim Modell ankommt."""
            w.OVERRIDES.append((r"Zur Einordnung.*" + muster, "Du meinst das von eben, oder?"))
            return len(w.OVERRIDES)

        await aufraeumen()
        await schliesse_offene()
        await qdrant.patch_profile_fields("sklave", {"wette": {}})     # Kopie der Live-Daten: keine laufende Wette
        await neue_aufgabe("Er soll heute Abend den Tisch decken.")
        st.gesendet_abhaken(dom_chat, 9e12)                            # Vorgeschichte interessiert hier nicht
        # 1) Er wettet → Info an sie → sie antwortet mit einem einzigen Wort
        await h.send(S, "/wette")
        gedrueckt = await h.press(S, "wette:setzen")
        print(f"   gewettet: {gedrueckt} | gemerkt: {gemerkt()}")
        n = erwarte_vorspann("gewettet")
        vor = w.ZAEHLER["n"]
        await h.send(D, "Einverstanden")
        print(f"   Ein-Wort-Antwort: Vorspann mit der Wett-Info beim Modell: {len(w.OVERRIDES) < n} | "
              f"{w.ZAEHLER['n'] - vor} Prompts | danach gemerkt: {gemerkt()} | {modi()}")
        verlauf = st.get_history(dom_chat)[-2:]
        print(f"   im Verlauf: {[(m['role'], m['content'][:40]) for m in verlauf]}")
        # 2) Folge-Nachricht ohne neue Info → kein Vorspann mehr
        n = erwarte_vorspann(".")
        await h.send(D, "Alles klar, danke dir.")
        print(f"   Folge-Nachricht ohne Vorspann: {len(w.OVERRIDES) == n} | gemerkt: {gemerkt()}")
        w.OVERRIDES.pop()
        # 3) Auftrag mit Sprachnachricht: Bestätigung und Angebot stehen daneben, seine Antwort nicht
        await h.send(D, "Sag ihm bitte: Er soll um neun bereitstehen.")
        print(f"   nach Auftrag gemerkt: {gemerkt()} | {modi()} | Knöpfe D: {w.kn(h, D)}")
        n = erwarte_vorspann("Auftrag")
        await h.send(D, "Ja, mach das")
        print(f"   getippte Antwort auf das Angebot: Vorspann beim Modell: {len(w.OVERRIDES) < n} | {modi()}")
        if len(w.OVERRIDES) == n:
            w.OVERRIDES.pop()
        await aufraeumen()
        await schliesse_offene()
        await qdrant.patch_profile_fields("sklave", {"wette": {}})
    await phase(h, "47 Coach kennt automatische Nachrichten", p47)
