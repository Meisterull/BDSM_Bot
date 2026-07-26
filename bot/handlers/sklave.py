"""
Sklave Handler – normaler Chat (außerhalb Follow-up States).
"""
import difflib
import logging
import re
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from bot import state
from bot.services import qdrant, grok, embeddings as emb, kategorie_logik, telegram_helper, lokal_llm
from bot.prompts import sklave as sklave_prompt
from bot.handlers import onboarding
from bot.messages import t

logger = logging.getLogger(__name__)

_THEMEN = {
    "gefühl": ["fühl", "gefühl", "angst", "freude", "schäm", "stolz", "geil", "erregt", "traurig"],
    "grenze": ["grenze", "limit", "nicht mehr", "zu viel", "unwohl"],
    "wunsch": ["wünsch", "würde gern", "hätte gern", "möchte", "fantasie", "traum"],
    "aufgabe": ["aufgabe", "task", "erledigt", "geschafft"],
    "beziehung": ["liebe", "vertrauen", "nähe", "zusammen", "wir"],
    "alltag": ["arbeit", "müde", "stress", "tag", "heute"],
}


def _themen(text: str) -> list[str]:
    tl = (text or "").lower()
    treffer = [t for t, kws in _THEMEN.items() if any(k in tl for k in kws)]
    return treffer or ["allgemein"]


def _wichtige_punkte(text: str) -> list[str]:
    saetze = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]
    return saetze[:2]


def _normalisiere(text: str) -> str:
    """Kleinschreibung ohne Satzzeichen, Whitespace kollabiert – Basis für den
    Ähnlichkeitsvergleich zweier Antworten."""
    return " ".join(re.sub(r"[^\wäöüß ]", " ", (text or "").lower()).split())


def _ist_echo(antwort: str, vorherige: list[str], schwelle: float = 0.85,
              schluss_schwelle: float = 0.8) -> bool:
    """True, wenn `antwort` eine der letzten Herrin-Antworten fast wortgleich
    wiederholt ODER wieder mit demselben Schlussmotiv endet. Der Ganz-Antwort-
    Vergleich fängt Verbatim-Kopien bei inhaltsarmer Eingabe; der Schluss-Vergleich
    fängt das Template-Muster darunter (Befund 02.07.: vier Antworten in Folge
    endeten wortgleich mit demselben Schlussbild bei nur 0.60–0.69
    Gesamt-Ähnlichkeit). Die eigentliche Bremse (frequency/presence_penalty) ist
    auf grok-4.3 inert."""
    n = _normalisiere(antwort)
    if not n:
        return False
    for v in vorherige:
        vn = _normalisiere(v)
        if not vn:
            continue
        if difflib.SequenceMatcher(None, n, vn).ratio() >= schwelle:
            return True
        if (len(n) >= 45 and len(vn) >= 45
                and difflib.SequenceMatcher(None, n[-45:], vn[-45:]).ratio() >= schluss_schwelle):
            return True
    return False


_FUELLWOERTER = {"aber", "auch", "dann", "denn", "doch", "eher", "sehr", "noch",
                 "heute", "morgen", "wieder", "eine", "einen", "einem", "nicht",
                 "mein", "meine", "dein", "deine", "wird", "habe", "hast", "ist",
                 "jetzt", "bleibt", "wenn", "bist", "dass", "sich", "dich", "dir",
                 "damit", "danach", "spaeter", "später", "lass", "lasse", "immer",
                 "rein", "drin", "raus", "deinem", "deiner", "deinen", "seinem",
                 "seiner", "seinen", "ihrem", "ihrer", "ihren", "etwas", "richtig",
                 "liegt", "liegen", "bleiben", "kommt", "kommen", "machen", "macht"}


def _ist_spiegel_anfang(antwort: str, user_text: str, vorherige: list[str]) -> bool:
    """True, wenn die Antwort (a) mit einem Echo seiner Worte beginnt
    („Gelangweilt ohne X, <Anrede>?" auf „Eher gelangweilt, es fehlt
    X") oder (b) denselben Antwort-Anfang wie eine der letzten Antworten
    recycelt („X also, <Anrede>? Dann …"-Template). Live-Befund 04.07.:
    JEDE Antwort öffnete mit Spiegel + Anrede – FÜHREN STATT SPIEGELN als reine
    Prompt-Regel verliert gegen eine History voller Gegenbeispiele."""
    a_woerter = _normalisiere(antwort).split()
    if not a_woerter:
        return False
    anfang = a_woerter[:7]

    # (a) ≥2 Inhaltswörter aus SEINER Nachricht in den ersten 7 Wörtern
    user_woerter = {w for w in _normalisiere(user_text).split()
                    if len(w) > 3 and w not in _FUELLWOERTER}
    if len([w for w in anfang if w in user_woerter]) >= 2:
        return True

    # (b) Anfangs-Template-Recycling gegen die letzten Antworten
    a_anfang = " ".join(anfang)
    for v in vorherige:
        v_anfang = " ".join(_normalisiere(v).split()[:7])
        if v_anfang and difflib.SequenceMatcher(None, a_anfang, v_anfang).ratio() >= 0.72:
            return True
    return False


def _wiederholte_phrase(antwort: str, vorherige: list[str], n: int = 5) -> bool:
    """True, wenn die Antwort eine identische n-Wort-Sequenz mit einer der
    letzten Antworten teilt – an BELIEBIGER Position (Live-Befund Runde 3:
    Binnen-Phrasen wie 'interessiert mich wirklich wie es dir geht' und
    'bis ich entscheide ob dein arsch…' wurden wortgleich recycelt; Anfangs-/
    End-Checks sehen die nicht)."""
    woerter = _normalisiere(antwort).split()
    if len(woerter) < n:
        return False
    eigene = {" ".join(woerter[i:i + n]) for i in range(len(woerter) - n + 1)}
    for v in vorherige:
        vw = _normalisiere(v).split()
        for i in range(len(vw) - n + 1):
            if " ".join(vw[i:i + n]) in eigene:
                return True
    return False


_FRAGE_WOERTER = {"was", "wie", "warum", "wieso", "wann", "wo", "wer", "womit",
                  "kannst", "willst", "darf", "magst", "wirst", "erzähl", "erzaehl"}


def _ist_frage(text: str) -> bool:
    """Grobe Frage-Erkennung: seine direkte Frage darf nicht von einem
    Regie-Impuls überfahren werden (Live-Befund Runde 3: 'was könnte denn
    passieren?' bekam eine Gegenfrage nach seinem Befinden)."""
    t = (text or "").strip().lower()
    if not t:
        return False
    if t.endswith("?"):
        return True
    woerter = t.split()
    return bool(woerter) and woerter[0] in _FRAGE_WOERTER


_WUNSCH_MARKER = (
    "ich will", "will ich", "ich möchte", "ich moechte", "ich wünsch", "ich wuensch",
    "gib mir", "ich brauch", "benötige", "benoetige", "hätte gern", "haette gern",
    "lust auf", "mehr davon", "noch mehr", "will mehr", "mach weiter",
    "hör nicht auf", "hoer nicht auf",
)


def _ist_wunsch_aeusserung(text: str) -> bool:
    """Explizite Wunsch-/Inhaltsäußerung ('Ich will …', 'gib mir …'): darf wie
    eine direkte Frage nicht vom Regie-Impuls überfahren werden (Live-Befund
    15.07.: eine kurze 'Ich will …'-Wunschäußerung war ≤40 Zeichen → knapp →
    Neugier-Regie 'Frag nach seinem Tag' – Non-Sequitur mitten in der Szene)."""
    t = (text or "").strip().lower()
    return any(m in t for m in _WUNSCH_MARKER)


_LANGEWEILE_MARKER = (
    "langweilig", "langeweile", "was neues", "etwas neues", "mal was anderes",
    "immer das gleiche", "immer dasselbe", "abwechslung", "eintönig", "eintoenig",
)


def _ist_langeweile_signal(text: str) -> bool:
    """Er signalisiert Langeweile / will etwas Neues (Live-Befund 15.07.: die
    Eröffnung 'es wird langweilig, wie wäre es mit was Neuem' bekam Standard-
    Repertoire als Antwort). Löst eine Neuheits-Regie aus und erzwingt die
    volle Kontext-Injektion (entdeckte Wünsche etc.)."""
    t = (text or "").strip().lower()
    return any(m in t for m in _LANGEWEILE_MARKER)


def _dauermotive(vorherige: list[str], user_text: str, anrede: str = "") -> list[str]:
    """Wörter, die in ≥3 der letzten 4 Herrin-Antworten vorkommen (Dauermotiv,
    Live-Befund 04.07.: dieselben Requisiten-/Körper-Motive in praktisch
    jeder Antwort). Nur
    gesperrt, wenn SEINE aktuelle Nachricht sie nicht selbst anspricht – eine
    direkte Frage zum Motiv darf beantwortet werden. Max. 3, häufigste zuerst."""
    if len(vorherige) < 3:
        return []
    user_woerter = set(_normalisiere(user_text).split())
    anrede_woerter = set(_normalisiere(anrede).split())
    zaehler: dict[str, int] = {}
    for v in vorherige:
        for w in set(_normalisiere(v).split()):
            if len(w) > 3 and w not in _FUELLWOERTER and w not in anrede_woerter:
                zaehler[w] = zaehler.get(w, 0) + 1
    kandidaten = [w for w, n in zaehler.items() if n >= 3 and w not in user_woerter]
    # Tie-Break alphabetisch – sonst entscheidet die Set-Hash-Reihenfolge (flaky)
    return sorted(kandidaten, key=lambda w: (-zaehler[w], w))[:3]


def _rotierende_auswahl(items: list, k: int, offset: int) -> list:
    """Rotierende Teilmenge (max k Elemente). Variiert über die Turns hinweg, welche
    Vorlieben/Motive dem Prompt vorne stehen, statt immer dieselben Top-Einträge –
    das bremst die thematische Verengung auf ein Dauer-Motiv."""
    items = list(items or [])
    if len(items) <= k:
        return items
    start = offset % len(items)
    return [items[(start + i) % len(items)] for i in range(k)]


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()

    # Onboarding prüfen
    if await onboarding.start_if_needed(update, context, "sklave"):
        await onboarding.handle(update, context, "sklave")
        return

    if state.get_mode(chat_id) == "onboarding":
        return

    # Profil laden (Sklave + Domina – damit die Bot-Herrin auch die Grenzen
    # ihrer "echten" Person-Vorlage respektiert)
    profile = await qdrant.get_user_profile("sklave") or {}
    domina_profile = await qdrant.get_user_profile("domina") or {}

    # Offene Aufgaben laden
    offene = await qdrant.get_tasks_by_status(["offen", "gefragt"])
    offene_str = "\n".join(f"- {t.get('aufgabe', '')}" for t in offene) or "Keine offenen Aufgaben."
    offene_anzahl = len(offene)

    # Vergangene Gespräche als Erinnerungs-Kontext (hybrid, nur wenn Nachricht inhaltsreich ist)
    erinnerungs_kontext = ""
    query_vector = None
    if len(text) > 10:
        try:
            query_vector = await emb.get_embedding(text)
            entries = await qdrant.get_hybrid_conversation_context("sklave", query_vector, limit=6)
            if entries:
                lines = []
                for e in entries[:6]:
                    datum = (e.get("datum") or "")[:10]
                    z = e.get("zusammenfassung", "")[:300]
                    if z:
                        lines.append(f"[{datum}] {z}")
                if lines:
                    erinnerungs_kontext = "\n\nFrühere Gespräche mit ihm (als Kontext, nicht direkt zitieren):\n" + "\n".join(lines)
        except Exception as e:
            logger.error("Fehler beim Laden des Erinnerungs-Kontextes (Sklave): %s", e)

    # Gelerntes Wissen über ihn zusammenstellen, damit die Herrin ihn spürbar kennt
    mag = kategorie_logik.top_kategorien(profile)
    dislike = kategorie_logik.dislike_kategorien(profile)
    wunsch = profile.get("wunsch_kategorien", []) or []
    # Intensitäts-Level für mag/wunsch-Kategorien
    level_quellen = list(dict.fromkeys((mag or []) + (wunsch or [])))
    intensitaet_hinweis = ", ".join(
        f"{k}: {kategorie_logik.level_label(kategorie_logik.kategorie_level(profile, k))}"
        for k in level_quellen[:5]
    )
    # Letzte Gefühle (echte Worte) als emotionales Kennenlern-Material
    letzte_gefuehle = []
    try:
        erledigt = await qdrant.get_tasks_by_status(["erledigt"], sort_by_datum=True)
        for task in erledigt[:3]:
            g = (task.get("gefuehl") or "").strip()
            if g:
                kat = task.get("kategorie", "")
                letzte_gefuehle.append(f"{kat}: {g[:70]}" if kat else g[:70])
    except Exception as e:
        logger.error("Fehler beim Laden letzter Gefühle (Sklave): %s", e)
    stimmung_entry = await qdrant.get_latest_stimmung("sklave")
    stimmung = stimmung_entry.get("zusammenfassung", "") if stimmung_entry else ""

    # Anti-Verengung: Bei kurzen Alltags-Check-ins ("läuft so", "stressig") nicht
    # jedes Mal das volle Vorlieben-/Dossier-Paket vorladen – das drückt die Herrin
    # sonst immer in dieselben zwei Dauer-Motive. Stattdessen eine rotierende
    # Teilmenge, damit sie mal dies, mal jenes aufgreift. Inhaltsreiche Nachrichten
    # (echte Wünsche/Rückmeldungen) bekommen weiter den vollen Kontext.
    langeweile = _ist_langeweile_signal(text)
    # Bei Langeweile-Signal nie den Knapp-Sparmodus: gerade dann braucht der
    # Prompt die volle Injektion (entdeckte Wünsche, Dossier) für etwas Neues.
    knapp = len(text) <= 40 and not langeweile
    turn_offset = sum(1 for m in state.get_history(chat_id) if m.get("role") == "assistant")
    if knapp:
        vorlieben_inj = _rotierende_auswahl(profile.get("vorlieben", []), 4, turn_offset)
        mag_inj = _rotierende_auswahl(mag, 3, turn_offset)
        dossier_inj = ""
        entdeckte_inj = []
    else:
        vorlieben_inj = profile.get("vorlieben", [])
        mag_inj = mag
        dossier_inj = profile.get("dossier", "")
        entdeckte_inj = profile.get("entdeckte_wuensche", [])

    # Regie-Impuls bei knappen Check-ins: rotiert deterministisch das Register
    # (Neugier/Anweisung/Faden/Necken/Fürsorge/Vorfreude), damit „läuft so"-Inputs
    # nicht immer in denselben zwei Dauer-Motiven landen (Live-Befund 04.07.).
    _IMPULSE = (
        "Stell ihm EINE konkrete Frage zu seinem Tag oder Zustand, die NICHTS mit Sex zu tun hat – echte Neugier zuerst, Dominanz danach.",
        "Gib ihm eine kleine, sofort machbare Anweisung aus einem Bereich, der in den letzten Antworten NICHT vorkam.",
        "Greif einen offenen Faden oder etwas auf, das er früher erzählt hat – zeig, dass du dir merkst, was er sagt.",
        "Necke ihn mit einer Beobachtung über IHN – ohne Befehl, nur Präsenz und Schmunzeln.",
        "Zeig kurz die fürsorgliche Seite deiner Kontrolle: wie es ihm WIRKLICH geht, interessiert dich – dann erst wieder Herrin.",
        "Bau Vorfreude auf später auf: kündige an, dass etwas kommt, ohne zu verraten was.",
    )
    # Keine Regie, wenn er eine direkte Frage stellt oder einen Wunsch äußert –
    # erst DARAUF eingehen (Live-Befund 15.07.: Wunschäußerung bekam Smalltalk-Regie).
    regie = ""
    if knapp and not _ist_frage(text) and not _ist_wunsch_aeusserung(text):
        regie = f"\n\nREGIE FÜR GENAU DIESE ANTWORT: {_IMPULSE[turn_offset % len(_IMPULSE)]}"
    if langeweile:
        regie = (
            "\n\nLANGEWEILE-SIGNAL: Er sagt gerade, dass ihm langweilig ist bzw. er etwas "
            "Neues will. Nimm das ernst: bedien dich NICHT beim Standard-Repertoire und "
            "nicht bei Motiven aus deinen letzten Antworten. Schlag EINE konkrete Sache "
            "vor, die für euch neu ist – ein noch unerfüllter entdeckter Wunsch, eine "
            "selten bediente Vorliebe oder eine neue Variante/Kombination – und benenne "
            "konkret, was daran heute anders ist als sonst."
        )

    system = sklave_prompt.get(
        hard_limits=profile.get("hard_limits", []),
        vorlieben=vorlieben_inj,
        offene_aufgaben=offene_str,
        offene_anzahl=offene_anzahl,
        domina_grenzen=domina_profile.get("grenzen", []),
        persoenlichkeit_tags=profile.get("persoenlichkeit_tags", []),
        mag_kategorien=mag_inj,
        dislike_kategorien=dislike,
        wunsch_kategorien=wunsch,
        intensitaet_hinweis=intensitaet_hinweis,
        letzte_gefuehle=letzte_gefuehle,
        stimmung=stimmung,
        streak=profile.get("streak", 0),
        punkte=profile.get("punkte", 0),
        dossier=dossier_inj,
        offene_faeden=profile.get("offene_faeden", []),
        entdeckte_wuensche=entdeckte_inj,
    ) + erinnerungs_kontext + regie

    # Abwesenheit als harter Fakt (nicht über Retrieval-Zufall): die Herrin muss
    # wissen, wann er weg ist und ab wann wieder da (/abwesend).
    from bot.services import persona_config
    system += persona_config.abwesenheit_hinweis()

    # Verbrauchte Schluss-Bilder der letzten Antworten explizit sperren – die
    # generische WORTVIELFALT-Regel allein verliert gegen eine History voller
    # Beispiele desselben Templates (Befund 02.07.: 7 von 15 Antworten endeten
    # mit demselben Motiv).
    letzte_schluesse = []
    letzte_anfaenge = []
    for a in [m["content"] for m in state.get_history(chat_id) if m.get("role") == "assistant"][-4:]:
        saetze = [x.strip() for x in re.split(r"(?<=[.!?])\s+", a) if x.strip()]
        if saetze:
            letzte_schluesse.append(saetze[-1])
            letzte_anfaenge.append(saetze[0][:80])
    if letzte_schluesse:
        system += (
            "\n\nVERBRAUCHTE SCHLUSS-BILDER (so endeten deine letzten Antworten – beende diese "
            "Antwort mit einem ANDEREN Motiv und verwende keines dieser Bilder erneut):\n"
            + "\n".join(f"- {s}" for s in dict.fromkeys(letzte_schluesse))
        )
    if letzte_anfaenge:
        system += (
            "\n\nVERBRAUCHTE ANFÄNGE (so begannen deine letzten Antworten – beginne diese Antwort "
            "STRUKTURELL anders: nicht mit seinen Worten, nicht mit der Anrede, nicht mit "
            "'X also…?'):\n"
            + "\n".join(f"- {s}" for s in dict.fromkeys(letzte_anfaenge))
        )

    # Dauermotiv-Bremse: Motive, die in fast jeder letzten Antwort vorkamen, sind
    # für DIESE Antwort tabu (außer er spricht sie selbst an) – bricht das
    # Ein-Thema-Loch, das History-Anker + offene Aufgabe zusammen erzeugen.
    from bot.services import persona_config as _pc
    motive = _dauermotive(
        [m["content"] for m in state.get_history(chat_id) if m.get("role") == "assistant"][-4:],
        text, anrede=_pc.sklave_anrede(),
    )
    if motive:
        system += (
            f"\n\nDAUERMOTIV-BREMSE: Diese Motive hast du in fast jeder deiner letzten Antworten "
            f"benutzt: {', '.join(motive)}. In DIESER Antwort sind sie TABU – auch nicht umschrieben "
            f"oder als Nebensatz. Nimm einen völlig anderen Aufhänger aus dem, was du über ihn weißt "
            f"oder was er gerade erzählt."
        )

    # Aktuelle Nachricht zur History hinzufügen
    state.add_message(chat_id, "user", text)

    # Grok mit echter Message History aufrufen
    history = state.get_history(chat_id)
    try:
        async with telegram_helper.typing_action(context.bot, chat_id):
            response = await grok.chat(
                system, history,
                # grok-4.3 lehnt frequency/presence_penalty mit HTTP 400 ab
                # (GROK_SUPPORTS_PENALTIES=0 → werden in grok.chat verworfen). Bis ein
                # Modell sie kann bleiben sie als Absicht stehen; der wirksame Hebel
                # gegen Gleichförmigkeit ist hier die erhöhte Temperatur.
                temperature=0.9,
                frequency_penalty=0.5,   # bestraft wortgleiche Phrasen-Wiederholung (inert auf grok-4.3)
                presence_penalty=0.3,    # schiebt sanft Richtung neuer Begriffe (inert auf grok-4.3)
            )
    except Exception as e:
        logger.error("Grok-Ausfall im Sklave-Handler: %s", e)
        # Notbetrieb: lokales Modell mit Kurz-Prompt (voller Prompt wäre auf CPU
        # unbrauchbar langsam, siehe lokal_llm.py). Erst wenn auch das scheitert,
        # die statische Fallback-Nachricht.
        response = ""
        if lokal_llm.aktiv():
            try:
                async with telegram_helper.typing_action(context.bot, chat_id):
                    response = (await lokal_llm.chat_kurz(
                        sklave_prompt.get_kurz(
                            profile.get("hard_limits", []),
                            domina_profile.get("grenzen", []),
                        ),
                        lokal_llm.kuerze_history(history),
                    )).strip()
            except Exception as e2:
                logger.error("Lokales Fallback-Modell ebenfalls fehlgeschlagen: %s", e2)
        if not response:
            # User-Nachricht aus History entfernen bei Fehler
            state.remove_last_message(chat_id)
            await update.message.reply_text(t("FALLBACK_SKLAVE_CHAT"))
            return
        # Antwort direkt ausliefern und hier enden: Anti-Echo-Neugenerierung und
        # das Post-Processing (Wunsch-Erfassung, Präferenz-Detektor, Domina-Relay)
        # brauchen alle Grok und würden nur Retries verbrennen. Die Qdrant-
        # Persistenz dieses einen Notbetrieb-Austauschs opfern wir bewusst mit.
        state.add_message(chat_id, "assistant", response)
        await update.message.reply_text(response)
        return

    # Anti-Echo: Hat grok fast wortgleich eine frühere Antwort wiederholt (typisch
    # bei inhaltsarmer Eingabe, weil dann der letzte Assistant-Turn kopiert wird),
    # einmal mit klarer Ansage + höherer Temperatur neu generieren. `history` enthält
    # hier die aktuelle User-Nachricht, aber noch NICHT die neue Antwort.
    letzte_antworten = [m["content"] for m in history if m.get("role") == "assistant"][-6:]
    if (_ist_echo(response, letzte_antworten)
            or _ist_spiegel_anfang(response, text, letzte_antworten)
            or _wiederholte_phrase(response, letzte_antworten)):
        logger.info("Sklave-Antwort war Echo/Spiegel-Anfang – generiere einmal neu.")
        try:
            async with telegram_helper.typing_action(context.bot, chat_id):
                response = await grok.chat(
                    system + "\n\nACHTUNG: Deine letzte Antwort hat gespiegelt oder sich wiederholt "
                    "(seine Worte zurückgegeben, mit demselben Muster begonnen oder mit demselben "
                    "Schlussmotiv geendet). Formulier jetzt völlig neu – beginne NICHT mit seinen "
                    "Worten und NICHT wie deine letzten Antworten. Führ das Gespräch: eigene "
                    "Beobachtung, neue Anweisung, echte Frage oder ein anderes Thema. "
                    "Wiederhole keinen deiner vorherigen Sätze und kein früheres Schlussbild.",
                    history,
                    temperature=1.1,
                )
        except Exception as e:
            logger.error("Anti-Echo-Neugenerierung fehlgeschlagen: %s", e)

    # Antwort zur History hinzufügen
    state.add_message(chat_id, "assistant", response)

    await update.message.reply_text(response)

    # Geäußerte Wünsche / "würde gern mal ausprobieren" aus dem Chat dauerhaft aufnehmen
    # (gated über Signalwörter, hard-limit-gefiltert). Best-effort, nie den Chat blockieren.
    if len(text) > 6:
        try:
            from bot.handlers import dossier as _dossier
            neuer_wunsch = await _dossier.erfasse_wunsch_aus_chat(text)
            if neuer_wunsch:
                logger.debug("Neuer entdeckter Wunsch aufgenommen: %s", neuer_wunsch)
        except Exception as e:
            logger.error("Wunsch-Erfassung fehlgeschlagen: %s", e)

    # Vorlieben / No-Gos aus dem Gespräch erkennen und dem Sklaven als ✅/🗑-Vorschlag
    # fürs eigene Profil anbieten (best-effort, gated, blockiert den Chat nie).
    try:
        from bot.services import praeferenz_detektor
        await praeferenz_detektor.erkenne_und_schlage_vor(context.bot, "sklave", text)
    except Exception as e:
        logger.error("Präferenz-Detektor (Sklave) fehlgeschlagen: %s", e)

    # Will er, dass seine echte Domina etwas erfährt? Dann nicht roh durchreichen,
    # sondern via Grok als Coach-Hinweis an die Domina (best-effort, blockiert nie).
    try:
        from bot.services import domina_relay
        await domina_relay.pruefe_und_leite_weiter(context.bot, text)
    except Exception as e:
        logger.error("Domina-Hinweis-Weiterleitung fehlgeschlagen: %s", e)

    # Konversation persistieren, damit die Herrin in zukünftigen Sessions Kontinuität zeigen kann.
    # Nur wenn der Sklave inhaltlich etwas gesagt hat (kein 'ja'/'ok' o.ä.) – sonst bläht das die DB auf.
    # Bewusst OHNE query_vector-Gate (Review D8/M12): save_conversation embeddet
    # selbst – ein Embedder-Schluckauf beim Kontext-Laden oben darf nicht still
    # den kompletten Austausch aus dem Langzeit-Gedächtnis kosten.
    if len(text) > 10:
        try:
            session_id = f"sklave_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            # Zusammenfassung nur fürs Embedding/Preview; voller Wortlaut s.u. in den Feldern.
            zusammenfassung = f"Sklave: {text[:2000]}\nHerrin: {response[:2000]}"
            themen = _themen(text + " " + response)
            await qdrant.save_conversation("sklave", session_id, {
                "zusammenfassung": zusammenfassung,
                "wichtige_punkte": _wichtige_punkte(text),
                "themen": themen,
                "thema": themen[0] if themen else "allgemein",
                "sklave_nachricht": text,      # vollständig speichern (kein Abschneiden)
                "herrin_antwort": response,    # vollständig speichern (kein Abschneiden)
            })
        except Exception as e:
            logger.error("Fehler beim Speichern der Sklave-Konversation: %s", e)
