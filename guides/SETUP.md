# 安裝與模型設定

適用版本：v2.0.0-preview.1。從專案根目錄操作；公開包不含模型、金鑰、素材或本機開發紀錄。

## 首次準備

1. 安裝 Python 3.12，確認 `python --version` 指向正確版本。
2. 安裝 FFmpeg（含 ffprobe）與 Node.js，加入 PATH，重新開啟終端，確認 `ffmpeg -version`、`ffprobe -version`、`node --version`。
3. 在專案根目錄執行下列指令。第一行會建立 `.venv` 並安裝套件，可能下載大型 torch 套件；不會自動安裝系統 FFmpeg／Node，也不會把模型權重全部下載完成。

```powershell
python -m bootstrap
python -m venv .venv-download
.\.venv-download\Scripts\python.exe -m pip install "yt-dlp[default]"
.\Start-DonBee-Subtitle-Studio.bat
```

下載器是獨立環境。`Start-DonBee-Subtitle-Studio.bat` 會自動找到 `.venv-download`；自行啟動 API 時請設定 `STUDIO_YTDLP_PYTHON`。第一次可能需要連網安裝或取得模型，不是解壓後完全離線可用。

想先看缺什麼：`python -m bootstrap --check-only --json`。它不安裝套件，但會把環境報告存到本機 `data/`。

## 本機轉錄模型

開啟網頁的模型設定／環境與模型，查缺件與下載容量，確認後下載。模型清單以 `models.manifest.json`、API capabilities／models status 為準。

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m app models status --json
.\.venv\Scripts\python.exe -m app models download --ids ct2:breeze-asr-25 --confirm --wait --json
```

平衡／品質模式先做草稿，再精修及對齊，除了所選精修模型也可能需要草稿、語言對齊與分類模型。Breeze 未安裝時服務預設會回退 large-v3；實際使用模型請看工作結果，不只看選單名稱。

Qwen3-ASR-1.7B 有獨立的套件環境要求；請先看環境檢查與模型狀態，不能只放權重就當作安裝完成。VibeVoice 是選用引擎，不在本版預設安裝群組；公開包不附它的參考專案，請勿直接啟用 `engine_b` 群組。GPU 記憶體不足時，先卸載其他占用模型或換較小設定。

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
