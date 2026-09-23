"""驗證逐詞時間碼使用的不可變音訊與時間對映。"""
import hashlib
from .store import StudioError


def validate_binding(transcript, alignment, assets):
    expected = {
        'transcript_revision': transcript['id'],
        'source_id': transcript['source_id'],
        'audio_track_id': transcript.get('audio_track_id'),
        'asset_ids': transcript.get('asset_ids', []),
        'map_revisions': [a.get('map_revision') for a in assets],
        'content_hashes': [a.get('content_hash') for a in assets],
    }
    if (not assets or [a['id'] for a in assets] != expected['asset_ids']
            or any(a.get('source_id') != transcript['source_id'] for a in assets)
            or any(not a.get('content_hash') or not a.get('map_revision') for a in assets)
            or any(alignment.get(key) != value for key, value in expected.items())):
        raise StudioError('ALIGNMENT_REQUIRED', '音訊或時間對映與對齊版本不一致，請重新對齊', 409)
    validate_audio(assets)


def validate_audio(assets):
    for asset in assets:
        try:
            with open(asset['path'], 'rb') as stream:
                matches = hashlib.file_digest(stream, 'sha256').hexdigest() == asset.get('content_hash')
        except OSError:
            matches = False
        if not matches:
            raise StudioError('ALIGNMENT_REQUIRED', '對齊音訊已變更或無法讀取，請重新對齊', 409)
