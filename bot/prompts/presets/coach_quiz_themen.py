"""Kuratierter Themenkatalog für das Coach-Wissensquiz (Domina-Seite).

Grok darf Fragen und Auflösungen NUR aus diesen Fakten formulieren — kein
freies Halbwissen, gerade bei Sicherheitsthemen. `kategorien` koppelt ein
Thema an den Aufgaben-Katalog (config.AUFGABEN_KATEGORIEN): Themen mit
Kategorien erscheinen nur, wenn die Vorlieben des Paares dort andocken,
und nie, wenn eine der Kategorien in den Limits liegt. Themen ohne
Kategorien sind Basiswissen und immer im Pool.
"""

# Jedes Thema: name (eindeutig, dient als Anti-Wiederholungs-Schlüssel),
# kategorien (leer = Basiswissen), fakten (Stichpunkte als Faktenanker).
THEMEN: list[dict] = [
    # --- Basiswissen: Sicherheit, Kommunikation, Psychologie -----------------
    {
        "name": "Safewords und nonverbale Alternativen",
        "kategorien": [],
        "fakten": [
            "Ampelsystem: Grün = weiter, Gelb = langsamer/prüfen, Rot = sofortiger Stopp ohne Diskussion",
            "Gelb ist kein Scheitern, sondern Steuerung – wer Gelb ernst nimmt, bekommt ehrlicheres Feedback",
            "Ist der Mund unbenutzbar (Knebel, Position), braucht es ein nonverbales Safeword: Gegenstand fallen lassen, dreimal klopfen/summen",
            "Nach jedem Rot: Szene beenden, erst versorgen, erst später auswerten",
        ],
    },
    {
        "name": "Sub-Drop und Top-Drop",
        "kategorien": [],
        "fakten": [
            "Nach intensiven Szenen fällt der Endorphin-/Adrenalinspiegel ab – das kann ein Stimmungstief auslösen (Drop)",
            "Ein Drop kann auch verzögert kommen, oft erst 24–72 Stunden später – darum lohnt ein Check-in am Folgetag",
            "Auch die dominante Seite kann droppen (Top-Drop): Zweifel, Schuldgefühle, Erschöpfung – Aftercare gilt für beide",
            "Gegenmittel: Wärme, Wasser, etwas Zuckerhaltiges, Körperkontakt, ausdrückliche Rückversicherung",
        ],
    },
    {
        "name": "Aftercare planen statt improvisieren",
        "kategorien": [],
        "fakten": [
            "Aftercare gehört VOR der Szene verhandelt: Was braucht jede Seite danach (Nähe, Ruhe, Essen, Alleinsein)?",
            "Körperlich: zudecken (Temperatur fällt nach Anspannung), Wasser, Druckstellen und Haut kontrollieren",
            "Emotional: Lob und Einordnung ('das war Spiel, du bist sicher'), keine Kritik direkt nach der Szene",
            "Die Auswertung der Szene (was war gut/zu viel) hat einen eigenen Termin verdient – nicht in der ersten Stunde danach",
        ],
    },
    {
        "name": "Verhandlung und Konsens-Modelle",
        "kategorien": [],
        "fakten": [
            "SSC (safe, sane, consensual) und RACK (risk-aware consensual kink) sind die gängigen Rahmen – RACK betont, dass Restrisiko bewusst getragen wird",
            "Vor neuen Spielarten: Limits, Safeword, Tabuwörter und Nachsorge explizit klären – nicht mitten in der Szene",
            "Konsens ist widerruflich: ein früheres Ja gilt nicht automatisch für heute",
            "Meta-Gespräche über die Dynamik gehören AUSSERHALB der Rollen geführt, auf Augenhöhe",
        ],
    },
    {
        "name": "Szenen-Aufbau und Erregungskurve",
        "kategorien": [],
        "fakten": [
            "Eine Szene trägt weiter, wenn sie einen Bogen hat: Ankommen/Einstimmen, Steigerung, Höhepunkt, Abkühlen",
            "Anfangsrituale (Anrede, Haltung, ein fester Satz) schalten beide Seiten zuverlässig in die Dynamik",
            "Abwechslung schlägt Härte: Tempo, Intensität und Reizart zu variieren wirkt stärker als immer mehr Druck",
            "Ein bewusster Abschluss (Ritual, Lob) verhindert, dass die Szene 'ausfranst' und die Rollen verschwimmen",
        ],
    },
    {
        "name": "Beobachten während der Szene",
        "kategorien": [],
        "fakten": [
            "Atmung, Hautfarbe, Muskeltonus und Antwortfähigkeit sind die wichtigsten Live-Signale des Subs",
            "Wortkarge Einsilbigkeit oder 'weggetretener' Blick können Subspace sein – dann keine neuen Verhandlungen mehr, der Sub kann nicht mehr frei zustimmen",
            "Im Subspace sinkt das Schmerzempfinden – 'er sagt nichts' ist KEIN Freibrief für mehr Intensität",
            "Regelmäßige kurze Check-ins ('Farbe?') kosten keine Stimmung, sie zeigen Kontrolle",
        ],
    },
    {
        "name": "Grenzen pflegen wie einen Vertrag",
        "kategorien": [],
        "fakten": [
            "Hard Limits sind nicht verhandelbar und werden nie 'getestet' – auch nicht im Spiel angedeutet",
            "Soft Limits dürfen nur nüchtern und außerhalb der Szene neu verhandelt werden",
            "Limits ändern sich mit Erfahrung und Lebensphase – ein fester Termin (z.B. monatlich) zum Abgleich hält beide ehrlich",
            "Wer eine Grenze aus Versehen touchiert: sofort benennen, nicht überspielen – das baut Vertrauen statt es zu kosten",
        ],
    },
    {
        "name": "Belohnung und Strafe dosieren",
        "kategorien": [],
        "fakten": [
            "Belohnung wirkt stärker als Strafe: gezieltes Lob formt Verhalten nachhaltiger als Sanktionen",
            "Strafen brauchen einen klaren Anlass, ein angekündigtes Maß und ein definiertes Ende – Willkür zerstört Vertrauen",
            "Niemals aus echtem Ärger strafen: erst abkühlen, dann in der Rolle handeln",
            "Nach der Strafe ist die Sache erledigt – Nachtragen vergiftet die Dynamik",
        ],
    },

    # --- Vorlieben-gebundene Themen ------------------------------------------
    {
        "name": "Anal-Basics: Gleitgel und Geduld",
        "kategorien": ["Anal", "Analdehnung", "Analeingangstraining", "Dildo_Training", "Buttplug_Tragen"],
        "fakten": [
            "Die Analschleimhaut produziert keine eigene Feuchtigkeit und ist verletzlich – ohne reichlich Gleitgel geht nichts",
            "Silikongleitgel hält länger, gehört aber NICHT auf Silikonspielzeug (Oberfläche wird angegriffen) – dort Wasserbasis",
            "Spielzeug braucht eine ausgestellte Basis (Flare), sonst kann es vollständig hineinrutschen – dann hilft nur die Notaufnahme",
            "Dehnung ist ein Wochen-Projekt: Schmerz ist ein Warnsignal, betäubende Cremes sind deshalb tabu",
            "Nach Anal nie ohne Reinigung vaginal oder oral weiterspielen (Keimverschleppung)",
        ],
    },
    {
        "name": "Buttplug-Tragezeit",
        "kategorien": ["Buttplug_Tragen"],
        "fakten": [
            "Tragezeit langsam steigern: mit 15–30 Minuten beginnen, nicht mit Stunden",
            "Dauerdruck auf dieselbe Stelle kann die Schleimhaut reizen – bei Brennen oder Taubheit sofort raus",
            "Für längeres Tragen: weiches Material (Silikon), schlanker Steg, und vorher wie nachher Gleitgel",
            "Alltag mit Plug (Sitzen, Autofahren) verändert den Druck stark – erste Versuche zuhause, nicht unterwegs",
        ],
    },
    {
        "name": "Pegging: Winkel und Führung",
        "kategorien": ["Pegging", "Strap_on", "Prostatamassage"],
        "fakten": [
            "Die Prostata liegt wenige Zentimeter hinter dem Eingang Richtung Bauchdecke – flacher Winkel, kein tiefes Stoßen nötig",
            "Die empfangende Seite steuert am Anfang das Tempo (sich selbst 'aufnehmen' lassen statt gestoßen werden)",
            "Ein Harness muss fest sitzen – ein wackelnder Dildo macht Winkelkontrolle unmöglich",
            "Kommunikation im Takt: 'tiefer/langsamer/halten' vorher als kurze Kommandos vereinbaren",
        ],
    },
    {
        "name": "Impact: Zonen und Aufwärmen",
        "kategorien": ["Spanking", "Impact", "Paddle_Training", "Peitsche"],
        "fakten": [
            "Sichere Zonen: Gesäß und äußere Oberschenkel-Rückseiten – dort sitzen Muskel- und Fettpolster",
            "Tabuzonen: Nieren (unterer Rücken), Wirbelsäule, Nacken, Gelenke, Kniekehlen",
            "Aufwärmen von leicht nach fest lässt Endorphine mitkommen – kalte harte Schläge reißen aus der Szene",
            "Flächige Werkzeuge (Hand, Paddle) wirken dumpf und 'warm', schmale (Rohrstock, Peitsche) stechend – Wirkung vorher am eigenen Unterarm testen",
            "Danach Haut kontrollieren; Blutergüsse kühlen, am Folgetag Arnika",
        ],
    },
    {
        "name": "Fesselung ohne Nervenschaden",
        "kategorien": ["Klassische_Fesselspiele"],
        "fakten": [
            "Zwei-Finger-Regel: Unter jede Fessel müssen zwei Finger passen – Abschnüren verhindert",
            "Der Radialisnerv verläuft außen am Oberarm: Kribbeln oder Taubheit in Daumen/Hand = sofort lösen",
            "Gefesselte nie allein lassen, eine Rettungsschere (EMT-Schere) liegt griffbereit",
            "Nie am Hals, nie an Gelenken zug-belastet fixieren; Positionen spätestens alle 20–30 Minuten prüfen",
        ],
    },
    {
        "name": "Enema sicher gestalten",
        "kategorien": ["Enema_Play"],
        "fakten": [
            "Nur körperwarmes klares Wasser (~37 °C) – zu heiß verbrüht die Schleimhaut, zu kalt verursacht Krämpfe",
            "Einsteigermengen sind klein (100–300 ml Klistier); Menge und Haltezeit langsam steigern",
            "Keine Zusätze wie Seife oder Alkohol – sie reizen und schädigen die Darmflora",
            "Nicht zu häufig: übermäßiges Spülen stört Elektrolyt- und Darmhaushalt",
            "Langsamer Einlauf mit Pausen; Krämpfe sind das Signal zum Anhalten, nicht zum Durchziehen",
        ],
    },
    {
        "name": "Natursekt: Hygiene-Grundlagen",
        "kategorien": ["Piss_Play", "Toiletten_Sklave"],
        "fakten": [
            "Urin gesunder Menschen ist keimarm, aber nicht steril – bei Blasenentzündung oder Infekten ist Pause",
            "Viel trinken verdünnt Geschmack und Geruch; Mittelstrahl ist die sauberste Fraktion",
            "Augenkontakt vermeiden (Bindehaut ist empfindlich für Keime)",
            "Medikamente und manche Lebensmittel gehen in den Urin über – relevant, wenn geschluckt wird",
        ],
    },
    {
        "name": "Orgasmuskontrolle und Denial",
        "kategorien": ["Orgasmusverweigerung", "Ruiniertes_Orgasmen"],
        "fakten": [
            "Denial wirkt über Erwartung: angekündigte Regeln und Fristen sind der eigentliche Hebel, nicht bloßes Verbieten",
            "Ein ruinierter Orgasmus (Stimulation im Point of no Return stoppen) entlädt körperlich, verweigert aber die Belohnung – starkes Werkzeug, sparsam einsetzen",
            "Bei Keuschhaltung mit Käfig: Taubheit, kalte Haut oder Verfärbung = sofort abnehmen; tägliche Hygiene ist Pflicht",
            "Lange Denial-Phasen brauchen Ventil-Check-ins außerhalb der Rolle – Frust darf gesagt werden dürfen",
        ],
    },
    {
        "name": "Psychospiele verantwortungsvoll",
        "kategorien": ["Psycho", "Sissy_Training", "Feminisierung", "Demütigung",
                        "Erniedrigung", "Verbale_Demütigung", "Objektifizierung"],
        "fakten": [
            "Erniedrigung wirkt nur im vereinbarten Rahmen: Welche Wörter erlaubt sind und welche nie, wird vorher festgelegt",
            "Echte wunde Punkte (Aussehen, Beruf, Familie, alte Verletzungen) sind tabu, wenn sie nicht ausdrücklich freigegeben wurden",
            "Nach intensiven Kopf-Szenen ist Aftercare wichtiger als nach körperlichen: die Einordnung 'das war Spiel' muss ausdrücklich kommen",
            "Bleed beachten: Rollen-Sätze sickern in den Alltag – regelmäßig prüfen, ob sich beide außerhalb der Szene noch auf Augenhöhe begegnen",
        ],
    },
    {
        "name": "Chastity-Alltag",
        "kategorien": ["Orgasmusverweigerung"],
        "fakten": [
            "Passform ist alles: Der Ring darf nicht einschnüren – nächtliche Erektionen erzeugen den höchsten Druck",
            "Tägliche Reinigung (Duschen mit Käfig, Wattestäbchen) verhindert Geruch und Hautreizung",
            "Ein Notfall-Zugang (Zweitschlüssel, bekannter Ablageort) ist Sicherheits-Grundausstattung, kein Stilbruch",
            "Einstieg mit Stunden bis einzelnen Tagen, nicht mit Wochen – Haut und Kopf brauchen Gewöhnung",
        ],
    },
]


def themen_namen() -> set[str]:
    return {t["name"] for t in THEMEN}
