# 來源與授權通知

- 來源：專案內參考目錄 `docs/VibeVoice-main/`（使用者提供的 VibeVoice 專案副本），檔案 `gen_srt.py`、`vibevoice_asr_to_srt.py`，於 2026-09-16 原樣複製，未修改內容。
- 授權：同目錄 `LICENSE`（隨上游專案）。`vibevoice` Python 套件（模型程式）仍以 `docs/VibeVoice-main` 為安裝來源，由啟動器安裝。
- 用途：`app.reuse` 重用 `gen_srt.is_tag`／`fmt`；`app.vv_worker` 子行程重用 `vibevoice_asr_to_srt.load_model`／`transcribe`。
- 更新方式：若上游檔案更新，重新複製並在此記錄日期；不在此目錄修改邏輯，需要調整時在本專案模組包裝。
