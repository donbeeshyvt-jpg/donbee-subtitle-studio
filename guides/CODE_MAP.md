# 程式入口地圖

公開版只描述程式責任與使用入口，不包含開發歷程或對話。適用 v2.0.0-preview.1。

| Path | Responsibility (EN) | 職責（繁中） |
|---|---|---|
| `Start-DonBee-Subtitle-Studio.bat`、`bootstrap/` | Launcher and environment checks | 啟動本機工作臺、依賴檢查及專案環境準備 |
| `src/app/__main__.py`、`src/app/studio/cli.py` | HTTP CLI | `python -m app`；CLI 與網頁共用 API |
| `src/app/studio/api.py`、`contracts.py` | API and request schemas | `/v1` 路由、驗證、工作提交與內容存取 |
| `src/app/studio/coordinator.py`、`store.py`、`worker.py` | Durable jobs and workers | 排程、SQLite、版本、子程序與成果 |
| `src/app/studio/media.py` | Acquisition and media processing | yt-dlp、FFmpeg、範圍下載及剪輯 |
| `src/app/studio/asr.py`、`asr_models.py`、`remote_asr.py`、`qwen_worker.py` | Speech transcription | 本機與遠端 ASR、模型選擇與隔離 |
| `src/app/studio/providers.py`、`provider_secrets.py` | Text model adapters | 校字、摘要、供應者與本機 secrets |
| `src/app/studio/subtitles.py` | Subtitle formatting | 字幕分句、時間與 SRT／VTT 輸出 |
| `src/app/config.py`、`models.manifest.json` | Paths and model catalog | 專案根、資料／模型位置與下載清單 |
| `src/app/vendor/vibevoice/` | Vendored helpers | 保留原授權的共用字幕輔助程式 |
| `frontend/src/App.tsx`、`frontend/src/components/` | Web workbench | 下載、時間軸、字幕、校字與設定介面 |
| `frontend/src/api/`、`frontend/src/domain/` | Client and editing domain | API 客戶端、時間／片段及狀態邏輯 |
| `frontend/dist/` | Prebuilt web assets | API 提供的網頁程式打包 |
| `tests/`、`frontend/tests/` | Automated tests | 程式驗證；API 替身須遵守字幕預覽契約；不包含使用者素材或驗證產物 |
| `scripts/check-public-release.py`、`scripts/git-hooks/` | Release boundary checks | Git 暫存檔案、常見金鑰與公開索引同步檢查 |
| `.agents/skills/subtitle-studio/SKILL.md` | Agent operation skill | 經 API／CLI 操作程式的安全工作流程 |

公開操作契約：[API_CLI.md](API_CLI.md)。本機若另有 `docs/`，其內容僅用於開發交接，不納入公開版本。

## 給要修改程式的人

不需要私人對話或開發日誌就能修改本專案。先照 [SETUP](SETUP.md) 準備環境，再讀 [API_CLI](API_CLI.md) 確認對外契約。操作技能教的是「使用程式」，不是要求開發者完全照技能流程開發。

### 架構與資料流

React 網頁與 CLI 都呼叫 FastAPI。API 驗證請求後交給 coordinator 排程，worker 執行下載、辨識、校字或匯出；store 保存工作狀態、revision 與 artifact。CLI 不應另外實作模型流程。

`data/` 是執行時資料，不是原始碼；模型在 `models/`。兩者由啟動／執行流程建立，不需從作者電腦複製。測試應使用臨時資料目錄與合成素材；缺少模型時，不把跳過推論當成模型驗收通過。

API JSON 時間以整數微秒表示。source map 對映原片和下載片段；文字修改建立新 revision，舊的逐詞對齊不能直接當作新稿的時間。這些是修改字幕或剪輯邏輯時需守住的界線。

### 開發、建置與測試

先啟動已準備好的 API。另開終端執行 `npm --prefix frontend ci`、`npm --prefix frontend run dev`，使用終端顯示的開發網址；Vite 會將 `/v1` 代理到本機 8765。交付前用 `npm --prefix frontend run build` 更新隨包提供的 `frontend/dist/`。

在專案根目錄執行一組不需真實模型服務的入口測試：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_public_release.py tests/test_public_manual.py tests/test_studio_cli.py tests/test_studio_cli_models.py tests/test_studio_cli_providers.py tests/test_studio_provider_setup.py tests/test_bootstrap.py -q
npm --prefix frontend test
npm --prefix frontend run build
```

修改其他模組時，依檔名增加對應 `tests/test_studio_*.py`，不能只跑入口測試就宣稱全功能正常。需要 GPU、網路或付費 provider 的測試另外確認環境與授權；測試輸出不得提交。

### 變更與相容性

- 改 API 欄位：同步 `contracts.py`、網頁 API 型別、CLI 選項與公開手冊，增加契約測試。
- 改功能：先寫能重現問題的失敗測試，再修改，最後更新這份地圖。
- 發布前執行 [RELEASE](RELEASE.md) 的檢查，只提交程式及公開說明，不補入私人 `docs/`。
- 英文顯示名稱為 **DonBee Subtitle Studio**。舊資料遷移路徑 `DongbiStudio`、既有內部設定鍵及套件識別名稱不是顯示品牌，不為改字樣而變更，以免既有資料失聯。
