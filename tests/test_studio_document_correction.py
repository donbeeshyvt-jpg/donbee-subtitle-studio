"""整份逐字稿一次送出的校字（2026-09-20 使用者：參考 srt-context-proofreader 技能）。

要點：走 API 時「整份送一次、收回結果再套用」，不要為了同一次校字連續打很多請求；
本機小模型放不下時才分段，而且每一段都要附上前後鄰句（只能讀、不可改），段落之間不重疊、合起來涵蓋全文。
"""
import json

import pytest

from app.studio.providers import ProviderError, correct
from tests.test_studio_providers import server

LONG = [dict(id=f'c{i:03d}', raw_text=f'第{i}句彈步遊戲真的好難我們再來一次好不好呢這樣講有沒有比較清楚一點點',
             start_us=i * 1000000, end_us=(i + 1) * 1000000) for i in range(40)]


def sent_cue_ids(call):
    payload = json.loads(call['messages'][-1]['content'])
    return [row['cue_id'] for row in payload['cues']]


def context_ids(call, key):
    payload = json.loads(call['messages'][-1]['content'])
    return [row['cue_id'] for row in payload.get(key, [])]


def test_whole_transcript_is_sent_in_one_request_when_it_fits():
    """API 常見用法：一次送整份、收回結果再套用（不會連續送很多次）。"""
    with server([{'patches': []}]) as (config, calls):
        config['max_context_chars'] = 120000
        config['max_output_tokens'] = 16384
        result = correct(LONG, config, ['彈幕'])
    assert len(calls) == 1  # 只打一次
    assert sent_cue_ids(calls[0]) == [cue['id'] for cue in LONG]  # 整份都在同一個請求裡
    assert result['requests'] == 1 and result['segmented'] is False
    assert context_ids(calls[0], 'context_before') == [] and context_ids(calls[0], 'context_after') == []


def test_a_transcript_too_large_for_the_model_is_split_with_neighbour_context():
    """本機小模型放不下時才分段：段落不重疊、涵蓋全文，每段附上前後鄰句供閱讀。"""
    with server([{'patches': []}] * 8) as (config, calls):
        config['max_context_chars'] = 2500
        config['max_output_tokens'] = 4096
        result = correct(LONG, config, ['彈幕'])
    assert 1 < len(calls) <= 8
    editable = [sent_cue_ids(call) for call in calls]
    assert [i for part in editable for i in part] == [cue['id'] for cue in LONG]  # 不重疊、不漏、順序一致
    assert context_ids(calls[1], 'context_before')  # 中間段看得到前一段的句子
    assert context_ids(calls[0], 'context_after')  # 第一段看得到下一段的句子
    assert context_ids(calls[0], 'context_before') == []
    assert context_ids(calls[-1], 'context_after') == []
    assert set(context_ids(calls[1], 'context_before')) & set(editable[0])  # 鄰句就是隔壁段的句子
    assert set(context_ids(calls[1], 'context_before')) & set(editable[1]) == set()  # 鄰句不可與本段重複
    assert result['requests'] == len(calls) and result['segmented'] is True
    assert result['context_window_cues'] > 0


def test_a_patch_for_a_read_only_neighbour_cue_is_not_applied():
    """鄰句只能讀：模型若回鄰句的修改，那一段視為無效輸出，不會套用到別段。"""
    bad = {'patches': [{'cue_id': 'c000', 'original_text': LONG[0]['raw_text'],
                        'replacement_text': '被鄰句偷改', 'reason': '越界'}]}
    with server([bad, bad, {'patches': []}] * 8) as (config, calls):
        config['max_context_chars'] = 2500
        config['max_output_tokens'] = 4096
        config['max_retries'] = 1
        result = correct(LONG, config, ['彈幕'])
    assert all(patch['cue_id'] != 'c000' or patch['replacement_text'] != '被鄰句偷改' for patch in result['patches'])
    assert result['failed_chunks']  # 第二段改到第一段的句子＝那一段無效，並且照實回報


def test_the_output_budget_also_decides_how_much_goes_in_one_request():
    """輸出上限太小時仍要分段（否則一定被截斷）。
    2026-09-21 改：以前假設「逐句改寫每句都回、而且變長 2.4 倍」，真跑 10 分鐘直播 307 句 DeepSeek 只回 21 句（7%），
    卻被迫拆成 6 次送 → 兩種強度都照「只回有改的句子」估，真的被截斷才接著送（見下面兩個測試）。"""
    with server([{'patches': []}] * 12) as (config, calls):
        config['max_context_chars'] = 120000
        config['max_output_tokens'] = 256  # 輸出放不下整份
        correct(LONG, config, ['彈幕'], mode='rewrite')
    assert len(calls) > 1


STREAM = [dict(id=f'cue_{i:032x}', raw_text=['讓我們研究一下喔', '有種小影子眼鏡反光有柯南的感覺', '遇到需要就是打乖的地方就打乖',
                                             '然後上一屆的聖誕會', '我的事就是它會稀奇'][i % 5],
               start_us=i * 1950000, end_us=(i + 1) * 1950000) for i in range(307)]


def test_rewrite_mode_sends_a_ten_minute_stream_in_one_request():
    """2026-09-21 真跑：10 分鐘直播 307 句、OpenRouter 預設額度（上下文 12 萬字、輸出 8192），逐句改寫被拆成 6 次 → 應該一次送完。"""
    with server([{'patches': []}] * 8) as (config, calls):
        config['max_context_chars'] = 120000
        config['max_output_tokens'] = 8192
        result = correct(STREAM, config, ['星之卡比'], mode='rewrite')
    assert len(calls) == 1 and result['requests'] == 1 and result['segmented'] is False
    assert sent_cue_ids(calls[0]) == [cue['id'] for cue in STREAM]


def _patch(cue):
    return {'cue_id': cue['id'], 'original_text': cue['raw_text'], 'replacement_text': cue['raw_text'].replace('彈步', '彈幕'),
            'reason': '同音錯字'}


def test_truncated_patches_continue_from_the_last_line_the_model_reached():
    """真的改很多、輸出被截斷時：收下已完整的修改，從模型回到的最後一句之後接著送剩下的句子（附前文），不漏、不重送整份。"""
    first = json.dumps({'patches': [_patch(LONG[5]), _patch(LONG[10])]}, ensure_ascii=False)
    cut = first[:-2] + ',{"cue_id":"c01'  # 第三筆寫到一半就被截斷
    later = {'patches': [_patch(LONG[30])]}
    with server([('length', cut), later]) as (config, calls):
        config['max_context_chars'] = 120000
        config['max_output_tokens'] = 8192
        result = correct(LONG, config, ['彈幕'], mode='rewrite')
    assert len(calls) == 2 and result['requests'] == 2 and result['continued_chunks'] == 1
    assert sent_cue_ids(calls[0]) == [cue['id'] for cue in LONG]
    assert sent_cue_ids(calls[1]) == [cue['id'] for cue in LONG[11:]]  # 從 c010 之後接著送
    assert 'c010' in context_ids(calls[1], 'context_before')  # 附前文
    assert sorted(p['cue_id'] for p in result['patches']) == ['c005', 'c010', 'c030']


def test_truncated_before_any_complete_patch_splits_that_part_in_half():
    """截斷到連一筆完整修改都沒有：不知道模型看到哪裡 → 把那一段切半重送，不會原地重送同一段（避免無限重試）。"""
    with server([('length', '{"patches":[{"cue_id":"c0'), {'patches': []}, {'patches': []}]) as (config, calls):
        config['max_context_chars'] = 120000
        config['max_output_tokens'] = 8192
        result = correct(LONG, config, ['彈幕'], mode='rewrite')
    assert len(calls) == 3
    assert sent_cue_ids(calls[1]) + sent_cue_ids(calls[2]) == [cue['id'] for cue in LONG]
    assert result['continued_chunks'] == 1


def test_remote_defaults_are_large_enough_to_send_a_whole_transcript_at_once():
    from app.studio.providers import DEFAULT_PROVIDERS, transcription_only, upgrade_default_providers
    # 只做轉錄的 ElevenLabs 不送文字，沒有上下文額度
    remote = {item['id']: item for item in DEFAULT_PROVIDERS if item.get('allow_remote') and not transcription_only(item)}
    for item in remote.values():
        assert item['max_context_chars'] >= 100000 and item['max_output_tokens'] >= 8192
    legacy = [dict(id='api-openrouter', model='openai/gpt-4o-mini', max_context_chars=24000, max_output_tokens=4096)]
    assert upgrade_default_providers(legacy) is True  # 舊設定檔自動放大，使用者不必自己改
    assert legacy[0]['max_context_chars'] >= 100000 and legacy[0]['max_output_tokens'] >= 8192
    custom = [dict(id='api-openrouter', model='openai/gpt-4o-mini', max_context_chars=8000, max_output_tokens=1024)]
    assert upgrade_default_providers(custom) is False  # 使用者自己調過的值不動


def test_a_context_overflow_is_recovered_by_splitting_that_part():
    """整份送出後服務說放不下（本機模型載入的上下文比設定小）：自動切半重送，不是整個工作失敗。"""
    overflow = (400, {'error': {'message': "This model's maximum context length is 4096 tokens."}})
    with server([overflow, {'patches': []}, {'patches': []}]) as (config, calls):
        config['max_context_chars'] = 120000
        config['max_output_tokens'] = 16384
        result = correct(LONG, config, ['彈幕'])
    assert len(calls) == 3  # 一次整份（被拒）＋ 切成兩半各一次
    halves = [sent_cue_ids(call) for call in calls[1:]]
    assert halves[0] + halves[1] == [cue['id'] for cue in LONG]
    assert context_ids(calls[1], 'context_after') and context_ids(calls[2], 'context_before')
    assert result['context_splits'] == 1 and result['requests'] == 3
    assert not result['failed_chunks']


def test_local_defaults_try_one_request_first_because_overflow_is_now_recoverable():
    """本機預設也先試整份送（放不下會自動切半），不再一開始就切小段。"""
    from app.studio.providers import DEFAULT_PROVIDERS, upgrade_default_providers
    local = [item for item in DEFAULT_PROVIDERS if not item.get('allow_remote')]
    for item in local:
        assert item['max_context_chars'] >= 12000 and item['max_output_tokens'] >= 2048
    legacy = [dict(id='local-lmstudio', model='auto', max_context_chars=6000, max_output_tokens=1536)]
    assert upgrade_default_providers(legacy) is True
    assert legacy[0]['max_context_chars'] >= 12000 and legacy[0]['max_output_tokens'] >= 2048
    tuned = [dict(id='local-lmstudio', model='auto', max_context_chars=4000, max_output_tokens=1024)]
    assert upgrade_default_providers(tuned) is False  # 使用者自己調小的不動


# 逐行保留標點（srt-context-proofreader 技能）：原行沒有標點就不該被加上
PUNCTUATED = [dict(id='p1', raw_text='碰碰大家繼續說', start_us=0, end_us=1000000),
              dict(id='p2', raw_text='對阿，我知道了。', start_us=1000000, end_us=2000000)]


def test_punctuation_is_not_added_to_a_line_that_had_none():
    # 2026-09-21：原本的例子「好→好了」是加語助詞，新規則下本來就不該收；改用真的聽錯的名字
    reply = {'patches': [{'cue_id': 'p1', 'original_text': '碰碰大家繼續說',
                          'replacement_text': '彭彭，大家繼續說', 'reason': '詞彙表人名的近音錯字'}]}
    with server([reply]) as (config, _):
        result = correct(PUNCTUATED, config, ['彭彭'], mode='rewrite')
    patch = result['patches'][0]
    assert patch['replacement_text'] == '彭彭大家繼續說'  # 文字修正照留，新增的逗號拿掉
    assert patch['punctuation_stripped'] is True


def test_rewriting_the_punctuation_of_a_line_that_had_some_is_held_back_when_being_careful():
    reply = {'patches': [{'cue_id': 'p2', 'original_text': '對阿，我知道了。',
                          'replacement_text': '對啊！我知道了', 'reason': '同音錯字'}]}
    # 2026-09-21：只有「字幕保留標點」時才管標點序列；預設不保留標點時，標點本來就會拿掉，文字修正照套
    with server([reply]) as (config, _):
        careful = correct(PUNCTUATED, config, [], mode='conservative', keep_punctuation=True)
    assert careful['patches'] == []
    assert 'punctuation_changed' in careful['rejected_patches'][0]['rejection_reasons']
    with server([reply]) as (config, _):
        bold = correct(PUNCTUATED, config, [], mode='rewrite', keep_punctuation=True)
    assert bold['patches'][0]['replacement_text'] == '對啊！我知道了'  # 積極模式照套，介面上看得到新舊比對
    with server([reply]) as (config, _):
        plain = correct(PUNCTUATED, config, [], mode='conservative')
    assert plain['patches'][0]['replacement_text'] == '對啊我知道了' and plain['rejected_patches'] == []


def test_openrouter_cost_is_kept_in_the_usage_totals():
    """2026-09-21：OpenRouter 的 usage 會回 cost（美元，小數）；以前只加總整數欄位，費用被丟掉，結果看不到花了多少。"""
    reply = {'patches': []}
    with server([reply] * 8) as (config, calls):
        config['max_context_chars'] = 2500
        config['max_output_tokens'] = 4096
        import app.studio.providers as providers_module
        original = providers_module._request
        def with_cost(settings, method, path, body=None):
            response = original(settings, method, path, body)
            if path == '/chat/completions':
                response['usage'] = dict(response.get('usage') or {}, cost=0.00012, is_byok=False)
            return response
        providers_module._request = with_cost
        try:
            result = correct(LONG[:24], config, ['彈幕'])
        finally:
            providers_module._request = original
    assert result['requests'] >= 1
    assert abs(result['usage']['cost'] - 0.00012 * result['requests']) < 1e-9  # 每次請求的費用都加總
    assert 'is_byok' not in result['usage']  # 布林值不當成數字加總


def test_corrections_to_chinese_lines_come_back_in_traditional_characters():
    """2026-09-21 真跑：本機 gemma 把「原來會打死都沒有」改成「原來會打死隊友都没有」（没＝簡體）。
    提示詞說要保留繁體，但沒有強制；轉錄流程本來就會轉繁體，校字結果也要。日文／英文句子不動。"""
    cues = [dict(id='z', raw_text='原來會打死都沒有', start_us=0, end_us=1000000, lang='zh'),
            dict(id='j', raw_text='あたましい', start_us=1000000, end_us=2000000, lang='ja')]
    reply = {'patches': [{'cue_id': 'z', 'original_text': '原來會打死都沒有', 'replacement_text': '原來會打死隊友都没有', 'reason': '依上下文修正'},
                         {'cue_id': 'j', 'original_text': 'あたましい', 'replacement_text': 'あたらしい', 'reason': '錯字'}]}
    with server([reply]) as (config, _):
        result = correct(cues, config, [], mode='rewrite')
    by_id = {p['cue_id']: p['replacement_text'] for p in result['patches']}
    assert by_id['z'] == '原來會打死隊友都沒有'  # 没 → 沒
    assert by_id['j'] == 'あたらしい'  # 日文不轉


# ---- 2026-09-21 使用者：「送 API 的修正提出的 PROMPT，不是只修個案的字，要修改根本問題」----

def _system_and_user(call):
    return call['messages'][0]['content'], json.loads(call['messages'][-1]['content'])


def test_prompt_states_the_output_format_from_the_users_subtitle_settings():
    """根本原因：提示詞寫著「不做繁簡轉換」「原句有標點就保留原符號…不改全形半形」，也沒告訴模型字幕要什麼格式。
    改成把使用者的字幕設定（台灣繁體、要不要標點）明確放進請求。"""
    cues = [dict(id='a', raw_text='我在哪', start_us=0, end_us=1000000)]
    with server([{'patches': []}, {'patches': []}]) as (config, calls):
        correct(cues, config, [], mode='rewrite')
        correct(cues, config, [], mode='rewrite', keep_punctuation=True)
    system, user = _system_and_user(calls[0])
    assert user['context']['output'] == {'script': 'zh-Hant-TW', 'punctuation': 'none'}  # 預設＝網頁預設「不保留標點」
    assert '不做繁簡轉換' not in system and '不改全形半形' not in system  # 與目標格式衝突的舊規則拿掉
    assert '台灣繁體' in system and '簡體' in system  # 明講輸出用台灣繁體、簡體要改掉
    assert 'context.output' in system
    assert _system_and_user(calls[1])[1]['context']['output']['punctuation'] == 'keep'


def test_prompt_forbids_rewording_the_speaker_in_both_modes():
    """真跑看到的過度改寫（講→說、有了→有啦、對不起→不好意思）是提示詞叫模型「改成通順」造成的；
    兩種強度都要明講：只改聽錯的字，不換同義詞、不動語助詞、不改成書面語。"""
    cues = [dict(id='a', raw_text='我跟你講', start_us=0, end_us=1000000)]
    for mode in ('conservative', 'rewrite'):
        with server([{'patches': []}]) as (config, calls):
            correct(cues, config, [], mode=mode)
        system, _ = _system_and_user(calls[0])
        assert '改成通順' not in system
        assert '同義詞' in system and '語助詞' in system
        assert '講' in system and '說' in system  # 用實際看到的例子講清楚


def test_no_punctuation_format_strips_what_the_model_adds_instead_of_blocking_the_fix():
    """字幕格式是不保留標點時：模型給的修正若帶標點，拿掉標點、保留文字修正（不是整筆擋掉）。"""
    cues = [dict(id='q', raw_text='原來會打死的。', start_us=0, end_us=1000000)]
    reply = {'patches': [{'cue_id': 'q', 'original_text': '原來會打死的。', 'replacement_text': '原來會打死隊友。', 'reason': '依上下文修正聽錯的字'}]}
    with server([reply]) as (config, _):
        careful = correct(cues, config, [], mode='conservative')
    assert [p['replacement_text'] for p in careful['patches']] == ['原來會打死隊友']
    assert careful['rejected_patches'] == []


def test_small_models_that_ignore_the_rules_are_stopped_by_general_guards():
    """真跑（第三十一輪）：新提示詞下 DeepSeek 0 次改寫用詞，但本機 gemma-4-e4b 照樣「有了→有啦」「講→說」「哦→喔」，
    理由還寫「修正語氣詞」「更口語化」「可互換…更流暢」。一般規則擋下（不是列字詞清單）：
    - 只動到語助詞的修改（拿掉語助詞後兩句一樣）→ 兩種強度都擋
    - 模型自己說是潤飾（更流暢、口語化、可互換…）→ 逐句改寫也擋（改寫＝修正聽錯的字，不是潤飾）
    真正聽錯的字照樣套用。"""
    cues = [dict(id='p', raw_text='有了', start_us=0, end_us=1000000),
            dict(id='s', raw_text='我可以講', start_us=1000000, end_us=2000000),
            dict(id='o', raw_text='我在哪哦', start_us=2000000, end_us=3000000),
            dict(id='r', raw_text='原來會打死都還沒', start_us=3000000, end_us=4000000)]
    reply = {'patches': [
        {'cue_id': 'p', 'original_text': '有了', 'replacement_text': '有啦', 'reason': '此處應為感嘆的語氣詞，修正為更口語化的「有啦」'},
        {'cue_id': 's', 'original_text': '我可以講', 'replacement_text': '我可以說', 'reason': '「講」和「說」可互換，使用「說」更流暢'},
        {'cue_id': 'o', 'original_text': '我在哪哦', 'replacement_text': '我在哪喔', 'reason': '修正語氣詞的書寫'},
        {'cue_id': 'r', 'original_text': '原來會打死都還沒', 'replacement_text': '原來會打死隊友', 'reason': '依上下文修正聽錯的字'}]}
    with server([reply]) as (config, _):
        result = correct(cues, config, [], mode='rewrite')
    assert [p['cue_id'] for p in result['patches']] == ['r']  # 只有真的聽錯的那句
    blocked = {p['cue_id']: p['rejection_reasons'] for p in result['rejected_patches']}
    assert 'particle_only_change' in blocked['p'] and 'particle_only_change' in blocked['o']
    assert 'stylistic_rewrite' in blocked['s']


def test_openrouter_limits_left_at_the_previous_default_are_raised():
    from app.studio.providers import DEFAULT_PROVIDERS, upgrade_default_providers
    remote = next(p for p in DEFAULT_PROVIDERS if p['id'] == 'api-openrouter')
    assert remote['max_context_chars'] == 240000 and remote['max_output_tokens'] == 16384
    previous = [dict(id='api-openrouter', model='deepseek/deepseek-v4.1-flash', max_context_chars=120000, max_output_tokens=8192)]
    assert upgrade_default_providers(previous) is True
    assert previous[0]['max_context_chars'] == 240000 and previous[0]['max_output_tokens'] == 16384
