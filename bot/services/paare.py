"""
Paar-Registry – zentrale Auflösung "wer ist wer" (Multiuser-Fundament).

Schritt 1+2 der Migrations-Strategie (TODO.md, Git-Stand 746b20a): statt an
>100 Stellen `chat_id == config.DOMINA_CHAT_ID` zu vergleichen, beantwortet
dieses Modul zentral: Zu welchem Paar gehört diese Chat-ID, und in welcher
Rolle? Callsites werden inkrementell auf `resolve()`/`default_paar()`
migriert; solange nur das Env-Paar existiert, ist das Verhalten identisch.

WICHTIG: Es gibt bewusst noch KEINEN Registrierungs-Flow für weitere Paare.
Die Persistenzschicht (Qdrant-Queries ohne Paar-Filter, `geheimnisse` ohne
user_id, globales Safeword, Scheduler-Jobs) ist noch nicht mandantenfähig –
ein zweites Paar würde heute Daten mischen. Erst wenn diese Schritte (3–6
der Strategie) stehen, bekommt die Registry Persistenz + Pairing-Flow.
"""
import asyncio
import contextlib
import json
import logging
import os
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass

from bot import config

logger = logging.getLogger(__name__)

# Interne Rollen-Keys. Bewusst identisch zu den historischen Qdrant-user_ids
# des Env-Paars ("domina"/"sklave") – Anzeige-Labels kommen aus prompts/rollen.py.
ROLLE_DOM = "domina"
ROLLE_SUB = "sklave"

# Das Env-Paar (DOMINA_CHAT_ID/SKLAVE_CHAT_ID) ist immer Paar "1". Seine
# Qdrant-user_ids bleiben die Legacy-Keys ohne Paar-Präfix – Bestandsdaten!
LEGACY_PAAR_ID = "1"


@dataclass(frozen=True)
class Paar:
    paar_id: str
    dom_chat_id: str
    sub_chat_id: str

    def rolle(self, chat_id) -> str | None:
        """Rolle dieser Chat-ID im Paar ("domina"/"sklave") oder None."""
        cid = str(chat_id)
        if cid == self.dom_chat_id:
            return ROLLE_DOM
        if cid == self.sub_chat_id:
            return ROLLE_SUB
        return None

    def chat_id(self, rolle: str) -> str:
        if rolle == ROLLE_DOM:
            return self.dom_chat_id
        if rolle == ROLLE_SUB:
            return self.sub_chat_id
        raise ValueError(f"Unbekannte Rolle: {rolle!r}")

    def partner_chat_id(self, chat_id) -> str | None:
        """Chat-ID des jeweils anderen Parts (Medien-Weiterleitung etc.)."""
        rolle = self.rolle(chat_id)
        if rolle == ROLLE_DOM:
            return self.sub_chat_id
        if rolle == ROLLE_SUB:
            return self.dom_chat_id
        return None

    def user_id(self, rolle: str) -> str:
        """Mandanten-Key für Qdrant-Payloads (siehe user_id_fuer)."""
        return user_id_fuer(self.paar_id, rolle)


def user_id_fuer(paar_id: str, rolle: str) -> str:
    """Mandanten-Key für Qdrant-Payloads.

    Das Legacy-Paar behält die historischen Keys "domina"/"sklave"
    (alle Bestandsdaten sind so gespeichert, keine Migration nötig);
    künftige Paare bekommen "{paar_id}:{rolle}"."""
    if rolle not in (ROLLE_DOM, ROLLE_SUB):
        raise ValueError(f"Unbekannte Rolle: {rolle!r}")
    if str(paar_id) == LEGACY_PAAR_ID:
        return rolle
    return f"{paar_id}:{rolle}"


# ---------------------------------------------------------------------------
# Paar-Kontext: "für welches Paar arbeitet dieser Codepfad gerade?"
#
# ContextVar statt Funktions-Parameter, damit die vielen synchronen Getter
# (persona_config.bot_name() & Co., aufgerufen tief in Prompt-Buildern ohne
# Paar-Parameter) den richtigen Mandanten sehen, ohne dass jede Signatur
# angefasst werden muss. Gesetzt wird der Kontext an den Eintrittspunkten:
# pro Telegram-Update (main.py, TypeHandler group=-3) und später pro
# Scheduler-Job (Schritt 5). ContextVars sind Task-lokal – das bleibt auch
# mit concurrent_updates (Schritt 7) korrekt.
# ---------------------------------------------------------------------------

_kontext: ContextVar[str] = ContextVar("paar_kontext", default=LEGACY_PAAR_ID)


def aktueller_kontext() -> str:
    """paar_id des aktiven Kontexts (Default: Legacy-/Env-Paar)."""
    return _kontext.get()


def set_kontext(paar_id: str) -> None:
    _kontext.set(str(paar_id))


@contextlib.contextmanager
def kontext(paar_id: str):
    """Scoped-Variante für Jobs/Tools: with paare.kontext(paar.paar_id): ..."""
    token = _kontext.set(str(paar_id))
    try:
        yield
    finally:
        _kontext.reset(token)


def paar_im_kontext() -> Paar:
    """Das Paar des aktiven Kontexts. Zeigt der Kontext auf ein entferntes/
    unbekanntes Paar, wird NIE aufs Env-Paar zurückgefallen (dessen Chats
    bekämen sonst fremde Nachrichten) – lieber laut scheitern, die Aufrufer
    (Jobs, Sender) haben Fangnetze."""
    kontext_id = aktueller_kontext()
    paar = get_paar(kontext_id)
    if paar is not None:
        return paar
    if kontext_id != LEGACY_PAAR_ID:
        raise LookupError(f"Paar {kontext_id!r} ist nicht (mehr) registriert")
    return default_paar()


def dom_chat_id() -> str:
    """Chat-ID der Dom-Seite des Kontext-Paares. Ersatz für das frühere
    config.DOMINA_CHAT_ID in Handlers/Scheduler (Rollen-Guards, State-Keys):
    im Update-/Job-Kontext ist das automatisch das richtige Paar."""
    return paar_im_kontext().dom_chat_id


def sub_chat_id() -> str:
    """Chat-ID der Sub-Seite des Kontext-Paares (s. dom_chat_id)."""
    return paar_im_kontext().sub_chat_id


# ---------------------------------------------------------------------------
# Paar-Locks (Multiuser Schritt 7): mit concurrent_updates laufen Updates
# parallel – die Mode-Maschine (state.get_mode -> Routing -> Mutation) ist
# aber nur innerhalb EINES Paares konsistent, wenn dessen Updates strikt
# nacheinander verarbeitet werden. Verschiedene Paare brauchen keinen
# gemeinsamen Takt: ein 10-20s-Grok-Call eines Paares blockiert die anderen
# nicht mehr (Audit-Befund "kein concurrent_updates").
# ---------------------------------------------------------------------------

_locks: dict[str, asyncio.Lock] = {}


def lock(paar_id: str) -> asyncio.Lock:
    """Das (lazy erzeugte) Lock eines Paares – identisch pro paar_id."""
    return _locks.setdefault(str(paar_id), asyncio.Lock())


def rolle_von_user_id(user_id: str) -> str | None:
    """Rolle aus einem Mandanten-Key: "sklave" (Legacy) und "7:sklave" -> "sklave".

    Für Lookups, die pro ROLLE definiert sind (z.B. erlaubte Profil-Felder in
    qdrant._PROFILE_ALLOWED_FIELDS), während Reads/Writes den vollen
    Mandanten-Key verwenden. None bei unbekanntem Format."""
    rolle = (user_id or "").rsplit(":", 1)[-1]
    return rolle if rolle in (ROLLE_DOM, ROLLE_SUB) else None


# ---------------------------------------------------------------------------
# Registry-Persistenz (Multiuser-Abschluss): weitere Paare + offene Invites
# liegen in config.PAARE_FILE (gemountetes Volume, analog state.json). Das
# Env-Paar bleibt IMMER Paar "1" und steht nie in der Datei.
# ---------------------------------------------------------------------------

_registry: dict | None = None  # {"paare": [...], "invites": {code: {...}}, "naechste_id": int}

# Ohne verwechselbare Zeichen (0/O, 1/I); 8 Stellen ≈ 32^8 – nicht ratbar.
_INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INVITE_CODE_LAENGE = 8


def _lade_registry() -> dict:
    global _registry
    if _registry is None:
        _registry = {"paare": [], "invites": {}, "naechste_id": 2}
        try:
            if os.path.exists(config.PAARE_FILE):
                with open(config.PAARE_FILE, "r", encoding="utf-8") as f:
                    daten = json.load(f)
                for key in ("paare", "invites", "naechste_id"):
                    if key in daten:
                        _registry[key] = daten[key]
                logger.info("Paar-Registry geladen: %d Paare, %d offene Invites",
                            len(_registry["paare"]), len(_registry["invites"]))
        except Exception:
            logger.exception("Paar-Registry nicht ladbar (%s) – starte leer", config.PAARE_FILE)
    return _registry


def _speichere_registry() -> None:
    """Atomar (tmp + rename), wie state._persist_now. Registrierung ist selten –
    synchrones Schreiben ist hier ok."""
    try:
        pfad = config.PAARE_FILE
        os.makedirs(os.path.dirname(pfad) or ".", exist_ok=True)
        tmp = pfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_lade_registry(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, pfad)
    except Exception:
        logger.exception("Paar-Registry nicht speicherbar (%s)", config.PAARE_FILE)


def alle_paare() -> list[Paar]:
    """Alle aktiven Paare: das Env-Paar ("1") + registrierte Paare aus der
    Registry-Datei. Tests können die Env-IDs monkeypatchen (kein Env-Cache)."""
    result = []
    if config.DOMINA_CHAT_ID and config.SKLAVE_CHAT_ID:
        result.append(Paar(
            paar_id=LEGACY_PAAR_ID,
            dom_chat_id=str(config.DOMINA_CHAT_ID),
            sub_chat_id=str(config.SKLAVE_CHAT_ID),
        ))
    for eintrag in _lade_registry()["paare"]:
        if eintrag.get("aktiv", True):
            result.append(Paar(
                paar_id=str(eintrag["paar_id"]),
                dom_chat_id=str(eintrag["dom_chat_id"]),
                sub_chat_id=str(eintrag["sub_chat_id"]),
            ))
    return result


def registriere_paar(dom_chat_id, sub_chat_id) -> Paar:
    """Legt ein neues Paar an (persistiert). Wirft ValueError bei Konflikten."""
    dom, sub = str(dom_chat_id), str(sub_chat_id)
    if dom == sub:
        raise ValueError("Dom- und Sub-Chat dürfen nicht identisch sein")
    for chat in (dom, sub):
        if resolve(chat) is not None:
            raise ValueError(f"Chat {chat} gehört bereits zu einem Paar")
    reg = _lade_registry()
    paar_id = str(reg["naechste_id"])
    reg["naechste_id"] = int(reg["naechste_id"]) + 1
    reg["paare"].append({
        "paar_id": paar_id, "dom_chat_id": dom, "sub_chat_id": sub,
        "aktiv": True, "erstellt_am": time.time(),
    })
    _speichere_registry()
    logger.info("Neues Paar %s registriert (dom=%s, sub=%s)", paar_id, dom, sub)
    return Paar(paar_id=paar_id, dom_chat_id=dom, sub_chat_id=sub)


def entferne_paar(paar_id) -> bool:
    """Entfernt ein registriertes Paar aus der Registry (persistiert).
    Das Env-Paar ist nicht entfernbar (es lebt in der .env, nicht der Datei).
    Qdrant-Daten löscht separat qdrant.loesche_paar_daten – der Aufrufer
    (handlers/admin.py) orchestriert beides + Scheduler-/Cache-Aufräumen."""
    pid = str(paar_id)
    if pid == LEGACY_PAAR_ID:
        raise ValueError("Das Env-Paar kann nicht entfernt werden (DOMINA_CHAT_ID/SKLAVE_CHAT_ID)")
    reg = _lade_registry()
    vorher = len(reg["paare"])
    reg["paare"] = [e for e in reg["paare"] if str(e.get("paar_id")) != pid]
    if len(reg["paare"]) == vorher:
        return False
    _speichere_registry()
    _locks.pop(pid, None)
    logger.info("Paar %s aus der Registry entfernt", pid)
    return True


def _bereinige_invites(reg: dict) -> None:
    ttl = config.INVITE_TTL_STUNDEN * 3600
    abgelaufen = [c for c, i in reg["invites"].items()
                  if time.time() - float(i.get("erstellt_am", 0)) > ttl]
    for code in abgelaufen:
        del reg["invites"][code]


def erstelle_invite(rolle: str, chat_id) -> str:
    """Erzeugt einen Einladungs-Code: Ersteller nimmt `rolle`, wer den Code
    einlöst, wird automatisch die Gegenrolle. Ein Chat hat max. einen offenen
    Invite (neuer ersetzt alten)."""
    if rolle not in (ROLLE_DOM, ROLLE_SUB):
        raise ValueError(f"Unbekannte Rolle: {rolle!r}")
    if resolve(chat_id) is not None:
        raise ValueError("Chat gehört bereits zu einem Paar")
    reg = _lade_registry()
    _bereinige_invites(reg)
    reg["invites"] = {c: i for c, i in reg["invites"].items()
                      if str(i.get("chat_id")) != str(chat_id)}
    code = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(INVITE_CODE_LAENGE))
    reg["invites"][code] = {"chat_id": str(chat_id), "rolle": rolle, "erstellt_am": time.time()}
    _speichere_registry()
    return code


def loese_invite_ein(code: str, partner_chat_id) -> Paar | None:
    """Löst einen Invite-Code ein und registriert das Paar. None bei ungültigem/
    abgelaufenem/eigenem Code oder wenn einer der Chats schon vergeben ist."""
    reg = _lade_registry()
    _bereinige_invites(reg)
    inv = reg["invites"].get((code or "").strip().upper())
    if not inv:
        return None
    if str(inv["chat_id"]) == str(partner_chat_id):
        return None  # eigener Code
    if inv["rolle"] == ROLLE_DOM:
        dom, sub = inv["chat_id"], str(partner_chat_id)
    else:
        dom, sub = str(partner_chat_id), inv["chat_id"]
    try:
        paar = registriere_paar(dom, sub)
    except ValueError:
        logger.warning("Invite-Einlösung abgelehnt (dom=%s, sub=%s)", dom, sub)
        return None
    reg["invites"] = {c: i for c, i in reg["invites"].items()
                      if str(i.get("chat_id")) not in (dom, sub)}
    _speichere_registry()
    return paar


def get_paar(paar_id: str) -> Paar | None:
    for paar in alle_paare():
        if paar.paar_id == str(paar_id):
            return paar
    return None


def resolve(chat_id) -> tuple[Paar, str] | None:
    """Zentrale Auflösung: chat_id -> (Paar, Rolle) oder None (nicht autorisiert)."""
    for paar in alle_paare():
        rolle = paar.rolle(chat_id)
        if rolle:
            return paar, rolle
    return None


def ist_autorisiert(chat_id) -> bool:
    return resolve(chat_id) is not None


def default_paar() -> Paar:
    """Das Env-Paar – Übergangs-API für noch nicht paar-parametrisierte
    Callsites (Scheduler-Jobs, send_domina/send_sklave-Wrapper). Jede neue
    Verwendung ist bewusste Alt-Schuld: bei Mehr-Paar-Betrieb müssen diese
    Stellen auf explizite Paar-Parameter umgestellt sein."""
    paare_liste = alle_paare()
    if not paare_liste:
        raise RuntimeError("Kein Paar konfiguriert (DOMINA_CHAT_ID/SKLAVE_CHAT_ID fehlen)")
    return paare_liste[0]
