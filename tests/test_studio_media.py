from pathlib import Path
import subprocess
import pytest
from app.studio.media import canonical_youtube_url, ffprobe, cut_media, merge_media, download_youtube


def test_url_restrictions():
    assert canonical_youtube_url('https://youtu.be/Yn2mE6_tMC8?t=6600') == 'https://www.youtube.com/watch?v=Yn2mE6_tMC8'
    for value in ['https://youtube.com.evil.test/watch?v=Yn2mE6_tMC8', 'file:///etc/passwd', 'http://127.0.0.1/', 'https://www.youtube.com/playlist?list=123']:
        with pytest.raises(ValueError): canonical_youtube_url(value)


@pytest.fixture
def media(tmp_path):
    clips=[]
    for index,color in enumerate(['red','blue']):
        path=tmp_path/f'{color}.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=c={color}:s=160x90:r=25:d=3','-f','lavfi','-i',f'sine=frequency={440+index*440}:duration=3','-c:v','libx264','-g','75','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(path)],check=True,timeout=30)
        clips.append(path)
    return clips


def test_accurate_cut_and_merge_order(media,tmp_path):
    a=cut_media(media[0],tmp_path/'a.mp4',500000,1700000,mode='accurate')
    b=cut_media(media[1],tmp_path/'b.mp4',800000,1800000,mode='accurate')
    assert abs(a['duration_us']-1200000)<60000
    result=merge_media([b['path'],a['path']],tmp_path/'merged.mp4',mode='accurate')
    assert abs(result['duration_us']-2200000)<90000
    pixels=subprocess.run(['ffmpeg','-v','error','-i',result['path'],'-vf','scale=1:1','-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],capture_output=True,check=True,timeout=20).stdout
    assert pixels[2]>pixels[0]+100
    assert result['inputs']==[b['path'],a['path']]


def test_copy_discloses_keyframe_expansion(media,tmp_path):
    result=cut_media(media[0],tmp_path/'copy.mp4',1100000,1900000,mode='copy')
    assert result['requested_range']=={'start_us':1100000,'end_us':1900000}
    assert result['actual_range']['start_us']==0
    assert result['warnings']
    assert result['source_map']['status']=='estimated'


def test_bounds_and_no_overwrite(media,tmp_path):
    with pytest.raises(ValueError):cut_media(media[0],tmp_path/'x.mp4',0,4000000)
    with pytest.raises(ValueError):cut_media(media[0],tmp_path/'x.mp4',0.5,1000000)
    with pytest.raises(FileExistsError):cut_media(media[0],media[0],0,1000000)
    with pytest.raises(ValueError):merge_media([],tmp_path/'none.mp4')


def test_audio_cut(media,tmp_path):
    result=cut_media(media[0],tmp_path/'audio.wav',0,1000000,kind='audio',mode='accurate')
    assert all(s['codec_type']=='audio' for s in ffprobe(result['path'])['streams'])
    assert abs(result['duration_us']-1000000)<1000

def test_three_segments_repeat_and_copy_compatibility(media,tmp_path):
    a=cut_media(media[0],tmp_path/'a.mp4',0,1000000)
    b=cut_media(media[1],tmp_path/'b.mp4',0,1000000)
    result=merge_media([b['path'],a['path'],a['path']],tmp_path/'repeat.mp4')
    assert len(result['inputs'])==3
    assert result['inputs'][1]==result['inputs'][2]
    assert abs(result['duration_us']-3000000)<90000
    for seconds,blue in [('0.5',True),('1.5',False),('2.5',False)]:
        pixel=subprocess.run(['ffmpeg','-v','error','-ss',seconds,'-i',result['path'],'-vf','scale=1:1','-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],capture_output=True,check=True,timeout=20).stdout
        assert (pixel[2]>pixel[0])==blue
    audio=cut_media(media[0],tmp_path/'mono.wav',0,1000000,kind='audio')
    with pytest.raises(ValueError):merge_media([a['path'],audio['path']],tmp_path/'bad.mp4',mode='copy')


def test_probe_and_peaks(media):
    from app.studio.media import keyframes,waveform_peaks
    assert keyframes(media[0])==[0]
    result=waveform_peaks(media[0],buckets=100)
    assert len(result['peaks'])<=100
    assert max(result['peaks'])>0.05
    assert abs(result['duration_us']-3000000)<30000


def test_download_validates_before_invocation(tmp_path):
    with pytest.raises(ValueError):download_youtube('file:///etc/passwd',[], 'video',tmp_path)
    with pytest.raises(ValueError):download_youtube('https://youtu.be/Yn2mE6_tMC8',[], 'audio',tmp_path,{'audio_bitrate':'128;whoami'})

def test_subprocess_deadline():
    import sys,time
    from app.studio.media import _run
    started=time.monotonic()
    with pytest.raises(TimeoutError):_run([sys.executable,'-c','import time; time.sleep(20)'],timeout=0.2)
    assert time.monotonic()-started<5

def test_format_policy_rejects_implicit_transcode(tmp_path):
    for policy in [{'container':'mp3'}, {'audio_bitrate_kbps':128}, {'container':'source','audio_bitrate_kbps':128,'allow_transcode':True}, {'max_fps':0}, {'max_height':True}]:
        with pytest.raises(ValueError):download_youtube('https://youtu.be/Yn2mE6_tMC8',[], 'audio',tmp_path,policy)

def test_download_contract_ranges_callbacks_and_policy(media,tmp_path,monkeypatch):
    import shutil
    import app.studio.media as module
    monkeypatch.setenv('STUDIO_MEDIA_HTTP_BRIDGE','0')
    monkeypatch.setattr(module,'probe_youtube',lambda url: {'id':'Yn2mE6_tMC8','url':'https://www.youtube.com/watch?v=Yn2mE6_tMC8','duration_us':10000000,'formats':[{'format_id':'140','acodec':'aac','vcodec':'none'}]})
    actual_run=module._run
    calls=[]
    def adapter(args,timeout=60,binary=False):
        if 'yt_dlp' in args:
            calls.append(args)
            target=Path(args[args.index('-o')+1].replace('%(ext)s','mp4'))
            shutil.copyfile(media[0],target)
            return ''
        return actual_run(args,timeout,binary)
    monkeypatch.setattr(module,'_run',adapter)
    emitted=[]
    result=download_youtube('https://youtu.be/Yn2mE6_tMC8',[{'start_us':0,'end_us':3000000},{'start_us':4000000,'end_us':7000000}], 'video', tmp_path/'downloads', {'container':'mp4','max_height':720,'max_fps':30,'audio_track_id':'140'},on_asset=emitted.append)
    assert len(result)==len(emitted)==len(calls)==2
    assert calls[0][calls[0].index('--download-sections')+1]=='*0.000000-3.000000'
    assert calls[1][calls[1].index('--download-sections')+1]=='*4.000000-7.000000'
    assert '[fps<=30]' in calls[0][calls[0].index('-f')+1]
    assert all(r['source_map']['status']=='estimated' and r['actual_range'] is None for r in result)
    assert result[0]['path']!=result[1]['path']
    with pytest.raises(RuntimeError, match='不完整媒體'):
        download_youtube('https://youtu.be/Yn2mE6_tMC8',[{'start_us':0,'end_us':9000000}], 'video', tmp_path/'truncated', {'container':'mp4'}, boundary_policy='accurate', on_asset=emitted.append)
    assert len(emitted)==2

def test_range_bridge_with_real_ffmpeg_and_controlled_https(media,tmp_path,monkeypatch):
    import io,json
    import urllib.request
    import app.studio.media as module
    from app.studio.domain import TimeRange
    payload=media[0].read_bytes()
    calls=[]
    class Response(io.BytesIO):
        status=206
        def __init__(self,start,end):
            super().__init__(payload[start:end+1])
            self.headers={'Content-Type':'video/mp4','Content-Length':str(end-start+1),'Content-Range':f'bytes {start}-{end}/{len(payload)}'}
    def fake_https(request,timeout):
        assert request.full_url=='https://fixture.googlevideo.com/media'
        start,end=request.headers['Range'][6:].split('-')
        start=int(start);end=min(int(end) if end else len(payload)-1,len(payload)-1)
        calls.append((start,end))
        return Response(start,end)
    monkeypatch.setattr(urllib.request,'urlopen',fake_https)
    actual_run=module._run
    def runner(args,timeout=60,binary=False):
        if 'yt_dlp' in args:return json.dumps({'url':'https://fixture.googlevideo.com/media','format_id':'18','ext':'mp4','vcodec':'h264','acodec':'aac'})
        return actual_run(args,timeout,binary)
    monkeypatch.setattr(module,'_run',runner)
    result=module._download_section_bridge('https://youtu.be/Yn2mE6_tMC8','18',TimeRange(start_us=1000000,end_us=2000000),'video',tmp_path,'mp4','accurate',30)
    assert calls and result['format_ids']==['18']
    assert result['source_total_bytes']['0']==len(payload)
    assert abs(ffprobe(tmp_path/'media.mp4')['duration_us']-1000000)<40000
    assert all('fixture.googlevideo.com' not in x for x in result['command'])


def test_copy_mode_estimates_actual_start_from_duration(media,tmp_path,monkeypatch):
    """2026-09-18 實測：source_seek（copy）下載會從請求起點之前的關鍵影格／片段開始（量到早 9.98 秒），終點精準。
    來源對映必須用「請求終點 − 實際時長」當起點，不能假設從請求起點開始。"""
    import shutil
    import app.studio.media as module
    monkeypatch.setenv('STUDIO_MEDIA_HTTP_BRIDGE','0')
    monkeypatch.setattr(module,'probe_youtube',lambda url: {'id':'Yn2mE6_tMC8','url':'https://www.youtube.com/watch?v=Yn2mE6_tMC8','duration_us':10000000,'formats':[]})
    actual_run=module._run
    def adapter(args,timeout=60,binary=False):
        if 'yt_dlp' in args:
            shutil.copyfile(media[0],Path(args[args.index('-o')+1].replace('%(ext)s','mp4')))  # 3 秒的檔案
            return ''
        return actual_run(args,timeout,binary)
    monkeypatch.setattr(module,'_run',adapter)
    result=download_youtube('https://youtu.be/Yn2mE6_tMC8',[{'start_us':1000000,'end_us':3000000}],'video',tmp_path/'copy',{'container':'mp4'},boundary_policy='source_seek')[0]
    assert abs(result['duration_us']-3000000)<60000
    assert result['actual_range']['end_us']==3000000 and abs(result['actual_range']['start_us']-0)<60000
    assert abs(result['source_map']['source_start_us']-0)<60000 and result['source_map']['status']=='estimated_from_duration'
    assert any('關鍵影格' in w for w in result['warnings'])
