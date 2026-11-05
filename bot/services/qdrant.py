"""
Fassade der Datenschicht: `from bot.services import qdrant` bleibt der Zugriffspfad.

Der frühere 1 970-Zeilen-Monolith liegt jetzt in bot/services/db/ (05.11.2026):
  kern           – Qdrant-Client, I/O-Executor, Mandanten-Key und Collection-Setup.
  profile        – user_profiles: Lesen, Upsert, Feld-Patches, Limit-Pflege, Auto-Updater-Patches.
  tasks          – tasks: Anlegen, Lesen, Status, Follow-up-Zeitpunkte.
  conversations  – conversations: Speichern, Kontext-Suche (hybrid), Zeitraum-Abfragen.
  lernen         – Lerntagebuch und Quiz-Wissen (conversations-Collection, eigene Typen).
  auswertung     – Auswertungen über Aufgaben: Lernkurve, Level-Score, Streak, Bewertungs-Kontext.
  vorschlaege    – Stimmung, Inspirationen und Tiny-Task-Vorschläge (progress/tasks-Typen).
  progress       – progress: Fortschritt, erledigte Kategorien, Training.
  wuensche       – wuensche: Wunschliste des Subs.
  geheimnisse    – geheimnisse: zeitgesteuerte Geheimnisse.
  ketten         – Aufgaben-Ketten und Gruppen-Glieder (tasks-Collection).
  strafen        – strafen + Vertrauens-Score.
  coach_regeln   – coach_regeln: Lern-Speicher des Coachs.
  verwaltung     – Betreiber-Funktionen: Paar-Daten löschen.
  skills         – skills: kuratiertes Wissen je Kategorie.

Jeder Name wird bei JEDEM Zugriff im definierenden Modul nachgeschlagen
(PEP 562, __getattr__). Dadurch wirkt ein Patch auf z. B. db.profile.get_user_profile
sofort für alle Aufrufer von qdrant.get_user_profile. Umgekehrt NICHT auf die
Fassade patchen (qdrant.x = …) – das überdeckt nur hier und erreicht weder die
Teilmodule noch deren interne Aufrufe. Tests patchen daher immer das Teilmodul.
"""
import importlib

# Geteilte Module, die Alt-Code/Tests über die Fassade erreichen (dieselben Objekte
# wie in den Teilmodulen – ein Patch auf emb.embed wirkt daher überall).
from qdrant_client import models as qm  # noqa: F401
from bot import config  # noqa: F401
from bot.services import embeddings as emb  # noqa: F401
from bot.services import paare  # noqa: F401

_MODUL_VON = {
    "KONTEXT_FELDER": "conversations",
    "_DENSE": "kern",
    "_MANDANTEN_COLLECTIONS": "verwaltung",
    "_PROFILE_ALLOWED_FIELDS": "profile",
    "_PROFILE_EMBED_FIELDS": "profile",
    "_PROFILE_LIMIT_FIELDS": "profile",
    "_PROFILE_PROTECTED_FIELDS": "profile",
    "_aio": "kern",
    "_diversify_by_thema": "conversations",
    "_executor": "kern",
    "_followup_zeitpunkt_utc": "tasks",
    "_limit_basis": "profile",
    "_reembed_profile_vector": "profile",
    "_skills_filter": "skills",
    "_vorschlag_label": "vorschlaege",
    "append_profile_limits": "profile",
    "apply_profile_patch": "profile",
    "client": "kern",
    "count_tasks_by_status": "tasks",
    "ensure_collections": "kern",
    "erstelle_task": "tasks",
    "followup_zeitpunkt_utc": "tasks",
    "get_active_coach_regeln": "coach_regeln",
    "get_bewertungs_kontext": "auswertung",
    "get_coach_regel": "coach_regeln",
    "get_completed_count_by_kategorie": "progress",
    "get_completed_kategorien_set": "progress",
    "get_completed_task_count": "progress",
    "get_conversation_context": "conversations",
    "get_conversations_in_range": "conversations",
    "get_faellige_geheimnisse": "geheimnisse",
    "get_gruppen_glieder": "ketten",
    "get_hybrid_conversation_context": "conversations",
    "get_kette_wartende": "ketten",
    "get_latest_stimmung": "vorschlaege",
    "get_lernkurve_daten": "auswertung",
    "get_level_score": "auswertung",
    "get_naechster_ketten_task": "ketten",
    "get_nicht_erledigt_streak": "auswertung",
    "get_offene_quiz_themen": "lernen",
    "get_open_followup_tasks": "tasks",
    "get_pending_coach_regeln": "coach_regeln",
    "get_pending_tiny_tasks_for_feedback": "vorschlaege",
    "get_progress_entries": "progress",
    "get_recent_inspirationen": "vorschlaege",
    "get_recent_lerntagebuch": "lernen",
    "get_recent_quiz_wissen": "lernen",
    "get_recent_rejected_tiny_tasks": "vorschlaege",
    "get_recent_stimmung_eintraege": "vorschlaege",
    "get_recent_stimmung_fragen": "vorschlaege",
    "get_recent_task_kategorien": "auswertung",
    "get_recent_tiny_tasks": "vorschlaege",
    "get_skill": "skills",
    "get_strafe": "strafen",
    "get_strafen": "strafen",
    "get_task": "tasks",
    "get_tasks_by_status": "tasks",
    "get_tiny_task_by_id": "vorschlaege",
    "get_training_entries": "progress",
    "get_user_profile": "profile",
    "get_vertrauens_score": "strafen",
    "get_wunsch": "wuensche",
    "list_skills": "skills",
    "loesche_paar_daten": "verwaltung",
    "loesche_task": "tasks",
    "mandanten_key": "kern",
    "mark_tiny_task_status": "vorschlaege",
    "patch_profile_fields": "profile",
    "refine_profile_limit": "profile",
    "run_io": "kern",
    "save_coach_regel": "coach_regeln",
    "save_conversation": "conversations",
    "save_geheimnis": "geheimnisse",
    "save_lerntagebuch": "lernen",
    "save_progress": "progress",
    "save_quiz_wissen": "lernen",
    "save_skill": "skills",
    "save_strafe": "strafen",
    "save_task": "tasks",
    "save_training": "progress",
    "save_wunsch": "wuensche",
    "set_coach_regel_status": "coach_regeln",
    "update_skill_fields": "skills",
    "update_strafe": "strafen",
    "update_task": "tasks",
    "update_wunsch": "wuensche",
    "upsert_user_profile": "profile",
}


def __getattr__(name: str):
    mod = _MODUL_VON.get(name)
    if mod is None:
        raise AttributeError(f"module 'bot.services.qdrant' has no attribute '{name}'")
    return getattr(importlib.import_module(f"bot.services.db.{mod}"), name)


def __dir__():
    return sorted(_MODUL_VON)


def modul_von(name: str):
    """Das Teilmodul, das `name` definiert – zum Patchen in Tests:
    setattr(qdrant.modul_von("get_user_profile"), "get_user_profile", fake)."""
    return importlib.import_module(f"bot.services.db.{_MODUL_VON[name]}")

