"""Shared local reader preferences for history, refresh and realtime work."""
import json


def wechat_images_enabled(store):
    with store.connect() as db:
        row = db.execute("SELECT value FROM client_settings WHERE name='live_ingestion'").fetchone()
    value = json.loads(row[0]).get('wechat_read_images', True) if row else True
    if type(value) is not bool:
        raise ValueError('微信图片读取设置无效，请重新保存实时设置。')
    return value
