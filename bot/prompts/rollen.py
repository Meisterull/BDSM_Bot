"""
Rollen-Konfiguration: Labels, Pronomen und Anatomie-Grundierung für beide Rollen.

Veröffentlichungs-Schritt 1 (TODO 🚀 2026-07-02): die Konstellation
Frau-Herrin/Mann-Sklave war in persona.py hart codiert (GRUNDIERUNG inkl.
Anatomie-/Creampie-Logik, "deine Herrin", er/ihm überall). Dieses Modul
generiert alle rollen- und geschlechtsabhängigen Prompt-Bausteine aus
persona_config (`dom_geschlecht`, `sub_geschlecht`; Default frau/mann =
Bestandsverhalten). Prompt-Builder ziehen Labels/Pronomen von hier, statt
"Herrin"/"Sklave"/"er" zu hardcoden – die Migration der übrigen Builder
läuft inkrementell (siehe TODO).

Perspektive der Bausteine: "du" = die dominante Rolle (das LLM spielt sie),
"er/sie" = die devote Rolle (der/die reale Nutzer:in des Sub-Chats).
"""

# Deklinierte Formen je Geschlecht. Bewusst simple Lookup-Tabellen statt
# NLP-Magie – es sind genau zwei Werte pro Rolle, und die Prompt-Builder
# brauchen nur diese Handvoll Formen.
_DOM_FORMEN = {
    "frau": {
        "label": "Herrin",           # die Rolle als Wort
        "anrede": "deine Herrin",    # wie der Sub sie/ihn nennt
        "nom": "sie", "dat": "ihr", "akk": "sie", "poss": "ihre",
        "real": "Domina",            # die reale dominante Person (Coach-Texte)
        "real_gen": "der Domina",    # "Grenzen der Domina" / "des Doms"
        "real_dat": "der Domina",    # "mit der Domina" / "dem Dom"
        "real_akk": "die Domina",
    },
    "mann": {
        "label": "Herr",
        "anrede": "dein Herr",
        "nom": "er", "dat": "ihm", "akk": "ihn", "poss": "seine",
        "real": "Dom",
        "real_gen": "des Doms",
        "real_dat": "dem Dom",
        "real_akk": "den Dom",
    },
}

_SUB_FORMEN = {
    "mann": {
        "label": "Sklave",
        "label_nom": "der Sklave",    # "der Sklave hat …"
        "label_gen": "des Sklaven",   # "Profil des Sklaven"
        "label_dat": "dem Sklaven",   # "mit dem Sklaven"
        "label_akk": "den Sklaven",   # "den Sklaven nennt ihr …"
        "unbest_akk": "einen Sklaven",  # "(irgend)einen Sklaven"
        "nom": "er", "dat": "ihm", "akk": "ihn", "poss": "sein",
    },
    "frau": {
        "label": "Sklavin",
        "label_nom": "die Sklavin",
        "label_gen": "der Sklavin",
        "label_dat": "der Sklavin",
        "label_akk": "die Sklavin",
        "unbest_akk": "eine Sklavin",
        "nom": "sie", "dat": "ihr", "akk": "sie", "poss": "ihr",
    },
}


def dom_geschlecht() -> str:
    """'frau' oder 'mann'; unbekannte Werte fallen auf den Default 'frau' zurück."""
    from bot.services import persona_config
    g = (persona_config.dom_geschlecht() or "").strip().lower()
    return g if g in _DOM_FORMEN else "frau"


def sub_geschlecht() -> str:
    """'mann' oder 'frau'; unbekannte Werte fallen auf den Default 'mann' zurück."""
    from bot.services import persona_config
    g = (persona_config.sub_geschlecht() or "").strip().lower()
    return g if g in _SUB_FORMEN else "mann"


def dom() -> dict:
    """Formen-Set der dominanten Rolle (label/anrede/Pronomen)."""
    return _DOM_FORMEN[dom_geschlecht()]


def sub() -> dict:
    """Formen-Set der devoten Rolle (label/label_dat/label_akk/Pronomen)."""
    return _SUB_FORMEN[sub_geschlecht()]


def dom_poss_aus_sub_sicht() -> str:
    """Genitiv-Konstrukt aus Sub-Sicht: 'seiner Herrin' / 'ihrem Herrn' …
    (für Kopfzeilen wie 'aus der Ich-Form seiner Herrin')."""
    s, d = sub_geschlecht(), dom_geschlecht()
    poss = {"mann": {"frau": "seiner Herrin", "mann": "seines Herrn"},
            "frau": {"frau": "ihrer Herrin", "mann": "ihres Herrn"}}
    return poss[s][d]


# ---------------------------------------------------------------------------
# Kombi-Auswahl (Setup-Wizard + /einstellungen) und Platzhalter-Ersetzung
# für die Verhaltensregel-Templates (persona_presets.template).
# ---------------------------------------------------------------------------

# Reihenfolge = Menü-Nummern; F/M zuerst (Default/Bestandsverhalten).
KOMBIS = (("frau", "mann"), ("mann", "frau"), ("frau", "frau"), ("mann", "mann"))


def kombi_label(dg: str, sg: str) -> str:
    """Anzeigename einer Konstellation, z.B. 'Herrin & Sklave'."""
    return f'{_DOM_FORMEN[dg]["label"]} & {_SUB_FORMEN[sg]["label"]}'


def aktuelle_kombi_label() -> str:
    return kombi_label(dom_geschlecht(), sub_geschlecht())


def kombi_hinweis() -> str:
    """Nummerierte Kombi-Auswahl (für /einstellungen und das Onboarding)."""
    return "\n".join(f"{i}️⃣ {kombi_label(*k)}" for i, k in enumerate(KOMBIS, start=1))


def kombi_fuer_eingabe(text: str) -> tuple[str, str] | None:
    """Akzeptiert Nummer (1..4); None wenn ungültig."""
    try:
        i = int((text or "").strip())
        if 1 <= i <= len(KOMBIS):
            return KOMBIS[i - 1]
    except ValueError:
        pass
    return None


def platzhalter() -> dict[str, str]:
    """Ersetzungs-Map für die Verhaltensregel-Templates. Die F/M-Werte ergeben
    wortgleich den früher hardcodierten Text (Bestandsverhalten)."""
    d, s = dom(), sub()
    dom_frau = dom_geschlecht() == "frau"
    return {
        "{sub_nom}": s["nom"],
        "{sub_dat}": s["dat"],
        "{sub_dat_gross}": s["dat"].capitalize(),
        "{sub_akk}": s["akk"],
        "{sub_poss}": s["poss"],
        "{sub_poss_f}": s["poss"] + "e",     # "seine/ihre Wünsche"
        "{sub_poss_akk}": s["poss"] + "en",  # "seinen/ihren Wunsch"
        "{dom_rolle}": ("die " if dom_frau else "der ") + d["label"],
        # "Du bist seine Herrin / ihr Herr" – Possessiv des Subs + Dom-Label.
        "{dom_aus_sub_sicht}": (s["poss"] + "e " if dom_frau else s["poss"] + " ") + d["label"],
        # Die reale dominante Person hinter dem Bot (Relay-Regel).
        "{dom_real_nom}": d["nom"],
        "{dom_real_dat}": d["dat"],
        "{dom_real_akk}": d["akk"],
        "{dom_real_klammer}": (f'({s["poss"]}e echte Domina)' if dom_frau
                               else f'({s["poss"]} echter Dom)'),
    }


def ersetze_platzhalter(text: str, extra: dict[str, str] | None = None) -> str:
    """Platzhalter tolerant per replace ersetzen (kein .format – ein verirrtes
    '{' in einer Override-Datei des Betreibers darf nicht crashen)."""
    mapping = platzhalter()
    if extra:
        mapping.update(extra)
    for k, v in mapping.items():
        text = text.replace(k, v)
    return text


# ---------------------------------------------------------------------------
# GRUNDIERUNG – Anatomie-/Logik-Konsistenz, generiert aus der Geschlechter-Kombi.
# Die F/M-Fassung entspricht wortgleich dem bisherigen hardcodierten Block in
# persona.py (Bestandsverhalten); die anderen Kombis übertragen dieselbe Logik:
# Wer hat eine reale Sperma-Quelle, welche Creampie-Richtung ist überhaupt
# möglich, Spielzeug ejakuliert nie.
# ---------------------------------------------------------------------------

_ANATOMIE = {
    ("frau", "mann"): (
        "- Du bist eine FRAU – die Herrin. Bleib anatomisch und logisch konsistent: du hast selbst "
        "kein Sperma und keinen Penis; für Penetration nutzt du Strapon/Spielzeug.\n"
        "- Ein Strapon/Spielzeug EJAKULIERT NICHT. Es gibt also keinen 'Creampie' in IHM und nichts "
        "aus ihm zum Sauberlecken – dreh eine Cleanup-Szene NIEMALS so herum (du 'füllst' ihn nicht, "
        "das ergibt körperlich keinen Sinn).\n"
        "- Die EINZIGE reale Flüssigkeit hier ist SEIN Sperma, wenn ER kommt. Ein 'Creampie-Cleanup' "
        "funktioniert deshalb nur in EINER Richtung: ER spritzt in DICH und leckt es danach bei dir "
        "wieder sauber – falls so eine Szene aufkommt, immer nur so herum, nie umgekehrt. Was er "
        "konkret beschreibt, hat trotzdem Vorrang (siehe unten); erfinde nicht ungefragt einen "
        "Creampie dazu, wo keiner gemeint war."
    ),
    ("mann", "frau"): (
        "- Du bist ein MANN – der Herr. Bleib anatomisch und logisch konsistent: DU hast einen Penis "
        "und eigenes Sperma; sie hat beides nicht – wenn sie penetriert, dann mit Strapon/Spielzeug.\n"
        "- Ein Strapon/Spielzeug EJAKULIERT NICHT. Aus ihr kommt kein Sperma – es gibt nichts von ihr "
        "zum Sauberlecken; dreh eine Cleanup-Szene NIEMALS so herum.\n"
        "- Die EINZIGE reale Sperma-Quelle bist DU. Ein 'Creampie-Cleanup' funktioniert deshalb nur "
        "in EINER Richtung: DU spritzt in SIE, und aufgenommen wird danach bei IHR – nie umgekehrt. "
        "Was sie konkret beschreibt, hat trotzdem Vorrang (siehe unten); erfinde nicht ungefragt "
        "einen Creampie dazu, wo keiner gemeint war."
    ),
    ("frau", "frau"): (
        "- Ihr seid beide FRAUEN – du die Herrin, sie die Sklavin. Bleib anatomisch und logisch "
        "konsistent: keine von euch hat einen Penis oder Sperma; Penetration läuft über "
        "Strapon/Spielzeug, Finger oder Zunge.\n"
        "- Ein Strapon/Spielzeug EJAKULIERT NICHT. Es gibt hier KEINE reale Sperma-Quelle – erfinde "
        "also NIEMALS einen 'Creampie' oder Sperma, wo körperlich keins existieren kann."
    ),
    ("mann", "mann"): (
        "- Ihr seid beide MÄNNER – du der Herr, er der Sklave. Beide habt ihr einen Penis und "
        "eigenes Sperma. Bleib strikt dabei, WESSEN Sperma gemeint ist und wer wen penetriert – "
        "verwechsle Richtung und Quelle nie.\n"
        "- Ein 'Creampie-Cleanup' ist körperlich in beide Richtungen möglich – aber NUR in der "
        "Richtung, die er bzw. die Szene konkret beschreibt; dreh sie niemals um und erfinde "
        "nicht ungefragt einen Creampie dazu, wo keiner gemeint war."
    ),
}


# Kombi-spezifisches Beispiel zur "übernimm GENAU diese Richtung"-Regel – die
# Härtung stammt aus dem Richtungs-Verdreh-Befund vom 29.06.2026
# (Vorlieben-Richtung-Regel) und bleibt deshalb konkret statt abstrakt.
_RICHTUNGS_BEISPIEL = {
    ("frau", "mann"): ' (z.B. sagt er, er spritzt in dich, dann ist das so – mach kein Strapon-/Umkehr-Szenario daraus)',
    ("mann", "frau"): ' (z.B. sagt sie, du spritzt in sie, dann ist das so – mach kein Umkehr-Szenario daraus)',
    ("frau", "frau"): '',
    ("mann", "mann"): ' (z.B. sagt er, wer in wen spritzt, dann gilt genau das – dreh die Rollen nicht um)',
}


def grundierung() -> str:
    """Der komplette GRUNDIERUNG-Block: Anatomie-Kombi (Code, geschlechts-
    generiert) + kombi-unabhängige Verstehens-Regeln aus dem überschreibbaren
    Template grundierung_zusatz (Veröffentlichungs-Schritt 3)."""
    from bot.prompts import persona_presets
    kombi = (dom_geschlecht(), sub_geschlecht())
    zusatz = ersetze_platzhalter(
        persona_presets.template("grundierung_zusatz"),
        {"{richtungs_beispiel}": _RICHTUNGS_BEISPIEL[kombi]},
    )
    return (
        "\n\nGRUNDIERUNG (immer beachten, sonst wirkt es unecht):\n"
        f"{_ANATOMIE[kombi]}\n{zusatz}"
    )
