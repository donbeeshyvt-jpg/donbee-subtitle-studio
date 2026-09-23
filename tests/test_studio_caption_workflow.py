"""來源字幕與生成字幕版本獨立；下載邊界使用來源字幕 fixture。"""
from pathlib import Path
import json

import pytest
from fastapi.testclient import TestClient
from app.studio.api import create_app
from app.studio.contracts import WorkflowRequest
from app.studio.store import Store, StudioError
from app.studio.worker import Worker


def fixture(tmp_path, monkeypatch):
    from app.studio import source_subtitles
    s = Store(tmp_path / 'state')
    p = s.create('project', {'name': '來源字幕驗證'})['id']
    source = s.create('source', {'kind': 'youtube', 'url': 'https://youtu.be/Yn2mE6_tMC8'}, p)['id']
    raw = tmp_path / 'original.vtt'
    content = 'WEBVTT\n\n01:50:01.000 --> 01:50:02.000\n原始字幕。\n'
    raw.write_text(content, encoding='utf-8')
    def acquire(url, folder, **kw):
        parsed = source_subtitles.parse_captions(content, 'vtt', 'zh-Hant', source_id=source, ranges=kw.get('ranges'))
        return {**parsed, 'raw_path': str(raw), 'track': {'id':'manual:zh-Hant', 'language':'zh-Hant', 'automatic':False},
                'downloaded_media': False}
    monkeypatch.setattr(source_subtitles, 'acquire_captions', acquire)
    return s, p, source


def test_source_capture_preserves_generated_head_and_original(tmp_path, monkeypatch):
    s, p, source = fixture(tmp_path, monkeypatch)
    generated = s.revise(f'transcript:{source}:default', 'transcript', {'source_id':source,'cues':[]}, None, p, initial=None)
    job = s.submit(p, {'kind':'acquire_subtitles','source_id':source,
        'ranges':[{'start_us':6600000000,'end_us':6660000000}],'source_language':'zh-Hant'}, 'download')
    result = Worker(s, job).run()
    assert result['transcript_revision'] != generated['id']
    assert s.head(f'transcript:{source}:default') == generated['id']
    caption = s.get(result['source_subtitle_revision'], 'source_subtitle')
    assert caption['language'] == 'zh-Hant'
    tr = s.get(result['transcript_revision'], 'transcript')
    assert tr['cues'][0]['start_us'] == 6601000000
    assert not tr['cues'][0]['words']
    assert b'WEBVTT' in s.artifact_path(caption['original_artifact_id']).read_bytes()


@pytest.mark.parametrize('policy', ['source','compare'])
def test_source_policy_plan_is_supported_and_visible(tmp_path, policy):
    app = create_app(tmp_path, start_workers=False)
    with TestClient(app) as c:
        c.get('/v1/session')
        p=c.post('/v1/projects',json={'name':'字幕'}).json()['project_id']
        src=c.post(f'/v1/projects/{p}/sources',json={'kind':'youtube','url':'https://youtu.be/Yn2mE6_tMC8'}).json()['source_id']
        r=c.post(f'/v1/projects/{p}/plans',json={'source_id':src,'analysis':{'mode':'none' if policy=='source' else 'draft'},
            'subtitles':{'policy':policy,'source_language':'zh-Hant','source_kind':'prefer_manual'}})
        assert r.status_code==201, r.text
        assert 'source_subtitles.acquire' in r.json()['dependency_graph']
        assert c.get('/v1/capabilities').json()['source_subtitles']


def test_source_word_alignment_cannot_be_fabricated():
    with pytest.raises(ValueError):
        WorkflowRequest.model_validate({'source_id':'s','analysis':{'mode':'none'},
            'subtitles':{'policy':'source','alignment':'require_word'}})


def test_source_summary_uses_exported_transcript_version(tmp_path, monkeypatch):
    s,p,source=fixture(tmp_path,monkeypatch)
    (s.root/'config.json').write_text('{}')
    generated=s.revise(f'transcript:{source}:default','transcript',{'source_id':source,
        'cues':[{'id':'generated','start_us':6601000000,'end_us':6602000000,
                 'raw_text':'這是另一份辨識稿。','words':[]}]},None,p,initial=None)
    plan=WorkflowRequest.model_validate({'source_id':source,'ranges':[{'start_us':6600000000,'end_us':6660000000}],
        'analysis':{'summary':True},'subtitles':{'policy':'source'},
        'deliverables':{'formats':['srt','summary_md']}})
    stored=s.create('plan',plan.model_dump(),p)
    root=s.submit(p,{'kind':'workflow','plan_id':stored['id']},'workflow')
    class Harness(Worker):
        def child(self,body,resource):
            job=s.submit(p,body,resource)
            if body['kind']=='probe': result={'duration_us':7200000000}
            elif body['kind']=='acquire': result={'asset_ids':[],'child_job_ids':[]}
            else: result=Worker(s,job).run()
            s.finish(job['id'],'succeeded',result)
            return job['id']
    result=Harness(s,root).workflow()
    summary=next(a for a in result['artifacts'] if a['kind']=='summary_md')
    assert summary['provenance']['transcript_revision']==result['transcript_revision']
    assert result['transcript_revision']!=generated['id']
    assert '原始字幕' in s.artifact_path(summary['id']).read_text(encoding='utf-8')


def test_source_only_workflow_does_not_download_audio_or_start_model(tmp_path, monkeypatch):
    s, p, source = fixture(tmp_path, monkeypatch)
    plan = WorkflowRequest.model_validate({'source_id':source, 'ranges':[{'start_us':6600000000,'end_us':6660000000}],
        'analysis':{'mode':'none'}, 'subtitles':{'policy':'source'}})
    stored = s.create('plan', plan.model_dump(), p)
    root = s.submit(p, {'kind':'workflow','plan_id':stored['id']}, 'workflow')
    calls=[]
    class Harness(Worker):
        def child(self, body, resource):
            calls.append(body['kind'])
            assert body['kind'] in {'probe', 'acquire_subtitles', 'export'}, '來源字幕不應取得影音或啟動模型'
            job=self.store.submit(p, body, resource)
            result={'duration_us':7200000000} if body['kind']=='probe' else Worker(s, job).run()
            self.store.finish(job['id'], 'succeeded', result)
            return job['id']
    result=Harness(s, root).workflow()
    assert set(calls)=={'probe','acquire_subtitles','export'}
    assert result['source_subtitle_revision']
    subtitle=next(a for a in result['artifacts'] if a['kind']=='srt')
    assert '00:00:01,000' in s.artifact_path(subtitle['id']).read_text(encoding='utf-8')


@pytest.mark.parametrize('save_files', [False, True])
def test_compare_exports_both_versions_without_replacing_generated(tmp_path, monkeypatch, save_files):
    s, p, source = fixture(tmp_path, monkeypatch)
    generated = s.revise(f'transcript:{source}:default', 'transcript', {'source_id':source, 'audio_track_id':'default',
        'cues':[{'id':'generated', 'start_us':6601000000, 'end_us':6602000000,'raw_text':'生成不同字幕。', 'words':[]}]},
        None, p, initial=None)
    output=tmp_path/'output'
    output.mkdir()
    (s.root/'config.json').write_text(json.dumps({'roots':{'out':str(output)}}))
    plan=WorkflowRequest.model_validate({'source_id':source,'ranges':[{'start_us':6600000000,'end_us':6660000000}],
        'subtitles':{'policy':'compare'},'analysis':{'music_policy':'off'},
        'deliverables':{'output_root_id':'out' if save_files else None}})
    stored=s.create('plan',plan.model_dump(),p)
    root=s.submit(p,{'kind':'workflow','plan_id':stored['id']},'workflow')
    class Harness(Worker):
        def child(self,body,resource):
            job=s.submit(p,body,resource)
            if body['kind']=='probe': result={'duration_us':7200000000}
            elif body['kind']=='acquire': result={'asset_ids':[],'child_job_ids':[]}
            else: result=Worker(s,job).run()
            s.finish(job['id'],'succeeded',result)
            return job['id']
    result=Harness(s,root).workflow()
    assert s.head(f'transcript:{source}:default')==generated['id']
    comparison=s.get(result['comparison_revision'],'subtitle_comparison')
    assert comparison['generated_revision']==generated['id']
    assert comparison['comparisons'][0]['status']=='different'
    outputs=[a for a in result['artifacts'] if a['kind']=='srt']
    assert len(outputs)==2
    assert '生成不同字幕' in s.artifact_path(outputs[0]['id']).read_text(encoding='utf-8')
    assert '原始字幕' in s.artifact_path(outputs[1]['id']).read_text(encoding='utf-8')
    assert outputs[1]['subtitle_origin']=='source'
    manifest=json.loads(s.artifact_path(result['manifest_artifact']['id']).read_text(encoding='utf-8'))
    assert {a['id'] for a in manifest['outputs']}=={a['id'] for a in result['artifacts']}
    assert manifest['source_subtitle_revision']==result['source_subtitle_revision']
    assert manifest['comparison_revision']==comparison['id']
    assert len(manifest['export_manifests'])==2
    if save_files:
        saved_ids={item['artifact_id'] for item in result['saved_files']}
        assert {a['id'] for a in result['artifacts']} <= saved_ids
        assert result['manifest_artifact']['id'] in saved_ids
        for item in result['saved_files']:
            assert Path(item['path']).read_bytes()==s.artifact_path(item['artifact_id']).read_bytes()
        # 2026-09-19：一律平放在 subtitle_studio；生成字幕與來源字幕不同名，不互相覆蓋
        names=sorted(Path(item['path']).name for item in result['saved_files'])
        assert len(set(names))==len(names)
        assert all(Path(item['path']).parent==output.resolve()/'subtitle_studio' for item in result['saved_files'])
        assert any(name.endswith('_來源字幕.srt') for name in names) and any(name.endswith('.workflow_manifest.json') for name in names)
