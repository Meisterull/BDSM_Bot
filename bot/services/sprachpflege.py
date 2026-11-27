"""
Feste Korrekturen an ausgehenden Texten – nur für Fehler, die das Modell
nachweislich immer wieder macht und die sich eindeutig beheben lassen.

Log-Durchsicht 27.11.2026: „den Paddle“ stand 16-mal in gesendeten Nachrichten
(richtig: das Paddle, 83-mal). Das Inventar nennt den Gegenstand ohne Artikel,
das Modell rät dann ab und zu das Maskulinum. Angewandt in app/routing (jede
Textnachricht) und vor der Sprachausgabe (telegram_helper.voice_an).
"""
import re

# Akkusativ Maskulinum vor dem Neutrum „Paddle“ → Neutrum. Nicht bei „Paddles“
# (Plural) und nicht vor einem Bindestrich („mit den Paddle-Schlägen“ ist richtig).
_PADDLE_RE = re.compile(r"\b([Dd]en|[Ee]inen|[Kk]einen|[Dd]einen|[Ss]einen|[Mm]einen|[Dd]iesen)(\s+)Paddle(?![\w-])")
_NEUTRUM = {"den": "das", "einen": "ein", "keinen": "kein", "deinen": "dein", "seinen": "sein",
            "meinen": "mein", "diesen": "dieses"}


def _neutrum(m: re.Match) -> str:
    wort = m.group(1)
    neu = _NEUTRUM[wort.lower()]
    if wort[0].isupper():
        neu = neu[0].upper() + neu[1:]
    return f"{neu}{m.group(2)}Paddle"


def korrigieren(text):
    """Gibt den korrigierten Text zurück; alles, was kein str ist, unverändert."""
    if not isinstance(text, str) or "Paddle" not in text:
        return text
    return _PADDLE_RE.sub(_neutrum, text)
