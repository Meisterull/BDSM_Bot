"""
Gemeinsamer Command-Katalog – einzige Datenquelle für:

  * das /hilfe-Menü (bot/handlers/hilfe.py)  – gruppierte Darstellung
  * die BotCommand-Listen (bot/main.py)       – set_my_commands()

Pro Rolle (Domina / Sklave) eine Liste von Gruppen mit Einträgen.

Eintrag-Felder:
  command   – Command-Name ohne führenden "/"
  kurz      – Beschreibung fürs Bot-Menü (BotCommand)
  lang      – abweichende (meist ausführlichere) Beschreibung für /hilfe;
              None = identisch mit `kurz`
  im_menue  – erscheint im Telegram-Command-Menü
  in_hilfe  – erscheint im /hilfe-Text

Die Reihenfolge im Command-Menü weicht historisch von der Hilfe-Gruppierung
ab und wird daher separat in _DOMINA_MENUE_REIHENFOLGE / _SKLAVE_MENUE_-
REIHENFOLGE gepflegt (nur Namen – Beschreibungen kommen aus dem Katalog).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Eintrag:
    command: str
    kurz: str
    lang: str | None = None
    im_menue: bool = True
    in_hilfe: bool = True

    @property
    def hilfe_text(self) -> str:
        """Beschreibung für das /hilfe-Menü."""
        return self.lang if self.lang is not None else self.kurz


DOMINA_GRUPPEN: list[tuple[str, list[Eintrag]]] = [
    ("📋 Aufgaben & Vorlagen", [
        Eintrag("aufgaben",      "Erledigte und offene Aufgaben anzeigen"),
        Eintrag("aufgaben_alle", "Alle Aufgaben (alle Kategorien)", im_menue=False),
        Eintrag("loeschen",      "Offene Aufgabe pausieren oder löschen"),
        Eintrag("vorlagen",      "Aufgaben-Vorlagen verwalten"),
        Eintrag("inspiration",   "3 Aufgaben-Ideen passend zum Level"),
        Eintrag("tinytask",      "Tiny Task Vorschlag anfordern"),
        Eintrag("wuerfel",       "🎲 Surprise-Aufgabe würfeln"),
        Eintrag("roulette",      "🎰 Strafen-Roulette: die Maschine entscheidet",
                lang="Strafen-Roulette: Slot-Machine bestimmt Gnade oder Härte"),
        Eintrag("dauer",         "🕰 Dauer-Anweisung: /dauer <Stunden> <Text>",
                lang="Anweisung über Stunden mit unangekündigten Zwischen-Checks"),
    ]),
    ("📖 Storylines", [
        Eintrag("arc",         "📖 Aktive Storyline anzeigen",
                lang="Aktive Storyline anzeigen"),
        Eintrag("arc_starten", "Neue Storyline: /arc_starten <thema>"),
        Eintrag("arc_beenden", "Aktive Storyline beenden"),
        Eintrag("event",         "🎂 Event-Storyline planen (Finale am Datum)",
                lang="Event planen: /event <TT.MM.> [Tage] <Thema> – Storyline endet am Event-Tag"),
        Eintrag("event_loeschen", "Geplantes Event verwerfen", in_hilfe=False),
        Eintrag("adventskalender", "🎄 Adventskalender: 24 Türchen im Dezember",
                lang="Adventskalender planen – 1.-24.12. jeden Morgen ein Türchen"),
    ]),
    ("🎭 Rollenspiel & Wochenplanung", [
        Eintrag("rollenspiel",         "Rollenspiel-Szenario starten"),
        Eintrag("rollenspiel_beenden", "Rollenspiel beenden"),
        Eintrag("wochenplanung",       "Wochenplan erstellen"),
        Eintrag("training",            "Psycho-Training starten"),
    ]),
    ("📊 Statistik & Reflexion", [
        Eintrag("profil",     "Profil anzeigen und bearbeiten"),
        Eintrag("ziele",      "Ziele und Fortschritt anzeigen"),
        Eintrag("rueckblick", "Rückblick der letzten Wochen"),
        Eintrag("strafen",    "Strafen-Protokoll anzeigen"),
        Eintrag("geheimnis",  "Geheimnis hinterlegen",
                lang="Geheimnis für späteren Zeitpunkt hinterlegen"),
    ]),
    # Nur im Command-Menü sichtbar (historisch nicht Teil von /hilfe):
    ("🧠 Coach & Wissen", [
        Eintrag("quiz",             "🧠 Coach-Quiz: Fachwissen lernen oder Sklaven-Wissen prüfen"),
        Eintrag("lerntagebuch",     "📓 Coach-Gespräche der letzten Tage verdichten", in_hilfe=False),
        Eintrag("dossier",          "🗒 Charakteristik des Sklaven (was der Bot über ihn weiß)", in_hilfe=False),
        Eintrag("botname",          "🏷 Namen der Bot-Herrin festlegen", in_hilfe=False),
        Eintrag("sklavenname",      "🏷 Anrede für den Sklaven festlegen", in_hilfe=False),
        Eintrag("setup",            "🧩 Setup/Kontext (Rollen, Anatomie, Ausstattung) festlegen", in_hilfe=False),
        Eintrag("regel",            "⚡ Verbindliche Regel für den Coach setzen", in_hilfe=False),
        Eintrag("merken",           "📝 Notiz/Vorliebe merken", in_hilfe=False),
        Eintrag("regeln",           "📋 Gelernte Regeln & Vorschläge anzeigen", in_hilfe=False),
        Eintrag("vergessen",        "🗑 Regel deaktivieren (Nummer aus /regeln)", in_hilfe=False),
        Eintrag("profil_check",     "🧬 Profil-Updates manuell prüfen", in_hilfe=False),
        Eintrag("lerne",            "📚 Wissens-Brief zu einer Kategorie", in_hilfe=False),
        Eintrag("skills",           "📚 Vorhandene Wissens-Einträge", in_hilfe=False),
        Eintrag("lerne_neu",        "📚 Wissens-Brief neu generieren", in_hilfe=False),
        Eintrag("skill_bearbeiten", "✏️ Wissens-Eintrag überschreiben", in_hilfe=False),
    ]),
    ("⚙️ System", [
        Eintrag("einstellungen", "⚙️ Sprache & Persönlichkeit einstellen",
                lang="Sprache, Persönlichkeits-Stil, Namen und Setup einstellen"),
        Eintrag("abwesend",      "📆 Abwesenheit eintragen (fließt in Vorschläge ein)",
                lang="Abwesenheit eintragen: /abwesend 20.07.-02.08. Grund – Aufgaben & Vorschläge berücksichtigen den Zeitraum; /abwesend ende hebt auf"),
        Eintrag("luecken",       "🕊 Lücken-Füller an/aus", in_hilfe=False,
                lang="Bei längerer Aufgaben-Ruhe automatisch einen Task-Vorschlag bekommen (du gibst frei)"),
        Eintrag("blitz",         "⚡ Blitzaufgaben an/aus", in_hilfe=False,
                lang="Unangekündigte Mini-Aufgaben mit Countdown für den Sklaven (gehen direkt raus)"),
        Eintrag("ueberspringen", "Optionalen Kommentar überspringen"),
        Eintrag("app",           "📱 Mini-App: Cockpit & Sprachnachrichten-Studio",
                lang="Mini-App im Chat öffnen: Statistik-Cockpit + Sprachnachrichten-Studio (LAN)"),
        Eintrag("abbrechen",     "Aktuelle Aktion abbrechen"),
        Eintrag("hilfe",         "Alle Befehle anzeigen",
                lang="Diese Übersicht"),
    ]),
]


SKLAVE_GRUPPEN: list[tuple[str, list[Eintrag]]] = [
    ("📊 Status & Statistik", [
        Eintrag("profil", "Profil anzeigen und bearbeiten"),
        Eintrag("stats",  "Punkte, Streak und Abzeichen",
                lang="Punkte, Streak, Abzeichen, Privilegien"),
    ]),
    ("💬 Mitteilen", [
        Eintrag("stimmung",         "Stimmung mitteilen"),
        Eintrag("wunsch",           "Wunsch oder Vorschlag einreichen"),
        Eintrag("meinewuensche",    "Gesammelte Wünsche ansehen/aufräumen", in_hilfe=False),
        Eintrag("wunschkategorien", "Lieblings-Kategorien wählen",
                lang="Lieblings-Kategorien wählen (max 3)"),
    ]),
    ("🎁 Belohnungen", [
        Eintrag("privileg", "🎁 Privilegien einlösen",
                lang="Privilegien einlösen (kostet Punkte)"),
        Eintrag("wette", "🎰 Punkte-Wette: Doppelt oder nichts",
                lang="Punkte auf die nächste Aufgabe wetten (doppelt oder nichts)"),
        Eintrag("quiz", "🧠 Quiz: Wie gut kennst du deine Herrin?",
                lang="Quizfrage über deine Herrin – richtige Antwort gibt Punkte"),
    ]),
    ("📋 Aufgaben", [
        Eintrag("meineaufgaben", "📋 Offene Aufgaben ansehen & abschließen",
                lang="Offene Aufgaben ansehen & abschließen"),
    ]),
    ("⚙️ System", [
        Eintrag("abwesend",  "📆 Abwesenheit eintragen (fließt in Vorschläge ein)",
                lang="Abwesenheit eintragen: /abwesend 20.07.-02.08. Grund – Aufgaben & Vorschläge berücksichtigen den Zeitraum; /abwesend ende hebt auf"),
        Eintrag("app",       "📱 Mini-App: Deine Übersicht",
                lang="Mini-App im Chat öffnen: Punkte, Streak, Aufgaben als Übersicht (LAN)"),
        Eintrag("abbrechen", "Aktuelle Aktion abbrechen"),
        Eintrag("hilfe",     "Alle Befehle anzeigen",
                lang="Diese Übersicht"),
    ]),
]


# Reihenfolge im Telegram-Command-Menü (set_my_commands).
_DOMINA_MENUE_REIHENFOLGE = [
    "aufgaben", "inspiration", "tinytask", "wuerfel", "roulette", "dauer",
    "arc", "arc_starten", "arc_beenden", "event", "event_loeschen", "adventskalender",
    "loeschen", "vorlagen",
    "rollenspiel", "rollenspiel_beenden", "wochenplanung", "training",
    "ziele", "rueckblick",
    "quiz", "lerntagebuch", "dossier", "botname", "sklavenname", "setup",
    "regel", "merken", "regeln", "vergessen", "profil_check",
    "lerne", "skills", "lerne_neu", "skill_bearbeiten",
    "strafen", "geheimnis", "profil",
    "einstellungen", "abwesend", "luecken", "blitz", "ueberspringen", "app", "hilfe", "abbrechen",
]

_SKLAVE_MENUE_REIHENFOLGE = [
    "stats", "meineaufgaben",
    "stimmung", "wunsch", "meinewuensche", "wunschkategorien",
    "privileg", "wette", "quiz", "profil",
    "abwesend", "app", "hilfe", "abbrechen",
]

# Fallback-Menü für unbekannte Chats: nur /hilfe.
FALLBACK_MENUE: list[tuple[str, str]] = [("hilfe", "Alle Befehle anzeigen")]


def fallback_menue() -> list[tuple[str, str]]:
    """Menü für unbekannte Chats: bei aktiviertem Pairing steht /start vorn –
    sonst wüsste ein Fremder nicht, wie er das Pairing beginnt."""
    from bot import config
    if config.PAIRING_ENABLED:
        return [("start", "Loslegen / Paar verbinden")] + FALLBACK_MENUE
    return FALLBACK_MENUE


# ---------------------------------------------------------------------------
# Locale-Anbindung (Veröffentlichungs-Schritt 2): englische Command-Aliase und
# Beschreibungen kommen aus bot/locales/commands_en.py. Aliase werden IMMER
# mitregistriert (beide Namen funktionieren in jedem Deployment); nur das
# Telegram-Menü und /hilfe zeigen die Variante der aktiven BOT_LOCALE.
# ---------------------------------------------------------------------------

def _commands_en():
    """commands_en-Modul, best-effort (None solange die Datei fehlt)."""
    try:
        from bot.locales import commands_en
        return commands_en
    except ImportError:
        return None


def aliases(command: str) -> list[str]:
    """Registrierungs-Namen für CommandHandler: deutscher Name + ggf.
    englischer Alias."""
    en = _commands_en()
    alias = en.ALIASES.get(command) if en else None
    return [command, alias] if alias and alias != command else [command]


def _locale_anzeige() -> object | None:
    """commands_en nur, wenn die UI-Locale DES KONTEXT-PAARES Englisch ist
    (Menü/Hilfe). Fallback: Deployment-Default config.BOT_LOCALE."""
    from bot import config
    try:
        from bot.services import persona_config
        locale = persona_config.ui_locale() or config.BOT_LOCALE
    except Exception:
        locale = config.BOT_LOCALE
    return _commands_en() if locale == "en" else None


def anzeige_command(command: str) -> str:
    """Command-Name in der Anzeige-Variante der aktiven Locale."""
    en = _locale_anzeige()
    return en.ALIASES.get(command, command) if en else command


def anzeige_kurz(eintrag: "Eintrag") -> str:
    en = _locale_anzeige()
    if en:
        uebersetzt = en.BESCHREIBUNGEN.get(eintrag.command)
        if uebersetzt and uebersetzt[0]:
            return uebersetzt[0]
    return eintrag.kurz


def anzeige_hilfe_text(eintrag: "Eintrag") -> str:
    en = _locale_anzeige()
    if en:
        uebersetzt = en.BESCHREIBUNGEN.get(eintrag.command)
        if uebersetzt:
            kurz_en, lang_en = uebersetzt
            if lang_en is not None:
                return lang_en
            if kurz_en:
                return kurz_en
    return eintrag.hilfe_text


def anzeige_gruppe(titel: str) -> str:
    en = _locale_anzeige()
    return en.GRUPPEN.get(titel, titel) if en else titel


def _menue_liste(
    gruppen: list[tuple[str, list[Eintrag]]],
    reihenfolge: list[str],
) -> list[tuple[str, str]]:
    """Baut (command, kurz)-Paare in Menü-Reihenfolge und prüft Konsistenz.
    Command-Name + Beschreibung in der Variante der aktiven BOT_LOCALE."""
    eintraege = {e.command: e for _, grp in gruppen for e in grp}

    fehlend = [c for c in reihenfolge if c not in eintraege]
    if fehlend:
        raise ValueError(f"Menü-Reihenfolge verweist auf unbekannte Commands: {fehlend}")

    nicht_gelistet = [
        e.command for _, grp in gruppen for e in grp
        if e.im_menue and e.command not in reihenfolge
    ]
    if nicht_gelistet:
        raise ValueError(f"Commands mit im_menue=True fehlen in der Reihenfolge: {nicht_gelistet}")

    return [(anzeige_command(c), anzeige_kurz(eintraege[c])) for c in reihenfolge]


def _mit_aliasen(commands: set[str]) -> set[str]:
    """Command-Set + die englischen Aliase (Rollen-Guard muss beide kennen)."""
    en = _commands_en()
    if not en:
        return commands
    return commands | {en.ALIASES[c] for c in commands if c in en.ALIASES}


def alle_domina_commands() -> set[str]:
    """Alle Domina-Commands inkl. Aliase (auch im_menue=False) – für den Rollen-Guard."""
    return _mit_aliasen({e.command for _, grp in DOMINA_GRUPPEN for e in grp})


def alle_sklave_commands() -> set[str]:
    """Alle Sklave-Commands inkl. Aliase (auch im_menue=False) – für den Rollen-Guard."""
    return _mit_aliasen({e.command for _, grp in SKLAVE_GRUPPEN for e in grp})


def domina_menue() -> list[tuple[str, str]]:
    """(command, beschreibung)-Paare für das Domina-Command-Menü."""
    return _menue_liste(DOMINA_GRUPPEN, _DOMINA_MENUE_REIHENFOLGE)


def sklave_menue() -> list[tuple[str, str]]:
    """(command, beschreibung)-Paare für das Sklave-Command-Menü."""
    return _menue_liste(SKLAVE_GRUPPEN, _SKLAVE_MENUE_REIHENFOLGE)
