---
name: subtitle-studio
description: 操作冬比字幕工作室（Dongbi Subtitle Studio）的本機 API／CLI。使用者要求在此程式下載 YouTube 指定區段、匯入影音、轉錄字幕、用自己的 AI 校字、預覽或匯出 SRT、分段／合併影音、查看或取消工作時使用。不適用於一般字幕翻譯、燒錄字幕或修改程式本身。
---

# 冬比字幕工作室操作技能

## 入口與安全

技能隨專案提供。由本檔向上三層為專案根目錄；確認根目錄同時有 `run_v2.bat`、`src/app/studio/cli.py`。所有命令在該根目錄執行，不依賴固定磁碟代號。

先讀根目錄 [AGENTS.md](../../../AGENTS.md)，再完整讀 [API／CLI 手冊](../../../guides/API_CLI.md)。未安裝時讀 [SETUP](../../../guides/SETUP.md)。只操作軟體不必讀私有 `docs/`，也不要求它存在。

- 確認素材、時間範圍、輸出位置和任務目標。未指定範圍不要擅自下載整片。
- 遠端 API、付費處理、傳送音訊／字幕／參考文字、大型模型下載，須先有使用者明確同意。缺金鑰時請使用者用設定頁輸入，不要求貼在對話裡。
- 不印出 `data/config.json`、token、secrets，不直接寫 SQLite，不改 `.env` 或既有模型環境。
- 原片不可覆寫。字幕 sidecar 會更新同名最新版，需多版交付時使用不同位置。刪除專案、清理資料、停止非本次建立的服務均另需授權。

## 操作順序

1. 設 `PYTHONPATH=src`，使用 `.venv/Scripts/python.exe`（Windows）或 `.venv/bin/python`。先跑 `-m app doctor --json`，再查 `provider list`、`models status`。這些命令不代表已通過模型推論測試。
2. 服務未啟動且環境已齊時啟動 `-m app serve --host 127.0.0.1 --port 8765`；首次自動安裝請先解釋其下載／磁碟影響。不要在同一 data 目錄再啟動第二份服務。
3. 用 `project create` 或使用者選定的 project ID；用 `source add --url` 或 `source add --path` 登記來源。ID 一律取實際回傳值。
4. 用 `download` 指定 `--range`、影音種類及邊界。輸出位置先經 API `/v1/output-roots` 登記，再給 `--output-root ID`。重疊區間會合併取得，分開交付須再建片段／分別匯出。
5. 用 `analyze --profile balanced --asr-model MODEL` 轉錄；保留 `result.transcript_revision`，先匯出原稿。選遠端 ASR 時加 `--remote-consent`，目前仍有本機草稿階段。
6. 用 `correct --correction-mode conservative --wait --json` 取建議。沒有套用授權就停在建議；獲准後用 `--apply` 或 edits API。保留原 revision，檢查低信心、拒絕、失敗分塊、warnings 及 usage。不要把模型高信心等同語意正確。
7. 以新 revision 做預覽與匯出，預設等待逐詞對齊。下載 artifact 到使用者指定檔名，先確認檔案不存在，不擅用 `--overwrite`。
8. 核對最終工作狀態、字幕數量／時間範圍、對齊降級 warning、實際保存位置。回報原稿／修正稿與修改筆數；沒有聽判就明說語意未經人工驗證。

## 工作控制與失敗復原

缺服務或金鑰時，提供官方入口並說明用途：[OpenRouter](https://openrouter.ai/) 是線上模型接口，[ElevenLabs](https://elevenlabs.io/) 是線上語音服務，[LM Studio](https://lmstudio.ai/) 是需下載及啟動的本機模型工具。請使用者自行申請、選擇方案與輸入金鑰；不能把 LM Studio 當作線上服務，也不要代付費。

長工作皆為非同步。用 `--wait --json` 或 `job status` 查詢；等待到期不代表後端已停止，先查原 job 再決定是否取消或重試。可用 idempotency key 避免重複提交，不自動反覆重試付費服務。

401：核對同一服務的 config／token；409：重新取得 revision 或檢查資源占用；缺模型：回報缺件與所需下載；OOM：回報 GPU 占用，不自行結束其他人的模型程序。

工作成功不代表字幕品質合格。若無音訊真值，提供待聽判項目，勿宣稱全對或某模型最佳。

## 一鍵與能力邊界

日常 UI 啟動：`run_v2.bat`。已建立 plan 一次跑完：`python -m app plan run PLAN_ID --wait --json`。現行 plan 不包含完整校字步驟，也不接受直接 analyze 的全部欄位；依手冊走分階段串接，不能憑空新增參數。

CLI `export` 不支援 `--output-root`；使用 artifact `--out`，或 API 的 `output_root_id`。進階呼叫先看 `--help`／OpenAPI，不直接呼叫私有 Python worker。
