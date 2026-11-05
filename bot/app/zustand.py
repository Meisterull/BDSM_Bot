"""
State-Restore beim Start (pro Paar).

Herausgelöst aus bot/main.py (Wartbarkeits-Umbau 05.11.2026). Öffentlicher Zugriff weiterhin über bot.main.
"""
import logging
from bot import state
from bot.services import paare, qdrant
from bot.messages import t

logger = logging.getLogger(__name__)



async def restore_state() -> None:
    # Pro Paar mit dessen Mandanten-Key – ein 'gefragt'-Task eines Paares landet
    # nie mehr im State eines anderen (Audit-Befund "Recovery ohne Paar-Filter").
    for paar in paare.alle_paare():
        await _restore_paar_state(paar)


async def _restore_paar_state(paar: "paare.Paar") -> None:
    sub_key = paar.user_id(paare.ROLLE_SUB)
    pending = await qdrant.get_tasks_by_status(
        ["gefragt", "gefuehl_pending", "reaktion_pending"], user_id=sub_key)
    sklave_chat = paar.sub_chat_id
    domina_chat = paar.dom_chat_id

    # Neueste zuerst: bei mehreren 'gefragt'-Tasks gewinnt deterministisch der
    # jüngste (konsistent mit der Recovery in handle_message, die max(erteilt_am)
    # nimmt) – nicht die willkürliche Scroll-Reihenfolge.
    pending.sort(key=lambda task: task.get("erteilt_am", ""), reverse=True)

    for task in pending:
        point_id = task.get("qdrant_point_id")
        status = task.get("status")
        if status == "gefragt":
            state.set_followup_task(sklave_chat, point_id)
        elif status == "gefuehl_pending":
            state.set_gefuehl_pending(sklave_chat, point_id)
        elif status == "reaktion_pending":
            state.set_reaktion_pending(domina_chat, point_id)

    # Aktiven Kette-Task wiederherstellen: ein "offen" Task mit kette_id, der noch
    # nicht 'gefragt' wurde (z.B. weil der Bot zwischen Freischaltung und Followup-Job
    # crashte). Den jüngsten setzen wir als Followup-Task UND auf Status 'gefragt',
    # damit Sklaven-Antwort sauber zugeordnet wird und der DB-Status mit dem
    # In-Memory-State synchron bleibt.
    offene = await qdrant.get_tasks_by_status(["offen"], user_id=sub_key)
    offene_ketten = [t for t in offene if t.get("kette_id")]
    if offene_ketten:
        offene_ketten.sort(key=lambda t: t.get("erteilt_am", ""), reverse=True)
        aktiv = offene_ketten[0]
        aktiv_id = aktiv.get("qdrant_point_id")
        # Status nur auf 'gefragt' setzen, wenn der In-Memory-State wirklich
        # gesetzt wurde – set_followup_task skippt still, wenn die Pending-
        # Schleife oben bereits einen Mode gesetzt hat (sonst Status-Drift).
        if state.set_followup_task(sklave_chat, aktiv_id):
            try:
                await qdrant.update_task(aktiv_id, {"status": "gefragt"})
            except Exception as e:
                logger.error("Restore: konnte Kette-Task %s nicht auf 'gefragt' setzen: %s", aktiv_id, e)
        else:
            logger.info("Restore: Kette-Task %s bleibt 'offen' – Sklave-Mode bereits belegt.", aktiv_id)
