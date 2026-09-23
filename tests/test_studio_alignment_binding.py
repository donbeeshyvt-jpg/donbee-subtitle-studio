"""對齊結果必須綁定實際音訊與來源時間對映。"""
import hashlib
from unittest.mock import Mock
import pytest
from app.studio.store import StudioError
from app.studio.alignment_binding import validate_binding
from app.studio.store import Store
from app.studio.worker import Worker

def binding(tmp_path):
    path = tmp_path / 'audio.wav'
    path.write_bytes(b'original')
    asset = {'id': 'a', 'source_id': 's', 'path': str(path), 'map_revision': 'm',
             'content_hash': hashlib.sha256(b'original').hexdigest()}
    transcript = {'id': 't', 'source_id': 's', 'audio_track_id': 'track', 'asset_ids': ['a']}
    alignment = {'transcript_revision': 't', 'source_id': 's', 'audio_track_id': 'track',
                 'asset_ids': ['a'], 'map_revisions': ['m'], 'content_hashes': [asset['content_hash']]}
    return transcript, alignment, asset, path

def test_valid_binding(tmp_path):
    tr, alignment, asset, _ = binding(tmp_path)
    validate_binding(tr, alignment, [asset])

@pytest.mark.parametrize('field,value', [('source_id','other'), ('audio_track_id','other'),
    ('asset_ids',['other']), ('map_revisions',['other']), ('content_hashes',None), ('transcript_revision','old')])
def test_rejects_stale_binding(tmp_path, field, value):
    tr, alignment, asset, _ = binding(tmp_path)
    alignment[field] = value
    with pytest.raises(StudioError, match='重新對齊'):
        validate_binding(tr, alignment, [asset])

def test_rejects_replaced_audio(tmp_path):
    tr, alignment, asset, path = binding(tmp_path)
    path.write_bytes(b'replaced')
    with pytest.raises(StudioError, match='重新對齊'):
        validate_binding(tr, alignment, [asset])

@pytest.mark.parametrize('replaced', [True, False])
def test_worker_checks_audio_before_loading_alignment_model(tmp_path, monkeypatch, replaced):
    _, _, data, path = binding(tmp_path)
    store = Store(tmp_path / 'state')
    project = store.create('project', {'name': '對齊驗收'})['id']
    source = store.create('source', {'kind': 'local'}, project)['id']
    data.pop('id')
    asset = store.create('asset', {**data, 'source_id': source,
        'source_map': [{'source_start_us': 0, 'source_end_us': 1000000}]}, project)
    cues = [{'id': 'cue', 'start_us': 0, 'end_us': 900000, 'text': '測試', 'words': []}]
    tr = store.create('transcript', {'source_id': source, 'audio_track_id': 'track',
        'asset_ids': [asset['id']], 'cues': cues}, project)
    job = store.submit(project, {'kind': 'align', 'transcript_revision': tr['id']})
    model = Mock(return_value={'cues': cues})
    monkeypatch.setattr('app.studio.asr.align', model)
    if replaced:
        path.write_bytes(b'replaced')
        with pytest.raises(StudioError):
            Worker(store, job).refine_align()
        model.assert_not_called()
    else:
        result = Worker(store, job).refine_align()
        alignment = store.get(result['alignment_revision'], 'alignment')
        assert alignment['content_hashes'] == [asset['content_hash']]
        model.assert_called_once()
