# API／CLI 操作手冊

適用版本：v2.0.0-preview.1。本文描述現行入口；精確 schema 以正在執行服務的 `/openapi.json` 與 CLI `--help` 為準。CLI 是 HTTP 客戶端，必須先啟動 API；不另外執行一份模型管線。

## 1. 啟動與驗證

完成 [SETUP](SETUP.md) 後雙擊 `Start-DonBee-Subtitle-Studio.bat`，或在已準備好的環境執行：

```powershell
$env:PYTHONPATH = 'src'
$env:STUDIO_YTDLP_PYTHON = (Resolve-Path '.venv-download/Scripts/python.exe').Path
.\.venv\Scripts\python.exe -m app serve --host 127.0.0.1 --port 8765 --open-browser
```

另一個終端在相同專案根目錄：

```powershell
$env:PYTHONPATH = 'src'
$py = '.\.venv\Scripts\python.exe'
& $py -m app doctor --json
& $py -m app provider list --json
& $py -m app models status --json
```

CLI 預設讀 `data/config.json` 的 token；自訂資料目錄須同時設定 `STUDIO_DATA_DIR` 或用 `--config` 指向同一服務的設定檔。可用 `--api-url http://127.0.0.1:8765` 覆寫網址。不要把 token 放在命令列或對話裡。

原生 HTTP 的連線範例（變數不印出）：

```powershell
$base = 'http://127.0.0.1:8765'
$cfg = Get-Content -Raw './data/config.json' | ConvertFrom-Json
$headers = @{ Authorization = "Bearer $($cfg.token)" }
Invoke-RestMethod "$base/v1/health" -Headers $headers
```

若有 `STUDIO_API_TOKEN`／自訂資料目錄，改用相符的 token。401 請檢查是否連到不同實例，不要關掉驗證。

## 2. 建立專案與來源

下列 `PROJECT_ID`、`SOURCE_ID`、`TRANSCRIPT_ID`、`JOB_ID`、`ARTIFACT_ID` 都須替換成 API 真正回傳的 ID，不能自行猜測。YouTube URL 也請換成有權使用的影片。

```powershell
& $py -m app project create --name '新字幕專案' --json
& $py -m app source add --project PROJECT_ID --url 'https://www.youtube.com/watch?v=VIDEO_ID' --json
```

本機檔案用 `source add --project PROJECT_ID --path '影音檔的完整路徑' --json`，原始檔不會被複製或修改。CLI `source upload --file` 則會上傳一份到本機服務。

API 對應：

| 操作 | HTTP | JSON body |
|---|---|---|
| 建立專案 | `POST /v1/projects` | `{"name":"新字幕專案"}` |
| 加 YouTube | `POST /v1/projects/{project_id}/sources` | `{"kind":"youtube","url":"https://www.youtube.com/watch?v=VIDEO_ID"}` |
| 加本機檔 | `POST /v1/projects/{project_id}/sources/local-file` | `{"path":"完整影音路徑"}` |
| 登記輸出位置 | `POST /v1/output-roots` | `{"path":"完整資料夾路徑","name":"成品","create":true}` |

## 3. 區段下載與輸出位置

```powershell
& $py -m app download --project PROJECT_ID --source SOURCE_ID --range '5:00-10:00' --range '9:00-15:00' --audio-only --boundary accurate --wait --json
```

移除 `--audio-only` 就下載影片。`--output-root ROOT_ID` 接受的是已登記的位置 ID，不是任意路徑；先用網頁或 `/v1/output-roots` 登記。實際檔案會放在回傳的 `target`，通常是所選位置的 `subtitle_studio/`。

同一操作的 API：`POST /v1/projects/{project_id}/jobs`。

```json
{
  "kind": "acquire",
  "source_id": "SOURCE_ID",
  "asset_kind": "audio",
  "ranges": [
    {"start_us": 300000000, "end_us": 600000000},
    {"start_us": 540000000, "end_us": 900000000}
  ],
  "boundary_policy": "accurate"
}
```

API 時間是整數微秒，1 秒＝1,000,000 微秒。CLI 可用秒數、`MM:SS`、`HH:MM:SS`。未指定 ranges 代表整片，agent 必須先確認這符合使用者要求。

yt-dlp 先解析串流，FFmpeg 再 seek 取得範圍，不保證網路傳輸剛好只含所填時間。重疊區間會合併；結果看 `requested_ranges`／`downloaded_ranges`。`accurate` 重新編碼以求精準邊界，`source_seek` 有關鍵影格限制。未結束直播、DRM 或需要額外存取權的素材不保證可用。

## 4. 轉錄 → 原稿 → AI 校字 → 新稿

```powershell
& $py -m app analyze --project PROJECT_ID --source SOURCE_ID --profile balanced --asr-model breeze-asr-25 --asr-hints '角色名,作品名' --wait --json
& $py -m app transcript get --project PROJECT_ID TRANSCRIPT_ID --limit 200 --json
& $py -m app export --project PROJECT_ID --source SOURCE_ID --range '5:00-15:00' --transcript TRANSCRIPT_ID --formats srt --wait --json
```

`analyze` 完成後取 `result.transcript_revision`。可選本機 `breeze-asr-25`、`large-v3`、`qwen3-asr-1.7b`；先用 models status 確認可用。`draft` 是快稿，不等同上述精修選項。遠端 `--asr-model elevenlabs` 或 `openrouter:模型ID` 還要 `--remote-consent`，且先完成供應者設定。遠端目前仍依賴本機草稿，不保證省去本機 GPU 工作。

先用原 revision 匯出原稿；工作回傳的 artifact 再下載，才能保留可比對的原始輸出。

```powershell
& $py -m app correct --project PROJECT_ID --transcript TRANSCRIPT_ID --provider local-lmstudio --glossary '角色名,作品名' --correction-mode conservative --wait --json
```

上面只產生修正建議。要套用高信心修正，加 `--apply`（必須同時 `--wait`）。回傳 `transcript_revision` 是要繼續使用的版本；低信心及被守門拒絕的項目不會直接套用。`--reference-file` 可帶 UTF-8 參考資料，`--keep-punctuation` 保留標點；`rewrite` 校字強度改動較大，需使用者要求才用。

線上校字將 `--provider` 換成已設定且允許遠端的供應者 ID，例如 `api-openrouter`。`correct` 沒有 `--remote-consent` 旗標；遠端開關在供應者設定。agent 仍須先取得使用者的資料外傳／費用同意。

校字 API 是 job body `{"kind":"correct","transcript_revision":"TRANSCRIPT_ID","provider_id":"local-lmstudio","correction_mode":"conservative","mode":"suggest"}`。結果含 `patches`、`rejected_patches`、`usage`。接受修正用 `POST /v1/projects/{p}/transcripts/{revision}/edits`：

```json
{
  "base_revision": "TRANSCRIPT_ID",
  "origin": "llm_correction",
  "edits": [{"cue_id": "CUE_ID", "text": "確認後的文字"}]
}
```

請先檢查原文與 revision 相符；遇到 409 重新取得版本，不可直接覆蓋。

## 5. 預覽、匯出、下載

```powershell
& $py -m app subtitles preview --project PROJECT_ID --source SOURCE_ID --transcript TRANSCRIPT_ID --range '5:00-15:00' --sentences 1 --json
& $py -m app export --project PROJECT_ID --source SOURCE_ID --transcript TRANSCRIPT_ID --range '5:00-15:00' --formats srt,transcript_json --sentences 1 --wait --json
& $py -m app artifact get ARTIFACT_ID --out './corrected.srt'
```

時間範圍沿用來源座標，例如下載原片第 5 分開始，仍填 5:00；不要自行扣掉 5 分。合併輸出的字幕預設為新輸出序列時間，分段輸出為片段時間；需要原片時間用 `--timebase source`。

匯出預設等待逐詞對齊，失敗可能退回句子時間並附 warning。檢查工作及 manifest，不能只看到 SRT 存在就認定時間與語意正確。字幕預覽 API 是 `POST /v1/projects/{project_id}/subtitles/preview`；修改歷史是 `GET /v1/projects/{project_id}/transcripts/{revision_id}/history`。

CLI `export` **目前沒有 `--output-root` 選項**。要直接保存到登記位置，改用 API export job 的 `output_root_id`，或工作計畫的 `deliverables.output_root_id`；CLI 下載 artifact 則用 `--out`。下載 artifact 遇同名預設拒絕，不要自行加 `--overwrite`。

## 6. 一次執行已建立的工作計畫

將下面存成 `workflow.json`，替換來源 ID。這個範例是「指定音訊區間 → 快稿 → SRT」，**不包含 AI 校字**，也不是點一次便完成首次安裝。

```json
{
  "source_id": "SOURCE_ID",
  "ranges": [{"start_us": 300000000, "end_us": 600000000}],
  "acquisition": {"audio_first": true, "video": "none", "boundary_policy": "accurate"},
  "analysis": {"mode": "draft", "language_policy": "zh", "music_policy": "off"},
  "subtitles": {"policy": "generate", "sentences_per_cue": 1},
  "deliverables": {"formats": ["srt"], "grouping": "merge"}
}
```

```powershell
& $py -m app plan create --project PROJECT_ID --file './workflow.json' --json
& $py -m app plan run PLAN_ID --wait --json
```

API 對應 `POST /v1/projects/{p}/plans`、`POST /v1/plans/{plan_id}/run`。目前工作計畫的 analysis schema 與直接 `analyze` 不完全相同，不能把 `asr_model` 等欄位直接照搬。需要指定精修模型及校字時，使用第 4 節逐步操作。

## 7. 進度、重試與錯誤

模型安裝不是一般 job。`models download --ids hf:Systran/faster-whisper-large-v3 --confirm --wait --json` 透過 `/v1/models/status` 輪詢，stderr 即時顯示下載階段／數量，stdout 留最後 JSON；`download_id` 用於識別該次下載。檔案數不是位元組百分比。只有所有結果成功才回 0；逾時不取消伺服器下載，用 `models status --json` 查現況。尚未有環境／服務時，用 [SETUP](SETUP.md) 的 bootstrap 本機方案。

提交長工作回 202，之後輪詢 `GET /v1/jobs/{id}` 或訂閱 `/v1/jobs/{id}/events`。CLI：

```powershell
& $py -m app job status JOB_ID --json
& $py -m app job wait JOB_ID --wait-timeout 3600 --json
& $py -m app job cancel JOB_ID --json
```

狀態為 `succeeded` 才算完成；`failed`／`cancelled`／`interrupted` 不可當成成功。`--wait-timeout` 只停止客戶端等待，**不取消後端工作**；先查現況，避免重複提交付費作業。需要可重放提交時使用 `--idempotency-key`／HTTP `Idempotency-Key`。

| CLI exit code | 意義 |
|---|---|
| 0 | 成功 |
| 2 | 參數或一般 4xx 錯誤 |
| 3 | 工作／服務端失敗 |
| 4 | 工作取消 |
| 5 | 等待逾時，後端可能仍在跑 |
| 6 | 409 衝突 |

摘要用 `summarize`、剪輯提案用 `plan edits`、工作取消重試及更多選項請讀各命令 `--help`。刪除專案、清理資料、覆寫檔案均需明確授權，不屬於例行診斷。
