# 護理排班系統 v3

## 架構說明

```
Streamlit UI (app.py)
      ↓
j3soon_bridge.py  ← 轉換層（UIConfig → YAML → j3soon → ScheduleResult）
      ↓
j3soon_core/      ← j3soon/nurse-scheduling 原始核心（AGPL-3.0）
  nurse_scheduling/
    scheduler.py      # 主排班 pipeline
    solver_ortools_cp_sat.py  # OR-Tools CP-SAT 求解
    preference_types.py       # 各類 preference 實作
    models.py                 # Pydantic 資料模型
```

j3soon 核心授權：AGPL-3.0 © Johnson Sun & Contributors
來源：https://github.com/j3soon/nurse-scheduling

## 功能

**硬約束（必須滿足）**
- 每人每天最多一班
- 每班最少人數需求
- 大夜 → 白班 禁止
- 小夜 → 白班 禁止
- 每人每月大夜上限

**軟約束（最佳化目標）**
- 上班天數均衡
- 休假天數均衡
- 週末出勤均攤（j3soon `WEEKEND` 關鍵字）
- 大夜班次均攤
- 個人班別偏好請求

**年資**
- 顯示於排班統計，不作為排班約束

## 部署到 Streamlit Cloud

1. 將整個資料夾上傳至 GitHub repo
2. 登入 [share.streamlit.io](https://share.streamlit.io)
3. New app → 選 repo → Main file: `app.py` → Deploy

## 本地執行

```bash
pip install -r requirements.txt
streamlit run app.py
```
