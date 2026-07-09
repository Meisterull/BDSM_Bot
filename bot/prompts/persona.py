"""
Persona der Herrin (Sklaven-Sicht).

Wird in allen Prompts referenziert, die Sklaven-sichtbaren Output erzeugen:
followup-Fragen, Befehle, Reaktionen auf Gefühle. Konsistente Stimme
über alle Pfade hinweg.
"""

# Kern-Stilbeschreibung kommt aus dem aktiven Preset (persona_presets.py, gewählt
# via /einstellungen). Bewusst keine wörtlichen Beispiel-Phrasen in den Presets,
# weil der LLM diese sonst kopiert.


def fuer_sklaven_prompt() -> str:
    """Block der in jeden Sub-seitigen Prompt eingebettet wird. Name der
    dominanten Rolle und Anrede des Subs kommen (optional) aus persona_config;
    Labels/Pronomen/Anatomie-Grundierung liefert rollen.py (Rollen-Konstellation
    konfigurierbar, Default Herrin/Sklave = Bestandsverhalten)."""
    from bot.services import persona_config
    from bot.prompts import rollen
    name = persona_config.bot_name()
    anrede = persona_config.sklave_anrede()
    d, s = rollen.dom(), rollen.sub()

    if name:
        poss_name = "Ihr" if d["nom"] == "sie" else "Sein"
        identitaet = (f'- {poss_name} Name ist {name}. {d["nom"].capitalize()} darf sich so nennen oder so '
                      f'unterzeichnen, wenn es natürlich passt – muss aber nicht ständig.')
    else:
        identitaet = (f'- Bleibt namenlos – {d["nom"]} ist "{d["anrede"]}", '
                      f'nie mit Eigenname unterzeichnet.')

    anrede_zeile = ""
    if anrede:
        # "ausschließlich … auch wenn ältere Nachrichten": die Anrede ist änderbar
        # (/sklavenname); ohne diese Härtung kopiert das Modell die alte Anrede
        # aus dem Gesprächsverlauf weiter (Test-Befund F6).
        anrede_zeile = (
            f'\n- {d["nom"].capitalize()} spricht {s["akk"]} an als "{anrede}" – wenn eine Anrede fällt, dann '
            f'AUSSCHLIESSLICH diese (auch wenn {s["nom"]} in älteren Nachrichten im Verlauf anders genannt '
            f'wurde). Aber SPARSAM: höchstens einmal pro Antwort und nicht in jeder Antwort – eine Anrede '
            f'wirkt durch Seltenheit; in jeder Antwort wird sie zur Floskel. Meist reicht das "du".'
        )

    grundierung = rollen.grundierung()

    setup = persona_config.setup_kontext()
    setup_block = f"\n\nSETUP/KONTEXT (so ist es bei euch wirklich – halte dich daran):\n{setup}" if setup else ""

    # Sprach-Anweisung (zentraler i18n-Hebel: deckt alle Sklaven-seitigen Prompts ab)
    sprache = persona_config.sprache()
    sprache_block = f"\n\nSPRACHE: Antworte ausschließlich auf {sprache}." if sprache else ""

    from bot.prompts import persona_presets
    preset = persona_presets.aktuelles_preset()
    return f"{preset['stil_kopf']}\n{identitaet}{anrede_zeile}{grundierung}{setup_block}{sprache_block}\n\n{preset['stil_fuss']}"
