"""
scheduler.py
護理排班求解核心 — 使用 Google OR-Tools CP-SAT
"""

from dataclasses import dataclass, field
from typing import Optional
import calendar
from ortools.sat.python import cp_model


# ── 班別常數 ────────────────────────────────────────────
SHIFT_OFF  = 0   # 休假
SHIFT_DAY  = 1   # 白班  07:00–15:00
SHIFT_EVE  = 2   # 小夜  15:00–23:00
SHIFT_NIGHT= 3   # 大夜  23:00–07:00

SHIFT_NAMES = {
    SHIFT_OFF:   "休",
    SHIFT_DAY:   "白",
    SHIFT_EVE:   "小夜",
    SHIFT_NIGHT: "大夜",
}

SHIFT_COLORS = {
    "休":   "#F1EFE8",
    "白":   "#E1F5EE",
    "小夜": "#E6F1FB",
    "大夜": "#EEEDFE",
}

# 大夜隔天不可排白班（禁止銜接）
FORBIDDEN_SUCCESSIONS = [
    (SHIFT_NIGHT, SHIFT_DAY),
]


@dataclass
class ScheduleConfig:
    year: int
    month: int
    nurses: list[str]

    # 每班最少人數需求
    min_day:   int = 3
    min_eve:   int = 2
    min_night: int = 2

    # 個人限制
    max_consecutive_days: int = 6   # 最多連續上班天數
    max_nights_per_month: int = 8   # 每月大夜上限

    # 個人偏好請求：{護理師名稱: [(天index, shift_id), ...]}
    # 例如 {"王小明": [(4, SHIFT_OFF), (5, SHIFT_OFF)]}
    requests: dict = field(default_factory=dict)


@dataclass
class ScheduleResult:
    status: str          # "optimal" | "feasible" | "infeasible" | "timeout"
    schedule: dict       # {nurse_name: [shift_id per day]}
    stats: dict          # 統計資訊


def solve(config: ScheduleConfig, time_limit_sec: int = 30) -> ScheduleResult:
    """
    主求解函式。
    回傳 ScheduleResult，schedule[護理師名稱] = list of shift_id (長度=當月天數)
    """
    year, month = config.year, config.month
    num_days = calendar.monthrange(year, month)[1]
    nurses   = config.nurses
    n_nurses = len(nurses)
    shifts   = [SHIFT_OFF, SHIFT_DAY, SHIFT_EVE, SHIFT_NIGHT]

    model = cp_model.CpModel()

    # ── 決策變數 ────────────────────────────────────────
    # x[n][d][s] = 1 表示護理師 n 在第 d 天排班別 s
    x = {}
    for n in range(n_nurses):
        for d in range(num_days):
            for s in shifts:
                x[n, d, s] = model.new_bool_var(f"x_n{n}_d{d}_s{s}")

    # ── 硬約束 ──────────────────────────────────────────

    # 1. 每人每天恰好一個班別
    for n in range(n_nurses):
        for d in range(num_days):
            model.add_exactly_one(x[n, d, s] for s in shifts)

    # 2. 每班每天最少人數
    min_req = {
        SHIFT_DAY:   config.min_day,
        SHIFT_EVE:   config.min_eve,
        SHIFT_NIGHT: config.min_night,
    }
    for d in range(num_days):
        for s, req in min_req.items():
            model.add(sum(x[n, d, s] for n in range(n_nurses)) >= req)

    # 3. 禁止班別銜接（大夜隔天不排白班）
    for (s_prev, s_next) in FORBIDDEN_SUCCESSIONS:
        for n in range(n_nurses):
            for d in range(num_days - 1):
                model.add(x[n, d, s_prev] + x[n, d + 1, s_next] <= 1)

    # 4. 最多連續工作天數
    for n in range(n_nurses):
        for d in range(num_days - config.max_consecutive_days):
            model.add(
                sum(
                    1 - x[n, d + k, SHIFT_OFF]
                    for k in range(config.max_consecutive_days + 1)
                ) <= config.max_consecutive_days
            )

    # 5. 每月大夜上限
    for n in range(n_nurses):
        model.add(
            sum(x[n, d, SHIFT_NIGHT] for d in range(num_days))
            <= config.max_nights_per_month
        )

    # ── 軟約束（目標函數）────────────────────────────────
    penalty_terms = []

    # 5a. 個人請求（重量最高）
    for nurse_name, reqs in config.requests.items():
        if nurse_name not in nurses:
            continue
        n = nurses.index(nurse_name)
        for (d, s) in reqs:
            if 0 <= d < num_days:
                # 偏好 s，若排了其他班別則扣分
                for s2 in shifts:
                    if s2 != s:
                        penalty_terms.append(x[n, d, s2] * 10)

    # 5b. 班別分配均衡（各護理師上班天數盡量相等）
    total_work_vars = []
    for n in range(n_nurses):
        work_days = model.new_int_var(0, num_days, f"work_{n}")
        model.add(work_days == sum(1 - x[n, d, SHIFT_OFF] for d in range(num_days)))
        total_work_vars.append(work_days)

    # 最大最小差距最小化
    max_work = model.new_int_var(0, num_days, "max_work")
    min_work = model.new_int_var(0, num_days, "min_work")
    model.add_max_equality(max_work, total_work_vars)
    model.add_min_equality(min_work, total_work_vars)
    imbalance = model.new_int_var(0, num_days, "imbalance")
    model.add(imbalance == max_work - min_work)
    penalty_terms.append(imbalance * 3)

    # 5c. 大夜班均攤
    night_counts = []
    for n in range(n_nurses):
        nc = model.new_int_var(0, num_days, f"nights_{n}")
        model.add(nc == sum(x[n, d, SHIFT_NIGHT] for d in range(num_days)))
        night_counts.append(nc)
    max_night = model.new_int_var(0, num_days, "max_night")
    min_night = model.new_int_var(0, num_days, "min_night")
    model.add_max_equality(max_night, night_counts)
    model.add_min_equality(min_night, night_counts)
    night_imbalance = model.new_int_var(0, num_days, "night_imbal")
    model.add(night_imbalance == max_night - min_night)
    penalty_terms.append(night_imbalance * 5)

    model.minimize(sum(penalty_terms))

    # ── 求解 ────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_sec
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

    # ── 整理結果 ─────────────────────────────────────────
    schedule = {}
    for n, name in enumerate(nurses):
        schedule[name] = [
            next(s for s in shifts if solver.value(x[n, d, s]))
            for d in range(num_days)
        ]

    # 統計
    stats = {}
    for name in nurses:
        row = schedule[name]
        stats[name] = {
            "上班天數": sum(1 for s in row if s != SHIFT_OFF),
            "白班":     row.count(SHIFT_DAY),
            "小夜":     row.count(SHIFT_EVE),
            "大夜":     row.count(SHIFT_NIGHT),
            "休假":     row.count(SHIFT_OFF),
        }

    return ScheduleResult(status=status, schedule=schedule, stats=stats)
