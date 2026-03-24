"""
app.py — 護理排班系統
只依賴 requirements.txt 中的標準套件，無本地套件資料夾依賴。
"""

import io
import calendar
import datetime
import pandas as pd
import streamlit as st
import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from scheduler_core import (
    ScheduleConfig, solve, SHIFT_NAMES, SHIFT_COLORS,
    SHIFT_OFF, SHIFT_DAY, SHIFT_EVE, SHIFT_NIGHT,
)

# ── 頁面設定 ─────────────────────────────────────────────
st.set_page_config(
    page_title="護理排班系統",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.stButton>button { width: 100%; }
div[data-testid="stMetricValue"] { font-size: 1.4rem; }
</style>
""", unsafe_allow_html=True)


# ── Session State ─────────────────────────────────────────
def init_state():
    defaults = {
        "nurses":     ["護理師01", "護理師02", "護理師03", "護理師04",
                       "護理師05", "護理師06", "護理師07", "護理師08"],
        "seniority":  {},
        "year":       datetime.date.today().year,
        "month":      datetime.date.today().month,
        "min_day":    2,
        "min_eve":    2,
        "min_night":  1,
        "max_nights": 8,
        "requests":   {},
        "result":     None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()


# ── Sidebar ──────────────────────────────────────────────
with st.sidebar:
    st.image("https://img.icons8.com/color/96/hospital.png", width=56)
    st.title("護理排班系統")
    page = st.radio(
        "頁面",
        ["⚙️ 人員與班別設定", "🎓 年資設定",
         "📋 偏好與請假", "📅 產生排班"],
        label_visibility="collapsed",
    )
    st.divider()
    st.caption("排班引擎：Google OR-Tools CP-SAT")
    st.caption("約束設計參考：j3soon/nurse-scheduling")


# ════════════════════════════════════════════════════════
# 頁面一：人員與班別設定
# ════════════════════════════════════════════════════════
if page == "⚙️ 人員與班別設定":
    st.header("⚙️ 人員與班別設定")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("排班月份")
        yc, mc = st.columns(2)
        with yc:
            st.session_state.year = st.number_input(
                "年份", min_value=2024, max_value=2030,
                value=st.session_state.year)
        with mc:
            st.session_state.month = st.selectbox(
                "月份", list(range(1, 13)),
                index=st.session_state.month - 1,
                format_func=lambda x: f"{x} 月")
        num_days = calendar.monthrange(
            st.session_state.year, st.session_state.month)[1]
        st.info(f"本月共 **{num_days}** 天")

    with col2:
        st.subheader("每班最少人數需求")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.session_state.min_day = st.number_input(
                "🌞 白班", min_value=1, max_value=10,
                value=st.session_state.min_day)
        with c2:
            st.session_state.min_eve = st.number_input(
                "🌆 小夜", min_value=1, max_value=10,
                value=st.session_state.min_eve)
        with c3:
            st.session_state.min_night = st.number_input(
                "🌙 大夜", min_value=1, max_value=10,
                value=st.session_state.min_night)

    st.divider()
    st.subheader("護理師名單")
    col_list, col_stat = st.columns([2, 1])

    with col_list:
        nurses_text = st.text_area(
            "✏️ 直接在此修改姓名（每行一人，勿修改程式碼）",
            value="\n".join(st.session_state.nurses),
            height=220,
        )
        new_nurses = [n.strip() for n in nurses_text.split("\n") if n.strip()]
        removed = set(st.session_state.nurses) - set(new_nurses)
        for name in removed:
            st.session_state.seniority.pop(name, None)
            st.session_state.requests.pop(name, None)
        st.session_state.nurses = new_nurses

    with col_stat:
        n_total      = len(st.session_state.nurses)
        min_required = (st.session_state.min_day +
                        st.session_state.min_eve +
                        st.session_state.min_night)
        st.metric("人員總數", f"{n_total} 人")
        st.divider()
        if n_total < min_required:
            st.error(f"❌ 人數不足（每日需 {min_required} 人）")
        elif n_total < min_required + 2:
            st.warning("⚠️ 人數偏少，休假空間有限")
        else:
            st.success("✅ 人數與規則可行")

    st.divider()
    st.subheader("進階限制")
    st.session_state.max_nights = st.slider(
        "每月大夜上限（每人）", 2, 15,
        value=st.session_state.max_nights)

    st.info(
        "💡 系統自動套用：\n"
        "• 大夜 → 白班 禁止\n"
        "• 小夜 → 白班 禁止\n"
        "• 週末 / 假日出勤均攤\n"
        "• 休假天數均攤\n"
        "• 大夜班次均攤\n"
        "• 台灣國定假日自動識別"
    )


# ════════════════════════════════════════════════════════
# 頁面二：年資設定
# ════════════════════════════════════════════════════════
elif page == "🎓 年資設定":
    st.header("🎓 年資設定")
    st.info("年資僅供顯示與參考，不作為排班硬約束。")

    if not st.session_state.nurses:
        st.warning("請先至「人員與班別設定」填寫護理師名單。")
        st.stop()

    st.subheader("各護理師年資（年）")
    cols = st.columns(2)
    for i, name in enumerate(st.session_state.nurses):
        val = cols[i % 2].number_input(
            name, min_value=0.0, max_value=40.0, step=0.5,
            value=float(st.session_state.seniority.get(name, 0.0)),
            key=f"sen_{name}", format="%.1f")
        st.session_state.seniority[name] = val

    st.divider()
    df_sen = pd.DataFrame([
        {"護理師": n, "年資(年)": st.session_state.seniority.get(n, 0.0)}
        for n in st.session_state.nurses
    ]).sort_values("年資(年)", ascending=False).reset_index(drop=True)
    st.dataframe(df_sen, use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════
# 頁面三：偏好與請假
# ════════════════════════════════════════════════════════
elif page == "📋 偏好與請假":
    st.header("📋 護理師偏好與請假設定")

    if not st.session_state.nurses:
        st.warning("請先至「人員與班別設定」填寫護理師名單。")
        st.stop()

    num_days = calendar.monthrange(
        st.session_state.year, st.session_state.month)[1]

    shift_options = ["（不指定）", "休", "白", "小夜", "大夜"]
    shift_map = {
        "（不指定）": None,
        "休": SHIFT_OFF, "白": SHIFT_DAY,
        "小夜": SHIFT_EVE, "大夜": SHIFT_NIGHT,
    }
    rev_map = {v: k for k, v in shift_map.items() if v is not None}

    selected = st.selectbox("選擇護理師", st.session_state.nurses)
    st.caption("設定班別偏好（不指定 = 系統自動安排）")

    existing    = st.session_state.requests.get(selected, [])
    exist_map   = {d: s for d, s in existing}
    wd_names    = ["一", "二", "三", "四", "五", "六", "日"]
    first_wd    = calendar.weekday(
        st.session_state.year, st.session_state.month, 1)

    header_cols = st.columns(7)
    for i, wd in enumerate(wd_names):
        color = "red" if i >= 5 else "gray"
        header_cols[i].markdown(
            f"<div style='text-align:center;font-weight:bold;"
            f"color:{color}'>{wd}</div>", unsafe_allow_html=True)

    new_req = {}
    day_idx = 0
    while day_idx < num_days:
        row_cols = st.columns(7)
        for cp in range(7):
            if day_idx == 0 and cp < first_wd:
                row_cols[cp].write("")
                continue
            if day_idx >= num_days:
                break
            d  = day_idx
            wd = calendar.weekday(
                st.session_state.year, st.session_state.month, d + 1)
            color = "#cc2200" if wd >= 5 else "#333"
            row_cols[cp].markdown(
                f"<div style='text-align:center;font-size:12px;"
                f"color:{color}'>{d+1}</div>", unsafe_allow_html=True)
            cur = rev_map.get(exist_map.get(d), "（不指定）")
            sel = row_cols[cp].selectbox(
                f"d{d}", shift_options,
                index=shift_options.index(cur),
                key=f"req_{selected}_{d}",
                label_visibility="collapsed")
            if sel != "（不指定）":
                new_req[d] = shift_map[sel]
            day_idx += 1

    st.session_state.requests[selected] = [
        (d, s) for d, s in new_req.items()]

    st.divider()
    st.subheader("請求摘要")
    rows = [
        {"護理師": n, "日期": f"{st.session_state.month}/{d+1}",
         "偏好": rev_map.get(s, "？")}
        for n in st.session_state.nurses
        for d, s in st.session_state.requests.get(n, [])
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows),
                     use_container_width=True, hide_index=True)
    else:
        st.info("目前無請求，系統將完全依規則排班。")

    if st.button("🗑️ 清除所有請求"):
        st.session_state.requests = {}
        st.rerun()


# ════════════════════════════════════════════════════════
# 頁面四：產生排班
# ════════════════════════════════════════════════════════
elif page == "📅 產生排班":
    st.header("📅 產生排班結果")

    if not st.session_state.nurses:
        st.warning("請先至「人員與班別設定」填寫護理師名單。")
        st.stop()

    num_days = calendar.monthrange(
        st.session_state.year, st.session_state.month)[1]

    with st.expander("📋 當前設定摘要", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("排班月份",
                  f"{st.session_state.year}/{st.session_state.month}")
        c2.metric("護理師人數",
                  f"{len(st.session_state.nurses)} 人")
        c3.metric("排班天數", f"{num_days} 天")
        c4.metric("大夜上限/人",
                  f"{st.session_state.max_nights} 次")
        c1.metric("白班最少", f"{st.session_state.min_day} 人")
        c2.metric("小夜最少", f"{st.session_state.min_eve} 人")
        c3.metric("大夜最少", f"{st.session_state.min_night} 人")
        c4.metric("請假請求",
                  f"{sum(len(v) for v in st.session_state.requests.values())} 筆")

    col_btn, col_note = st.columns([1, 2])
    with col_btn:
        run_btn = st.button("🚀 開始自動排班", type="primary",
                            use_container_width=True)
    with col_note:
        st.caption("通常 10–30 秒完成，使用 OR-Tools CP-SAT 求解。")

    if run_btn:
        config = ScheduleConfig(
            year=st.session_state.year,
            month=st.session_state.month,
            nurses=st.session_state.nurses,
            min_day=st.session_state.min_day,
            min_eve=st.session_state.min_eve,
            min_night=st.session_state.min_night,
            max_nights_per_month=st.session_state.max_nights,
            seniority=st.session_state.seniority,
            requests=st.session_state.requests,
            timeout=30,
        )
        with st.spinner("⏳ 求解中，請稍候..."):
            result = solve(config)
        st.session_state.result = result

        if result.status in ("optimal", "feasible"):
            label = "最優解" if result.status == "optimal" else "可行解"
            st.success(f"✅ 排班完成！（{label}）")
        elif result.status == "infeasible":
            st.error(
                "❌ 找不到可行排班！可能原因：\n"
                "• 每班需求人數加總接近總人數（休假空間不足）\n"
                "• 大夜上限過嚴\n"
                "• 請假請求衝突過多"
            )
        else:
            st.warning(f"⚠️ 狀態：{result.status}")

    result = st.session_state.result
    if result is None or not result.schedule:
        st.info("請按「開始自動排班」產生排班表。")
        st.stop()

    st.divider()

    # ── 排班表 ───────────────────────────────────────────
    st.subheader("📅 排班表")
    wd_ch = ["一", "二", "三", "四", "五", "六", "日"]
    col_labels = []
    for d in range(num_days):
        wd   = calendar.weekday(
            st.session_state.year, st.session_state.month, d + 1)
        mark = "★" if wd >= 5 else ""
        col_labels.append(f"{d+1}{mark}({wd_ch[wd]})")

    color_map = {
        "白":   "background-color:#D0EDE0",
        "小夜": "background-color:#B5D4F4",
        "大夜": "background-color:#CECBF6",
        "休":   "background-color:#F1EFE8",
    }
    df_sched = pd.DataFrame(result.schedule, index=col_labels).T
    st.dataframe(
        df_sched.style.applymap(lambda v: color_map.get(v, "")),
        use_container_width=True, height=360,
    )

    # ── 每日班別人數 ─────────────────────────────────────
    st.subheader("📊 每日班別人數")
    daily = {"日期": col_labels, "白班": [], "小夜": [], "大夜": []}
    for d in range(num_days):
        day_s = [result.schedule[n][d] for n in result.schedule]
        daily["白班"].append(day_s.count("白"))
        daily["小夜"].append(day_s.count("小夜"))
        daily["大夜"].append(day_s.count("大夜"))
    st.dataframe(
        pd.DataFrame(daily).set_index("日期").T,
        use_container_width=True,
    )

    # ── 個人統計 ─────────────────────────────────────────
    st.subheader("📊 個人統計")
    stats_df = pd.DataFrame(result.stats).T.reset_index()
    stats_df.columns = ["護理師", "上班天數", "白班", "小夜",
                        "大夜", "休假", "週末出勤", "年資(年)"]

    max_wk = stats_df["週末出勤"].max()
    min_wk = stats_df["週末出勤"].min()
    max_of = stats_df["休假"].max()
    min_of = stats_df["休假"].min()

    def hl(row):
        s   = [""] * len(row)
        idx = list(stats_df.columns)
        if max_wk > min_wk:
            if row["週末出勤"] == max_wk:
                s[idx.index("週末出勤")] = "background-color:#ffd6cc"
            if row["週末出勤"] == min_wk:
                s[idx.index("週末出勤")] = "background-color:#d0ede0"
        if max_of > min_of:
            if row["休假"] == max_of:
                s[idx.index("休假")] = "background-color:#d0ede0"
            if row["休假"] == min_of:
                s[idx.index("休假")] = "background-color:#ffd6cc"
        return s

    st.dataframe(
        stats_df.style.apply(hl, axis=1),
        use_container_width=True, hide_index=True,
    )
    st.caption("🟥 紅色 = 偏高　🟩 綠色 = 偏低（週末出勤與休假）")

    # ── 均衡指標 ─────────────────────────────────────────
    st.subheader("⚖️ 均衡指標")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("週末出勤差距", f"{max_wk - min_wk} 天")
    m2.metric("休假天數差距", f"{max_of - min_of} 天")
    m3.metric("大夜班差距",
              f"{stats_df['大夜'].max()-stats_df['大夜'].min()} 次")
    m4.metric("上班天數差距",
              f"{stats_df['上班天數'].max()-stats_df['上班天數'].min()} 天")

    # ── 請求滿足率 ────────────────────────────────────────
    total_req = sum(len(v) for v in st.session_state.requests.values())
    if total_req > 0:
        satisfied = sum(
            1
            for name, reqs in st.session_state.requests.items()
            if name in result.schedule
            for d, s in reqs
            if 0 <= d < num_days
            and result.schedule[name][d] == SHIFT_NAMES.get(s, "")
        )
        st.metric("班別請求滿足率",
                  f"{int(satisfied / total_req * 100)}%",
                  help=f"{satisfied}/{total_req} 筆已滿足")

    # ── 匯出 Excel ────────────────────────────────────────
    st.divider()

    def to_excel(result, year, month):
        num_days = calendar.monthrange(year, month)[1]
        nurses   = list(result.schedule.keys())
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"{year}年{month}月排班表"

        THIN   = Side(style="thin", color="FFBBBBBB")
        BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
        CELL_COLORS = {
            "白":   "FFD0EDE0",
            "小夜": "FFB5D4F4",
            "大夜": "FFCECBF6",
            "休":   "FFF1EFE8",
        }

        ws.cell(1, 1,
                f"{year} 年 {month} 月 護理排班表").font = Font(bold=True, size=14)
        ws.cell(1, 1).alignment = Alignment(horizontal="center")
        ws.merge_cells(start_row=1, start_column=1,
                       end_row=1, end_column=num_days + 2)

        wd_ch = ["一", "二", "三", "四", "五", "六", "日"]
        ws.cell(2, 1, "護理師")
        ws.cell(2, 1).font      = Font(bold=True)
        ws.cell(2, 1).alignment = Alignment(horizontal="center")
        ws.column_dimensions["A"].width = 10

        for d in range(num_days):
            col  = d + 2
            wd   = calendar.weekday(year, month, d + 1)
            cell = ws.cell(2, col, f"{d+1}\n({wd_ch[wd]})")
            cell.font      = Font(bold=True, size=9)
            cell.alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True)
            ws.row_dimensions[2].height = 28
            ws.column_dimensions[get_column_letter(col)].width = 5.5
            if wd >= 5:
                cell.fill = PatternFill("solid", fgColor="FFFFF0C0")

        ws.cell(2, num_days + 2, "上班天數")
        ws.column_dimensions[
            get_column_letter(num_days + 2)].width = 8

        for r, name in enumerate(nurses):
            row = r + 3
            ws.cell(row, 1, name).alignment = \
                Alignment(horizontal="center")
            for d, label in enumerate(result.schedule[name]):
                col  = d + 2
                cell = ws.cell(row, col, label)
                cell.alignment = Alignment(
                    horizontal="center", vertical="center")
                cell.border = BORDER
                cell.fill   = PatternFill(
                    "solid", fgColor=CELL_COLORS.get(label, "FFFFFFFF"))
            ws.cell(row, num_days + 2,
                    result.stats[name]["上班天數"]
                    ).alignment = Alignment(horizontal="center")

        stat_row = len(nurses) + 3
        for i, lbl in enumerate(["白班人數", "小夜人數", "大夜人數"]):
            ws.cell(stat_row + i, 1, lbl).font = Font(bold=True, size=9)

        for d in range(num_days):
            col = d + 2
            for i, key in enumerate(["白", "小夜", "大夜"]):
                cnt = sum(1 for n in nurses
                          if result.schedule[n][d] == key)
                ws.cell(stat_row + i, col, cnt).alignment = \
                    Alignment(horizontal="center")

        # 個人統計分頁
        ws2 = wb.create_sheet("個人統計")
        headers = ["護理師", "上班天數", "白班", "小夜",
                   "大夜", "休假", "週末出勤", "年資(年)"]
        for c, h in enumerate(headers, 1):
            cell = ws2.cell(1, c, h)
            cell.font      = Font(bold=True)
            cell.alignment = Alignment(horizontal="center")
        for r, name in enumerate(nurses, 2):
            s = result.stats[name]
            ws2.cell(r, 1, name)
            for c, key in enumerate(headers[1:], 2):
                ws2.cell(r, c, s.get(key, "")).alignment = \
                    Alignment(horizontal="center")

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    excel_bytes = to_excel(
        result, st.session_state.year, st.session_state.month)
    st.download_button(
        "📥 下載 Excel 排班表",
        data=excel_bytes,
        file_name=(f"排班表_"
                   f"{st.session_state.year}"
                   f"{st.session_state.month:02d}.xlsx"),
        mime=("application/vnd.openxmlformats-officedocument"
              ".spreadsheetml.sheet"),
        type="primary",
        use_container_width=True,
    )
