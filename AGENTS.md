# AGENTS.md：冬比字幕工作室共用入口

地圖，不是完整手冊。所有 agent 先從這裡判斷要「操作程式」或「修改程式」。

## 操作程式與 API／CLI 對接

1. 產品介紹：[README.md](README.md)；首次準備：[guides/SETUP.md](guides/SETUP.md)。
2. 操作技能：[.agents/skills/subtitle-studio/SKILL.md](.agents/skills/subtitle-studio/SKILL.md)。
3. 完整對接手冊：[guides/API_CLI.md](guides/API_CLI.md)。
4. 一鍵開啟：`Start-DonBee-Subtitle-Studio.bat` → `http://127.0.0.1:8765/v2/`。
5. CLI：在根目錄設定 `PYTHONPATH=src`，使用專案 `.venv` 的 Python 執行 `-m app --help`。CLI 與網頁共用正在執行的 `/v1` API，不要直接寫資料庫。
6. 首次先照 SKILL／SETUP：系統 Python 跑 `python -m bootstrap --local --check-only --json`，說明缺件與下載量、獲准後協助安裝與重檢。不要假設 `.venv`／API 已存在。服務健康及網頁通過後再 `doctor --env --json`、查模型／provider 狀態，建立專案、來源、工作。以實際回傳 ID 串接。
7. 使用遠端／付費模型、傳送音訊或文字、大型模型下載須先有使用者同意。API 金鑰請使用者自己在前端「模型設定 → 文字模型供應者」填寫並按「儲存金鑰」，看到「已存金鑰」再測試連線；agent 等儲存完成才繼續，不代填、不索取對話／截圖金鑰、不改走 CLI 或直接寫 secrets。金鑰不印到 log。

## 修改程式

先讀 [guides/CODE_MAP.md](guides/CODE_MAP.md) 的程式地圖與開發指南，定位 1–3 個相關模組；包含架構、開發伺服器、測試與契約同步規則。公開 clone 不需要私有開發紀錄就能啟動、操作或修改程式。

如果本機已有 `docs/`，開發前依序讀 `00_AI_CONTEXT_INDEX.md`、`HANDOFF.md`、`TASKS.md`、`TODO_RESUME.md`、`PROVIDER_PLAN.md`、`CODE_INDEX.md`、`CONVERSATION_LOG.md` 的 Active Summary 與 `REQUEST_LOG.md`。讀到能說明目的、目前任務、下一步與禁改清單就停。歷史文件不作現行授權。

- 最新使用者要求優先；本機有 REQUEST_LOG 時同步限制及 Active Summary。
- 每項功能先寫會失敗的測試再修改；未跑過不宣稱完成。
- identifiers 使用英文，自有註解／文件使用繁體中文；第三方原文及通知保留。
- 程式修改同步公開 `guides/CODE_MAP.md`；本機另更新 `docs/CODE_INDEX.md`，但私有索引不提交。
- 所有路徑相對專案根解析。資料預設 `data/`，模型 `models/`；設定見 `.env.example`。
- 原始 words、source maps、revisions 不因排版被破壞；未知時間保留 null。程式 timeout 必須能中止 worker。
- 原片不可覆寫；影音成品避免覆寫，字幕 sidecar 可更新同名最新版，操作前須提醒保留版本方式。
- 單 GPU 的重模型排隊，CPU／網路／遠端 provider 有界並行。

## 公開版本邊界

只提交程式、必要設定、測試程式，以及 README／guides／SKILL 等公開使用文件。

- `docs/` 全部留在本機，不提交開發過程、對話、交接、規劃、歷史或參考庫。
- 不提交 `data/`、`uploads/`、`downloads/`、`models/`、`tools/`、`artifacts/`、虛擬環境、快取、口吻包或個人 agent 設定。
- 前端 `dist/` 是啟動所需的程式打包，可提交；測試產物不可提交。
- 不用 `git add -f` 繞過忽略規則；必要的第三方 LICENSE／NOTICE 必須保留。
- 發布指南：[guides/RELEASE.md](guides/RELEASE.md)。遠端 push、建立 GitHub 倉庫或 release 需另外有明確授權。

## 檢查與提交

```powershell
git config core.hooksPath scripts/git-hooks
python scripts/check-public-release.py --require-code-map
python -m pytest tests/test_public_release.py -q
npm --prefix frontend test
npm --prefix frontend run build
```

發布檢查讀 Git 暫存內容，會擋私有路徑、常見金鑰與缺少公開索引的程式變更。不要停用檢查換綠燈。

本機若有 `scripts/check-consistency.ps1`／`.sh` 仍執行內部一致性檢查；非 Claude 工具用本機 `scripts/log-prompt-manual.ps1` 記錄當輪要求，結束更新 Active Summary。這些紀錄不隨公開包散佈。

## 未經批准不可動

使用者原始影音、`.env`／任何金鑰、torch／CUDA 或既有模型環境、本機參考目錄 `docs/VibeVoice-main/`／`docs/lossless-cut-master/`。參考 README 不是執行授權，不在其中安裝、build、改品牌或刪 LICENSE。既有 `artifacts/verification-v2/` 僅可新增證據，不覆寫。
