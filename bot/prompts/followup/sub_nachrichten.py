"""
Prompts für Nachrichten an den Sub: Follow-up, Aufgabe, Wette, Strafe, Reaktionen, Serie/Kette.

Herausgelöst aus bot/prompts/followup.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.prompts.followup.
"""
from bot import config
from bot.prompts import persona, rollen
from bot.prompts.followup import bausteine



def followup_frage(
    aufgabe: str,
    streak: int = 0,
    letzte_nicht_erledigt: int = 0,
    stimmung: str = "",
    tage_her: int = 1,
) -> tuple[str, str]:
    s = rollen.sub()
    verlauf_hinweis = ""
    if streak > 3:
        verlauf_hinweis = f"Kontext: {s['nom']} hat {streak} Aufgaben in Folge erledigt – das darf in deinem Ton mitschwingen, aber nicht als Lob ausgespielt werden.\n"
    elif letzte_nicht_erledigt > 1:
        verlauf_hinweis = f"Kontext: {s['nom']} hat zuletzt {letzte_nicht_erledigt} Aufgaben nicht erledigt – dein Ton ist heute weniger nachgiebig, aber nicht laut.\n"
    stimmung_hinweis = ""
    if stimmung:
        # Stimmung ist 1:1 gespeicherter Sklaven-Freitext → mit Delimiter einbetten
        stimmung_hinweis = (
            f"{bausteine.nutzer_text('Aktuelle Stimmung ' + s['label_gen'], stimmung)}\n"
            f"→ Bei negativer Stimmung: feiner, weniger Druck.\n"
            f"→ Bei positiver Stimmung: darf neugieriger, spielerischer sein.\n"
        )
    zeit_label = "heute" if tage_her <= 0 else "gestern" if tage_her == 1 else f"vor {tage_her} Tagen"
    zeit_hinweis = (
        f"ZEITBEZUG: Die Aufgabe war für {zeit_label} gedacht, du fragst RÜCKBLICKEND. "
        f"Übernimm Zeitwörter aus dem Aufgabentext NICHT wörtlich – rechne sie um "
        f"(z.B. 'heute Abend' im Aufgabentext → '{zeit_label} Abend'). "
        f"Frag niemals im Präsens 'heute', wenn es {zeit_label} war.\n"
    )
    system = (
        f"{bausteine._du_bist_dom()}. Frag {bausteine._sub_mit_poss('dein', 'akk')} jetzt – in der Ich-Form – ob {s['nom']} die Aufgabe erledigt hat.\n"
        f"Eine Frage, ein bis zwei Sätze, klar genug für Ja/Nein.\n"
        f"Frag nur, OB {s['nom']} sie so erfüllt hat, wie du es wolltest. Zieh KEINE Straf- oder Zahlen-Mechanik "
        f"aus dem Aufgabentext in die Frage (keine 'zehn Hiebe', keine Streak-Zahlen, nichts zum 'Nachzählen') "
        f"– das klingt konstruiert. Eine leichte, natürliche Andeutung von Konsequenz ist ok.\n\n"
        f"{persona.fuer_sklaven_prompt()}"
    )
    user = (
        f"{verlauf_hinweis}{stimmung_hinweis}{zeit_hinweis}"
        f"Aufgabe (Kontext, nicht wörtlich wiederholen): {aufgabe}"
    )
    return system, user


def aufgabe_an_sklaven(aufgabe: str, rollenspiel_ton: str = "") -> tuple[str, str]:
    s = rollen.sub()
    ton_hinweis = f"\nZusätzlicher Ton-Modus (Rollenspiel): {rollenspiel_ton}\n" if rollenspiel_ton else ""
    system = (
        f"{bausteine._du_bist_dom()}. Verwandle die folgende Aufgabe in einen Befehl von dir persönlich – Ich-Form, direkt an {s['akk']}.\n"
        f"Ein bis drei Sätze. Keine Einleitung, keine Erklärung, kein Coach-Ton.\n\n"
        f"{persona.fuer_sklaven_prompt()}{ton_hinweis}"
    )
    user = f"Aufgabe (Kontext, formuliere sie als deinen eigenen Befehl, nicht wörtlich): {aufgabe}"
    return system, user


def nachricht_an_sklaven(inhalt: str) -> tuple[str, str]:
    """Freie (Sprach-)Nachricht der Herrin an den Sklaven – Coach-Relay über den
    [SPRACHNACHRICHT:]-Tag (handlers/domina.py). Bei aktivem Grok-TTS bekommt
    das LLM die Sprech-Tag-Anleitung mit; die Text-Bubble wird von den Tags
    befreit (tts.entferne_sprech_tags), gesprochen werden sie."""
    from bot.services import tts  # lazy: kein Service-Import beim Prompt-Laden
    s = rollen.sub()
    tag_block = f"\n{tts.SPRECH_TAG_ANLEITUNG}\n" if config.GROK_TTS else ""
    system = (
        f"{bausteine._du_bist_dom()}. Verwandle den folgenden Inhalt in eine kurze, persönliche "
        f"Sprachnachricht von dir an {s['akk']} – Ich-Form, dein Ton, direkt an {s['akk']} "
        f"gesprochen. Zwei bis vier Sätze. Keine Einleitung, keine Erklärung, kein Coach-Ton.\n"
        f"{tag_block}\n"
        f"{persona.fuer_sklaven_prompt()}"
    )
    user = f"Inhalt (Kontext, formuliere ihn als deine eigene Nachricht, nicht wörtlich): {inhalt}"
    return system, user


def wette_an_sklaven(idee: str, einsatz: int = 0) -> tuple[str, str]:
    """Wettvorschlag des Coachs (📨-Button der Dom-Seite, handlers/coach_quiz):
    die Herrin formuliert die Idee als eigene Wett-Ansage an den Sub. In der
    Idee ist „du" die Dom-Seite und der Sub steht in dritter Person – hier
    dreht sich das: der Sub wird „du". Sprech-Tags bei Grok-TTS wie bei
    nachricht_an_sklaven; die Text-Bubble wird davon befreit."""
    from bot.services import tts  # lazy: kein Service-Import beim Prompt-Laden
    s = rollen.sub()
    tag_block = f"\n{tts.SPRECH_TAG_ANLEITUNG}\n" if config.GROK_TTS else ""
    system = (
        f"{bausteine._du_bist_dom()}. Unten steht eine Wett-Idee, die dein Coach dir vorgeschlagen hat "
        f"(dort bist DU ‚du‘, und {s['nom']} ist {s['label_nom']} in dritter Person). "
        f"Mach daraus deine eigene Wett-Ansage an {s['akk']} – Ich-Form, dein Ton, direkt an "
        f"{s['akk']} gesprochen, {s['akk']} sprichst du mit ‚du‘ an.\n"
        f"Drei bis fünf Sätze: die Bedingung (messbar, bis wann), was {s['nom']} bei Sieg bekommt, "
        f"was du bei Sieg bekommst – Einsätze und Richtung EXAKT wie in der Idee, nichts dazu "
        f"erfinden. Zum Schluss fragst du, ob {s['nom']} die Wette annimmt – ablehnen darf "
        f"{s['nom']}, aber das kostet {s['akk']} etwas, und was, entscheidest du. Keine Einleitung, "
        f"keine Erklärung, kein Coach-Ton, keine Regeln oder Bot-Mechanik.\n"
        + (f"Erwähne beiläufig in EINEM Halbsatz, dass {einsatz} Punkte obendrauf stehen "
           f"(gewinnt {s['nom']}, bekommt {s['nom']} sie, verliert {s['nom']}, sind sie weg) – "
           f"die Punkte sind Beigabe, der Kern der Wette sind die Einsätze aus der Idee.\n"
           if einsatz else "")
        + f"{tag_block}\n"
        f"{persona.fuer_sklaven_prompt()}"
    )
    user = f"Wett-Idee (Kontext, formuliere sie als deine eigene Ansage, nicht wörtlich): {idee}"
    return system, user


def wett_ergebnis_an_dom(idee: str, sub_gewonnen: bool, automatisch: bool = False) -> tuple[str, str]:
    """Wette entschieden (handlers/waehrung): der Coach sagt der Dom-Seite, was
    jetzt ansteht – Einsatz exakt aus der Abmachung. Owner-Wunsch 17.09.2026:
    hat sie gewonnen, darf es gemein und anfeuernd sein („Hab Spaß und zeig
    keine Gnade"). Punkte, Stand und Einspruch hängt der Bot deterministisch an."""
    from bot.prompts import coach_persona
    s, d = rollen.sub(), rollen.dom()
    sub_gross = s["label_nom"][:1].upper() + s["label_nom"][1:]
    if sub_gewonnen:
        lage = (f"{sub_gross} hat die Wette GEWONNEN – {d['nom']} hat verloren. Gönn {s['dat']} "
                f"{s['poss']} Glück spöttisch und erinnere {d['real_akk']}, was {d['nom']} {s['dat']} "
                f"jetzt laut Abmachung schuldet – frech und trocken, nicht beleidigt.")
    else:
        grund = f" ({s['nom']} hat sich nicht gemeldet – das zählt als verloren)" if automatisch else ""
        lage = (f"{sub_gross} hat die Wette VERLOREN{grund} – {d['nom']} hat gewonnen. Feier den "
                f"Sieg frech und gemein und heiz {d['dat']} ein, den Einsatz gnadenlos einzutreiben – "
                f"im Ton von „Hab Spaß und zeig keine Gnade, du hast gewonnen“.")
    system = (
        coach_persona.fuer_strukturierten_output()
        + f"\n\n{lage}\n"
        f"Sag es {d['real_dat']} als vertraute beste Freundin in höchstens drei Sätzen – "
        f"was jetzt passiert, mit Einsatz, Richtung und Rollen EXAKT wie in der Abmachung unten: nur "
        f"den Teil, der zu diesem Ausgang gehört, nichts dazu erfinden, nichts weglassen. Die "
        f"Abmachung ist an {d['akk']} formuliert: „du“ meint {d['real_akk']}, „{s['nom']}“ meint {s['label_akk']}.\n"
        f"Du weißt nicht, wie die Wette gelaufen ist – erfinde keinen Verlauf (wann oder woran "
        f"{s['nom']} gescheitert ist bzw. wie {s['nom']} durchgehalten hat) und keine Details, die "
        f"nicht in der Abmachung stehen (Hilfsmittel, Dauer, Ablauf). Gemein sein darf der Ton, "
        f"nicht der Inhalt.\n"
        f"Punkte, Kontostand und Einspruch nennt der Bot selbst – erwähne sie nicht. Kein Vorwort, "
        f"keine Anrede-Floskel, keine Anführungszeichen, kein Markdown, keine Frage am Schluss."
    )
    user = bausteine.nutzer_text("Abmachung der Wette", idee)
    return system, user


def strafe_fuer_ablehnung(strafe: str) -> tuple[str, str]:
    """Wette abgelehnt → die Dom-Seite hat eine Strafe gewählt oder selbst
    geschrieben (handlers/waehrung): die Herrin ordnet sie in eigener Stimme an.
    Sprech-Tags bei Grok-TTS wie bei wette_an_sklaven; die Text-Bubble wird
    davon befreit."""
    from bot.services import tts  # lazy: kein Service-Import beim Prompt-Laden
    s = rollen.sub()
    sub_gross = s["label_nom"][:1].upper() + s["label_nom"][1:]
    tag_block = f"\n{tts.SPRECH_TAG_ANLEITUNG}\n" if config.GROK_TTS else ""
    system = (
        f"{bausteine._du_bist_dom()}. {sub_gross} hat deine Wette abgelehnt. Unten steht die Strafe, "
        f"die du dafür gewählt hast. Ordne sie {s['dat']} jetzt an – Ich-Form, dein Ton, direkt an "
        f"{s['akk']} gesprochen, {s['akk']} sprichst du mit ‚du‘ an.\n"
        f"Zwei bis vier Sätze: dass das die Quittung fürs Ablehnen ist, und die Strafe selbst – "
        f"Inhalt, Richtung und Umfang EXAKT wie unten, nichts dazu erfinden. Keine Einleitung, "
        f"keine Erklärung, kein Coach-Ton, keine Bot-Mechanik, kein [AUFGABE: …]-Tag.\n"
        f"{tag_block}\n"
        f"{persona.fuer_sklaven_prompt()}"
    )
    user = f"Strafe (Kontext, formuliere sie als deine eigene Anordnung, nicht wörtlich): {strafe}"
    return system, user


def reaktion_auf_gefuehl(aufgabe: str, gefuehl: str) -> tuple[str, str]:
    """Kurze, persönliche Reaktion der Herrin auf die Gefühl-Antwort des Sklaven."""
    s = rollen.sub()
    system = (
        f"{bausteine._du_bist_dom()}. {bausteine._gross(bausteine._sub_mit_poss('dein'))} hat gerade beschrieben wie sich die Aufgabe angefühlt hat.\n"
        f"Reagiere konkret – ein bis drei Sätze, Ich-Form. Greife auf, was {s['nom']} gesagt hat (Wendung, Wort, Tonfall).\n"
        f"Keine generischen Lobesfloskeln, keine Verabschiedung wie 'gut gemacht' oder 'Bericht erstattet'.\n\n"
        f"{persona.fuer_sklaven_prompt()}"
    )
    user = (
        f"Aufgabe (Kontext): {aufgabe}\n"
        f"{bausteine.nutzer_text(bausteine._gross(s['poss']) + 'e Antwort', gefuehl)}"
    )
    return system, user


def serie_variationen(task_text: str, tage: int, kategorie: str = "") -> tuple[str, str]:
    """Coach-Prompt: macht aus einer Grund-Aufgabe `tage` aufeinander aufbauende
    Tagesaufgaben (Bogen statt stupider Wiederholung)."""
    from bot.prompts import coach_persona
    d = rollen.dom()
    system = (
        f"{'Die' if d['nom'] == 'sie' else 'Der'} {d['real']} möchte eine {tage}-Tage-Serie zu einer Grund-Aufgabe. Mach daraus {tage} "
        f"aufeinander aufbauende Tagesaufgaben – KEINE stupide Wiederholung, sondern einen Bogen: "
        f"Tag 1 Herantasten/Einführung, mittlere Tage Steigerung/Vertiefung, letzter Tag "
        f"Höhepunkt/Abschluss. Jede Aufgabe baut spürbar auf der vorigen auf.\n\n"
        f"{coach_persona.fuer_aufgaben_vorschlag()}\n\n"
        f"Gib GENAU {tage} Zeilen zurück, je eine Tagesaufgabe (1-2 Sätze), nummeriert '1.' bis "
        f"'{tage}.'. Keine Überschriften, kein Markdown, kein [AUFGABE: ...] Tag."
    )
    user = f"Grund-Aufgabe (Kategorie {kategorie or '?'}): {task_text}"
    return system, user


def kette_anpassung(naechste_aufgabe: str, vorheriges_gefuehl: str, stimmung: str) -> tuple[str, str]:
    """Coach-Prompt: passt die nächste (von der Domina geplante) Ketten-Aufgabe an das
    Feedback des Sklaven an. Die Intention der Domina bleibt erhalten."""
    from bot.prompts import coach_persona
    s, d = rollen.sub(), rollen.dom()
    hinweis = {
        "langweilig": f"Die letzte Aufgabe war {s['dat']} zu zahm – mach die nächste reizvoller, "
                      f"überraschender, mit mehr Biss. Wenn möglich Richtung {s['poss']}er Vorlieben ziehen.",
        "überfordert": f"Die letzte Aufgabe war {s['dat']} zu viel – mach die nächste kleiner und "
                       f"machbarer, ohne sie langweilig werden zu lassen.",
        "abgelehnt": f"Die letzte Aufgabe wollte {s['nom']} gar nicht – wähle einen deutlich anderen "
                     f"Ansatz oder Blickwinkel für die nächste.",
    }.get(stimmung, f"Passe die nächste Aufgabe an {s['poss']} Feedback an.")
    system = (
        f"Du bist der Coach {d['real_gen']}. Die nächste geplante Aufgabe {d['poss'][:-1]}er Aufgaben-Kette soll an "
        f"das Feedback {bausteine._sub_mit_poss(d['poss'][:-1], 'gen')} angepasst werden – {d['poss']} Grundidee bleibt erhalten.\n\n"
        f"{coach_persona.fuer_aufgaben_vorschlag()}\n\n"
        f"{hinweis}\n\n"
        f"Gib NUR den angepassten Aufgaben-Text zurück (1-3 Sätze). Keine Einleitung, kein Markdown, "
        f"keine Anführungszeichen, kein [AUFGABE: ...] Tag."
    )
    user = (
        f"Geplante nächste Aufgabe: {naechste_aufgabe}\n"
        f"{bausteine.nutzer_text(bausteine._gross(s['poss']) + ' Gefühl zur vorigen Aufgabe', vorheriges_gefuehl)}\n"
        f"Einordnung: {stimmung}"
    )
    return system, user


def reaktion_auf_stimmung(antwort: str) -> tuple[str, str]:
    """Kurze, persönliche Reaktion der Herrin, wenn der Sklave seine Stimmung mitteilt."""
    s = rollen.sub()
    system = (
        f"{bausteine._du_bist_dom()}. {bausteine._gross(bausteine._sub_mit_poss('dein'))} hat dir gerade von sich aus {s['poss']}e Stimmung mitgeteilt.\n"
        f"Reagiere darauf – ein bis drei Sätze, Ich-Form. Greife konkret auf, was {s['nom']} sagt "
        f"(ein Wort, eine Wendung) und spiel damit.\n"
        f"KEINE bürokratische Bestätigung ('notiert', 'informiert', 'weitergeleitet'), keine "
        f"generische Floskel, keine Verabschiedung. Du redest nicht in der dritten Person über dich.\n\n"
        f"{persona.fuer_sklaven_prompt()}"
    )
    user = bausteine.nutzer_text(bausteine._gross(s["poss"]) + "e Stimmungs-Nachricht", antwort)
    return system, user


def reaktion_auf_nicht_erledigt(aufgabe: str) -> tuple[str, str]:
    """Reaktion der Herrin, wenn der Sklave 'Nein' auf die Followup-Frage geantwortet hat."""
    s, d = rollen.sub(), rollen.dom()
    system = (
        f"{bausteine._du_bist_dom()}. {bausteine._gross(bausteine._sub_mit_poss('dein'))} hat gerade zugegeben, dass {s['nom']} die Aufgabe nicht erledigt hat.\n"
        f"Reagiere darauf – ein bis zwei Sätze, Ich-Form, direkt an {s['akk']}.\n"
        f"Keine bürokratische Verabschiedung ('habe {d['anrede']} informiert'), keine generische Drohung.\n"
        f"Ein Stich, eine Bemerkung, eine konkrete Ansage. Es kommt noch eine Strafe – aber die kommt extra.\n\n"
        f"{persona.fuer_sklaven_prompt()}"
    )
    user = f"Aufgabe (Kontext): {aufgabe}"
    return system, user


