# 護理排班系統

以 Streamlit + Google OR-Tools 建置的護理排班原型，適合部署到 Streamlit Cloud 供護理長直接使用。

## 功能

- 設定護理師名單、排班月份、每班最少人數需求
- 設定個人偏好：指定休假日、偏好班別
- 自動求解排班（OR-Tools CP-SAT 約束規劃）
- 互動式排班表（色彩標示班別）
- 一鍵匯出 Excel

## 內建約束規則

**硬約束（必須滿足）**
- 每人每天只能排一個班別
- 每班每天不得少於設定人數
- 大夜班隔天禁止排白班
- 最多連續上班天數（預設 6 天）
- 每人每月大夜上限（預設 8 次）

**軟約束（盡量滿足）**
- 護理師個人班別偏好請求
- 上班天數均衡分配
- 大夜班均攤

## 本地執行

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Cloud 部署步驟

1. 將此資料夾上傳至 GitHub（public 或 private repo 皆可）
2. 前往 [share.streamlit.io](https://share.streamlit.io)
3. 登入 → New app → 選擇你的 repo
4. Main file path：`app.py`
5. 點 Deploy → 等待約 2–3 分鐘完成

部署後會取得一個公開網址，護理長用瀏覽器開啟即可使用，無需安裝任何程式。

## 檔案結構

```
nurse_scheduler/
├── app.py            # Streamlit 主程式（UI）
├── scheduler.py      # OR-Tools 求解核心
├── export_excel.py   # Excel 匯出
├── requirements.txt  # 相依套件
└── README.md
```
