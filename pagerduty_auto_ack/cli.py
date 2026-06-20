import argparse
import logging
import os
import sys
import time
import tomllib
from collections import deque
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import pd

HKT = ZoneInfo("Asia/Hong_Kong")
NIGHT_SHIFT_START = dtime(1, 30)
NIGHT_SHIFT_END = dtime(8, 30)

COOLDOWN_WINDOW = timedelta(minutes=60)
COOLDOWN_THRESHOLD = 10
COOLDOWN_DURATION = timedelta(minutes=30)

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
    "night_shift_excluded_team_ids": [],
    "night_shift_excluded_escalation_policy_ids": [],
}


def is_night_shift_hkt(now=None) -> bool:
    """夜班时段为 HKT 01:30~08:30（左闭右开）。"""
    now = now or datetime.now(HKT)
    t = now.astimezone(HKT).time()
    return NIGHT_SHIFT_START <= t < NIGHT_SHIFT_END


def partition_excluded_incidents(
    incidents,
    excluded_team_ids=(),
    excluded_escalation_policy_ids=(),
):
    """Split incidents into (kept, dropped) for the night shift.

    An incident is dropped if it matches an excluded team OR its escalation
    policy is in the excluded set (OR semantics).

    Prefer escalation-policy filtering: at Orderly, P0 and P1 alerts share the
    same team (``P8ALLOI`` "Support Team") but have separate escalation policies
    (P0 ``PNEK5IJ`` vs P1 ``PAG1IST``) and services, so only the policy/service
    can tell P0 from P1. Team filtering is kept for backward compatibility.
    """
    excluded_teams = set(excluded_team_ids or [])
    excluded_eps = set(excluded_escalation_policy_ids or [])
    if not excluded_teams and not excluded_eps:
        return list(incidents), []
    kept, dropped = [], []
    for inc in incidents:
        team_ids = {t.get("id") for t in inc.get("teams", []) if t.get("id")}
        ep_id = (inc.get("escalation_policy") or {}).get("id")
        if (team_ids & excluded_teams) or (ep_id and ep_id in excluded_eps):
            dropped.append(inc)
        else:
            kept.append(inc)
    return kept, dropped


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
        "night_shift_excluded_team_ids": pick(None, "night_shift_excluded_team_ids"),
        "night_shift_excluded_escalation_policy_ids": pick(
            None, "night_shift_excluded_escalation_policy_ids"
        ),
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
    night_shift_excluded_team_ids = cfg["night_shift_excluded_team_ids"] or []
    night_shift_excluded_escalation_policy_ids = (
        cfg["night_shift_excluded_escalation_policy_ids"] or []
    )

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

            processed_history: deque[datetime] = deque()
            cooldown_until: datetime | None = None

            while True:
                try:
                    now = datetime.now(timezone.utc)

                    # 清理 60 分钟窗口外的处理记录
                    cutoff = now - COOLDOWN_WINDOW
                    while processed_history and processed_history[0] < cutoff:
                        processed_history.popleft()

                    # 冷却期内跳过所有处理
                    if cooldown_until and now < cooldown_until:
                        remaining = int((cooldown_until - now).total_seconds())
                        logger.info(f"In cooldown ({remaining}s left), skipping cycle")
                        time.sleep(interval)
                        continue
                    if cooldown_until and now >= cooldown_until:
                        logger.info("Cooldown ended, resuming")
                        cooldown_until = None

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

                    # 夜班时段（HKT 01:30~08:30）排除指定 escalation policy / 团队的 incidents
                    skipped_by_team = []
                    has_night_exclusions = (
                        night_shift_excluded_escalation_policy_ids
                        or night_shift_excluded_team_ids
                    )
                    if has_night_exclusions and is_night_shift_hkt(now):
                        incidents, skipped_by_team = partition_excluded_incidents(
                            incidents,
                            excluded_team_ids=night_shift_excluded_team_ids,
                            excluded_escalation_policy_ids=night_shift_excluded_escalation_policy_ids,
                        )
                        if skipped_by_team:
                            logger.info(
                                f"Night shift: skipping {len(skipped_by_team)} excluded incidents"
                            )
                            for inc in skipped_by_team:
                                logger.info(f"  -- #{inc.get('incident_number')}  {inc.get('title', 'N/A')}")

                    # PD API supports max of 250 updates at the same time
                    incidents = incidents[:250]
                    incident_ids = [i.get("id") for i in incidents]

                    ack_incidents += incidents

                    action_fn(pd_client, incident_ids)

                    if incidents:
                        logger.info(f"{len(incidents)} incidents {action_label}:")
                        for inc in incidents:
                            logger.info(f"  -> #{inc.get('incident_number')}  {inc.get('title', 'N/A')}")
                    elif not skipped_by_team:
                        logger.info(f"No incidents to {action_label[:-1]}")

                    # 记录本周期处理过的告警时间戳，并检查是否触发冷却
                    for _ in incidents:
                        processed_history.append(now)
                    if len(processed_history) > COOLDOWN_THRESHOLD:
                        cooldown_until = now + COOLDOWN_DURATION
                        logger.warning(
                            f"Processed {len(processed_history)} incidents in last "
                            f"{int(COOLDOWN_WINDOW.total_seconds() // 60)}m, entering "
                            f"{int(COOLDOWN_DURATION.total_seconds() // 60)}m cooldown"
                        )
                        processed_history.clear()
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
