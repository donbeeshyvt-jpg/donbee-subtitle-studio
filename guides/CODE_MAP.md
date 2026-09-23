# 程式入口地圖

公開版只描述程式責任與使用入口，不包含開發歷程或對話。適用 v2.0.0-preview.1。

| Path | Responsibility (EN) | 職責（繁中） |
|---|---|---|
| `run_v2.bat`、`bootstrap/` | Launcher and environment checks | 啟動本機工作臺、依賴檢查及專案環境準備 |
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
