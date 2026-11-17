"""
Stimmungs- und Gefühlsfragen, Berichte erledigt/nicht erledigt.

Herausgelöst aus bot/prompts/followup.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.prompts.followup.
"""
import random
from bot.prompts import persona, rollen
from bot.prompts.followup import bausteine


# Nur offene Fragen nach dem ERLEBEN. Die früheren Varianten „Skala 1-5",
# „Würdest du das nochmal machen?" und „Was würdest du dir stattdessen
# wünschen?" lieferten Ziffern, Ja/Nein und Rückfragen – gespeichert und
# ausgewertet wurden sie trotzdem als „Gefühl", ohne die Frage dazu
# (Review D11: Ursache der unbrauchbaren Rückmeldungen).
_GEFUEHL_FRAGEN = [
    "Wie hat es sich angefühlt?",
    "Was war das Beste daran?",
    "Was ging dir dabei durch den Kopf?",
    "Welcher Moment ist dir am stärksten geblieben?",
    "Was hat es mit dir gemacht?",
]


_STIMMUNG_RICHTUNGEN = [
    # Bewusst Stichworte, KEINE fertigen Fragesätze: fertige Sätze schreibt das
    # Modell wörtlich ab, statt sie umzuformulieren (Befund 27.08.2026 – am
    # 25.08. ging der Seed unverändert als Frage an den Sub raus).
    # Ebenso bewusst OHNE Pronomen: die Sub-Rolle ist konfigurierbar, ein
    # hartes "sein/er" hier würde die Rollen-Pronomen aushebeln. Und ohne
    # zweite Person ("dir"/"du" = hier die Dom-Seite): schreibt das Modell
    # das Stichwort doch mal ab, kippt die Anrede sonst auf den Sub und
    # dreht die Richtung um (Befund 27.08.2026, vgl. AUFGABEN_ANTI_KLISCHEE).
    "das allgemeine Befinden im Moment",
    "wo der Kopf heute steht",
    "wie der bisherige Tag nachwirkt",
    "was gerade innerlich beschäftigt",
    "was gerade durch den Kopf geht",
    "wie viel Energie heute noch da ist",
    "das Beste und das Nervigste am heutigen Tag",
    "wie angespannt oder entspannt es gerade ist",
    "welche Unterstützung heute gebraucht wird",
    "wie der Körper sich heute anfühlt",
    "worauf heute Lust wäre und worauf gar nicht",
    "ein Wetter-Bild für den heutigen Tag",
    "was bei einem Gespräch von Angesicht zu Angesicht zuerst käme",
    "ob der Tag so läuft wie vorgestellt",
]


_STIMMUNG_TONLAGEN = [
    "ganz kurz und beiläufig, wie nebenbei hingeworfen",
    "fürsorglich, aber bestimmt",
    "neugierig und konkret nachhakend",
    "spielerisch-provozierend",
    "ruhig und ernst",
]


def _satzanfaenge(vermeiden: list[str], woerter: int = 3) -> list[str]:
    """Die ersten `woerter` Wörter der zuletzt gestellten Fragen – exakt die
    Wortfolgen, auf die der Wiederholungs-Detektor anspringt."""
    raus, gesehen = [], set()
    for v in vermeiden:
        teile = (v or "").split()[:woerter]
        if len(teile) < woerter:
            continue
        anfang = " ".join(teile).rstrip(",;:")
        if anfang.lower() not in gesehen:
            gesehen.add(anfang.lower())
            raus.append(anfang)
    return raus


def stimmung_richtungen() -> list[str]:
    """Die möglichen Richtungen – der Aufrufer wählt selbst, damit er über
    mehrere Versuche hinweg OHNE Zurücklegen ziehen kann (handlers/stimmung)."""
    return list(_STIMMUNG_RICHTUNGEN)


def _anrede() -> str:
    try:
        from bot.services import persona_config
        return (persona_config.sklave_anrede() or "").strip()
    except Exception:
        return ""


def stimmung_abfragen(vermeiden: list[str] | None = None,
                      richtung: str | None = None) -> tuple[str, str]:
    """Frisch formulierte Stimmungs-Frage in der Stimme der Herrin – statt Tag
    für Tag desselben statischen Texts (Nutzer-Feedback 2026-06-12). Zwei
    unabhängige Seeds (Richtung + Tonlage) lenken die Formulierung;
    `vermeiden` = die zuletzt gestellten Fragen als Sperr-Liste, weil das
    Modell sonst trotz Seed auf dieselbe Lieblings-Formulierung konvergiert
    (Nutzer-Feedback 2026-07-06: fast jeden Tag gleich formuliert).
    `richtung` kommt vom Aufrufer (Retry mit anderer Richtung, s.
    stimmung_richtungen); ohne Angabe wird gewürfelt."""
    frage = richtung or random.choice(_STIMMUNG_RICHTUNGEN)
    tonlage = random.choice(_STIMMUNG_TONLAGEN)
    s = rollen.sub()
    vermeiden_block = ""
    if vermeiden:
        letzte = "\n".join(f"- {v}" for v in vermeiden if v)
        vermeiden_block = (
            "So hast du zuletzt gefragt – formuliere heute DEUTLICH anders "
            f"(anderer Satzanfang, andere Wörter):\n{letzte}\n"
        )
        # Review D10: der Zwang zu immer „anderen Bildern" schraubte die Fragen
        # hoch, und die Anrede stand in jeder der letzten Fragen.
        vermeiden_block += (
            "Anders heißt NICHT kunstvoller: eine schlichte, direkte Frage, die man in einem "
            "Atemzug beantworten kann – höchstens ein Bild, keine verschachtelten Entweder-oder-Sätze.\n"
        )
        anrede = _anrede()
        if anrede and sum(1 for v in vermeiden if v and anrede.lower() in v.lower()) >= 2:
            # Nicht „nur ‚du'" schreiben: das Modell hängte daraufhin an jede Frage ein „…, du?" an.
            vermeiden_block += (f"Die Anrede „{anrede}\" stand zuletzt in fast jeder Frage. In der heutigen Frage "
                                "kommt sie NICHT vor – auch kein Ersatz dafür und kein angehängtes „du\" am "
                                "Satzende; die Frage spricht ihn einfach mit dir/dich/dein an.\n")
        # Die Satzanfänge NOCH EINMAL einzeln und wörtlich: der Detektor in
        # handlers/stimmung verwirft jede Frage, die mit denselben drei Wörtern
        # beginnt wie eine der letzten. Die Liste oben allein reicht nicht – die
        # Herrin startete trotzdem fast jede Frage mit "Ich frage mich"
        # (Befund 27.08.2026), womit reihenweise brauchbare Fragen durchfielen.
        anfaenge = _satzanfaenge(vermeiden)
        if anfaenge:
            gesperrt = ", ".join(f'"{a}"' for a in anfaenge)
            vermeiden_block += (
                f"Beginne auf KEINEN Fall mit einer dieser Wortfolgen: {gesperrt}. "
                "Such einen anderen Einstieg.\n"
            )
    system = (
        f"{bausteine._du_bist_dom()}. Frag {bausteine._sub_mit_poss('dein', 'akk')} kurz nach {s['poss']}er aktuellen Stimmung – "
        "Ich-Form, ein bis zwei Sätze, offen formuliert, in deinem Ton. "
        "Keine Aufgabe, keine Liste, keine Erklärung, keine Anführungszeichen.\n"
        f"Richtung diesmal: {frage} – Tonlage: {tonlage}.\n"
        "Die Richtung ist ein Stichwort NUR für dich: formuliere daraus deine eigene "
        "Frage. Übernimm die Stichwort-Formulierung NICHT wörtlich und schreib sie "
        "nicht ab.\n"
        f"{vermeiden_block}\n"
        f"{persona.fuer_sklaven_prompt()}"
    )
    user = "Formuliere jetzt genau EINE solche Frage."
    return system, user


# Der FRAGE-Prompt erfährt bewusst nichts von den Antworten zum Antippen: Ein
# Hinweis darauf („unter deiner Frage stehen gleich kurze Antworten …") ließ das
# Modell die Antwortvorschläge gleich selbst unter die Frage schreiben (Echt-Test
# 17.11.2026, 4 von 5 Fragen). Die Antworten entstehen getrennt, zur fertigen Frage.
ANTWORT_ANZAHL = 4
ANTWORT_MAX_ZEICHEN = 28


def stimmung_antworten(frage: str, nachbessern: bool = False) -> tuple[str, str]:
    """Kurze Antworten zum Antippen, passend zu GENAU dieser Stimmungsfrage.

    Die Frage wird täglich frisch und oft bildhaft gestellt; eine feste Skala
    (mies … aufgedreht) passt darauf selten, und ohne passenden Knopf blieb sie
    unbeantwortet. Zu jeder Antwort gehört ein Klartext: Der wird gespeichert
    und steht später als „Aktuelle Stimmung" in anderen Prompts – er muss ohne
    die Frage verständlich sein. Bewusst ohne Rollen-Wörter: die Frage kann an
    jede der beiden Personen gehen (Vanilla)."""
    from bot.services import persona_config
    system = (
        "Unter einer Frage nach der Stimmung stehen Antworten zum Antippen. Du schreibst, was die "
        "gefragte Person darauf antworten könnte.\n"
        f"Schreib genau {ANTWORT_ANZAHL} Antworten:\n"
        "- Jede ist eine direkte Antwort auf GENAU diese Frage und greift ihre Wörter oder ihr Bild auf.\n"
        "- Zusammen decken sie die Spannweite ab: bei einem Entweder-oder beide Seiten, dazu etwas "
        "dazwischen und eine Antwort für „weder noch\" oder „ganz anders\". Fragt die Frage nach mehreren "
        "Dingen, antworte auf ihren Kern – nicht auf jeden Teil einzeln.\n"
        "- Erfinde nichts aus dem Leben der Person: keine Ereignisse, Orte, Dinge oder Wünsche, die nur sie "
        "kennen kann. Fragt die Frage danach (was war das Beste, worauf hast du Lust), bleiben die Antworten "
        "auf der Ebene der Stimmung – etwa wie viel Schönes und wie viel Nerviges, wie viel Lust überhaupt.\n"
        f"- Kurz wie ein Knopf: zwei bis fünf Wörter, höchstens {ANTWORT_MAX_ZEICHEN} Zeichen, in den eigenen "
        "Worten der Person (Ich-Form oder Stichwort) – ohne Anrede, ohne Emoji, ohne Anführungszeichen, "
        "ohne Punkt am Ende.\n"
        "- Zu jeder Antwort ein Klartext: was sie über die Stimmung sagt – drei bis acht Wörter als "
        "Stichworte OHNE Subjekt und ohne Pronomen (nicht „sie ist …\", nicht „er fühlt …\"), nüchtern, "
        "OHNE das Bild der Frage, verständlich für jemanden, der die Frage nicht kennt.\n"
        'Antworte NUR mit JSON in dieser Form: {"antworten": [{"knopf": "…", "klartext": "…"}]}'
        f"{persona_config.sprache_anweisung()}"
    )
    user = bausteine.nutzer_text("Die Frage", frage)
    if nachbessern:
        user += (f"\n\nDein erster Versuch war unbrauchbar (Antworten zu lang für einen Knopf oder kein "
                 f"gültiges JSON). Jetzt wirklich: höchstens {ANTWORT_MAX_ZEICHEN} Zeichen je Antwort, nur das JSON.")
    return system, user


def gefuehl_abfragen(aufgabe: str) -> tuple[str, str]:
    frage = random.choice(_GEFUEHL_FRAGEN)
    s = rollen.sub()
    system = (
        f"{bausteine._du_bist_dom()}. {bausteine._gross(bausteine._sub_mit_poss('dein'))} hat die Aufgabe gerade erledigt. Frag {s['akk']} jetzt – Ich-Form – wie sich das angefühlt hat.\n"
        f"{bausteine._gross(s['nom'])} hat dir dazu noch NICHTS erzählt – du fragst zum ersten Mal. Unterstelle keinen "
        f"bereits erfolgten Bericht (nicht 'jetzt wo du mir erzählt hast …').\n"
        f"Ein oder zwei Sätze, offen formuliert. Keine Liste, keine Erklärung.\n"
        f"Variiere die Frage – nicht immer nur 'Wie hat es sich angefühlt?'. Diesmal: '{frage}'\n\n"
        f"{persona.fuer_sklaven_prompt()}"
    )
    user = f"Aufgabe (Kontext): {aufgabe}"
    return system, user


def bericht_erledigt(aufgabe: str, gefuehl: str, vorherige_gefuehle: list = None,
                     gefuehl_frage: str = "", level: int | None = None,
                     erledigte: int | None = None) -> tuple[str, str]:
    """EINE Nachricht an die Dom-Seite nach einer erledigten Aufgabe: was
    passiert ist plus ein Gedanke dazu. Vorher gingen dafür zwei LLM-Texte zum
    selben Ereignis raus (Bericht + Coach-Feedback) und danach die Bewertungs-
    frage als dritte Nachricht (Review D11) – die hängt der Aufrufer jetzt an."""
    from bot.prompts import coach_persona
    s, d = rollen.sub(), rollen.dom()
    vergleich = ""
    if vorherige_gefuehle:
        # Auch die FRÜHEREN Rückmeldungen sind roher Sklaven-Freitext → Delimiter
        # (die aktuelle unten läuft bereits über nutzer_text). Es sind die
        # letzten Rückmeldungen zu ANDEREN Aufgaben – kein Urteil über „ähnliche".
        gefuehle_str = ", ".join(vorherige_gefuehle)
        vergleich = (
            f"{bausteine.nutzer_text('Letzte Rückmeldungen ' + s['label_gen'] + ' zu anderen Aufgaben', gefuehle_str)}\n"
            f"→ Nur erwähnen, wenn ein klarer Unterschied zu heute auffällt.\n"
        )
    level_hinweis = f" – passend zu Level {level}" if level else ""
    system = (
        f"Du gibst {d['real_dat']} Bescheid, dass die Aufgabe erledigt ist – wie eine Freundin, die kurz erzählt, wie es gelaufen ist.\n"
        f"Drei bis fünf Sätze in EINER Nachricht, locker. Kein Bericht-Format, kein 'Status: Erledigt', keine Überschriften:\n"
        f"- zuerst, was passiert ist und wie es {s['dat']} damit ging;\n"
        f"- dann EIN eigener Gedanke dazu: eine Beobachtung oder ein konkreter nächster Schritt{level_hinweis}. "
        f"Nur wenn die Reaktion klar positiv war, darf ein kurzer Satz dazu, was das über {d['poss']} Wirkung sagt – ohne Pathos.\n"
        f"Keine Frage am Schluss: die Bitte um {d['poss']} Bewertung hängt der Bot selbst an.\n\n"
        f"WICHTIG: Fasse das Gefühl {s['label_gen']} ZUSAMMEN, zitiere es NICHT wörtlich. "
        f"Schreibe z.B. '{s['nom']} fand es spannend' statt '{s['nom']} schrieb: ich fand es spannend'. "
        f"Schütze die Intimität {s['label_gen']}.\n\n"
        f"{coach_persona.fuer_coach_prompt()}"
    )
    frage_zeile = (f"{bausteine.nutzer_text('Gestellte Frage an ' + s['akk'], gefuehl_frage)}\n"
                   if (gefuehl_frage or "").strip() else "")
    stand = f"Bisher erledigte Aufgaben gesamt: {erledigte}\n" if erledigte else ""
    user = (
        f"Aufgabe: {aufgabe}\n"
        f"{frage_zeile}"
        f"{bausteine.nutzer_text('Antwort ' + s['label_gen'], gefuehl)}\n"
        f"{stand}"
        f"{vergleich}"
    )
    return system, user


def bericht_nicht_erledigt(aufgabe: str) -> tuple[str, str]:
    from bot.prompts import coach_persona
    s, d = rollen.sub(), rollen.dom()
    system = (
        f"Du erzählst {d['real_dat']} kurz dass {bausteine._sub_mit_poss(d['poss'][:-1])} die Aufgabe nicht erledigt hat – wie eine Freundin, die das beiläufig erwähnt.\n"
        f"Ein bis zwei Sätze. Frag am Ende ob {d['nom']} {s['dat']} was ausrichten möchte. Kein Bericht-Format.\n\n"
        f"{coach_persona.fuer_coach_prompt()}"
    )
    user = f"Aufgabe: {aufgabe}"
    return system, user
