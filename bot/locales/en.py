"""
English UI/flow texts (translated from the German reference locale).

Must carry exactly the same keys and {placeholders} as bot/locales/de.py –
missing keys fall back to German at runtime (bot/locales/__init__.lade).
Consistency is enforced by tests/test_locales.py.

Note: literal user-input tokens and command names the code matches against
(ja/nein, /abbrechen, …) are intentionally kept German until step 2 of the
publication plan localizes command parsing (see TODO 🚀).
"""

MESSAGES = {
    # --- Gemeinsame UI-Texte ----------------------------------------------
    "COMMON_ABGEBROCHEN": "✅ Cancelled.",
    "COMMON_ABGEBROCHEN_AUFGABE_BLEIBT": "✅ Cancelled. The task is kept.",
    "COMMON_JA_NEIN": "Please answer with yes or no.",
    "COMMON_NICHT_AUTORISIERT": "Not authorized.",
    "COMMON_TASK_NICHT_GEFUNDEN": "I can't find that task anymore right now.",
    "COMMON_BESCHAEFTIGT": "I'm busy at the moment. Please wait a bit.",
    "COMMON_NICHT_FUER_DICH": "This command is not for you.",
    # Markdown – gemeinsamer Text für Onboarding Schritt 6 und /profil-Edit
    # (beide nutzen zeiten.parse_kinderfreie_zeiten, der "keine"/"immer frei" akzeptiert)
    "COMMON_ZEITEN_UNVERSTANDEN": (
        "I didn't understand that as a time window. 🙈\n\n"
        "Please use the format *HH:MM-HH:MM*, multiple ones comma-separated –\n"
        "e.g. `20:00-23:00` or `07:00-08:00, 20:00-23:00`\n\n"
        "Or write *always free*"
    ),

    # --- Technische Fehler --------------------------------------------------
    "FEHLER_ALLGEMEIN": "⚠️ An error occurred. Please try again.",
    "FEHLER_LADEN": "⚠️ Error while loading. Please try again.",
    "FEHLER_KEINE_ANTWORT": "⚠️ I couldn't respond just now. Please try again.",

    # --- Safety: Safeword-Flow (bewusst statisch, NICHT per LLM) ------------
    # {wort} = config.RESUME_WORT (per Env konfigurierbar, Default "weiter")
    "SAFEWORD_PAUSIERT_HINWEIS": "The system is paused. Write '{wort}' to continue.",
    "SAFEWORD_PAUSIERT": "⛔ Safeword used. Everything is paused.\nWrite '{wort}' to continue.",
    "SAFEWORD_AKTIV": "✅ System active again.",

    # --- Persona-Fallbacks (Stimme der Herrin) bei LLM-Ausfall ---------------
    "FALLBACK_NICHT_ERLEDIGT": "So not that. We'll talk about this later.",
    "FALLBACK_GEFUEHL_REAKTION": "Hm. I'll let that sink in.",
    "FALLBACK_GEFUEHL_FRAGE": "Good. Now tell me: how was that for you?",
    "FALLBACK_FOLLOWUP_FRAGE": "Did you complete this: {aufgabe}?",
    "FALLBACK_WUNSCH_ANGENOMMEN": "I grant you your wish.",
    "FALLBACK_WUNSCH_ABGELEHNT": "I do not grant you your wish.",
    # War vorher "Entschuldigung, ein technischer Fehler …" – brach die Herrin-Fiktion.
    "FALLBACK_SKLAVE_CHAT": "I'm briefly unavailable right now. Write to me again in a moment.",
    "FALLBACK_STIMMUNG_REAKTION": "Hm. I'll let that settle.",

    # --- Medien-Weiterleitung (main.py) --------------------------------------
    "MEDIEN_VON_SKLAVE": "📎 Media from your slave:",
    "MEDIEN_VON_HERRIN": "📎 Media from your Mistress:",
    "MEDIEN_AN_HERRIN_WEITERGELEITET": "📎 Forwarded to your Mistress.",
    "MEDIEN_AN_SKLAVEN_WEITERGELEITET": "📎 Forwarded to your slave.",
    "MEDIEN_FEHLER": "⚠️ Could not forward the message.",

    # --- Onboarding (Markdown) ------------------------------------------------
    "ONBOARDING_ABGEBROCHEN": "❌ Onboarding cancelled. You can restart it anytime with /start.",
    "ONBOARDING_BEREIT_HINWEIS": "Write *yes* when you're ready.",
    "ONBOARDING_DOMINA_BEGRUESSUNG": (
        "👑 *Hi.*\n\n"
        "I'm your companion here – kind of like a best friend who's into the same scene. "
        "Let's quickly set up a profile for you, takes two minutes.\n\n"
        "Ready? Write *yes*."
    ),
    "ONBOARDING_DOMINA_SCHRITT_SPRACHE": (
        "🌍 *Step 1/9 – Language*\n\n"
        "In which language should the bot reply?\n\n"
        "1️⃣ German (default)\n"
        "2️⃣ English\n\n"
        "Write 1, 2 – or type another language"
    ),
    "ONBOARDING_DOMINA_SCHRITT_ROLLEN": (
        "🎭 *Step 2/9 – Role constellation*\n\n"
        "Which constellation do you play?\n\n"
        "{liste}\n\n"
        "Write the number\n"
        "_(determines forms of address, pronouns and the anatomy logic of the "
        "generated texts – can be changed later in /einstellungen)_"
    ),
    "ONBOARDING_DOMINA_SCHRITT_STIL": (
        "🖤 *Step 3/9 – Style*\n\n"
        "What style should the bot's dominant voice have?\n\n"
        "{liste}\n\n"
        "Write the number _(can be changed later in /einstellungen)_"
    ),
    "ONBOARDING_DOMINA_SCHRITT_ERFAHRUNG": (
        "📊 *Step 4/9 – Experience level*\n\n"
        "How would you describe your experience level?\n\n"
        "1️⃣ Beginner – I'm just getting started\n"
        "2️⃣ Some experience – I've had my first experiences\n"
        "3️⃣ Experienced – I know my way around\n\n"
        "Write 1, 2 or 3"
    ),
    "ONBOARDING_DOMINA_SCHRITT_INTERESSEN": (
        "✨ *Step 5/9 – Interests*\n\n"
        "What interests you the most? _(comma-separated)_\n\n"
        "Examples: rituals, service, obedience, mind games, punishments, body control"
    ),
    "ONBOARDING_DOMINA_SCHRITT_GRENZEN": (
        "🚫 *Step 6/9 – Limits*\n\n"
        "Are there absolute limits you want to set?\n"
        "_(comma-separated, e.g. blood, injuries)_\n\n"
        "Or write *none*"
    ),
    "ONBOARDING_DOMINA_SCHRITT_ZIELE": (
        "🎯 *Step 7/9 – Goals*\n\n"
        "What do you want to achieve with this bot?\n"
        "Briefly describe your goals as a Domme."
    ),
    "ONBOARDING_DOMINA_SCHRITT_TEMPO": (
        "⏱ *Step 8/9 – Pace*\n\n"
        "At what pace would you like to proceed?\n\n"
        "1️⃣ Slow – I'd rather ease into it carefully\n"
        "2️⃣ Normal – a balanced pace\n"
        "3️⃣ Fast – I want to make quick progress\n\n"
        "Write 1, 2 or 3"
    ),
    "ONBOARDING_DOMINA_SCHRITT_ZEITEN": (
        "👨‍👩‍👧 *Step 9/9 – Kid-free times*\n\n"
        "Are there times when kids are in the house?\n"
        "If so: when are you undisturbed?\n"
        "_(e.g. 20:00-23:00, or multiple comma-separated)_\n\n"
        "Or write *always free*"
    ),
    "ONBOARDING_SPRACHE_WAHL": "Please choose 1️⃣ or 2️⃣ – or type your language (e.g. French).",
    "ONBOARDING_ROLLEN_WAHL": "Please choose a number:\n\n{liste}",
    "ONBOARDING_STIL_WAHL": "I don't know that one. Please choose a number:\n\n{liste}",
    "ONBOARDING_ERFAHRUNG_WAHL": "Please choose 1️⃣, 2️⃣ or 3️⃣ – or briefly describe your experience level.",
    "ONBOARDING_ERFAHRUNG_NUR_ZAHLEN": "Please describe your experience level in your own words, not just with numbers.",
    "ONBOARDING_DOMINA_ZUSAMMENFASSUNG": (
        "✅ *Profile saved! Here's your summary:*\n\n"
        "🌍 Language: {sprache}\n"
        "🎭 Roles: {rollen}\n"
        "🖤 Style: {stil}\n"
        "👤 Experience level: {erfahrungsstand}\n"
        "✨ Interests: {interessen}\n"
        "🚫 Limits: {grenzen}\n"
        "🎯 Goals: {ziele}\n"
        "⏱ Pace: {tempo}\n"
        "👨‍👩‍👧 Kid-free times: {zeiten}\n\n"
        "──────────────────────\n\n"
        "📋 *Most important commands:*\n"
        "/profil – edit profile\n"
        "/inspiration – 3 task ideas\n"
        "/wochenplanung – create a weekly plan\n"
        "/training – start psycho training\n"
        "/stats – statistics (for slaves)\n\n"
        "All set! Just start writing – I'm your coach. 🖤"
    ),
    "ONBOARDING_SKLAVE_INFO_AKTIV": "I'm ready now. It begins – you'll hear from me. 🖤",
    "ONBOARDING_SKLAVE_BEGRUESSUNG": (
        "You will be speaking with your Mistress here.\n\n"
        "Before that starts, tell me three things about yourself – so she knows "
        "what's on the table and what isn't.\n\n"
        "──────────────────────\n\n"
        "🚫 *Step 1/3 – Absolute limits*\n\n"
        "What are your absolute limits?\n"
        "These will *NEVER* be crossed.\n"
        "_(comma-separated, e.g. blood, public humiliation)_\n\n"
        "Or write *none*"
    ),
    "ONBOARDING_SKLAVE_SCHRITT_2": (
        "💚 *Step 2/3 – Preferences*\n\n"
        "What do you especially enjoy? What gives you energy?\n"
        "_(comma-separated, e.g. rituals, physical tasks, reflection)_"
    ),
    "ONBOARDING_SKLAVE_SCHRITT_3": (
        "📖 *Step 3/3 – Experience level*\n\n"
        "How long have you been in this role?\n"
        "Briefly describe your experience level."
    ),
    "ONBOARDING_SKLAVE_GESPEICHERT": (
        "Good. I now know what I need to know.\n\n"
        "You will receive tasks from me. Always answer me honestly – "
        "that's the most important thing between us. 🖤"
    ),
    "ONBOARDING_DOMINA_INFO_SKLAVE_FERTIG": (
        "ℹ️ *Your slave has set up his profile.*\n\n"
        "Absolute limits: {limits}\n"
        "Preferences: {vorlieben}\n"
        "Experience level: {erfahrungsstand}\n\n"
        "The bot is now fully operational. 🖤"
    ),

    # --- Vorlagen (Markdown) --------------------------------------------------
    "VORLAGEN_TITEL": "📁 *Task templates*\n",
    "VORLAGEN_KEINE": "_No templates saved yet._\n",
    "VORLAGEN_AKTIONEN": "What would you like to do?\n`neu` = create a new template",
    "VORLAGEN_AKTIONEN_MIT_LISTE": "`1` (number) = send template as a task\n`l1` (l + number) = delete template",
    "VORLAGEN_ABBRECHEN_HINWEIS": "\nOr /abbrechen",
    "VORLAGEN_NAME_FRAGE": "📝 Name of the template:",
    "VORLAGEN_TEXT_FRAGE": "📝 Task text of the template:",
    "VORLAGEN_GELOESCHT": "🗑 Template deleted.",
    "VORLAGEN_BESTAETIGUNG": (
        "📋 Template:\n_{aufgabe}_\n\n"
        "Should I forward this task to him?\n"
        "Answer with `ja` or `nein`"
    ),
    "VORLAGEN_UNGUELTIG": "Invalid input. Please enter a number, `neu`, or /abbrechen",
    "VORLAGEN_GESPEICHERT": "✅ Template *{name}* saved!\n\nYou can access it anytime with /vorlagen.",

    # --- Domina-Chat: Aufgaben-/Ketten-Dialog ----------------------------------
    "DOMINA_AUFGABE_GRENZEN": (
        "⚠️ This task touches your limits ({treffer}) – "
        "I will *not* forward it to him. "
        "Rephrase it if you like."
    ),
    # MarkdownV2 – Platzhalter am Callsite mit escape_md übergeben
    "DOMINA_AUFGABE_ERKANNT": (
        "📋 Detected task:\n_{aufgabe}_\n\n"
        "Should I forward this task to him?\n"
        "Answer with `ja` or `nein`"
    ),
    "DOMINA_AUFGABE_VERWORFEN": "✅ Task discarded. No problem!",
    "DOMINA_KETTE_FRAGE": (
        "🔗 Should this become a *task chain*?\n\n"
        "In a chain, further tasks are only unlocked "
        "once the previous one has been completed.\n\n"
        "Answer with `ja` or `nein`"
    ),
    # MarkdownV2 – enthält bewusst escapte Satzzeichen
    "DOMINA_KETTE_START": (
        "🔗 Creating a task chain\\.\n\n"
        "*Task 1:* _{aufgabe}_\n\n"
        "Write the next task or *fertig* to finish the chain:"
    ),
    "DOMINA_KETTE_ERSTELLT": (
        "✅ Task chain with {gesamt} tasks created!\n"
        "The first task is being sent to him now."
    ),
    "DOMINA_KETTE_AUFGABE_HINZU": (
        "✅ Task {nummer} added.\n\n"
        "Write *task {naechste}* or *fertig* to finish the chain:"
    ),
    "DOMINA_LEVEL_UP": (
        "🎉 Congratulations! You've reached *level {level}*!\n"
        "Variety: {vielfalt}★ | Streak: {streak}★ | Rating: {bewertung}★"
    ),
    "DOMINA_NEUES_ABZEICHEN": "{emoji} *New badge:* {name}\n_{beschreibung}_",

    # --- Storylines / Arcs (Markdown) -------------------------------------------
    "ARC_STATUS": (
        "📖 *Active storyline: {thema}*\n\n"
        "Day *{tag_aktuell} of {tage_gesamt}*\n\n"
        "{tage_text}\n\n"
        "Use `/arc_beenden` to end the storyline early,\n"
        "or `/arc_starten <thema>` to begin a new one afterwards."
    ),
    "ARC_HILFE": (
        "📖 *Storylines / Arcs*\n\n"
        "A storyline connects 3-7 tasks into a narrative arc.\n\n"
        "Use `/arc_starten <thema>` to start one.\n"
        "Examples:\n"
        "  • `/arc_starten A week of pure devotion`\n"
        "  • `/arc_starten Training to become the perfect servant`\n"
        "  • `/arc_starten Exploring limits`"
    ),
    "ARC_THEMA_FEHLT": (
        "Please provide a theme:\n`/arc_starten A week of pure devotion`\n"
        "Optionally with a number of days (3-7): `/arc_starten 7 A week of pure devotion`"
    ),
    "ARC_GENERIERE": "📖 I'm generating the storyline for: _{thema}_ ...",
    "ARC_LIMIT_ABBRUCH": (
        "⚠️ The storyline touched your limits multiple times – aborted. "
        "Try a different theme or make it more specific."
    ),
    "ARC_TAGE_VERLETZT": "⚠️ Storyline days {tage} violated your limits – aborted.",
    "ARC_FEHLER": "⚠️ Could not create a storyline. Try again.",
    "ARC_GESTARTET": (
        "📖 *Storyline started: {thema}*\n\n"
        "{uebersicht}\n\n"
        "Day 1 will be assigned automatically with the next tiny-task job. "
        "Use `/arc` to see the progress."
    ),
    "ARC_KEINE_AKTIV": "No active storyline.",
    "ARC_BEREITS_AKTIV": (
        "⚠️ The storyline *{thema}* is already running. "
        "End it first with `/arc_beenden`, then you can start a new one."
    ),
    "ARC_BEENDET": "📖 Storyline _{thema}_ ended.",

    # --- Kleine Wait-/Prefix-Texte ----------------------------------------------
    "RUECKBLICK_WARTE": "📊 One moment, I'm analyzing the past weeks...",
    "RUECKBLICK_PREFIX": "📊 *Your review:*\n\n{analyse}",
    "TINYTASK_WARTE": "💡 One moment, I'm creating a suggestion...",
    "STIMMUNG_FRAGE": "How are you doing right now? What's your mood? 🖤",
    "STIMMUNG_HINWEIS_AN_DOMINA": "💭 Your slave's mood: _{antwort}_\n\n{hinweis}",
    # --- Button-Labels -----------------------------------------------------------
    "BUTTON_ANNEHMEN": "✅ Accept",
    "BUTTON_ABLEHNEN": "❌ Decline",
    "BUTTON_MERKEN": "✅ Save",
    "BUTTON_VERWERFEN": "🗑 Discard",
    "BUTTON_ALLE_LOESCHEN": "🗑 Delete all",
    "BUTTON_BESTAETIGEN": "✅ Confirm",
    "BUTTON_VERWEIGERN": "❌ Refuse",
    "BUTTON_ERNEUT_ERTEILEN": "🔁 Assign again",
    "BUTTON_DIESE_WOCHE_NICHT": "⏭ Not this week",

    # --- Scheduler-Jobs: Rahmen/Prefixe -----------------------------------------
    "ARC_TAG_ANGEPASST": "\n\n_(adapted to his latest feedback: {stimmung})_",
    "ARC_TAG_VORSCHLAG": (
        "📖 *Storyline: {thema}* – Day {tag}/{gesamt}\n\n"
        "*{titel}* _(category: {kategorie})_\n\n"
        "{aufgabe}{hinweis}"
    ),
    "ARC_ABGESCHLOSSEN": (
        "📖 *Storyline completed: {thema}*\n"
        "All {tage} days completed."
    ),
    "ARC_NEUE_ABZEICHEN": "\n\n🏅 New badges: {liste}",
    "TINYTASK_PAUSE_TAG": "🎁 No tiny task today – your slave's pause-day privilege has been redeemed.",
    "TINYTASK_PREFIX_TINY": "💡 *Tip for today:*\n\n{vorschlag}",
    "TINYTASK_PREFIX_AUSFUEHRLICH": "🎯 *Task suggestion for today:*\n\n{vorschlag}",
    "ROLLENSPIEL_IDEE": "🎭 *Roleplay idea for tonight:*\n\n{vorschlag}",
    "LERNKURVE_PREFIX": "📊 *Your learning curve – last 2 weeks:*\n\n{analyse}",
    "GEHEIMNIS_PREFIX": "🔓 *Revelation:*\n\n{nachricht}",
    "KOMMENTAR_ANALYSE_PREFIX": "📝 *Weekly review of your feedback:*\n\n{analyse}",
    "RESURFACE_VORSCHLAG": (
        "🕰 *Today, ~3 months ago*\n\n"
        "_{datum}_ – category *{kategorie}*, your rating: {sterne}\n\n"
        "_{aufgabe}_\n\n"
        "Feel like assigning that one again?"
    ),
    "ERINNERUNG_KEINE_AUFGABE": (
        "⏰ *Reminder:* Your slave hasn't received a task in more than {tage} days.\n"
        "Would you like to give him a new task today? "
        "Write `aufgabe: <text>` or grab ideas with /inspiration."
    ),
    "BACKUP_FEHLGESCHLAGEN": "⚠️ The automatic Qdrant backup failed today. Please check the logs.",
    "REFLEXION_INTRO": (
        "🧭 *Coach reflection – {zeitraum}*\n\n"
        "Looking back over our last 14 days, I noticed {anzahl} things "
        "I could do differently. You decide whether I adopt them."
    ),

    # --- Aufgaben-Historie / Lösch-Dialog ------------------------------------------
    "AUFGABEN_KEINE_ERLEDIGT": "No completed tasks yet.",
    "AUFGABEN_LISTE_TITEL": "📋 *Recently completed tasks{filter}:*\n",
    "AUFGABEN_EINTRAG": (
        "{nr}. *{aufgabe}*{serie}\n"
        "   📅 {erteilt} | 🏷 {kategorie}\n"
        "   💬 Feeling: _{gefuehl}_\n"
    ),
    "AUFGABEN_FILTER_KOPF": (
        "─────────────────\n"
        "🏷 *Filter by category:*\n"
        "`/aufgaben_alle` = show all"
    ),
    "AUFGABEN_KATEGORIE_UNBEKANNT": "⚠️ I don't know the category „{kategorie}“ – /aufgaben shows all filters.",
    "AUFGABEN_KEINE_OFFEN": "No open tasks available.",
    "AUFGABEN_LOESCHEN_TITEL": "📋 *Open tasks:*\n",
    "AUFGABEN_LOESCHEN_FUSS": (
        "\nWrite the *number* followed by:\n"
        "`p` = pause  |  `x` = delete\n"
        "Example: `1 p` or `2 x`\n"
        "\nOr /abbrechen"
    ),
    "AUFGABEN_GELOESCHT": "🗑 Task deleted.",
    "AUFGABEN_UNGUELTIG": "Invalid input. Example: `1 p` (pause) or `1 x` (delete)\nOr /abbrechen",
    "AUFGABEN_LISTE_VERALTET": "⚠️ The selection list is outdated. Please restart /loeschen.",
    "AUFGABEN_BEREITS_MARKIERT": "⚠️ This task has already been marked as '{status}'. Restart /loeschen.",
    "AUFGABEN_PAUSIERT": "⏸ Task paused.",
    "AUFGABEN_LOESCHEN_BESTAETIGUNG": "⚠️ Really delete this task?\n\n_{aufgabe}_\n\nReply with `ja` or `nein`",

    # --- Privilegien ----------------------------------------------------------------
    # --- Bet (double or nothing) -----------------------------------------------
    "WETTE_ANGEBOT": (
        "🎰 *Double or nothing*\n\n"
        "You have *{punkte} points*. Place a bet that you will complete your "
        "next due task:\n"
        "Succeed → double your stake back. Fail → stake gone."
    ),
    "WETTE_PLATZIERT": "🎰 Bet placed: *{einsatz} points*. Remaining balance: {rest} points.\nNo turning back now.",
    "WETTE_SCHON_AKTIV": "🎰 You already have a bet of *{einsatz} points* running. That one gets decided first.",
    "WETTE_KEINE_AUFGABE": "🎰 No open task to bet on. Only once something is on the table.",
    "WETTE_ZU_WENIG_PUNKTE": "🎰 You have {punkte} points – a bet takes at least {minimum}.",
    "WETTE_STATE_WEG": "These bet buttons are no longer valid. Start over with /wette.",
    "WETTE_VERLOREN": "🎰 *Bet lost.* Your stake of {einsatz} points is gone.",
    "WETTE_INFO_DOMINA": "🎰 By the way: your slave just bet *{einsatz} points* that he will complete his next task. Double or nothing.",

    # --- Flash tasks ⚡ ----------------------------------------------------------
    "BLITZ_AN": (
        "⚡ *Flash tasks activated.*\n\n"
        "From now on the bot occasionally sends your slave an unannounced mini task "
        "with a *{minuten} minute countdown* – directly, without asking you first "
        "(limits are checked as always, kid-free times respected). "
        "You get every flash task for your information. Turn off: /blitz"
    ),
    "BLITZ_AUS": "⚡ Flash tasks deactivated.",
    "BLITZ_AN_SKLAVEN": (
        "⚡ *FLASH TASK* ⚡\n\n{anweisung}\n\n"
        "⏱ You have *{minuten} minutes*. Press the button when you're done – "
        "after that it's too late."
    ),
    "BUTTON_BLITZ_GESCHAFFT": "⚡ Done!",
    "BLITZ_GESCHAFFT": "⚡ *Done!* +{punkte} points (total: {gesamt}).",
    "BLITZ_GESCHAFFT_DOMINA": "⚡ He completed the flash task in time.",
    "BLITZ_VERPASST": "⏱ Time's up. The flash task has expired – she will remember that.",
    "BLITZ_VERPASST_DOMINA": "⚡ He let the flash task expire: “{aufgabe}” – in case you want to make something of it.",
    "BLITZ_INFO_DOMINA": "⚡ FYI: flash task sent to your slave ({minuten} min countdown): “{aufgabe}”",
    "BLITZ_NICHT_MEHR_OFFEN": "This flash task is no longer open.",
    "BLITZ_ZU_SPAET": "⏱ Too late – the countdown had already expired. The task is forfeited.",

    # --- Event arcs 🎂 -----------------------------------------------------------
    "EVENT_HILFE": (
        "🎂 *Event storylines*\n\n"
        "Plan a storyline whose finale lands exactly on a date "
        "(birthday, anniversary, …):\n"
        "`/event 24.12. Christmas special`\n"
        "`/event 15.08.2026 7 Anniversary`\n\n"
        "Format: /event <DD.MM.[YYYY]> [days 3-7] <topic>\n"
        "It starts automatically, you'll be notified."
    ),
    "EVENT_LISTE": "🎂 *Planned events:*\n\n{liste}\n\nNew: /event <DD.MM.> [days] <topic> – delete: /event\\_loeschen <no>",
    "EVENT_DATUM_UNVERSTANDEN": "I didn't understand that date. Format: *DD.MM.* or *DD.MM.YYYY* (e.g. `/event 24.12. Christmas special`).",
    "EVENT_THEMA_FEHLT": "And what should it be about? `/event <DD.MM.> [days] <topic>`",
    "EVENT_ZU_SPAET": "That date is not in the future – for today only /arc_starten helps.",
    "EVENT_GEPLANT": (
        "🎂 *Event planned:* {thema}\n"
        "📅 Finale on *{datum}*, storyline over *{tage} days* "
        "(starts in ~{start_in} days, automatically).\n\n"
        "View: /event – discard: /event\\_loeschen"
    ),
    "EVENT_GESTARTET": (
        "🎂 *Event storyline started:* {thema}\n"
        "Finale on *{datum}* – the days build up to it:\n\n{uebersicht}\n\n"
        "Runs like a normal arc from now on (/arc)."
    ),
    "EVENT_WARTET": "🎂 The event *{thema}* wants to start, but another storyline is still running. I'll try again tomorrow – the event storyline gets shorter accordingly. (/arc_beenden makes room.)",
    "EVENT_VERPASST": "🎂 No time left for the event *{thema}* (less than 3 days to the date). I discarded it – for something spontaneous: /arc_starten or /wuerfel.",
    "EVENT_GELOESCHT": "🗑 Event “{thema}” discarded.",
    "EVENT_KEINE_GEPLANT": "No events planned. New: /event <DD.MM.> [days] <topic>",
    "EVENT_LOESCHEN_HINWEIS": "Which number? /event_loeschen <no>\n\n{liste}",

    # --- Voice input 🎤 ----------------------------------------------------------
    "VOICE_VERSTANDEN": "🎤 “{text}”",
    "VOICE_NICHT_VERSTANDEN": "🎤 I didn't catch that – speak again or type it.",
    "VOICE_ZU_LANG": "🎤 Too long – at most {sekunden} seconds per voice message.",

    # --- Punishment roulette 🎰 ---------------------------------------------------
    "ROULETTE_JACKPOT": (
        "🎰 *JACKPOT!*\n\nThe machine has decided: *MERCY*. No punishment.\n"
        "Want me to announce it to him – or keep it to yourself?"
    ),
    "BUTTON_ROULETTE_GNADE": "😇 Announce mercy",
    "ROULETTE_STUFE_MILD": "mild",
    "ROULETTE_STUFE_MITTEL": "medium",
    "ROULETTE_STUFE_HART": "HARSH",
    "ROULETTE_VORSCHLAG": (
        "🎰 *The machine says: {stufe}*\n\n{strafe}\n\n"
        "Issue or discard?"
    ),
    "ROULETTE_AN_SKLAVEN": "🎰 *The machine has decided.*\n\n{anweisung}",
    "ROULETTE_ERTEILT": "🎰 Punishment issued – the machine bears the responsibility.",
    "ROULETTE_VERWORFEN": "🎰 Discarded. The machine stays silent.",
    "ROULETTE_GNADE_VERKUENDET": "😇 Mercy announced.",
    "ROULETTE_GNADE_FALLBACK": "🎰 The machine hit the jackpot: mercy. This time.",
    "ROULETTE_STATE_WEG": "These roulette buttons are no longer valid. Spin again: /roulette",
    "ROULETTE_FEHLER": "⚠️ The machine is jammed – could not generate a punishment. Try again.",

    # --- Endurance orders 🕰 -----------------------------------------------------
    "DAUER_HILFE": "🕰 *Endurance order:* `/dauer <hours {min}-{max}> <order>`\ne.g. `/dauer 4 You will wear … until tonight`\nShe checks in unannounced along the way.",
    "DAUER_AN_SKLAVEN": "🕰 *ENDURANCE ORDER* – valid for *{stunden} hours* (until ~{bis}):\n\n{anweisung}\n\n_She will check on you along the way. At the end she will ask._",
    "DAUER_ERTEILT": "🕰 Endurance order issued ({stunden}h, until ~{bis}). Check-ins run automatically.",
    "DAUER_ENDE_FALLBACK": "🕰 Time is up. Did you last – yes or no? (Order: {aufgabe})",
    "DAUER_CHECK_FALLBACK": "🕰 Checking in. I hope for your sake you are still at it.",

    # --- Quiz 🧠 ------------------------------------------------------------------
    "QUIZ_FRAGE": "🧠 *Quiz – how well do you know your Mistress?*\n\n{frage}\n\n_Answer freely – or /abbrechen._",
    "QUIZ_RICHTIG": "🧠 ✅ *Correct!* +{punkte} points.",
    "QUIZ_TEILWEISE": "🧠 🟡 *Half right.* +{punkte} points.\nThe answer was: _{antwort}_",
    "QUIZ_FALSCH": "🧠 ❌ *Wrong.* Correct would be: _{antwort}_",
    "QUIZ_ZU_WENIG_DATEN": "🧠 I don't know enough about her yet – the quiz needs a maintained profile/dossier.",
    "QUIZ_FEHLER": "⚠️ Quiz not possible right now – try again later.",

    # --- Advent calendar 🎄 ------------------------------------------------------
    "ADVENT_DEFAULT_THEMA": "Advent calendar",
    "ADVENT_GEPLANT": (
        "🎄 *Advent calendar {jahr} planned:* {thema}\n\n"
        "From December 1st to 24th a door opens automatically every morning (~8:00) "
        "for your slave – each generated fresh that day, with rising intensity up to "
        "the finale on Christmas Eve. You get every door for your information.\n\n"
        "Cancel: `/adventskalender stop`"
    ),
    "ADVENT_STATUS": "🎄 *Advent calendar {jahr}* – topic: {thema}\nLast opened: door {letzte}/24\n\nCancel: `/adventskalender stop`",
    "ADVENT_KEINER": "🎄 No advent calendar planned. Create one: /adventskalender [topic]",
    "ADVENT_GESTOPPT": "🎄 Advent calendar stopped.",
    "ADVENT_TUERCHEN": "🎄 *Door {tuer}/24*\n\n{anweisung}",
    "ADVENT_INFO_DOMINA": "🎄 Door {tuer}/24 opened: “{aufgabe}”",

    "PRIVILEG_KATALOG": (
        "🎁 *Privilege catalog*\n\n"
        "Your point balance: *{punkte}*\n\n"
        "{katalog}\n\n"
        "_Pick a privilege with a tap, or reply with a number/cancel._"
    ),
    "PRIVILEG_NUR_NUMMER": "Please enter only the number (e.g. `2`) or `abbrechen`.",
    "PRIVILEG_NUMMER_BEREICH": "Number must be between 1 and {max}.",
    "PRIVILEG_ZU_WENIG_PUNKTE": "⚠️ You only have *{punkte}* points. '{name}' costs *{kosten}*.",
    "PRIVILEG_EINGELOEST": (
        "🎁 Redeemed: *{name}* (−{kosten} points, *{rest}* left).\n"
        "Whether I actually grant it to you, I'll decide in a moment."
    ),
    "PRIVILEG_NEUE_ABZEICHEN": "\n\n🏅 New badges: {liste}",
    "PRIVILEG_AN_DOMINA": (
        "🎁 *Your slave has redeemed a privilege:*\n\n"
        "*{name}* ({kosten} points)\n"
        "_{beschreibung}_\n\n"
        "_Choose directly or reply as text with a comment._"
    ),
    "PRIVILEG_ENTSCHIEDEN": "{emoji} Privilege {entscheidung}.",
    "PRIVILEG_ENTSCHEIDUNG_HINWEIS": "Please reply with *bestätigen* or *verweigern*.",
    "PRIVILEG_ENTSCHEIDUNG_GESPEICHERT": "✅ Decision saved.",
    "PRIVILEG_NICHT_GEFUNDEN": "⚠️ Privilege no longer found.",
    "PRIVILEG_PUNKTE_ZURUECK": "\n_(Points refunded: {kosten})_",
    # Persona-Fallbacks bei LLM-Ausfall (Stimme der Herrin)
    "FALLBACK_PRIVILEG_GEWAEHRT": "Granted: {name}.",
    "FALLBACK_PRIVILEG_VERWEIGERT": "Not this time – I'm not granting you {name}. Your {kosten} points have been returned.",

    # --- Coach-Regeln / Lern-System ------------------------------------------------
    "COACHREGELN_MERKEN_USAGE": (
        "ℹ️ How to use /merken:\n"
        "`/merken <what the coach should remember>`\n\n"
        "Example: `/merken I like short, clear tasks in the morning.`"
    ),
    "COACHREGELN_REGEL_USAGE": (
        "ℹ️ How to use /regel:\n"
        "`/regel <rule the coach must always follow>`\n\n"
        "Example: `/regel Always reply in 4 sentences at most.`"
    ),
    "COACHREGELN_VERGESSEN_USAGE": "ℹ️ Use: `/vergessen <nr>` – you'll find the numbers in /regeln.",
    # MarkdownV2 – {text} am Callsite mit escape_md übergeben
    # MarkdownV2 – Satzzeichen müssen escaped sein; {text} kommt am Callsite via escape_md.
    "COACHREGELN_NOTIZ_GESPEICHERT": "📝 Noted\\. I'll keep this in mind from now on:\n_{text}_",
    "COACHREGELN_REGEL_AKTIV": "⚡ Rule active\\. I'll stick to this from now on:\n_{text}_",
    "COACHREGELN_KEINE": (
        "📋 No learned rules yet.\n\n"
        "With /regel <text> you set a binding rule,\n"
        "with /merken <text> a casual note."
    ),
    "COACHREGELN_LISTE_TITEL": "📋 *Active rules & notes:*",
    "COACHREGELN_LISTE_FUSS": "\nUse `/vergessen <nr>` to deactivate one.",
    "COACHREGELN_PENDING_TITEL": "🤔 *Suggestions waiting for your confirmation:*",
    "COACHREGELN_PENDING_FUSS": "\nThey each show up in the chat with yes/no buttons.",
    "COACHREGELN_KEINE_NUMMER": "⚠️ That wasn't a valid number.",
    "COACHREGELN_NUMMER_UNBEKANNT": (
        "⚠️ I don't know that number. Call /regeln first, then pick a number from there."
    ),
    "COACHREGELN_DEAKTIVIERT": "🗑 Rule {nr} deactivated.",
    "COACHREGELN_UEBERNOMMEN": "\n\n✅ Adopted – active from now on.",
    "COACHREGELN_PROFIL_AKTUALISIERT": "\n\n✅ Profile ({profile_user}) updated:\n{aenderungen}",
    "COACHREGELN_PATCH_LEER": "\n\n⚠️ Profile patch contained no applicable changes.",
    "COACHREGELN_PATCH_FEHLER": "\n\n⚠️ Error while applying the profile patch: {fehler}",
    "COACHREGELN_PATCH_IGNORIERT": "\n_Ignored: {liste}_",
    "COACHREGELN_VERWORFEN": "\n\n🗑 Discarded – I won't remember it.",
    "COACHREGELN_VORSCHLAG": "💡 *Learning suggestion:*\n_{text}_",
    "COACHREGELN_VORSCHLAG_ANLASS": "\n\n_Trigger: {kontext}_",
    "COACHREGELN_VORSCHLAG_FRAGE": "\n\nShould I remember this?",
    "COACHREGELN_PROFILCHECK_WARTE": "🧬 Checking profiles for updates from the last {days} days... one moment.",
    "COACHREGELN_PROFILCHECK_OK": (
        "🧬 {anzahl} profile suggestions sent (period {zeitraum}).\n"
        "Confirm or discard them via the buttons."
    ),
    "COACHREGELN_PROFILCHECK_LEER": "🧬 No profile updates needed: {info}",
    "COACHREGELN_PROFILCHECK_FEHLER": "⚠️ Error during profile maintenance: {info}",
    "COACHREGELN_PROFIL_VORSCHLAG": "🧬 *Profile update suggestion ({rolle}):*\n```\n{diff}\n```",
    "COACHREGELN_PROFIL_VORSCHLAG_FUSS": (
        "\n\nHard limits are automatically exempt. "
        "On ✅ the patch is applied additively (lists get extended, nothing is deleted)."
    ),

    # --- Präferenz-Detektor (Vorlieben/No-Gos aus dem Gespräch) ---------------------
    "PRAEFERENZ_VORSCHLAG": (
        "📝 From our conversation – should this go into the profile?\n```\n{diff}\n```\n"
        "_No-gos are only added or refined with exceptions, never removed._"
    ),

    # --- Skills / Wissens-Briefe ----------------------------------------------------
    "SKILL_HEADER": "📚 *Knowledge – {kategorie}* (_{source}, as of {stand}_)\n\n",
    "SKILL_LERNE_USAGE": (
        "ℹ️ How to use /lerne:\n"
        "`/lerne <category>`\n\n"
        "Examples: `/lerne Spanking`, `/lerne pegging`, `/lerne blowjob_training`\n"
        "You can see the available categories in /aufgaben_alle."
    ),
    "SKILL_LERNE_NEU_USAGE": "ℹ️ Use: `/lerne_neu <category>` – replaces an existing entry.",
    "SKILL_BEARBEITEN_USAGE": "ℹ️ Use: `/skill_bearbeiten <category>` – then send the new text.",
    "SKILL_GENERIERE": "📚 Creating knowledge briefing on *{kategorie}*… one moment.",
    "SKILL_GENERIERE_NEU": "📚 Regenerating for *{kategorie}*… one moment.",
    "SKILL_BEARBEITEN_HINWEIS": (
        "ℹ️ You can overwrite the text anytime with `/skill_bearbeiten {kategorie}` "
        "or have it regenerated with `/lerne_neu {kategorie}`."
    ),
    "SKILL_EDIT_START": (
        "✏️ *Edit – {kategorie}*\n\nCurrent version:\n\n{aktuell}\n\n"
        "_Now send me the new version as a message. Use /abbrechen to discard._"
    ),
    "SKILL_EDIT_ABGEBROCHEN": "✅ Editing cancelled, the old version stays.",
    "SKILL_EDIT_ZU_KURZ": (
        "⚠️ That seems very short. Send the complete text "
        "or /abbrechen to discard."
    ),
    "SKILL_GESPEICHERT": "✅ *{kategorie}* saved (source: manual).",
    # Bewusst OHNE Exception-Details (Exception-Text nicht roh an den User leaken)
    "SKILL_GENERIEREN_FEHLER": "⚠️ Couldn't create the knowledge briefing right now. Try again in a moment.",
    "SKILL_SPEICHERN_FEHLER": "⚠️ Couldn't save. Try again in a moment.",
    "SKILL_KEINE": (
        "📚 No knowledge entries yet.\n"
        "Start with `/lerne <category>` – Grok will create a basic entry for you."
    ),
    "SKILL_LISTE_TITEL": "📚 *Existing knowledge entries:*",
    "SKILL_LISTE_LEGENDE": "\n✏️ = manually overwritten, 🤖 = Grok-generated",

    # --- Wünsche ----------------------------------------------------------------
    "WUNSCH_EINREICHEN": (
        "🙏 *Submit a wish*\n\n"
        "Tell me what you wish for – I decide whether you get it.\n"
        "Phrase it respectfully.\n\n"
        "Write your wish or /abbrechen"
    ),
    "WUNSCH_KEINE_GESAMMELT": (
        "I haven't collected any wishes from you yet. Just mention in the chat "
        "what you'd like to try sometime – I'll remember it."
    ),
    "WUNSCH_LISTE": "🗒 *Your collected wishes:*\n{liste}\n\nTap a button to remove one:",
    "WUNSCH_ALLE_GELOESCHT": "🗑 All collected wishes deleted.",
    "WUNSCH_EINTRAG_WEG": "That entry is gone – tap /meinewuensche for the current list.",
    "WUNSCH_LISTE_LEER": "🗑 Removed. You have no collected wishes left.",
    # Bewusst statische Persona-Bestätigung (Stimme der Herrin)
    "WUNSCH_ANGEKOMMEN": "Received. Whether I grant it to you, I'll think about – have some patience. 🖤",
    "WUNSCH_AN_DOMINA": (
        "📬 *A wish from your slave:*\n\n{text}\n\n"
        "Choose directly or reply as text with a comment (e.g. _annehmen mal sehen_)."
    ),
    "WUNSCH_AN_DOMINA_WARTEND": (
        "📬 *A wish from your slave (waiting for you):*\n\n{text}\n\n"
        "_Tap a button whenever you have time._"
    ),
    "WUNSCH_ENTSCHIEDEN": "{emoji} Wish {entscheidung}.",
    "WUNSCH_ENTSCHEIDUNG_HINWEIS": "Please reply with *annehmen* or *ablehnen* (optionally with a comment).",
    "WUNSCH_ENTSCHEIDUNG_GESPEICHERT": "✅ Decision saved: {entscheidung}",

    # --- Inspiration-Flow ----------------------------------------------------------
    "INSPIRATION_WARTE": "✨ One moment, I'm fetching inspiration for you...",
    "INSPIRATION_VORSCHLAEGE": (
        "✨ 3 inspirations for you:\n\n{raw}\n\n"
        "Is there anything that appeals to you?\n"
        "Reply with yes or no"
    ),
    "INSPIRATION_NEUE_VORSCHLAEGE": (
        "✨ 3 new inspirations:\n\n{raw}\n\n"
        "Anything this time?\n"
        "Reply with yes or no"
    ),
    "INSPIRATION_NUMMER_FRAGE": (
        "Which suggestion would you like to save as a template?\n"
        "Reply with `1`, `2` or `3`"
    ),
    "INSPIRATION_FEEDBACK_FRAGE": (
        "What didn't fit? Briefly describe why the suggestions "
        "weren't the right thing."
    ),
    "INSPIRATION_NUR_123": "Please reply with `1`, `2` or `3`",
    "INSPIRATION_UNGUELTIGE_NUMMER": "Invalid number.",
    "INSPIRATION_VORLAGE_GESPEICHERT": (
        "✅ Suggestion {nummer} saved as a template!\n"
        "You can retrieve it anytime with /vorlagen."
    ),
    "INSPIRATION_COACH_HINWEIS": "💬 Coach note:\n\n{erklaerung}",
    "INSPIRATION_NEU_GENERIEREN": "I'm generating new suggestions based on your feedback...",

    # --- Profil-Anzeige/-Edit (MarkdownV2 – Werte am Callsite mit escape_md) --------
    "PROFIL_KEIN": "No profile found. Please send a message to start onboarding.",
    "PROFIL_DOMINA": (
        "👤 *Your profile*\n\n"
        "1️⃣ Experience level: {erfahrungsstand}\n"
        "2️⃣ Interests: {interessen}\n"
        "3️⃣ Limits: {grenzen}\n"
        "4️⃣ Goals: {ziele}\n"
        "5️⃣ Pace: {tempo}\n"
        "6️⃣ Child-free times: {zeiten}\n"
        "7️⃣ Children in the household: {kinder}\n"
        "\nLevel: {level}\n\n"
        "✏️ What would you like to change\\?\n"
        "Write the number \\(1\\-7\\) or /abbrechen"
    ),
    "PROFIL_SKLAVE": (
        "👤 *Your profile*\n\n"
        "1️⃣ Hard limits: {hard_limits}\n"
        "2️⃣ Preferences: {vorlieben}\n"
        "3️⃣ Experience level: {erfahrungsstand}\n\n"
        "✏️ What would you like to change\\?\n"
        "Write the number \\(1\\-3\\) or /abbrechen"
    ),
    "PROFIL_WUNSCH_WARTET": (
        "\n\n📬 A wish from your slave is still waiting for your decision.\n"
        "Reply with *annehmen* or *ablehnen*."
    ),
    "PROFIL_ZAHL_BEREICH": "Please enter a number between 1 and {max} or /abbrechen",
    "PROFIL_NEUER_WERT": "✏️ *{feld}*\n\nNew value:",
    "PROFIL_GANZE_ZAHL": "Please enter a whole number.",
    "PROFIL_GESPEICHERT_PREFIX": "✅ Saved\\!\n\n",

    # --- Wochenplanung ----------------------------------------------------------------
    "BUTTON_WOCHENPLAN_ALLE": "✅ Assign all as tasks",
    "BUTTON_WOCHENPLAN_VERWERFEN": "🗑 Suggestion only",
    "WOCHENPLAN_THEMA_FRAGE": (
        "📅 *Weekly Planning*\n\n"
        "Is there a theme or focus for this week?\n\n"
        "e.g. _'more rituals'_, _'strengthen obedience'_, or _'just keep it varied'_\n\n"
        "Write your theme or /abbrechen"
    ),
    "WOCHENPLAN_WARTE": "⏳ Creating your weekly plan...",
    "WOCHENPLAN_TITEL": "📅 Your weekly plan:",
    "WOCHENPLAN_FEHLER": "⚠️ Error while creating the weekly plan.",
    "WOCHENPLAN_NUR_VORSCHLAG": "👍 It stays just a suggestion.",
    "WOCHENPLAN_NICHT_IM_SPEICHER": "⚠️ Plan is no longer in memory – create it again with /wochenplanung.",
    "WOCHENPLAN_ERSTELLT": "✅ {anzahl} tasks created from the weekly plan – day 1 starts now, the rest follows daily.",
    "WOCHENPLAN_UEBERSPRUNGEN": "\n({anzahl} skipped due to limits or missing text.)",

    # --- Serie ---------------------------------------------------------------------
    "SERIE_FRAGE": (
        "🔄 Should this task be issued as a *series*?\n\n"
        "Reply with `{optionen}` for the number of days\n"
        "or `nein` for a one-time task."
    ),
    "SERIE_OPTIONEN_HINWEIS": "Please reply with `{optionen}` or `nein`",
    "SERIE_DISLIKE_WARNUNG": (
        "⚠️ Heads-up: your slave has rated *{kategorie}* negatively "
        "({anzahl}x negative). "
        "The series will be created anyway – but the slave probably won't be thrilled.\n\n"
        "_Creating the series now…_"
    ),
    "SERIE_GESPEICHERT": "🔄 Series saved! {tage} days {hinweis}.\nDay 1 starts now.",
    "SERIE_HINWEIS_BOGEN": "as an escalating arc",
    "SERIE_HINWEIS_TAEGLICH": "daily",

    # --- Training ------------------------------------------------------------------
    "TRAINING_WARTE": "🧠 *Psycho Training – {typ}*\n\nOne moment...",
    "TRAINING_UEBUNG": "🧠 {typ}:\n\n{uebung}\n\nWrite your thoughts or answer. /abbrechen to stop.",
    "TRAINING_BEENDET": "✅ Training finished.",
    "TRAINING_FEEDBACK_PREFIX": "💬 *Coach feedback:*\n\n{feedback}",
    "TRAINING_TAEGLICH": (
        "🧠 Daily training – {typ}:\n\n{uebung}\n\n"
        "Write your thoughts or /abbrechen to skip."
    ),

    # --- Namen / Persona-Settings -----------------------------------------------------
    "NAMEN_BOTNAME_ANZEIGE": "Current bot name: {aktuell}\n\nSet: /botname <name>\nRemove: /botname -",
    "NAMEN_BOTNAME_GESETZT": "✅ Bot name set: *{name}* — applies to both sides.",
    "NAMEN_BOTNAME_ENTFERNT": "✅ Bot name removed – she is “your Mistress” again.",
    "NAMEN_SETUP_ANZEIGE": (
        "Current setup context:\n{aktuell}\n\n"
        "Set: /setup <description>\n"
        "e.g. /setup Mistress is female, penetrates him with a strapon. Cum is his own "
        "(ruined orgasms). Creampie cleanup = he licks up his own cum.\n"
        "Remove: /setup -"
    ),
    "NAMEN_SETUP_GESETZT": "✅ Setup context set – the bot now follows it.",
    "NAMEN_SETUP_ENTFERNT": "✅ Setup context removed.",
    "NAMEN_ANREDE_ANZEIGE": "Current slave form of address: {aktuell}\n\nSet: /sklavenname <address>\nRemove: /sklavenname -",
    "NAMEN_ANREDE_GESETZT": "✅ Slave form of address set: *{name}*.",
    "NAMEN_ANREDE_ENTFERNT": "✅ Slave form of address removed – neutral again.",

    # --- Würfel ---------------------------------------------------------------------
    "WUERFEL_GEFALLEN": "🎲 *The die has been cast!*\n\nCategory: *{kategorie}*\n\nGenerating a task...",
    "WUERFEL_GEFALLEN_WURF": "🎲 *A {wert}!*\n\nCategory: *{kategorie}*\n\nGenerating a task...",
    "WUERFEL_GRENZEN": "⚠️ The dice produced a task against your limits ({treffer}) – try again.",
    "BUTTON_ALS_TASK_ERTEILEN": "✅ Issue as task",
    "WUERFEL_VORSCHLAG": "🎲 *Dice task for the slave ({kategorie}):*\n\n{aufgabe}\n\n_Preview – he only gets it once you issue it._",
    "WUERFEL_FEHLER": "⚠️ Could not generate a task.",
    "WUERFEL_VERWORFEN": "❌ Dice task discarded.",
    "WUERFEL_STATE_WEG": "⚠️ Task no longer in state – roll again.",
    "WUERFEL_BEFEHL_PREFIX": "🎲 The dice have decided:\n\n{anweisung}",
    "WUERFEL_ERTEILT": "✅ Dice task issued as task (category: {kategorie})",

    # Lücken-Füller (luecke.py / luecken_*_job)
    "LUECKE_VORSCHLAG": "🕊️ *No task has been running for him for {tage} days.* My suggestion – you decide:\n\n{vorschlag}\n\n_He only gets it once you approve._",
    "LUECKE_VORSCHLAG_NEU": "🔄 *New suggestion – you decide:*\n\n{vorschlag}\n\n_He only gets it once you approve._",
    "BUTTON_LUECKE_JETZT": "✅ Send now",
    "BUTTON_LUECKE_ABEND": "🌙 Tonight",
    "BUTTON_LUECKE_ANDERER": "🔄 Different suggestion",
    "BUTTON_LUECKE_HEUTE_NICHT": "🚫 Not today",
    "LUECKE_GESENDET_JETZT": "✅ Done – he has the task.",
    "LUECKE_GEPLANT_ABEND": "🌙 Saved – it goes out to him tonight.",
    "LUECKE_HEUTE_NICHT": "🚫 Okay, not today. I'll check back in a few days.",
    "LUECKE_STATE_WEG": "⚠️ That suggestion is no longer current – ignore the old button.",
    "LUECKE_KEIN_VORSCHLAG": "⚠️ I can't think of anything clean right now – I'll try again later.",
    "LUECKE_TOGGLE_AN": "🕊️ Gap filler *on*. If no task has been running for a while, I'll suggest something – nothing gets issued unless you approve it.",
    "LUECKE_TOGGLE_AUS": "🕊️ Gap filler *off*.",

    # --- Gefühl-/Erledigungs-Mechanik ---------------------------------------------------
    "GEFUEHL_BEWERTUNG_FRAGE": "⭐ How do you think he did? Give him 1-5.",
    "GEFUEHL_WUERFEL_ABZEICHEN": "🏅 New badges: {liste}",
    "GEFUEHL_PUNKTE": "⭐ +{punkte} points _(total: {gesamt})_",
    "GEFUEHL_STREAK_SUFFIX": "\n🔥 Streak: *{streak}*",
    "GEFUEHL_ABZEICHEN_VERDIENT": "🎖 *New badge earned:*\n\n{vorschlag}",
    "GEFUEHL_LEVEL_TEASER": (
        "✨ *Only {fehlend} point{plural} to go until level {level}!*\n\n"
        "You're almost there — what would be a fitting next task for him?"
    ),
    # Gemeinsamer Prefix für Ketten-Freischaltung (gefuehl.py + kette_adaptiv.py)
    "KETTE_FREIGESCHALTET": "🔗 The next step, {pos} of {gesamt}:\n\n{anweisung}",

    # --- Followup-Antwort / Bestrafung ----------------------------------------------------
    "BUTTON_ERLEDIGT": "✅ Done",
    "BUTTON_NICHT_ERLEDIGT": "❌ Not done",
    "FOLLOWUP_KLARSTELLUNG": "Tell me straight – done or not? Use the buttons or write yes/no.",
    "FOLLOWUP_ERST_BEANTWORTEN": "Answer my open question first – then we'll deal with the next task.",
    "BESTRAFUNG_KEIN_VORSCHLAG": (
        "⚠️ Could not generate a punishment suggestion that respects the limits. "
        "Please issue a punishment manually."
    ),
    "BESTRAFUNG_LABEL_ESKALATION": "🚨 *Escalation – repeated pattern:*",
    "BESTRAFUNG_LABEL_VORSCHLAG": "⚠️ *Punishment suggestion:*",

    # --- Rollenspiel (Liste/Aktiv-Meldung sind MarkdownV2) --------------------------------
    "ROLLENSPIEL_LISTE_TITEL": "🎭 *Roleplay – pick a scenario:*\n",
    "ROLLENSPIEL_LISTE_FUSS": "\nWrite a number \\(1\\-5\\) or describe your own scenario\\.",
    "ROLLENSPIEL_ABBRECHEN_HINWEIS": "Or /abbrechen",
    "ROLLENSPIEL_INTENSITAET_FRAGE": "🎭 Scenario: *{name}*\n\nChoose the intensity:\n{liste}",
    "ROLLENSPIEL_1_2_3": "Please choose 1, 2 or 3.",
    "ROLLENSPIEL_AKTIV": (
        "🎭 *{prefix}Scenario active: {name}*\n"
        "Intensity: {intensitaet}\n\n"
        "The mode is now active\\. Just keep writing – I'll adapt my replies\\.\n"
        "/rollenspiel\\_beenden to stop\\."
    ),
    "ROLLENSPIEL_BEENDET": "✅ Roleplay '{name}' ended.\n\nWe're back in normal mode.",
    "ROLLENSPIEL_AUTO_BEENDET": "\U0001F3AD Your roleplay '{name}' had been sitting unfinished for days – I quietly ended it. Start a new one anytime with /rollenspiel.",
    "ROLLENSPIEL_KEIN_AKTIV": "✅ No active roleplay.",

    # --- Lerntagebuch ----------------------------------------------------------------------
    "LERNTAGEBUCH_WARTE": "📓 Condensing coach conversations from the last {days} days... one moment.",
    "LERNTAGEBUCH_LEER": "📓 There were no coach conversations to condense in the period {zeitraum}.",
    "LERNTAGEBUCH_FEHLER": "⚠️ Error while generating the learning journal: {fehler}",
    "LERNTAGEBUCH_HEADER": "📓 *Learning journal saved* ({zeitraum}, {anzahl} conversations)\n\n",
    "LERNTAGEBUCH_GEKUERZT": "\n\n_(shortened – full version in the coach memory)_",

    # --- Resurface ----------------------------------------------------------------------------
    "RESURFACE_UEBERSPRUNGEN": "⏭ Skipped.",
    "RESURFACE_DISLIKE": (
        "⚠️ Category *{kategorie}* is on your slave's dislike list. "
        "Task skipped."
    ),
    "RESURFACE_PREFIX": "🕰 *A tried-and-true task for you:*\n\n{anweisung}",
    "RESURFACE_ERTEILT": "✅ Task issued again (category: {kategorie})",

    # --- Ziele ------------------------------------------------------------------------------------
    "ZIELE_KEINE": (
        "You haven't set any goals yet. Write me what you want to achieve "
        "or update your profile with /profil."
    ),
    "ZIELE_WARTE": "📊 One moment, I'm analyzing your progress...",
    "ZIELE_PREFIX": "🎯 *Your goals & progress:*\n\n_Your goals:_ {ziele}\n\n{analyse}",
    "ZIELE_ERINNERUNG_PREFIX": "🎯 *Weekly goal reminder:*\n\n{erinnerung}",

    # --- Geheimnis ------------------------------------------------------------------------------------
    "GEHEIMNIS_START": (
        "🔒 *Store a secret*\n\n"
        "You can leave the slave a secret piece of information "
        "that he only learns at a specific point in time.\n\n"
        "Write the secret or /abbrechen"
    ),
    "GEHEIMNIS_DATUM_FRAGE": (
        "📅 When should the secret be revealed?\n\n"
        "Format: *DD.MM.YYYY HH:MM* or *in X days*\n"
        "Example: _25.12.2025 20:00_ or _in 7 days_"
    ),
    "GEHEIMNIS_DATUM_FEHLER": (
        "⚠️ The date could not be recognized.\n"
        "Please use the format *DD.MM.YYYY HH:MM* or *in X days*"
    ),
    "GEHEIMNIS_GESPEICHERT": "✅ Secret saved!\n\nReveal on: *{datum}*",

    # --- Wunsch-Kategorien --------------------------------------------------------------------------------
    "WUNSCHKAT_MENU": (
        "🎯 *Your wish categories*\n\n"
        "Current: _{aktuell}_\n\n"
        "You may pick up to *{max}* favorite categories.\n"
        "I'll let them flow into my suggestions – but I'm still the one who decides. 🖤\n\n"
        "*Available:*\n{katalog}\n\n"
        "Reply with the numbers separated by commas (e.g. `3, 14, 38`).\n"
        "Missing something? Just add it as text (e.g. `3, Cuckold`) – "
        "and I'll create it as a custom category.\n"
        "Or write `keine` to clear your selection, or /abbrechen"
    ),
    "WUNSCHKAT_KEINE_NUMMERN": "No valid selection recognized. Try again.",
    "WUNSCHKAT_EIGENE_NEU": "\n\n🆕 Newly created as custom category: {liste}",
    "WUNSCHKAT_MAX": "Maximum {max} categories. You gave {anzahl}.",
    "WUNSCHKAT_BEREICH": "Number {n} is outside the valid range (1-{max}).",
    "WUNSCHKAT_GESPEICHERT": (
        "Noted:\n{liste}\n\n"
        "Whether you get any of it is my decision. 🖤"
    ),
    "WUNSCHKAT_ZURUECKGESETZT": "✅ Your wish categories have been reset.",

    # --- Adaptive Kette ---------------------------------------------------------------------------------------
    "BUTTON_ANPASSUNG_SENDEN": "✅ Send adjustment",
    "BUTTON_ORIGINAL_SENDEN": "🗑 Send original",
    "KETTE_ANPASSUNG_VORSCHLAG": (
        "🔗 *Chain {pos}/{gesamt} – adjust?*\n\n"
        "He experienced the last task as _{stimmung}_. "
        "Suggestion for the next one:\n\n"
        "➡️ {adapted}\n\n"
        "_Original:_ {original}"
    ),
    "KETTE_GESENDET": "✅ {label} task sent to him.",
    "KETTE_FEHLSCHLAG_FRAGE": (
        "🔗 *Chain: link {pos}/{gesamt} was not completed.*\n\n"
        "The next link would be:\n_{naechste}_\n\n"
        "Should the chain continue or be aborted?"
    ),
    "BUTTON_KETTE_WEITER": "▶️ Continue",
    "BUTTON_KETTE_ABBRECHEN": "🛑 Abort chain",
    "KETTE_WEITER_BESTAETIGT": "▶️ Chain continues – link {pos}/{gesamt} sent to him.",
    "KETTE_ABGEBROCHEN_DOMINA": "🛑 Chain aborted – {anzahl} remaining link(s) discarded.",
    "KETTE_BEREITS_ENTSCHIEDEN": "⚠️ This chain has already been decided on.",

    # --- Kommentar / Meine Aufgaben / Reaktion ---------------------------------------------------------------------
    "KOMMENTAR_PREFIX": "💬 Feedback from your Mistress:\n\n{kommentar}",
    "MEINEAUFGABEN_KEINE": "You have no open tasks right now. 🖤",
    "MEINEAUFGABEN_TITEL": "📋 *Your open tasks:*\n",
    "BUTTON_NR_ABSCHLIESSEN": "✅ Complete no. {nr}",
    "MEINEAUFGABEN_NICHT_OFFEN": "This task is no longer open.",
    "REAKTION_ALTERNATIV_FRAGE": "What should I tell him instead?",
    "REAKTION_ANGEORDNET": "✅ Punishment has been ordered.",
    "REAKTION_WEITERGELEITET": "✅ Your message has been forwarded.",

    # --- Tiny-Task-Feedback ------------------------------------------------------------------------------------------
    "BUTTON_UEBERNOMMEN": "✅ Adopted",
    "BUTTON_GUT_NICHT_HEUTE": "👌 Good, but not today",
    "TINYFB_FRAGE": (
        "💬 *Quick question about today's suggestion*\n\n"
        "Suggestion (category: _{kategorien}_):\n"
        "_{inhalt}_\n\n"
        "You haven't forwarded it (yet). Pick directly or write a reason "
        "as text (e.g. 'too complex', 'wrong mood')."
    ),
    "TINYFB_KEIN_OFFENER": "No open tiny-task suggestion found within the last 72h.",
    "TINYFB_ANTWORT_UEBERNOMMEN": "✅ Noted as _adopted_.",
    "TINYFB_ANTWORT_GUT": "👌 Noted. Will count positively for future suggestions.",
    "TINYFB_NOTIERT": "📝 Noted: _{grund}_\nWill be taken into account in future suggestions.",

    # --- Strafen-Protokoll -----------------------------------------------------------------------------------------------
    "STRAFEN_KEINE": "📋 No punishments logged yet.",
    "STRAFEN_TITEL": "📋 *Punishment log (last 10):*\n\n",

    # --- Einstellungen -----------------------------------------------------------------------------------------------------
    # Sicherheitsrelevanter Hinweis: die Grenzen-Prüfung (limits_check) nutzt eine
    # DEUTSCHE Synonym-Liste – bei anderer Sprache greift nur das wörtliche Matching.
    "EINSTELLUNGEN_SPRACHE_LIMITS_WARNUNG": (
        "⚠️ *Important for safety:* The automatic limits check works with "
        "German term lists. For replies in {sprache}, only the literally "
        "stored limit terms are recognized – paraphrases are not. "
        "It's best to also store your hard limits in {sprache} (/profil)."
    ),
    "EINSTELLUNGEN_ZAHL_HINWEIS": "Please enter a number between 1 and 8 or /abbrechen",
    "EINSTELLUNGEN_FELD_PROMPT": "✏️ *{label}*\n\n{hinweis}",
    "EINSTELLUNGEN_STIL_UNBEKANNT": "I don't know that one. {hinweis}",
    # MarkdownV2 (wird mit dem MarkdownV2-Menü kombiniert) – "!" muss escaped sein
    "EINSTELLUNGEN_GESPEICHERT": "✅ Saved\\!",

    # --- Bewertung / Rest-Kleinkram ---------------------------------------------------------------------------------------
    "BEWERTUNG_1_5": "Please rate 1-5",
    "BEWERTUNG_KOMPLEX_HOCH": "📈 You really enjoyed your recent tasks – I'm raising the complexity!",
    "BEWERTUNG_KOMPLEX_NIEDRIG": "📉 I'm adjusting the task complexity to your preferences.",
    "BEWERTUNG_KOMPLEX_NORMAL": "📊 Task complexity adjusted back to normal.",
    "BEWERTUNG_TIPP_PREFIX": "💡 {tipp}",
    "BEWERTUNG_KOMMENTAR_FRAGE": "Got it. Want to give him a note as well? (otherwise /ueberspringen)",
    "BEWERTUNG_NOTIERT": "Got it – noted.",
    "KETTE_NICHT_VORHANDEN": "⚠️ This chain task no longer exists.",
    "TRAINING_DEAKTIVIERT": "Training is currently disabled.",
    "SERIE_EINMALIG": "✅ One-time task saved and forwarded.",
    "KOMMENTAR_GESENDET": "✅ He got it.",
    "KOMMENTAR_UEBERSPRUNGEN": "✅ Comment skipped.",
    # Bewusst ohne interne Begriffe ("State"/"Qdrant" gehören nicht in Nutzer-Texte)
    "RESURFACE_STATE_WEG": "⚠️ The selection is no longer active – wait for the next suggestion.",
    "RESURFACE_NICHT_GEFUNDEN": "⚠️ The old task can no longer be found.",

    "DOSSIER_WARTE": "⏳ Condensing what I know about him …",
    "DOSSIER_ZU_WENIG": (
        "Not enough material for a dossier yet – it builds up as he completes tasks, "
        "shares feelings and you two chat."
    ),
    "DOSSIER_PREFIX": "🗒 *What I know about him:*\n\n{text}",

    # --- Pairing (registration of new couples, PAIRING_ENABLED) ---
    "PAIRING_START_BEKANNT": "We're already connected. 😉 /hilfe shows you everything I can do.",
    "PAIRING_START_MENUE": (
        "👋 Welcome! This bot accompanies the two of you as a couple.\n\n"
        "Which role do you take?\n"
        "1 – the dominant part\n"
        "2 – the submissive part\n\n"
        "You'll then get an invite code for your partner.\n"
        "Already have a code? Just send it to me."
    ),
    "PAIRING_ROLLE_UNGUELTIG": "Please answer with 1, 2 – or send me an invite code.",
    "PAIRING_CODE_ERSTELLT": (
        "✅ Your invite code: `{code}`\n\n"
        "Your partner sends me /start and then this code – "
        "that connects the two of you. The code is valid for {stunden} hours."
    ),
    "PAIRING_CODE_UNGUELTIG": (
        "This code is invalid or expired. Check the spelling – "
        "or have your partner create a new one with /start."
    ),
    "PAIRING_ERFOLG": (
        "🎉 You're connected! Just send me a message and "
        "I'll set up everything else with you."
    ),
    "PAIRING_HINWEIS_START": "Send /start to get going.",

    # --- Admin/operator (ADMIN_CHAT_ID only) ---
    "ADMIN_PAARE_KOPF": "👥 Registered couples:",
    "ADMIN_INVITES_KOPF": "✉️ Open invites ({anzahl}):",
    "ADMIN_PAAR_LOESCHEN_USAGE": "Usage: /paar_loeschen <paar_id>",
    "ADMIN_PAAR_LOESCHEN_BESTAETIGUNG": (
        "⚠️ Really delete couple {paar_id} (dom={dom}, sub={sub}) IRREVERSIBLY?\n"
        "All of the couple's data (profiles, tasks, chats, …) will be removed "
        "from the database. Backups only rotate out after the retention period.\n\n"
        "To confirm, send:\n/paar_loeschen {paar_id} LOESCHEN"
    ),
    "ADMIN_PAAR_GELOESCHT": "✅ Couple {paar_id} removed, {punkte} data points deleted.",
    "ADMIN_PAAR_LOESCHEN_FEHLER": "⚠️ Errors in collections: {collections} – check the log and run again.",
    "ADMIN_PAAR_UNBEKANNT": "Couple {paar_id} is not registered (/paare shows the list).",
    "ADMIN_PAAR_ENV": "The env couple is managed via .env and cannot be deleted here.",
    "ADMIN_ZUGANG_BEENDET": "This bot access has been terminated and the associated data has been deleted.",
    "BUDGET_ERSCHOEPFT": (
        "⏸ Your couple's message quota for today is used up – "
        "we'll continue tomorrow. Commands like /hilfe keep working."
    ),
}
