"""
j3soon_bridge.py
將 Streamlit UI 設定轉換為 j3soon YAML，呼叫 schedule()，並整理回傳結果。

核心引擎：j3soon/nurse-scheduling (AGPL-3.0)
  https://github.com/j3soon/nurse-scheduling

注意：nurse_scheduling/ 套件與本檔案放在同一層目錄，
      Streamlit Cloud 可直接 import，不需要 sys.path 操作。
"""

import math
import calendar
import datetime
import logging
from dataclasses import dataclass, field

import yaml

# nurse_scheduling/ 與 j3soon_bridge.py 同層，直接 import
from nurse_scheduling.scheduler import schedule as j3soon_schedule

logging.disable(logging.CRITICAL)

# ── 班別顯示設定 ────────────────────────────────────────
SHIFT_DISPLAY = {
    "D":   {"label": "白",   "color": "#D0EDE0"},
    "E":   {"label": "小夜", "color": "#B5D4F4"},
    "N":   {"label": "大夜", "color": "#CECBF6"},
    "":    {"label": "休",   "color": "#F1EFE8"},
    "OFF": {"label": "休",   "color": "#F1EFE8"},
}


@dataclass
class UIConfig:
    year:  int
    month: int
    nurses: list

    min_day:   int = 2
    min_eve:   int = 2
    min_night: int = 1

    max_nights_per_month: int = 8

    # {姓名: [(day_index 0-based, '白'/'小夜'/'大夜'/'休'), ...]}
    requests: dict = field(default_factory=dict)

    # 年資（顯示用，不影響排班）
    seniority: dict = field(default_factory=dict)

    timeout: int = 30


@dataclass
class ScheduleResult:
    status:    str
    schedule:  dict   # {姓名: ["白"|"小夜"|"大夜"|"休", ...]}
    stats:     dict
    yaml_input: str


def build_yaml(config: UIConfig) -> str:
    year, month = config.year, config.month
    num_days    = calendar.monthrange(year, month)[1]
    start_date  = datetime.date(year, month, 1)
    end_date    = datetime.date(year, month, num_days)

    nurse_ids  = [f"N{i+1:02d}" for i in range(len(config.nurses))]
    name_to_id = dict(zip(config.nurses, nurse_ids))

    people_items = [
        {"id": nid, "description": name}
        for nid, name in zip(nurse_ids, config.nurses)
    ]

    preferences = [
        {"type": "at most one shift per day"},

        # 每班最少人數（硬約束 weight=+inf）
        {"type": "shift type requirement", "shiftType": "D",
         "requiredNumPeople": config.min_day,   "weight": math.inf},
        {"type": "shift type requirement", "shiftType": "E",
         "requiredNumPeople": config.min_eve,   "weight": math.inf},
        {"type": "shift type requirement", "shiftType": "N",
         "requiredNumPeople": config.min_night, "weight": math.inf},

        # 禁止銜接（weight=-inf 代表禁止此模式）
        {"type": "shift type successions", "person": "ALL",
         "pattern": ["N", "D"], "weight": -math.inf},
        {"type": "shift type successions", "person": "ALL",
         "pattern": ["E", "D"], "weight": -math.inf},

        # 大夜每月上限（硬約束）
        {"type": "shift count", "person": "ALL",
         "countDates": "ALL", "countShiftTypes": "N",
         "expression": "x <= T", "target": config.max_nights_per_month,
         "weight": math.inf},

        # 上班天數均衡（軟）
        {"type": "shift count", "person": "ALL",
         "countDates": "ALL", "countShiftTypes": ["D", "E", "N"],
         "expression": "|x - T|^2",
         "target": "round(AVG_SHIFTS_PER_PERSON)", "weight": -500},

        # 休假天數均衡（軟）
        {"type": "shift count", "person": "ALL",
         "countDates": "ALL", "countShiftTypes": "OFF",
         "expression": "|x - T|^2",
         "target": "round(AVG_SHIFTS_PER_PERSON)", "weight": -500},

        # 週末出勤均攤（j3soon 內建 WEEKEND 關鍵字）（軟）
        {"type": "shift count", "person": "ALL",
         "countDates": "WEEKEND", "countShiftTypes": ["D", "E", "N"],
         "expression": "|x - T|^2",
         "target": "round(AVG_SHIFTS_PER_PERSON)", "weight": -800},

        # 大夜班均攤（軟）
        {"type": "shift count", "person": "ALL",
         "countDates": "ALL", "countShiftTypes": "N",
         "expression": "|x - T|^2",
         "target": "round(AVG_SHIFTS_PER_PERSON)", "weight": -600},
    ]

    # 個人班別請求
    shift_label_to_id = {
        "白": "D", "小夜": "E", "大夜": "N", "休": "OFF",
        "D": "D",  "E": "E",   "N": "N",    "OFF": "OFF",
    }
    for name, reqs in config.requests.items():
        nid = name_to_id.get(name)
        if not nid:
            continue
        for day_idx, shift_label in reqs:
            sid = shift_label_to_id.get(shift_label)
            if not sid:
                continue
            date_str = str(start_date + datetime.timedelta(days=day_idx))
            preferences.append({
                "type": "shift request",
                "person": nid,
                "date": date_str,
                "shiftType": sid,
                "weight": 200,
            })

    data = {
        "apiVersion": "alpha",
        "country":    "TW",
        "dates": {"range": {
            "startDate": str(start_date),
            "endDate":   str(end_date),
        }},
        "people":     {"items": people_items},
        "shiftTypes": {"items": [
            {"id": "D", "description": "白班 07:00-15:00"},
            {"id": "E", "description": "小夜 15:00-23:00"},
            {"id": "N", "description": "大夜 23:00-07:00"},
        ]},
        "preferences": preferences,
    }
    return yaml.dump(data, allow_unicode=True,
                     default_flow_style=False, sort_keys=False)


def run(config: UIConfig) -> ScheduleResult:
    year, month = config.year, config.month
    num_days    = calendar.monthrange(year, month)[1]
    nurse_ids   = [f"N{i+1:02d}" for i in range(len(config.nurses))]
    id_to_name  = dict(zip(nurse_ids, config.nurses))

    yaml_str = build_yaml(config)

    try:
        df, solution, obj, status, _ = j3soon_schedule(
            yaml_str.encode(), timeout=config.timeout
        )
    except Exception as e:
        return ScheduleResult(
            status="ERROR", schedule={}, stats={}, yaml_input=yaml_str)

    if df is None:
        return ScheduleResult(
            status=status, schedule={}, stats={}, yaml_input=yaml_str)

    # 解析 df → schedule dict
    rows      = df.values.tolist()
    data_rows = rows[2:-2]

    raw = {}
    for row in data_rows:
        pid    = str(row[0]).strip()
        shifts = [str(v).strip() for v in row[1:]]
        raw[pid] = shifts

    schedule_out = {}
    for pid, name in id_to_name.items():
        shifts_raw = (raw.get(pid, []) + [""] * num_days)[:num_days]
        schedule_out[name] = [
            SHIFT_DISPLAY.get(s, SHIFT_DISPLAY[""]).get("label", "休")
            for s in shifts_raw
        ]

    weekend_days = [
        d for d in range(num_days)
        if calendar.weekday(year, month, d + 1) >= 5
    ]

    stats = {}
    for name in config.nurses:
        row = schedule_out.get(name, ["休"] * num_days)
        stats[name] = {
            "上班天數": sum(1 for s in row if s != "休"),
            "白班":     row.count("白"),
            "小夜":     row.count("小夜"),
            "大夜":     row.count("大夜"),
            "休假":     row.count("休"),
            "週末出勤": sum(1 for d in weekend_days if row[d] != "休"),
            "年資(年)": round(config.seniority.get(name, 0.0), 1),
        }

    return ScheduleResult(
        status=status,
        schedule=schedule_out,
        stats=stats,
        yaml_input=yaml_str,
    )
