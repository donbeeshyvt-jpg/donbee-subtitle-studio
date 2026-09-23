# 第三方程式與授權

本程式包含自有程式及不同授權的第三方元件；未將所有後端、模型或外部工具概括授權為同一條款。

- 前端包含改寫自 [LosslessCut](https://github.com/mifi/lossless-cut) 的時間軸與片段操作。前端依現有 `GPL-2.0-only` 宣告提供；完整條款在 [frontend/LICENSE](frontend/LICENSE)，來源、修改與檔案對映在 [frontend/THIRD_PARTY_NOTICES.md](frontend/THIRD_PARTY_NOTICES.md)。對應原始碼是本版 `frontend/src/`，建置方式在 [SETUP](guides/SETUP.md)。
- VibeVoice 輔助腳本保留在 [src/app/vendor/vibevoice/](src/app/vendor/vibevoice/)，附原 [LICENSE](src/app/vendor/vibevoice/LICENSE) 與 [NOTICE](src/app/vendor/vibevoice/NOTICE.md)。選用的完整模型執行環境不在本包中。
- Python 與 npm 依賴按各自授權使用，版本見 `requirements.lock`、`requirements-studio.txt` 與 `frontend/package-lock.json`。
- FFmpeg、yt-dlp、LM Studio、llama.cpp 及模型權重不隨本程式包提供。各工具與模型有各自的授權／使用條件；模型來源資訊見 `models.manifest.json`。

根目錄未新增涵蓋所有自有後端程式的授權宣告；公開程式碼不等於自動授予未宣告部分的再散佈授權。若要作為完整開源產品發布，仍需擁有者確認自有部分採用的授權及與第三方條款的相容性。
