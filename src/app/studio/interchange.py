"""LLC v2 與三欄 CSV 交換；座標相對指定媒體，不是完整專案格式。"""

import csv
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import io
import json


MAX_US = 9007199254740991


def _us(value):
    if isinstance(value,bool):
        raise ValueError('INVALID_TIME')
    try:
        number = Decimal(str(value))*1000000
        if not number.is_finite() or number < 0 or number > MAX_US:
            raise ValueError('INVALID_TIME')
        return int(number.to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation,TypeError) as error:
        raise ValueError('INVALID_TIME') from error


def _relative(value, offset):
    if isinstance(value,bool) or not isinstance(value,int) or not isinstance(offset,int) or value < offset or value > MAX_US or offset < 0:
        raise ValueError('INVALID_MEDIA_MAPPING')
    return value-offset


def _segment(row, index, offset=0):
    if not isinstance(row,dict) or 'start' not in row or not isinstance(row.get('name'),str):
        raise ValueError('INVALID_SEGMENT')
    start = _us(row['start'])+offset
    end = _us(row['end'])+offset if row.get('end') is not None else None
    if start > MAX_US or (end is not None and (end <= start or end > MAX_US)):
        raise ValueError('INVALID_TIME_RANGE')
    tags = dict(row.get('tags') or {})
    if not all(isinstance(k,str) and isinstance(v,str) for k,v in tags.items()):
        raise ValueError('INVALID_TAGS')
    selected = row.get('selected',True)
    if not isinstance(selected,bool):
        raise ValueError('INVALID_SELECTED')
    identity = tags.pop('studio_segment_id',f'import_{index}')
    if not identity:
        raise ValueError('INVALID_SEGMENT_ID')
    item = dict(id=identity,kind='marker' if end is None else 'clip',start_us=start,name=row['name'],selected=selected,tags=tags)
    if end is not None:
        item['end_us'] = end
    return item


def _export_row(item, offset):
    start = _relative(item['start_us'],offset)
    row = dict(start=start/1000000,name=item.get('name',''))
    if item.get('kind','clip') != 'marker':
        end = _relative(item['end_us'],offset)
        if end <= start:
            raise ValueError('INVALID_TIME_RANGE')
        row['end'] = end/1000000
    return row


def to_llc(items, media_file_name, source_offset_us=0):
    if not isinstance(media_file_name,str) or not media_file_name:
        raise ValueError('MEDIA_NAME_REQUIRED')
    rows = []
    for item in items:
        row = _export_row(item,source_offset_us)
        row['selected'] = item.get('selected',True)
        row['tags'] = dict(item.get('tags') or {},studio_segment_id=item['id'])
        _segment(row,len(rows))
        rows.append(row)
    return json.dumps(dict(version=2,mediaFileName=media_file_name,cutSegments=rows),ensure_ascii=False,indent=2)


def from_llc(text, expected_media=None, source_offset_us=0):
    if isinstance(source_offset_us,bool) or not isinstance(source_offset_us,int) or not 0 <= source_offset_us <= MAX_US:
        raise ValueError('INVALID_MEDIA_MAPPING')
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import json5
        except ImportError as error:
            raise ValueError('JSON5_PARSER_UNAVAILABLE') from error
        data = json5.loads(text)
    if not isinstance(data,dict) or type(data.get('version')) is not int or data['version'] != 2:
        raise ValueError('UNSUPPORTED_LLC_VERSION')
    if expected_media is not None and data.get('mediaFileName') != expected_media:
        raise ValueError('MEDIA_MAPPING_MISMATCH')
    if not isinstance(data.get('cutSegments'),list):
        raise ValueError('INVALID_SEGMENTS')
    items = [_segment(row,i,source_offset_us) for i,row in enumerate(data['cutSegments'])]
    if len({item['id'] for item in items}) != len(items):
        raise ValueError('DUPLICATE_SEGMENT_ID')
    return items


def to_csv(items, source_offset_us=0):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream,lineterminator='\n')
    for item in items:
        # Decimal 避免 CSV 文字經浮點中介喪失微秒。
        _export_row(item,source_offset_us)
        start = Decimal(item['start_us']-source_offset_us)/1000000
        end = '' if item.get('kind')=='marker' else Decimal(item['end_us']-source_offset_us)/1000000
        writer.writerow([str(start),str(end),item.get('name','')])
    return stream.getvalue()


def from_csv(text, source_offset_us=0):
    if isinstance(source_offset_us,bool) or not isinstance(source_offset_us,int) or not 0 <= source_offset_us <= MAX_US:
        raise ValueError('INVALID_MEDIA_MAPPING')
    result = []
    for row in csv.reader(io.StringIO(text,newline=''),strict=True):
        if not row:
            continue
        if len(row)!=3:
            raise ValueError('CSV_REQUIRES_THREE_COLUMNS')
        result.append(_segment(dict(start=row[0],end=row[1] or None,name=row[2]),len(result),source_offset_us))
    return result
