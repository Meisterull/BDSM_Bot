"""
Domina Coach System Prompt.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.prompts import coach_persona, rollen


def _tageszeit(stunde: int) -> str:
    if 5 <= stunde < 11:
        return "Morgen"
    elif 11 <= stunde < 14:
        return "Vormittag/Mittag"
    elif 14 <= stunde < 18:
        return "Nachmittag"
    elif 18 <= stunde < 22:
        return "Abend"
    else:
        return "Nacht"


def _saison_kontext(monat: int) -> str:
    if monat in (12, 1, 2):
        return "Winter – drinnen, warm, Kerzenlicht-Atmosphäre passt gut"
    elif monat in (3, 4, 5):
        return "Frühling – Aufbruchsstimmung, neue Rituale einführen"
    elif monat in (6, 7, 8):
        return "Sommer – draußen möglich, leichtere Aufgaben, Hitze beachten"
    else:
        return "Herbst – gemütlich, Rückzug, intensive Rituale passen gut"


def get(
    erfahrungsstand: str,
    level: int,
    interessen: list,
    grenzen: list,
    ziele: str,
    conversation_context: str,
    sklave_hard_limits: list = None,
    sklave_vorlieben: list = None,
    kinderfreie_zeiten: list = None,
    kind_anzahl: int | None = None,
    letzte_kategorien: list = None,
    sklave_persoenlichkeit: dict = None,
    rollenspiel: dict = None,
    schwierigkeit: str = "normal",
    vertrauens_score: dict = None,
    stimmung: str = "",
    lerntagebuch_context: str = "",
    coach_regeln: list = None,
    coach_notizen: list = None,
    domina_dossier: str = "",
    kategorien_pool: list = None,
) -> str:
    s, d = rollen.sub(), rollen.dom()
    # Nominativ mit Artikel ("Die Domina …" / "Der Dom …") – rollen.py führt
    # für die reale dominante Person nur Gen/Dat/Akk-Formen mit Artikel.
    dom_nom_gross = f"{'Die' if d['nom'] == 'sie' else 'Der'} {d['real']}"
    # Aktuelle Zeit in der Deployment-Zeitzone (Default Europe/Berlin)
    from bot import config
    jetzt = datetime.now(ZoneInfo(config.TIMEZONE))
    uhrzeit_str = jetzt.strftime("%H:%M")
    wochentag_str = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
                     "Freitag", "Samstag", "Sonntag"][jetzt.weekday()]
    tageszeit = _tageszeit(jetzt.hour)
    saison = _saison_kontext(jetzt.month)

    # Kinder-/Diskretions-Kontext (die Uhrzeit-Zeile steht zentral im zeit_kontext –
    # vorher war sie hier doppelt)
    n = kind_anzahl if isinstance(kind_anzahl, int) else None
    if n is None or n > 0:
        anzahl_str = (
            f"{n} Kind{'er' if (n or 0) != 1 else ''} im Haus"
            if isinstance(n, int) else "Kinder im Haus"
        )
        if kinderfreie_zeiten:
            zeiten_str = ", ".join(kinderfreie_zeiten)
            kinder_kontext = (
                f"{anzahl_str}. Kinderfreie Zeiten heute: {zeiten_str}\n"
                f"→ Außerhalb der kinderfreien Zeiten nur diskrete, leise, kinderfreie Aufgaben."
            )
        else:
            kinder_kontext = (
                f"{anzahl_str} – alle Aufgaben müssen diskret, leise und kinderfrei bleiben."
            )
    else:
        # n == 0: keine Kinder, keine Diskretions-Constraint
        kinder_kontext = ""

    # Zeit-Kontext: nur die JETZT gültigen Hinweise rendern – alle Regeln für alle
    # Tage/Zeiten aufzulisten verwässert den Prompt nur.
    if jetzt.weekday() == 0:
        wochentag_hinweis = "→ Montag: Frage aktiv nach dem Wochenende wenn es passt"
    elif jetzt.weekday() >= 4:
        wochentag_hinweis = "→ Wochenende/Freitag: Mehr Zeit verfügbar, anspruchsvollere Aufgaben möglich"
    else:
        wochentag_hinweis = "→ Mitte der Woche: Kurze, alltagstaugliche Aufgaben bevorzugen"
    tageszeit_hinweis = {
        "Morgen": "→ Morgens: energetisch, motivierend, auf den Tag einstimmen",
        "Abend": "→ Abends: reflektierend, ruhiger, Tagesrückblick einbeziehen",
        "Nacht": "→ Nachts: diskrete, leise Aufgaben bevorzugen",
    }.get(tageszeit, "")
    zeit_kontext = f"Aktuelle Uhrzeit: {uhrzeit_str} ({wochentag_str}, {tageszeit})\n{wochentag_hinweis}"
    if tageszeit_hinweis:
        zeit_kontext += f"\n{tageszeit_hinweis}"

    # Persönlichkeitsprofil des Sklaven
    persoenlichkeit_kontext = ""
    if sklave_persoenlichkeit:
        tags = sklave_persoenlichkeit.get("tags", [])
        reaktionen = sklave_persoenlichkeit.get("reaktionen", {})
        dossier = sklave_persoenlichkeit.get("dossier", "")
        offene_faeden = sklave_persoenlichkeit.get("offene_faeden", [])
        if tags or reaktionen or dossier or offene_faeden:
            # Nur die Zähl-Buckets summieren – die Dicts enthalten auch
            # Metadaten wie letztes_signal (Timestamp-String, fürs Decay).
            reaktionen_str = ", ".join(
                f"{kat}: {v.get('positiv', 0)}+ {v.get('neutral', 0)}~ {v.get('negativ', 0)}-"
                for kat, v in reaktionen.items()
                if v and sum(v.get(b, 0) for b in ("positiv", "neutral", "negativ")) > 0
            ) if reaktionen else ""
            dossier_zeile = f"Charakteristik von {s['dat']}: {dossier}\n" if dossier else ""
            faeden_zeile = (
                "Offene Fäden (woran man anknüpfen kann): " + "; ".join(offene_faeden) + "\n"
            ) if offene_faeden else ""
            persoenlichkeit_kontext = (
                f"\nBekannte Persönlichkeits-Muster {s['label_gen']}: {', '.join(tags) if tags else 'noch keine'}\n"
                f"Kategorie-Reaktionen: {reaktionen_str if reaktionen_str else 'noch keine'}\n"
                f"{dossier_zeile}{faeden_zeile}"
                f"→ Berücksichtige diese Muster bei Empfehlungen\n"
            )

    # Aufgaben-Kategorien Ausgleich
    kategorien_kontext = ""
    if letzte_kategorien:
        from collections import Counter
        zaehler = Counter(letzte_kategorien)
        dominante = zaehler.most_common(1)[0]
        if dominante[1] > 3:
            from bot import config
            pool = kategorien_pool or config.AUFGABEN_KATEGORIEN
            kategorien_kontext = (
                f"\nLetzte Aufgaben waren hauptsächlich '{dominante[0]}'.\n"
                f"→ Schlage zur Abwechslung mal etwas aus einer anderen Kategorie vor "
                f"(verfügbar: {', '.join(pool)})"
            )

    # Schwierigkeitsgrad: nur die aktuell gültige Stufe (gemeinsame Konstante)
    schwierigkeit_kontext = "\n" + coach_persona.schwierigkeit_zeile(schwierigkeit)

    # Vertrauens-Score-Kontext
    vertrauens_kontext = ""
    if vertrauens_score:
        score = vertrauens_score.get("score", 50)
        stufe = vertrauens_score.get("stufe", "unbekannt")
        quote = vertrauens_score.get("quote", 0)
        vertrauens_kontext = (
            f"\nVertrauens-Score {s['label_gen']}: {score}/100 ({stufe})\n"
            f"Erledigungsquote: {quote}%\n"
            f"→ Bei niedrigem Score: einfachere, sicherere Aufgaben empfehlen\n"
            f"→ Bei hohem Score: anspruchsvollere Aufgaben möglich"
        )

    # Stimmungs-Kontext des Sklaven
    stimmung_kontext = ""
    if stimmung:
        stimmung_kontext = f"\nAktuelle Stimmung {s['label_gen']}: {stimmung}\n→ Berücksichtige das bei Aufgaben-Empfehlungen und deinem Ton\n"

    # Langzeit-Wissen-Block (Lerntagebuch)
    lerntagebuch_block = (
        f"Verdichtetes Langzeit-Wissen (Wochen-Lerntagebuch):\n{lerntagebuch_context}\n\n"
        if lerntagebuch_context else ""
    )

    # Gelernte Regeln/Notizen (vom User bestaetigt) – stehen direkt nach dem Intro,
    # also VOR Persona-Block und allem Kontext
    regeln_block = ""
    regeln_zeilen = []
    if coach_regeln:
        for r in coach_regeln:
            regeln_zeilen.append(f"- {r}")
    if coach_notizen:
        for n in coach_notizen:
            regeln_zeilen.append(f"  ({n})")
    if regeln_zeilen:
        regeln_block = (
            f"⚡ GELERNTE REGELN UND NOTIZEN (von {d['real_dat']} bestaetigt, NIE ignorieren):\n"
            + "\n".join(regeln_zeilen)
            + "\n\n"
        )

    # Rollenspiel-Kontext
    rollenspiel_kontext = ""
    if rollenspiel and rollenspiel.get("szenario_name"):
        vokabular_str = ", ".join(rollenspiel.get("vokabular", [])) or "keines"
        # Anrede explizit IM Szenario-Block verankern (Test-Befund F6): Szenen-
        # Texte sprechen den Sklaven direkt an, und das Modell kopiert sonst die
        # alte Anrede aus früheren Szenen im Gesprächsverlauf.
        from bot.services import persona_config
        anrede = persona_config.sklave_anrede()
        anrede_zeile = (
            f'\n   Anrede {s["label_gen"]} in der Szene: AUSSCHLIESSLICH "{anrede}" '
            f"(ältere Szenen im Verlauf können eine andere nutzen – die gilt NICHT mehr)"
        ) if anrede else ""
        rollenspiel_kontext = f"""
⚠️ AKTIVER ROLLENSPIEL-MODUS: {rollenspiel['szenario_name']}
   Ton: {rollenspiel.get('ton', '')}
   Intensität: {rollenspiel.get('intensitaet', '')}
   Vokabular bevorzugen: {vokabular_str}{anrede_zeile}
   → Alle Antworten und Aufgaben-Vorschläge müssen diesem Szenario entsprechen
   → Bleibe konsequent im Modus bis er explizit beendet wird
"""

    # Vorlieben je Zeile (D9/M10, Muster coach_persona.sklaven_kontext_block):
    # Klammer-Zusätze mit Richtungs-/Bedingungs-Constraints fragmentieren in
    # einer flachen Komma-Liste und werden vom Modell verdreht (Einlauf-Klasse).
    if sklave_vorlieben:
        vorlieben_zeilen = "\n" + "\n".join(f"    - {v}" for v in sklave_vorlieben)
    else:
        vorlieben_zeilen = " nicht angegeben"

    return f"""Du begleitest {'eine' if d['nom'] == 'sie' else 'einen'} {d['real']} (mit du-Form). Du bist kein steifer Coach, sondern eine vertraute, erfahrene Begleiterin – wie eine beste Freundin, die im gleichen Thema unterwegs ist.{rollenspiel_kontext}

{regeln_block}{coach_persona.fuer_coach_prompt()}
{schwierigkeit_kontext}{vertrauens_kontext}{stimmung_kontext}

{zeit_kontext}
{kinder_kontext}
Aktuelle Saison: {saison} → Berücksichtige das bei Aufgaben-Vorschlägen
{kategorien_kontext}

Profil {d['real_gen']}:
  Erfahrungsstand: {erfahrungsstand}
  {coach_persona.level_zeile(level)}
  Interessen: {', '.join(interessen) if interessen else 'nicht angegeben'}
  Grenzen: {', '.join(grenzen) if grenzen else 'keine angegeben'}
  Ziele: {ziele}
{('  Wer ' + d['nom'] + ' als ' + d['label'] + ' ist: ' + domina_dossier + chr(10)) if domina_dossier else ''}
Profil {s['label_gen']} (nur als Kontext, niemals direkt ansprechen):
  Absolute Grenzen (NIEMALS überschreiten): {', '.join(sklave_hard_limits) if sklave_hard_limits else 'keine angegeben'}
  Vorlieben:{vorlieben_zeilen}
{persoenlichkeit_kontext}

{lerntagebuch_block}Vergangene Gespräche und Ereignisse:
{conversation_context if conversation_context else 'Noch keine Vorgeschichte.'}

Gedächtnis und Kontinuität:
- Beziehe dich auf vergangene Gespräche wenn es natürlich passt – aber NIE als Floskel ("Du hattest letzte Woche erwähnt …"). Formuliere es so, wie eine Freundin es sagen würde, die sich wirklich erinnert.
- Übertreibe Rückgriffe nicht. Nur wo es etwas Konkretes verbindet.

[AUFGABE: ...] Tag-Regeln:
- Füge [AUFGABE: <einzeilige Beschreibung>] NUR hinzu wenn:
  a) {dom_nom_gross} explizit eine konkrete Aufgabe für {s['label_akk']} formuliert, ODER
  b) {dom_nom_gross} dich bittet eine Aufgabe vorzuschlagen UND du eine konkrete vorschlägst
- Bei Begrüßungen, Fragen, allgemeinem Chat: KEIN Tag
- Bei allgemeinen Ideen oder Erklärungen ohne konkreten Auftrag: KEIN Tag
- Bei Berichten über vergangene Ereignisse: KEIN Tag
- Der Tag kommt ans Ende der Nachricht, niemals mittendrin
- Schreibe diese Regeln NIE als Text in deine Antwort aus – sie sind interne Anweisung."""


def format_context(entries: list[dict]) -> str:
    """Formatiert Konversations-Einträge aus Qdrant in lesbaren Kontext-String.

    Zeigt pro Eintrag: Datum, Themen, volle Zusammenfassung (Domina+Coach Texte),
    und wichtige Punkte. Wird nicht künstlich gekürzt – die Speicher-Seite hat
    bereits sinnvoll begrenzt (2000 Zeichen je Seite).
    """
    if not entries:
        return ""
    lines = []
    for entry in entries:
        datum = entry.get("datum", "")[:10]
        zusammenfassung = entry.get("zusammenfassung", "")
        wichtige_punkte = entry.get("wichtige_punkte", [])
        # Neues Feld 'themen' (Liste); Fallback auf altes 'thema'
        themen = entry.get("themen") or ([entry["thema"]] if entry.get("thema") else [])
        line = f"[{datum}]"
        if themen:
            line += f" Themen: {', '.join(themen)}"
        line += f"\n  {zusammenfassung}"
        if wichtige_punkte:
            punkte_iter = wichtige_punkte if isinstance(wichtige_punkte, list) else [wichtige_punkte]
            for p in punkte_iter:
                # Doppel-Rendering vermeiden (Review D7, B6): die wichtigen Punkte
                # sind Satz-Präfixe der Domina-Nachricht, die bereits in der
                # Zusammenfassung steht – nur zeigen, was NICHT schon oben steht.
                if p and p not in zusammenfassung:
                    line += f"\n  • {p}"
        lines.append(line)
    return "\n\n".join(lines)


def format_lerntagebuch(entries: list[dict]) -> str:
    """Formatiert verdichtete Wochen-Zusammenfassungen (Lerntagebuch)."""
    if not entries:
        return ""
    blocks = []
    for e in entries:
        zeitraum = e.get("zeitraum", "")
        inhalt = e.get("inhalt", "")
        blocks.append(f"📓 Lerntagebuch ({zeitraum}):\n{inhalt}")
    return "\n\n".join(blocks)