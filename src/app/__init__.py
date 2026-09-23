"""字幕工作室 (Subtitle Studio) 核心套件。

模組：
- config   全域設定/環境/watchdog 上限
- reuse    橋接重用既有 VibeVoice 腳本（路徑見 config.VIBEVOICE_ROOT）
- regions  區段正規化/切片/offset
- lid      逐段語言偵測
- srt_build SRT 組裝
- music_gate 唱歌/語音切分（TASK-005）
- asr_default WhisperX 轉錄+對齊（TASK-006）
- cli      端到端串接（TASK-007）
- download yt_dlp 下載 MP4（TASK-008）
"""
