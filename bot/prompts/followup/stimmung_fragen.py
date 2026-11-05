"""
Stimmungs- und Gefühlsfragen, Berichte erledigt/nicht erledigt.

Herausgelöst aus bot/prompts/followup.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.prompts.followup.
"""
import random
from bot.prompts import persona, rollen
from bot.prompts.followup import bausteine


_GEFUEHL_FRAGEN = [
    "Wie hat es sich angefühlt?",
    "Was war das Beste daran?",
    "Würdest du das nochmal machen?",
    "Wie herausfordernd war das auf einer Skala 1-5?",
    "Was würdest du dir stattdessen wünschen?",
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
            f"(anderer Satzanfang, andere Wörter, anderes Bild):\n{letzte}\n"
        )
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


def bericht_erledigt(aufgabe: str, gefuehl: str, vorherige_gefuehle: list = None) -> tuple[str, str]:
    from bot.prompts import coach_persona
    s, d = rollen.sub(), rollen.dom()
    vergleich = ""
    if vorherige_gefuehle:
        # Auch die FRÜHEREN Gefühle sind roher Sklaven-Freitext → Delimiter
        # (das aktuelle Gefühl unten läuft bereits über nutzer_text).
        gefuehle_str = ", ".join(vorherige_gefuehle)
        vergleich = (
            f"{bausteine.nutzer_text('Frühere Reaktionen ' + s['label_gen'] + ' auf ähnliche Aufgaben', gefuehle_str)}\n"
            f"→ Wenn relevant: nimm Bezug darauf, hat {s['nom']} sich diesmal anders gefühlt?\n"
        )
    system = (
        f"Du berichtest {d['real_dat']} kurz was gerade passiert ist – wie eine Freundin, die {d['dat']} Bescheid gibt.\n"
        f"Zwei bis drei Sätze, locker. Kein Bericht-Format, kein 'Status: Erledigt'.\n\n"
        f"WICHTIG: Fasse das Gefühl {s['label_gen']} ZUSAMMEN, zitiere es NICHT wörtlich. "
        f"Schreibe z.B. '{s['nom']} fand es spannend' statt '{s['nom']} schrieb: ich fand es spannend'. "
        f"Schütze die Intimität {s['label_gen']}.\n\n"
        f"{coach_persona.fuer_coach_prompt()}"
    )
    user = (
        f"Aufgabe: {aufgabe}\n"
        f"{bausteine.nutzer_text('Stimmung ' + s['label_gen'], gefuehl)}\n"
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
