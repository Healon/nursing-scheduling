"""
app.py
護理排班系統 — Streamlit 原型
使用方式：streamlit run app.py
"""

import calendar
import datetime
import pandas as pd
import streamlit as st

from scheduler import (
    ScheduleConfig, solve,
    SHIFT_NAMES, SHIFT_COLORS,
    SHIFT_OFF, SHIFT_DAY, SHIFT_EVE, SHIFT_NIGHT,
    FORBIDDEN_SUCCESSIONS,
)
from export_excel import to_excel

# ── 頁面設定 ────────────────────────────────────────────
st.set_page_config(
    page_title="護理排班系統",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 自訂 CSS ─────────────────────────────────────────────
st.markdown("""
<style>
    .stButton>button { width: 100%; }
    .shift-休   { background:#F1EFE8; padding:2px 6px; border-radius:4px; }
    .shift-白   { background:#D0EDE0; padding:2px 6px; border-radius:4px; }
    .shift-小夜 { background:#B5D4F4; padding:2px 6px; border-radius:4px; }
    .shift-大夜 { background:#CECBF6; padding:2px 6px; border-radius:4px; }
    div[data-testid="stMetricValue"] { font-size: 1.4rem; }
</style>
""", unsafe_allow_html=True)


# ── Session State 初始化 ──────────────────────────────────
def init_state():
    defaults = {
        "nurses":       ["王小明", "李美玲", "陳志偉", "張雅惠", "林佳欣",
                         "黃俊豪", "吳雅婷", "蔡宗翰"],
        "year":         datetime.date.today().year,
        "month":        datetime.date.today().month,
        "min_day":      2,
        "min_eve":      2,
        "min_night":    1,
        "max_consec":   6,
        "max_nights":   8,
        "requests":     {},   # {nurse: [(day_idx, shift_id)]}
        "result":       None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()


# ── Sidebar 導覽 ─────────────────────────────────────────
with st.sidebar:
    st.image("https://img.icons8.com/color/96/hospital.png", width=60)
    st.title("護理排班系統")
    page = st.radio(
        "選擇頁面",
        ["⚙️ 人員與班別設定", "📋 偏好與請假", "📅 產生排班"],
        label_visibility="collapsed",
    )
    st.divider()
    st.caption("Powered by Google OR-Tools + Streamlit")


# ════════════════════════════════════════════════════════════
# 頁面一：人員與班別設定
# ════════════════════════════════════════════════════════════
if page == "⚙️ 人員與班別設定":
    st.header("⚙️ 人員與班別設定")

    col1, col2 = st.columns(2)

    # ── 排班月份 ───────────────────────────────────────
    with col1:
        st.subheader("排班月份")
        y, m = st.columns(2)
        with y:
            st.session_state.year = st.number_input(
                "年份", min_value=2024, max_value=2030,
                value=st.session_state.year
            )
        with m:
            st.session_state.month = st.selectbox(
                "月份", list(range(1, 13)),
                index=st.session_state.month - 1,
                format_func=lambda x: f"{x} 月"
            )
        num_days = calendar.monthrange(
            st.session_state.year, st.session_state.month
        )[1]
        st.info(f"本月共 **{num_days}** 天")

    # ── 人數需求 ───────────────────────────────────────
    with col2:
        st.subheader("每班最少人數需求")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.session_state.min_day = st.number_input(
                "🌞 白班", min_value=1, max_value=10,
                value=st.session_state.min_day
            )
        with c2:
            st.session_state.min_eve = st.number_input(
                "🌆 小夜", min_value=1, max_value=10,
                value=st.session_state.min_eve
            )
        with c3:
            st.session_state.min_night = st.number_input(
                "🌙 大夜", min_value=1, max_value=10,
                value=st.session_state.min_night
            )

    st.divider()

    # ── 護理師名單 ─────────────────────────────────────
    st.subheader("護理師名單")
    col_list, col_edit = st.columns([2, 1])

    with col_list:
        nurses_text = st.text_area(
            "每行一位護理師姓名",
            value="\n".join(st.session_state.nurses),
            height=220,
            help="直接在此編輯名單，每行輸入一個姓名"
        )
        st.session_state.nurses = [
            n.strip() for n in nurses_text.split("\n") if n.strip()
        ]

    with col_edit:
        n_total = len(st.session_state.nurses)
        st.metric("人員總數", f"{n_total} 人")
        st.divider()
        # 可行性估算：每天至少需要 min_required 人上班
        # max_consec=6 → 每月最少休 ~ceil(num_days/7) 天
        min_required = (st.session_state.min_day +
                        st.session_state.min_eve +
                        st.session_state.min_night)
        num_days_cur = calendar.monthrange(
            st.session_state.year, st.session_state.month
        )[1]
        min_off_per_person = max(1, num_days_cur // (st.session_state.max_consec + 1))
        total_off_needed   = n_total * min_off_per_person
        total_off_avail    = (n_total - min_required) * num_days_cur
        if n_total < min_required:
            st.error(f"❌ 人數 ({n_total}) 少於每日最低需求 ({min_required})，無法排班")
        elif total_off_needed > total_off_avail:
            rec = min_required + (total_off_needed // num_days_cur) + 1
            st.warning(f"⚠️ 人數可能不足\n建議至少 **{rec} 人**\n（現有 {n_total} 人，連休規則需要更多休班空間）")
        else:
            st.success("✅ 人數與規則設定可行")

    st.divider()

    # ── 進階限制 ───────────────────────────────────────
    st.subheader("進階限制")
    a1, a2 = st.columns(2)
    with a1:
        st.session_state.max_consec = st.slider(
            "最多連續上班天數", 3, 10,
            value=st.session_state.max_consec,
            help="超過此天數必須休假一天"
        )
    with a2:
        st.session_state.max_nights = st.slider(
            "每月大夜上限（每人）", 2, 15,
            value=st.session_state.max_nights,
        )

    st.info("💡 大夜班後隔天**禁止**排白班（系統自動套用，保護休息時間）")


# ════════════════════════════════════════════════════════════
# 頁面二：偏好與請假
# ════════════════════════════════════════════════════════════
elif page == "📋 偏好與請假":
    st.header("📋 護理師偏好與請假設定")

    if not st.session_state.nurses:
        st.warning("請先至「人員與班別設定」填寫護理師名單。")
        st.stop()

    num_days = calendar.monthrange(
        st.session_state.year, st.session_state.month
    )[1]

    st.markdown(
        f"**{st.session_state.year} 年 {st.session_state.month} 月**　"
        f"請設定各護理師的班別偏好（選填，系統會盡量滿足）"
    )

    # 用 DataFrame 做互動表格
    shift_options = ["（不指定）", "休", "白", "小夜", "大夜"]
    shift_map = {
        "（不指定）": None,
        "休":   SHIFT_OFF,
        "白":   SHIFT_DAY,
        "小夜": SHIFT_EVE,
        "大夜": SHIFT_NIGHT,
    }

    selected_nurse = st.selectbox(
        "選擇護理師",
        st.session_state.nurses,
        key="req_nurse"
    )

    st.caption("點選日期設定偏好班別（不指定 = 讓系統自動安排）")

    # 顯示當前請求
    existing = st.session_state.requests.get(selected_nurse, [])
    existing_map = {d: s for d, s in existing}

    # 用 3 欄 grid 顯示日期
    weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
    cols_per_row  = 7
    days_list     = list(range(num_days))

    # 表頭（星期）
    header_cols = st.columns(cols_per_row)
    for i, wd in enumerate(weekday_names):
        header_cols[i].markdown(
            f"<div style='text-align:center;font-weight:bold;color:gray'>{wd}</div>",
            unsafe_allow_html=True
        )

    # 第一天是星期幾
    first_weekday = calendar.weekday(
        st.session_state.year, st.session_state.month, 1
    )

    # 建立 selectbox grid
    new_requests = {}
    day_idx = 0
    while day_idx < num_days:
        row_cols = st.columns(cols_per_row)
        for col_pos in range(cols_per_row):
            # 第一週空格補位
            if day_idx == 0 and col_pos < first_weekday:
                row_cols[col_pos].write("")
                continue
            if day_idx >= num_days:
                break

            d = day_idx
            wd = calendar.weekday(
                st.session_state.year, st.session_state.month, d + 1
            )
            is_weekend = wd >= 5

            current_label = "（不指定）"
            if d in existing_map:
                rev = {v: k for k, v in shift_map.items() if v is not None}
                current_label = rev.get(existing_map[d], "（不指定）")

            label_color = "#d62" if is_weekend else "#333"
            row_cols[col_pos].markdown(
                f"<div style='text-align:center;font-size:12px;"
                f"color:{label_color};margin-bottom:2px'>{d+1}</div>",
                unsafe_allow_html=True
            )
            selected = row_cols[col_pos].selectbox(
                f"d{d}",
                shift_options,
                index=shift_options.index(current_label),
                key=f"req_{selected_nurse}_{d}",
                label_visibility="collapsed",
            )
            if selected != "（不指定）":
                new_requests[d] = shift_map[selected]

            day_idx += 1

    # 儲存請求
    st.session_state.requests[selected_nurse] = [
        (d, s) for d, s in new_requests.items()
    ]

    # 摘要
    st.divider()
    st.subheader("📊 所有請求摘要")
    summary_rows = []
    for name in st.session_state.nurses:
        reqs = st.session_state.requests.get(name, [])
        if reqs:
            rev = {v: k for k, v in shift_map.items() if v is not None}
            for (d, s) in reqs:
                summary_rows.append({
                    "護理師": name,
                    "日期":   f"{st.session_state.month}/{d+1}",
                    "偏好班別": rev.get(s, "？"),
                })

    if summary_rows:
        st.dataframe(
            pd.DataFrame(summary_rows),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("目前尚無請求，系統將完全依規則自動排班。")

    if st.button("🗑️ 清除所有請求"):
        st.session_state.requests = {}
        st.rerun()


# ════════════════════════════════════════════════════════════
# 頁面三：產生排班
# ════════════════════════════════════════════════════════════
elif page == "📅 產生排班":
    st.header("📅 產生排班結果")

    if not st.session_state.nurses:
        st.warning("請先至「人員與班別設定」填寫護理師名單。")
        st.stop()

    num_days = calendar.monthrange(
        st.session_state.year, st.session_state.month
    )[1]

    # ── 設定摘要 ───────────────────────────────────────
    with st.expander("📋 當前設定摘要", expanded=False):
        c1, c2, c3 = st.columns(3)
        c1.metric("排班月份", f"{st.session_state.year}/{st.session_state.month}")
        c2.metric("護理師人數", f"{len(st.session_state.nurses)} 人")
        c3.metric("排班天數", f"{num_days} 天")
        c1.metric("白班最少", f"{st.session_state.min_day} 人")
        c2.metric("小夜最少", f"{st.session_state.min_eve} 人")
        c3.metric("大夜最少", f"{st.session_state.min_night} 人")

    # ── 求解按鈕 ───────────────────────────────────────
    col_btn, col_note = st.columns([1, 2])
    with col_btn:
        run_btn = st.button("🚀 開始自動排班", type="primary", use_container_width=True)
    with col_note:
        st.caption("求解時間視規模而定，通常 5–30 秒內完成。")

    if run_btn:
        config = ScheduleConfig(
            year=st.session_state.year,
            month=st.session_state.month,
            nurses=st.session_state.nurses,
            min_day=st.session_state.min_day,
            min_eve=st.session_state.min_eve,
            min_night=st.session_state.min_night,
            max_consecutive_days=st.session_state.max_consec,
            max_nights_per_month=st.session_state.max_nights,
            requests=st.session_state.requests,
        )

        with st.spinner("⏳ 求解中，請稍候..."):
            result = solve(config, time_limit_sec=30)

        st.session_state.result = result

        if result.status == "infeasible":
            st.error("❌ 找不到可行排班！請檢查：人數是否足夠？限制條件是否過嚴？")
        elif result.status == "timeout":
            st.warning("⚠️ 求解超時，已回傳目前最佳解，可能未達最優。")
        elif result.status in ("optimal", "feasible"):
            st.success(f"✅ 排班完成！（狀態：{'最優解' if result.status=='optimal' else '可行解'}）")

    # ── 顯示結果 ───────────────────────────────────────
    result = st.session_state.result
    if result is None or not result.schedule:
        st.info("請按上方「開始自動排班」按鈕產生排班表。")
        st.stop()

    st.divider()
    st.subheader("📅 排班表")

    # 建立 DataFrame 顯示
    weekday_ch = ["一", "二", "三", "四", "五", "六", "日"]
    col_labels = []
    for d in range(num_days):
        wd = calendar.weekday(st.session_state.year, st.session_state.month, d + 1)
        col_labels.append(f"{d+1}({weekday_ch[wd]})")

    rows = {}
    for name, shifts in result.schedule.items():
        rows[name] = [SHIFT_NAMES[s] for s in shifts]

    df = pd.DataFrame(rows, index=col_labels).T

    # 色彩標示
    def color_cell(val):
        color_map = {
            "休":   "background-color:#F1EFE8",
            "白":   "background-color:#D0EDE0",
            "小夜": "background-color:#B5D4F4",
            "大夜": "background-color:#CECBF6",
        }
        return color_map.get(val, "")

    styled = df.style.applymap(color_cell)
    st.dataframe(styled, use_container_width=True, height=350)

    # ── 每日人數統計 ────────────────────────────────────
    st.subheader("📊 每日班別人數")
    daily_stats = {"日期": col_labels, "白班": [], "小夜": [], "大夜": []}
    for d in range(num_days):
        day_shifts = [result.schedule[n][d] for n in result.schedule]
        daily_stats["白班"].append(day_shifts.count(SHIFT_DAY))
        daily_stats["小夜"].append(day_shifts.count(SHIFT_EVE))
        daily_stats["大夜"].append(day_shifts.count(SHIFT_NIGHT))

    df_daily = pd.DataFrame(daily_stats).set_index("日期")
    st.dataframe(df_daily.T, use_container_width=True)

    # ── 個人統計 ────────────────────────────────────────
    st.subheader("📊 個人班別統計")
    stats_df = pd.DataFrame(result.stats).T.reset_index()
    stats_df.columns = ["護理師", "上班天數", "白班", "小夜", "大夜", "休假"]
    st.dataframe(stats_df, use_container_width=True, hide_index=True)

    # ── 請求滿足率 ──────────────────────────────────────
    total_req = sum(len(v) for v in st.session_state.requests.values())
    if total_req > 0:
        satisfied = 0
        for name, reqs in st.session_state.requests.items():
            if name not in result.schedule:
                continue
            for (d, s) in reqs:
                if 0 <= d < num_days and result.schedule[name][d] == s:
                    satisfied += 1
        pct = int(satisfied / total_req * 100)
        st.metric("班別請求滿足率", f"{pct}%",
                  help=f"{satisfied}/{total_req} 筆請求已滿足")

    # ── 下載按鈕 ────────────────────────────────────────
    st.divider()
    st.subheader("💾 匯出")
    excel_bytes = to_excel(
        result,
        st.session_state.year,
        st.session_state.month,
    )
    filename = f"排班表_{st.session_state.year}{st.session_state.month:02d}.xlsx"
    st.download_button(
        label="📥 下載 Excel 排班表",
        data=excel_bytes,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        use_container_width=True,
    )
