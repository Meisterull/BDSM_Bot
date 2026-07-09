"""
Test-Harness: simuliert Telegram vollständig (kein Netzwerk zu Telegram),
nutzt aber die ECHTE Handler-Verdrahtung aus bot.main.register_handlers,
die echte (Test-)Qdrant und das echte LLM (Grok via .env).

Alle Ein- und Ausgaben werden in Markdown-Protokolle geschrieben:
  tests/protokolle/domina.md  – alles, was die Domina sendet/sieht
  tests/protokolle/sklave.md  – alles, was der Sklave sendet/sieht
  tests/protokolle/fehler.md  – ungefangene Handler-Fehler (vom error_handler)

Nutzung (im bdsm-bot-test Container, QDRANT_URL zeigt auf qdrant-test):
    from tests.harness import Harness
    h = Harness();  await h.start()
    await h.send("domina", "/hilfe")
    await h.send("sklave", "Hallo")
    await h.press("sklave", "followup:ja")     # Inline-Button per callback_data-Teilstring
    await h.job("followup", followup_job)      # Scheduler-Job manuell auslösen
"""
import asyncio
import itertools
import logging
import os
from datetime import datetime, timezone

from telegram import (
    CallbackQuery, Chat, InlineKeyboardMarkup, Message, MessageEntity, Update, User,
)
from telegram.ext import ApplicationBuilder

from bot import config, state
from bot import main as botmain
from bot.services import persona_config, qdrant

logger = logging.getLogger("tests.harness")

PROTOKOLL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "protokolle")


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


class Transcript:
    """Schreibt pro Rolle eine Markdown-Datei mit allen Ein-/Ausgaben.

    neu=False (Default) hängt an bestehende Protokolle an – so überschreibt ein
    schneller Verifikations-Lauf nicht versehentlich die Protokolle eines
    kompletten Szenario-Laufs. Nur szenario.py startet mit neu=True."""

    def __init__(self, neu: bool = False):
        os.makedirs(PROTOKOLL_DIR, exist_ok=True)
        self.pfade = {
            "domina": os.path.join(PROTOKOLL_DIR, "domina.md"),
            "sklave": os.path.join(PROTOKOLL_DIR, "sklave.md"),
        }
        self.fehler_pfad = os.path.join(PROTOKOLL_DIR, "fehler.md")
        modus = "w" if neu else "a"
        kopf = (f"\n\n# Test-Protokoll – {{rolle}}\n\nGestartet: "
                f"{datetime.now().isoformat(timespec='seconds')}\n")
        for rolle, pfad in self.pfade.items():
            with open(pfad, modus, encoding="utf-8") as f:
                f.write(kopf.format(rolle=rolle.upper()))
        with open(self.fehler_pfad, modus, encoding="utf-8") as f:
            f.write("\n# Ungefangene Handler-Fehler\n")

    def _append(self, rolle: str, text: str) -> None:
        with open(self.pfade[rolle], "a", encoding="utf-8") as f:
            f.write(text)

    def user_in(self, rolle: str, text: str) -> None:
        self._append(rolle, f"\n---\n**[{_now()}] 👤 {rolle.upper()} schreibt:**\n\n{text}\n")

    def button_in(self, rolle: str, label: str, data: str) -> None:
        self._append(rolle, f"\n---\n**[{_now()}] 👤 {rolle.upper()} drückt Button:** `{label}` (`{data}`)\n")

    def bot_out(self, rolle: str, text: str, buttons=None, art: str = "Nachricht") -> None:
        zeile = f"\n**[{_now()}] 🤖 BOT → {rolle.upper()} ({art}):**\n\n{text}\n"
        if buttons:
            zeile += "\nButtons: " + " | ".join(f"`{t_}` (`{d}`)" for t_, d in buttons) + "\n"
        self._append(rolle, zeile)

    def note(self, text: str) -> None:
        for rolle in self.pfade:
            self._append(rolle, f"\n\n## {text}\n")

    def fehler(self, text: str) -> None:
        with open(self.fehler_pfad, "a", encoding="utf-8") as f:
            f.write(f"\n---\n**[{_now()}]**\n```\n{text}\n```\n")


class Harness:
    def __init__(self, neu: bool = False):
        self.transcript = Transcript(neu=neu)
        self._mid = itertools.count(1000)     # message_ids
        self._uid = itertools.count(1)        # update_ids
        self._cqid = itertools.count(1)       # callback_query ids
        # pro Chat: Liste (label, data, message) der zuletzt gesendeten Buttons
        self._buttons: dict[str, list] = {}
        self.fehler_liste: list[str] = []
        self.app = None
        self.bot = None

    # ------------------------------------------------------------------ Setup
    async def start(self) -> None:
        qdrant.ensure_collections()
        self.app = ApplicationBuilder().token("4242:TESTTOKEN").build()
        self.bot = self.app.bot
        self._patch_bot()
        await self.app.initialize()
        botmain.register_handlers(self.app)
        # Fehler zusätzlich für den Testbericht einsammeln (läuft NEBEN dem
        # echten error_handler, der die FEHLER_ALLGEMEIN-Antwort sendet).
        async def _fehler_rec(update, context):
            import traceback
            tb = "".join(traceback.format_exception(
                type(context.error), context.error, context.error.__traceback__))
            self.fehler_liste.append(tb)
            self.transcript.fehler(tb)
        self.app.add_error_handler(_fehler_rec)
        await persona_config.load()
        state.load_persisted()

    def _rolle(self, chat_id) -> str:
        return "domina" if str(chat_id) == str(config.DOMINA_CHAT_ID) else "sklave"

    def _chat_id(self, rolle: str) -> str:
        return config.DOMINA_CHAT_ID if rolle == "domina" else config.SKLAVE_CHAT_ID

    def _patch_bot(self) -> None:
        bot = self.bot
        harness = self

        async def fake_get_me(*a, **k):
            user = User(id=4242, first_name="TestBot", is_bot=True, username="testbot")
            bot._bot_user = user
            return user

        async def fake_send_message(chat_id=None, text=None, *a, **kwargs):
            rolle = harness._rolle(chat_id)
            markup = kwargs.get("reply_markup")
            buttons = harness._extract_buttons(markup)
            msg = Message(
                message_id=next(harness._mid),
                date=datetime.now(timezone.utc),
                chat=Chat(id=int(chat_id), type=Chat.PRIVATE),
                from_user=await fake_get_me(),
                text=str(text),
                reply_markup=markup if isinstance(markup, InlineKeyboardMarkup) else None,
            )
            msg.set_bot(bot)
            if buttons:
                harness._buttons.setdefault(str(chat_id), []).append(
                    [(t_, d, msg) for t_, d in buttons])
            harness.transcript.bot_out(rolle, str(text), buttons)
            return msg

        async def fake_edit_message_text(text=None, chat_id=None, message_id=None, *a, **kwargs):
            rolle = harness._rolle(chat_id)
            buttons = harness._extract_buttons(kwargs.get("reply_markup"))
            harness.transcript.bot_out(rolle, str(text), buttons, art="Edit")
            return True

        async def fake_answer_callback_query(callback_query_id=None, text=None, *a, **kwargs):
            if text:
                # Toast geht an den Drücker des Buttons – wir wissen ihn nicht aus
                # der Signatur; der Aufrufer (press) protokolliert den Kontext.
                harness.transcript.bot_out(harness._letzter_drucker, str(text), art="Toast")
            return True

        async def fake_true(*a, **k):
            return True

        async def fake_copy_message(chat_id=None, from_chat_id=None, message_id=None, *a, **k):
            harness.transcript.bot_out(harness._rolle(chat_id), "[Medien-Weiterleitung]", art="Medien")
            return True

        with bot._unfrozen():
            bot.get_me = fake_get_me
            bot.send_message = fake_send_message
            bot.edit_message_text = fake_edit_message_text
            bot.edit_message_reply_markup = fake_true
            bot.answer_callback_query = fake_answer_callback_query
            bot.send_chat_action = fake_true
            bot.set_my_commands = fake_true
            bot.delete_my_commands = fake_true
            bot.delete_message = fake_true
            bot.copy_message = fake_copy_message
            bot.delete_webhook = fake_true
            bot.initialize = fake_get_me   # kein HTTPX-Init nötig
        self.app._initialized = False  # initialize() in start() setzt es korrekt
        self._letzter_drucker = "domina"

    @staticmethod
    def _extract_buttons(markup):
        if not isinstance(markup, InlineKeyboardMarkup):
            return []
        out = []
        for reihe in markup.inline_keyboard:
            for btn in reihe:
                if btn.callback_data:
                    out.append((btn.text, btn.callback_data))
        return out

    # ------------------------------------------------------------- Eingaben
    def _mk_message(self, chat_id: str, text: str) -> Message:
        entities = []
        if text.startswith("/"):
            cmd = text.split()[0]
            entities = [MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=len(cmd))]
        msg = Message(
            message_id=next(self._mid),
            date=datetime.now(timezone.utc),
            chat=Chat(id=int(chat_id), type=Chat.PRIVATE),
            from_user=User(id=int(chat_id), first_name=self._rolle(chat_id).capitalize(), is_bot=False),
            text=text,
            entities=entities,
        )
        msg.set_bot(self.bot)
        return msg

    async def send(self, rolle: str, text: str, settle: float = 120) -> None:
        """Simuliert eine Text-/Command-Nachricht der Rolle und wartet auf Abschluss."""
        chat_id = self._chat_id(rolle)
        self.transcript.user_in(rolle, text)
        update = Update(update_id=next(self._uid), message=self._mk_message(chat_id, text))
        await self.app.process_update(update)
        await self._settle(settle)

    async def press(self, rolle: str, treffer: str, settle: float = 120) -> bool:
        """Drückt den jüngsten Inline-Button, dessen callback_data ODER Label
        `treffer` enthält. False, wenn kein passender Button existiert."""
        chat_id = self._chat_id(rolle)
        self._letzter_drucker = rolle
        for gruppe in reversed(self._buttons.get(str(chat_id), [])):
            for label, data, msg in gruppe:
                if treffer in data or treffer in label:
                    self.transcript.button_in(rolle, label, data)
                    cq = CallbackQuery(
                        id=str(next(self._cqid)),
                        from_user=User(id=int(chat_id), first_name=rolle.capitalize(), is_bot=False),
                        chat_instance="test",
                        data=data,
                        message=msg,
                    )
                    cq.set_bot(self.bot)
                    update = Update(update_id=next(self._uid), callback_query=cq)
                    await self.app.process_update(update)
                    await self._settle(settle)
                    return True
        self.transcript.note(f"⚠️ HARNESS: Kein Button passend zu '{treffer}' für {rolle} gefunden")
        return False

    async def job(self, name: str, fn, settle: float = 180) -> None:
        """Löst einen Scheduler-Job manuell aus (fn bekommt den Fake-Bot)."""
        self.transcript.note(f"⏰ Scheduler-Job manuell ausgelöst: {name}")
        try:
            await fn(self.bot)
        except Exception:
            import traceback
            tb = traceback.format_exc()
            self.fehler_liste.append(f"Job {name}:\n{tb}")
            self.transcript.fehler(f"Job {name}:\n{tb}")
        await self._settle(settle)

    def note(self, text: str) -> None:
        self.transcript.note(text)

    async def _settle(self, timeout: float) -> None:
        """Wartet, bis alle nebenläufigen Tasks (z.B. create_task-Hintergrund-
        LLM-Calls aus Handlern) abgeschlossen sind."""
        loop = asyncio.get_running_loop()
        ende = loop.time() + timeout
        while loop.time() < ende:
            offen = [t for t in asyncio.all_tasks(loop)
                     if t is not asyncio.current_task() and not t.done()]
            if not offen:
                return
            try:
                await asyncio.wait(offen, timeout=min(5.0, ende - loop.time()))
            except Exception:
                pass
        self.transcript.note("⚠️ HARNESS: _settle Timeout – Hintergrund-Tasks liefen weiter")
