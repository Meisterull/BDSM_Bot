"""
Deutsche UI-/Flow-Texte (Referenz-Locale).

Aus bot/messages.py extrahiert (Veröffentlichungs-Schritt 2, 2026-07-02).
Struktur/Namespaces: siehe bot/messages.py. Diese Datei ist die REFERENZ –
andere Locales (en.py, …) müssen exakt dieselben Keys und {platzhalter}
tragen; fehlende Keys fallen zur Laufzeit auf Deutsch zurück
(bot/locales/__init__.lade). Konsistenz sichert tests/test_locales.py.
"""

MESSAGES = {
    # --- Gemeinsame UI-Texte ----------------------------------------------
    "COMMON_ABGEBROCHEN": "✅ Abgebrochen.",
    "COMMON_ABGEBROCHEN_AUFGABE_BLEIBT": "✅ Abgebrochen. Aufgabe bleibt erhalten.",
    "COMMON_JA_NEIN": "Bitte antworte mit ja oder nein.",
    "COMMON_NICHT_AUTORISIERT": "Nicht autorisiert.",
    "COMMON_TASK_NICHT_GEFUNDEN": "Die Aufgabe finde ich gerade nicht mehr.",
    "COMMON_BESCHAEFTIGT": "Ich bin gerade beschäftigt. Bitte warte kurz.",
    "COMMON_NICHT_FUER_DICH": "Dieser Befehl ist nicht für dich.",
    # Markdown – gemeinsamer Text für Onboarding Schritt 6 und /profil-Edit
    # (beide nutzen zeiten.parse_kinderfreie_zeiten, der "keine"/"immer frei" akzeptiert)
    "COMMON_ZEITEN_UNVERSTANDEN": (
        "Das habe ich nicht als Zeitfenster verstanden. 🙈\n\n"
        "Bitte im Format *HH:MM-HH:MM* angeben, mehrere kommagetrennt –\n"
        "z.B. `20:00-23:00` oder `07:00-08:00, 20:00-23:00`\n\n"
        "Oder schreibe *immer frei*"
    ),

    # --- Technische Fehler --------------------------------------------------
    "FEHLER_ALLGEMEIN": "⚠️ Ein Fehler ist aufgetreten. Bitte erneut versuchen.",
    "FEHLER_LADEN": "⚠️ Fehler beim Laden. Bitte erneut versuchen.",
    "FEHLER_KEINE_ANTWORT": "⚠️ Ich konnte gerade nicht antworten. Bitte nochmal versuchen.",

    # --- Safety: Safeword-Flow (bewusst statisch, NICHT per LLM) ------------
    # {wort} = config.RESUME_WORT (per Env konfigurierbar, Default "weiter")
    "SAFEWORD_PAUSIERT_HINWEIS": "Das System ist pausiert. Schreibe '{wort}' um fortzufahren.",
    "SAFEWORD_PAUSIERT": "⛔ Safeword verwendet. Alles pausiert.\nSchreibe '{wort}' um fortzufahren.",
    "SAFEWORD_AKTIV": "✅ System wieder aktiv.",

    # --- Persona-Fallbacks (Stimme der Herrin) bei LLM-Ausfall ---------------
    "FALLBACK_NICHT_ERLEDIGT": "Das also nicht. Das besprechen wir noch.",
    "FALLBACK_GEFUEHL_REAKTION": "Hm. Das lass ich mir durch den Kopf gehen.",
    "FALLBACK_GEFUEHL_FRAGE": "Gut. Und jetzt erzähl mir: Wie war das für dich?",
    "FALLBACK_FOLLOWUP_FRAGE": "Hast du das hier erledigt: {aufgabe}?",
    "FALLBACK_WUNSCH_ANGENOMMEN": "Deinen Wunsch gewähre ich dir.",
    "FALLBACK_WUNSCH_ABGELEHNT": "Deinen Wunsch gewähre ich dir nicht.",
    # War vorher "Entschuldigung, ein technischer Fehler …" – brach die Herrin-Fiktion.
    "FALLBACK_SKLAVE_CHAT": "Ich bin gerade kurz nicht erreichbar. Schreib mir gleich nochmal.",
    "FALLBACK_STIMMUNG_REAKTION": "Hm. Das lass ich mal sacken.",

    # --- Medien-Weiterleitung (main.py) --------------------------------------
    "MEDIEN_VON_SKLAVE": "📎 Medien von deinem Sklaven:",
    "MEDIEN_VON_HERRIN": "📎 Medien von deiner Herrin:",
    "MEDIEN_AN_HERRIN_WEITERGELEITET": "📎 An deine Herrin weitergeleitet.",
    "MEDIEN_AN_SKLAVEN_WEITERGELEITET": "📎 An deinen Sklaven weitergeleitet.",
    "MEDIEN_FEHLER": "⚠️ Konnte die Nachricht nicht weiterleiten.",

    # --- Onboarding (Markdown) ------------------------------------------------
    "ONBOARDING_ABGEBROCHEN": "❌ Onboarding abgebrochen. Du kannst es jederzeit mit /start neu beginnen.",
    "ONBOARDING_BEREIT_HINWEIS": "Schreibe *ja* wenn du bereit bist.",
    "ONBOARDING_DOMINA_BEGRUESSUNG": (
        "👑 *Hi.*\n\n"
        "Ich bin deine Begleiterin hier – so eine Art beste Freundin, die im gleichen Thema unterwegs ist. "
        "Wir richten dir kurz ein Profil ein, dauert zwei Minuten.\n\n"
        "Bereit? Schreib *ja*."
    ),
    "ONBOARDING_DOMINA_SCHRITT_SPRACHE": (
        "🌍 *Schritt 1/9 – Sprache*\n\n"
        "In welcher Sprache soll der Bot antworten?\n\n"
        "1️⃣ Deutsch (Standard)\n"
        "2️⃣ Englisch\n\n"
        "Schreibe 1, 2 – oder tippe eine andere Sprache ein"
    ),
    "ONBOARDING_DOMINA_SCHRITT_ROLLEN": (
        "🎭 *Schritt 2/9 – Rollen-Konstellation*\n\n"
        "Welche Konstellation spielt ihr?\n\n"
        "{liste}\n\n"
        "Schreibe die Nummer\n"
        "_(bestimmt Anrede, Pronomen und Anatomie-Logik der generierten Texte – "
        "später änderbar in /einstellungen)_"
    ),
    "ONBOARDING_DOMINA_SCHRITT_STIL": (
        "🖤 *Schritt 3/9 – Stil*\n\n"
        "Welchen Stil soll die dominante Stimme des Bots haben?\n\n"
        "{liste}\n\n"
        "Schreibe die Nummer _(später änderbar in /einstellungen)_"
    ),
    "ONBOARDING_DOMINA_SCHRITT_ERFAHRUNG": (
        "📊 *Schritt 4/9 – Erfahrungsstand*\n\n"
        "Wie würdest du deinen Erfahrungsstand beschreiben?\n\n"
        "1️⃣ Anfänger – ich stehe am Anfang\n"
        "2️⃣ Etwas Erfahrung – ich habe erste Erfahrungen gemacht\n"
        "3️⃣ Erfahren – ich kenne mich gut aus\n\n"
        "Schreibe 1, 2 oder 3"
    ),
    "ONBOARDING_DOMINA_SCHRITT_INTERESSEN": (
        "✨ *Schritt 5/9 – Interessen*\n\n"
        "Was interessiert dich am meisten? _(kommagetrennt)_\n\n"
        "Beispiele: Rituale, Service, Gehorsam, Psychospiele, Strafen, Körperkontrolle"
    ),
    "ONBOARDING_DOMINA_SCHRITT_GRENZEN": (
        "🚫 *Schritt 6/9 – Grenzen*\n\n"
        "Gibt es absolute Grenzen die du setzen möchtest?\n"
        "_(kommagetrennt, z.B. Blut, Verletzungen)_\n\n"
        "Oder schreibe *keine*"
    ),
    "ONBOARDING_DOMINA_SCHRITT_ZIELE": (
        "🎯 *Schritt 7/9 – Ziele*\n\n"
        "Was möchtest du mit diesem Bot erreichen?\n"
        "Beschreibe kurz deine Ziele als Domina."
    ),
    "ONBOARDING_DOMINA_SCHRITT_TEMPO": (
        "⏱ *Schritt 8/9 – Tempo*\n\n"
        "In welchem Tempo möchtest du vorgehen?\n\n"
        "1️⃣ Langsam – lieber vorsichtig rantasten\n"
        "2️⃣ Normal – ausgewogenes Tempo\n"
        "3️⃣ Schnell – ich möchte schnell vorankommen\n\n"
        "Schreibe 1, 2 oder 3"
    ),
    "ONBOARDING_DOMINA_SCHRITT_ZEITEN": (
        "👨‍👩‍👧 *Schritt 9/9 – Kinderfreie Zeiten*\n\n"
        "Gibt es Zeiten wo Kinder im Haus sind?\n"
        "Falls ja: wann bist du ungestört?\n"
        "_(z.B. 20:00-23:00, oder mehrere kommagetrennt)_\n\n"
        "Oder schreibe *immer frei*"
    ),
    "ONBOARDING_SPRACHE_WAHL": "Bitte wähle 1️⃣ oder 2️⃣ – oder tippe deine Sprache ein (z.B. Französisch).",
    "ONBOARDING_ROLLEN_WAHL": "Bitte wähle eine Nummer:\n\n{liste}",
    "ONBOARDING_STIL_WAHL": "Das kenne ich nicht. Bitte wähle eine Nummer:\n\n{liste}",
    "ONBOARDING_ERFAHRUNG_WAHL": "Bitte wähle 1️⃣, 2️⃣ oder 3️⃣ – oder beschreibe deinen Erfahrungsstand kurz.",
    "ONBOARDING_ERFAHRUNG_NUR_ZAHLEN": "Bitte beschreibe deinen Erfahrungsstand in eigenen Worten, nicht nur mit Zahlen.",
    "ONBOARDING_DOMINA_ZUSAMMENFASSUNG": (
        "✅ *Profil gespeichert! Hier deine Zusammenfassung:*\n\n"
        "🌍 Sprache: {sprache}\n"
        "🎭 Rollen: {rollen}\n"
        "🖤 Stil: {stil}\n"
        "👤 Erfahrungsstand: {erfahrungsstand}\n"
        "✨ Interessen: {interessen}\n"
        "🚫 Grenzen: {grenzen}\n"
        "🎯 Ziele: {ziele}\n"
        "⏱ Tempo: {tempo}\n"
        "👨‍👩‍👧 Kinderfreie Zeiten: {zeiten}\n\n"
        "──────────────────────\n\n"
        "📋 *Wichtigste Commands:*\n"
        "/profil – Profil bearbeiten\n"
        "/inspiration – 3 Aufgaben-Ideen\n"
        "/wochenplanung – Wochenplan erstellen\n"
        "/training – Psycho-Training starten\n"
        "/stats – Statistiken (für Sklaven)\n\n"
        "Alles bereit! Schreibe einfach los – ich bin dein Coach. 🖤"
    ),
    "ONBOARDING_SKLAVE_INFO_AKTIV": "Ich bin jetzt bereit. Es geht los – du hörst von mir. 🖤",
    "ONBOARDING_SKLAVE_BEGRUESSUNG": (
        "Du wirst hier mit deiner Herrin sprechen.\n\n"
        "Bevor das losgeht, sag mir kurz drei Dinge über dich – damit sie weiß, "
        "was geht und was nicht.\n\n"
        "──────────────────────\n\n"
        "🚫 *Schritt 1/3 – Absolute Grenzen*\n\n"
        "Was sind deine absoluten Grenzen?\n"
        "Diese werden *NIEMALS* überschritten.\n"
        "_(kommagetrennt, z.B. Blut, öffentliche Demütigung)_\n\n"
        "Oder schreibe *keine*"
    ),
    "ONBOARDING_SKLAVE_SCHRITT_2": (
        "💚 *Schritt 2/3 – Vorlieben*\n\n"
        "Was magst du besonders? Was gibt dir Energie?\n"
        "_(kommagetrennt, z.B. Rituale, körperliche Aufgaben, Reflexion)_"
    ),
    "ONBOARDING_SKLAVE_SCHRITT_3": (
        "📖 *Schritt 3/3 – Erfahrungsstand*\n\n"
        "Wie lange bist du schon in dieser Rolle?\n"
        "Beschreibe kurz deinen Erfahrungsstand."
    ),
    "ONBOARDING_SKLAVE_GESPEICHERT": (
        "Gut. Ich weiß jetzt, was ich wissen muss.\n\n"
        "Du wirst von mir Aufgaben bekommen. Antworte mir immer ehrlich – "
        "das ist das Wichtigste zwischen uns. 🖤"
    ),
    "ONBOARDING_DOMINA_INFO_SKLAVE_FERTIG": (
        "ℹ️ *Dein Sklave hat sein Profil eingerichtet.*\n\n"
        "Absolute Grenzen: {limits}\n"
        "Vorlieben: {vorlieben}\n"
        "Erfahrungsstand: {erfahrungsstand}\n\n"
        "Der Bot ist jetzt vollständig einsatzbereit. 🖤"
    ),

    # --- Vorlagen (Markdown) --------------------------------------------------
    "VORLAGEN_TITEL": "📁 *Aufgaben-Vorlagen*\n",
    "VORLAGEN_KEINE": "_Noch keine Vorlagen gespeichert._\n",
    "VORLAGEN_AKTIONEN": "Was möchtest du tun?\n`neu` = neue Vorlage erstellen",
    "VORLAGEN_AKTIONEN_MIT_LISTE": "`1` (Nummer) = Vorlage als Aufgabe senden\n`l1` (l + Nummer) = Vorlage löschen",
    "VORLAGEN_ABBRECHEN_HINWEIS": "\nOder /abbrechen",
    "VORLAGEN_NAME_FRAGE": "📝 Name der Vorlage:",
    "VORLAGEN_TEXT_FRAGE": "📝 Aufgaben-Text der Vorlage:",
    "VORLAGEN_GELOESCHT": "🗑 Vorlage gelöscht.",
    "VORLAGEN_BESTAETIGUNG": (
        "📋 Vorlage:\n_{aufgabe}_\n\n"
        "Soll ich diese Aufgabe an ihn weiterleiten?\n"
        "Antworte mit `ja` oder `nein`"
    ),
    "VORLAGEN_UNGUELTIG": "Ungültige Eingabe. Bitte Nummer, `neu`, oder /abbrechen",
    "VORLAGEN_GESPEICHERT": "✅ Vorlage *{name}* gespeichert!\n\nDu kannst sie mit /vorlagen jederzeit abrufen.",

    # --- Domina-Chat: Aufgaben-/Ketten-Dialog ----------------------------------
    "DOMINA_AUFGABE_GRENZEN": (
        "⚠️ Diese Aufgabe berührt eure Grenzen ({treffer}) – "
        "ich leite sie *nicht* an ihn weiter. "
        "Formuliere sie ggf. anders."
    ),
    # MarkdownV2 – Platzhalter am Callsite mit escape_md übergeben
    "DOMINA_AUFGABE_ERKANNT": (
        "📋 Erkannte Aufgabe:\n_{aufgabe}_\n\n"
        "Soll ich diese Aufgabe an ihn weiterleiten?\n"
        "Antworte mit `ja` oder `nein`"
    ),
    "DOMINA_AUFGABE_VERWORFEN": "✅ Aufgabe verworfen. Kein Problem!",
    # MarkdownV2 – Platzhalter am Callsite mit escape_md übergeben
    "DOMINA_AUFGABE_ERKANNT_TERMIN": (
        "📋 Erkannte Aufgabe:\n_{aufgabe}_\n\n"
        "📅 Erkannter Termin: *{termin}*\n\n"
        "Soll ich sie an diesem Tag an ihn weiterleiten?\n"
        "Antworte mit `ja` oder `nein`"
    ),
    "DOMINA_AUFGABE_WANN": (
        "📅 Wann soll er die Aufgabe bekommen?\n\n"
        "Antworte mit *sofort* – oder nenn mir einen Tag "
        "(z. B. `morgen`, `Samstag`, `26.07.`)."
    ),
    "DOMINA_AUFGABE_WANN_UNKLAR": (
        "Das habe ich nicht als Zeitpunkt verstanden. 🤔\n"
        "Sag *sofort* – oder einen Tag wie `morgen`, `Samstag` oder `26.07.`"
    ),
    "DOMINA_AUFGABE_TERMIN_GEPLANT": (
        "📅 Gespeichert! Die Aufgabe geht am *{termin}* morgens an ihn raus – "
        "abends frage ich dann nach, ob er sie erledigt hat."
    ),
    "DOMINA_KETTE_FRAGE": (
        "🔗 Soll das eine *Aufgaben-Kette* werden?\n\n"
        "Bei einer Kette werden weitere Aufgaben erst freigeschaltet, "
        "wenn die vorherige erledigt wurde.\n\n"
        "Antworte mit `ja` oder `nein`"
    ),
    # MarkdownV2 – enthält bewusst escapte Satzzeichen
    "DOMINA_KETTE_START": (
        "🔗 Aufgaben\\-Kette wird erstellt\\.\n\n"
        "*Aufgabe 1:* _{aufgabe}_\n\n"
        "Schreibe die nächste Aufgabe oder *fertig* um die Kette abzuschließen:"
    ),
    "DOMINA_KETTE_ERSTELLT": (
        "✅ Aufgaben-Kette mit {gesamt} Aufgaben erstellt!\n"
        "Die erste Aufgabe wird jetzt an ihn gesendet."
    ),
    "DOMINA_KETTE_AUFGABE_HINZU": (
        "✅ Aufgabe {nummer} hinzugefügt.\n\n"
        "Schreibe *Aufgabe {naechste}* oder *fertig* um die Kette abzuschließen:"
    ),
    "DOMINA_LEVEL_UP": (
        "🎉 Herzlichen Glückwunsch! Du hast *Level {level}* erreicht!\n"
        "Vielfalt: {vielfalt}★ | Streak: {streak}★ | Bewertung: {bewertung}★"
    ),
    "DOMINA_NEUES_ABZEICHEN": "{emoji} *Neues Abzeichen:* {name}\n_{beschreibung}_",

    # --- Storylines / Arcs (Markdown) -------------------------------------------
    "ARC_STATUS": (
        "📖 *Aktive Storyline: {thema}*\n\n"
        "Tag *{tag_aktuell} von {tage_gesamt}*\n\n"
        "{tage_text}\n\n"
        "Nutze `/arc_beenden` um die Storyline vorzeitig zu beenden,\n"
        "oder `/arc_starten <thema>` um danach eine neue zu beginnen."
    ),
    "ARC_HILFE": (
        "📖 *Storylines / Arcs*\n\n"
        "Eine Storyline verbindet 3-7 Aufgaben zu einem narrativen Bogen.\n\n"
        "Nutze `/arc_starten <thema>` um eine zu starten.\n"
        "Beispiele:\n"
        "  • `/arc_starten Eine Woche reine Hingabe`\n"
        "  • `/arc_starten Ausbildung zum perfekten Diener`\n"
        "  • `/arc_starten Grenzen erforschen`"
    ),
    "ARC_THEMA_FEHLT": (
        "Bitte ein Thema angeben:\n`/arc_starten Eine Woche reine Hingabe`\n"
        "Optional mit Tage-Anzahl (3-7): `/arc_starten 7 Eine Woche reine Hingabe`"
    ),
    "ARC_GENERIERE": "📖 Ich generiere die Storyline zu: _{thema}_ ...",
    "ARC_LIMIT_ABBRUCH": (
        "⚠️ Storyline berührte mehrfach eure Grenzen – abgebrochen. "
        "Versuche ein anderes Thema oder schärfe es genauer."
    ),
    "ARC_TAGE_VERLETZT": "⚠️ Storyline-Tage {tage} verletzten eure Grenzen – abgebrochen.",
    "ARC_FEHLER": "⚠️ Konnte keine Storyline erstellen. Versuch es nochmal.",
    "ARC_GESTARTET": (
        "📖 *Storyline gestartet: {thema}*\n\n"
        "{uebersicht}\n\n"
        "Tag 1 wird beim nächsten Tiny-Task-Job automatisch erteilt. "
        "Nutze `/arc` um den Fortschritt zu sehen."
    ),
    "ARC_KEINE_AKTIV": "Keine aktive Storyline.",
    "ARC_BEREITS_AKTIV": (
        "⚠️ Es läuft bereits die Storyline *{thema}*. "
        "Beende sie zuerst mit `/arc_beenden`, dann kannst du eine neue starten."
    ),
    "ARC_BEENDET": "📖 Storyline _{thema}_ beendet.",

    # --- Kleine Wait-/Prefix-Texte ----------------------------------------------
    "RUECKBLICK_WARTE": "📊 Einen Moment, ich analysiere die letzten Wochen...",
    "RUECKBLICK_PREFIX": "📊 *Dein Rückblick:*\n\n{analyse}",
    "TINYTASK_WARTE": "💡 Einen Moment, ich erstelle einen Vorschlag...",
    "STIMMUNG_FRAGE": "Wie geht es dir gerade? Wie ist deine Stimmung? 🖤",
    "STIMMUNG_HINWEIS_AN_DOMINA": "💭 Stimmung deines Sklaven: _{antwort}_\n\n{hinweis}",
    # --- Button-Labels -----------------------------------------------------------
    "BUTTON_ANNEHMEN": "✅ Annehmen",
    "BUTTON_ABLEHNEN": "❌ Ablehnen",
    "BUTTON_MERKEN": "✅ Merken",
    "BUTTON_VERWERFEN": "🗑 Verwerfen",
    "BUTTON_ALLE_LOESCHEN": "🗑 Alle löschen",
    "BUTTON_BESTAETIGEN": "✅ Bestätigen",
    "BUTTON_VERWEIGERN": "❌ Verweigern",
    "BUTTON_ERNEUT_ERTEILEN": "🔁 Erneut erteilen",
    "BUTTON_DIESE_WOCHE_NICHT": "⏭ Diese Woche nicht",

    # --- Scheduler-Jobs: Rahmen/Prefixe -----------------------------------------
    "ARC_TAG_ANGEPASST": "\n\n_(an sein letztes Feedback angepasst: {stimmung})_",
    "ARC_TAG_VORSCHLAG": (
        "📖 *Storyline: {thema}* – Tag {tag}/{gesamt}\n\n"
        "*{titel}* _(Kategorie: {kategorie})_\n\n"
        "{aufgabe}{hinweis}"
    ),
    "ARC_ABGESCHLOSSEN": (
        "📖 *Storyline abgeschlossen: {thema}*\n"
        "Alle {tage} Tage durchlaufen."
    ),
    "ARC_NEUE_ABZEICHEN": "\n\n🏅 Neue Abzeichen: {liste}",
    "TINYTASK_PAUSE_TAG": "🎁 Heute kein Tiny-Task – Pause-Tag-Privileg deines Sklaven wurde eingelöst.",
    "TINYTASK_PREFIX_TINY": "💡 *Tipp für heute:*\n\n{vorschlag}",
    "TINYTASK_PREFIX_AUSFUEHRLICH": "🎯 *Aufgaben-Vorschlag für heute:*\n\n{vorschlag}",
    "ROLLENSPIEL_IDEE": "🎭 *Rollenspiel-Idee für heute Abend:*\n\n{vorschlag}",
    "LERNKURVE_PREFIX": "📊 *Deine Lernkurve – letzte 2 Wochen:*\n\n{analyse}",
    "GEHEIMNIS_PREFIX": "🔓 *Enthüllung:*\n\n{nachricht}",
    "KOMMENTAR_ANALYSE_PREFIX": "📝 *Wochenrückblick deiner Rückmeldungen:*\n\n{analyse}",
    "RESURFACE_VORSCHLAG": (
        "🕰 *Heute vor ~3 Monaten*\n\n"
        "_{datum}_ – Kategorie *{kategorie}*, deine Bewertung: {sterne}\n\n"
        "_{aufgabe}_\n\n"
        "Lust, das nochmal zu erteilen?"
    ),
    "ERINNERUNG_KEINE_AUFGABE": (
        "⏰ *Erinnerung:* Dein Sklave hat seit mehr als {tage} Tagen keine Aufgabe erhalten.\n"
        "Möchtest du ihm heute eine neue Aufgabe stellen? "
        "Schreibe `aufgabe: <text>` oder hol dir Ideen mit /inspiration."
    ),
    "BACKUP_FEHLGESCHLAGEN": "⚠️ Das automatische Qdrant-Backup ist heute fehlgeschlagen. Bitte die Logs prüfen.",
    "REFLEXION_INTRO": (
        "🧭 *Coach-Reflexion – {zeitraum}*\n\n"
        "Beim Durchsehen unserer letzten 14 Tage sind mir {anzahl} Dinge aufgefallen, "
        "die ich anders machen könnte. Du entscheidest, ob ich sie übernehme."
    ),

    # --- Aufgaben-Historie / Lösch-Dialog ------------------------------------------
    "AUFGABEN_KEINE_ERLEDIGT": "Noch keine erledigten Aufgaben.",
    "AUFGABEN_LISTE_TITEL": "📋 *Letzte erledigte Aufgaben{filter}:*\n",
    "AUFGABEN_EINTRAG": (
        "{nr}. *{aufgabe}*{serie}\n"
        "   📅 {erteilt} | 🏷 {kategorie}\n"
        "   💬 Gefühl: _{gefuehl}_\n"
    ),
    "AUFGABEN_FILTER_KOPF": (
        "─────────────────\n"
        "🏷 *Filter nach Kategorie:*\n"
        "`/aufgaben_alle` = alle anzeigen"
    ),
    "AUFGABEN_KATEGORIE_UNBEKANNT": "⚠️ Kategorie „{kategorie}“ kenne ich nicht – /aufgaben zeigt alle Filter.",
    "AUFGABEN_KEINE_OFFEN": "Keine offenen Aufgaben vorhanden.",
    "AUFGABEN_LOESCHEN_TITEL": "📋 *Offene Aufgaben:*\n",
    "AUFGABEN_LOESCHEN_FUSS": (
        "\nSchreibe die *Nummer* und dann:\n"
        "`p` = pausieren  |  `x` = löschen  |  `s` = ganze Serie/Kette stoppen\n"
        "Beispiel: `1 p` oder `2 x`\n"
        "\nOder /abbrechen"
    ),
    "AUFGABEN_GELOESCHT": "🗑 Aufgabe gelöscht.",
    "AUFGABEN_UNGUELTIG": "Ungültige Eingabe. Beispiel: `1 p` (pausieren), `1 x` (löschen) oder `1 s` (Serie/Kette stoppen)\nOder /abbrechen",
    "AUFGABEN_KEINE_SERIE": "Diese Aufgabe gehört zu keiner Serie/Kette. Nutze `x` zum Löschen.",
    "AUFGABEN_SERIE_STOPP_BESTAETIGUNG": "⚠️ Ganze Serie/Kette wirklich stoppen?\n\n_{aufgabe}_\n\n🔄 *{anzahl}* Glied(er) werden verworfen.\n\nAntworte mit `ja` oder `nein`",
    "AUFGABEN_SERIE_GESTOPPT": "🗑 Serie/Kette gestoppt – {anzahl} Glied(er) verworfen.",
    "AUFGABEN_LISTE_VERALTET": "⚠️ Die Auswahlliste ist veraltet. Bitte starte /loeschen neu.",
    "AUFGABEN_BEREITS_MARKIERT": "⚠️ Diese Aufgabe wurde bereits als '{status}' markiert. Starte /loeschen neu.",
    "AUFGABEN_PAUSIERT": "⏸ Aufgabe pausiert.",
    "AUFGABEN_LOESCHEN_BESTAETIGUNG": "⚠️ Aufgabe wirklich löschen?\n\n_{aufgabe}_\n\nAntworte mit `ja` oder `nein`",

    # --- Privilegien ----------------------------------------------------------------
    # --- Wette (Doppelt oder nichts) -----------------------------------------
    "BUTTON_WETTE_EINSATZ": "🎰 {punkte} Punkte",
    "WETTE_ANGEBOT": (
        "🎰 *Doppelt oder nichts*\n\n"
        "Du hast *{punkte} Punkte*. Setz einen Einsatz darauf, dass du deine "
        "nächste fällige Aufgabe schaffst:\n"
        "Schaffst du sie → doppelter Einsatz zurück. Versagst du → Einsatz weg."
    ),
    "WETTE_PLATZIERT": "🎰 Wette platziert: *{einsatz} Punkte*. Rest-Konto: {rest} Punkte.\nJetzt gibt es kein Zurück.",
    "WETTE_SCHON_AKTIV": "🎰 Du hast bereits eine Wette über *{einsatz} Punkte* laufen. Erst wird die entschieden.",
    "WETTE_KEINE_AUFGABE": "🎰 Keine offene Aufgabe, auf die du wetten könntest. Erst wenn etwas auf dem Tisch liegt.",
    "WETTE_ZU_WENIG_PUNKTE": "🎰 Du hast {punkte} Punkte – für eine Wette brauchst du mindestens {minimum}.",
    "WETTE_STATE_WEG": "Diese Wett-Buttons sind nicht mehr gültig. Starte neu mit /wette.",
    "WETTE_VERLOREN": "🎰 *Wette verloren.* Dein Einsatz von {einsatz} Punkten ist weg.",
    "WETTE_INFO_DOMINA": "🎰 Nebenbei: dein Sklave hat gerade *{einsatz} Punkte* darauf gewettet, dass er seine nächste Aufgabe schafft. Doppelt oder nichts.",
    "SPIEL_IMPULS_WETTE": "Mir ist gerade nach einem Spiel – und du darfst mitspielen:",

    # --- Blitzaufgaben ⚡ --------------------------------------------------------
    "BLITZ_AN": (
        "⚡ *Blitzaufgaben aktiviert.*\n\n"
        "Der Bot schickt deinem Sklaven ab jetzt gelegentlich unangekündigt eine "
        "Mini-Aufgabe mit *{minuten} Minuten Countdown* – direkt, ohne Rückfrage bei dir "
        "(Limits werden wie immer geprüft, kinderfreie Zeiten beachtet). "
        "Du bekommst jede Blitzaufgabe zur Info. Ausschalten: /blitz"
    ),
    "BLITZ_AUS": "⚡ Blitzaufgaben deaktiviert.",
    "BLITZ_AN_SKLAVEN": (
        "⚡ *BLITZAUFGABE* ⚡\n\n{anweisung}\n\n"
        "⏱ Du hast *{minuten} Minuten*. Drück den Knopf, wenn du fertig bist – "
        "danach ist es zu spät."
    ),
    "BUTTON_BLITZ_GESCHAFFT": "⚡ Geschafft!",
    "BLITZ_GESCHAFFT": "⚡ *Geschafft!* +{punkte} Punkte (gesamt: {gesamt}).",
    "BLITZ_GESCHAFFT_DOMINA": "⚡ Er hat die Blitzaufgabe rechtzeitig geschafft.",
    "BLITZ_VERPASST": "⏱ Die Zeit ist um. Die Blitzaufgabe ist verfallen – das merkt sie sich.",
    "BLITZ_VERPASST_DOMINA": "⚡ Er hat die Blitzaufgabe verstreichen lassen: „{aufgabe}“ – falls du daraus etwas machen willst.",
    "BLITZ_INFO_DOMINA": "⚡ Zur Info: Blitzaufgabe an deinen Sklaven raus ({minuten} Min Countdown): „{aufgabe}“",
    "BLITZ_NICHT_MEHR_OFFEN": "Diese Blitzaufgabe ist nicht mehr offen.",
    "BLITZ_ZU_SPAET": "⏱ Zu spät – der Countdown war schon abgelaufen. Die Aufgabe ist verfallen.",

    # --- Event-Arcs 🎂 -----------------------------------------------------------
    "EVENT_HILFE": (
        "🎂 *Event-Storylines*\n\n"
        "Plane eine Storyline, deren Finale genau auf ein Datum fällt "
        "(Geburtstag, Jahrestag, …):\n"
        "`/event 24.12. Weihnachts-Special`\n"
        "`/event 15.08.2026 7 Jahrestag`\n\n"
        "Format: /event <TT.MM.[JJJJ]> [Tage 3-7] <Thema>\n"
        "Der Start passiert automatisch, du bekommst Bescheid."
    ),
    "EVENT_LISTE": "🎂 *Geplante Events:*\n\n{liste}\n\nNeu: /event <TT.MM.> [Tage] <Thema> – Löschen: /event\\_loeschen <Nr>",
    "EVENT_DATUM_UNVERSTANDEN": "Das Datum habe ich nicht verstanden. Format: *TT.MM.* oder *TT.MM.JJJJ* (z.B. `/event 24.12. Weihnachts-Special`).",
    "EVENT_THEMA_FEHLT": "Und worum soll es gehen? `/event <TT.MM.> [Tage] <Thema>`",
    "EVENT_ZU_SPAET": "Das Datum liegt nicht in der Zukunft – für heute hilft nur noch /arc_starten.",
    "EVENT_GEPLANT": (
        "🎂 *Event geplant:* {thema}\n"
        "📅 Finale am *{datum}*, Storyline über *{tage} Tage* "
        "(Start in ~{start_in} Tagen, automatisch).\n\n"
        "Ansehen: /event – Verwerfen: /event\\_loeschen"
    ),
    "EVENT_GESTARTET": (
        "🎂 *Event-Storyline gestartet:* {thema}\n"
        "Finale am *{datum}* – die Tage bauen darauf hin:\n\n{uebersicht}\n\n"
        "Läuft ab jetzt wie ein normaler Arc (/arc)."
    ),
    "EVENT_WARTET": "🎂 Das Event *{thema}* möchte starten, aber es läuft noch eine andere Storyline. Ich versuche es morgen wieder – die Event-Storyline wird entsprechend kürzer. (/arc_beenden macht Platz.)",
    "EVENT_VERPASST": "🎂 Für das Event *{thema}* ist keine Zeit mehr (unter 3 Tage bis zum Datum). Ich habe es verworfen – für Spontanes: /arc_starten oder /wuerfel.",
    "EVENT_GELOESCHT": "🗑 Event „{thema}“ verworfen.",
    "EVENT_KEINE_GEPLANT": "Keine Events geplant. Neu: /event <TT.MM.> [Tage] <Thema>",
    "EVENT_LOESCHEN_HINWEIS": "Welche Nummer? /event_loeschen <Nr>\n\n{liste}",

    # --- Voice-Input 🎤 ----------------------------------------------------------
    "VOICE_VERSTANDEN": "🎤 „{text}“",
    "VOICE_NICHT_VERSTANDEN": "🎤 Das habe ich nicht verstanden – sprich nochmal oder tipp es.",
    "VOICE_ZU_LANG": "🎤 Zu lang – maximal {sekunden} Sekunden pro Sprachnachricht.",
    "COACH_SPRACHNACHRICHT_GESENDET": "🔊 Sprachnachricht ist raus.",
    "COACH_SPRACHNACHRICHT_LIMIT": "⛔ Nicht gesendet – das würde Limits verletzen ({begriffe}).",
    "COACH_SPRACHNACHRICHT_FEHLER": "⚠️ Sprachnachricht konnte nicht zugestellt werden – probier es gleich nochmal.",
    "MINIAPP_OEFFNEN": "📱 Deine Mini-App – tippe auf den Knopf (nur im Heimnetz erreichbar):",
    "MINIAPP_KNOPF": "📱 Öffnen",
    "MINIAPP_AUS": "Die Mini-App ist nicht eingerichtet (MINIAPP_PORT/MINIAPP_URL in der .env).",

    # --- Strafen-Roulette 🎰 -----------------------------------------------------
    "ROULETTE_JACKPOT": (
        "🎰 *JACKPOT!*\n\nDie Maschine hat entschieden: *GNADE*. Keine Strafe.\n"
        "Willst du es ihm verkünden lassen – oder behältst du es für dich?"
    ),
    "BUTTON_ROULETTE_GNADE": "😇 Gnade verkünden",
    "ROULETTE_STUFE_MILD": "mild",
    "ROULETTE_STUFE_MITTEL": "mittel",
    "ROULETTE_STUFE_HART": "HART",
    "ROULETTE_VORSCHLAG": (
        "🎰 *Die Maschine sagt: {stufe}*\n\n{strafe}\n\n"
        "Erteilen oder verwerfen?"
    ),
    "ROULETTE_AN_SKLAVEN": "🎰 *Die Maschine hat entschieden.*\n\n{anweisung}",
    "ROULETTE_ERTEILT": "🎰 Strafe erteilt – die Maschine trägt die Verantwortung.",
    "ROULETTE_VERWORFEN": "🎰 Verworfen. Die Maschine schweigt.",
    "ROULETTE_GNADE_VERKUENDET": "😇 Gnade verkündet.",
    "ROULETTE_GNADE_FALLBACK": "🎰 Die Maschine hat den Jackpot gezogen: Gnade. Diesmal.",
    "ROULETTE_STATE_WEG": "Diese Roulette-Buttons sind nicht mehr gültig. Neu drehen: /roulette",
    "ROULETTE_FEHLER": "⚠️ Die Maschine klemmt – Strafe konnte nicht generiert werden. Versuch es nochmal.",

    # --- Dauer-Anweisungen 🕰 ----------------------------------------------------
    "DAUER_HILFE": "🕰 *Dauer-Anweisung:* `/dauer <Stunden {min}-{max}> <Anweisung>`\nz.B. `/dauer 4 Du trägst bis heute Abend …`\nDie Herrin kontrolliert zwischendurch unangekündigt.",
    "DAUER_AN_SKLAVEN": "🕰 *DAUER-ANWEISUNG* – gilt für *{stunden} Stunden* (bis ~{bis} Uhr):\n\n{anweisung}\n\n_Sie wird zwischendurch kontrollieren. Am Ende fragt sie nach._",
    "DAUER_ERTEILT": "🕰 Dauer-Anweisung erteilt ({stunden}h, bis ~{bis} Uhr). Zwischen-Checks laufen automatisch.",
    "DAUER_ENDE_FALLBACK": "🕰 Die Zeit ist um. Hast du durchgehalten – ja oder nein? (Anweisung: {aufgabe})",
    "DAUER_CHECK_FALLBACK": "🕰 Kontrolle. Ich hoffe für dich, du bist noch dabei.",

    # --- Quiz 🧠 ------------------------------------------------------------------
    "QUIZ_FRAGE": "🧠 *Quiz – wie gut kennst du deine Herrin?*\n\n{frage}\n\n_Antworte frei – oder /abbrechen._",
    "SPIEL_IMPULS_QUIZ": "🧠 *Spontanes Quiz* – mir ist gerade danach, dich zu prüfen.\n\n{frage}\n\n_Antworte frei – oder /abbrechen._",
    "QUIZ_RICHTIG": "🧠 ✅ *Richtig!* +{punkte} Punkte.",
    "QUIZ_TEILWEISE": "🧠 🟡 *Halb richtig.* +{punkte} Punkte.\nGemeint war: _{antwort}_",
    "QUIZ_FALSCH": "🧠 ❌ *Daneben.* Richtig wäre: _{antwort}_",
    "QUIZ_ZU_WENIG_DATEN": "🧠 Dazu weiß ich noch zu wenig über sie – das Quiz braucht ein gepflegtes Profil/Dossier.",
    "QUIZ_FEHLER": "⚠️ Quiz gerade nicht möglich – versuch es später nochmal.",

    # --- Coach-Quiz 🧠 (Domina-Seite) --------------------------------------------
    "COACH_QUIZ_FRAGE_WISSEN": "🧠 *Quiz für dich* – Thema: {thema}\n\n{frage}\n\n_Antworte frei – oder /abbrechen._",
    "COACH_QUIZ_FRAGE_SKLAVE": "🧠 *Quiz* – wie gut kennst du ihn wirklich?\n\n{frage}\n\n_Antworte frei – oder /abbrechen._",
    "COACH_QUIZ_RICHTIG": "✅ *Sitzt.* Wusste ich doch, dass du das drauf hast.",
    "COACH_QUIZ_TEILWEISE": "🟡 *Fast.* Gemeint war: _{antwort}_",
    "COACH_QUIZ_FALSCH": "❌ *Daneben – passiert.* Gemeint war: _{antwort}_",
    "COACH_QUIZ_AUFLOESUNG": "📚 *Zum Mitnehmen:* {aufloesung}",
    "COACH_QUIZ_ZU_WENIG_DATEN": "🧠 Über ihn weiß ich noch zu wenig – pfleg erst Profil/Dossier, dann wird das ein Quiz.",
    "COACH_QUIZ_FEHLER": "⚠️ Quiz gerade nicht möglich – versuch es später nochmal.",
    "COACH_IMPULS_QUIZ_PREFIX": "☕ Kurze Zwischenfrage von mir – einfach weil's mich interessiert:",
    "COACH_IMPULS_WETTE": "🎲 *Idee für euch zwei* – falls dir nach einem Spiel ist:\n\n{idee}\n\n_Nur eine Idee – gib sie weiter, wenn sie dir gefällt._",

    # --- Adventskalender 🎄 ------------------------------------------------------
    "ADVENT_DEFAULT_THEMA": "Adventskalender",
    "ADVENT_GEPLANT": (
        "🎄 *Adventskalender {jahr} geplant:* {thema}\n\n"
        "Vom 1. bis 24. Dezember öffnet sich jeden Morgen (~8:00) automatisch ein "
        "Türchen für deinen Sklaven – jedes tagesaktuell generiert, mit steigender "
        "Intensität bis zum Finale an Heiligabend. Du bekommst jedes Türchen zur Info.\n\n"
        "Abbrechen: `/adventskalender stop`"
    ),
    "ADVENT_STATUS": "🎄 *Adventskalender {jahr}* – Thema: {thema}\nZuletzt geöffnet: Türchen {letzte}/24\n\nAbbrechen: `/adventskalender stop`",
    "ADVENT_KEINER": "🎄 Kein Adventskalender geplant. Anlegen: /adventskalender [Thema]",
    "ADVENT_GESTOPPT": "🎄 Adventskalender gestoppt.",
    "ADVENT_TUERCHEN": "🎄 *Türchen {tuer}/24*\n\n{anweisung}",
    "ADVENT_INFO_DOMINA": "🎄 Türchen {tuer}/24 geöffnet: „{aufgabe}“",

    "PRIVILEG_KATALOG": (
        "🎁 *Privilegien-Katalog*\n\n"
        "Dein Punktestand: *{punkte}*\n\n"
        "{katalog}\n\n"
        "_Wähle ein Privileg per Tap, oder antworte mit Nummer/Abbrechen._"
    ),
    "PRIVILEG_NUR_NUMMER": "Bitte nur die Nummer eingeben (z.B. `2`) oder `abbrechen`.",
    "PRIVILEG_NUMMER_BEREICH": "Nummer muss zwischen 1 und {max} sein.",
    "PRIVILEG_ZU_WENIG_PUNKTE": "⚠️ Du hast nur *{punkte}* Punkte. '{name}' kostet *{kosten}*.",
    "PRIVILEG_EINGELOEST": (
        "🎁 Eingelöst: *{name}* (−{kosten} Punkte, noch *{rest}*).\n"
        "Ob ich es dir wirklich gewähre, entscheide ich gleich."
    ),
    "PRIVILEG_NEUE_ABZEICHEN": "\n\n🏅 Neue Abzeichen: {liste}",
    "PRIVILEG_AN_DOMINA": (
        "🎁 *Dein Sklave hat ein Privileg eingelöst:*\n\n"
        "*{name}* ({kosten} Punkte)\n"
        "_{beschreibung}_\n\n"
        "_Wähle direkt oder antworte als Text mit Kommentar._"
    ),
    "PRIVILEG_ENTSCHIEDEN": "{emoji} Privileg {entscheidung}.",
    "PRIVILEG_ENTSCHEIDUNG_HINWEIS": "Bitte mit *bestätigen* oder *verweigern* antworten.",
    "PRIVILEG_ENTSCHEIDUNG_GESPEICHERT": "✅ Entscheidung gespeichert.",
    "PRIVILEG_NICHT_GEFUNDEN": "⚠️ Privileg nicht mehr gefunden.",
    "PRIVILEG_PUNKTE_ZURUECK": "\n_(Punkte zurück: {kosten})_",
    "PRIVILEG_VERFALLEN_ERSTATTET": (
        "⌛ Deine Einlösung ist verfallen (keine Entscheidung deiner Herrin): {namen}.\n"
        "_(Punkte zurück: {kosten})_"
    ),
    "PRIVILEG_FREI_AUFGABE_PROMPT": (
        "🎁 *Frei-Aufgabe:* Du darfst deine nächste Aufgabe selbst vorschlagen.\n"
        "Schreib sie mir jetzt in einer Nachricht – oder /abbrechen "
        "(das Privileg bleibt dann offen, Wiedereinstieg über /privileg)."
    ),
    "PRIVILEG_FREI_AUFGABE_GRENZEN": (
        "🚫 Dein Vorschlag verletzt gesetzte Grenzen:\n{treffer}\n\n"
        "Formuliere ihn neu – oder /abbrechen."
    ),
    "PRIVILEG_FREI_AUFGABE_ERSTELLT": (
        "✅ Deine Frei-Aufgabe ist erteilt. Sie zählt wie jede andere Aufgabe – "
        "ich frage zur gewohnten Zeit nach."
    ),
    "PRIVILEG_FREI_AUFGABE_AN_DOMINA": (
        "🎁 *Frei-Aufgabe eingelöst:* Er hat sich seine nächste Aufgabe selbst gewählt:\n\n"
        "_{aufgabe}_"
    ),
    # Persona-Fallbacks bei LLM-Ausfall (Stimme der Herrin)
    "FALLBACK_PRIVILEG_GEWAEHRT": "Gewährt: {name}.",
    "FALLBACK_PRIVILEG_VERWEIGERT": "Nicht diesmal – {name} gewähre ich dir nicht. Deine {kosten} Punkte hast du zurück.",

    # --- Coach-Regeln / Lern-System ------------------------------------------------
    "COACHREGELN_MERKEN_USAGE": (
        "ℹ️ So nutzt du /merken:\n"
        "`/merken <was der Coach sich merken soll>`\n\n"
        "Beispiel: `/merken Ich mag kurze, klare Aufgaben in der Früh.`"
    ),
    "COACHREGELN_REGEL_USAGE": (
        "ℹ️ So nutzt du /regel:\n"
        "`/regel <Regel, an die sich der Coach immer halten muss>`\n\n"
        "Beispiel: `/regel Antworte immer in maximal 4 Sätzen.`"
    ),
    "COACHREGELN_VERGESSEN_USAGE": "ℹ️ Nutze: `/vergessen <nr>` – die Nummern siehst du in /regeln.",
    # MarkdownV2 – {text} am Callsite mit escape_md übergeben
    # MarkdownV2 – Satzzeichen müssen escaped sein; {text} kommt am Callsite via escape_md.
    "COACHREGELN_NOTIZ_GESPEICHERT": "📝 Notiert\\. Ich behalte das ab jetzt im Kopf:\n_{text}_",
    "COACHREGELN_REGEL_AKTIV": "⚡ Regel aktiv\\. Ich halte mich ab jetzt daran:\n_{text}_",
    "COACHREGELN_KEINE": (
        "📋 Noch keine gelernten Regeln.\n\n"
        "Mit /regel <text> setzt du eine verbindliche Regel,\n"
        "mit /merken <text> eine lockere Notiz."
    ),
    "COACHREGELN_LISTE_TITEL": "📋 *Aktive Regeln & Notizen:*",
    "COACHREGELN_LISTE_FUSS": "\nMit `/vergessen <nr>` deaktivierst du eine.",
    "COACHREGELN_PENDING_TITEL": "🤔 *Vorschläge, die auf deine Bestätigung warten:*",
    "COACHREGELN_PENDING_FUSS": "\nDie kommen jeweils mit Ja/Nein-Knöpfen in den Chat.",
    "COACHREGELN_KEINE_NUMMER": "⚠️ Das war keine gültige Nummer.",
    "COACHREGELN_NUMMER_UNBEKANNT": (
        "⚠️ Diese Nummer kenne ich nicht. Ruf erst /regeln auf, dann nimm eine Nummer von dort."
    ),
    "COACHREGELN_DEAKTIVIERT": "🗑 Regel {nr} deaktiviert.",
    "COACHREGELN_UEBERNOMMEN": "\n\n✅ Übernommen – ab jetzt aktiv.",
    "COACHREGELN_PROFIL_AKTUALISIERT": "\n\n✅ Profil ({profile_user}) aktualisiert:\n{aenderungen}",
    "COACHREGELN_PATCH_LEER": "\n\n⚠️ Profil-Patch enthielt keine anwendbaren Änderungen.",
    "COACHREGELN_PATCH_FEHLER": "\n\n⚠️ Fehler beim Anwenden des Profil-Patches: {fehler}",
    "COACHREGELN_PATCH_IGNORIERT": "\n_Ignoriert: {liste}_",
    "COACHREGELN_VERWORFEN": "\n\n🗑 Verworfen – merke ich mir nicht.",
    "COACHREGELN_VORSCHLAG": "💡 *Lern-Vorschlag:*\n_{text}_",
    "COACHREGELN_VORSCHLAG_ANLASS": "\n\n_Anlass: {kontext}_",
    "COACHREGELN_VORSCHLAG_FRAGE": "\n\nSoll ich mir das merken?",
    "COACHREGELN_PROFILCHECK_WARTE": "🧬 Prüfe Profile auf Updates der letzten {days} Tage... einen Moment.",
    "COACHREGELN_PROFILCHECK_OK": (
        "🧬 {anzahl} Profil-Vorschläge gesendet (Zeitraum {zeitraum}).\n"
        "Bestätige oder verwerfe sie über die Buttons."
    ),
    "COACHREGELN_PROFILCHECK_LEER": "🧬 Keine Profil-Updates nötig: {info}",
    "COACHREGELN_PROFILCHECK_FEHLER": "⚠️ Fehler bei Profil-Pflege: {info}",
    "COACHREGELN_PROFIL_VORSCHLAG": "🧬 *Profil-Update-Vorschlag ({rolle}):*\n```\n{diff}\n```",
    "COACHREGELN_PROFIL_VORSCHLAG_FUSS": (
        "\n\nHard Limits werden automatisch ausgenommen. "
        "Bei ✅ wird der Patch ergänzend angewandt (Listen werden erweitert, nichts gelöscht)."
    ),

    # --- Präferenz-Detektor (Vorlieben/No-Gos aus dem Gespräch) ---------------------
    "PRAEFERENZ_VORSCHLAG": (
        "📝 Aus unserem Gespräch – soll das ins Profil?\n```\n{diff}\n```\n"
        "_No-Gos werden nur ergänzt oder um Ausnahmen präzisiert, nie entfernt._"
    ),

    # --- Skills / Wissens-Briefe ----------------------------------------------------
    "SKILL_HEADER": "📚 *Wissen – {kategorie}* (_{source}, Stand {stand}_)\n\n",
    "SKILL_LERNE_USAGE": (
        "ℹ️ So nutzt du /lerne:\n"
        "`/lerne <kategorie>`\n\n"
        "Beispiele: `/lerne Spanking`, `/lerne pegging`, `/lerne blowjob_training`\n"
        "Die verfügbaren Kategorien siehst du in /aufgaben_alle."
    ),
    "SKILL_LERNE_NEU_USAGE": "ℹ️ Nutze: `/lerne_neu <kategorie>` – ersetzt einen vorhandenen Eintrag.",
    "SKILL_BEARBEITEN_USAGE": "ℹ️ Nutze: `/skill_bearbeiten <kategorie>` – danach schickst du den neuen Text.",
    "SKILL_GENERIERE": "📚 Erstelle Wissens-Brief zu *{kategorie}*… einen Moment.",
    "SKILL_GENERIERE_NEU": "📚 Generiere neu zu *{kategorie}*… einen Moment.",
    "SKILL_BEARBEITEN_HINWEIS": (
        "ℹ️ Den Text kannst du jederzeit mit `/skill_bearbeiten {kategorie}` überschreiben "
        "oder mit `/lerne_neu {kategorie}` neu generieren lassen."
    ),
    "SKILL_EDIT_START": (
        "✏️ *Bearbeiten – {kategorie}*\n\nAktueller Stand:\n\n{aktuell}\n\n"
        "_Schicke mir jetzt die neue Version als Nachricht. Mit /abbrechen verwirfst du._"
    ),
    "SKILL_EDIT_ABGEBROCHEN": "✅ Bearbeitung abgebrochen, alter Stand bleibt.",
    "SKILL_EDIT_ZU_KURZ": (
        "⚠️ Das wirkt sehr knapp. Schicke den vollständigen Text "
        "oder /abbrechen zum Verwerfen."
    ),
    "SKILL_GESPEICHERT": "✅ *{kategorie}* gespeichert (Quelle: manuell).",
    # Bewusst OHNE Exception-Details (Exception-Text nicht roh an den User leaken)
    "SKILL_GENERIEREN_FEHLER": "⚠️ Konnte den Wissens-Brief gerade nicht erstellen. Versuch es gleich nochmal.",
    "SKILL_SPEICHERN_FEHLER": "⚠️ Konnte nicht speichern. Versuch es gleich nochmal.",
    "SKILL_KEINE": (
        "📚 Noch keine Wissens-Einträge.\n"
        "Starte mit `/lerne <kategorie>` – Grok erstellt dir einen Basis-Eintrag."
    ),
    "SKILL_LISTE_TITEL": "📚 *Vorhandene Wissens-Einträge:*",
    "SKILL_LISTE_LEGENDE": "\n✏️ = manuell überschrieben, 🤖 = Grok-generiert",

    # --- Wünsche ----------------------------------------------------------------
    "WUNSCH_EINREICHEN": (
        "🙏 *Wunsch einreichen*\n\n"
        "Sag mir, was du dir wünschst – ich entscheide, ob du es bekommst.\n"
        "Formuliere es respektvoll.\n\n"
        "Schreib deinen Wunsch oder /abbrechen"
    ),
    "WUNSCH_KEINE_GESAMMELT": (
        "Ich habe noch keine Wünsche von dir gesammelt. Erwähn einfach im Chat, "
        "was du mal ausprobieren möchtest – ich merke es mir."
    ),
    "WUNSCH_LISTE": "🗒 *Deine gesammelten Wünsche:*\n{liste}\n\nZum Entfernen tippe einen Knopf:",
    "WUNSCH_ALLE_GELOESCHT": "🗑 Alle gesammelten Wünsche gelöscht.",
    "WUNSCH_EINTRAG_WEG": "Der Eintrag ist nicht mehr da – tippe /meinewuensche für die aktuelle Liste.",
    "WUNSCH_BEREITS_ENTSCHIEDEN": "Dieser Wunsch ist bereits entschieden.",
    "WUNSCH_LISTE_LEER": "🗑 Entfernt. Du hast keine gesammelten Wünsche mehr.",
    # Bewusst statische Persona-Bestätigung (Stimme der Herrin)
    "WUNSCH_ANGEKOMMEN": "Angekommen. Ob ich ihn dir gewähre, überlege ich mir – hab etwas Geduld. 🖤",
    "WUNSCH_AN_DOMINA": (
        "📬 *Wunsch deines Sklaven:*\n\n{text}\n\n"
        "Wähle direkt oder antworte als Text mit Kommentar (z.B. _annehmen mal sehen_)."
    ),
    "WUNSCH_AN_DOMINA_WARTEND": (
        "📬 *Wunsch deines Sklaven (wartet auf dich):*\n\n{text}\n\n"
        "_Tippe einen Button, sobald du Zeit hast._"
    ),
    "WUNSCH_ENTSCHIEDEN": "{emoji} Wunsch {entscheidung}.",
    "WUNSCH_ENTSCHEIDUNG_HINWEIS": "Bitte antworte mit *annehmen* oder *ablehnen* (optional mit Kommentar).",
    "WUNSCH_ENTSCHEIDUNG_GESPEICHERT": "✅ Entscheidung gespeichert: {entscheidung}",

    # --- Inspiration-Flow ----------------------------------------------------------
    "INSPIRATION_WARTE": "✨ Einen Moment, ich hole Inspiration für dich...",
    "INSPIRATION_VORSCHLAEGE": (
        "✨ 3 Inspirationen für dich:\n\n{raw}\n\n"
        "Ist etwas dabei das dich anspricht?\n"
        "Antworte mit ja oder nein"
    ),
    "INSPIRATION_NEUE_VORSCHLAEGE": (
        "✨ 3 neue Inspirationen:\n\n{raw}\n\n"
        "Ist diesmal etwas dabei?\n"
        "Antworte mit ja oder nein"
    ),
    "INSPIRATION_NUMMER_FRAGE": (
        "Welchen Vorschlag möchtest du als Vorlage speichern?\n"
        "Antworte mit `1`, `2` oder `3`"
    ),
    "INSPIRATION_FEEDBACK_FRAGE": (
        "Was hat nicht gepasst? Beschreibe kurz warum die Vorschläge "
        "nicht das Richtige waren."
    ),
    "INSPIRATION_NUR_123": "Bitte antworte mit `1`, `2` oder `3`",
    "INSPIRATION_UNGUELTIGE_NUMMER": "Ungültige Nummer.",
    "INSPIRATION_VORLAGE_GESPEICHERT": (
        "✅ Vorschlag {nummer} als Vorlage gespeichert!\n"
        "Du kannst ihn jederzeit mit /vorlagen abrufen."
    ),
    "INSPIRATION_COACH_HINWEIS": "💬 Coach-Hinweis:\n\n{erklaerung}",
    "INSPIRATION_NEU_GENERIEREN": "Ich generiere neue Vorschläge basierend auf deinem Feedback...",

    # --- Profil-Anzeige/-Edit (MarkdownV2 – Werte am Callsite mit escape_md) --------
    "PROFIL_KEIN": "Kein Profil gefunden. Bitte schreibe eine Nachricht um das Onboarding zu starten.",
    "PROFIL_DOMINA": (
        "👤 *Dein Profil*\n\n"
        "1️⃣ Erfahrungsstand: {erfahrungsstand}\n"
        "2️⃣ Interessen: {interessen}\n"
        "3️⃣ Grenzen: {grenzen}\n"
        "4️⃣ Ziele: {ziele}\n"
        "5️⃣ Tempo: {tempo}\n"
        "6️⃣ Kinderfreie Zeiten: {zeiten}\n"
        "7️⃣ Kinder im Haushalt: {kinder}\n"
        "\nLevel: {level}\n\n"
        "✏️ Was möchtest du ändern\\?\n"
        "Schreibe die Nummer \\(1\\-7\\) oder /abbrechen"
    ),
    "PROFIL_SKLAVE": (
        "👤 *Dein Profil*\n\n"
        "1️⃣ Absolute Grenzen: {hard_limits}\n"
        "2️⃣ Vorlieben: {vorlieben}\n"
        "3️⃣ Erfahrungsstand: {erfahrungsstand}\n\n"
        "✏️ Was möchtest du ändern\\?\n"
        "Schreibe die Nummer \\(1\\-3\\) oder /abbrechen"
    ),
    "PROFIL_WUNSCH_WARTET": (
        "\n\n📬 Es wartet noch ein Wunsch deines Sklaven auf deine Entscheidung.\n"
        "Antworte mit *annehmen* oder *ablehnen*."
    ),
    "PROFIL_ZAHL_BEREICH": "Bitte eine Zahl zwischen 1 und {max} eingeben oder /abbrechen",
    "PROFIL_NEUER_WERT": "✏️ *{feld}*\n\nNeuer Wert:",
    "PROFIL_GANZE_ZAHL": "Bitte eine ganze Zahl eingeben.",
    "PROFIL_GESPEICHERT_PREFIX": "✅ Gespeichert\\!\n\n",

    # --- Wochenplanung ----------------------------------------------------------------
    "BUTTON_WOCHENPLAN_ALLE": "✅ Alle als Aufgaben erteilen",
    "BUTTON_WOCHENPLAN_VERWERFEN": "🗑 Nur Vorschlag",
    "WOCHENPLAN_THEMA_FRAGE": (
        "📅 *Wochenplanung*\n\n"
        "Gibt es ein Thema oder einen Fokus für diese Woche?\n\n"
        "z.B. _'mehr Rituale'_, _'Gehorsam stärken'_, oder _'einfach abwechslungsreich'_\n\n"
        "Schreibe dein Thema oder /abbrechen"
    ),
    "WOCHENPLAN_WARTE": "⏳ Erstelle deinen Wochenplan...",
    "WOCHENPLAN_TITEL": "📅 Dein Wochenplan:",
    "WOCHENPLAN_FEHLER": "⚠️ Fehler beim Erstellen des Wochenplans.",
    "WOCHENPLAN_NUR_VORSCHLAG": "👍 Bleibt nur ein Vorschlag.",
    "WOCHENPLAN_NICHT_IM_SPEICHER": "⚠️ Plan nicht mehr im Speicher – erstelle ihn neu mit /wochenplanung.",
    "WOCHENPLAN_ERSTELLT": "✅ {anzahl} Aufgaben aus dem Wochenplan erstellt – Tag 1 startet jetzt, der Rest folgt täglich.",
    "WOCHENPLAN_UEBERSPRUNGEN": "\n({anzahl} wegen Grenzen oder fehlendem Text übersprungen.)",

    # --- Serie ---------------------------------------------------------------------
    "SERIE_FRAGE": (
        "🔄 Soll diese Aufgabe als *Serie* erteilt werden?\n\n"
        "Antworte mit `{optionen}` für die Anzahl Tage\n"
        "oder `nein` für einmalige Aufgabe."
    ),
    "SERIE_OPTIONEN_HINWEIS": "Bitte antworte mit `{optionen}` oder `nein`",
    "SERIE_DISLIKE_WARNUNG": (
        "⚠️ Achtung: Dein Sklave hat *{kategorie}* negativ bewertet "
        "({anzahl}x negativ). "
        "Die Serie wird trotzdem erstellt – aber der Sklave wird wahrscheinlich nicht begeistert sein.\n\n"
        "_Serie wird jetzt erstellt…_"
    ),
    "SERIE_GESPEICHERT": "🔄 Serie gespeichert! {tage} Tage {hinweis}.\nTag 1 startet jetzt.",
    "SERIE_HINWEIS_BOGEN": "als aufbauender Bogen",
    "SERIE_HINWEIS_TAEGLICH": "täglich",

    # --- Training ------------------------------------------------------------------
    "TRAINING_WARTE": "🧠 *Psycho-Training – {typ}*\n\nEinen Moment...",
    "TRAINING_UEBUNG": "🧠 {typ}:\n\n{uebung}\n\nSchreibe deine Gedanken oder Antwort. /abbrechen zum Beenden.",
    "TRAINING_BEENDET": "✅ Training beendet.",
    "TRAINING_FEEDBACK_PREFIX": "💬 *Coach-Feedback:*\n\n{feedback}",
    "TRAINING_TAEGLICH": (
        "🧠 Tägliches Training – {typ}:\n\n{uebung}\n\n"
        "Schreibe deine Gedanken oder /abbrechen zum Überspringen."
    ),

    # --- Namen / Persona-Settings -----------------------------------------------------
    "NAMEN_BOTNAME_ANZEIGE": "Aktueller Bot-Name: {aktuell}\n\nSetzen: /botname <Name>\nEntfernen: /botname -",
    "NAMEN_BOTNAME_GESETZT": "✅ Bot-Name gesetzt: *{name}* — gilt für beide Seiten.",
    "NAMEN_BOTNAME_ENTFERNT": "✅ Bot-Name entfernt – sie ist wieder „deine Herrin“.",
    "NAMEN_SETUP_ANZEIGE": (
        "Aktueller Setup-Kontext:\n{aktuell}\n\n"
        "Setzen: /setup <Beschreibung>\n"
        "z.B. /setup Herrin weiblich, penetriert ihn mit Strapon. Sperma stammt von ihm selbst "
        "(ruinierte Orgasmen). Creampie-Cleanup = er leckt sein eigenes Sperma auf.\n"
        "Entfernen: /setup -"
    ),
    "NAMEN_SETUP_GESETZT": "✅ Setup-Kontext gesetzt – der Bot richtet sich jetzt danach.",
    "NAMEN_SETUP_ENTFERNT": "✅ Setup-Kontext entfernt.",
    "NAMEN_ANREDE_ANZEIGE": "Aktuelle Sklaven-Anrede: {aktuell}\n\nSetzen: /sklavenname <Anrede>\nEntfernen: /sklavenname -",
    "NAMEN_ANREDE_GESETZT": "✅ Sklaven-Anrede gesetzt: *{name}*.",
    "NAMEN_ANREDE_ENTFERNT": "✅ Sklaven-Anrede entfernt – wieder neutral.",

    # --- Würfel ---------------------------------------------------------------------
    "WUERFEL_GEFALLEN": "🎲 *Würfel gefallen!*\n\nKategorie: *{kategorie}*\n\nIch generiere eine Aufgabe...",
    "WUERFEL_GEFALLEN_WURF": "🎲 *Eine {wert}!*\n\nKategorie: *{kategorie}*\n\nIch generiere eine Aufgabe...",
    "WUERFEL_GRENZEN": "⚠️ Würfel ergab eine Aufgabe gegen eure Grenzen ({treffer}) – probier nochmal.",
    "BUTTON_ALS_TASK_ERTEILEN": "✅ Als Task erteilen",
    "WUERFEL_VORSCHLAG": "🎲 *Würfel-Aufgabe für den Sklaven ({kategorie}):*\n\n{aufgabe}\n\n_Vorschau – er bekommt sie erst, wenn du sie erteilst._",
    "WUERFEL_FEHLER": "⚠️ Konnte keine Aufgabe generieren.",
    "WUERFEL_VERWORFEN": "❌ Würfel-Aufgabe verworfen.",
    "WUERFEL_STATE_WEG": "⚠️ Aufgabe nicht mehr im State – würfle nochmal.",
    "WUERFEL_BEFEHL_PREFIX": "🎲 Der Würfel hat entschieden:\n\n{anweisung}",
    "WUERFEL_ERTEILT": "✅ Würfel-Aufgabe als Task erteilt (Kategorie: {kategorie})",

    # Lücken-Füller (luecke.py / luecken_*_job)
    "LUECKE_VORSCHLAG": "🕊️ *Seit {tage} Tagen lief keine Aufgabe für ihn.* Mein Vorschlag – du entscheidest:\n\n{vorschlag}\n\n_Er bekommt sie erst, wenn du freigibst._",
    "LUECKE_VORSCHLAG_NEU": "🔄 *Neuer Vorschlag – du entscheidest:*\n\n{vorschlag}\n\n_Er bekommt sie erst, wenn du freigibst._",
    "BUTTON_LUECKE_JETZT": "✅ Jetzt senden",
    "BUTTON_LUECKE_ABEND": "🌙 Heute Abend",
    "BUTTON_LUECKE_ANDERER": "🔄 Anderer Vorschlag",
    "BUTTON_LUECKE_HEUTE_NICHT": "🚫 Heute nicht",
    "LUECKE_GESENDET_JETZT": "✅ Erledigt – er hat die Aufgabe.",
    "LUECKE_GEPLANT_ABEND": "🌙 Gespeichert – geht heute Abend an ihn raus.",
    "LUECKE_HEUTE_NICHT": "🚫 Okay, heute nicht. Ich melde mich in ein paar Tagen wieder.",
    "LUECKE_STATE_WEG": "⚠️ Der Vorschlag ist nicht mehr aktuell – ignorier den alten Button.",
    "LUECKE_KEIN_VORSCHLAG": "⚠️ Mir fällt gerade nichts Sauberes ein – ich versuch's später nochmal.",
    "LUECKE_TOGGLE_AN": "🕊️ Lücken-Füller *an*. Wenn länger keine Aufgabe läuft, schlage ich dir was vor – erteilt wird nur, was du freigibst.",
    "LUECKE_TOGGLE_AUS": "🕊️ Lücken-Füller *aus*.",

    # Abwesenheit (abwesenheit.py) – beide Rollen dürfen setzen/aufheben
    "ABWESEND_STATUS_AKTIV": "📆 Abwesenheit eingetragen: {zeitraum}{grund}.\nAlles läuft normal weiter – Aufgaben und Vorschläge berücksichtigen die Abwesenheit. Aufheben mit /abwesend ende.",
    "ABWESEND_STATUS_KEINE": "📆 Keine Abwesenheit eingetragen.\nEintragen z. B. mit: /abwesend 20.07.-02.08. Dienstreise – oder /abwesend 2 wochen.",
    "ABWESEND_GESETZT": "📆 Eingetragen: abwesend {zeitraum}{grund}.\nAlles läuft normal weiter – Aufgaben und Vorschläge berücksichtigen den Zeitraum (nichts, was Anwesenheit zu Hause erfordert). Früher zurück? /abwesend ende.",
    "ABWESEND_AUFGEHOBEN": "📆 Abwesenheit aufgehoben – gilt wieder als zu Hause.",
    "ABWESEND_UNVERSTANDEN": "⚠️ Zeitraum nicht verstanden. Beispiele: /abwesend 20.07.-02.08. Dienstreise · /abwesend 2 wochen · /abwesend bis Sonntag · /abwesend ende",
    "ABWESEND_PARTNER_GESETZT": "📆 Info: Abwesenheit wurde eingetragen – {zeitraum}{grund}. Aufgaben und Vorschläge berücksichtigen das; ändern jederzeit mit /abwesend.",
    "ABWESEND_PARTNER_AUFGEHOBEN": "📆 Info: Die Abwesenheit wurde aufgehoben – gilt wieder als zu Hause.",

    # --- Gefühl-/Erledigungs-Mechanik ---------------------------------------------------
    "GEFUEHL_BEWERTUNG_FRAGE": "⭐ Wie fandest du, wie er das gemacht hat? Gib ihm 1-5.",
    "GEFUEHL_WUERFEL_ABZEICHEN": "🏅 Neue Abzeichen: {liste}",
    "GEFUEHL_PUNKTE": "⭐ +{punkte} Punkte _(gesamt: {gesamt})_",
    "GEFUEHL_STREAK_SUFFIX": "\n🔥 Streak: *{streak}*",
    "GEFUEHL_ABZEICHEN_VERDIENT": "🎖 *Neues Abzeichen verdient:*\n\n{vorschlag}",
    "GEFUEHL_LEVEL_TEASER": (
        "✨ *Noch {fehlend} Punkt{plural} bis Level {level}!*\n\n"
        "Du bist kurz davor — was wäre eine passende nächste Aufgabe für ihn?"
    ),
    # Gemeinsamer Prefix für Ketten-Freischaltung (gefuehl.py + kette_adaptiv.py)
    "KETTE_FREIGESCHALTET": "🔗 Der nächste Schritt, {pos} von {gesamt}:\n\n{anweisung}",

    # --- Followup-Antwort / Bestrafung ----------------------------------------------------
    "BUTTON_ERLEDIGT": "✅ Erledigt",
    "BUTTON_NICHT_ERLEDIGT": "❌ Nicht erledigt",
    "FOLLOWUP_KLARSTELLUNG": "Sag's mir klar – erledigt oder nicht? Nutz die Knöpfe oder schreib ja/nein.",
    "FOLLOWUP_ERST_BEANTWORTEN": "Beantworte erst meine offene Frage – danach kümmern wir uns um die nächste Aufgabe.",
    "BESTRAFUNG_KEIN_VORSCHLAG": (
        "⚠️ Konnte keinen Grenzen-konformen Bestrafungsvorschlag generieren. "
        "Bitte erteile eine Strafe manuell."
    ),
    "BESTRAFUNG_LABEL_ESKALATION": "🚨 *Eskalation – Wiederholtes Muster:*",
    "BESTRAFUNG_LABEL_VORSCHLAG": "⚠️ *Bestrafungsvorschlag:*",

    # --- Rollenspiel (Liste/Aktiv-Meldung sind MarkdownV2) --------------------------------
    "ROLLENSPIEL_LISTE_TITEL": "🎭 *Rollenspiel – Wähle ein Szenario:*\n",
    "ROLLENSPIEL_LISTE_FUSS": "\nSchreibe eine Nummer \\(1\\-5\\) oder beschreibe dein eigenes Szenario\\.",
    "ROLLENSPIEL_ABBRECHEN_HINWEIS": "Oder /abbrechen",
    "ROLLENSPIEL_INTENSITAET_FRAGE": "🎭 Szenario: *{name}*\n\nWähle die Intensität:\n{liste}",
    "ROLLENSPIEL_1_2_3": "Bitte wähle 1, 2 oder 3.",
    "ROLLENSPIEL_AKTIV": (
        "🎭 *{prefix}Szenario aktiv: {name}*\n"
        "Intensität: {intensitaet}\n\n"
        "Der Modus ist jetzt aktiv\\. Schreibe einfach weiter – ich passe meine Antworten an\\.\n"
        "/rollenspiel\\_beenden zum Beenden\\."
    ),
    "ROLLENSPIEL_BEENDET": "✅ Rollenspiel '{name}' beendet.\n\nWir sind wieder im normalen Modus.",
    "ROLLENSPIEL_AUTO_BEENDET": "\U0001F3AD Euer Rollenspiel '{name}' lag seit Tagen unbeendet herum – ich habe es still beendet. Mit /rollenspiel startet ihr jederzeit ein neues.",
    "ROLLENSPIEL_KEIN_AKTIV": "✅ Kein aktives Rollenspiel.",

    # --- Lerntagebuch ----------------------------------------------------------------------
    "LERNTAGEBUCH_WARTE": "📓 Verdichte Coach-Gespräche der letzten {days} Tage... einen Moment.",
    "LERNTAGEBUCH_LEER": "📓 Im Zeitraum {zeitraum} gab es keine Coach-Gespräche zum Verdichten.",
    "LERNTAGEBUCH_FEHLER": "⚠️ Fehler beim Erzeugen des Lerntagebuchs: {fehler}",
    "LERNTAGEBUCH_HEADER": "📓 *Lerntagebuch gespeichert* ({zeitraum}, {anzahl} Gespräche)\n\n",
    "LERNTAGEBUCH_GEKUERZT": "\n\n_(gekürzt – vollständig im Coach-Gedächtnis)_",

    # --- Resurface ----------------------------------------------------------------------------
    "RESURFACE_UEBERSPRUNGEN": "⏭ Übersprungen.",
    "RESURFACE_DISLIKE": (
        "⚠️ Kategorie *{kategorie}* ist auf der Dislike-Liste deines Sklaven. "
        "Aufgabe übersprungen."
    ),
    "RESURFACE_GRENZEN": (
        "⚠️ Die alte Aufgabe verstößt gegen eure AKTUELLEN Grenzen ({treffer}). "
        "Aufgabe übersprungen."
    ),
    "RESURFACE_PREFIX": "🕰 *Eine bewährte Aufgabe für dich:*\n\n{anweisung}",
    "RESURFACE_ERTEILT": "✅ Aufgabe erneut erteilt (Kategorie: {kategorie})",

    # --- Ziele ------------------------------------------------------------------------------------
    "ZIELE_KEINE": (
        "Du hast noch keine Ziele gesetzt. Schreibe mir was du erreichen möchtest "
        "oder aktualisiere dein Profil mit /profil."
    ),
    "ZIELE_WARTE": "📊 Einen Moment, ich analysiere deinen Fortschritt...",
    "ZIELE_PREFIX": "🎯 *Deine Ziele & Fortschritt:*\n\n_Deine Ziele:_ {ziele}\n\n{analyse}",
    "ZIELE_ERINNERUNG_PREFIX": "🎯 *Wöchentliche Ziel-Erinnerung:*\n\n{erinnerung}",

    # --- Geheimnis ------------------------------------------------------------------------------------
    "GEHEIMNIS_START": (
        "🔒 *Geheimnis hinterlegen*\n\n"
        "Du kannst dem Sklaven eine geheime Information hinterlassen, "
        "die er erst zu einem bestimmten Zeitpunkt erfährt.\n\n"
        "Schreibe das Geheimnis oder /abbrechen"
    ),
    "GEHEIMNIS_DATUM_FRAGE": (
        "📅 Wann soll das Geheimnis enthüllt werden?\n\n"
        "Format: *TT.MM.YYYY HH:MM* oder *in X Tagen*\n"
        "Beispiel: _25.12.2025 20:00_ oder _in 7 Tagen_"
    ),
    "GEHEIMNIS_DATUM_FEHLER": (
        "⚠️ Datum konnte nicht erkannt werden.\n"
        "Bitte verwende das Format *TT.MM.YYYY HH:MM* oder *in X Tagen*"
    ),
    "GEHEIMNIS_GESPEICHERT": "✅ Geheimnis gespeichert!\n\nEnthüllung am: *{datum}*",

    # --- Wunsch-Kategorien --------------------------------------------------------------------------------
    "WUNSCHKAT_MENU": (
        "🎯 *Deine Wunsch-Kategorien*\n\n"
        "Aktuell: _{aktuell}_\n\n"
        "Du kannst bis zu *{max}* Lieblings-Kategorien wählen.\n"
        "Ich lasse sie in meine Vorschläge einfließen – entscheiden tue weiterhin ich. 🖤\n\n"
        "*Verfügbar:*\n{katalog}\n\n"
        "Antworte mit den Nummern getrennt durch Komma (z.B. `3, 14, 38`).\n"
        "Fehlt dir etwas? Schreib es einfach als Text dazu (z.B. `3, Cuckold`) – "
        "dann lege ich es als eigene Kategorie an.\n"
        "Oder schreibe `keine` um deine Wahl zu löschen, oder /abbrechen"
    ),
    "WUNSCHKAT_KEINE_NUMMERN": "Keine gültige Auswahl erkannt. Nochmal bitte.",
    "WUNSCHKAT_EIGENE_NEU": "\n\n🆕 Als eigene Kategorie neu angelegt: {liste}",
    "WUNSCHKAT_MAX": "Maximal {max} Kategorien. Du hast {anzahl} angegeben.",
    "WUNSCHKAT_BEREICH": "Nummer {n} ist außerhalb des gültigen Bereichs (1-{max}).",
    "WUNSCHKAT_GESPEICHERT": (
        "Notiert:\n{liste}\n\n"
        "Ob du davon etwas bekommst, entscheide ich. 🖤"
    ),
    "WUNSCHKAT_ZURUECKGESETZT": "✅ Deine Wunsch-Kategorien wurden zurückgesetzt.",

    # --- Adaptive Kette ---------------------------------------------------------------------------------------
    "BUTTON_ANPASSUNG_SENDEN": "✅ Anpassung senden",
    "BUTTON_ORIGINAL_SENDEN": "🗑 Original senden",
    "KETTE_ANPASSUNG_VORSCHLAG": (
        "🔗 *Kette {pos}/{gesamt} – Anpassung?*\n\n"
        "Er hat die letzte Aufgabe als _{stimmung}_ erlebt. "
        "Vorschlag für die nächste:\n\n"
        "➡️ {adapted}\n\n"
        "_Original:_ {original}"
    ),
    "KETTE_GESENDET": "✅ {label} Aufgabe an ihn gesendet.",
    "KETTE_FEHLSCHLAG_FRAGE": (
        "🔗 *Kette: Glied {pos}/{gesamt} wurde nicht erledigt.*\n\n"
        "Nächstes Glied wäre:\n_{naechste}_\n\n"
        "Soll die Kette weiterlaufen oder abgebrochen werden?"
    ),
    "KETTE_HAENGT_FRAGE": (
        "🔗 *Kette hängt: Glied {pos}/{gesamt} wartet, aber nichts ist mehr in Arbeit.*\n\n"
        "Das wartende Glied:\n_{naechste}_\n\n"
        "Soll die Kette weiterlaufen oder abgebrochen werden?"
    ),
    "BUTTON_KETTE_WEITER": "▶️ Weiterführen",
    "BUTTON_KETTE_ABBRECHEN": "🛑 Kette abbrechen",
    "KETTE_WEITER_BESTAETIGT": "▶️ Kette läuft weiter – Glied {pos}/{gesamt} an ihn gesendet.",
    "KETTE_ABGEBROCHEN_DOMINA": "🛑 Kette abgebrochen – {anzahl} verbleibende(s) Glied(er) verworfen.",
    "KETTE_BEREITS_ENTSCHIEDEN": "⚠️ Über diese Kette wurde bereits entschieden.",

    # --- Kommentar / Meine Aufgaben / Reaktion ---------------------------------------------------------------------
    "KOMMENTAR_PREFIX": "💬 Rückmeldung deiner Herrin:\n\n{kommentar}",
    "MEINEAUFGABEN_KEINE": "Du hast gerade keine offenen Aufgaben. 🖤",
    "MEINEAUFGABEN_TITEL": "📋 *Deine offenen Aufgaben:*\n",
    "BUTTON_NR_ABSCHLIESSEN": "✅ Nr. {nr} abschließen",
    "MEINEAUFGABEN_NICHT_OFFEN": "Diese Aufgabe ist nicht mehr offen.",
    "REAKTION_ALTERNATIV_FRAGE": "Was soll ich ihm stattdessen ausrichten?",
    "REAKTION_ANGEORDNET": "✅ Bestrafung wurde angeordnet.",
    "REAKTION_WEITERGELEITET": "✅ Deine Nachricht wurde weitergeleitet.",

    # --- Tiny-Task-Feedback ------------------------------------------------------------------------------------------
    "BUTTON_UEBERNOMMEN": "✅ Übernommen",
    "BUTTON_GUT_NICHT_HEUTE": "👌 Gut, aber nicht heute",
    "TINYFB_FRAGE": (
        "💬 *Kurze Rückfrage zum heutigen Vorschlag*\n\n"
        "Vorschlag (Kategorie: _{kategorien}_):\n"
        "_{inhalt}_\n\n"
        "Du hast ihn (noch) nicht weitergeleitet. Wähle direkt oder schreibe eine Begründung "
        "als Text (z.B. 'zu komplex', 'falsche Stimmung')."
    ),
    "TINYFB_KEIN_OFFENER": "Kein offener Tiny-Task-Vorschlag der letzten 72h gefunden.",
    "TINYFB_ANTWORT_UEBERNOMMEN": "✅ Notiert als _übernommen_.",
    "TINYFB_ANTWORT_GUT": "👌 Notiert. Wird positiv für zukünftige Vorschläge gewertet.",
    "TINYFB_NOTIERT": "📝 Notiert: _{grund}_\nWird in zukünftigen Vorschlägen berücksichtigt.",

    # --- Strafen-Protokoll -----------------------------------------------------------------------------------------------
    "STRAFEN_KEINE": "📋 Noch keine Strafen protokolliert.",
    "STRAFEN_TITEL": "📋 *Strafen-Protokoll (letzte 10):*\n\n",

    # --- Einstellungen -----------------------------------------------------------------------------------------------------
    # Sicherheitsrelevanter Hinweis: die Grenzen-Prüfung (limits_check) nutzt eine
    # DEUTSCHE Synonym-Liste – bei anderer Sprache greift nur das wörtliche Matching.
    "EINSTELLUNGEN_SPRACHE_LIMITS_WARNUNG": (
        "⚠️ *Wichtig zur Sicherheit:* Die automatische Grenzen-Prüfung arbeitet mit "
        "deutschen Begriffslisten. Bei Antworten auf {sprache} werden nur die wörtlich "
        "hinterlegten Limit-Begriffe erkannt – Umschreibungen nicht. "
        "Hinterlegt eure Hard Limits am besten zusätzlich auf {sprache} (/profil)."
    ),
    "EINSTELLUNGEN_ZAHL_HINWEIS": "Bitte eine Zahl zwischen 1 und 8 eingeben oder /abbrechen",
    "EINSTELLUNGEN_FELD_PROMPT": "✏️ *{label}*\n\n{hinweis}",
    "EINSTELLUNGEN_STIL_UNBEKANNT": "Das kenne ich nicht. {hinweis}",
    # MarkdownV2 (wird mit dem MarkdownV2-Menü kombiniert) – "!" muss escaped sein
    "EINSTELLUNGEN_GESPEICHERT": "✅ Gespeichert\\!",

    # --- Bewertung / Rest-Kleinkram ---------------------------------------------------------------------------------------
    "BEWERTUNG_1_5": "Bitte bewerte mit 1-5",
    "BEWERTUNG_KOMPLEX_HOCH": "📈 Deine letzten Aufgaben haben dir sehr gut gefallen – ich erhöhe die Komplexität!",
    "BEWERTUNG_KOMPLEX_NIEDRIG": "📉 Ich passe die Aufgaben-Komplexität an deine Präferenzen an.",
    "BEWERTUNG_KOMPLEX_NORMAL": "📊 Aufgaben-Komplexität zurück auf normal angepasst.",
    "BEWERTUNG_TIPP_PREFIX": "💡 {tipp}",
    "BEWERTUNG_KOMMENTAR_FRAGE": "Hab ich. Magst du ihm noch was mitgeben? (sonst /ueberspringen)",
    "BEWERTUNG_NOTIERT": "Hab ich – notiert.",
    "KETTE_NICHT_VORHANDEN": "⚠️ Diese Ketten-Aufgabe ist nicht mehr vorhanden.",
    "TRAINING_DEAKTIVIERT": "Training ist aktuell deaktiviert.",
    "SERIE_EINMALIG": "✅ Einmalige Aufgabe gespeichert und weitergeleitet.",
    "KOMMENTAR_GESENDET": "✅ Hat er bekommen.",
    "KOMMENTAR_UEBERSPRUNGEN": "✅ Kommentar übersprungen.",
    # Bewusst ohne interne Begriffe ("State"/"Qdrant" gehören nicht in Nutzer-Texte)
    "RESURFACE_STATE_WEG": "⚠️ Die Auswahl ist nicht mehr aktiv – warte auf den nächsten Vorschlag.",
    "RESURFACE_NICHT_GEFUNDEN": "⚠️ Die alte Aufgabe ist nicht mehr auffindbar.",

    "DOSSIER_WARTE": "⏳ Ich verdichte, was ich über ihn weiß …",
    "DOSSIER_ZU_WENIG": (
        "Noch zu wenig Material für ein Dossier – es entsteht, sobald er Aufgaben "
        "erledigt, Gefühle teilt und ihr chattet."
    ),
    "DOSSIER_PREFIX": "🗒 *Was ich über ihn weiß:*\n\n{text}",

    # --- Pairing (Registrierung neuer Paare, PAIRING_ENABLED) ---
    "PAIRING_START_BEKANNT": "Wir sind schon verbunden. 😉 /hilfe zeigt dir alles, was ich kann.",
    "PAIRING_START_MENUE": (
        "👋 Willkommen! Dieser Bot begleitet euch als Paar.\n\n"
        "Welche Rolle übernimmst du?\n"
        "1 – der dominante Part\n"
        "2 – der devote Part\n\n"
        "Du bekommst dann einen Einladungs-Code für deinen Partner.\n"
        "Hast du schon einen Code? Schick ihn mir einfach."
    ),
    "PAIRING_ROLLE_UNGUELTIG": "Bitte antworte mit 1, 2 – oder schick mir einen Einladungs-Code.",
    "PAIRING_CODE_ERSTELLT": (
        "✅ Dein Einladungs-Code: `{code}`\n\n"
        "Dein Partner sendet mir /start und dann diesen Code – "
        "damit seid ihr verbunden. Der Code gilt {stunden} Stunden."
    ),
    "PAIRING_CODE_UNGUELTIG": (
        "Dieser Code ist ungültig oder abgelaufen. Prüf die Schreibweise – "
        "oder lass deinen Partner mit /start einen neuen erstellen."
    ),
    "PAIRING_ERFOLG": (
        "🎉 Ihr seid verbunden! Schreib mir einfach eine Nachricht, "
        "dann richte ich alles Weitere mit dir ein."
    ),
    "PAIRING_HINWEIS_START": "Sende /start, um loszulegen.",

    # --- Admin/Betreiber (nur ADMIN_CHAT_ID) ---
    "ADMIN_PAARE_KOPF": "👥 Registrierte Paare:",
    "ADMIN_INVITES_KOPF": "✉️ Offene Invites ({anzahl}):",
    "ADMIN_PAAR_LOESCHEN_USAGE": "Verwendung: /paar_loeschen <paar_id>",
    "ADMIN_PAAR_LOESCHEN_BESTAETIGUNG": (
        "⚠️ Paar {paar_id} (dom={dom}, sub={sub}) wirklich UNWIDERRUFLICH löschen?\n"
        "Alle Daten des Paares (Profile, Aufgaben, Chats, …) werden aus der "
        "Datenbank entfernt. Backups rotieren erst nach der Aufbewahrungsfrist heraus.\n\n"
        "Zum Bestätigen sende:\n/paar_loeschen {paar_id} LOESCHEN"
    ),
    "ADMIN_PAAR_GELOESCHT": "✅ Paar {paar_id} entfernt, {punkte} Datenpunkte gelöscht.",
    "ADMIN_PAAR_LOESCHEN_FEHLER": "⚠️ Fehler in Collections: {collections} – bitte Log prüfen und erneut ausführen.",
    "ADMIN_PAAR_UNBEKANNT": "Paar {paar_id} ist nicht registriert (/paare zeigt die Liste).",
    "ADMIN_PAAR_ENV": "Das Env-Paar wird über die .env verwaltet und kann hier nicht gelöscht werden.",
    "ADMIN_ZUGANG_BEENDET": "Dieser Bot-Zugang wurde beendet und die zugehörigen Daten wurden gelöscht.",
    "BUDGET_ERSCHOEPFT": (
        "⏸ Für heute ist das Nachrichten-Kontingent eures Paares aufgebraucht – "
        "morgen geht es weiter. Befehle wie /hilfe funktionieren weiterhin."
    ),
}
