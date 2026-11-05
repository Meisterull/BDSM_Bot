"""
Aufgaben-Kataloge der Nicht-BDSM-Modi (Kink, Vanilla) – Entwurf 05.11.2026.

Pro Modus vier Tabellen, strukturgleich zum BDSM-Bestand in
config.AUFGABEN_KATEGORIEN + kategorie_logik (_KEYWORDS/_CLUSTER/
_KATEGORIE_VORAUSSETZUNG):

  KATEGORIEN      – fester Katalog in Menü-Reihenfolge (Nummern bleiben stabil,
                    also nur hinten anhängen, nie umsortieren)
  KEYWORDS        – Substring-Matching (lowercase, deutsch + englisch in EINER
                    Liste). ACHTUNG Substring-Fallen (siehe kategorie_logik):
                    "eis" steckt in "Reise", "öl" in "Köln", "date" in "Update",
                    "oral" in "Moral" – deshalb längere Formen ("eiswürfel",
                    "massageöl", "date night", "oralsex").
  CLUSTER         – thematische Nachbarschaft (Exploration / Cross-Slot /
                    Themen-Vielfalt), Mehrfachzugehörigkeit erlaubt
  VORAUSSETZUNG   – Kategorie → Prädikat(dom_geschlecht, sub_geschlecht);
                    fehlend = immer erlaubt

Der BDSM-Katalog bleibt bewusst dort, wo er ist (Bestandsverhalten, Public-
Kontrakt); kategorie_logik baut daraus plus diesen Tabellen die Modus-Map.
Die Keywords sind Daten, keine Live-Inhalte – Muster, nicht reale Chats.
"""

# ---------------------------------------------------------------------------
# KINK – asymmetrisch (eine Person führt), ohne harte BDSM-Themen
# ---------------------------------------------------------------------------

KINK_KATEGORIEN = [
    "Sinnesspiel",
    "Fesselspiel_Light",
    "Rollenspiel",
    "Dirty_Talk",
    "Verfuehrung",
    "Striptease",
    "Erotische_Massage",
    "Spielzeug",
    "Orgasmuskontrolle_Light",
    "Spanking_Light",
    "Fotos_Fuereinander",
    "Sexting",
    "Oral",
    "Anal_Light",
    "Dessous_Outfit",
    "Fantasie_Teilen",
    "Verwoehnen",
    "Regeln_Rituale",
    "Date_Geheimnis",
    "Psycho",
]

KINK_KEYWORDS: dict[str, list[str]] = {
    "Sinnesspiel":             ["augenbinde", "sinnesspiel", "alle sinne", "feder", "eiswürfel", "wachs",
                                "blindfold", "sensory", "feather"],
    "Fesselspiel_Light":       ["schal", "handschellen", "fessel", "festhalten", "gefesselt",
                                "handcuff", "tie up", "tied", "restrain"],
    "Rollenspiel":             ["rollenspiel", "szenario", "kostüm", "verkleid", "roleplay", "costume"],
    "Dirty_Talk":              ["dirty talk", "versaut reden", "ins ohr", "flüster", "whisper", "dirty"],
    "Verfuehrung":             ["verführ", "anmach", "reiz", "neck", "seduc", "tease"],
    "Striptease":              ["striptease", "strip", "lapdance", "langsam ausziehen"],
    "Erotische_Massage":       ["massage", "massier", "massageöl", "oil massage"],
    "Spielzeug":               ["vibrator", "spielzeug", "toy", "dildo", "fernbedien", "remote"],
    "Orgasmuskontrolle_Light": ["edging", "hinhalten", "nicht kommen", "erlaubnis", "denial", "orgasm control"],
    "Spanking_Light":          ["klaps", "spanking", "versohl", "spank"],
    "Fotos_Fuereinander":      ["foto", "selfie", "bild schicken", "video", "photo", "picture"],
    "Sexting":                 ["sexting", "sext", "textnachricht", "nachricht schreiben", "voice message"],
    "Oral":                    ["oralsex", "oral ", "blasen", "lecken", "blowjob", "lick", "going down"],
    "Anal_Light":              ["anal", "plug", "hintern", "butt"],
    "Dessous_Outfit":          ["dessous", "outfit", "unterwäsche", "strümpfe", "high heels",
                                "lingerie", "stockings", "underwear"],
    "Fantasie_Teilen":         ["fantasie", "wunsch erzähl", "vorstell", "fantasy", "daydream"],
    "Verwoehnen":              ["verwöhn", "dienst", "service", "bedien", "pamper", "serve", "spoil"],
    "Regeln_Rituale":          ["regel", "ritual", "routine", "jeden abend", "jeden morgen", "rule"],
    "Date_Geheimnis":          ["date night", "verabred", "ausgehen", "restaurant", "unterwegs",
                                "öffentlich", "public", "secret"],
    "Psycho":                  ["fühlen", "nachdenken", "reflektieren", "gedanken", "tagebuch",
                                "reflect", "journal"],
}

KINK_CLUSTER: list[list[str]] = [
    # Sinne / Berührung
    ["Sinnesspiel", "Fesselspiel_Light", "Erotische_Massage", "Spielzeug"],
    # Worte
    ["Dirty_Talk", "Sexting", "Fantasie_Teilen", "Rollenspiel"],
    # Zeigen / Reiz
    ["Striptease", "Dessous_Outfit", "Fotos_Fuereinander", "Verfuehrung"],
    # Führung light
    ["Orgasmuskontrolle_Light", "Spanking_Light", "Regeln_Rituale", "Verwoehnen"],
    # Körper
    ["Oral", "Anal_Light", "Spielzeug"],
    # Draußen / Innen
    ["Date_Geheimnis", "Psycho"],
]

KINK_VORAUSSETZUNG: dict = {}

# ---------------------------------------------------------------------------
# VANILLA – gleichberechtigt, Paar-Intimität ohne Machtgefälle (ab Etappe 2
# wählbar; Katalog hier schon definiert, damit Tests/Struktur stehen)
# ---------------------------------------------------------------------------

VANILLA_KATEGORIEN = [
    "Date_Night",
    "Massage",
    "Naehe_Kuscheln",
    "Gespraech",
    "Komplimente",
    "Ueberraschung",
    "Verfuehrung",
    "Vorspiel",
    "Neue_Stellung",
    "Oral",
    "Spielzeug",
    "Dessous_Outfit",
    "Fantasie_Teilen",
    "Sexting",
    "Gemeinsame_Zeit",
    "Entlastung",
    "Dusche_Bad",
    "Flirten",
    "Fotos_Fuereinander",
    "Psycho",
]

VANILLA_KEYWORDS: dict[str, list[str]] = {
    "Date_Night":         ["date night", "verabred", "ausgehen", "restaurant", "kino", "dinner"],
    "Massage":            ["massage", "massier", "massageöl", "oil massage"],
    "Naehe_Kuscheln":     ["kuschel", "nähe", "umarm", "händchen", "cuddle", "hug", "snuggle"],
    "Gespraech":          ["gespräch", "reden über", "erzähl mir", "frage", "zuhör", "talk about", "listen"],
    "Komplimente":        ["kompliment", "schön find", "liebe an dir", "compliment", "admire"],
    "Ueberraschung":      ["überrasch", "geschenk", "kleinigkeit", "surprise", "gift"],
    "Verfuehrung":        ["verführ", "anmach", "reiz", "neck", "seduc", "tease"],
    "Vorspiel":           ["vorspiel", "langsam", "streichel", "küss", "foreplay", "caress", "kiss"],
    "Neue_Stellung":      ["stellung", "position", "ausprobieren", "neue art", "try something new"],
    "Oral":               ["oralsex", "oral ", "blasen", "lecken", "blowjob", "lick", "going down"],
    "Spielzeug":          ["vibrator", "spielzeug", "toy", "dildo"],
    "Dessous_Outfit":     ["dessous", "outfit", "unterwäsche", "strümpfe", "lingerie", "stockings", "underwear"],
    "Fantasie_Teilen":    ["fantasie", "wunsch erzähl", "vorstell", "fantasy", "daydream"],
    "Sexting":            ["sexting", "sext", "textnachricht", "nachricht schreiben", "voice message"],
    "Gemeinsame_Zeit":    ["gemeinsam", "zu zweit", "ohne handy", "spaziergang", "together", "quality time"],
    "Entlastung":         ["entlast", "abnehmen", "aufräumen", "kochen", "einkaufen", "chore", "cook"],
    "Dusche_Bad":         ["dusche", "duschen", "baden", "badewanne", "shower", "bath"],
    "Flirten":            ["flirt", "zwinker", "anlächeln", "wink"],
    "Fotos_Fuereinander": ["foto", "selfie", "bild schicken", "video", "photo", "picture"],
    "Psycho":             ["fühlen", "nachdenken", "reflektieren", "gedanken", "tagebuch", "reflect", "journal"],
}

VANILLA_CLUSTER: list[list[str]] = [
    # Nähe
    ["Naehe_Kuscheln", "Massage", "Dusche_Bad", "Gemeinsame_Zeit"],
    # Worte
    ["Gespraech", "Komplimente", "Flirten", "Sexting", "Fantasie_Teilen"],
    # Erotik
    ["Vorspiel", "Neue_Stellung", "Oral", "Spielzeug", "Verfuehrung"],
    # Alltag / Aufmerksamkeit
    ["Date_Night", "Ueberraschung", "Entlastung", "Dessous_Outfit", "Fotos_Fuereinander"],
    # Reflexion
    ["Psycho"],
]

VANILLA_VORAUSSETZUNG: dict = {}


def _pruefe(name: str, kategorien, keywords, cluster) -> None:
    fehlend = [k for k in kategorien if k not in keywords]
    fremd = [k for k in keywords if k not in kategorien]
    fremd_cluster = [k for c in cluster for k in c if k not in kategorien]
    if fehlend or fremd or fremd_cluster:
        raise RuntimeError(f"Katalog {name} inkonsistent: ohne Keywords={fehlend}, "
                           f"Keywords ohne Kategorie={fremd}, Cluster-Fremde={fremd_cluster}")
    if len(set(kategorien)) != len(kategorien):
        raise RuntimeError(f"Katalog {name}: doppelte Kategorie")


_pruefe("kink", KINK_KATEGORIEN, KINK_KEYWORDS, KINK_CLUSTER)
_pruefe("vanilla", VANILLA_KATEGORIEN, VANILLA_KEYWORDS, VANILLA_CLUSTER)
