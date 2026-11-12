"""
Wettvorschlag des Coachs.

Herausgelöst aus bot/prompts/followup.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.prompts.followup.
"""
from bot import config
from bot.prompts import rollen



_WETT_AUSGANG = {"gewonnen": "er hat gewonnen", "verloren": "er hat verloren",
                 "verfallen": "er hat sich nicht gemeldet, zählt als verloren",
                 "abgelehnt": "er hat die Wette abgelehnt"}


def _wie_lange_her(datum_iso: str, heute=None) -> str:
    """„heute" / „gestern" / „vor N Tagen" statt eines nackten Datums: das Modell
    kennt das heutige Datum nicht und erfand sonst Zeitbezüge (Live 18.09.2026:
    „letzte Woche" für eine Wette vom Vortag)."""
    from datetime import date, datetime
    from zoneinfo import ZoneInfo
    try:
        tag = date.fromisoformat(str(datum_iso)[:10])
    except ValueError:
        return str(datum_iso)
    heute = heute or datetime.now(ZoneInfo(config.TIMEZONE)).date()
    tage = (heute - tag).days
    if tage <= 0:
        return "heute"
    return "gestern" if tage == 1 else f"vor {tage} Tagen"


def wett_idee(sklave_vorlieben: list = None, sklave_hard_limits: list = None,
              domina_interessen: list = None, verbrauchte_zutaten: list = None,
              schwerpunkt: str = "", letzte_wetten: list = None, heute=None,
              domina_grenzen: list = None, sklave_dislike_kategorien: list = None) -> tuple[str, str]:
    """Coach-Impuls: fertige Wett-Idee für die Domina, zum Weitergeben an den Sub.
    Dieselben Bausteine wie die Aufgaben-Vorschläge (Live-Befund 07.09.: der
    erste Generator kannte weder Rollen-Rahmen noch Richtungs-Regel noch
    Zutaten-Sperre – die Ich-Perspektive der Vorlieben wurde 1:1 übernommen,
    der Sub wurde zur dritten Person und die Domina zugleich „du" und „sie").
    schwerpunkt: optionales Thema aus einem Wunsch-Wettvorschlag (coach_wette_wunsch)."""
    from bot.prompts import coach_persona
    s, d = rollen.sub(), rollen.dom()
    sub_nom_gross = s["label_nom"][0].upper() + s["label_nom"][1:]
    # Anrede des Subs nur als Negativ-Beispiel („kein ‚<Anrede>, wir machen …'"):
    # Live 15.09. begann die Idee mit dem Vokativ und war eine fertige Nachricht.
    try:
        from bot.services import persona_config  # lazy: kein Service-Import beim Prompt-Laden
        anrede = persona_config.sklave_anrede() or s["label_nom"]
    except Exception:
        anrede = s["label_nom"]
    zutaten_str = ""
    if verbrauchte_zutaten:
        zutaten_str = (
            "\nVERBRAUCHTE ZUTATEN (kamen in den letzten Aufgaben-Vorschlägen schon vor – "
            "heute in KEINEM Einsatz, auch nicht als Beiwerk):\n"
            + "\n".join(f"  • {z}" for z in verbrauchte_zutaten) + "\n"
        )
    verlauf_str = ""
    if letzte_wetten:
        zeilen = []
        for e in letzte_wetten[-5:]:
            datum = _wie_lange_her(e.get("datum", ""), heute)
            ausgang = _WETT_AUSGANG.get(e.get("ergebnis", ""), e.get("ergebnis", ""))
            zeilen.append(f"  • [{datum}, {ausgang}] {str(e.get('idee', ''))[:220]}")
        verlauf_str = (
            "\nLETZTE WETTEN (mit Ausgang): Bedingung UND Einsatz sollen diesmal andere sein – "
            "nicht dieselbe Messlatte in neuer Verpackung. Auf den letzten Ausgang darfst du dich "
            "locker beziehen (ein Halbsatz, Zeitangabe nur wie hier vermerkt), aber zähl die "
            "Wetten nie auf:\n"
            + "\n".join(zeilen) + "\n"
        )
    schwerpunkt_str = ""
    if schwerpunkt:
        schwerpunkt_str = (
            f"\nSCHWERPUNKT (ausdrücklicher Wunsch): Die Wette dreht sich um „{schwerpunkt}\" – "
            "als Einsatz oder als Teil der Bedingung. Richtung und Rollen genau so, wie die "
            "passende Vorliebe bzw. das passende Interesse unten sie festlegen.\n"
        )
    system = f"""Schlag {d['real_dat']} EINE konkrete Wette vor, die {d['nom']} {s['label_dat']} anbieten kann – als Coach, der {d['akk']} wie eine vertraute Freundin begleitet.

{coach_persona.fuer_aufgaben_vorschlag()}

ROLLEN UND ANREDE (strikt):
- Du sprichst {d['real_akk']} direkt mit „du" an. {sub_nom_gross} ist in deinem Text immer „{s['nom']}"/„{s['dat']}" bzw. {s['poss']}e Anrede – NIE „du", und NIE mit {d['real_dat']} verwechselt.
- Die Vorlieben unten sind aus {s['poss']}er Sicht notiert: „ich/mich/mein" = {s['nom']}, „{d['nom']}" bzw. „{d['real']}" = dein „du". Übersetze das konsequent in deine Anrede – aus „… wenn {d['nom']} Kaffee trinkt" wird „… während du Kaffee trinkst".
- Die Interessen {d['real_gen']} sind aus {d['poss'][:-1] if d['poss'].endswith('e') else d['poss']}er Sicht notiert: dort ist „ich/mein" = dein „du", und „{s['poss']}/{s['akk']}" = {s['label_nom']}.

DIE WETTE (strikt):
- 2–4 lockere Sätze (höchstens 500 Zeichen): die Wett-Bedingung (messbar, in den nächsten 1–3 Tagen entscheidbar) und was jede Seite bei Sieg bekommt.
- Wer mit wem was tut, ändert sich durch die Wette NICHT: jede Handlung behält in beiden Ausgängen genau die Richtung aus der Vorliebe bzw. dem Interesse – egal, wer gewinnt. Die Wette entscheidet nur, OB und WANN etwas passiert, nie die Rollen.
- Steht in einer Vorliebe oder einem Interesse „bei X", ist das die Folge von X – nie die Belohnung dafür, dass X ausbleibt. Wer verliert, bestimmt nicht selbst über seinen Einsatz.
- Safeword, Grenzen und Aftercare gelten immer und sind nie Teil der Wette.
- Du schreibst die IDEE an {d['real_akk']} – KEINE fertige Nachricht an {s['akk']}: kein „{anrede}, wir machen eine Wette …", keine Anführungszeichen, kein „schick mir ‚angenommen‘". Das Ausformulieren an {s['akk']} übernimmt der Bot auf Knopfdruck.
- Einsätze nur aus den Vorlieben {s['label_gen']} und den Interessen {d['real_gen']} unten – nichts Neues einführen; Richtung, Rollen und Bedingungen jeder Vorliebe EXAKT übernehmen.
- JE SEITE EIN Einsatz – keine Kette aus mehreren Praktiken („… plus … plus …"). Ein klarer Gewinn, ein klarer Preis.
- Kein Vorwort, keine Erklärung, KEINE Rückfrage am Ende („Willst du das so abschicken?", „Soll ich noch was ändern?") und kein Kommentar über die Wette selbst („kurz und klar", „in 1–2 Tagen entscheidbar") – die Weitergabe regelt der Bot. Nur der Vorschlag selbst.
{schwerpunkt_str}{verlauf_str}{zutaten_str}"""
    vorlieben_block = ("\n" + "\n".join(f"  - {v}" for v in sklave_vorlieben)) if sklave_vorlieben else " nicht angegeben"
    prompt = f"""Vorlieben {s['label_gen']} (verdecktes Steuerwissen, aus {s['poss']}er Sicht notiert – NIE als Liste oder Treffer erwähnen):{vorlieben_block}
Absolute Grenzen {s['label_gen']} (NIEMALS): {', '.join(sklave_hard_limits) if sklave_hard_limits else 'keine'}
Interessen {d['real_gen']}: {', '.join(domina_interessen) if domina_interessen else 'nicht angegeben'}"""
    # Grenzen der Domina und gelernte Abneigungen des Subs (12.11.2026): beides
    # kannte dieser Generator nicht. Abneigungen sind als Bedingung/Belohnung
    # tabu, als Preis einer Niederlage aber erlaubt (wie das SPANNUNGSFELD der
    # Aufgaben-Vorschläge: Strafe ja, Standard nein).
    if domina_grenzen:
        prompt += (f"\nPersönliche Grenzen {d['real_gen']} (NIEMALS, auch nicht umschrieben – "
                   f"\"langweilig\" heißt: will {d['nom']} nicht): {', '.join(domina_grenzen)}")
    if sklave_dislike_kategorien:
        prompt += (f"\nDas lehnt {s['label_nom']} wiederholt ab: {', '.join(sklave_dislike_kategorien)} – "
                   f"nie als Wett-Bedingung und nie als Gewinn; höchstens als Preis, den {s['nom']} "
                   f"bei einer Niederlage zahlt.")
    return system, prompt
