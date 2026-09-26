"""Small, inert file descriptors; no download and no content parsing on import."""
import re
from pathlib import PureWindowsPath
from xml.etree import ElementTree as ET


def clean_descriptor(raw):
    if not isinstance(raw, dict):
        return None
    name = str(raw.get('filename') or '').replace('\x00', '')
    name = PureWindowsPath(name).name[:255]
    if not name or name in ('.', '..'):
        return None
    item = {'filename': name, 'extension': PureWindowsPath(name).suffix.lower().lstrip('.')}
    for key in ('local_path', 'resource_id', 'file_id', 'busid'):
        if isinstance(raw.get(key), (str, int)):
            item[key] = str(raw[key])[:2048]
    size = raw.get('size', raw.get('total_bytes'))
    if isinstance(size, int) and 0 <= size < 2**63:
        item['size'] = size
    md5 = str(raw.get('md5') or '').lower()
    if re.fullmatch('[a-f0-9]{32}', md5):
        item['md5'] = md5
    return item


def wechat_file(content):
    if not isinstance(content, str) or len(content) > 1024 * 1024:
        return None
    # File-message XML is data, not a source of URLs/keys for the model.
    if '<!DOCTYPE' in content.upper() or '<!ENTITY' in content.upper():
        return None
    try:
        root = ET.fromstring(content[content.index('<'):])
    except (ValueError, ET.ParseError):
        return None
    app = root if root.tag == 'appmsg' else root.find('.//appmsg')
    if app is None or app.findtext('type') != '6':
        return None
    size = app.findtext('appattach/totallen', '')
    return clean_descriptor(dict(filename=app.findtext('title', ''),
        size=int(size) if size.isdigit() else None,
        md5=app.findtext('appattach/filemd5', '') or app.findtext('md5', ''),
        resource_id=app.findtext('appattach/attachid', '')))


def qq_file(q, blob):
    value = q._extract_qq_attachment_meta(blob)
    if not value or value.get('kind') != 'file':
        return None
    item = clean_descriptor(value)
    if not item:
        return None
    fields = q._proto_parse(blob)
    for typ, data in fields.get(q._NTQQ_MSG_OUTER_WRAPPER, []):
        if typ == 'bytes':
            fields = q._proto_parse(data)
            break
    for field in (q._NTQQ_MSG_FILE_MD5_BIN, q._NTQQ_MSG_FILE_MD5_ALT):
        for typ, data in fields.get(field, []):
            if isinstance(data, bytes) and len(data) == 16:
                item['md5'] = data.hex()
    return item
