"""真 FFmpeg 雙格式輸出，不可悄悄忽略音檔選項。"""
import hashlib
import subprocess
import pytest
from app.studio.store import Store
from app.studio.worker import Worker
from app.studio.media import ffprobe


@pytest.mark.parametrize('grouping', ['merge', 'separate'])
def test_video_and_audio_are_both_exported_in_selected_order(tmp_path, grouping):
    path=tmp_path/'original.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=160x90:r=10:d=3',
        '-f','lavfi','-i','sine=frequency=440:duration=3','-c:v','libx264','-c:a','aac','-shortest',str(path)],
        check=True,timeout=20)
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    store=Store(tmp_path/'state')
    p=store.create('project',{'name':'雙格式'})['id']
    source=store.create('source',{'kind':'local','path':str(path)},p)['id']
    store.create('asset',{'source_id':source,'kind':'video','path':str(path),
        'source_map':[{'id':'map','source_start_us':0,'source_end_us':3000000,
                       'asset_start_us':0,'asset_end_us':3000000,'status':'verified'}]},p)
    seq=store.create('sequence',{'source_id':source,'items':[
        {'id':'last','kind':'clip','start_us':2000000,'end_us':3000000},
        {'id':'first','kind':'clip','start_us':0,'end_us':1000000}]},p)
    transcript=store.create('transcript',{'source_id':source,'cues':[
        {'id':'cue','start_us':2100000,'end_us':2900000,'text':'來源字幕','words':[]}]},p)
    job=store.submit(p,{'kind':'export','sequence_revision':seq['id'], 'transcript_revision':transcript['id'],
        'formats':['mp4','audio','srt'],'subtitle_timebase':'source','grouping':grouping,'cut_mode':'accurate'})
    result=Worker(store,job).run()
    videos=[a for a in result['artifacts'] if a['kind']=='mp4']
    audio=[a for a in result['artifacts'] if a['kind']=='m4a']
    assert len(videos)==len(audio)==(1 if grouping=='merge' else 2)
    caption=next(a for a in result['artifacts'] if a['kind']=='srt')
    assert caption['provenance']['mappings'][0]['output_start_us']==2000000
    assert result['manifest']['mappings'][0]['output_start_us']==0
    assert result['manifest']['subtitle_mappings'][0]['output_start_us']==2000000
    for v,a in zip(videos,audio):
        vp=ffprobe(store.artifact_path(v['id']))
        ap=ffprobe(store.artifact_path(a['id']))
        assert {s['codec_type'] for s in ap['streams']}=={'audio'}
        assert abs(float(vp['format']['duration'])-float(ap['format']['duration']))<0.08
        assert a['provenance']['derived_from_artifact_id']==v['id']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
