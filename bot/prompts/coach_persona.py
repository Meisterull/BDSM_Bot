"""
Persona des Coach (Domina-Sicht).

Eingebettet in domina_coach.py sowie in alle anderen Prompts, die Output an die
Domina erzeugen (Bestrafungsvorschlag, Coach-Feedback, Wochenplan etc.).
Konsistente Stimme über alle Pfade hinweg.
"""
from bot.prompts import rollen


# Der Coach-Stil kommt aus dem aktiven Preset (persona_presets.py).
def coach_stil() -> str:
    from bot.prompts import persona_presets
    return persona_presets.aktuelles_preset()["coach_stil"]


# Format-Block NUR für freie Chat-Antworten. Aufgaben-/Plan-/Bestrafungs-Prompts
# definieren ihr eigenes Format (oft "kein Markdown", eigene Längenlimits) und
# würden sich mit diesem Block im selben Prompt widersprechen – die nutzen
# fuer_strukturierten_output() bzw. fuer_aufgaben_vorschlag().
# Funktion statt Modul-Konstante: die Rollen-Konstellation ist Laufzeit-Config.
def _chat_format() -> str:
    d = rollen.dom()
    return f"""OUTPUT-FORMAT (Chat):
- Telegram-Markdown ist erlaubt: *fett*, _kursiv_, Listen mit – . KEIN **doppel-Sternchen** und keine ## Überschriften (rendert Telegram nicht). Nicht übertreiben – Strukturierung nur wenn es wirklich hilft. Kurze Antworten brauchen keine Formatierung.
- Länge: in der Regel 1-6 Sätze. Längere Antworten nur wenn {d['nom']} explizit nach Erklärung/Plan fragt."""


# Aufgaben-Komplexität: eine Zeile für die JETZT gültige Stufe – statt alle drei
# Stufen aufzulisten (war 3× dupliziert: domina_coach + tiny/ausfuehrlich).
_SCHWIERIGKEIT_LABELS = {
    # 5-15 statt 5-10 (D9/A1): deckungsgleich mit dem Tiny-Rahmen im System-Prompt.
    "niedrig": "einfache, kurze Aufgaben (5-15 Min.)",
    "normal": "Standard-Aufgaben (15-30 Min.)",
    "hoch": "komplexe, mehrstufige Aufgaben (30-60 Min.)",
}


def schwierigkeit_zeile(schwierigkeit: str) -> str:
    label = _SCHWIERIGKEIT_LABELS.get(schwierigkeit, _SCHWIERIGKEIT_LABELS["normal"])
    return f"Aufgaben-Komplexität: {schwierigkeit} → {label}"


def level_zeile(level: int) -> str:
    """Einheitliche Level-Beschriftung mit Skala – ein nacktes 'Level: 3' sagt dem
    Modell nichts über die Spannweite."""
    d = rollen.dom()
    return f"Level: {level} von 5 (1=Einsteiger{'in' if d['nom'] == 'sie' else ''}, 5=sehr erfahren)"


def _namen_block() -> str:
    """Optionaler Namens-/Sprach-Kontext (Bot-Name, Sub-Anrede, Sprache)."""
    from bot.services import persona_config
    zeilen = []
    name = persona_config.bot_name()
    anrede = persona_config.sklave_anrede()
    if name:
        zeilen.append(f'- Dein Name ist {name}. Du darfst dich so geben, wenn es natürlich passt.')
    if anrede:
        # Härtung analog persona.py (Test-Befund F6): ohne "ausschließlich" kopiert
        # das Modell nach einer Anrede-Änderung die alte Form aus dem Verlauf.
        s = rollen.sub()
        label_akk = s["label_akk"][0].upper() + s["label_akk"][1:]
        zeilen.append(f'- {label_akk} nennt ihr "{anrede}" – benutze ausschließlich diese Anrede, '
                      f'wenn du über {s["akk"]} sprichst, auch wenn ältere Nachrichten im Verlauf eine andere verwenden.')
    setup = persona_config.setup_kontext()
    if setup:
        zeilen.append(f"- Setup/Kontext (so ist es bei ihnen wirklich, Aufgaben daran ausrichten): {setup}")
    # Sprach-Anweisung (zentraler i18n-Hebel: deckt alle Coach-seitigen Prompts ab)
    sprache = persona_config.sprache()
    if sprache:
        zeilen.append(f"- SPRACHE: Antworte ausschließlich auf {sprache}.")
    return ("\n" + "\n".join(zeilen)) if zeilen else ""


# Nur für den freien Coach-Chat: der Coach darf das ECHTE Liebesleben des Paares
# anfachen, nicht nur das D/s-Spiel verwalten. Bewusst NICHT in fuer_aufgaben_vorschlag/
# fuer_strukturierten_output – Aufgaben/Strafen-Generatoren sollen davon unberührt bleiben.
def _liebesleben_impuls() -> str:
    s, d = rollen.sub(), rollen.dom()
    return f"""ECHTES LIEBESLEBEN (beiläufig, nicht in jeder Nachricht):
- Die beiden sind ein echtes Paar, nicht nur {d['label']} und {s['label']}. Wenn es natürlich passt, lass ab und zu eine Idee fallen, wie {d['nom']} {d['poss']} reale Zweisamkeit/Intimität anfachen kann – ein Date, selbst die Initiative ergreifen, ein zärtlicher oder sinnlicher Moment abseits von Aufgaben und Strafen.
- Wie eine gute Freundin, die merkt, dass da mehr geht als nur das Spiel – kein Ratgeber-Ton, kein "es ist wichtig dass…", keine Tipp-Liste. Ein Gedanke, nebenbei, in deiner Stimme.
- Dräng nicht und mach kein Dauer-Thema draus. Lieber selten und echt als ständig."""


def fuer_coach_prompt() -> str:
    """Voller Chat-Baustein: Stimme + Liebesleben-Impuls + Chat-Format. Für freie Coach-Antworten."""
    return coach_stil() + _namen_block() + "\n\n" + _liebesleben_impuls() + "\n\n" + _chat_format()


def fuer_strukturierten_output() -> str:
    """Stimme OHNE Chat-Format – für Prompts, die ihr eigenes Format/Längenlimit
    vorgeben (z.B. 'kein Markdown', 'max. 4-5 Sätze')."""
    return coach_stil() + _namen_block()


# Anti-Klischee-Block für Aufgaben- und Strafen-Vorschläge.
# Verwende ihn in jedem Prompt, der konkrete Aufgaben/Strafen für den Sklaven generiert.
def _aufgaben_anti_klischee() -> str:
    s, d = rollen.sub(), rollen.dom()
    return f"""ANTI-KLISCHEE und PERSONALISIERUNG:
- VERMEIDE BDSM-101-Klischees, die in jedem Einsteiger-Forum stehen:
  • Kniebeugen mit lauter Zählung, Liegestütze, Standpause
  • "X-mal niederknien und Y aufsagen"
  • "Schreibe N mal: ich werde …"
  • Generische "halte unbequeme Position für Z Minuten"
  • Kalt duschen als Standard-Strafe
  • Zeilen-Schreiben als Strafe
- Diese Aufgaben sind austauschbar – jede Standardliste hat sie. Sie ignorieren das Profil.
- PERSONALISIERE stattdessen: nimm {s['poss']}e Vorlieben (als Hebel oder Umkehrung von Intensität/Rahmen), {s['poss']}e Kategorie-Reaktionsmuster (was wirkt bei {s['dat']}), die Interessen {d['real_gen']}, vergangene Aufgaben (nicht wiederholen, aber anknüpfen).
- Eine gute Aufgabe lässt sich nur für {s['akk'].upper()} formulieren, nicht für irgend{s['unbest_akk']}.
- RICHTUNG/ROLLEN NIEMALS UMKEHREN: Legt eine Vorliebe fest, wer gibt und wer empfängt, wer was mit wem tut oder eine sonstige Bedingung ("nur bei X", "nur wenn Y"), dann übernimm diese Richtung EXAKT. "Umkehrung" meint nur Intensität/Framing (sanft↔hart, Lust↔Strafe) – NIEMALS die physische Rolle oder eine genannte Bedingung. Beispiel: "Wachsspiel nur bei {d['real_dat']}, Sub gießt und entfernt das Wachs" heißt NIE, dass der Sub das Wachs abbekommt. Im Zweifel: die im Profil genannte Richtung gilt, eine Umkehrung, die ein Hard Limit berühren würde, ist ausgeschlossen."""


def fuer_aufgaben_vorschlag() -> str:
    """Coach-Stil + Anti-Klischee-Block. Für alle Aufgaben-Vorschlags-Prompts."""
    return coach_stil() + _namen_block() + "\n\n" + _aufgaben_anti_klischee()


def dossier_gekuerzt(dossier: str, limit: int = 1200) -> str:
    """Dossier an der letzten Satzgrenze vor `limit` kürzen statt hart mitten im
    Wort (Review D7, B5 – live fehlte ausgerechnet der angeschnittene
    Führungs-Hinweis). Fallback: Wortgrenze + Ellipse."""
    d = (dossier or "").strip()
    if len(d) <= limit:
        return d
    cut = d[:limit]
    satz_ende = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    if satz_ende > limit // 2:
        return cut[: satz_ende + 1]
    return cut.rsplit(" ", 1)[0] + " …"


def sklaven_kontext_block(sklave_profile: dict, domina_grenzen: list | None = None) -> str:
    """Einheitlicher Persönlichkeits-Kontext des Sklaven für alle Aufgaben-Generatoren
    (Würfel, Wochenplan, Inspiration, Arc, …) – das Daten-Gegenstück zu
    fuer_aufgaben_vorschlag(): Vorlieben, Grenzen, Reaktionsmuster, Charakter-Tags,
    Wunsch-/Dislike-Kategorien, offene Fäden und Dossier an einer Stelle, statt dass
    jeder Generator nur unvollständige Teile bekommt. Gehört in die User-Message
    (Daten, nicht Anweisung)."""
    from bot.services import kategorie_logik
    from bot.prompts.sklave import _tags_lesbar  # lokal: sklave.py importiert coach_persona

    s, d = rollen.sub(), rollen.dom()
    vorlieben = sklave_profile.get("vorlieben") or []
    hard_limits = sklave_profile.get("hard_limits") or []
    # Vorlieben je Zeile (nicht komma-verkettet): mehrere enthalten Klammer-Zusätze
    # mit Richtungs-/Bedingungs-Constraints ("nur bei der Domina, …"), die in einer
    # flachen Komma-Liste vom Modell abgetrennt und verdreht werden.
    if vorlieben:
        vorlieben_block = "\n" + "\n".join(f"    - {v}" for v in vorlieben)
    else:
        vorlieben_block = " nicht angegeben"
    zeilen = [
        f"Profil {s['label_gen']}:",
        f"  Vorlieben (als Hebel, nicht direkt benennen):{vorlieben_block}",
        f"  Absolute Grenzen (NIEMALS): {', '.join(hard_limits) if hard_limits else 'keine'}",
    ]
    spitzen = kategorie_logik.reaktions_spitzen(sklave_profile)
    if spitzen:
        zeilen.append(f"  Kategorie-Reaktionsmuster (was bei {s['dat']} wirkt): {spitzen}")
    tags = _tags_lesbar(sklave_profile.get("persoenlichkeit_tags"))
    if tags:
        zeilen.append(f"  Charakter/Muster: {', '.join(tags)}")
    wunsch = sklave_profile.get("wunsch_kategorien") or []
    if wunsch:
        zeilen.append(f"  Wunsch-Kategorien (bevorzugen, wenn es passt): {', '.join(wunsch)}")
    dislikes = kategorie_logik.dislike_kategorien(sklave_profile)
    if dislikes:
        zeilen.append(f"  Kategorien, die {s['nom']} wiederholt ablehnt (NIEMALS vorschlagen): {', '.join(dislikes)}")
    faeden = sklave_profile.get("offene_faeden") or []
    if faeden:
        zeilen.append(
            f"  Offene Fäden aus {s['poss']}en Gesprächen (kann die Aufgabe aufgreifen): "
            + "; ".join(f[:80] for f in faeden[:5])
        )
    dossier = dossier_gekuerzt(sklave_profile.get("dossier") or "")
    if dossier:
        zeilen.append(f"Was du über {s['akk']} weißt (Dossier):\n{dossier}")
    if domina_grenzen is not None:
        zeilen.append(
            f"Persönliche Grenzen {d['real_gen']} (NIEMALS überschreiten): "
            f"{', '.join(domina_grenzen) if domina_grenzen else 'keine'}"
        )
    return "\n".join(zeilen)


def _skill_kurztext(eintrag: dict) -> str:
    """Kondensat eines Skill-Eintrags: gespeicherte `kurzfassung`, sonst die
    ⚠️-Sektion des Volltexts (Alt-Einträge/fehlgeschlagene Kondensierung)."""
    kurz = (eintrag.get("kurzfassung") or "").strip()
    if kurz:
        return kurz[:800]
    inhalt = eintrag.get("inhalt") or ""
    idx = inhalt.find("⚠️")
    if idx < 0:
        return ""
    sektion = inhalt[idx:]
    # bis zur nächsten Emoji-Überschrift des Brief-Formats schneiden
    ende = min((p for p in (sektion.find(m, 2) for m in ("📈", "🛠", "💡", "🎯", "🧠", "🔬")) if p > 0),
               default=len(sektion))
    return " ".join(sektion[:ende].split())[:800]


async def skill_kontext_block(kategorien: list[str] | None = None, max_eintraege: int = 3) -> str:
    """Kondensierte /lerne-Wissens-Briefe (Sicherheit & Progression) für die
    Aufgaben-Generatoren. Mit `kategorien` nur die passenden Einträge (Würfel/
    Tiny-Task – Kategorie vorab bekannt), ohne alle vorhandenen (Arc/Wochenplan/
    Inspiration – die Kategorie wählt dort erst das LLM). Gehört in die
    User-Message (Daten). Best-effort: leerer String statt Exception – das Wissen
    ist Zusatzkontext und darf keine Generierung blockieren."""
    from bot.services import qdrant
    try:
        if kategorien is not None:
            eintraege = [e for e in [await qdrant.get_skill(k) for k in kategorien[:max_eintraege]] if e]
        else:
            eintraege = (await qdrant.list_skills())[:max_eintraege]
    except Exception:
        return ""
    zeilen = []
    for e in eintraege:
        kurz = _skill_kurztext(e)
        if kurz:
            zeilen.append(f"  {e.get('kategorie', '?')}: {kurz}")
    if not zeilen:
        return ""
    d = rollen.dom()
    return (
        f"Kuratiertes Wissen {d['real_gen']} zu Kategorien (Sicherheits-Hinweise strikt beachten, "
        "Progression als Maßstab für die Schwierigkeit):\n" + "\n".join(zeilen)
    )
