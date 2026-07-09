import os
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DOMINA_CHAT_ID = os.getenv("DOMINA_CHAT_ID")
SKLAVE_CHAT_ID = os.getenv("SKLAVE_CHAT_ID")
XAI_API_KEY = os.getenv("XAI_API_KEY")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
# Optionaler Qdrant-API-Key (Härtung): muss zum QDRANT__SERVICE__API_KEY des
# Qdrant-Containers passen (hier via docker-compose.override.yml gesetzt).
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
FOLLOWUP_TIME = os.getenv("FOLLOWUP_TIME", "17:30")
SAFEWORD = os.getenv("SAFEWORD", "stopp").lower()
# Wort, das die Safeword-Pause aufhebt – konfigurierbar (z.B. "continue" bei
# englischsprachiger Nutzung). Wird auch in den SAFEWORD_*-Texten angezeigt.
RESUME_WORT = os.getenv("RESUME_WORT", "weiter").lower()
TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")
GROK_MODEL = os.getenv("GROK_MODEL", "grok-4.3")
# Separates Reasoning-Modell via Env GROK_MODEL_REASONING konfigurierbar. Solange es
# gleich GROK_MODEL ist (grok-4.3 hat keine eigene Reasoning-Variante), ist das
# reasoning=True-Flag an den Callsites bewusst wirkungslos – es markiert nur, welche
# Aufrufe von einem stärkeren Modell profitieren würden.
GROK_MODEL_REASONING = os.getenv("GROK_MODEL_REASONING", GROK_MODEL)
GROK_API_URL = "https://api.x.ai/v1/chat/completions"
# grok-4.3 lehnt frequency_penalty/presence_penalty mit HTTP 400 ab ("does not
# support parameter"). Penalties daher nur senden, wenn das Modell sie kann –
# per Env GROK_SUPPORTS_PENALTIES=1 reaktivierbar für Modelle, die sie können.
GROK_SUPPORTS_PENALTIES = os.getenv("GROK_SUPPORTS_PENALTIES", "0").lower() in ("1", "true", "yes")
# Embedding-Modell (lokal via Ollama). jina-embeddings-v2-base-de ist deutsch-trainiert
# und schlug nomic im Retrieval-Vergleich (Top-3 100% vs 70%, MRR +13%). 768-dim wie zuvor.
# Rollback: OLLAMA_MODEL=nomic-embed-text:latest als Env setzen (+ Re-Embedding zurück).
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "hf.co/MAY-A/jina-embeddings-v2-base-de-Q5_K_M-GGUF:Q5_K_M")
# ACHTUNG i18n: das Default-Modell ist DEUTSCH-trainiert. Fuer nicht-deutsche
# Deployments ein multilinguales Embedding-Modell setzen (z.B. bge-m3 -> 1024)
# und EMBEDDING_DIM passend mitziehen. Ein Modellwechsel erfordert Re-Embedding
# ALLER Qdrant-Collections (Dimension + Vektorraum aendern sich).
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))

# Level-Aufstieg: Gesamt-Score (Tasks + Vielfalt + Streak + Bewertung) -> Level
LEVEL_THRESHOLDS = {2: 10, 3: 25, 4: 50, 5: 100}

# UI-Locale der statischen Texte + Command-Aliase (bot/locales/, "de" = Referenz).
# Unabhaengig von persona_config.sprache (Sprache der LLM-Antworten, zur Laufzeit
# aenderbar) - BOT_LOCALE braucht einen Neustart.
BOT_LOCALE = os.getenv("BOT_LOCALE", "de").strip().lower()

# Eigene Persona-Presets + Template-Overrides des Betreibers (*.md bzw.
# templates/*.md, siehe bot/prompts/persona_presets.py). Liegt im gemounteten
# ./data-Volume; Aenderungen brauchen einen Neustart.
PERSONA_PRESETS_DIR = os.getenv("PERSONA_PRESETS_DIR", "data/persona_presets")

# Feature Flags
STIMMUNG_ENABLED = os.getenv("STIMMUNG_ENABLED", "false").lower() == "true"
TRAINING_ENABLED = os.getenv("TRAINING_ENABLED", "true").lower() == "true"

# Pairing (Multiuser-Abschluss): Registrierung weiterer Paare via /start + Invite-Code.
# Default AUS – bewusste Betreiber-Entscheidung, erst aktivieren wenn Mehr-Paar-
# Betrieb gewollt ist. Registrierte Paare liegen in PAARE_FILE (gemountetes Volume).
PAIRING_ENABLED = os.getenv("PAIRING_ENABLED", "false").lower() == "true"
PAARE_FILE = os.getenv("PAARE_FILE", "/app/data/paare.json")
INVITE_TTL_STUNDEN = int(os.getenv("INVITE_TTL_STUNDEN", "48"))
# Betreiber-Chat für Admin-Kommandos (/paare, /paar_loeschen). Leer = Kommandos aus.
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")
# Missbrauchs-/Kosten-Bremse: max. Chat-Nachrichten pro Paar und Tag, die den
# LLM-Pfad erreichen (0 = aus). Commands/Safeword sind ausgenommen; der Zähler
# ist in-memory (Neustart resettet – Bremse, keine Abrechnung).
LLM_BUDGET_PRO_TAG = int(os.getenv("LLM_BUDGET_PRO_TAG", "0"))

# Concurrency (Multiuser Schritt 7): max. parallel verarbeitete Telegram-Updates.
# Updates DESSELBEN Paares bleiben über paare.lock() strikt seriell (Mode-Maschine);
# der Wert begrenzt nur die Parallelität ÜBER Paare hinweg. 1 = altes Verhalten.
CONCURRENT_UPDATES = int(os.getenv("CONCURRENT_UPDATES", "8"))


def hm(zeit: str) -> tuple:
    """'HH:MM' -> (hour, minute) für scheduler.add_job."""
    h, m = zeit.split(":")
    return int(h), int(m)


# Scheduler-Zeiten (HH:MM, per Env übersteuerbar; Wochentage bleiben im Code)
TINY_TASK_TIME = os.getenv("TINY_TASK_TIME", "18:00")
STIMMUNG_TIME = os.getenv("STIMMUNG_TIME", "19:00")
ZIEL_ERINNERUNG_TIME = os.getenv("ZIEL_ERINNERUNG_TIME", "20:00")            # montags
ROLLENSPIEL_VORSCHLAG_TIME = os.getenv("ROLLENSPIEL_VORSCHLAG_TIME", "18:00")  # Fr+Sa
WOCHENPLANUNG_TIME = os.getenv("WOCHENPLANUNG_TIME", "19:00")                # sonntags
TINY_TASK_FEEDBACK_TIME = os.getenv("TINY_TASK_FEEDBACK_TIME", "21:30")
TRAINING_ERINNERUNG_TIME = os.getenv("TRAINING_ERINNERUNG_TIME", "20:00")

# Lücken-Füller: schlägt der Domina nach LUECKEN_INTERVALL_TAGE Tagen ohne erteilte
# Aufgabe/Szene einen Task vor (nur bei Opt-in via /luecken; sie gibt jeden frei).
LUECKEN_INTERVALL_TAGE = int(os.getenv("LUECKEN_INTERVALL_TAGE", "2"))
LUECKEN_CHECK_TIME = os.getenv("LUECKEN_CHECK_TIME", "16:00")   # täglicher Nachmittags-Check
LUECKEN_ABEND_TIME = os.getenv("LUECKEN_ABEND_TIME", "20:00")   # "Heute Abend"-Zustellung

# Blitzaufgaben ⚡: unangekündigte Mini-Aufgabe mit Countdown (Opt-in via /blitz).
# Der Check läuft alle 30 Min im Fenster; CHANCE pro Check hält es selten
# (0.02 bei ~24 Checks/Tag ≈ 3-4x/Woche, gedrosselt durch MIN_ABSTAND_TAGE).
BLITZ_FENSTER = os.getenv("BLITZ_FENSTER", "09:00-21:00")
BLITZ_CHANCE = float(os.getenv("BLITZ_CHANCE", "0.02"))
BLITZ_COUNTDOWN_MINUTEN = int(os.getenv("BLITZ_COUNTDOWN_MINUTEN", "30"))
BLITZ_MIN_ABSTAND_TAGE = int(os.getenv("BLITZ_MIN_ABSTAND_TAGE", "2"))

# Sprachnachrichten der Herrin 🔊 (lokales Piper via Wyoming-Protokoll).
# Leer = aus. Beispiel: tcp://192.0.2.10:10200 (wyoming-piper auf dem Host).
# Aufgaben-Zustellungen kommen dann zusätzlich als Telegram-Voice (best-effort).
TTS_WYOMING_URL = os.getenv("TTS_WYOMING_URL", "")
TTS_VOICE = os.getenv("TTS_VOICE", "")        # leer = Server-Default-Stimme
# Stimmen pro Sprache (Multiuser): "de=de_DE-...-high,en=en_US-...-high".
# Piper-Stimmen sind einsprachig – die Stimme wird über den Sprachcode des
# Paares gewählt (persona_config.sprach_code); kein Treffer → TTS_VOICE.
# Die Stimmen müssen auf dem wyoming-piper-Server installiert sein.
TTS_STIMMEN = {
    code.strip().lower(): stimme.strip()
    for eintrag in os.getenv("TTS_STIMMEN", "").split(",") if "=" in eintrag
    for code, stimme in [eintrag.split("=", 1)]
    if code.strip() and stimme.strip()
}
TTS_MAX_ZEICHEN = int(os.getenv("TTS_MAX_ZEICHEN", "600"))

# Sprachnachrichten VERSTEHEN 🎤 (lokales Whisper via Wyoming-Protokoll).
# Leer = aus (Voice läuft dann wie bisher als Medien-Weiterleitung).
STT_WYOMING_URL = os.getenv("STT_WYOMING_URL", "")
STT_SPRACHE = os.getenv("STT_SPRACHE", "")     # leer = Server-Default
STT_MAX_SEKUNDEN = int(os.getenv("STT_MAX_SEKUNDEN", "120"))

# Dauer-Anweisungen 🕰 (/dauer): zufällige Zwischen-Checks der Herrin
DAUER_CHECK_ABSTAND_MIN = int(os.getenv("DAUER_CHECK_ABSTAND_MIN", "90"))  # Minuten zwischen Checks
DAUER_CHECK_CHANCE = float(os.getenv("DAUER_CHECK_CHANCE", "0.4"))         # pro 15-Min-Tick

# Wortlimits für generierte Vorschläge (werden in den Prompts referenziert)
TINY_TASK_WORTLIMIT = int(os.getenv("TINY_TASK_WORTLIMIT", "200"))
AUSFUEHRLICH_WORTLIMIT = int(os.getenv("AUSFUEHRLICH_WORTLIMIT", "400"))
WOCHENPLAN_WORTLIMIT = int(os.getenv("WOCHENPLAN_WORTLIMIT", "1000"))

# Schwellen (per Env übersteuerbar)
VERTRAUEN_SCHWELLE_SENKEN = int(os.getenv("VERTRAUEN_SCHWELLE_SENKEN", "40"))    # hoch -> normal
VERTRAUEN_SCHWELLE_NIEDRIG = int(os.getenv("VERTRAUEN_SCHWELLE_NIEDRIG", "25"))  # -> niedrig
TRAINING_ERINNERUNG_TAGE = int(os.getenv("TRAINING_ERINNERUNG_TAGE", "4"))
RESURFACE_TAGE_MIN = int(os.getenv("RESURFACE_TAGE_MIN", "80"))
RESURFACE_TAGE_MAX = int(os.getenv("RESURFACE_TAGE_MAX", "100"))

# Backup (Qdrant) – BACKUP_DIR muss ein gemountetes Volume sein (siehe docker-compose.yml)
BACKUP_DIR = os.getenv("BACKUP_DIR", "/app/backups")
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "14"))
BACKUP_SNAPSHOTS = os.getenv("BACKUP_SNAPSHOTS", "true").lower() == "true"

# State-Persistenz (message_history + Pause-Flag über Neustart). Muss gemountetes Volume sein.
STATE_FILE = os.getenv("STATE_FILE", "/app/data/state.json")

# Logging: komplette Textdatei (rotierend) + optionaler HTTP-Port zur Live-Analyse.
LOG_FILE = os.getenv("LOG_FILE", "/app/data/bot.log")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", "5000000"))   # 5 MB pro Datei
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "6"))
LOG_PORT = int(os.getenv("LOG_PORT", "0"))                  # 0 = aus (Default: intime Logs nicht exponieren)
LOG_TOKEN = os.getenv("LOG_TOKEN", "")                       # leer = kein Token (nur lokal binden!)
# HTTP-Basic-Auth für den Log-Server (Browser-Login). Format: "user1:pass1,user2:pass2".
# Leer = keine Auth. PFLICHT, sobald der Port über 127.0.0.1 hinaus freigegeben wird (LAN)!
LOG_USERS = os.getenv("LOG_USERS", "")

# Optionaler Fallback-LLM (OpenAI-kompatibler Chat-Endpoint), wenn Grok nicht erreichbar ist.
# Leer = aus (Verhalten wie bisher). Beispiel Ollama: http://localhost:11434/v1/chat/completions
FALLBACK_LLM_URL = os.getenv("FALLBACK_LLM_URL", "")
FALLBACK_LLM_KEY = os.getenv("FALLBACK_LLM_KEY", "")      # bei Ollama egal/leer
FALLBACK_LLM_MODEL = os.getenv("FALLBACK_LLM_MODEL", "")  # z.B. llama3.1 / kimi-k2.6:cloud

# Lokales Fallback-LLM für den Sklaven-Chat bei Grok-Ausfall. Anders als
# FALLBACK_LLM_* (voller Payload) bekommt es einen abgespeckten Kurz-Prompt –
# lokale CPU-Modelle verarbeiten Prompts zu langsam für den vollen Sklaven-Prompt.
# Läuft auf dem Ollama unter OLLAMA_URL (OpenAI-kompatibler /v1-Endpoint). Leer = aus.
LOKAL_LLM_MODEL = os.getenv("LOKAL_LLM_MODEL", "")  # z.B. krith/mistral-nemo-instruct-2407-abliterated:IQ4_XS
LOKAL_LLM_TIMEOUT = float(os.getenv("LOKAL_LLM_TIMEOUT", "180"))  # Sekunden; CPU-Inferenz ist langsam

# Aufgaben-Serien Optionen (Tage)
SERIE_OPTIONEN = [2, 3, 7, 14]

# Aufgaben-Kategorien
AUFGABEN_KATEGORIEN = [
    "Anal",
    "Schlucken",
    "Psycho",
    "Analdehnung",
    "Analeingangstraining",
    "Buttplug_Tragen",
    "Dildo_Training",
    "Pegging",
    "Strap_on",
    "Blowjob_Training",
    "Deepthroat_Training",
    "Gesichtsfick",
    "Enema_Play",
    "Creampie_Cleanup",
    "Fisting",
    "Prostatamassage",
    "Spanking",
    "Sperma_Schlucken",
    "Impact",
    "Peitsche",
    "Klassische_Fesselspiele",
    "Paddle_Training",
    "Piss_Play",
    "Toiletten_Sklave",
    "Arschanbetung",
    "Muschianbetung",
    "Orgasmusverweigerung",
    "Ruiniertes_Orgasmen",
    "Sissy_Training",
    "Feminisierung",
    "Facesitting",
    "Smothering",
    "Schmerz",
    "Speichelspiel",
    "Pet_Play",
    "Demütigung",
    "Verbale_Demütigung",
    "Erniedrigung",
    "Objektifizierung",
    "Dienst",
    "Bestrafung",
]


def kat_to_cmd(kat: str) -> str:
    """Kategoriename → gültiger Telegram-Command (nur ASCII, lowercase)."""
    return (kat.lower()
            .replace("ü", "ue").replace("ä", "ae")
            .replace("ö", "oe").replace("ß", "ss"))


# Psycho-Training Zeit (Minuten nach FOLLOWUP_TIME)
TRAINING_OFFSET_MINUTEN = 5


def validate() -> None:
    """Fail-fast beim Start: Pflicht-Env-Vars prüfen mit klarer Meldung.

    Ohne diese Prüfung schlägt z.B. ein fehlender XAI_API_KEY erst zur Laufzeit
    als 401 fehl und fehlende Chat-IDs erst als int(None)-Crash in post_init.
    """
    fehler = []
    if not TELEGRAM_BOT_TOKEN:
        fehler.append("TELEGRAM_BOT_TOKEN fehlt")
    if not XAI_API_KEY:
        fehler.append("XAI_API_KEY fehlt")
    for name, wert in (("DOMINA_CHAT_ID", DOMINA_CHAT_ID),
                       ("SKLAVE_CHAT_ID", SKLAVE_CHAT_ID)):
        if not wert:
            fehler.append(f"{name} fehlt")
        elif not str(wert).lstrip("-").isdigit():
            fehler.append(f"{name} ist keine gültige Chat-ID (numerisch): {wert!r}")
    # Zeit-Formate (HH:MM) prüfen, sonst crasht erst post_init beim Job-Scheduling.
    import re as _re
    for name, wert in (
        ("FOLLOWUP_TIME", FOLLOWUP_TIME),
        ("TINY_TASK_TIME", TINY_TASK_TIME),
        ("STIMMUNG_TIME", STIMMUNG_TIME),
        ("ZIEL_ERINNERUNG_TIME", ZIEL_ERINNERUNG_TIME),
        ("ROLLENSPIEL_VORSCHLAG_TIME", ROLLENSPIEL_VORSCHLAG_TIME),
        ("WOCHENPLANUNG_TIME", WOCHENPLANUNG_TIME),
        ("TINY_TASK_FEEDBACK_TIME", TINY_TASK_FEEDBACK_TIME),
        ("TRAINING_ERINNERUNG_TIME", TRAINING_ERINNERUNG_TIME),
        ("LUECKEN_CHECK_TIME", LUECKEN_CHECK_TIME),
        ("LUECKEN_ABEND_TIME", LUECKEN_ABEND_TIME),
    ):
        # Stunden strikt 00-23: "[0-2]?\d" hätte auch 24-29 akzeptiert und der
        # Crash käme dann doch erst beim Job-Scheduling in post_init.
        if not _re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", wert):
            fehler.append(f"{name} ist kein HH:MM-Zeitformat: {wert!r}")
    if fehler:
        raise SystemExit(
            "Konfigurationsfehler – Bot wird nicht gestartet:\n  - "
            + "\n  - ".join(fehler)
            + "\nBitte die .env vervollständigen."
        )