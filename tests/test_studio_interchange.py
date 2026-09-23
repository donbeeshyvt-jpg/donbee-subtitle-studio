import pytest

from app.studio.interchange import to_llc, from_llc, to_csv, from_csv


ITEMS = [dict(id='s1',kind='clip',start_us=1200000,end_us=3400000,name='含,逗號',selected=True,tags={'topic':'測試'}),
         dict(id='m1',kind='marker',start_us=5000000,name='標記',selected=False,tags={})]


def test_llc_roundtrip_and_media_identity():
    encoded = to_llc(ITEMS,'source.mp4')
    result = from_llc(encoded,expected_media='source.mp4')
    assert result == ITEMS
    with pytest.raises(ValueError):
        from_llc(encoded,expected_media='wrong.mp4')


def test_csv_roundtrip_marker_and_quoting():
    result = from_csv(to_csv(ITEMS))
    assert [(x['start_us'],x.get('end_us'),x['name'],x['kind']) for x in result] == [(1200000,3400000,'含,逗號','clip'),(5000000,None,'標記','marker')]


@pytest.mark.parametrize('text', ['{"version":1,"cutSegments":[]}', '{"version":2,"cutSegments":[{"start":-1,"name":"x"}]}', '{"version":2,"cutSegments":[{"start":1,"end":0,"name":"x"}]}'])
def test_llc_rejects_invalid(text):
    with pytest.raises(ValueError):
        from_llc(text)


@pytest.mark.parametrize('text', ['1,2,name,extra', '-1,2,bad', 'NaN,2,bad','2,1,bad'])
def test_csv_rejects_invalid(text):
    with pytest.raises(ValueError):
        from_csv(text)


def test_export_offsets_reference_actual_media():
    encoded = to_llc([dict(ITEMS[0], start_us=6601200000,end_us=6603400000)], 'clip.mp4', source_offset_us=6600000000)
    assert from_llc(encoded,source_offset_us=6600000000)[0]['start_us']==6601200000
