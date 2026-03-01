import argparse
import logging
import os
import sys
import time
import tomllib

from . import pd

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

DEFAULTS = {
    "pagerduty_api_key": None,
    "interval": 60,
    "urgencies": [],
    "action": "ack",
    "all_incidents": False,
    "schedule_id": None,
}


def load_config(config_path: str) -> dict:
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def parse_args():
    parser = argparse.ArgumentParser(
        prog="pagerduty-auto-ack",
        description="Monitor and automatically ACKnowledge or resolve PagerDuty incidents",
    )

    parser.add_argument(
        "--config",
        required=False,
        help="path to TOML config file",
    )
    parser.add_argument("--pagerduty-api-key", required=False, default=None)
    parser.add_argument(
        "--interval",
        required=False,
        type=int,
        default=None,
        help="how often (in seconds) to run the check",
    )
    parser.add_argument(
        "--urgency",
        required=False,
        choices=["high", "low"],
        action="append",
        default=None,
        dest="urgencies",
        help="defaults to all urgencies",
    )
    parser.add_argument(
        "--action",
        required=False,
        choices=["ack", "resolve"],
        default=None,
        help="action to take on incidents (default: ack)",
    )
    parser.add_argument(
        "--all-incidents",
        required=False,
        action="store_true",
        default=None,
        help="process all incidents, not just those assigned to you",
    )
    parser.add_argument(
        "--schedule-id",
        required=False,
        default=None,
        help="PagerDuty schedule ID; only process incidents when you are on-call",
    )

    return parser.parse_args()


def resolve_config(args):
    """Merge CLI args > config file > defaults."""
    config = {}
    if args.config:
        config = load_config(args.config)

    def pick(cli_val, key):
        if cli_val is not None:
            return cli_val
        return config.get(key, DEFAULTS[key])

    return {
        "pagerduty_api_key": pick(args.pagerduty_api_key, "pagerduty_api_key"),
        "interval": pick(args.interval, "interval"),
        "urgencies": pick(args.urgencies, "urgencies"),
        "action": pick(args.action, "action"),
        "all_incidents": pick(args.all_incidents, "all_incidents"),
        "schedule_id": pick(args.schedule_id, "schedule_id"),
    }


def main():
    args = parse_args()
    cfg = resolve_config(args)

    pd_api_key = cfg["pagerduty_api_key"]
    if not pd_api_key:
        logger.error("--pagerduty-api-key is required (via CLI or config file)")
        sys.exit(1)

    action = cfg["action"]
    interval = cfg["interval"]
    urgencies = cfg["urgencies"]
    all_incidents = cfg["all_incidents"]
    schedule_id = cfg["schedule_id"]

    try:
        ack_incidents = []
        with pd.get_client(pd_api_key) as pd_client:
            user = pd.get_current_user(pd_client)
            user_email = user.get("email")
            user_id = user.get("id")

            if action == "resolve":
                statuses = ["triggered", "acknowledged"]
                action_fn = pd.resolve_incidents
                action_label = "resolved"
            else:
                statuses = ["triggered"]
                action_fn = pd.acknowledge_incidents
                action_label = "acknowledged"

            scope = "all incidents" if all_incidents else "my incidents"
            schedule_info = f", schedule: {schedule_id}" if schedule_id else ""
            logger.info(f"Running as user: {user_email} (action: {action}, scope: {scope}{schedule_info})")

            user_ids = [] if all_incidents else [user_id]

            while True:
                try:
                    # 如果配置了 schedule_id，先检查是否在值班
                    if schedule_id:
                        if not pd.is_user_oncall(pd_client, user_id, schedule_id):
                            logger.info("Not on-call, skipping")
                            time.sleep(interval)
                            continue

                    incidents = list(pd.get_incidents(
                        pd_client,
                        user_ids=user_ids,
                        urgencies=urgencies,
                        statuses=statuses,
                    ))

                    # PD API supports max of 250 updates at the same time
                    incidents = incidents[:250]
                    incident_ids = [i.get("id") for i in incidents]

                    ack_incidents += incidents

                    action_fn(pd_client, incident_ids)

                    if incidents:
                        logger.info(f"{len(incidents)} incidents {action_label}:")
                        for inc in incidents:
                            logger.info(f"  -> #{inc.get('incident_number')}  {inc.get('title', 'N/A')}")
                    else:
                        logger.info(f"No incidents to {action_label[:-1]}")
                except Exception:
                    logger.warning("Request failed, will retry next cycle", exc_info=True)

                logger.debug(f"Sleeping for {interval} seconds")
                time.sleep(interval)

    except KeyboardInterrupt:
        count = len(ack_incidents)
        logger.info(f"Shutting down. Total {action_label}: {count}")
        if ack_incidents:
            print(f"\n{'─' * 50}")
            print(f"  {action_label.upper()} INCIDENTS SUMMARY")
            print(f"{'─' * 50}")
            for inc in ack_incidents:
                print(f"  #{inc.get('incident_number')}  {inc.get('title', 'N/A')}")
            print(f"{'─' * 50}")
            print(f"  Total: {count}")
            print(f"{'─' * 50}")


if __name__ == "__main__":
    main()
