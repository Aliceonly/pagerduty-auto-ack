import unittest
from datetime import datetime, timezone

from pagerduty_auto_ack.cli import (
    is_night_shift_hkt,
    partition_excluded_incidents,
)

# Real-world ids from the Orderly PagerDuty account (see config comments).
TEAM_SUPPORT = "P8ALLOI"          # umbrella "Support Team" — owns BOTH P0 and P1
EP_P0 = "PNEK5IJ"                  # escalation policy: Support Team (P0)
EP_P1 = "PAG1IST"                  # escalation policy: Support Team (P1)


def _incident(number, ep_id, team_ids=(TEAM_SUPPORT,)):
    return {
        "incident_number": number,
        "id": f"PINC{number}",
        "teams": [{"id": t} for t in team_ids],
        "escalation_policy": {"id": ep_id},
    }


class TestIsNightShiftHKT(unittest.TestCase):
    def test_reported_time_0525_hkt_is_night_shift(self):
        # 2026-06-20 05:25 HKT == 2026-06-19 21:25 UTC (the time #679829 was skipped)
        now = datetime(2026, 6, 19, 21, 25, tzinfo=timezone.utc)
        self.assertTrue(is_night_shift_hkt(now))

    def test_start_boundary_is_inclusive(self):
        # 01:30 HKT == 17:30 UTC prev day
        self.assertTrue(is_night_shift_hkt(datetime(2026, 6, 19, 17, 30, tzinfo=timezone.utc)))

    def test_just_before_start_is_not_night_shift(self):
        # 01:29 HKT
        self.assertFalse(is_night_shift_hkt(datetime(2026, 6, 19, 17, 29, tzinfo=timezone.utc)))

    def test_end_boundary_is_exclusive(self):
        # 08:30 HKT == 00:30 UTC
        self.assertFalse(is_night_shift_hkt(datetime(2026, 6, 20, 0, 30, tzinfo=timezone.utc)))

    def test_just_before_end_is_night_shift(self):
        # 08:29 HKT
        self.assertTrue(is_night_shift_hkt(datetime(2026, 6, 20, 0, 29, tzinfo=timezone.utc)))


class TestPartitionByEscalationPolicy(unittest.TestCase):
    def test_p0_dropped_p1_kept_when_excluding_p0_policy(self):
        p0 = _incident(700001, EP_P0)
        p1 = _incident(679829, EP_P1)  # the real incident that was wrongly skipped
        kept, dropped = partition_excluded_incidents(
            [p0, p1], excluded_escalation_policy_ids=[EP_P0]
        )
        self.assertEqual([i["incident_number"] for i in kept], [679829])
        self.assertEqual([i["incident_number"] for i in dropped], [700001])

    def test_regression_p1_no_longer_skipped_by_team_filter(self):
        # The original bug: team filter on P8ALLOI dropped the P1 incident too,
        # because P0 and P1 share the same team. EP-based filtering must keep it.
        p1 = _incident(679829, EP_P1)
        kept, dropped = partition_excluded_incidents(
            [p1], excluded_escalation_policy_ids=[EP_P0]
        )
        self.assertEqual(kept, [p1])
        self.assertEqual(dropped, [])

    def test_old_team_filter_still_supported(self):
        # Backward compat: excluding by team still works (OR semantics).
        p0 = _incident(700001, EP_P0)
        kept, dropped = partition_excluded_incidents(
            [p0], excluded_team_ids=[TEAM_SUPPORT]
        )
        self.assertEqual(dropped, [p0])
        self.assertEqual(kept, [])

    def test_no_exclusions_keeps_everything(self):
        incs = [_incident(1, EP_P0), _incident(2, EP_P1)]
        kept, dropped = partition_excluded_incidents(incs)
        self.assertEqual(kept, incs)
        self.assertEqual(dropped, [])

    def test_missing_escalation_policy_is_kept(self):
        inc = {"incident_number": 9, "teams": [], "escalation_policy": None}
        kept, dropped = partition_excluded_incidents(
            [inc], excluded_escalation_policy_ids=[EP_P0]
        )
        self.assertEqual(kept, [inc])
        self.assertEqual(dropped, [])


if __name__ == "__main__":
    unittest.main()
