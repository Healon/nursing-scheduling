"""
scheduler_core.py
自包含排班引擎 — 整合 j3soon/nurse-scheduling 的約束設計 + Google OR-Tools CP-SAT

不依賴任何本地套件資料夾，只需 pip install ortools pyyaml pydantic holidays
"""

import math
import calendar
import datetime
import logging
from dataclasses import dataclass, field

# ── OR-Tools CP-SAT ─────────────────────────────────────
from ortools.sat.python import cp_model

# ── 台灣國定假日 ────────────────────────────────────────
try:
    import holidays
    _TW_HOLIDAYS = holidays.Taiwan()
    def is_tw_holiday(d: datetime.date) -> bool:
        return d in _TW_HOLIDAYS or d.weekday() >= 5
except ImportError:
    def is_tw_holiday(d: datetime.date) -> bool:
        return d.weekday() >= 5

# ── 班別常數 ────────────────────────────────────────────
SHIFT_OFF   = 0
SHIFT_DAY   = 1   # 白班
SHIFT_EVE   = 2   # 小夜
SHIFT_NIGHT = 3   # 大夜

SHIFT_NAMES = {SHIFT_OFF: "休", SHIFT_DAY: "白",
               SHIFT_EVE: "小夜", SHIFT_NIGHT: "大夜"}

SHIFT_COLORS = {
    "休":   "#F1EFE8",
    "白":   "#D0EDE0",
    "小夜": "#B5D4F4",
    "大夜": "#CECBF6",
}

WORK_SHIFTS = [SHIFT_DAY, SHIFT_EVE, SHIFT_NIGHT]

# 禁止銜接（前一天 → 次日）
# 大夜/小夜 → 白班：休息不足
FORBIDDEN = [
    (SHIFT_NIGHT, SHIFT_DAY),
    (SHIFT_EVE,   SHIFT_DAY),
]


@dataclass
class ScheduleConfig:
    year:  int
    month: int
    nurses: list           # 護理師姓名清單

    min_day:   int = 2
    min_eve:   int = 2
    min_night: int = 1

    max_nights_per_month: int = 8

    # {姓名: [(day_index 0-based, shift_id), ...]}
    requests: dict = field(default_factory=dict)

    # 年資（顯示用，不影響排班）
    seniority: dict = field(default_factory=dict)

    timeout: int = 30


@dataclass
class ScheduleResult:
    status:   str     # "optimal" | "feasible" | "infeasible" | "timeout"
    schedule: dict    # {姓名: ["白"|"小夜"|"大夜"|"休", ...]}
    stats:    dict


def solve(config: ScheduleConfig) -> ScheduleResult:
    year, month = config.year, config.month
    num_days    = calendar.monthrange(year, month)[1]
    nurses      = config.nurses
    n_nurses    = len(nurses)
    shifts      = [SHIFT_OFF, SHIFT_DAY, SHIFT_EVE, SHIFT_NIGHT]

    # 週末 + 台灣國定假日
    freedays = [
        d for d in range(num_days)
        if is_tw_holiday(datetime.date(year, month, d + 1))
    ]
    workdays = [d for d in range(num_days) if d not in freedays]

    model = cp_model.CpModel()

    # ── 決策變數：x[n, d, s] ∈ {0,1} ─────────────────────
    x = {}
    for n in range(n_nurses):
        for d in range(num_days):
            for s in shifts:
                x[n, d, s] = model.new_bool_var(f"x{n}_{d}_{s}")

    # ══════════════════════════════════════════════════════
    # 硬約束
    # ══════════════════════════════════════════════════════

    # H1. 每人每天恰好一班
    for n in range(n_nurses):
        for d in range(num_days):
            model.add_exactly_one(x[n, d, s] for s in shifts)

    # H2. 每班每天最少人數
    min_req = {SHIFT_DAY: config.min_day,
               SHIFT_EVE: config.min_eve,
               SHIFT_NIGHT: config.min_night}
    for d in range(num_days):
        for s, req in min_req.items():
            model.add(sum(x[n, d, s] for n in range(n_nurses)) >= req)

    # H3. 禁止班別銜接（大夜/小夜 → 白班）
    for s_prev, s_next in FORBIDDEN:
        for n in range(n_nurses):
            for d in range(num_days - 1):
                model.add(x[n, d, s_prev] + x[n, d + 1, s_next] <= 1)

    # H4. 每月大夜上限
    for n in range(n_nurses):
        model.add(
            sum(x[n, d, SHIFT_NIGHT] for d in range(num_days))
            <= config.max_nights_per_month
        )

    # ══════════════════════════════════════════════════════
    # 軟約束（最小化懲罰）
    # ══════════════════════════════════════════════════════
    penalty = []

    # S1. 個人班別請求（權重最高）
    for name, reqs in config.requests.items():
        if name not in nurses:
            continue
        n = nurses.index(name)
        for d, s_want in reqs:
            if 0 <= d < num_days:
                for s2 in shifts:
                    if s2 != s_want:
                        penalty.append(x[n, d, s2] * 10)

    def add_balance(counts_vars, weight, max_val):
        """把 max-min 差距加入懲罰"""
        hi = model.new_int_var(0, max_val, f"hi_{weight}_{id(counts_vars)}")
        lo = model.new_int_var(0, max_val, f"lo_{weight}_{id(counts_vars)}")
        model.add_max_equality(hi, counts_vars)
        model.add_min_equality(lo, counts_vars)
        gap = model.new_int_var(0, max_val, f"gap_{weight}_{id(counts_vars)}")
        model.add(gap == hi - lo)
        penalty.append(gap * weight)

    # S2. 上班天數均衡（j3soon 對應：shift count ALL work shifts MSE，權重 500）
    work_vars = []
    for n in range(n_nurses):
        wv = model.new_int_var(0, num_days, f"work{n}")
        model.add(wv == sum(1 - x[n, d, SHIFT_OFF] for d in range(num_days)))
        work_vars.append(wv)
    add_balance(work_vars, 3, num_days)

    # S3. 休假天數均衡（j3soon 對應：shift count OFF MSE，權重 500）
    off_vars = []
    for n in range(n_nurses):
        ov = model.new_int_var(0, num_days, f"off{n}")
        model.add(ov == sum(x[n, d, SHIFT_OFF] for d in range(num_days)))
        off_vars.append(ov)
    add_balance(off_vars, 4, num_days)

    # S4. 週末/假日出勤均攤（j3soon 對應：countDates=WEEKEND，權重 800）
    if freedays:
        wknd_vars = []
        for n in range(n_nurses):
            wv = model.new_int_var(0, len(freedays), f"wknd{n}")
            model.add(wv == sum(1 - x[n, d, SHIFT_OFF] for d in freedays))
            wknd_vars.append(wv)
        add_balance(wknd_vars, 6, len(freedays))

    # S5. 大夜班均攤（j3soon 對應：countShiftTypes=N MSE，權重 600）
    night_vars = []
    for n in range(n_nurses):
        nv = model.new_int_var(0, num_days, f"night{n}")
        model.add(nv == sum(x[n, d, SHIFT_NIGHT] for d in range(num_days)))
        night_vars.append(nv)
    add_balance(night_vars, 5, num_days)

    model.minimize(sum(penalty))

    # ── 求解 ─────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = config.timeout
    solver.parameters.num_search_workers  = 4
    status_code = solver.solve(model)

    status_map = {
        cp_model.OPTIMAL:    "optimal",
        cp_model.FEASIBLE:   "feasible",
        cp_model.INFEASIBLE: "infeasible",
        cp_model.UNKNOWN:    "timeout",
    }
    status = status_map.get(status_code, "unknown")

    if status in ("infeasible", "timeout", "unknown"):
        return ScheduleResult(status=status, schedule={}, stats={})

    # ── 整理結果 ──────────────────────────────────────────
    schedule = {}
    for n, name in enumerate(nurses):
        row = [next(s for s in shifts if solver.value(x[n, d, s]))
               for d in range(num_days)]
        schedule[name] = [SHIFT_NAMES[s] for s in row]

    stats = {}
    for name in nurses:
        row = schedule[name]
        stats[name] = {
            "上班天數": sum(1 for s in row if s != "休"),
            "白班":     row.count("白"),
            "小夜":     row.count("小夜"),
            "大夜":     row.count("大夜"),
            "休假":     row.count("休"),
            "週末出勤": sum(1 for d in freedays if row[d] != "休"),
            "年資(年)": round(config.seniority.get(name, 0.0), 1),
        }

    return ScheduleResult(status=status, schedule=schedule, stats=stats)
