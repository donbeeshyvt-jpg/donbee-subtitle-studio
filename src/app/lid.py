"""逐段語言偵測：kana 腳本啟發 → lingua 裁決 → 白名單。"""
import re
from app.config import SUPPORTED_LANGS

_KANA = re.compile(r"[぀-ヿ]")   # 平假名 + 片假名
_HAN = re.compile(r"[一-鿿]")    # CJK 統一表意文字
_LATIN = re.compile(r"[A-Za-z]")

_detector = None
_LINGUA_MAP = {"JAPANESE": "ja", "CHINESE": "zh", "ENGLISH": "en"}


def _get_detector():
    """延遲建立 lingua 偵測器（ja/zh/en），避免 import 時即載入。"""
    global _detector
    if _detector is None:
        from lingua import Language, LanguageDetectorBuilder
        _detector = LanguageDetectorBuilder.from_languages(
            Language.JAPANESE, Language.CHINESE, Language.ENGLISH).build()
    return _detector


def label_cue(text, prev_lang=None):
    """
    回傳語言碼 'ja'/'zh'/'en'，或 None（無法判定）。
    層 A：含假名 → ja（華語絕不含假名，決定性）。
    層 B：純拉丁 → en；其餘交 lingua 裁決。
    層 C：極短（<=2 字）感嘆詞 → 繼承前一 cue 語言。
    """
    t = (text or "").strip()
    if not t:
        return prev_lang
    if _KANA.search(t):
        return "ja"
    if _LATIN.search(t) and not _HAN.search(t):
        return "en"
    if prev_lang and len(re.sub(r"\s", "", t)) <= 2:
        return prev_lang
    try:
        lang = _get_detector().detect_language_of(t)
        if lang is not None:
            code = _LINGUA_MAP.get(lang.name)
            if code in SUPPORTED_LANGS:
                return code
    except Exception:
        pass
    if _HAN.search(t):           # 退回：有漢字當華語
        return "zh"
    return prev_lang
