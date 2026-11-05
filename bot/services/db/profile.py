"""
user_profiles: Lesen, Upsert, Feld-Patches, Limit-Pflege, Auto-Updater-Patches.

Aus bot/services/qdrant.py herausgelöst (Wartbarkeits-Umbau 05.11.2026).
Öffentlicher Zugriff weiterhin über die Fassade bot.services.qdrant.
"""
import logging
import uuid
from typing import Optional
from qdrant_client import models as qm
from bot.services import embeddings as emb
from bot.services import paare
from bot.services.db import kern

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# user_profiles
# ---------------------------------------------------------------------------

async def get_user_profile(user_id: str) -> Optional[dict]:
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="user_profiles",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id)))
        ]),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    return results[0].payload if results else None


async def upsert_user_profile(user_id: str, data: dict) -> str:
    """Merge-Semantik statt Full-Replace (D9/N19): bestehende Felder, die
    `data` nicht nennt, bleiben erhalten. Vorher überschrieb der Onboarding-
    Abschluss alles, was VOR dem Abschluss schon am Profil-Punkt hing
    (z. B. Patches von lock-freien Scheduler-Jobs/Detektoren)."""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="user_profiles",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id)))
        ]),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    bestehend = (results[0].payload or {}) if results else {}
    # Neuanlage mit DETERMINISTISCHER Punkt-ID (Review D8/M13): Scheduler-Jobs
    # laufen nicht unter dem Paar-Lock – legen Job und Handler ein noch fehlendes
    # Profil parallel an, kollidieren beide Upserts jetzt idempotent auf derselben
    # ID statt zwei Punkte mit gleichem user_id zu erzeugen (get_user_profile
    # limit=1 lieferte danach zufällig einen von beiden).
    point_id = (str(results[0].id) if results
                else str(uuid.uuid5(uuid.NAMESPACE_URL, f"user_profile:{kern.mandanten_key(user_id)}")))

    merged = {**bestehend, **data, "user_id": kern.mandanten_key(user_id)}

    # Nur semantisch relevante Felder embedden — verhindert nutzlose Vektoren aus Zahlen/Listen
    embed_data = {k: v for k, v in merged.items() if k in _PROFILE_EMBED_FIELDS and v}
    profile_text = " ".join(str(v) for v in embed_data.values()) if embed_data else f"{user_id} profile"
    vector = await emb.get_embedding(profile_text)

    await kern._aio(kern.client.upsert,
        collection_name="user_profiles",
        points=[qm.PointStruct(
            id=point_id,
            vector={"text": vector},
            payload=merged,
        )],
    )
    return point_id


async def _reembed_profile_vector(point_id: str, payload_nach_update: dict) -> None:
    """Profil-Vektor nach Feld-Änderung neu berechnen und GEZIELT schreiben
    (update_vectors). Bewusst kein Full-Upsert: zwischen Payload-Read und Write
    läge sonst der Embedding-HTTP-Call, in dem parallel via set_payload gepatchte
    Felder (punkte/streak) verloren gingen (Lost Update)."""
    embed_data = {k: v for k, v in payload_nach_update.items() if k in _PROFILE_EMBED_FIELDS and v}
    profile_text = (" ".join(str(v) for v in embed_data.values()) if embed_data
                    else f"{payload_nach_update.get('user_id', '?')} profile")
    vector = await emb.get_embedding(profile_text)
    await kern._aio(kern.client.update_vectors,
        collection_name="user_profiles",
        points=[qm.PointVectors(id=point_id, vector={"text": vector})],
    )


async def patch_profile_fields(user_id: str, fields: dict, erlaube_geschuetzt: bool = False) -> str:
    """Atomares Update einzelner Profil-Felder ohne Full-Overwrite (Race-Condition-safe).
    Nutzt Qdrant set_payload statt upsert mit vollem Profil.

    erlaube_geschuetzt=True NUR für den manuellen /profil-Edit des Owners –
    automatische Schreiber (Detektor, Jobs, Patches) lassen den Default stehen."""
    # Geschützte Felder niemals automatisch patchen
    protected = _PROFILE_PROTECTED_FIELDS & set(fields.keys())
    if protected and not erlaube_geschuetzt:
        logger.warning("patch_profile_fields: Versuch geschützte Felder zu ändern: %s — ignoriert", protected)
        fields = {k: v for k, v in fields.items() if k not in _PROFILE_PROTECTED_FIELDS}
    if not fields:
        return ""
    results, _ = await kern._aio(kern.client.scroll,
        collection_name="user_profiles",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id)))
        ]),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    if not results:
        # Kein Profil vorhanden → normal upsert
        return await upsert_user_profile(user_id, {"user_id": kern.mandanten_key(user_id), **fields})

    point_id = str(results[0].id)
    payload = results[0].payload or {}

    # Re-Embed-Trigger = exakt die Embed-Felder – früher stand hier eine eigene
    # (unvollständige) Teilmenge, wodurch Patches von ziele/erfahrungsstand/
    # wunsch_kategorien den Profil-Vektor stale ließen.
    needs_reembed = any(k in _PROFILE_EMBED_FIELDS for k in fields)

    # Felder IMMER gezielt patchen – nie Full-Upsert mit dem stale Read von oben.
    await kern._aio(kern.client.set_payload,
        collection_name="user_profiles",
        payload=fields,
        points=[point_id],
    )
    if needs_reembed:
        # Re-embed mit aktualisiertem Profil – NUR die semantischen Felder (wie
        # upsert_user_profile), sonst verrauschen Punkte/IDs den Profil-Vektor.
        await _reembed_profile_vector(point_id, {**payload, **fields})
    return point_id


# Felder, die der Auto-Updater anfassen darf – Hard Limits & kinderfreie Zeiten sind tabu.
_PROFILE_ALLOWED_FIELDS = {
    "domina": {
        "list_add":    {"interessen"},
        "text_replace": {"ziele", "erfahrungsstand"},
    },
    "sklave": {
        "list_add":    {"vorlieben", "wunsch_kategorien", "persoenlichkeit_tags"},
        "text_replace": {"erfahrungsstand"},
    },
}


# Sicherheits-Grenzen-Feld pro Rolle. Diese Felder dürfen NUR additiv (mehr Grenzen =
# strenger = sicher) und NUR über einen explizit bestätigten User-Flow geschrieben
# werden (append_profile_limits / apply_profile_patch-Operation "limit_add").
# Der stille Auto-Updater fasst sie nie an – hard_limits ist zusätzlich in
# _PROFILE_PROTECTED_FIELDS gegen patch_profile_fields gesperrt.
_PROFILE_LIMIT_FIELDS = {
    "sklave": "hard_limits",
    "domina": "grenzen",
}


async def append_profile_limits(user_id: str, feld: str, werte: list[str]) -> list[str]:
    """Fügt Sicherheits-Grenzen ADD-ONLY hinzu – niemals entfernen.

    Bewusst der EINZIGE Pfad, der das geschützte Feld hard_limits schreiben darf, und
    auch nur additiv. Nur aus einem expliziten, vom User bestätigten Flow aufrufen
    (nie aus dem stillen Auto-Updater). Gibt die NEU hinzugefügten Werte zurück."""
    if feld not in _PROFILE_LIMIT_FIELDS.values():
        logger.warning("append_profile_limits: unerlaubtes Feld %s – ignoriert", feld)
        return []
    if isinstance(werte, str):
        werte = [werte]
    werte = [w.strip() for w in (werte or []) if isinstance(w, str) and w.strip()]
    if not werte:
        return []

    results, _ = await kern._aio(kern.client.scroll,
        collection_name="user_profiles",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id)))
        ]),
        limit=1, with_payload=True, with_vectors=False,
    )
    if results:
        point_id = str(results[0].id)
        payload = results[0].payload or {}
    else:
        # Deterministische ID wie upsert_user_profile (Review D8/M13) – parallele
        # Erstanlagen kollidieren idempotent statt Duplikate zu erzeugen.
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"user_profile:{kern.mandanten_key(user_id)}"))
        payload = {"user_id": kern.mandanten_key(user_id)}

    bestand = payload.get(feld) or []
    if not isinstance(bestand, list):
        bestand = []
    # Auch INNERHALB der Eingabe und case-insensitiv dedupen (D9/N17): ein
    # Patch mit „Nadeln" zweimal bzw. „Anal"/„anal" erzeugte sonst Duplikate.
    gesehen = {b.casefold() for b in bestand if isinstance(b, str)}
    neue: list[str] = []
    for w in werte:
        if w.casefold() not in gesehen:
            neue.append(w)
            gesehen.add(w.casefold())
    if not neue:
        return []

    updated = {**payload, "user_id": kern.mandanten_key(user_id), feld: bestand + neue}
    if results:
        # Gezielt patchen + Vektor separat – kein Full-Upsert (Lost Update, s. _reembed_profile_vector).
        await kern._aio(kern.client.set_payload,
            collection_name="user_profiles",
            payload={feld: bestand + neue},
            points=[point_id],
        )
        await _reembed_profile_vector(point_id, updated)
    else:
        # Noch kein Profil-Punkt vorhanden → Neuanlage per Upsert.
        embed_data = {k: v for k, v in updated.items() if k in _PROFILE_EMBED_FIELDS and v}
        profile_text = " ".join(str(v) for v in embed_data.values()) if embed_data else f"{user_id} profile"
        vector = await emb.get_embedding(profile_text)
        await kern._aio(kern.client.upsert,
            collection_name="user_profiles",
            points=[qm.PointStruct(id=point_id, vector={"text": vector}, payload=updated)],
        )
    logger.info("Grenzen ergänzt (%s.%s): +%s", user_id, feld, ", ".join(neue))
    return neue


def _limit_basis(limit: str) -> str:
    """Limit-Begriff ohne '(Ausnahme: ...)'-Annotation."""
    idx = (limit or "").lower().find("(ausnahme")
    return limit[:idx].strip() if idx > 0 else (limit or "").strip()


async def refine_profile_limit(user_id: str, feld: str, alt: str, neu: str) -> bool:
    """Präzisiert eine BESTEHENDE Sicherheits-Grenze (Ausnahme-Annotation) – niemals
    entfernen oder ersetzen: `neu` muss den alten Basis-Begriff weiterhin enthalten
    (nur Annotation, z.B. 'Öffentlichkeit' → 'Öffentlichkeit (Ausnahme: Plug tragen)').
    Wie append_profile_limits nur aus einem explizit vom User bestätigten Flow
    aufrufen. Gibt True zurück, wenn der Eintrag ersetzt wurde."""
    if feld not in _PROFILE_LIMIT_FIELDS.values():
        logger.warning("refine_profile_limit: unerlaubtes Feld %s – ignoriert", feld)
        return False
    alt = (alt or "").strip()
    neu = (neu or "").strip()
    basis = _limit_basis(alt)
    if not alt or not neu or not basis:
        return False
    # Nur-Annotation-Garantie: der alte Basis-Begriff muss in `neu` erhalten bleiben.
    if basis.lower() not in neu.lower():
        logger.warning("refine_profile_limit: '%s' enthält Basis '%s' nicht – ignoriert", neu, basis)
        return False

    results, _ = await kern._aio(kern.client.scroll,
        collection_name="user_profiles",
        scroll_filter=qm.Filter(must=[
            qm.FieldCondition(key="user_id", match=qm.MatchValue(value=kern.mandanten_key(user_id)))
        ]),
        limit=1, with_payload=True, with_vectors=False,
    )
    if not results:
        return False
    point_id = str(results[0].id)
    payload = results[0].payload or {}
    bestand = payload.get(feld) or []
    if not isinstance(bestand, list):
        return False

    def _gleich(a: str, b: str) -> bool:
        return " ".join(a.lower().split()) == " ".join(b.lower().split())

    idx = next((i for i, b in enumerate(bestand) if isinstance(b, str) and _gleich(b, alt)), None)
    if idx is None:
        logger.info("refine_profile_limit: '%s' nicht im Bestand (%s.%s) – ignoriert", alt, user_id, feld)
        return False
    if any(isinstance(b, str) and _gleich(b, neu) for b in bestand):
        return False  # Ziel-Form steht schon drin

    neu_liste = list(bestand)
    neu_liste[idx] = neu
    # Gezielt patchen + Vektor separat – kein Full-Upsert (Lost Update, s. _reembed_profile_vector).
    await kern._aio(kern.client.set_payload,
        collection_name="user_profiles",
        payload={feld: neu_liste},
        points=[point_id],
    )
    await _reembed_profile_vector(point_id, {**payload, "user_id": kern.mandanten_key(user_id), feld: neu_liste})
    logger.info("Grenze präzisiert (%s.%s): %s → %s", user_id, feld, alt, neu)
    return True


# Niemals durch Auto-Updater veränderbare Felder. Hinweis: Einstellungs-Felder
# (bot_name, sklave_anrede, setup_kontext, persona_stil, sprache) sind durch die
# _PROFILE_ALLOWED_FIELDS-Allowlist bereits vor dem Auto-Updater geschützt – sie
# gehören NICHT hierher, weil patch_profile_fields (der legitime Setter-Pfad aus
# persona_config/einstellungen) PROTECTED-Felder grundsätzlich verweigert.
_PROFILE_PROTECTED_FIELDS = {"hard_limits", "kinderfreie_zeiten", "safe_word"}

# Felder, die in den Profil-Vektor einfließen (semantisch relevant). Gilt für
# upsert_user_profile UND patch_profile_fields – sonst je nach Pfad inkonsistente Vektoren.
# Einstellungs-Felder (sprache, persona_stil, bot_name, …) bewusst NICHT aufnehmen –
# sie würden den Vektor nur verrauschen.
_PROFILE_EMBED_FIELDS = {"interessen", "vorlieben", "hard_limits", "ziele", "erfahrungsstand",
                         "kategorie_reaktionen", "persoenlichkeit_tags", "wunsch_kategorien",
                         # grenzen = Domina-Pendant zu hard_limits (semantisch relevant) –
                         # ohne den Eintrag wäre das Re-Embed in append/refine_profile_limits
                         # fürs Domina-Profil ein No-Op.
                         "grenzen"}


async def apply_profile_patch(profile_user: str, patch: dict) -> dict:
    """Wendet einen Profil-Patch sicher an. Gibt Bericht zurueck (was wurde geaendert).

    Erlaubte Operationen: "list_add" (Liste erweitern, dedupliziert),
    "text_replace" (Text-Feld ersetzen), "limit_add" (Sicherheits-Grenze ADD-ONLY
    ergänzen – nur für das Grenzen-Feld der Rolle, niemals entfernen), "limit_refine"
    (bestehende Grenze um eine Ausnahme-Annotation präzisieren; der Basis-Begriff
    bleibt zwingend erhalten, wert = {"alt": ..., "neu": ...} oder Liste davon).
    Alles andere wird ignoriert. Hard Limits können ausschließlich additiv wachsen
    oder annotiert werden – nie schrumpfen.
    """
    bericht = {"angewandt": [], "ignoriert": []}
    # Whitelists sind pro ROLLE definiert; profile_user ist der Mandanten-Key
    # ("sklave" bzw. "7:sklave") – Reads/Writes nutzen den vollen Key.
    rolle = paare.rolle_von_user_id(profile_user)
    if rolle not in _PROFILE_ALLOWED_FIELDS:
        bericht["ignoriert"].append(f"unbekannter user '{profile_user}'")
        return bericht

    erlaubt = _PROFILE_ALLOWED_FIELDS[rolle]
    profil = await get_user_profile(profile_user) or {"user_id": profile_user}

    aenderungen = patch.get("changes") if isinstance(patch, dict) else None
    if not isinstance(aenderungen, list):
        bericht["ignoriert"].append("kein 'changes'-Array im Patch")
        return bericht

    # Nur die wirklich geänderten Felder sammeln und gezielt patchen (statt das
    # ganze Profil neu zu upserten) – sonst überschreibt der stale Read parallel
    # geschriebene Felder wie punkte/streak (Lost Update).
    geaendert: dict = {}
    limit_zusatz: dict = {}  # add-only Sicherheits-Grenzen, separat geschrieben
    limit_feld = _PROFILE_LIMIT_FIELDS.get(rolle)
    for ch in aenderungen:
        if not isinstance(ch, dict):
            continue
        feld = ch.get("feld")
        op = ch.get("operation")
        wert = ch.get("wert")

        if op == "limit_add" and feld and feld == limit_feld:
            if isinstance(wert, str):
                wert = [wert]
            if not isinstance(wert, list):
                bericht["ignoriert"].append(f"{feld}: kein Listen-Wert")
                continue
            # Innerhalb des Patches dedupen (D9/N17) – zwei limit_add-Changes
            # mit demselben Wert sammelten ihn sonst doppelt an.
            ziel = limit_zusatz.setdefault(feld, [])
            for v in wert:
                if v and v not in ziel:
                    ziel.append(v)
        elif op == "limit_refine" and feld and feld == limit_feld:
            alt_neu_liste = wert if isinstance(wert, list) else [wert]
            for alt_neu in alt_neu_liste:
                if not (isinstance(alt_neu, dict) and alt_neu.get("alt") and alt_neu.get("neu")):
                    bericht["ignoriert"].append(f"{feld}: limit_refine braucht alt/neu")
                    continue
                if await refine_profile_limit(profile_user, feld, alt_neu["alt"], alt_neu["neu"]):
                    bericht["angewandt"].append(f"✏️ {feld}: {alt_neu['alt']} → {alt_neu['neu']}")
                else:
                    bericht["ignoriert"].append(f"{feld}: '{alt_neu['alt']}' nicht präzisierbar")
        elif op == "list_add" and feld in erlaubt["list_add"]:
            # Basis inkl. bereits in DIESEM Patch angesammelter Werte
            # (Review D8/M5): sonst verwirft ein zweites list_add aufs selbe
            # Feld das erste – der Bericht meldete trotzdem beide als angewandt.
            bestand = geaendert.get(feld, profil.get(feld) or [])
            if not isinstance(bestand, list):
                bestand = []
            if isinstance(wert, str):
                wert = [wert]
            if not isinstance(wert, list):
                bericht["ignoriert"].append(f"{feld}: kein Listen-Wert")
                continue
            neue = []
            for v in wert:  # inkl. Eingabe-Dedup (D9/N17)
                if v and v not in bestand and v not in neue:
                    neue.append(v)
            if neue:
                geaendert[feld] = bestand + neue
                bericht["angewandt"].append(f"+ {feld}: {', '.join(neue)}")
        elif op == "text_replace" and feld in erlaubt["text_replace"]:
            if isinstance(wert, str) and wert.strip():
                geaendert[feld] = wert.strip()
                bericht["angewandt"].append(f"~ {feld}: {wert.strip()[:80]}")
            else:
                bericht["ignoriert"].append(f"{feld}: leerer Text")
        else:
            bericht["ignoriert"].append(f"{feld}/{op} nicht erlaubt")

    if geaendert:
        await patch_profile_fields(profile_user, geaendert)
    for feld, werte in limit_zusatz.items():
        neue = await append_profile_limits(profile_user, feld, werte)
        if neue:
            bericht["angewandt"].append(f"🚫 {feld}: {', '.join(neue)}")
    return bericht
