import json
import pandas as pd
from datetime import datetime, time, date, timedelta
from typing import List, Dict, Optional
from pathlib import Path
import gspread
from oauth2client.service_account import ServiceAccountCredentials

SERVICE_ACCOUNT_KEY_FILE = "credentials.json"
GOOGLE_SHEET_ID = '1E91raFGsy9OfZP5J_fd0GnHQw3cKjjo8ZSpdII0zfzQ'
SHEET_NAME = 'Orderly Mar 2026'
TODAY_DATE = date.today()
TARGET_PERSON_LIST = ['allen', 'Emma', 'Tony', 'Abel']
YEAR = 2026

# 输出文件路径
OUTPUT_FILE = Path(__file__).parent / "schedule_data.json"

# --- 定义时间范围常量和映射 ---
TIME_RANGE_PRIMARY = (time(8, 30), time(17, 30))
TIME_RANGE_EVENING = (time(17, 30), time(1, 30))
TIME_RANGE_NIGHT = (time(1, 30), time(8, 30))

TIME_RANGE_CONSTANTS = {
    "8:30~17:30 (HKT)": "TIME_RANGE_PRIMARY",
    "17:30~01:30 (HKT)": "TIME_RANGE_EVENING",
    "01:30~08:30 (HKT)": "TIME_RANGE_NIGHT",
}

SHIFTS_MAPPING = {
    "TIME_RANGE_PRIMARY": {"start": "08:30", "end": "17:30"},
    "TIME_RANGE_EVENING": {"start": "17:30", "end": "01:30"},
    "TIME_RANGE_NIGHT": {"start": "01:30", "end": "08:30"},
}


def download_sheet_to_dataframe() -> Optional[pd.DataFrame]:
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_KEY_FILE, scope)
        client = gspread.authorize(creds)

        spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = spreadsheet.worksheet(SHEET_NAME)

        data = worksheet.get_all_values()
        df = pd.DataFrame(data[1:], columns=data[0])
        print(f"Google Sheet '{SHEET_NAME}' 下载成功。")
        return df

    except FileNotFoundError:
        print(f"错误：未找到密钥文件 {SERVICE_ACCOUNT_KEY_FILE}。请检查路径。")
        return None
    except Exception as e:
        print(f"下载 Google Sheet 时发生错误: {e}")
        return None


def parse_date(date_str: str) -> Optional[datetime]:
    try:
        month, day = map(int, date_str.split('/'))
        return datetime(YEAR, month, day)
    except:
        return None


def process_schedule(df: pd.DataFrame, target_person: str) -> Dict[str, List[datetime]]:
    aggregated_shifts: Dict[str, List[datetime]] = {name: [] for name in TIME_RANGE_CONSTANTS.values()}
    date_row_indices = df[df.iloc[:, 0] == 'Time \\ Date'].index.tolist()
    date_columns = df.columns[1:]

    for i in date_row_indices:
        date_row = df.iloc[i]

        dates: Dict[str, datetime] = {}
        for col_name in date_columns:
            base_date = parse_date(date_row[col_name])
            if base_date:
                if base_date.date() >= TODAY_DATE:
                    dates[col_name] = base_date

        if not dates:
            continue

        shift_data_start_index = i + 1

        for j, time_desc in enumerate(TIME_RANGE_CONSTANTS.keys()):
            shift_row_index = shift_data_start_index + j

            if shift_row_index >= len(df):
                break

            shift_data = df.iloc[shift_row_index]
            time_range_constant_name = TIME_RANGE_CONSTANTS[time_desc]

            for col_name, base_date in dates.items():
                person_on_shift = str(shift_data.get(col_name, '')).strip()

                if person_on_shift == target_person:
                    # NIGHT 班次 (01:30~08:30) 实际发生在列日期的下一天
                    actual_date = base_date + timedelta(days=1) if time_range_constant_name == "TIME_RANGE_NIGHT" else base_date
                    aggregated_shifts[time_range_constant_name].append(actual_date)

    return aggregated_shifts


def save_schedule_data(all_shifts: Dict[str, Dict[str, List[str]]]):
    """将排班数据保存为 JSON 文件。"""
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_shifts, f, indent=2, ensure_ascii=False)
    print(f"排班数据已保存到 {OUTPUT_FILE}")


def main():
    schedule_df = download_sheet_to_dataframe()

    if schedule_df is None:
        print("程序终止：无法获取排班数据。")
        return

    all_shifts = {}

    for target_person in TARGET_PERSON_LIST:
        aggregated_shifts = process_schedule(schedule_df, target_person)

        # 转换为可序列化的格式：每条记录包含日期和班次类型
        person_shifts = []
        seen = set()
        for constant_name in ["TIME_RANGE_PRIMARY", "TIME_RANGE_EVENING", "TIME_RANGE_NIGHT"]:
            shifts_list = sorted(aggregated_shifts.get(constant_name, []))
            for date_obj in shifts_list:
                key = (date_obj.strftime("%Y-%m-%d"), constant_name)
                if key not in seen:
                    seen.add(key)
                    person_shifts.append({
                        "date": date_obj.strftime("%Y-%m-%d"),
                        "shift": constant_name,
                    })

        all_shifts[target_person] = person_shifts
        print(f"{target_person}: {len(person_shifts)} 个班次")

    # 附加班次时间映射，供 overrideSchedule 使用
    all_shifts["_shifts_mapping"] = SHIFTS_MAPPING

    save_schedule_data(all_shifts)


if __name__ == '__main__':
    main()
