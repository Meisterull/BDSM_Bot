FROM python:3.11-slim

# PYTHONUNBUFFERED: Logs sofort flushen (sonst hängen sie im Puffer).
# PYTHONDONTWRITEBYTECODE: keine __pycache__-Schreibversuche (der gemountete ./bot
# kann root-owned .pyc aus früheren Läufen enthalten -> Permission-Fehler als non-root).
# TZ: deutsche Zeit für Log-Timestamps (logging nutzt localtime); tzdata fehlt in slim.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Berlin

# opus-tools: opusenc für Telegram-Voice-Messages (services/tts.py, PCM→OGG/Opus)
RUN apt-get update && apt-get install -y --no-install-recommends tzdata opus-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
# .env wird NICHT ins Image kopiert – docker-compose lädt sie zur Laufzeit via env_file.

# Non-root: UID 1000 = Host-Owner der gemounteten Volumes ./data und ./backups,
# damit State/Backups/Logs schreibbar bleiben.
RUN useradd -u 1000 -m appuser
USER appuser

CMD ["python", "-m", "bot.main"]
