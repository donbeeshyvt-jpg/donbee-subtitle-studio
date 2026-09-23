# 安裝與模型設定

適用版本：v2.0.0-preview.1。從專案根目錄操作；公開包不含模型、金鑰、素材或本機開發紀錄。

## 首次準備

雙擊 `Start-DonBee-Subtitle-Studio.bat`，依提示選「1 本機轉錄」或「2 工作台與套件（略過模型權重）」。流程為：**檢查 → 列缺件／位置／下載量 → 同意 → 安裝 → 重檢 → 開啟工作台並確認可用**。

- 缺 Python 時，啟動器先詢問是否使用 winget 安裝 Python 3.12；沒有 winget、權限不足或安裝後找不到 Python，會停下提供提示。也可自行安裝 Python 3.12 後重開。
- FFmpeg（含 ffprobe）與 Node.js 缺少時另詢問系統安裝同意；使用 winget 指定 `Gyan.FFmpeg`、`OpenJS.NodeJS.LTS`，不修改顯示卡驅動／CUDA。Windows 可能要求使用者處理 UAC 或安裝介面。
- 專案 `.venv` 依鎖定清單安裝缺件／修正不符版本；選本機方案包括 WhisperX、faster-whisper、torch 與必要模型。`--confirm` 也代表同意專案環境的版本調整，不是「只增加套件」。不要指向別人的既有模型環境。
- 自動建立／檢查獨立 `.venv-download`，安裝 `yt-dlp[default]`。自訂 `STUDIO_YTDLP_PYTHON` 則只驗證，不擅自安裝進自訂環境。
- 首次可能下載多 GB，模型大小能查到就顯示，未知的套件／模型大小標示未知；檔案大小不等於解壓後磁碟需求。既有完整模型會重用。

Agent／非互動 CLI 請分兩步。先檢查（只查大小會連官方模型站，但不下載權重）：

```powershell
python -m bootstrap --local --check-only --estimate-download --json
```

向使用者說明後取得同意，再執行（不需先有 `.venv` 或 API）：

```powershell
python -m bootstrap --local --confirm --install-tools --serve
```

已手動準備 FFmpeg／Node，可省略 `--install-tools`。只安裝不啟動則省略 `--serve`。拒絕安裝或重檢失敗會停止，不會假裝準備完成。缺件全齊時不重複下載完整模型。啟動後以 `/v1/health` 和 `/v2/` 確認服務與網頁可用，通過後才開瀏覽器；這不是完整模型推論或字幕品質驗收。

`Start-DonBee-Subtitle-Studio.bat` 會自動找到 `.venv-download`；自行啟動 API 時請設定 `STUDIO_YTDLP_PYTHON`。第一次不是完全離線可用。若設定了 `STUDIO_MODEL_PYTHON`，啟動器走自訂環境路徑，**不自動安裝／改版**；agent 必須先核對該環境並另行取得修改授權，不能當作已完成安裝流程。

不連模型站的檢查：`python -m bootstrap --local --check-only --json`，省略 `--estimate-download`。檢查報告寫到本機 `data/bootstrap-last.json`。系統工具安裝後若 PATH 仍未生效，請重開終端／啟動器；不重複盲裝。

## 本機轉錄模型

本機方案以 **WhisperX 套件 + Whisper large-v3 權重**為核心：先確認套件可匯入，再優先準備 large-v3，接著補 turbo 草稿、中／日／英對齊與音訊分類模型。WhisperX 是處理框架，large-v3 是模型權重，兩者都要準備。此方案不擅自改你既有的預設 ASR 選擇。

| 內容 | 預設位置 |
|---|---|
| WhisperX 等套件 | `.venv/` |
| large-v3 | `models/hf/models--Systran--faster-whisper-large-v3/` |
| turbo／中文／日文對齊／分類 | `models/hf/` 下各模型快取目錄 |
| 英文對齊 | `models/torch/hub/checkpoints/` |
| 選用 Breeze-ASR-25 | `models/ct2/breeze-asr-25/` |

指定 `STUDIO_MODELS_DIR` 時整組移至該目錄。若另設 `HF_HUB_CACHE`／`TORCH_HOME` 且與此位置衝突，本機準備會停止，請先確認設定；不要出現下載到一處、推論找另一處。完成檢查包含 large-v3／turbo 必要檔案，不把只有設定檔的半成品當成已安裝。

開啟網頁的模型設定／環境與模型，查缺件與下載容量，確認後下載。模型清單以 `models.manifest.json`、API capabilities／models status 為準。

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m app models status --json
.\.venv\Scripts\python.exe -m app models download --ids ct2:breeze-asr-25 --confirm --wait --json
```

平衡／品質模式先做草稿，再精修及對齊，除了所選精修模型也可能需要草稿、語言對齊與分類模型。Breeze 未安裝時服務預設會回退 large-v3；實際使用模型請看工作結果，不只看選單名稱。

Qwen3-ASR-1.7B 有獨立的套件環境要求；請先看環境檢查與模型狀態，不能只放權重就當作安裝完成。VibeVoice 是選用引擎，不在本版預設安裝群組；公開包不附它的參考專案，請勿直接啟用 `engine_b` 群組。GPU 記憶體不足時，先卸載其他占用模型或換較小設定。

## CLI 動態進度

- 安裝時保留 pip／winget 即時輸出，pip 開啟下載進度，並每約 5 秒顯示階段與已過時間。依安裝器與終端支援情況，並非每一步都有百分比。
- 本機方案準備模型時即時顯示模型 ID、開始／下載／轉換／完成。Hugging Face 顯示檔案計數；可取得長度的直接下載顯示 `bytes_done`／`bytes_total`。未知大小不虛構百分比。
- API 已啟動後，`models download --confirm --wait --json` 會真正輪詢模型下載進度；stderr 是即時進度、stdout 是最後 JSON，可分開保存。`--wait-timeout` 只停止客戶端等待，不停止服務端下載；用 `models status` 接續查看，不重複提交。
- 所有模型項目成功才回成功代碼；某項失敗、需手動處理或狀態丟失會回非零。沒有 `--wait` 只代表提交成功。

## 自己的文字 AI

官方入口：[OpenRouter](https://openrouter.ai/)（註冊／取得金鑰）、[LM Studio](https://lmstudio.ai/)（下載本機工具）、[ElevenLabs](https://elevenlabs.io/)（註冊語音服務）。請由使用者自行申請帳號、確認方案及付款；agent 不代替使用者購買。

- LM Studio：自行安裝、載入文字模型並開啟本機伺服器；預設連線 `http://127.0.0.1:1234/v1`。在工作室選 `local-lmstudio`，測試連線後再校字。
- llama.cpp：自行啟動相容伺服器，在供應者設定填入網址與模型。程式不附 llama-server 執行檔或 GGUF。
- OpenRouter／DeepSeek：在供應者設定填自己的金鑰，開啟遠端使用並測試連線。遠端校字會傳送字幕與所填參考資料。
- ElevenLabs：用於語音轉錄，不是校字模型；需要具備語音轉文字權限的金鑰。遠端轉錄會傳送音訊。

模型 ID 請用連線探測回傳的清單，不保證範例模型永遠可用。服務費用依供應商計費；先用短片段試跑。`.env.example` 是變數說明，程式不會自動載入 `.env`，請在啟動環境設定或使用網頁安全儲存介面。

## 資料與安全

`data/` 放專案、工作資料庫與本機 secrets；`models/` 放模型。可用 `STUDIO_DATA_DIR`／`STUDIO_MODELS_DIR` 改位置。不要上傳這兩個資料夾。

同一資料目錄只啟動一個服務。遇到占用先確認現有服務，不要直接刪 lock 或盲目結束程序。預設只聽 localhost，不要自行暴露到公開網路。

字幕與文字 sidecar 在同一輸出資料夾更新最新版；影音避免覆寫。需要保留不同稿件，使用不同輸出位置或先備份。

## 前端與驗證

已附 `frontend/dist`，一般使用不需要重新 build。修改前端時才執行：

```powershell
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run build
```

只需要 API／CLI 的輕量環境可以安裝 `requirements-studio.txt`，但它不包含 GPU 轉錄所需的所有依賴，不能用「服務能啟動」代替模型已可運算。
