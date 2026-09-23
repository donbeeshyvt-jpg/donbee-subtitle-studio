"""固定微秒、不可伸縮來源對映與有版本的剪輯資料契約。"""
from __future__ import annotations

from decimal import Decimal
import re
from typing import Annotated, Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_US = 9007199254740991
Microseconds = Annotated[int, Field(strict=True, ge=0, le=MAX_US)]


# Whisper 在音樂／靜音段常見的幻聽字幕署名與片尾語；只用來「標記可疑」，不自動刪除。
HALLUCINATION_PATTERN = re.compile(
    r"(字幕小組|字幕組|字幕志願者|字幕由.{0,12}提供|字幕製作|請不吝點[贊赞讚]|訂閱.{0,6}(轉發|打賞)|"
    r"ご視聴ありがとうございました|チャンネル登録|thanks for watching|subtitles by|amara\.org)", re.I)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TimeRange(Contract):
    start_us: Microseconds
    end_us: Microseconds

    @model_validator(mode="after")
    def ordered(self):
        if self.end_us <= self.start_us:
            raise ValueError("區段結束必須晚於開始")
        return self


class SequenceItem(Contract):
    id: str = Field(default_factory=lambda: new_id("seg"))
    kind: Literal["clip", "marker"] = "clip"
    start_us: Microseconds
    end_us: Microseconds | None = None
    name: str = Field(default="", max_length=500)
    selected: bool = True
    tags: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_end(self):
        if self.kind == "marker" and self.end_us is not None:
            raise ValueError("標記不可有結束時間")
        if self.kind == "clip" and (self.end_us is None or self.end_us <= self.start_us):
            raise ValueError("片段必須有有效的開始與結束時間")
        return self


class SourceMap(Contract):
    id: str = Field(default_factory=lambda: new_id("map"))
    source_start_us: Microseconds
    source_end_us: Microseconds
    asset_start_us: Microseconds = 0
    asset_end_us: Microseconds
    status: Literal["verified", "estimated"] = "estimated"

    @model_validator(mode="after")
    def equal_span(self):
        source_span = self.source_end_us - self.source_start_us
        if source_span <= 0 or source_span != self.asset_end_us - self.asset_start_us:
            raise ValueError("對映必須等長且非空，不能默默伸縮時間")
        return self

    def to_source(self, asset_us: int) -> int:
        if not self.asset_start_us <= asset_us <= self.asset_end_us:
            raise ValueError("時間不在素材對映範圍內")
        return self.source_start_us + asset_us - self.asset_start_us


class OutputMap(Contract):
    item_id: str
    source_start_us: Microseconds
    source_end_us: Microseconds
    output_start_us: Microseconds
    output_end_us: Microseconds

    def to_output(self, source_us: int) -> int:
        if not self.source_start_us <= source_us <= self.source_end_us:
            raise ValueError("來源時間越界")
        return self.output_start_us + source_us - self.source_start_us

    def to_source(self, output_us: int) -> int:
        if not self.output_start_us <= output_us <= self.output_end_us:
            raise ValueError("輸出時間越界")
        return self.source_start_us + output_us - self.output_start_us


def sequence_mappings(items: list[SequenceItem]) -> list[OutputMap]:
    cursor = 0
    mappings = []
    for item in items:
        if item.kind != "clip" or not item.selected:
            continue
        end = cursor + item.end_us - item.start_us
        mappings.append(OutputMap(item_id=item.id, source_start_us=item.start_us,
                                  source_end_us=item.end_us, output_start_us=cursor, output_end_us=end))
        cursor = end
    return mappings


def parse_time(value: str) -> int:
    if not re.fullmatch(r"\d+(?::\d{1,2}){0,2}(?:\.\d{1,6})?", value):
        raise ValueError("時間格式為秒、MM:SS 或 HH:MM:SS，可帶六位小數")
    parts = value.split(":")
    if len(parts) > 1 and any(Decimal(p) >= 60 for p in parts[1:]):
        raise ValueError("分秒必須小於 60")
    total = Decimal(0)
    for part in parts:
        total = total * 60 + Decimal(part)
    result = int(total * 1000000)
    if result > MAX_US:
        raise ValueError("時間超出安全整數範圍")
    return result


def merge_ranges(ranges: list[TimeRange], duration_us: int | None = None) -> list[TimeRange]:
    result = []
    for span in sorted(ranges, key=lambda r: r.start_us):
        if duration_us is not None and span.end_us > duration_us:
            raise ValueError("區段超出來源時長")
        if result and span.start_us <= result[-1].end_us:
            previous = result.pop()
            result.append(TimeRange(start_us=previous.start_us, end_us=max(previous.end_us, span.end_us)))
        else:
            result.append(span)
    return result
