"""來源字幕格式、範圍與取得契約；下載測試使用明示替身。"""
import copy
import json

import pytest

from app.studio import source_subtitles as captions


VTT='WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n早段\n\n01:50:00.000 --> 01:50:03.000 align:start\n<v speaker>字幕 &amp; 文字</v>\n\n01:50:02.000 --> 01:50:04.000\n重疊句\n'


def metadata():
    return dict(id='Yn2mE6_tMC8',duration=10000,subtitles={'zh-Hant':[dict(ext='vtt',url='https://www.youtube.com/api/timedtext?manual',name='繁體中文')]},automatic_captions={'zh-Hant':[dict(ext='json3',url='https://www.youtube.com/api/timedtext?auto',name='繁體中文')]})


def test_public_metadata_hides_signed_urls_and_manual_preferred():
    before=copy.deepcopy(metadata())
    info=captions.caption_tracks(before)
    assert 'url' not in json.dumps(info)
    assert len(info)==2
    selected=captions.select_track(info,language='zh-Hant')
    assert selected['automatic'] is False
    assert before==metadata()


def test_explicit_automatic_and_unknown_language():
    tracks=captions.caption_tracks(metadata())
    assert captions.select_track(tracks,language='zh-Hant',kind='automatic')['automatic'] is True
    with pytest.raises(captions.CaptionError,match='CAPTION_LANGUAGE_UNAVAILABLE'):
        captions.select_track(tracks,language='ja')
    with pytest.raises(captions.CaptionError,match='NO_SOURCE_CAPTIONS'):
        captions.select_track([])


def test_vtt_nonzero_source_time_and_overlapping_ranges():
    result=captions.parse_captions(VTT,'vtt',language='zh-Hant',ranges=[{'start_us':6601000000,'end_us':6603000000},{'start_us':6602000000,'end_us':6605000000}])
    assert len(result['cues'])==2
    assert result['cues'][0]['start_us']==6600000000
    assert result['cues'][0]['raw_text']=='字幕 & 文字'
    assert result['cues'][1]['start_us']==6602000000
    assert result['cues'][0]['alignment_status']=='segment'


def test_srt_bom_and_multiline_preserve_original_payload():
    content='\ufeff1\r\n00:00:01,001 --> 00:00:02,999\r\n第一行\r\n第二行\r\n'
    result=captions.parse_captions(content,'srt',language='zh')
    assert result['cues'][0]['raw_text']=='第一行\n第二行'
    assert result['cues'][0]['start_us']==1001000
    assert result['format']=='srt'


def test_json3_missing_time_is_unresolved_never_fabricated():
    payload={'events':[{'tStartMs':6600000,'dDurationMs':1500,'segs':[{'utf8':'已知','tOffsetMs':0},{'utf8':'詞'}]},
                       {'tStartMs':6603000,'segs':[{'utf8':'缺結束'}]}]}
    result=captions.parse_captions(json.dumps(payload),'json3')
    assert len(result['cues'])==1
    assert result['cues'][0]['words'][0]['start_us']==6600000000
    assert result['cues'][0]['words'][0]['end_us'] is None
    assert result['cues'][0]['words'][1]['start_us'] is None
    assert result['unresolved_events'][0]['end_us'] is None
    assert 'unknown_event_time' in result['warnings']


@pytest.mark.parametrize('content,fmt',[('nonsense','vtt'),('WEBVTT\n\n-1 --> 5\nx','vtt'),('1\n00:00:03,000 --> 00:00:02,000\nx','srt'),('{broken','json3'),('WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:90000\n','vtt')])
def test_malformed_and_unverified_transport_timestamps_rejected(content,fmt):
    with pytest.raises(captions.CaptionError): captions.parse_captions(content,fmt)


def test_compare_preserves_both_and_does_not_count_overlaps_twice():
    generated=[dict(id='g1',start_us=1000000,end_us=4000000,raw_text='字幕 文字')]
    source=[dict(id='s1',start_us=1000000,end_us=3000000,raw_text='字幕文字'),dict(id='s2',start_us=2000000,end_us=4000000,raw_text='另一句')]
    before=copy.deepcopy((generated,source))
    result=captions.compare_captions(generated,source,generated_revision='g',source_revision='s')
    assert result['comparisons'][0]['overlap_us']==3000000
    assert result['comparisons'][0]['source_cue_ids']==['s1','s2']
    assert result['automatic_replacement'] is False
    assert (generated,source)==before


def test_empty_source_comparison_is_explicit():
    result=captions.compare_captions([dict(id='a',start_us=0,end_us=1000000,raw_text='字')],[])
    assert result['comparisons'][0]['status']=='source_missing'


def test_acquire_uses_isolated_probe_and_preserves_raw_file(monkeypatch,tmp_path):
    calls=[]
    def run(args,timeout):
        calls.append(args)
        return json.dumps(metadata())
    monkeypatch.setattr(captions,'_run',run)
    monkeypatch.setattr(captions,'_yt_args',lambda:['isolated-python','-m','yt_dlp'])
    monkeypatch.setattr(captions,'_fetch_caption',lambda *args,**kwargs:VTT.encode())
    result=captions.acquire_captions('https://www.youtube.com/live/Yn2mE6_tMC8',tmp_path,language='zh-Hant',ranges=[dict(start_us=6600000000,end_us=6605000000)])
    assert calls[0][0]=='isolated-python'
    assert '--skip-download' in calls[0]
    assert result['cues'][0]['start_us']==6600000000
    assert result['raw_path'] and result['sha256']
    assert result['track']['automatic'] is False
    assert 'url' not in json.dumps(result['track'])


def test_probe_no_caption_does_not_download_media(monkeypatch):
    monkeypatch.setattr(captions,'_run',lambda args,timeout:json.dumps({'id':'Yn2mE6_tMC8','duration':10000}))
    result=captions.probe_captions('https://www.youtube.com/live/Yn2mE6_tMC8')
    assert result['tracks']==[] and result['available'] is False


def test_auto_language_and_malformed_json3_segment():
    assert captions.select_track(captions.caption_tracks(metadata()),language='auto')['language']=='zh-Hant'
    with pytest.raises(captions.CaptionError,match='INVALID_JSON3_SEGMENT'):
        captions.parse_captions('{"events":[{"tStartMs":0,"dDurationMs":2000,"segs":[{"utf8":123}]}]}','json3')


def test_caption_origin_never_accepts_arbitrary_or_local_url():
    for url in ['http://www.youtube.com/api/timedtext','https://evil.test/captions','https://youtube.com.evil.test/x','https://127.0.0.1/x']:
        with pytest.raises(captions.CaptionError,match='CAPTION_DOWNLOAD_ORIGIN_REJECTED'):
            captions._fetch_caption(url)


def test_source_range_selection_never_shifts_original_time():
    result=captions.parse_captions(VTT,'vtt',ranges=[dict(start_us=6602500000,end_us=6603000000)])
    assert [cue['start_us'] for cue in result['cues']]==[6600000000,6602000000]
    with pytest.raises(captions.CaptionError):
        captions.parse_captions(VTT,'vtt',ranges=[dict(start_us=-1,end_us=100)])


def test_interval_lookup_skips_unrelated_captions_and_keeps_long_overlaps():
    source=[dict(id=str(i),start_us=i*100,end_us=i*100+50,raw_text='word') for i in range(4096)]
    source.insert(0,dict(id='long',start_us=0,end_us=500000,raw_text='long'))
    index=captions._interval_index(source)
    found=list(captions._overlapping(index,300000,300050))
    assert [cue['id'] for cue in found]==['long','3000']
    generated=[dict(id='g'+str(i),start_us=i*100,end_us=i*100+50,raw_text='word') for i in range(4096)]
    result=captions.compare_captions(generated,source)
    assert len(result['comparisons'])==4096
    assert result['comparisons'][3000]['source_cue_ids']==['long','3000']
    assert result['unmatched_source_cue_ids']==[]
