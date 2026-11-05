"""
Fassade: bot.scheduler.followup bleibt als Import-Pfad erhalten.

Der frühere 2 700-Zeilen-Monolith ist in Module aufgeteilt (05.11.2026):
  basis       – Scheduler-Grundgerüst: Job-Guards, Fangnetz, Nachholen blockierter Einmal-Jobs, Flow-Prüfung.
  schablonen  – Deterministische Text-Detektoren für Vorschläge: Schablonen, Formeln, Zutaten-Sperrliste, Diversität.
  tiny_task   – Tiny-Task-/Aufgaben-Vorschlag: Kontext-Rendering, Kategorien-Wahl, Machbarkeit, Versand, Feedback-Job.
  luecken     – Lücken-Erkennung und Stille-Check-in (Dom-Seite inaktiv).
  impulse     – Spiel-/Coach-Impulse, Blitz-, Event-, Dauer- und Kalender-Ticks.
  aufgaben    – Follow-up-Fragen zu offenen Aufgaben, Serien- und Ketten-Fortschritt.
  tagesjobs   – Tägliche/wöchentliche Jobs: Rollenspiel, Lernkurve, Training, Stimmung, Ziele, Geheimnis, Wochenplanung, Kommentare, Resurface.
  reflexion   – Sonntags-Kaskade: Gesprächs-Cache, Lerntagebuch, Profil-Pflege, Coach-Reflexion.
  wartung     – Wartungs-Jobs: Trainings-Erinnerung, Backup, Sklaven-Dossier, offene Fäden.

Neuer Code importiert direkt aus dem jeweiligen Modul. Zum PATCHEN in Tests
immer das Modul nehmen, in dem der Name definiert ist – ein Patch auf diese
Fassade erreicht die Aufrufer nicht.
"""
from bot.scheduler.basis import (  # noqa: F401
    _blockiert_cv, _flow_aktiv, _job_fangnetz, _job_guard,
    _melde_blockiert, _nach_llm_verworfen, _nachhol, _nachhol_lauf,
    _nachholbar, _parse_datum, _zweiwochen_takt, nachhol_tick_job,
)
from bot.scheduler.schablonen import (  # noqa: F401
    _ABSETZ_META_RE, _GENAU_PROFIL_RE, _JETZT_RICHTIG_WEIL_RE, _MAGST_RE,
    _META_RUECKFRAGE_RE, _PASST_WEIL_RE, _PASST_ZU_RE, _SITUATIV_RE,
    _STUFEN_RE, _TREFFER_RE, _WIE_LANGE_RE, _WIE_WAERS_RE,
    _ZUTATEN_MUSTER, _begruendungs_formel_entfernen, _cos, _diversester_kandidat,
    _formel_verstoesse, _kombi_vorlieben_wahl, _meta_rueckfrage_entfernen, _verbrauchte_zutaten,
    _vorlieben_cluster, _vorschlag_abschluss, _vorschlag_anfang,
)
from bot.scheduler.tiny_task import (  # noqa: F401
    _TINY_QUERY_VECTOR, _arc_tag_anpassen, _eintrag_alter_tage, _kategorie_level_hinweis,
    _machbarkeit_sichern, _machbarkeits_maengel, _save_tiny_task, _schwierigkeit_effektiv,
    _send_tiny_task_vorschlag, _sende_arc_vorschlag, _stimmung_aktuell, _tiny_query_vector,
    _vorschlag_kontext, _waehle_kategorien, tiny_task_feedback_job, tiny_task_vorschlag_job,
)
from bot.scheduler.luecken import (  # noqa: F401
    _letzte_domina_aktivitaet, luecken_check_job, luecken_zustellung_job, stille_checkin_job,
)
from bot.scheduler.impulse import (  # noqa: F401
    _IMPULS_KOLLISION, _WUNSCH_WETTE_VERSUCHE, _impuls_claim, _impuls_reihenfolge,
    _impuls_slot_frei, _quiz_verfall_nachreichen, blitz_ablauf_job, blitz_check_job,
    coach_impuls_job, dauer_job, event_check_job, kalender_job,
    spiel_impuls_job,
)
from bot.scheduler.aufgaben import (  # noqa: F401
    _KETTE_AKTIVE_STATUS, _KETTE_SWEEP_WARTE_TAGE, _followup_fragen, _process_kette_tasks,
    _process_serie_tasks, followup_job,
)
from bot.scheduler.tagesjobs import (  # noqa: F401
    geheimnis_job, kommentar_analyse_job, lernkurve_job, resurface_job,
    rollenspiel_vorschlag_job, stimmung_job, training_job, wochenplanung_job,
    ziel_erinnerung_job,
)
from bot.scheduler.reflexion import (  # noqa: F401
    _CONV_CACHES, _CONV_CACHE_TTL, _conv_cache, _domina_conversations,
    _kurzbeschreibung_patch, coach_reflexion_job, generiere_lerntagebuch, generiere_profil_vorschlaege,
    lerntagebuch_job, profil_pflege_job, vergiss_paar,
)
from bot.scheduler.wartung import (  # noqa: F401
    backup_job, offene_faeden_job, sklave_dossier_job, training_erinnerung_job,
)
# Geteilte Module, die Tests/Alt-Code über die Fassade erreichen (dieselben Objekte
# wie in den Teilmodulen – ein Patch auf qdrant.xyz wirkt daher überall).
from bot import config, state  # noqa: F401
from bot.services import (  # noqa: F401
    paare, qdrant, grok, telegram_helper, embeddings, privileg_effekte, limits_check,
    kategorie_logik, labels, zeiten, sticker_reaktionen,
)
from bot.prompts import followup as fp  # noqa: F401
from bot.prompts import coach_persona, domina_coach  # noqa: F401
