import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, call, patch

import requests

from schedule_handler.overrideSchedule import create_override, lookup_user_id, main


class TestCreateOverrideRetry(unittest.TestCase):
    @patch("schedule_handler.overrideSchedule.time_module.sleep")
    @patch("schedule_handler.overrideSchedule.requests.request")
    def test_create_override_retries_after_ssl_error_until_success(self, request_mock, sleep_mock):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"override": {"id": "OVR123"}}

        request_mock.side_effect = [
            requests.exceptions.SSLError("SSL handshake failed"),
            response,
        ]

        with patch("builtins.print") as print_mock:
            ok = create_override(
                headers={"Authorization": "Token token=abc"},
                schedule_id="SCH1",
                user_id="USR1",
                start_time_utc=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
                end_time_utc=datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc),
            )

        self.assertTrue(ok)
        self.assertEqual(request_mock.call_count, 2)
        sleep_mock.assert_called_once_with(1)

        printed = "\n".join(str(c.args[0]) for c in print_mock.call_args_list if c.args)
        self.assertIn("ATTEMPT 1", printed)
        self.assertIn("ATTEMPT 2", printed)
        self.assertIn("sleep 1s", printed)

    @patch("schedule_handler.overrideSchedule.time_module.sleep")
    @patch("schedule_handler.overrideSchedule.requests.request")
    def test_create_override_keeps_retrying_ssl_error_until_success(self, request_mock, sleep_mock):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"override": {"id": "OVR999"}}

        request_mock.side_effect = [
            requests.exceptions.SSLError("SSL handshake failed #1"),
            requests.exceptions.SSLError("SSL handshake failed #2"),
            requests.exceptions.SSLError("SSL handshake failed #3"),
            response,
        ]

        with patch("builtins.print") as print_mock:
            ok = create_override(
                headers={"Authorization": "Token token=abc"},
                schedule_id="SCH1",
                user_id="USR1",
                start_time_utc=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
                end_time_utc=datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc),
            )

        self.assertTrue(ok)
        self.assertEqual(request_mock.call_count, 4)
        self.assertEqual(sleep_mock.call_args_list, [call(1), call(2), call(4)])

        printed = "\n".join(str(c.args[0]) for c in print_mock.call_args_list if c.args)
        self.assertIn("ATTEMPT 4", printed)
        self.assertIn("sleep 4s", printed)

    @patch("schedule_handler.overrideSchedule.time_module.sleep")
    @patch("schedule_handler.overrideSchedule.requests.request")
    def test_create_override_retries_http_error_until_success(self, request_mock, sleep_mock):
        bad = Mock()
        bad.text = "forbidden"
        bad.json.return_value = {"error": {"message": "forbidden"}}
        bad.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "403 Client Error", response=bad
        )

        good = Mock()
        good.raise_for_status.return_value = None
        good.json.return_value = {"override": {"id": "OVR777"}}

        request_mock.side_effect = [bad, good]

        with patch("builtins.print") as print_mock:
            ok = create_override(
                headers={"Authorization": "Token token=abc"},
                schedule_id="SCH1",
                user_id="USR1",
                start_time_utc=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
                end_time_utc=datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc),
            )

        self.assertTrue(ok)
        self.assertEqual(request_mock.call_count, 2)
        sleep_mock.assert_called_once_with(1)

        printed = "\n".join(str(c.args[0]) for c in print_mock.call_args_list if c.args)
        self.assertIn("HTTP错误", printed)


class TestLookupUserRetry(unittest.TestCase):
    @patch("schedule_handler.overrideSchedule.time_module.sleep")
    @patch("schedule_handler.overrideSchedule.requests.request")
    def test_lookup_user_id_retries_443_ssl_then_success(self, request_mock, sleep_mock):
        good = Mock()
        good.raise_for_status.return_value = None
        good.json.return_value = {"users": [{"id": "U123", "name": "Alice"}]}

        request_mock.side_effect = [
            requests.exceptions.SSLError("[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE]"),
            good,
        ]

        uid = lookup_user_id({"Authorization": "Token token=abc"}, "Alice")
        self.assertEqual(uid, "U123")
        self.assertEqual(request_mock.call_count, 2)
        sleep_mock.assert_called_once_with(1)


class TestMainFlowControl(unittest.TestCase):
    @patch("schedule_handler.overrideSchedule.process_person_shifts")
    @patch("schedule_handler.overrideSchedule.lookup_user_id")
    @patch("schedule_handler.overrideSchedule.delete_all_future_overrides")
    @patch("schedule_handler.overrideSchedule.load_schedule_data")
    @patch("schedule_handler.overrideSchedule.make_headers")
    @patch("schedule_handler.overrideSchedule.load_config")
    def test_main_fail_fast_when_step1_fails(
        self,
        load_config_mock,
        make_headers_mock,
        load_schedule_data_mock,
        delete_overrides_mock,
        lookup_user_id_mock,
        process_person_shifts_mock,
    ):
        load_config_mock.return_value = {"pagerduty_api_key": "k", "schedule_id": "S1"}
        make_headers_mock.return_value = {"Authorization": "Token token=k"}
        load_schedule_data_mock.return_value = {
            "_shifts_mapping": {"D": {"start": "09:00", "end": "17:00"}},
            "Alice": [{"date": "2026-01-01", "shift": "D"}],
        }
        delete_overrides_mock.return_value = False

        with self.assertRaises(RuntimeError):
            main()

        lookup_user_id_mock.assert_not_called()
        process_person_shifts_mock.assert_not_called()

    @patch("schedule_handler.overrideSchedule.process_person_shifts")
    @patch("schedule_handler.overrideSchedule.lookup_user_id")
    @patch("schedule_handler.overrideSchedule.delete_all_future_overrides")
    @patch("schedule_handler.overrideSchedule.load_schedule_data")
    @patch("schedule_handler.overrideSchedule.make_headers")
    @patch("schedule_handler.overrideSchedule.load_config")
    def test_main_fails_when_any_user_lookup_fails_and_skips_step3(
        self,
        load_config_mock,
        make_headers_mock,
        load_schedule_data_mock,
        delete_overrides_mock,
        lookup_user_id_mock,
        process_person_shifts_mock,
    ):
        load_config_mock.return_value = {"pagerduty_api_key": "k", "schedule_id": "S1"}
        make_headers_mock.return_value = {"Authorization": "Token token=k"}
        load_schedule_data_mock.return_value = {
            "_shifts_mapping": {"D": {"start": "09:00", "end": "17:00"}},
            "Alice": [{"date": "2026-01-01", "shift": "D"}],
            "Bob": [{"date": "2026-01-02", "shift": "D"}],
        }
        delete_overrides_mock.return_value = True
        lookup_user_id_mock.side_effect = ["UA", None]

        with self.assertRaises(RuntimeError):
            main()

        process_person_shifts_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
