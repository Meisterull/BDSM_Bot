"""
Handler-Registrierung.

Herausgelöst aus bot/main.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.main.
"""
from telegram import Update
from telegram.ext import (
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
    Application,
    TypeHandler,
)
from bot import commands_katalog as ck
from bot.services import paare
from bot.handlers import (
    domina,
    followup_response,
    profil,
    aufgaben,
    vorlagen,
    stimmung,
    inspiration,
    ziele,
    rueckblick,
    training,
    stats,
    rollenspiel,
    wochenplanung,
    wunsch,
    kommentar,
    geheimnis,
    strafen_protokoll,
    tinytask,
    wuerfel,
    wunschkategorien,
    privileg,
    wette,
    blitz,
    arc,
    event_arc,
    roulette,
    dauer,
    quiz,
    coach_quiz,
    advent,
    tiny_task_feedback,
    hilfe,
    resurface,
    stille_checkin,
    lerntagebuch,
    coach_regeln,
    skill,
    kette_adaptiv,
    dossier,
    namen,
    meine_aufgaben,
    einstellungen,
    luecke,
    pairing,
    admin,
    abwesenheit,
    inventar,
    herrin_versaeumnis,
    waehrung as waehrung_h,
)
from bot.handlers.profil import abbrechen_command
from bot.app import gates, routing



def register_handlers(app: Application) -> None:
    """Registriert alle Handler. Eigene Funktion, damit der Test-Harness
    (tests/harness.py) exakt dieselbe Verdrahtung nutzt wie der echte Bot."""
    app.add_error_handler(gates.error_handler)

    # Zentrales Eingangs-Logging (läuft vor allen anderen Handlern, blockiert nicht)
    # group=-3: Paar-Kontext (Mandanten-Auflösung für persona_config & Co.);
    # group=-2: Logging zuerst (auch für Updates, die der Pause-Guard blockt);
    # group=-1: Pause-Guard – PTB führt pro Gruppe nur EINEN Handler aus, daher
    # eigene Gruppen statt zwei TypeHandler in derselben.
    app.add_handler(TypeHandler(Update, gates.paar_kontext_setzen), group=-3)
    app.add_handler(TypeHandler(Update, gates.log_incoming), group=-2)
    app.add_handler(TypeHandler(Update, gates.pause_guard), group=-1)

    app.add_handler(CommandHandler(ck.aliases("profil"),      profil.show))
    app.add_handler(CommandHandler(ck.aliases("stats"),       stats.show))  # neu
    app.add_handler(CommandHandler(ck.aliases("app"),         routing.app_cmd))
    app.add_handler(CommandHandler(ck.aliases("aufgaben"),    aufgaben.show))
    app.add_handler(CommandHandler(ck.aliases("vorlagen"),    vorlagen.show))
    app.add_handler(CommandHandler(ck.aliases("inspiration"), inspiration.show))
    app.add_handler(CommandHandler(ck.aliases("ziele"),       ziele.show))
    app.add_handler(CommandHandler(ck.aliases("rueckblick"),  rueckblick.show))
    app.add_handler(CommandHandler(ck.aliases("lerntagebuch"), lerntagebuch.show))
    app.add_handler(CommandHandler(ck.aliases("dossier"),      dossier.show))
    app.add_handler(CommandHandler(ck.aliases("einstellungen"), einstellungen.show))
    app.add_handler(CommandHandler(ck.aliases("botname"),      namen.botname))
    app.add_handler(CommandHandler(ck.aliases("sklavenname"),  namen.sklavenname))
    app.add_handler(CommandHandler(ck.aliases("setup"),        namen.setup))
    app.add_handler(CommandHandler(ck.aliases("regel"),       coach_regeln.regel))
    app.add_handler(CommandHandler(ck.aliases("merken"),      coach_regeln.merken))
    app.add_handler(CommandHandler(ck.aliases("regeln"),      coach_regeln.regeln))
    app.add_handler(CommandHandler(ck.aliases("vergessen"),   coach_regeln.vergessen))
    app.add_handler(CommandHandler(ck.aliases("profil_check"), coach_regeln.profil_check))
    app.add_handler(CommandHandler(ck.aliases("lerne"),            skill.lerne))
    app.add_handler(CommandHandler(ck.aliases("lerne_neu"),        skill.lerne_neu))
    app.add_handler(CommandHandler(ck.aliases("skill_bearbeiten"), skill.skill_bearbeiten))
    app.add_handler(CommandHandler(ck.aliases("skills"),           skill.skills))
    app.add_handler(CommandHandler(ck.aliases("training"),    training.show))
    app.add_handler(CommandHandler(ck.aliases("loeschen"),    aufgaben.show_loeschen))
    app.add_handler(CommandHandler(ck.aliases("stimmung"),    stimmung.start))
    app.add_handler(CommandHandler(ck.aliases("rollenspiel"),         rollenspiel.show))
    app.add_handler(CommandHandler(ck.aliases("rollenspiel_beenden"), rollenspiel.beenden))
    app.add_handler(CommandHandler(ck.aliases("wochenplanung"),       wochenplanung.show))
    app.add_handler(CommandHandler(ck.aliases("wunsch"),              wunsch.show))
    app.add_handler(CommandHandler(ck.aliases("meinewuensche"),       wunsch.meine_wuensche))
    app.add_handler(CommandHandler(ck.aliases("meineaufgaben"),       meine_aufgaben.show))
    app.add_handler(CommandHandler(ck.aliases("geheimnis"),           geheimnis.show))
    app.add_handler(CommandHandler(ck.aliases("strafen"),             strafen_protokoll.show))
    app.add_handler(CommandHandler(ck.aliases("ueberspringen"),        kommentar.ueberspringen))
    app.add_handler(CommandHandler(ck.aliases("tinytask"),            tinytask.show))
    app.add_handler(CommandHandler(ck.aliases("tinyfb"),              tiny_task_feedback.manuelle_frage))
    app.add_handler(CommandHandler(ck.aliases("wuerfel"),             wuerfel.show))
    app.add_handler(CommandHandler(ck.aliases("roulette"),            roulette.show))
    app.add_handler(CommandHandler(ck.aliases("dauer"),               dauer.starten))
    app.add_handler(CommandHandler(ck.aliases("luecken"),             luecke.toggle))
    app.add_handler(CommandHandler(ck.aliases("abwesend"),            abwesenheit.command))
    app.add_handler(CommandHandler(ck.aliases("inventar"),            inventar.command))
    app.add_handler(CommandHandler(ck.aliases("blitz"),               blitz.toggle))
    app.add_handler(CommandHandler(ck.aliases("arc"),                 arc.show))
    app.add_handler(CommandHandler(ck.aliases("arc_starten"),         arc.starten))
    app.add_handler(CommandHandler(ck.aliases("arc_beenden"),         arc.beenden))
    app.add_handler(CommandHandler(ck.aliases("event"),               event_arc.show))
    app.add_handler(CommandHandler(ck.aliases("event_loeschen"),      event_arc.loeschen))
    app.add_handler(CommandHandler(ck.aliases("adventskalender"),     advent.command))
    app.add_handler(CommandHandler(ck.aliases("wunschkategorien"),    wunschkategorien.show))
    app.add_handler(CommandHandler(ck.aliases("privileg"),            privileg.show))
    # /wette gibt es auf beiden Seiten: Dom → Wettvorschlag auf Abruf, Sub → Punkte-Wette
    app.add_handler(CommandHandler(ck.aliases("wette"),               coach_quiz.wette_router))
    # /quiz gibt es auf beiden Seiten: der Router verzweigt nach Rolle
    # (Sub → Sklave-Quiz, Domina → Coach-Quiz).
    app.add_handler(CommandHandler(ck.aliases("quiz"),                coach_quiz.quiz_router))
    app.add_handler(CommandHandler(ck.aliases("hilfe"),               hilfe.show))
    app.add_handler(CommandHandler(ck.aliases("help"),                hilfe.show))
    app.add_handler(CommandHandler(ck.aliases("abbrechen"),           abbrechen_command))
    # /start: Mitglieder → Hinweis; Fremde → Pairing-Flow (nur PAIRING_ENABLED)
    app.add_handler(CommandHandler("start",                           pairing.start))
    # Betreiber-Kommandos (nur ADMIN_CHAT_ID; leer = aus)
    app.add_handler(CommandHandler("paare",                           admin.paare_liste))
    app.add_handler(CommandHandler("paar_loeschen",                   admin.paar_loeschen))

    # Kategorie-Filter Commands: EIN dynamischer Router statt ~40 fest registrierter
    # Handler – löst /aufgaben_<kategorie> zur Laufzeit gegen den Pool auf (Katalog +
    # eigene Kategorien aus dem Profil; neue eigene Kategorien ohne Bot-Neustart).
    app.add_handler(MessageHandler(
        filters.COMMAND & filters.Regex(r"^/aufgaben_"),
        aufgaben.show_kategorie_command,
    ))

    # Inline-Button Callbacks
    app.add_handler(CallbackQueryHandler(wunsch.callback_entscheidung,    pattern=r"^wunsch:"))
    app.add_handler(CallbackQueryHandler(privileg.callback_einloesen,     pattern=r"^privileg:einloesen:"))
    app.add_handler(CallbackQueryHandler(privileg.callback_entscheidung,  pattern=r"^privileg:(bestaetigen|verweigern):"))
    app.add_handler(CallbackQueryHandler(tiny_task_feedback.callback_button, pattern=r"^tinyfb:"))
    app.add_handler(CallbackQueryHandler(stimmung.callback_button, pattern=r"^stimmung:"))
    app.add_handler(CallbackQueryHandler(stille_checkin.callback,           pattern=r"^stille:"))
    app.add_handler(CallbackQueryHandler(wuerfel.callback,                pattern=r"^wuerfel:"))
    app.add_handler(CallbackQueryHandler(wette.callback,                  pattern=r"^wette:"))
    app.add_handler(CallbackQueryHandler(blitz.callback_fertig,           pattern=r"^blitz:fertig:"))
    app.add_handler(CallbackQueryHandler(roulette.callback,               pattern=r"^roulette:"))
    app.add_handler(CallbackQueryHandler(luecke.callback,                 pattern=r"^luecke:"))
    app.add_handler(CallbackQueryHandler(resurface.callback,              pattern=r"^resurface:"))
    app.add_handler(CallbackQueryHandler(coach_regeln.callback_bestaetigen, pattern=r"^coachregel:"))
    app.add_handler(CallbackQueryHandler(kette_adaptiv.callback,            pattern=r"^ketteanpass:"))
    app.add_handler(CallbackQueryHandler(kette_adaptiv.callback_fehlschlag, pattern=r"^kettefail:"))
    app.add_handler(CallbackQueryHandler(wochenplanung.callback,            pattern=r"^wochenplan:"))
    app.add_handler(CallbackQueryHandler(followup_response.callback,        pattern=r"^followup:"))
    app.add_handler(CallbackQueryHandler(meine_aufgaben.callback,           pattern=r"^meinetask:"))
    app.add_handler(CallbackQueryHandler(herrin_versaeumnis.callback,        pattern=r"^herrin:"))
    app.add_handler(CallbackQueryHandler(herrin_versaeumnis.callback_domina, pattern=r"^herrinfehl:"))
    app.add_handler(CallbackQueryHandler(domina.callback_sn_aufgabe,         pattern=r"^snaufgabe:"))
    app.add_handler(CallbackQueryHandler(coach_quiz.callback_wett_idee,      pattern=r"^wettidee:"))
    app.add_handler(CallbackQueryHandler(waehrung_h.callback_punkteabzug,     pattern=r"^punkteabzug:"))
    app.add_handler(CallbackQueryHandler(waehrung_h.callback_wunschpreis,     pattern=r"^wunschpreis:"))
    app.add_handler(CallbackQueryHandler(waehrung_h.callback_wunschziel,      pattern=r"^wunschziel:"))
    app.add_handler(CallbackQueryHandler(waehrung_h.callback_wetteeinspruch,  pattern=r"^wetteeinspruch:"))
    app.add_handler(CallbackQueryHandler(waehrung_h.callback_wetteurteil,     pattern=r"^wetteurteil:"))
    app.add_handler(CallbackQueryHandler(waehrung_h.callback_wetteantwort,    pattern=r"^wetteantwort:"))
    app.add_handler(CallbackQueryHandler(waehrung_h.callback_wetteablehnung,  pattern=r"^wetteablehnung:"))
    app.add_handler(CallbackQueryHandler(wunsch.callback_loeschen,          pattern=r"^wunschdel:"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, routing.handle_message))
    # Nicht-Text (Foto/Video/Voice/Dokument/…) direkt an den anderen Chat durchreichen
    app.add_handler(MessageHandler(
        filters.VOICE, routing.handle_voice
    ))
    app.add_handler(MessageHandler(
        ~filters.TEXT & ~filters.COMMAND & ~filters.VOICE & ~filters.StatusUpdate.ALL, routing.handle_media
    ))

    # group=1: läuft für JEDEN Command nach group 0 – einheitlicher Hinweis,
    # wenn die falsche (aber autorisierte) Rolle einen Command aufruft (F8).
    app.add_handler(MessageHandler(filters.COMMAND, routing.falsche_rolle_hinweis), group=1)
