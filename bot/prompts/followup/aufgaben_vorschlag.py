"""
Aufgaben-Vorschlag: Kontext-Rendering, Formel-Verbot, Machbarkeits-Prüfung, Tiny-/ausführlicher Vorschlag.

Herausgelöst aus bot/prompts/followup.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.prompts.followup.
"""
import re
from bot import config
from bot.prompts import rollen
from bot.prompts.followup import bausteine



def _aufgaben_kontext(
    erfahrungsstand: str,
    level: int,
    interessen: list,
    sklave_vorlieben: list,
    sklave_hard_limits: list,
    sklave_dislike_kategorien: list = None,
    letzte_aufgaben: list = None,
    letzte_tiny_tasks: list = None,
    verbrauchte_anfaenge: list = None,
    verbrauchte_abschluesse: list = None,
    mehrstufig_bremse: bool = False,
    ausfuehrlich: bool = False,
    letzte_inspirationen: list = None,
    gewaehlte_kategorien: list = None,
    cross_kategorie: str = None,
    sklave_wunsch_kategorien: list = None,
    abgelehnte_tiny_tasks: list = None,
    conversation_context: str = "",
    stimmung: str = "",
    bewertungs_kontext: str = "",
    vertrauens_kontext: str = "",
    schwierigkeit: str = "normal",
    kategorie_level_hinweis: str = "",
    dossier: str = "",
    offene_faeden: list = None,
    kategorie_reaktionen: dict = None,
    domina_kategorie_praeferenzen: dict = None,
    verbrauchte_zutaten: list = None,
    kombi_vorlieben: list = None,
    inventar_impuls: str = None,
) -> str:
    """Gemeinsamer Kontext-Block für tiny_task_vorschlag und
    ausfuehrlicher_task_vorschlag – Profil-Daten + alle Hinweis-Bausteine.
    Die beiden Funktionen unterscheiden sich nur noch in Kopf und Output-Spezifikation."""
    s, d = rollen.sub(), rollen.dom()
    # Kurzlabels, keine Volltexte: 12 komplette Vorschlags-Nachrichten unter
    # "NICHT wiederholen" ankern das Modell auf die eigene Formel (Review D7, B1).
    # Die Labels kommen aus qdrant.get_recent_tiny_tasks/get_recent_inspirationen.
    nicht_wiederholen_str = ""
    alle_vorherigen = list(letzte_tiny_tasks or []) + list(letzte_inspirationen or [])
    if alle_vorherigen:
        ideen_liste = "\n".join(f"- {idea}" for idea in alle_vorherigen)
        nicht_wiederholen_str = (
            f"\nBereits vorgeschlagene Aufgaben-Themen (NICHT wiederholen, auch keine Variationen):\n"
            f"{ideen_liste}\n"
        )
    # Opener-/Struktur-Sperrliste (Live-Befund 16.07.): Kurzlabels dedupen nur den
    # Inhalt – Einstiegssatz und Aufbau wiederholten sich trotzdem ("Da du heute
    # mehr Zeit hast, lass uns …" 2× fast wortgleich, dreimal in Folge ein
    # Phasen/Stufen-Programm). Gleiche Detektor+Sperrlisten-Mechanik wie im
    # Sklave-Chat: konkrete verbrauchte Anfänge schlagen die generische Regel.
    anfaenge_str = ""
    if verbrauchte_anfaenge:
        anfaenge_str = (
            "\nVERBRAUCHTE ANFÄNGE (so begannen deine letzten Vorschläge – beginne heute "
            "STRUKTURELL anders, nicht mit demselben Einstieg oder Satzmuster):\n"
            + "\n".join(f"  • {a}" for a in verbrauchte_anfaenge) + "\n"
        )
    # D9/DIV3: Abschluss-Sätze recyceln genauso wie Opener – „Wie lange willst
    # du …?" beendete 4 von 5 Folgetags-Vorschlägen, stand aber in keiner Liste.
    if verbrauchte_abschluesse:
        anfaenge_str += (
            "\nVERBRAUCHTE ABSCHLÜSSE (so endeten deine letzten Vorschläge – beende heute "
            "ANDERS, keine Variation dieser Schluss-Sätze oder ihres Frage-Musters):\n"
            + "\n".join(f"  • {a}" for a in verbrauchte_abschluesse) + "\n"
        )
    if mehrstufig_bremse:
        if ausfuehrlich:
            # D9/A1: der Wochenend-Auftrag erlaubt explizit 2-3 Phasen – ein
            # hartes "KEIN Stufen-Format" wären zwei Absolutanweisungen
            # gegeneinander. Abschwächen statt verbieten.
            anfaenge_str += (
                "\nSTRUKTUR-BREMSE: Deine letzten Vorschläge waren mehrstufige "
                "Phasen/Stufen-Programme. Wenn du heute Phasen nutzt, baue sie "
                "ANDERS auf – kein 'Phase 1/2/3'-Nummernschema, andere Dramaturgie "
                "als zuletzt (z. B. ein durchgehendes Szenario mit Wendepunkt).\n"
            )
        else:
            anfaenge_str += (
                "\nSTRUKTUR-BREMSE: Deine letzten Vorschläge waren mehrstufige "
                "Phasen/Stufen-Programme. Heute KEIN nummeriertes Stufen-Format – "
                "formuliere den Vorschlag als EINE zusammenhängende Idee.\n"
            )
    # Positiv formuliert + auf Kurzlabels eingedampft: vollständige Aufgabentexte
    # prominent als "VERBOTEN" zu listen ankert das Modell genau auf diese Themen.
    abwechslung_str = ""
    if letzte_aufgaben:
        # [:95] statt [:60]: die Einträge tragen jetzt einen "vor N Tagen:"-Präfix,
        # der sonst vom Kurzlabel-Cut abgeht.
        labels = "\n".join(f"  • {a[:95]}" for a in letzte_aufgaben)
        abwechslung_str = (
            f"\nABWECHSLUNG (strikt): Die letzten Aufgaben (mit Zeitabstand) drehten sich um:\n"
            f"{labels}\n"
            f"Wähle für heute ein klar ANDERES Thema/Feld – nicht nur eine andere "
            f"Handlung im selben Bereich. Lagen die letzten Vorschläge alle im "
            f"selben Cluster, geh heute bewusst in ein anderes "
            f"(Oral, Impact, Anbetung, Demütigung, Dienst, Orgasmus-Kontrolle …). "
            f"Keine Variation oder Umformulierung der genannten.\n"
            f"ZEITBEZÜGE: Zeitwörter IN den Aufgabentexten oben ('heute Abend' …) "
            f"beziehen sich auf deren damaligen Tag, nicht auf heute. Erfinde in "
            f"deiner Nachricht KEINE eigenen Zeitbezüge ('gestern', 'die letzten "
            f"Tage') – wenn du dich auf Früheres beziehst, nutze die angegebenen "
            f"Zeitabstände. Die Abgrenzung von früheren Aufgaben ist deine interne "
            f"Auswahl-Logik: erwähne in der Nachricht NICHT, wovon du dich absetzt "
            f"oder was es heute alles nicht gibt.\n"
        )
    # Zutaten-Sperrliste (Live-Befund 06.09.): die Label-/Kategorien-Sperren oben
    # dedupen nur das HAUPTTHEMA – dieselben zwei, drei Beiwerk-Praktiken
    # garnierten trotzdem fast jeden der gemessenen Vorschläge.
    zutaten_str = ""
    if verbrauchte_zutaten:
        zutaten_str = (
            "\nVERBRAUCHTE ZUTATEN (kamen in den letzten Vorschlägen schon vor – heute "
            "KOMPLETT weglassen, auch nicht als Beiwerk, Übergang oder Abschluss-Geste):\n"
            + "\n".join(f"  • {z}" for z in verbrauchte_zutaten)
            + "\nDie ABWECHSLUNG oben deckt nur das Hauptthema – diese Liste gilt für "
            "JEDES Element der Szene.\n"
        )
    kategorie_str = ""
    if gewaehlte_kategorien:
        # Cross-Cluster-Slot markieren: ohne die Markierung kann das Modell die
        # ABWECHSLUNGs-Anweisung formal erfüllen und trotzdem bequem wieder ins
        # zuletzt bediente Cluster greifen (Review D7, B3).
        zeilen = []
        for k in gewaehlte_kategorien:
            if cross_kategorie and k == cross_kategorie:
                zeilen.append(f"  • {k} ← frisches Thema, heute BEVORZUGEN")
            else:
                zeilen.append(f"  • {k}")
        kat_liste = "\n".join(zeilen)
        cross_hinweis = ""
        if cross_kategorie and cross_kategorie in gewaehlte_kategorien:
            cross_hinweis = (
                f"Die markierte Kategorie liegt bewusst außerhalb der zuletzt bedienten Themen – "
                f"nimm sie, wenn du schwankst; die anderen nur, wenn sie die ABWECHSLUNG oben wirklich erfüllen.\n"
            )
        # Bei aktivem Kombi-Impuls darf die Kategorien-Pflicht nicht als zweite
        # Absolutanweisung dagegenstehen (gleiche Lektion wie D7/B4).
        if kombi_vorlieben:
            pflicht_zeile = (
                "Heute hat der KOMBI-IMPULS unten Vorrang – diese Kategorien sind nur "
                "Inspiration. Nur falls die Kombi nicht umsetzbar ist, MUSS der Vorschlag "
                "aus einer dieser Kategorien stammen.\n"
            )
        elif inventar_impuls:
            # Gleiche Lektion wie beim Kombi-Impuls: Pflicht-Kategorie UND Pflicht-
            # Gegenstand wären zwei Absolutanweisungen gegeneinander.
            pflicht_zeile = (
                "Heute hat der AUSSTATTUNGS-IMPULS unten Vorrang – diese Kategorien sind "
                "nur Inspiration. Nur falls der Gegenstand nicht stimmig einzubauen ist, MUSS "
                "der Vorschlag aus einer dieser Kategorien stammen.\n"
            )
        else:
            pflicht_zeile = "Der Vorschlag MUSS aus mindestens einer dieser Kategorien stammen.\n"
        kategorie_str = (
            f"\nKATEGORIEN FÜR HEUTE (wähle eine oder kombiniere zwei davon – zwei nur, wenn sie stimmig in EINE machbare Szene passen):\n"
            f"{kat_liste}\n"
            f"{pflicht_zeile}"
            f"{cross_hinweis}"
        )
    kombi_str = ""
    if kombi_vorlieben and len(kombi_vorlieben) >= 2:
        kombi_str = (
            f"\nKOMBI-IMPULS (heute): Verwebe diese beiden Vorlieben zu EINER Szene, "
            f"die es in den letzten Vorschlägen so nicht gab:\n"
            f"  1. {kombi_vorlieben[0]}\n"
            f"  2. {kombi_vorlieben[1]}\n"
            f"Beide behalten EXAKT ihre Richtung, Rollen und Bedingungen. Nennt eine der "
            f"beiden eine Alltags-Situation (Morgen-/Abendroutine, gemeinsame Rituale …), "
            f"nutze sie als Bühne der Szene. Verrate nicht, dass du kombinierst oder dass beides von "
            f"{s['poss']}er Vorlieben-Liste kommt – es soll wie dein spontaner Einfall wirken.\n"
        )
    from bot.prompts import coach_persona
    inventar_impuls_str = coach_persona.inventar_impuls_block(inventar_impuls) if inventar_impuls else ""
    # Ausstattung (Wissen + Verbot) gehört zu den Profil-Fakten unten – gleicher
    # Baustein wie in sklaven_kontext_block, damit alle Generatoren dasselbe wissen.
    inventar_str = coach_persona.inventar_block(perspektive="coach")
    if inventar_str:
        inventar_str = "\n" + inventar_str + "\n"
    wunsch_str = ""
    if sklave_wunsch_kategorien:
        # Widerspruchs-Abgleich (D9/DIV6): steht dieselbe Kategorie zugleich in
        # der „weniger gefielen (1-2★)"-Zeile des Bewertungs-Kontexts, wird das
        # im Prompt explizit versöhnt – beide Signale unkommentiert nebeneinander
        # („Lieblings-Kategorie Buttplug_Tragen" + „weniger gefielen:
        # Buttplug_Tragen") verwirrten das Modell (live gerendert 15.08.).
        schwach: set = set()
        for zeile in (bewertungs_kontext or "").splitlines():
            if "weniger gefielen" in zeile and ":" in zeile:
                schwach = {k.strip() for k in zeile.split(":", 1)[1].split(",") if k.strip()}

        def _lieblings_zeile(k: str) -> str:
            if k in schwach:
                return f"💚 {k} (zuletzt aber schwach bewertet – nur mit wirklich frischem Dreh)"
            return f"💚 {k}"

        wunsch_str = (
            f"\nLieblings-Kategorien {s['label_gen']} (HINWEIS, KEINE PFLICHT):\n"
            + "\n".join(_lieblings_zeile(k) for k in sklave_wunsch_kategorien)
            + "\nFalls eine der Pflicht-Kategorien für heute mit einer Lieblings-Kategorie "
            f"übereinstimmt, greif bevorzugt zu ihr – aber als dein eigener Einfall, ohne "
            f"die Vorliebe in der Nachricht als Begründung zu nennen.\n"
        )
    rejected_str = ""
    if abgelehnte_tiny_tasks:
        eintraege = "\n".join(
            f"  • '{t['inhalt'][:80]}' – Grund: {t['grund'][:120]}"
            for t in abgelehnte_tiny_tasks
        )
        rejected_str = (
            f"\nLERNEN AUS ABGELEHNTEN VORSCHLÄGEN ({d['real']} hat diese nicht übernommen):\n"
            f"{eintraege}\n"
            f"→ Vermeide ähnliche Muster. Was {'die' if d['nom'] == 'sie' else 'der'} {d['real']} ablehnt soll nicht wiederholt werden.\n"
        )
    kontext_str = ""
    if conversation_context:
        kontext_str = f"\nAktueller Kontext aus vergangenen Gesprächen:\n{conversation_context}\n→ Passe den Vorschlag an diesen Kontext an.\n"
    stimmung_str = ""
    if stimmung:
        # Stimmung ist 1:1 gespeicherter Sklaven-Freitext → mit Delimiter einbetten
        stimmung_str = (
            f"\n{bausteine.nutzer_text('Aktuelle Stimmung ' + s['label_gen'], stimmung)}\n"
            f"→ Bei schlechter Stimmung eher sanftere, aufbauende Aufgaben vorschlagen. "
            f"Bei guter Stimmung darf es anspruchsvoller sein.\n"
        )
    bewertung_str = f"\n{bewertungs_kontext}" if bewertungs_kontext else ""
    vertrauens_str = f"\n{vertrauens_kontext}" if vertrauens_kontext else ""
    from bot.prompts import coach_persona
    schwierigkeit_str = "\n" + coach_persona.schwierigkeit_zeile(schwierigkeit) + "\n"
    if ausfuehrlich:
        # D9/A1: "niedrig → kurze Aufgaben" widersprach dem Wochenend-Auftrag
        # "anspruchsvoll, mehrschichtig" im selben Prompt (live gerendert 15.08.).
        schwierigkeit_str = (
            "\n" + coach_persona.schwierigkeit_zeile(schwierigkeit)
            + " – gilt je Schritt: der Wochenend-Vorschlag darf trotzdem "
            "mehrschichtig und insgesamt länger sein, solange jeder einzelne "
            "Schritt dieser Komplexität entspricht.\n"
        )
    kat_level_str = ""
    if kategorie_level_hinweis:
        kat_level_str = (
            f"\n{kategorie_level_hinweis}\n"
            f"→ Richte die Intensität der Aufgabe nach dem Level der gewählten Kategorie "
            f"(niedrig = sanft/herantastend, hoch = intensiv/fordernd).\n"
        )
    # Konflikt-Auflösung (Review D7, B4): steht eine Kategorie zugleich in
    # "Domina gut gefielen (4-5★)" UND in den Sklaven-Dislikes, wären das zwei
    # sich widersprechende Absolutanweisungen im selben Prompt. Stattdessen:
    # EIN Spannungs-Hinweis, die Kategorie fliegt aus der NIEMALS-Liste.
    dislikes = [k for k in (sklave_dislike_kategorien or []) if k]
    hoch_zeile = next(
        (z for z in (bewertungs_kontext or "").splitlines() if "gut gefielen" in z), ""
    )
    konflikt = [
        k for k in dislikes
        if re.search(rf"(?<![\wäöüßÄÖÜ]){re.escape(k)}(?![\wäöüßÄÖÜ])", hoch_zeile)
    ]
    spannungs_str = ""
    if konflikt:
        # Bei gleichem Geschlecht kollidieren die Pronomen ("gefielen der Domina,
        # SIE lehnt ab") – dann das Label statt des Pronomens betonen.
        sub_betont = s["nom"].upper() if s["nom"] != d["nom"] else s["label_nom"].upper()
        spannungs_str = (
            f"\nSPANNUNGSFELD (bewusst einsetzen): {', '.join(konflikt)} – solche Aufgaben "
            f"gefielen {d['real_dat']} (4-5★), {sub_betont} lehnt sie wiederholt ab. Nicht als Belohnung "
            f"oder Standard-Vorschlag verwenden, aber als Strafe oder bewusste Forderung "
            f"einsetzbar, wenn es dramaturgisch passt.\n"
        )
    dislike_rest = [k for k in dislikes if k not in konflikt]
    dislike_str = ""
    if dislike_rest:
        dislike_str = (
            f"\nKategorien die {s['label_nom']} wiederholt ablehnt (NIEMALS vorschlagen):\n"
            + "\n".join(f"❌ {k}" for k in dislike_rest)
            + "\n"
        )
    # Persönlichkeits-Kontext: der Prompt verlangt Personalisierung („nur für IHN
    # formulierbar“) – dafür braucht der Generator Dossier, Reaktionsmuster und Fäden.
    dossier_str = ""
    if dossier:
        dossier_str = (
            f"\nWas du über {s['label_akk']} weißt (Dossier):\n"
            f"{coach_persona.dossier_gekuerzt(dossier)}\n"
        )
    faeden_str = ""
    if offene_faeden:
        faeden_str = (
            f"\nOffene Fäden aus {s['poss']}en Gesprächen (kann der Vorschlag aufgreifen):\n"
            + "\n".join(f"- {f}" for f in offene_faeden[:5]) + "\n"
        )
    reaktions_muster_str = ""
    if kategorie_reaktionen:
        from bot.services import kategorie_logik
        spitzen = kategorie_logik.reaktions_spitzen({"kategorie_reaktionen": kategorie_reaktionen})
        if spitzen:
            reaktions_muster_str = f"\nKategorie-Reaktionsmuster (was bei {s['dat']} wirkt): {spitzen}\n"
    # Domina-Signal (kategorie_praeferenzen) auch fürs LLM sichtbar – es steuert
    # seit 01.07. die Kategorie-Auswahl, der Prompt erklärte es aber nicht (B7).
    domina_praef_str = ""
    if domina_kategorie_praeferenzen:
        _netto = lambda v: int(v.get("positiv", 0)) - int(v.get("negativ", 0))
        gern = [k for k, v in domina_kategorie_praeferenzen.items() if _netto(v) > 0]
        ungern = [k for k, v in domina_kategorie_praeferenzen.items() if _netto(v) < 0]
        teile = []
        if gern:
            teile.append(f"zuletzt gern übernommen: {', '.join(sorted(gern))}")
        if ungern:
            teile.append(f"eher abgelehnt: {', '.join(sorted(ungern))}")
        if teile:
            # Gleiches Geschlecht → "ihre Vorlieben, nicht ihre" wäre sinnfrei;
            # dann über das Label abgrenzen ("nicht die der Sklavin").
            sub_poss = f"{s['poss']}e"
            abgrenzung = sub_poss if sub_poss != d["poss"] else f"die {s['label_gen']}"
            domina_praef_str = (
                f"\nVorschlags-Feedback {d['real_gen']} ({d['poss']} Vorlieben, nicht {abgrenzung}): "
                + " – ".join(teile) + "\n"
            )
    # Vorlieben je Zeile (nicht komma-verkettet): Klammer-Zusätze mit Richtungs-/
    # Bedingungs-Constraints ("nur bei der Domina, …") werden sonst in der flachen
    # Komma-Liste abgetrennt und vom Modell verdreht.
    if sklave_vorlieben:
        vorlieben_block = "\n" + "\n".join(f"    - {v}" for v in sklave_vorlieben)
    else:
        vorlieben_block = " nicht angegeben"
    return f"""Profil {d['real_gen']}:
  Erfahrungsstand: {erfahrungsstand}
  {coach_persona.level_zeile(level)}
  Interessen: {', '.join(interessen) if interessen else 'nicht angegeben'}
Profil {s['label_gen']}:
  Vorlieben (verdecktes Steuerwissen – als Hebel nutzen, NIE als Liste oder Treffer erwähnen):{vorlieben_block}
  Absolute Grenzen (NIEMALS): {', '.join(sklave_hard_limits) if sklave_hard_limits else 'keine'}
{inventar_str}{dossier_str}{reaktions_muster_str}{domina_praef_str}{faeden_str}{kontext_str}{stimmung_str}{bewertung_str}{vertrauens_str}{schwierigkeit_str}{kat_level_str}{dislike_str}{spannungs_str}{nicht_wiederholen_str}{anfaenge_str}{abwechslung_str}{zutaten_str}{kategorie_str}{kombi_str}{inventar_impuls_str}{wunsch_str}{rejected_str}"""


# Explizites Verbot der eingeschliffenen Vorschlags-Schablone (Review D7, B1):
# die drei Sätze standen 29.06.–01.07. dreimal identisch in den Tiny-Tasks.
# Funktion statt Modul-Konstante: die Rollen-Konstellation ist Laufzeit-Config.
def _formel_verbot() -> str:
    s, d = rollen.sub(), rollen.dom()
    return f"""FORMULIERUNGS-VIELFALT (strikt): Baue die Nachricht anders auf als zuletzt – variiere Einstieg, Aufbau und Schluss.
Diese abgenutzten Schablonen-Sätze sind VERBOTEN (auch leicht abgewandelt):
- Einstieg: "Hey, wie wär's mit …" / "Wie wär's heute mal mit …" / "Wie wär's, wenn du …"
- Begründung: "Das passt (genau) zu {s['dat']}, weil …" / "Das kommt/ist jetzt genau richtig, weil …" / "Passt (perfekt) zu dir/{s['dat']} …" (auch ohne "weil") / "Das holt/zieht/trifft genau {s['poss']}e … (Rolle/Seite/Lust) raus" / "Genau {s['poss']} Ding" / "…, den/die du magst" – jede Treffer-Meldung, die den Task mit {s['poss']}em Profil oder dem Profil {d['real_gen']} abgleicht
- Meta-Kommentar: "ohne dass es wieder … wird/abdriftet" / "ohne dass {s['nom']} sich hinter … verstecken kann" – erwähne NIE, wovon du dich absetzt
- Abschluss: "Klingt das machbar?" / "Wie fühlst du dich bei der Vorstellung …?" / "…, oder?" – keine Rückfrage über den Vorschlag selbst / "Wie lange willst du das laufen/ihn so stehen lassen?" (jede "Wie lange willst du …?"-Variante)
Der Inhalt (Aufgabe + kurze Begründung) bleibt – nur die Formulierung muss frisch sein."""


_PRUEF_ART = {
    "aufgabe": ("einen Aufgaben-Vorschlag", "Vorschlag", ""),
    "wette": ("eine Wett-Idee (Bedingung + was jede Seite bei Sieg bekommt)", "Wett-Idee",
              "Prüfe die Bedingung und jeden Einsatz für sich. Dass ein Einsatz nur in EINEM der "
              "beiden Ausgänge eintritt, ist kein Widerspruch; ob die Wette fair oder messbar ist, "
              "prüfst du nicht.\n"),
    "strafe": ("eine einzelne Strafe (ein Satz im Stil einer Aufgabenliste)", "Strafe", ""),
}


def machbarkeits_pruefung(vorschlag: str, inventar: list = None, art: str = "aufgabe") -> tuple[str, str]:
    """Zweiter Durchlauf nach der Generierung (services/machbarkeit): reine
    Mechanik-Prüfung, kein Geschmacks- oder Moralurteil. Antwort als JSON, damit
    der Aufrufer die Mängel in den Retry-Prompt legen kann. art: aufgabe | wette | strafe."""
    def _koerper(geschlecht: str) -> str:
        return "Frau (kein Penis, kein Sperma)" if geschlecht == "frau" else "Mann (Penis, Sperma)"
    was, label, zusatz = _PRUEF_ART.get(art, _PRUEF_ART["aufgabe"])
    system = (
        f"Du prüfst {was} für ein Paar NUR darauf, ob das Beschriebene körperlich machbar ist "
        "und die Geräte richtig benutzt werden. Kein Urteil über Geschmack, Härte, Ton oder Moral – "
        f"nur Mechanik.\n{zusatz}\n"
        "Prüfpunkte:\n"
        "1. Belegung: Jede Körperstelle und jeder Gegenstand ist zur selben Zeit nur für EINE Sache "
        "belegt (ein Knebel im Mund heißt: kein Lecken, kein Sprechen; ein Gegenstand ist nie an zwei "
        "Stellen zugleich).\n"
        "2. Haltung: Position und Lage passen zu dem, was gleichzeitig passieren soll – und zur "
        "Benutzungsangabe im Inventar (steht dort, wer wo liegt oder sitzt, gilt genau das).\n"
        "3. Reihenfolge: Kein Schritt hebt den vorherigen auf oder setzt etwas voraus, das noch nicht "
        "passiert ist; keine Wiederholung eines Schritts, nur um ein Element unterzubringen.\n"
        "4. Allein/zu zweit: Was eine Person allein tun soll, geht allein.\n"
        f"5. Anatomie: dominante Seite = {_koerper(rollen.dom_geschlecht())}, devote Seite = "
        f"{_koerper(rollen.sub_geschlecht())}. Spielzeug/Strapon ejakuliert nie.\n\n"
        "Melde nur klare Widersprüche – keine Spitzfindigkeiten und nichts, was sich mit gesundem "
        "Menschenverstand von selbst regelt.\n"
        'Antworte NUR als JSON: {"ok": true} oder {"ok": false, "maengel": ["…"]} – höchstens drei '
        "Mängel, je ein kurzer, konkreter Satz (was kollidiert womit). Kein Text außerhalb des JSON."
    )
    if inventar:
        inventar_str = "\n".join(f"- {g}" for g in inventar)
    else:
        inventar_str = "(kein Inventar hinterlegt)"
    user = f"{bausteine.nutzer_text(label, vorschlag)}\n\nInventar (mit Benutzungsangaben):\n{inventar_str}"
    return system, user


def tiny_task_vorschlag(**kwargs) -> tuple[str, str]:
    """Täglicher kleiner Aufgaben-Vorschlag (Werktag). Parameter siehe _aufgaben_kontext."""
    from bot.prompts import coach_persona
    s, d = rollen.sub(), rollen.dom()
    system = f"""Schlag {d['real_dat']} einen kleinen, einfachen "Tiny Task" für {bausteine._sub_mit_poss(d['poss'][:-1], 'akk')} vor – als Coach, der {d['akk']} wie eine vertraute Freundin begleitet.

{coach_persona.fuer_aufgaben_vorschlag()}

Der Vorschlag soll:
- Einfach umsetzbar sein (Tiny: 5-15 Min)
- Zu Level und Komplexität passen
- Eine kurze Begründung dabeihaben (1 Satz), warum das JETZT dran ist – aus Beobachtbarem (wie {s['nom']} zuletzt reagiert hat, Stimmung, was ansteht), nicht "weil Abwechslung wichtig ist" und NIE als Profil-Abgleich ("trifft/holt genau {s['poss']}e Vorliebe/Rolle") – verrate nicht, was auf {s['poss']}er Vorlieben-Liste steht
- Sich von den letzten Vorschlägen klar absetzen, nicht nur Variation

{_formel_verbot()}

Formuliere die Nachricht direkt an {d['real_akk']} (du-Form). Maximal {config.TINY_TASK_WORTLIMIT} Wörter.
KEIN [AUFGABE: ...] Tag – das ist nur ein Vorschlag, keine automatische Aufgabe."""
    return system, _aufgaben_kontext(**kwargs)


def ausfuehrlicher_task_vorschlag(**kwargs) -> tuple[str, str]:
    """Ausführlicher Wochenend-Vorschlag. Parameter siehe _aufgaben_kontext."""
    from bot.prompts import coach_persona
    s, d = rollen.sub(), rollen.dom()
    system = f"""{'Die' if d['nom'] == 'sie' else 'Der'} {d['real']} hat heute mehr Zeit. Schlag {d['dat']} eine anspruchsvollere, mehrschichtige Aufgabe für {bausteine._sub_mit_poss(d['poss'][:-1], 'akk')} vor.

{coach_persona.fuer_aufgaben_vorschlag()}

Der Vorschlag soll:
- Detailliert sein, darf 2-3 Phasen haben
- Zu Level und Komplexität passen
- Eine kurze Begründung dabeihaben (1 Satz), warum das JETZT dran ist – aus Beobachtbarem (wie {s['nom']} zuletzt reagiert hat, Stimmung, was ansteht), nicht "weil Abwechslung wichtig ist" und NIE als Profil-Abgleich ("trifft/holt genau {s['poss']}e Vorliebe/Rolle") – verrate nicht, was auf {s['poss']}er Vorlieben-Liste steht
- Sich von den letzten Vorschlägen klar absetzen

{_formel_verbot()}

Formuliere die Nachricht direkt an {d['real_akk']} (du-Form). Maximal {config.AUSFUEHRLICH_WORTLIMIT} Wörter.
KEIN [AUFGABE: ...] Tag – das ist nur ein Vorschlag, keine automatische Aufgabe."""
    return system, _aufgaben_kontext(ausfuehrlich=True, **kwargs)
