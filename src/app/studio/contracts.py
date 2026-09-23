"""HTTP 請求白名單與參數驗證。"""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from .domain import Contract, TimeRange, SequenceItem

ASR_REMOTE_KEY = r"(openrouter|elevenlabs)(:[A-Za-z0-9][A-Za-z0-9._/:-]{0,119})?"  # 與 asr_models.REMOTE_KEY_PATTERN 相同


class ProjectRequest(Contract):
    name: str = Field(min_length=1, max_length=200)


class ProviderRequest(Contract):
    """供應者設定：不接受 api_key 欄位（extra=forbid），金鑰只走環境變數或 secrets 端點。"""
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    adapter: Literal["openai_compatible", "elevenlabs"] = "openai_compatible"
    base_url: str = Field(min_length=8, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    gpu_ownership: Literal["external", "managed", "cpu"] = "external"
    timeout_sec: int = Field(default=60, strict=True, ge=1, le=600)
    max_retries: Literal[0, 1] = 1
    max_context_chars: int = Field(default=12000, strict=True, ge=1000, le=1000000)
    max_output_tokens: int = Field(default=2048, strict=True, ge=1, le=32768)
    response_format_mode: Literal["text", "json_object", "json_schema"] = "text"
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    allow_remote: bool = False
    api_key_env: str | None = Field(default=None, max_length=100)
    # 語音轉錄端口用的模型（OpenRouter /audio/transcriptions），與語言模型 model 分開、可替換
    transcription_model: str | None = Field(default=None, min_length=1, max_length=200)
    # 同一個來源登記的全部轉錄模型（2026-09-21：OpenRouter 再新增 microsoft/mai-transcribe-2）；精修模型清單每個各一個選項
    transcription_models: list[Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/:-]*$")]] | None = Field(default=None, max_length=20)


class SubtitlePreviewRequest(Contract):
    """字幕預覽（2026-09-20）：參數與「匯出 SRT」相同，回傳的就是匯出檔的內容（不寫檔）。"""
    transcript_revision: str = Field(min_length=1, max_length=200)
    source_id: str | None = None
    sequence_revision: str | None = None
    ranges: list[TimeRange] = Field(default_factory=list, max_length=500)
    alignment_revision: str | None = None
    sentences_per_cue: Literal[1, 2] = 1
    keep_punctuation: bool = False
    subtitle_timebase: Literal["source", "clip", "sequence"] = "sequence"
    grouping: Literal["merge"] = "merge"
    show_language: bool = False


class RealtimeSessionRequest(Contract):
    """即時字幕與翻譯（M7，2026-09-20）：faster-whisper 滑動視窗；translate_to 有值時每一行交給 provider_id 的文字模型翻譯。"""
    model: Literal["turbo", "large-v3"] = "turbo"
    language: Literal["auto", "zh", "ja", "en"] = "zh"
    hints: str | None = Field(default=None, max_length=2000)
    translate_to: Literal["zh-TW", "en", "ja"] | None = None
    provider_id: str | None = Field(default=None, max_length=64)
    remote_consent: bool = False
    step_sec: float = Field(default=1.0, ge=.25, le=5)
    device: Literal["auto", "cpu", "cuda"] = "auto"


class KeywordsRequest(Contract):
    """「內容拆解單詞」（2026-09-20）：把貼上的內容整理成逗號分隔的關鍵詞；provider_id 是目前選的文字模型，不填＝只用本機規則。"""
    text: str = Field(min_length=1, max_length=8000)
    provider_id: str | None = Field(default=None, max_length=64)
    remote_consent: bool = False


class SecretRequest(Contract):
    secret: str = Field(min_length=1, max_length=4096)


class ModelDownloadRequest(Contract):
    """選用模型的複製／下載：必須明示 confirm，ID 來自 models.manifest.json（hf:repo、torch:file、gguf:dir）。"""
    ids: list[str] = Field(min_length=1, max_length=20)
    confirm: bool = False


class SourceRequest(Contract):
    kind: Literal["youtube", "local"]
    url: str | None = None
    root_id: str | None = None
    relative_path: str | None = None

    @model_validator(mode="after")
    def source_fields(self):
        if self.kind == "youtube" and (not self.url or self.relative_path or self.root_id):
            raise ValueError("YouTube 來源只能指定網址")
        if self.kind == "local" and (self.url or not self.root_id or not self.relative_path):
            raise ValueError("本機來源須指定允許目錄與相對路徑")
        return self


class SequenceRequest(Contract):
    base_revision: str
    source_id: str
    items: list[SequenceItem] = Field(max_length=10000)
    proposal_id: str | None = None
    base_transcript_revision: str | None = None
    map_revision: str | None = None


class FormatPolicy(Contract):
    container: Literal["source", "mp4", "mkv", "m4a", "mp3"] = "source"
    max_height: int | None = Field(default=None, strict=True, ge=1, le=16384)
    max_fps: int | None = Field(default=None, strict=True, ge=1, le=240)
    audio_track_id: str | None = None
    audio_bitrate_kbps: int | None = Field(default=None, strict=True, ge=32, le=512)
    allow_transcode: bool = False


class FollowUp(Contract):
    kind: Literal["analyze"] = "analyze"
    profile: Literal["draft", "balanced", "quality"] = "draft"
    engine: Literal["whisperx", "vibevoice"] = "whisperx"
    fallback_engine: Literal["whisperx"] | None = None
    # 下載完成後自動轉錄用的精修模型、轉錄術語提示與遠端同意（與 analyze 同欄位；2026-09-21 真瀏覽器：以前沒帶，一律用預設）
    asr_model: str | None = Field(default=None, pattern=r"^(breeze-asr-25|large-v3|qwen3-asr-1\.7b|" + ASR_REMOTE_KEY + r")$")
    asr_hints: str | None = Field(default=None, max_length=2000)
    remote_consent: bool = False


class JobRequest(Contract):
    kind: Literal["probe", "acquire", "acquire_subtitles", "analyze", "refine", "align", "summarize", "correct", "plan_edits", "export", "import", "workflow"]
    source_language: str = Field(default="auto", min_length=1, max_length=40)
    source_kind: Literal["prefer_manual", "manual", "automatic"] = "prefer_manual"
    source_id: str | None = None
    asset_kind: Literal["audio", "video"] = "audio"
    ranges: list[TimeRange] = Field(default_factory=list, max_length=100)
    asset_ids: list[str] = Field(default_factory=list, max_length=100)
    quality: Literal["source", "preview"] = "source"
    boundary_policy: Literal["source_seek", "accurate"] = "accurate"  # 2026-09-18：source_seek 起點會提前（關鍵影格），預設改精準
    format_policy: FormatPolicy = Field(default_factory=FormatPolicy)
    output_root_id: str | None = None
    follow_up: FollowUp | None = None
    profile: Literal["draft", "balanced", "quality"] = "draft"
    auto_align: bool = True  # analyze 完成後自動排逐詞對齊（2026-09-18：對齊是輸出的一部分，不是另一顆按鈕）
    # 轉錄完成（並逐詞對齊）後自動輸出字幕與紀錄：{output_root_id, sentences_per_cue, keep_punctuation}
    auto_export: dict | None = None
    then_export: dict | None = None  # align 工作成功後接著排的匯出（由 analyze 轉交）
    include_records: bool = False  # 匯出時一併寫出逐字稿 JSON 與修改紀錄 JSON
    # 匯出字幕前先等這一版逐字稿的逐詞對齊（已完成就直接用；進行中就等；沒人排過就自己排一個）；失敗或逾時退回句子時間並在清單加警告
    await_alignment: bool = False
    model: str = Field(default="turbo", max_length=200)
    # analyze 的精修那一趟用哪個模型（2026-09-19）：large-v3 或模型清單 ct2 類的 Breeze-ASR-26（只處理中文段落，需先安裝）；
    # 不指定（None）＝服務預設：已安裝 Qwen3-ASR-1.7B 就用它（2026-09-20 CP 值比對），否則 large-v3（asr_models.default_model）。
    # 2026-09-20：Breeze-ASR-26 與 Qwen3-ASR-0.6B 依使用者要求移除；清單只留四種
    # openrouter：遠端轉錄（2026-09-20），聲音會送出本機 → 需要 remote_consent 與金鑰，且永遠不是預設
    # 2026-09-21：加 elevenlabs，與同一來源的其他轉錄模型「來源:模型名稱」（例如 openrouter:microsoft/mai-transcribe-2）
    asr_model: str | None = Field(default=None, pattern=r"^(breeze-asr-25|large-v3|qwen3-asr-1\.7b|" + ASR_REMOTE_KEY + r")$")
    # 轉錄術語提示（2026-09-20）：以逗號分隔的專有名詞，送進轉錄模型（faster-whisper hotwords、Qwen context），與校字詞彙分開
    asr_hints: str | None = Field(default=None, max_length=2000)
    remote_consent: bool = False
    engine: Literal["whisperx", "vibevoice"] = "whisperx"
    fallback_engine: Literal["whisperx"] | None = None
    device: Literal["auto", "cpu", "cuda"] = "auto"
    language_policy: Literal["auto_ja_zh_en", "zh", "ja", "en"] = "auto_ja_zh_en"
    music_policy: Literal["conservative", "off"] = "conservative"
    max_refine_audio_ratio: float = Field(default=.15, ge=0, le=1)
    transcript_revision: str | None = None
    alignment_revision: str | None = None
    sequence_revision: str | None = None
    base_sequence_revision: str | None = None
    map_revision: str | None = None
    audio_track_id: str = "default"
    cue_ids: list[str] = Field(default_factory=list)
    context_us: int = Field(default=300000, strict=True, ge=0, le=30000000)
    provider_id: str | None = None
    glossary_revision: str | None = None
    glossary: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=100)
    # 校字參考資料（2026-09-20）：故事大綱、角色表等自由文字，送進校字的 context.reference，只用來判斷詞彙寫法
    reference_text: str | None = Field(default=None, max_length=6000)
    # 校字強度（2026-09-20）：conservative＝只改明顯錯字；rewrite＝依上下文逐句改寫（全部套用、可整批還原）
    correction_mode: Literal["conservative", "rewrite"] = "conservative"
    mode: Literal["suggest"] = "suggest"
    outputs: list[Literal["summary", "chapters", "highlights"]] = Field(default_factory=lambda: ["summary", "highlights"])
    scope: Literal["analyzed_coverage"] = "analyzed_coverage"
    intent: str = Field(default="", max_length=4000)
    target_highlight_duration_us: int = Field(default=180000000, strict=True, gt=0)
    target_duration_us: int = Field(default=180000000, strict=True, gt=0)
    formats: list[Literal["mp4", "audio", "srt", "vtt", "transcript_json", "summary_md", "chapters_txt", "studio_json", "llc", "csv"]] = Field(default_factory=lambda: ["srt"])
    grouping: Literal["separate", "merge"] = "merge"
    cut_mode: Literal["copy", "accurate"] = "accurate"
    subtitle_timebase: Literal["source", "clip", "sequence"] = "sequence"
    subtitle_mode: Literal["natural", "oneline"] = "oneline"
    sentences_per_cue: Literal[1, 2] = 1
    keep_punctuation: bool = False
    alignment_policy: Literal["require_word", "allow_segment"] = "allow_segment"
    show_language: bool = False
    timeout_sec: int = Field(default=1800, strict=True, ge=1, le=14400)

    @model_validator(mode="after")
    def required_fields(self):
        if self.kind in {"probe", "acquire", "acquire_subtitles", "analyze"} and not self.source_id:
            raise ValueError("此工作需要 source_id")
        if self.kind in {"correct", "summarize", "align", "refine", "plan_edits"} and not self.transcript_revision:
            raise ValueError("此工作需要固定逐字稿版本")
        if self.kind == "export" and not self.sequence_revision and not (self.source_id and self.ranges):
            raise ValueError("匯出需要固定序列版本，或直接指定 source_id 與 ranges")
        if self.kind == "export" and self.subtitle_timebase != "source":
            expected = "clip" if self.grouping == "separate" else "sequence"
            if self.subtitle_timebase != expected:
                raise ValueError("字幕時基與分段／合併設定矛盾")
        return self


class Acquisition(Contract):
    output_root_id: str | None = None
    audio_first: bool = True
    video: Literal["none", "selected", "full"] = "none"
    quality: Literal["source", "preview"] = "source"
    format_policy: FormatPolicy = Field(default_factory=FormatPolicy)
    boundary_policy: Literal["source_seek", "accurate"] = "accurate"


class Analysis(Contract):
    mode: Literal["none", "draft", "balanced", "quality"] = "draft"
    engine: Literal["whisperx", "vibevoice"] = "whisperx"
    fallback_engine: Literal["whisperx"] | None = None
    summary: bool = False
    provider_id: str | None = None
    model: str = "turbo"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    language_policy: Literal["auto_ja_zh_en", "zh", "ja", "en"] = "auto_ja_zh_en"
    music_policy: Literal["conservative", "off"] = "conservative"


class Subtitles(Contract):
    source_language: str = Field(default="auto", min_length=1, max_length=40)
    source_kind: Literal["prefer_manual", "manual", "automatic"] = "prefer_manual"
    policy: Literal["none", "source", "generate", "compare"] = "generate"
    alignment: Literal["require_word", "allow_segment"] = "allow_segment"
    sentences_per_cue: Literal[1, 2] = 1
    keep_punctuation: bool = False


class Deliverables(Contract):
    output_root_id: str | None = None
    grouping: Literal["separate", "merge"] = "merge"
    formats: list[str] = Field(default_factory=lambda: ["srt"])
    cut_mode: Literal["copy", "accurate"] = "accurate"


class WorkflowRequest(Contract):
    source_id: str
    ranges: list[TimeRange] = Field(default_factory=list)
    sequence_revision: str | None = None
    acquisition: Acquisition = Field(default_factory=Acquisition)
    analysis: Analysis = Field(default_factory=Analysis)
    subtitles: Subtitles = Field(default_factory=Subtitles)
    deliverables: Deliverables = Field(default_factory=Deliverables)

    @model_validator(mode="after")
    def dependencies(self):
        if self.subtitles.policy == "source" and self.subtitles.alignment == "require_word":
            raise ValueError("來源字幕只有原始段落時間；逐詞對齊請使用生成或比對模式")
        if self.analysis.mode == "none" and (self.analysis.summary or self.subtitles.policy in {"generate", "compare"}):
            raise ValueError("摘要或生成字幕需要音訊分析")
        allowed = {"mp4", "audio", "srt", "vtt", "transcript_json", "summary_md", "chapters_txt", "studio_json", "llc", "csv"}
        if set(self.deliverables.formats) - allowed:
            raise ValueError("未知輸出格式")
        if any(f in self.deliverables.formats for f in {"srt", "vtt"}) and self.subtitles.policy == "none":
            raise ValueError("字幕輸出格式與不輸出字幕的設定矛盾")
        if "mp4" in self.deliverables.formats and self.acquisition.video == "none":
            raise ValueError("MP4 匯出需要影片取得策略")
        return self
