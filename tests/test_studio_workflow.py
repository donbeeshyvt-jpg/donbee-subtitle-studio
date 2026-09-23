"""真 FFmpeg 與受控 worker 的本機垂直整合，不替代真影片與模型驗收。"""
import json
import subprocess
import time
from fastapi.testclient import TestClient
from app.studio.api import create_app


def wait_job(client, job_id, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = client.get(f'/v1/jobs/{job_id}').json()
        if row['status'] in {'succeeded','failed','cancelled','interrupted'}:
            assert row['status'] == 'succeeded', row
            return row
        time.sleep(.1)
    raise AssertionError(f'job {job_id} did not finish')


def test_upload_probe_sequence_media_and_subtitle_export(tmp_path):
    source = tmp_path / 'input.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=160x90:r=10:d=4',
        '-f','lavfi','-i','sine=frequency=440:duration=4','-c:v','libx264','-c:a','aac','-shortest',str(source)],
        check=True, timeout=20)
    app = create_app(tmp_path / 'state')
    with TestClient(app) as client:
        client.get('/v1/session')
        project = client.post('/v1/projects',json={'name':'垂直測試'}).json()['project_id']
        with source.open('rb') as stream:
            uploaded = client.post(f'/v1/projects/{project}/uploads', files={'file':('test.mp4',stream,'video/mp4')})
        assert uploaded.status_code == 201, uploaded.text
        source_id = uploaded.json()['source_id']
        job = wait_job(client, uploaded.json()['job_id'])
        asset_id = job['result']['asset_ids'][0]
        response = client.get(f'/v1/assets/{asset_id}/content', headers={'Range':'bytes=0-31'})
        assert response.status_code == 206 and len(response.content) == 32
        assert client.get(f'/v1/assets/{asset_id}/content', headers={'Range':'bytes=999999999999-'}).status_code == 416
        sequence = client.put(f'/v1/projects/{project}/sequence',json={
            'source_id':source_id,'base_revision':'seq_00','items':[
                {'id':'last','start_us':2000000,'end_us':3000000,'name':'後'},
                {'id':'first','start_us':0,'end_us':1000000,'name':'前'},
                {'id':'repeat','start_us':0,'end_us':1000000,'name':'前再一次'}]}).json()
        transcript = app.state.store.revise(f'transcript:{source_id}:default','transcript',{
            'source_id':source_id,'audio_track_id':'default','cues':[
                {'id':'one','start_us':0,'end_us':800000,'raw_text':'第一句。','accepted_text':'第一句。','lang':'zh','words':[],'alignment_status':'segment'},
                {'id':'two','start_us':2000000,'end_us':2800000,'raw_text':'第二句！','accepted_text':'第二句！','lang':'zh','words':[],'alignment_status':'segment'}],
            'coverage':[{'start_us':0,'end_us':4000000}],'asset_ids':[asset_id]},None,project,initial=None)
        request = {'kind':'export','sequence_revision':sequence['revision'],'transcript_revision':transcript['id'],
            'formats':['mp4','srt'],'grouping':'merge','cut_mode':'accurate','subtitle_timebase':'sequence'}
        response = client.post(f'/v1/projects/{project}/jobs',json=request)
        assert response.status_code == 202, response.text
        export = wait_job(client,response.json()['job_id'])
        artifacts = export['result']['artifacts']
        assert {a['kind'] for a in artifacts} == {'mp4','srt'}
        srt = next(a for a in artifacts if a['kind']=='srt')
        text = client.get(srt['content_url']).text
        assert text.index('第二句') < text.index('第一句')
        assert text.count('第一句') == 2
        assert '。' not in text and '！' not in text
        assert len(export['result']['manifest']['mappings']) == 3
        assert client.get(f'/v1/projects/{project}/sequence').json()['revision'] == sequence['revision']


def test_no_analysis_plan_does_not_start_models(tmp_path):
    source = tmp_path / 'input.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:s=160x90:r=10:d=2',
        '-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-c:a','aac','-shortest',str(source)],
        check=True, timeout=20)
    app = create_app(tmp_path / 'state')
    with TestClient(app) as client:
        client.get('/v1/session')
        p = client.post('/v1/projects',json={'name':'只剪輯'}).json()['project_id']
        with source.open('rb') as stream:
            uploaded = client.post(f'/v1/projects/{p}/uploads',files={'file':('test.mp4',stream,'video/mp4')}).json()
        wait_job(client,uploaded['job_id'])
        body = {'source_id':uploaded['source_id'],'ranges':[{'start_us':0,'end_us':1000000}],
            'acquisition':{'video':'selected'},'analysis':{'mode':'none'},
            'subtitles':{'policy':'none'},'deliverables':{'formats':['mp4'],'grouping':'separate'}}
        plan = client.post(f'/v1/projects/{p}/plans',json=body)
        assert plan.status_code == 201, plan.text
        job = client.post(f"/v1/plans/{plan.json()['plan_id']}/run").json()
        result = wait_job(client,job['job_id'])
        assert len(result['result']['artifacts']) == 1
        jobs = client.get(f'/v1/projects/{p}/jobs').json()['items']
        assert not any(j['kind'] in {'analyze','align','refine','correct','summarize'} for j in jobs)
        assert client.post(f"/v1/plans/{plan.json()['plan_id']}/run").json()['job_id'] == job['job_id']
