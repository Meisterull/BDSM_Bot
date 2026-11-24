"""
Prompts für Nachrichten an den Sub: Follow-up, Aufgabe, Wette, Strafe, Reaktionen, Serie/Kette.

Herausgelöst aus bot/prompts/followup.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.prompts.followup.
"""
import re

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
    # Am selben Tag gibt es nichts umzurechnen (Review D11); sonst stehen die
    # Zeitwörter des Aufgabentexts als fertige Wendungen da – das frühere
    # Beispiel „'heute Abend' → 'vor 2 Tagen Abend'" stand wörtlich so in der
    # Frage an den Sub (live 18.11.2026).
    zeit_hinweis = bausteine.aufgaben_zeitbezug(aufgabe, max(0, tage_her), rueckfrage=True) + "\n"
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
    if not rollenspiel_ton:
        # Kein Aufrufer reichte den Ton je durch – ein laufendes Rollenspiel
        # färbte die Aufgaben deshalb nie (Review D11). Zentral nachschlagen.
        try:
            from bot import state
            rs = state.aktives_rollenspiel()
            if rs and rs.get("ton"):
                rollenspiel_ton = rs["ton"] + (f" (Intensität: {rs['intensitaet']})" if rs.get("intensitaet") else "")
        except Exception:
            rollenspiel_ton = ""
    ton_hinweis = f"\nZusätzlicher Ton-Modus (Rollenspiel): {rollenspiel_ton}\n" if rollenspiel_ton else ""
    system = (
        f"{bausteine._du_bist_dom()}. Verwandle die folgende Aufgabe in einen Befehl von dir persönlich – Ich-Form, direkt an {s['akk']}.\n"
        f"Ein bis drei Sätze. Keine Einleitung, keine Erklärung, kein Coach-Ton.\n"
        f"Zeiten, Zahlen, Bedingungen und angekündigte Folgen der Aufgabe bleiben vollständig erhalten – nichts weglassen, nichts dazuerfinden.\n\n"
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
        f"Drei bis fünf Sätze: worauf {s['nom']} wettet (die Bedingung – messbar, bis wann – und "
        f"bei welchem Ausgang {s['nom']} gewonnen hat), was {s['nom']} bei Sieg bekommt, was du bei "
        f"Sieg bekommst – Einsätze und Richtung EXAKT wie in der Idee, nichts dazu erfinden. Zum "
        f"Schluss fragst du, ob {s['nom']} die Wette annimmt. Ablehnen darf {s['nom']}, aber das "
        f"kostet {s['akk']} etwas – WAS, sagst du jetzt nicht: Das legst du erst fest, wenn "
        # Live 17.11.2026: „und was, entscheidest du" las das Modell als Auftrag,
        # gleich einen Preis zu erfinden („… oder du lehnst ab und machst dafür
        # X") – den kennt die Mechanik nicht, dort wählt die Dom-Seite erst nach
        # einer Ablehnung.
        f"{s['nom']} wirklich ablehnt. Nenne also keinen Preis und keine Strafe fürs Ablehnen. "
        f"Keine Einleitung, keine Erklärung, kein Coach-Ton, keine Regeln oder Bot-Mechanik.\n"
        + (f"Erwähne beiläufig in EINEM Halbsatz, dass {einsatz} Punkte obendrauf stehen "
           f"(gewinnt {s['nom']}, bekommt {s['nom']} sie, verliert {s['nom']}, sind sie weg) – "
           f"die Punkte sind Beigabe, der Kern der Wette sind die Einsätze aus der Idee.\n"
           if einsatz else "")
        + f"{tag_block}\n"
        f"{persona.fuer_sklaven_prompt()}"
    )
    user = f"Wett-Idee (Kontext, formuliere sie als deine eigene Ansage, nicht wörtlich): {idee}"
    return system, user


def wett_ergebnis_an_dom(idee: str, sub_gewonnen: bool, automatisch: bool = False,
                         ansage: str = "") -> tuple[str, str]:
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
    ansage = (ansage or "").strip()
    if ansage:
        # Die Ansage ist das, was der Sub gehört hat – und danach hat er
        # „gewonnen"/„verloren" gemeldet. Ohne sie deutete dieser Prompt die
        # Idee für sich und nannte der Dom-Seite den Einsatz des ANDEREN
        # Ausgangs (live 19.11.2026: angesagt war Sieg bei X, gemeldet wurde
        # der Einsatz für „nicht X").
        art = "die" if d["nom"] == "sie" else "der"
        system += (
            f"\nMASSGEBLICH dafür, bei welchem Ausgang {s['nom']} gewonnen hat, ist die ANSAGE unten: "
            f"So hat {art} {d['real']} {s['dat']} die Wette angesagt, und danach hat {s['nom']} "
            f"„{'gewonnen' if sub_gewonnen else 'verloren'}“ gemeldet. In der Ansage spricht {art} "
            f"{d['real']} selbst – „ich“ ist {d['nom']}, „du“ ist {s['label_nom']}. Lies daraus ab, "
            f"welcher Ausgang als {s['poss']} Sieg gilt, und nenne den Einsatz, der zu GENAU dem "
            f"gemeldeten Ausgang gehört. Sagt die Ansage dazu nichts Eindeutiges, gilt die Abmachung. "
            f"Was in der Ansage zum Ablehnen steht, ist erledigt – erwähne es nicht.\n"
        )
        user += "\n" + bausteine.nutzer_text("Ansage an " + s["akk"] + " (so wurde die Wette angesagt)", ansage)
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


def reaktion_auf_gefuehl(aufgabe: str, gefuehl: str, frage: str = "",
                         erteilt_am: str = "") -> tuple[str, str]:
    """Kurze, persönliche Reaktion der Herrin auf die Gefühl-Antwort des Sklaven.
    `erteilt_am` (ISO) ordnet die Zeitwörter des Aufgabentexts ein, wenn die
    Aufgabe nicht von heute ist."""
    s = rollen.sub()
    system = (
        f"{bausteine._du_bist_dom()}. {bausteine._gross(bausteine._sub_mit_poss('dein'))} hat gerade beschrieben wie sich die Aufgabe angefühlt hat.\n"
        f"Reagiere konkret – ein bis drei Sätze, Ich-Form. Greife auf, was {s['nom']} gesagt hat (Wendung, Wort, Tonfall).\n"
        f"Keine generischen Lobesfloskeln, keine Verabschiedung wie 'gut gemacht' oder 'Bericht erstattet'.\n\n"
        f"{persona.fuer_sklaven_prompt()}"
    )
    # Deine eigene Frage als Kontext: eine knappe Antwort („alles", eine Zahl)
    # ist ohne sie nicht zu deuten.
    frage_zeile = (f"{bausteine.nutzer_text('Deine Frage an ' + s['akk'] + ' war', frage)}\n"
                   if (frage or "").strip() else "")
    user = (
        f"Aufgabe (Kontext): {bausteine.aufgabe_mit_zeitbezug(aufgabe, erteilt_am)}\n"
        f"{frage_zeile}"
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


# Bauformen der Reaktion auf eine Stimmungs-Antwort. Die Form wählt der Aufrufer
# (handlers/stimmung), nicht das Modell: Ohne Vorgabe baute es die Reaktion Tag
# für Tag gleich – Antwort wiederholen, deuten, etwas ankündigen, zum Erzählen
# auffordern (Log-Durchsicht 22.11.2026). Bewusst Beschreibungen und keine
# Mustersätze: fertige Sätze schreibt das schnelle Modell ab.
_REAKTION_BAUFORMEN = {
    "ein_satz": "Genau EIN Satz, trocken hingeworfen – danach ist Schluss. Keine Frage.",
    "frage": "Genau EINE Frage und sonst nichts – ein ganzer Satz, mit dem du etwas Bestimmtes wissen willst, "
             "das offen geblieben ist. Kein Kommentar davor oder danach.",
    "umdrehen": "Zwei Sätze: Du nimmst die Antwort anders, als sie gemeint war, und bleibst dabei. "
                "Der zweite Satz ist eine Feststellung – keine Frage, keine Aufforderung.",
    "lesen": "Zwei Sätze: erst deine Deutung der Lage – als Feststellung in direkter Anrede –, "
             "dann dein Urteil darüber. Keine Frage, keine Aufforderung.",
    "nachhaken": "Zwei Sätze: ein knapper Kommentar, dann eine Frage nach genau dem, was die Antwort offen lässt.",
}

# Satzende, Gedankenstrich, Strichpunkt und Doppelpunkt trennen Teilsätze – ein
# Komma nicht (dahinter beginnt selten ein neuer Anlauf).
_TEILSATZ_GRENZE_RE = re.compile(r"[.!?…]+|\s+[–—-]\s+|[;:]\s+")
_ANFANG_MAX = 14


def reaktion_bauformen() -> list[str]:
    """Die möglichen Bauformen – der Aufrufer wählt, damit er über mehrere
    Tage hinweg wechseln kann (handlers/stimmung)."""
    return list(_REAKTION_BAUFORMEN)


def teilsatz_anfaenge(text: str, woerter: int = 2) -> list[str]:
    """Die ersten `woerter` Wörter jedes Satzes und jedes Gedankenstrich-Teils,
    kleingeschrieben und ohne Satzzeichen. Daran erkennt handlers/stimmung eine
    Reaktion, die wie die letzten gebaut ist – und genau diese Wortfolgen
    stehen deshalb auch im Auftrag."""
    aus, gesehen = [], set()
    for teil in _TEILSATZ_GRENZE_RE.split(text or ""):
        w = re.sub(r"[^\w ]", " ", teil.lower()).split()
        if len(w) < woerter:
            continue
        anfang = " ".join(w[:woerter])
        if anfang not in gesehen:
            gesehen.add(anfang)
            aus.append(anfang)
    return aus


def reaktion_auf_stimmung(antwort: str, frage: str = "", klartext: str = "", *,
                          vorherige: list[str] | None = None, bauform: str = "",
                          anrede_aus: bool = False,
                          nachbessern: list[str] | None = None) -> tuple[str, str]:
    """Kurze, persönliche Reaktion der Herrin, wenn der Sklave seine Stimmung mitteilt.
    `frage` = die Stimmungsfrage, auf die er antwortet (leer bei /stimmung aus
    eigenem Antrieb mit Standardfrage); `klartext` = Bedeutung einer angetippten Antwort.
    `bauform` = Schlüssel aus reaktion_bauformen(), `vorherige` = die letzten
    Reaktionen (jüngste zuerst), `anrede_aus` = die Anrede stand zuletzt fast
    immer drin und setzt heute aus, `nachbessern` = Mängel des ersten Versuchs."""
    s = rollen.sub()
    anlass = (f"hat gerade auf deine Frage nach {s['poss']}er Stimmung geantwortet" if (frage or "").strip()
              else f"hat dir gerade von sich aus {s['poss']}e Stimmung mitgeteilt")
    form = _REAKTION_BAUFORMEN.get(bauform)
    if form:
        auftrag = (f"- Form heute: {form}\n"
                   f"- Ich-Form. Bleib bei dem, was {s['nom']} gesagt hat – aber wiederhole die Antwort nicht erst "
                   f"im Wortlaut, um sie dann zu kommentieren; höchstens EIN Wort daraus greifst du auf.\n"
                   f"- Direkte Anrede: Du redest MIT {s['dat']} (du, dich, dir), nie in der dritten Person über "
                   f"{s['akk']} – und über {s['akk']} selbst, nicht über „deine Antwort\" oder „deine Worte\".\n"
                   f"- Die Form geht deiner Stil-Regel zum Offenhalten des Gesprächs vor: Verlangt sie keine "
                   f"Frage, dann stellst du keine und forderst auch nicht zum Erzählen auf.\n")
    else:
        auftrag = (f"- Ein bis drei Sätze, Ich-Form. Greife konkret auf, was {s['nom']} sagt "
                   f"(ein Wort, eine Wendung) und spiel damit.\n")
    # Die Nachricht kommt aus einem Zeit-Job oder nach einem Knopfdruck – es folgt
    # nichts darauf. Angekündigtes („gleich", „heute Abend") blieb deshalb immer aus.
    auftrag += (f"- Die Nachricht steht für sich: Du bist gerade nicht bei {s['dat']}, und danach kommt von dir "
                f"nichts weiter. Kündige deshalb nichts an, was du gleich, nachher oder heute noch mit {s['dat']} "
                f"machst oder {s['dat']} gibst, und stell keine Szene in Aussicht.\n"
                f"- KEINE bürokratische Bestätigung ('notiert', 'informiert', 'weitergeleitet'), keine "
                f"generische Floskel, keine Verabschiedung. Du redest nicht in der dritten Person über dich.\n")
    # Von den letzten Reaktionen gehen NUR die Satzanfänge in den Auftrag, nicht
    # der Wortlaut: Mit den vollen Texten davor übernahm das schnelle Modell im
    # Echt-Test deren Wendungen in die neue Reaktion (22.11.2026).
    anfaenge = [a for v in (vorherige or []) for a in teilsatz_anfaenge(v)]
    anfaenge = list(dict.fromkeys(anfaenge))[:_ANFANG_MAX]
    if anfaenge:
        auftrag += f"- Deine letzten Reaktionen auf {s['poss']}e Stimmung waren alle ähnlich gebaut – heute anders.\n"
    # Auch der Anfang der Antwort selbst steht in der Sperre: Die allgemeine Regel
    # oben hielt das Modell bei kurzen Antworten nicht davon ab, mit ihnen einzusteigen.
    gesperrt = list(dict.fromkeys(teilsatz_anfaenge(antwort)[:1] + anfaenge))
    if gesperrt:
        auftrag += ("- Beginne keinen Satz und keinen Teilsatz mit einer dieser Wortfolgen: "
                    + ", ".join(f'"{a}"' for a in gesperrt) + ".\n")
    if anrede_aus:
        try:
            from bot.services import persona_config
            anrede = (persona_config.sklave_anrede() or "").strip()
        except Exception:
            anrede = ""
        if anrede:
            # Nicht „nur ‚du'" schreiben: das Modell hängt sonst ein „…, du" an (s. stimmung_abfragen).
            auftrag += (f"- Die Anrede „{anrede}\" stand zuletzt in fast jeder Reaktion. Heute kommt sie NICHT "
                        f"vor – auch kein Ersatz dafür und kein angehängtes „du\" am Satzende.\n")
    if nachbessern:
        auftrag += ("- Dein erster Versuch ging so nicht: " + "; ".join(nachbessern)
                    + ". Schreib die Reaktion neu – in der Form von oben, ohne das.\n")
    system = (
        f"{bausteine._du_bist_dom()}. {bausteine._gross(bausteine._sub_mit_poss('dein'))} {anlass}. "
        f"Du reagierst darauf.\n\n"
        f"{persona.fuer_sklaven_prompt()}\n\n"
        f"FÜR DIESE NACHRICHT (geht den Stil-Regeln oben vor):\n"
        f"{auftrag}"
    )
    user = bausteine.stimmungs_daten(bausteine._gross(s["poss"]) + "e Stimmungs-Nachricht", antwort,
                                     frage, klartext, frage_label="Deine Frage")
    return system, user


def reaktion_auf_nicht_erledigt(aufgabe: str, erteilt_am: str = "") -> tuple[str, str]:
    """Reaktion der Herrin, wenn der Sklave 'Nein' auf die Followup-Frage geantwortet hat."""
    s, d = rollen.sub(), rollen.dom()
    system = (
        f"{bausteine._du_bist_dom()}. {bausteine._gross(bausteine._sub_mit_poss('dein'))} hat gerade zugegeben, dass {s['nom']} die Aufgabe nicht erledigt hat.\n"
        f"Reagiere darauf – ein bis zwei Sätze, Ich-Form, direkt an {s['akk']}.\n"
        f"Keine bürokratische Verabschiedung ('habe {d['anrede']} informiert'), keine generische Drohung.\n"
        f"Ein Stich, eine Bemerkung, eine konkrete Ansage. Es kommt noch eine Strafe – aber die kommt extra.\n\n"
        f"{persona.fuer_sklaven_prompt()}"
    )
    user = f"Aufgabe (Kontext): {bausteine.aufgabe_mit_zeitbezug(aufgabe, erteilt_am)}"
    return system, user


