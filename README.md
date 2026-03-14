# mosme-cheat

自動完成 [MOSME](https://www.mosme.net) 即時測評的工具，透過 Playwright 控制瀏覽器登入、展開題庫、選擇試卷並自動作答。

## 功能

- 自動登入 MOSME（透過 IPOE 帳號）
- 展開指定課程的題庫清單
- **互動選擇試卷**：列出所有可用試卷，讓使用者選擇後再開始
- 三層自動作答策略：
  1. 從頁面 Knockout.js ViewModel 取得答案
  2. 讀取 HTML `isanswer` 屬性
  3. 對照 PDF 答案卷（依題號點選正確選項）

## 環境需求

- Python 3.13+
- [uv](https://github.com/astral-sh/uv) 套件管理器

## 安裝

```bash
# On Windows.
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv sync
uv run playwright install chromium
```

## 設定

複製 `.env.example` 並填入帳號密碼：

```bash
cp .env.example .env
```

`.env` 內容：

```env
MOSME_ACCOUNT=你的帳號
MOSME_PASSWORD=你的密碼
```

## 使用

```bash
uv run main.py
# 或
.venv/Scripts/python main.py
```
https://github.com/engnyg/mosme-cheat/blob/master/2026-03-14%2012-07-51.mp4

執行後：
1. 自動登入並前往目標課程頁面
2. 展開題庫後列出所有可用試卷
3. 輸入編號選擇試卷（直接 Enter 選第一個）
4. 自動開始測驗並作答

## PDF 答案卷

將官方答案 PDF 放置於專案根目錄，預設讀取 `028003A11.pdf`。
路徑可在 `main.py` 頂部的 `PDF_PATH` 修改。

## 注意事項

- `.env` 含有帳號密碼，**請勿上傳至公開版本庫**（已加入 `.gitignore`）
- 本工具僅供個人練習使用
