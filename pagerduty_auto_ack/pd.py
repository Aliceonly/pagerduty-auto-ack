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
