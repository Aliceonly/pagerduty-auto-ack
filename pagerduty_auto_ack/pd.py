import logging

import pdpyras

logger = logging.getLogger(__name__)


def get_client(api_key: str):
    return pdpyras.APISession(api_key)


def get_current_user(client: pdpyras.APISession):
    return client.rget("users/me")


def get_incidents(
    client: pdpyras.APISession,
    user_ids=[],
    urgencies=[],
    statuses=["triggered"],
):
    logger.debug("Listing incidents")

    return client.rget(
        "incidents",
        params={
            "user_ids": user_ids,
            "urgencies": urgencies,
            "total": True,
            "statuses": statuses,
            "sort_by": "incident_number:desc",
        },
    )


def is_user_oncall(client: pdpyras.APISession, user_id: str, schedule_id: str) -> bool:
    """检查用户当前是否在指定 schedule 上值班。"""
    logger.debug(f"Checking oncall status for user {user_id} on schedule {schedule_id}")
    try:
        oncalls = client.rget(
            "oncalls",
            params={
                "user_ids[]": [user_id],
                "schedule_ids[]": [schedule_id],
            },
        )
        return len(list(oncalls)) > 0
    except Exception:
        logger.warning("Failed to check oncall status, assuming on-call", exc_info=True)
        return True


def _update_incidents(client: pdpyras.APISession, incident_ids=[], status="acknowledged"):
    if not incident_ids:
        logger.debug("No incidents to update")
        return

    body = {
        "incidents": [
            {"id": incident_id, "type": "incident_reference", "status": status}
            for incident_id in incident_ids
        ]
    }

    return client.rput(
        "incidents",
        params={
            "total": True,
        },
        json=body,
    )


def acknowledge_incidents(client: pdpyras.APISession, incident_ids=[]):
    logger.debug("Acknowledging incidents")
    return _update_incidents(client, incident_ids, status="acknowledged")


def resolve_incidents(client: pdpyras.APISession, incident_ids=[]):
    logger.debug("Resolving incidents")
    return _update_incidents(client, incident_ids, status="resolved")
