import json
import tomllib
import requests
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

TZ_UTC_PLUS_8 = timezone(timedelta(hours=8))
BASE_URL = "https://api.pagerduty.com"
API_VERSION_HEADER = "application/vnd.pagerduty+json;version=2"

SCHEDULE_DATA_FILE = Path(__file__).parent / "schedule_data.json"
CONFIG_FILE = Path(__file__).parent.parent / "config.toml"


def load_config() -> dict:
    """从 config.toml 加载配置。"""
    with open(CONFIG_FILE, "rb") as f:
        config = tomllib.load(f)
    api_key = config.get("pagerduty_api_key")
    if not api_key:
        raise ValueError("config.toml 中缺少 pagerduty_api_key")
    return config


def make_headers(api_key: str) -> dict:
    return {
        "Accept": API_VERSION_HEADER,
        "Authorization": f"Token token={api_key}",
        "Content-Type": "application/json",
    }


def lookup_user_id(headers: dict, name: str) -> Optional[str]:
    """通过 PagerDuty API 按名字查找用户 ID。"""
    try:
        response = requests.get(
            f"{BASE_URL}/users",
            headers=headers,
            params={"query": name},
        )
        response.raise_for_status()
        users = response.json().get("users", [])
        if users:
            user = users[0]
            print(f"  {name} -> {user['name']} (ID: {user['id']})")
            return user["id"]
        else:
            print(f"  未找到用户: {name}")
            return None
    except Exception as e:
        print(f"  查找用户 {name} 失败: {e}")
        return None


def load_schedule_data() -> dict:
    """从 JSON 文件加载排班数据。"""
    with open(SCHEDULE_DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_time(time_str: str) -> time:
    """将 'HH:MM' 字符串解析为 time 对象。"""
    h, m = map(int, time_str.split(":"))
    return time(h, m)


def delete_single_override(headers: dict, schedule_id: str, override_id: str) -> bool:
    url = f"{BASE_URL}/schedules/{schedule_id}/overrides/{override_id}"
    try:
        response = requests.delete(url, headers=headers)
        if response.status_code in (200, 204):
            print(f"   [OK] 覆盖 {override_id} 已删除。")
            return True
        else:
            response.raise_for_status()
    except requests.exceptions.HTTPError as err:
        print(f"   [ERROR] 删除 {override_id} 失败: {err}")
        return False
    except Exception as e:
        print(f"   [ERROR] 发生未知错误：{e}")
        return False


def delete_all_future_overrides(headers: dict, schedule_id: str, lookahead_days=365):
    now_utc = datetime.now(timezone.utc)
    future_utc = now_utc + timedelta(days=lookahead_days)

    params = {
        "since": now_utc.isoformat().replace('+00:00', 'Z'),
        "until": future_utc.isoformat().replace('+00:00', 'Z'),
        "editable": "true",
    }

    list_url = f"{BASE_URL}/schedules/{schedule_id}/overrides"
    print(f"--- 正在获取排班表 {schedule_id} 的未来覆盖记录 ---")

    try:
        response = requests.get(list_url, headers=headers, params=params)
        response.raise_for_status()
        overrides = response.json().get('overrides', [])

        if not overrides:
            print("未找到任何未来的值班覆盖，无需删除。")
            return True

        print(f"找到 {len(overrides)} 个未来的值班覆盖，准备删除...")

        deleted_count = 0
        for override in overrides:
            if delete_single_override(headers, schedule_id, override.get('id')):
                deleted_count += 1

        print(f"批量删除完成。成功 {deleted_count}/{len(overrides)} 个。")
        return True

    except requests.exceptions.HTTPError as err:
        print(f"获取 Override 列表失败: {err}")
        return False


def create_override(headers: dict, schedule_id: str, user_id: str,
                    start_time_utc: datetime, end_time_utc: datetime) -> bool:
    url = f"{BASE_URL}/schedules/{schedule_id}/overrides"

    start_iso = start_time_utc.isoformat().replace('+00:00', 'Z')
    end_iso = end_time_utc.isoformat().replace('+00:00', 'Z')

    payload = {
        "override": {
            "start": start_iso,
            "end": end_iso,
            "user": {"id": user_id, "type": "user_reference"},
        }
    }

    print(f"-> 创建覆盖: {start_iso} - {end_iso}")

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        print(f"   成功 (ID: {response.json()['override']['id']})")
        return True
    except requests.exceptions.HTTPError as err:
        print(f"   API 请求失败：{err}")
        try:
            error_details = response.json().get('error', {}).get('message', response.text)
            print(f"   详细错误: {error_details}")
        except:
            print(f"   详细错误: {response.text}")
        return False
    except Exception as e:
        print(f"   发生其他错误：{e}")
        return False


def calculate_utc_times(base_date: datetime, start_time_local: time, end_time_local: time):
    start_datetime_local = base_date.replace(
        hour=start_time_local.hour, minute=start_time_local.minute
    ).replace(tzinfo=TZ_UTC_PLUS_8)

    end_datetime_local = base_date.replace(
        hour=end_time_local.hour, minute=end_time_local.minute
    ).replace(tzinfo=TZ_UTC_PLUS_8)

    # 处理跨天（如 17:30~01:30）
    if end_time_local <= start_time_local:
        end_datetime_local += timedelta(days=1)

    return start_datetime_local.astimezone(timezone.utc), end_datetime_local.astimezone(timezone.utc)


def process_person_shifts(headers: dict, schedule_id: str, user_id: str,
                          shifts: list, shifts_mapping: dict):
    print(f"--- 开始批量创建覆盖 ({len(shifts)} 个班次) ---")

    for entry in shifts:
        base_date = datetime.strptime(entry["date"], "%Y-%m-%d")
        shift_info = shifts_mapping[entry["shift"]]
        start_time_local = parse_time(shift_info["start"])
        end_time_local = parse_time(shift_info["end"])

        start_utc, end_utc = calculate_utc_times(base_date, start_time_local, end_time_local)
        create_override(headers, schedule_id, user_id, start_utc, end_utc)

    print("--- 批量创建完成 ---\n")


def main():
    config = load_config()
    api_key = config["pagerduty_api_key"]
    schedule_id = config.get("schedule_id", "PC51GR2")
    headers = make_headers(api_key)

    # 加载排班数据
    schedule_data = load_schedule_data()
    shifts_mapping = schedule_data.pop("_shifts_mapping")

    # 先清除所有未来的 override，再重新创建
    print("=== 第 1 步：清除现有的未来 override ===")
    delete_all_future_overrides(headers, schedule_id)

    # 查找所有人的用户 ID
    print("\n=== 第 2 步：查找 PagerDuty 用户 ID ===")
    user_ids = {}
    for person_name in schedule_data:
        user_id = lookup_user_id(headers, person_name)
        if user_id:
            user_ids[person_name] = user_id

    # 为每个人创建覆盖
    print("\n=== 第 3 步：批量创建 override ===")
    for person_name, shifts in schedule_data.items():
        if person_name not in user_ids:
            print(f"跳过 {person_name}：未找到用户 ID")
            continue

        print(f"\n--- {person_name} ---")
        process_person_shifts(headers, schedule_id, user_ids[person_name], shifts, shifts_mapping)


if __name__ == "__main__":
    main()
