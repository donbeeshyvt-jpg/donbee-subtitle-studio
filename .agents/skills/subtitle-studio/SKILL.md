---
name: subtitle-studio
description: 協助冬比字幕工作室（DonBee Subtitle Studio）首次環境準備、模型下載及本機 API／CLI 操作。用於啟動工作台、下載 YouTube 區段、轉錄與 AI 校字、匯出字幕或剪輯。不適用於一般字幕翻譯、燒錄字幕或修改程式本身。
---

# DonBee Subtitle Studio｜冬比字幕工作室操作技能

## 入口與安全

技能隨專案提供。由本檔向上三層為專案根目錄；確認根目錄同時有 `Start-DonBee-Subtitle-Studio.bat`、`src/app/studio/cli.py`。所有命令在該根目錄執行，不依賴固定磁碟代號。

先讀根目錄 [AGENTS.md](../../../AGENTS.md)，再完整讀 [API／CLI 手冊](../../../guides/API_CLI.md)。未安裝時讀 [SETUP](../../../guides/SETUP.md)。只操作軟體不必讀私有 `docs/`，也不要求它存在。

英文品牌固定寫 **DonBee Subtitle Studio**。如果使用者要修改程式而非操作字幕流程，改讀 [公開開發指南與程式地圖](../../../guides/CODE_MAP.md)，不要要求取得私人開發紀錄，也不要用本技能代替工程測試。

- 確認素材、時間範圍、輸出位置和任務目標。未指定範圍不要擅自下載整片。
- 遠端 API、付費處理、傳送音訊／字幕／參考文字、大型模型下載，須先有使用者明確同意。API 金鑰一律請使用者自己在前端「模型設定 → 文字模型供應者」填寫，按「儲存金鑰」，確認「已存金鑰」後再「測試連線」。不能只填未存就繼續。
- 缺金鑰或需換金鑰時，暫停依賴該服務的工作，等使用者前端儲存完成再驗證。不索取對話／截圖中的金鑰、不代填，不引導改用 CLI、環境變數或直接編輯 secrets 繞過此流程；既有進階介面不是 agent 收集金鑰的授權。只核對是否已設定與連線結果，不讀出金鑰。
- 不印出 `data/config.json`、token、secrets，不直接寫 SQLite，不改 `.env` 或既有模型環境。
- 原片不可覆寫。字幕 sidecar 會更新同名最新版，需多版交付時使用不同位置。刪除專案、清理資料、停止非本次建立的服務均另需授權。

## 操作順序

1. 先走下節「首次環境與啟動」，不要假設 `.venv` 已存在，也不要先用需要 API 的 `doctor` 當安裝前檢查。
2. 服務確認可用後，設 `PYTHONPATH=src`，用 `.venv/Scripts/python.exe`（Windows）或 `.venv/bin/python` 跑 `-m app doctor --env --json`、`provider list`、`models status`。這些命令不代表已通過模型推論測試。
3. 用 `project create` 或使用者選定的 project ID；用 `source add --url` 或 `source add --path` 登記來源。ID 一律取實際回傳值。
4. 用 `download` 指定 `--range`、影音種類及邊界。輸出位置先經 API `/v1/output-roots` 登記，再給 `--output-root ID`。重疊區間會合併取得，分開交付須再建片段／分別匯出。
5. 用 `analyze --profile balanced --asr-model MODEL` 轉錄；保留 `result.transcript_revision`，先匯出原稿。選遠端 ASR 時加 `--remote-consent`，目前仍有本機草稿階段。
6. 用 `correct --correction-mode conservative --wait --json` 取建議。沒有套用授權就停在建議；獲准後用 `--apply` 或 edits API。保留原 revision，檢查低信心、拒絕、失敗分塊、warnings 及 usage。不要把模型高信心等同語意正確。
7. 以新 revision 做預覽與匯出，預設等待逐詞對齊。下載 artifact 到使用者指定檔名，先確認檔案不存在，不擅用 `--overwrite`。
8. 核對最終工作狀態、字幕數量／時間範圍、對齊降級 warning、實際保存位置。回報原稿／修正稿與修改筆數；沒有聽判就明說語意未經人工驗證。

## 首次環境與啟動

必須完成「檢查 → 說明缺件與下載量 → 獲准後安裝 → 重檢 → 開啟與驗收」，命令與限制以 [SETUP](../../../guides/SETUP.md) 為準。

- 確認專案根與既有服務。若服務已執行，先驗證並使用它；不要安裝進正在使用的環境、啟動第二份或結束其他程序。自訂 `STUDIO_MODEL_PYTHON` 不走自動安裝，先核對用途與修改授權。
- 查 `python --version`。Python 不存在時，說明會安裝 Python 3.12 到系統後取得同意；Windows 有 winget 可協助執行 `winget install --id Python.Python.3.12 --exact --source winget`。重新確認直譯器可用才往下；UAC／無 winget／權限不足時停下提供具體手動步驟，不自行改驅動。
- 用系統 Python 執行 `python -m bootstrap --local --check-only --estimate-download --json`，不需 API 或 `.venv`。此命令只查環境及官方模型大小、不安裝；不允許連網時省略 `--estimate-download`。
- 回報缺少的 Python 套件與 FFmpeg／Node、是否會調整既有版本、模型 ID、實際安裝目錄、可取得的大小與磁碟餘量。查不到的大小說未知；不能把未知當 0，也不能宣稱只是少量下載。確認要本機方案或略過權重，並取得下載／套件版本調整同意；系統工具安裝另說明。
- 獲准後直接協助執行 `python -m bootstrap --local --confirm --serve`；有系統安裝授權且缺 FFmpeg／Node 才加 `--install-tools`。選本機方案必備 WhisperX + large-v3，先核心再補 turbo、對齊與分類；不默默選 Breeze／Qwen，不改既有 ASR 預設。Breeze 等選用模型在 API 開啟後用 `models download --ids ... --confirm --wait --json`，須另有選用授權。
- 跟隨終端 stderr 即時進度。可回報檔案數／已下載位元組／安裝階段與耗時，沒有可量測的總量不報百分比。耗時工作使用可持續讀取的終端；不能只有最後結果、把程序仍執行誤判成卡死，或因工具等待到期重複送出安裝。
- 安裝流程會重檢套件、下載器、核心匯入與必要模型；失敗就停並讀錯誤，不無限重試。模型固定 `models/` 或明示的 `STUDIO_MODELS_DIR`；遇快取位置衝突先確認設定，不任意清空／重下載。
- 確認 `/v1/health` 回工作室名稱及 `ok`、`/v2/` 可讀，再回報工作台已開啟。提供環境與模型驗收結果；尚未用音訊試跑時明說只驗證安裝與啟動，不宣稱 GPU 推論／字幕效果通過。

## 工作控制與失敗復原

缺服務或金鑰時，提供官方入口並說明用途：[OpenRouter](https://openrouter.ai/) 是線上模型接口，[ElevenLabs](https://elevenlabs.io/) 是線上語音服務，[LM Studio](https://lmstudio.ai/) 是需下載及啟動的本機模型工具。請使用者自行申請、選擇方案與輸入金鑰；不能把 LM Studio 當作線上服務，也不要代付費。

長工作皆為非同步。用 `--wait --json` 或 `job status` 查詢；等待到期不代表後端已停止，先查原 job 再決定是否取消或重試。可用 idempotency key 避免重複提交，不自動反覆重試付費服務。

401：核對同一服務的 config／token；409：重新取得 revision 或檢查資源占用；缺模型：回報缺件與所需下載；OOM：回報 GPU 占用，不自行結束其他人的模型程序。

工作成功不代表字幕品質合格。若無音訊真值，提供待聽判項目，勿宣稱全對或某模型最佳。

## 一鍵與能力邊界

日常 UI 啟動：`Start-DonBee-Subtitle-Studio.bat`。已建立 plan 一次跑完：`python -m app plan run PLAN_ID --wait --json`。現行 plan 不包含完整校字步驟，也不接受直接 analyze 的全部欄位；依手冊走分階段串接，不能憑空新增參數。

CLI `export` 不支援 `--output-root`；使用 artifact `--out`，或 API 的 `output_root_id`。進階呼叫先看 `--help`／OpenAPI，不直接呼叫私有 Python worker。
