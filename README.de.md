# BDSM Coach Bot

> KI-gestützter Telegram-Bot für Paare in einer einvernehmlichen D/s-Dynamik – ein Coach für die dominante Seite, ein Persona-Chat für die devote Seite und dazwischen eine deterministische Sicherheitsschicht.

🇬🇧 [English version](README.md)

![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB)
![Deploy: docker compose](https://img.shields.io/badge/deploy-docker%20compose-2496ED)
![Self-hosted](https://img.shields.io/badge/data-self--hosted-success)

Der Bot begleitet die **dominante Seite** als Coach (klingt wie eine vertraute Freundin, die das Hobby teilt) und spricht mit der **devoten Seite** in der Persona ihrer Dominanten – in zwei getrennten Telegram-Chats. Er erkennt Aufgaben aus freiem Text, lässt sie bestätigen, stellt sie zu, fragt täglich nach, erfasst Gefühle und Fortschritt, lernt Vorlieben mit der Zeit – und bringt eigene Spiele mit: Würfel, Roulette, Wetten, Storylines, Quizze, Geheimnisse und mehr.

**Warum das statt eines ChatGPT-Tabs?** Weil ein Beziehungswerkzeug Gedächtnis, Initiative und Leitplanken braucht:

- **Deine Daten bleiben deine** – Profile, Gespräche, Gefühle und Fortschritt liegen in deiner eigenen lokalen Qdrant-Instanz. Der einzige externe Aufruf ist die LLM-API für die Textgenerierung.
- **Er ergreift Initiative** – tägliche Nachfragen, Stimmungs-Checks, Wochenplanung, mehrtägige Aufgaben-Serien und spontane Spiel-Impulse laufen über einen Scheduler, statt dass du ans Prompten denken musst.
- **Sicherheit ist Code, kein Bauchgefühl** – die Hard Limits **beider** Partner werden von einem deterministischen Matcher gegen jeden generierten Text geprüft, und ein Safeword pausiert sofort das ganze System – geprüft bei jeder Nachricht, vor allem anderen.
- **Zwei Chats, zwei Stimmen** – Coach-Stimme für die dominante Seite, die Persona der Dominanten für die devote. Nichts geht ohne Bestätigung der dominanten Seite an die devote.

## So sieht es aus

*(gestelltes Beispiel)*

> **Dominante:** morgen früh putzt er meine Stiefel, vor 9
>
> **Coach:** Notiert 📋 – *„Stiefel putzen“*, Zustellung morgen 7:00, Frist 9:00. Liest sich wie eine 2/5 für ihn. So schicken? ✅ ✏️ ❌

> **Bot → devote Seite** *(Persona-Stimme)***:** Guten Morgen. Bevor du auch nur an Kaffee denkst: meine Stiefel. Bis neun makellos – ich *werde* nachsehen.

Danach meldet sich der Sub zurück und erzählt, wie es sich angefühlt hat, Punkte und Streaks werden aktualisiert, und die dominante Seite bekommt einen Bericht mit der Bitte um eine Bewertung (1–5 ★) – die ins Lernsystem für die nächste Aufgabe fließt.

---

## ⚠️ Vorgesehene Nutzung

- Für **einvernehmliche Erwachsene** in einer bestehenden, ausgehandelten Dynamik. Der Bot ist ein Werkzeug, das eine Beziehung unterstützt – er ersetzt weder Aushandlung noch Einvernehmen noch Aftercare.
- Alle Daten (Profile, Gespräche, Gefühle) liegen **lokal** in deiner eigenen Qdrant-Instanz. Der einzige externe Dienst ist die LLM-API (standardmäßig xAI Grok); Nachrichteninhalte gehen zur Textgenerierung dorthin.
- Logs enthalten intime Inhalte. Der eingebaute Log-Server ist deshalb **standardmäßig aus** und startet ohne Authentifizierung nicht.

---

## Funktionen

### Aufgaben

- **Aufgaben aus freiem Text** – die dominante Seite schreibt ganz normal; der Bot erkennt die Aufgabe, fragt mit einer Schwierigkeits-Einschätzung und optionaler Zustellzeit nach Bestätigung, stellt sie in der Persona-Stimme zu, fragt nach, erfasst das Gefühl des Subs, vergibt Punkte und Streaks, berichtet zurück und lässt die dominante Seite bewerten (1–5 ★) und kommentieren.
- **Ketten, Serien, Vorlagen** – Aufgaben-Ketten schalten sich Schritt für Schritt frei, und lief der letzte Schritt schlecht, schlägt der Coach für den nächsten eine angepasste Alternative vor; mehrtägige Serien bekommen automatische tägliche Nachfragen; Vorlagen halten Lieblingsaufgaben einen Befehl entfernt.
- **Dauer-Anweisungen** – `/dauer <Stunden> <Text>`: eine Anweisung über 1–48 Stunden mit unangekündigten Zwischen-Checks, danach die übliche „Durchgehalten?“-Nachfrage.
- **Tiny Tasks & Inspiration** – täglich ein Vorschlag für eine kurze Aufgabe, den die dominante Seite mit einem Tipp weitergibt, drei Ideen auf Abruf passend zum Level des Subs, und abends die Frage, warum ein Vorschlag nicht genutzt wurde – die Antwort kann zur Coach-Regel werden.
- **Resurface** – einmal pro Woche holt der Bot eine gut bewertete Aufgabe von vor etwa drei Monaten hervor und bietet an, sie erneut zu erteilen.

### Spiel

- **Würfel & Roulette** – `/wuerfel` würfelt eine Überraschungsaufgabe außerhalb der üblichen Rotation, mit Telegrams Würfel-Animation; `/roulette` lässt eine Slot-Machine über eine Strafe entscheiden: Jackpot heißt Gnade, alles darunter bestimmt den Schweregrad. Beides erreicht die dominante Seite zuerst als Vorschau.
- **Wetten & Privilegien** – der Sub kann Punkte auf seine nächste Aufgabe setzen (doppelt oder nichts, mit Spott der Persona) und Punkte im Privilegien-Shop ausgeben: ein Pause-Tag, Easy Mode, eine Wunsch-Kategorie, eine freie Aufgabe, ein Geheimnis der Dominanten und mehr. Jede Einlösung braucht die Bestätigung der dominanten Seite.
- **Blitzaufgaben** – Opt-in: unangekündigte Mini-Aufgaben mit 30-Minuten-Countdown, nur in kinderfreien Zeitfenstern, nie zwei gleichzeitig.
- **Storylines** – `/arc_starten <Thema>` macht aus einem Thema eine 3–7-tägige Storyline mit einer Aufgabe pro Tag; `/event <Datum> <Thema>` plant eine Storyline, deren Finale genau auf einen Geburtstag oder Jahrestag fällt; `/adventskalender` öffnet vom 1. bis 24. Dezember jeden Morgen ein Türchen.
- **Rollenspiel** – Szenarien-Bibliothek mit einstellbarer Intensität; die Persona bleibt über Tage in der Rolle, und freitags und samstags schlägt der Bot ein passendes Szenario vor.
- **Quizze** – der Sub wird gefragt, wie gut er die dominante Seite kennt (jede Antwort muss aus gespeicherten Daten belegbar sein; Punkte für richtige Antworten, ein Konter der Persona für falsche); die dominante Seite bekommt ein Coach-Quiz, das Fachwissen mit Auflösung und Fragen über den Sub mischt.
- **Geheimnisse** – die dominante Seite hinterlegt ein Geheimnis mit Enthüllungsdatum; der Bot enthüllt es dem Sub im richtigen Moment.
- **Wünsche** – der Sub reicht Wünsche ein, über die die dominante Seite entscheidet, und wählt bis zu drei Lieblings-Kategorien; lässt der Sub die Persona wissen, dass die dominante Seite „etwas erfahren soll“, gibt der Coach es in eigenen Worten weiter statt wörtlich.
- **Reaktions-Sticker** – die Persona antwortet mit Stickern für Lob, Spott, Befehle und mehr; eigenes Set mitbringen, das Upload-Script liegt bei.
- **Spiel-Impulse** – optional: die Persona startet von sich aus ein Quiz oder eine Wette mit dem Sub, und der Coach lässt spontan eine Quizfrage oder eine fertige Wett-Idee für die dominante Seite fallen – gedrosselt, in Zeitfenstern, nie beides zugleich.
- **Punkte, Streaks, Abzeichen** – 15 Abzeichen plus eine Handvoll geheime, ein Vertrauens-Score, ein Level-System, das die Aufgaben-Komplexität skaliert, Wochenend-Multiplikatoren.

### Lernen & Coaching

- **Lernsystem** – Kategorie-Reaktionen, Persönlichkeits-Tags, Vorlieben-Erkennung aus dem Chat (immer als Vorschlag, nie still angewendet), Abneigungs-Schwellen, automatische Schwierigkeits-Anpassung, Vertrauens-Score, Level-System, Erkundung benachbarter Kategorien im 60/30/10-Mix aus Favoriten / Mittelfeld / Neuem, und wöchentliches Altern veralteter Reaktionen.
- **Coach-Gespräche** – die dominante Seite chattet mit einem Coach, der wie eine vertraute Freundin mit demselben Hobby klingt: Aufgaben-Ideen, Wochenplanung am Sonntag, Psycho-Training zweimal pro Woche (Mindset-Fragen und Challenges), auf Wunsch ein Rückblick auf die letzten Wochen, Ziel-Tracking mit Erinnerung am Montag.
- **Wissen** – kuratierte Wissens-Briefe pro Kategorie (`/lerne`: Anatomie, Sicherheit, Progression, Hilfsmittel, typische Fehler), die in jeden Generator fließen; Regeln und Notizen, die die dominante Seite mit einem Tipp bestätigt; alle zwei Wochen eine Selbstreflexion des Coachs, die aus den jüngsten Gesprächen neue Regeln vorschlägt.
- **Gedächtnis, das sich selbst verdichtet** – wöchentliche Dossiers beider Partner, ein wöchentliches Lerntagebuch der Coach-Gespräche, offene Fäden, die die Persona von sich aus aufgreift, und alle zwei Wochen eine Lernkurven-Analyse.
- **Lücken-Füller** – Opt-in: gab es länger keine Aufgabe, schlägt der Bot eine vor, und die dominante Seite schickt sie jetzt, heute Abend, verlangt eine andere oder lehnt ab.
- **Stille-Check-in** – nach einer Woche ohne jede Eingabe der dominanten Seite fragt der Coach selbst nach, was los ist (Ein-Tipp-Antworten: keine Zeit / die Vorschläge passen nicht / läuft gerade ohne den Bot / irgendwas nervt), und passt sich an: zwei Wochen Coach-Ruhe, ein Zuschauer-Modus, der nur noch berichtet, oder eine aus der Antwort gelernte Regel. Die Antwort bleibt beim Coach.

### Sicherheit

- **Safeword** – bei **jeder** Nachricht und Sprachnachricht vor aller anderen Logik geprüft, unabhängig von Groß-/Kleinschreibung; es pausiert sofort alle Jobs des Paares, ein zweites Wort hebt die Pause auf.
- **Hard Limits beider Partner** – deterministisch gegen jede generierte Aufgabe geprüft – Normalisierung, Wortgrenzen- und Stamm-Matching, Wiederholungsschleife – unabhängig vom LLM.
- **Leitplanken an den Rändern** – geschützte Profilfelder, kinderfreie Zeitfenster für alles Zeitkritische, Abwesenheiten (`/abwesend`), die in jeden Generator fließen, und ein Strafen-Protokoll, das die dominante Seite einsehen kann.

### Anpassung

- **Personas** – die dominante Stimme und die Coach-Stimme sind konfigurierbare Stil-Presets (Markdown-Dateien, eigene möglich), mit optionalem Bot-Namen, Anrede und einem realen Setup-Kontext, damit generierte Szenen anatomisch und logistisch konsistent bleiben.
- **Rollen-Konstellationen** – F/M, M/F, F/F, M/M. Bezeichnungen, Pronomen und die Anatomie-Regeln werden aus der eingestellten Konstellation erzeugt.
- **Sprachen** – UI-Texte, Menüs und Befehls-Aliase auf Deutsch und Englisch, die Antwortsprache des LLM frei wählbar – alles **pro Paar**, zur Laufzeit umschaltbar.
- **Stimme** – optionale Sprachnachrichten: Piper-TTS (komplett lokal) oder Grok-TTS (Cloud; ausdrucksstarke mehrsprachige Stimmen pro Rolle mit Sprech-Tags wie `[laugh]` und `<whisper>`), dazu Whisper-STT für Spracheingabe inklusive Safeword-Prüfung. Wer dem Bot spricht, bekommt eine gesprochene Antwort; der Coach kann auf Wunsch eine geflüsterte Sprachnachricht an den Sub weiterreichen.
- **Mini-App** – optionale Web-App im Chat (`/app`): ein Statistik-Cockpit für beide Partner und ein Sprachnachrichten-Studio für die dominante Seite (Tag-Buttons, TTS-Vorschau, Zustellung mit einem Tipp). Läuft auf Wunsch nur im LAN; siehe [MINIAPP.md](MINIAPP.md) (englisch).

### Betrieb

- **Mehrere Paare** – eine Installation kann mehrere Paare bedienen (`PAIRING_ENABLED`: `/start` → Rollenwahl → Einladungscode). Daten, Persona, Zeitpläne, Sprache, Safeword und Pause-Zustand sind pro Paar vollständig getrennt; Betreiber-Befehle (`ADMIN_CHAT_ID`) listen Paare und löschen eines samt **aller** gespeicherten Daten; ein optionales Tagesbudget deckelt die LLM-Kosten pro Paar.
- **Ops** – Docker-Deployment, tägliche Qdrant-Snapshots + JSON-Exporte, Restore-Script, Zustand überlebt Neustarts, ein OpenAI-kompatibler Fallback-Endpunkt plus optional ein lokales Ollama-Modell, das den Sub-Chat am Leben hält, wenn das Haupt-LLM ausfällt.

## Befehle im Überblick

<!-- commands:start -->

<details>
<summary><b>Dominante Seite</b> – 47 Befehle</summary>

**📋 Aufgaben & Vorlagen**

| Befehl | Was er tut |
|---|---|
| `/aufgaben` | Erledigte und offene Aufgaben anzeigen |
| `/aufgaben_alle` | Alle Aufgaben (alle Kategorien) |
| `/loeschen` | Offene Aufgabe pausieren oder löschen |
| `/vorlagen` | Aufgaben-Vorlagen verwalten |
| `/inspiration` | 3 Aufgaben-Ideen passend zum Level |
| `/tinytask` | Tiny Task Vorschlag anfordern |
| `/wuerfel` | 🎲 Surprise-Aufgabe würfeln |
| `/roulette` | Strafen-Roulette: Slot-Machine bestimmt Gnade oder Härte |
| `/dauer` | Anweisung über Stunden mit unangekündigten Zwischen-Checks |

**📖 Storylines**

| Befehl | Was er tut |
|---|---|
| `/arc` | Aktive Storyline anzeigen |
| `/arc_starten` | Neue Storyline: /arc_starten <thema> |
| `/arc_beenden` | Aktive Storyline beenden |
| `/event` | Event planen: /event <TT.MM.> [Tage] <Thema> – Storyline endet am Event-Tag |
| `/event_loeschen` | Geplantes Event verwerfen |
| `/adventskalender` | Adventskalender planen – 1.-24.12. jeden Morgen ein Türchen |

**🎭 Rollenspiel & Wochenplanung**

| Befehl | Was er tut |
|---|---|
| `/rollenspiel` | Rollenspiel-Szenario starten |
| `/rollenspiel_beenden` | Rollenspiel beenden |
| `/wochenplanung` | Wochenplan erstellen |
| `/training` | Psycho-Training starten |

**📊 Statistik & Reflexion**

| Befehl | Was er tut |
|---|---|
| `/profil` | Profil anzeigen und bearbeiten |
| `/ziele` | Ziele und Fortschritt anzeigen |
| `/rueckblick` | Rückblick der letzten Wochen |
| `/strafen` | Strafen-Protokoll anzeigen |
| `/geheimnis` | Geheimnis für späteren Zeitpunkt hinterlegen |

**🧠 Coach & Wissen**

| Befehl | Was er tut |
|---|---|
| `/quiz` | 🧠 Coach-Quiz: Fachwissen lernen oder Sklaven-Wissen prüfen |
| `/lerntagebuch` | 📓 Coach-Gespräche der letzten Tage verdichten |
| `/dossier` | 🗒 Charakteristik des Sklaven (was der Bot über ihn weiß) |
| `/botname` | 🏷 Namen der Bot-Herrin festlegen |
| `/sklavenname` | 🏷 Anrede für den Sklaven festlegen |
| `/setup` | 🧩 Setup/Kontext (Rollen, Anatomie, Ausstattung) festlegen |
| `/regel` | ⚡ Verbindliche Regel für den Coach setzen |
| `/merken` | 📝 Notiz/Vorliebe merken |
| `/regeln` | 📋 Gelernte Regeln & Vorschläge anzeigen |
| `/vergessen` | 🗑 Regel deaktivieren (Nummer aus /regeln) |
| `/profil_check` | 🧬 Profil-Updates manuell prüfen |
| `/lerne` | 📚 Wissens-Brief zu einer Kategorie |
| `/skills` | 📚 Vorhandene Wissens-Einträge |
| `/lerne_neu` | 📚 Wissens-Brief neu generieren |
| `/skill_bearbeiten` | ✏️ Wissens-Eintrag überschreiben |

**⚙️ System**

| Befehl | Was er tut |
|---|---|
| `/einstellungen` | Sprache, Persönlichkeits-Stil, Namen und Setup einstellen |
| `/abwesend` | Abwesenheit eintragen: /abwesend 20.07.-02.08. Grund – Aufgaben & Vorschläge berücksichtigen den Zeitraum; /abwesend ende hebt auf |
| `/luecken` | Bei längerer Aufgaben-Ruhe automatisch einen Task-Vorschlag bekommen (du gibst frei) |
| `/blitz` | Unangekündigte Mini-Aufgaben mit Countdown für den Sklaven (gehen direkt raus) |
| `/ueberspringen` | Optionalen Kommentar überspringen |
| `/app` | Mini-App im Chat öffnen: Statistik-Cockpit + Sprachnachrichten-Studio (LAN) |
| `/abbrechen` | Aktuelle Aktion abbrechen |
| `/hilfe` | Diese Übersicht |

</details>

<details>
<summary><b>Devote Seite</b> – 14 Befehle</summary>

**📊 Status & Statistik**

| Befehl | Was er tut |
|---|---|
| `/profil` | Profil anzeigen und bearbeiten |
| `/stats` | Punkte, Streak, Abzeichen, Privilegien |

**💬 Mitteilen**

| Befehl | Was er tut |
|---|---|
| `/stimmung` | Stimmung mitteilen |
| `/wunsch` | Wunsch oder Vorschlag einreichen |
| `/meinewuensche` | Gesammelte Wünsche ansehen/aufräumen |
| `/wunschkategorien` | Lieblings-Kategorien wählen (max 3) |

**🎁 Belohnungen**

| Befehl | Was er tut |
|---|---|
| `/privileg` | Privilegien einlösen (kostet Punkte) |
| `/wette` | Punkte auf die nächste Aufgabe wetten (doppelt oder nichts) |
| `/quiz` | Quizfrage über deine Herrin – richtige Antwort gibt Punkte |

**📋 Aufgaben**

| Befehl | Was er tut |
|---|---|
| `/meineaufgaben` | Offene Aufgaben ansehen & abschließen |

**⚙️ System**

| Befehl | Was er tut |
|---|---|
| `/abwesend` | Abwesenheit eintragen: /abwesend 20.07.-02.08. Grund – Aufgaben & Vorschläge berücksichtigen den Zeitraum; /abwesend ende hebt auf |
| `/app` | Mini-App im Chat öffnen: Punkte, Streak, Aufgaben als Übersicht (LAN) |
| `/abbrechen` | Aktuelle Aktion abbrechen |
| `/hilfe` | Diese Übersicht |

</details>

Die deutschen Befehlsnamen sind die Originale; die englischen Aliase (z. B. `/tasks` für `/aufgaben`) sind in jeder Installation ebenfalls registriert.

<!-- commands:end -->

## Tech-Stack

| Komponente | Technologie |
|-----------|------------|
| Bot-Framework | python-telegram-bot 21 |
| LLM | xAI Grok (konfigurierbar, optionaler Fallback-Endpunkt) |
| Vektor-DB / Gedächtnis | Qdrant (Hybrid aus semantischer Suche und Aktualität) |
| Embeddings | Ollama – `jina-embeddings-v2-base-de` (768 Dim., deutsch trainiert; konfigurierbar) |
| Scheduler | APScheduler |
| Deployment | Docker / docker-compose |

---

## Schnellstart

1. **Telegram-Bot anlegen** über [@BotFather](https://t.me/BotFather) und den Token notieren.
2. **Die beiden Chat-IDs herausfinden** (z. B. über [@userinfobot](https://t.me/userinfobot)) – eine pro Partner.
3. **xAI-API-Key besorgen** (oder `GROK_MODEL`/`FALLBACK_LLM_*` auf einen kompatiblen Endpunkt zeigen lassen).
4. Konfigurieren und starten:

```bash
cp .env.example .env      # die vier Pflichtwerte eintragen
mkdir -p qdrant_data qdrant_snapshots backups data

# Embeddings sind Pflicht. Entweder das mitgelieferte Profil nutzen ...
docker compose --profile ollama up -d ollama
docker compose exec ollama ollama pull hf.co/MAY-A/jina-embeddings-v2-base-de-Q5_K_M-GGUF:Q5_K_M
# ... oder OLLAMA_URL in der .env auf ein bestehendes Ollama zeigen lassen.
# (Das Standardmodell ist deutsch trainiert – für andere Sprachen ein
#  mehrsprachiges Embedding-Modell wählen und EMBEDDING_DIM anpassen, siehe .env.example.)

docker compose up -d --build
# optional komplett lokale Sprachnachrichten (TTS/STT): --profile voice ergänzen
# und TTS_WYOMING_URL/STT_WYOMING_URL in der .env setzen (siehe .env.example)
```

Beide Partner schreiben dann einfach dem Bot – beim ersten Kontakt läuft ein geführter Einrichtungs-Assistent (Sprache, Rollen-Konstellation, Stil-Preset, Erfahrung, Limits, Ziele, kinderfreie Zeiten).

### Tests

```bash
make test        # ganze Suite lokal (läuft ohne Docker/Abhängigkeiten über Stubs)
make deploy      # test → build → up (kein Deploy mit roten Tests)
```

---

## Konfiguration

Alle Einstellungen liegen in der `.env` (siehe [`.env.example`](.env.example) für die vollständige, kommentierte Liste). Die vier Pflichtwerte sind der Bot-Token, beide Chat-IDs und der LLM-API-Key.

Zur Laufzeit stellt die dominante Seite den Rest über `/einstellungen` ein:
Antwortsprache, Stil-Preset, Rollen-Konstellation, Bot-Name, Anrede, den realen Setup-Kontext, die Tageszeiten, das Safeword-Paar sowie Coach-Ruhe / Zuschauer-Modus.

### Eigene Personas & Regel-Vorlagen

Die drei eingebauten Stil-Presets liegen als reines Markdown in [`bot/prompts/presets/`](bot/prompts/presets/). Eine eigene Datei in `data/persona_presets/` (oder `PERSONA_PRESETS_DIR` setzen) ergänzt ein Preset – gleicher Schlüssel überschreibt ein eingebautes, fehlende Abschnitte erben von `standard`:

```markdown
label: Eiskalt & flüsternd

## stil_kopf
STIL der dominanten Stimme:
- ...

## stil_fuss      (optional – verbotenes Vokabular / Variations-Regeln)
## coach_stil     (optional – die Coach-Stimme)
```

Auch die festen Verhaltensregeln (führen statt spiegeln, Wortvielfalt, anatomische Verankerung) sind überschreibbar: eine `templates/regeln_gespraech.md` oder `templates/grundierung_zusatz.md` neben die Presets legen. Platzhalter wie `{sub_nom}` oder `{dom_rolle}` werden aus der eingestellten Rollen-Konstellation gefüllt. Ein Neustart übernimmt Änderungen – kein Rebuild nötig.

### Hinweise zur Sprache

`BOT_LOCALE` (de/en) schaltet die UI-Texte um und registriert englische Befehls-Aliase; die generierten Antworten folgen stattdessen der Laufzeit-Spracheinstellung. **Sicherheitshinweis:** der deterministische Hard-Limit-Matcher bringt eine deutsche Synonymliste mit – in nicht-deutschen Installationen werden nur wörtliche Limit-Begriffe erkannt, also Limits in der Sprache formulieren, in der gespielt wird. Das Standard-Embedding-Modell ist deutsch trainiert; für andere Sprachen **vor** der ersten Nutzung ein mehrsprachiges Modell über `OLLAMA_MODEL`/`EMBEDDING_DIM` setzen (ein späterer Wechsel erfordert Neu-Embedding).

### Log-Server (standardmäßig aus)

`LOG_PORT=0` deaktiviert den HTTP-Log-Server (Standard). Wird er aktiviert, verweigert er ohne `LOG_USERS` (Basic-Auth) den Start, spricht reines HTTP, und die Compose-Datei bindet ihn an `127.0.0.1` – Zugriff per SSH-Tunnel. Nachrichteninhalte landen nur in der 0600-Logdatei, nie in `docker logs`.

---

## Architektur (Kurzfassung)

```
eingehende Nachricht
  → Chat-ID-Autorisierung → Safeword-Prüfung → Zustandsmaschine (aktiver Flow?)
  → Rollen-Handler (dominant: Coach + Aufgaben-Erkennung / devot: Persona-Chat)
```

- **Gedächtnis:** jedes Gespräch wird in Qdrant eingebettet; Prompts kombinieren die semantisch nächsten und die jüngsten Einträge, dedupliziert.
- **Prompt-Aufbau:** Persona-Bausteine (Stil-Preset + Identität + Verankerung + Sprache) werden pro Nachricht in `bot/prompts/` zusammengesetzt; Aufgaben-Generatoren erhalten zusätzlich gelernten Kontext (Kategorie-Gewichte, Bewertungen, Abneigungen, kuratiertes Wissen, Dossier).
- **Sicherheits-Gate:** `limits_check` prüft jede generierte Aufgabe gegen die Limits beider Partner mit Normalisierung, Wortgrenzen-/Stamm-Matching und einer Wiederholungsschleife – deterministisch, unabhängig vom LLM.
- **Scheduler:** tägliche Nachfrage, Tiny-Task-Vorschläge, Stimmungs-Tracking, Wochenplanung, zweiwöchentliche Analysen, Geheimnis-Enthüllungen, Storyline-Tage, Blitzaufgaben, Spiel- und Coach-Impulse, Lücken-Checks und der Stille-Check-in – alle Jobs pausieren während einer Safeword-Pause.

Collections: `tasks`, `conversations`, `knowledge_base`, `progress`, `user_profiles`, `training`, `wuensche`, `geheimnisse`, `strafen`, `skills`, `coach_regeln`.

---

## Projektstruktur

```
bot/
├── main.py              # Einstiegspunkt: Handler, Befehle, Scheduler-Registrierung
├── config.py            # Env-Variablen + Validierung
├── state.py             # In-Memory-Zustandsmaschine (auf Platte persistiert)
├── locales/             # UI-Texte (de = Referenz, en als Overlay) + Befehls-Aliase
├── handlers/            # ein Modul pro Flow (Aufgabe, Gefühl, Bewertung, Wünsche, …)
├── services/            # qdrant, grok, embeddings, limits_check, labels, …
├── scheduler/           # APScheduler-Jobs
└── prompts/             # Prompt-Builder für Persona/Coach/Aufgaben
    └── presets/         # Stil-Presets (Markdown) + Verhaltensregel-Vorlagen
scripts/                 # Restore, Migrationen, README-Befehlstabellen
tests/                   # eigenständige Test-Scripts (laufen über make test)
```

---

## Backup & Restore

Ein täglicher Job schreibt native Qdrant-Snapshots nach `./qdrant_snapshots` und JSON-Exporte nach `./backups`. Wiederherstellen:

```bash
python3 scripts/restore_qdrant.py list
python3 scripts/restore_qdrant.py recover-all <YYYY-MM-DD-HH-MM-SS>
```

Eine externe Kopie von `qdrant_snapshots/` aufbewahren – sonst liegt sie auf derselben Platte.

---

## Trainingsdaten-Export (Fine-Tuning)

Der Bot kann die gespeicherten Gespräche als Fine-Tuning-Datensätze exportieren, z. B. um ein lokales Modell auf die beiden Stimmen zu trainieren:

```bash
docker exec bdsm-bot python -m bot.tools.export_training
# optional: Sitzungs-Lücke in Minuten / Mindestlänge der Nutzer-Nachricht
docker exec bdsm-bot python -m bot.tools.export_training --gap 45 --min-chars 12
```

Das schreibt zwei Dateien nach `./data/training/` auf dem Host:

- **`coach.jsonl`** – Gespräche dominante Seite → Coach (lernt die Coach-Stimme)
- **`herrin.jsonl`** – Gespräche devote Seite → Dominante (lernt die dominante Persona)

Jede Zeile ist eine Sitzung im OpenAI-**messages-JSONL-Format** (`{"messages": [{"role": ...}, ...]}`), direkt lesbar für unsloth, axolotl, llama-factory, OpenAI-Fine-Tuning und Ollama-Werkzeuge. Nachrichten mit weniger als `--gap` Minuten Abstand werden zu Mehrfach-Sitzungen zusammengefasst, damit das Modell Gesprächskontext lernt. Der System-Prompt je Zeile ist **nur der stabile Persona-Baustein** – der dynamische Laufzeit-Prompt mit Profil-/Dossier-Daten bleibt bewusst draußen, damit das Modell den Stil lernt, ohne sich auf persönliche Details einzuschießen. Platzhalter-Antworten („ok“, „notiert.“) und doppelte Paare werden gefiltert.

> **⚠️ Datenschutz:** der Persona-Baustein ist sauber, aber die `user`/`assistant`-Züge sind eure **echten Chat-Nachrichten, wörtlich** – intime Gespräche **beider** Partner, womöglich mit Namen, Orten und Alltagsdetails. Behandle die exportierten Dateien wie die Datenbank selbst: lokal lassen und Zeile für Zeile prüfen und schwärzen, bevor sie in irgendeiner Form deinen Rechner verlassen.

---

## Projekt unterstützen

Das ist ein Open-Source-Projekt aus der Freizeit. Wenn es dir nützt, kannst du die
Entwicklung über [GitHub Sponsors](https://github.com/sponsors/Meisterull) unterstützen –
einmalig oder monatlich, jeder Beitrag hilft. Das Geld fließt direkt in Entwicklungszeit
und die API-Kosten, um neue Funktionen gegen echte LLMs zu testen.

## Lizenz

[AGPL-3.0](LICENSE). Wer eine veränderte Version als Dienst betreibt, muss seinen Nutzern den Quellcode anbieten.
