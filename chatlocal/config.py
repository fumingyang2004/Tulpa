import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
DB = DATA / 'chats.sqlite3'
for folder in (DATA, ROOT / '.tmp', ROOT / '.cache', ROOT / 'imports'):
    folder.mkdir(exist_ok=True)
os.environ['GRADIO_ANALYTICS_ENABLED'] = 'False'
os.environ['GRADIO_TEMP_DIR'] = str(ROOT / '.tmp' / 'gradio')
os.environ['HF_HOME'] = str(ROOT / '.cache' / 'huggingface')
os.environ['TEMP'] = str(ROOT / '.tmp')
os.environ['TMP'] = str(ROOT / '.tmp')


# Bounds catch typos without silently changing the requested budget.
AGENT_LIMITS = {
    'AGENT_MAX_REQUESTS': (12, 2, 40),
    'AGENT_MAX_TOOL_CALLS': (30, 1, 100),
    'AGENT_MAX_MESSAGES': (300, 1, 1000),
    'AGENT_MAX_EVIDENCE_CHARS': (72000, 1000, 240000),
    'AGENT_MAX_REQUEST_CHARS': (200000, 16000, 600000),
    'AGENT_MAX_SECONDS': (600, 30, 1800),
    'AGENT_MAX_OUTPUT_TOKENS': (16384, 1024, 65536),
    'AGENT_STREAM_IDLE_SECONDS': (90, 20, 300),
}


def settings():
    from dotenv import dotenv_values
    values = dotenv_values(ROOT / '.env')
    return {key: os.environ.get(key, values.get(key) or default).strip()
            for key, default in [('API_BASE', 'https://api.deepseek.com'),
                                 ('API_KEY', ''), ('MODEL', 'deepseek-flash'),
                                 ('AGENT_REASONING_EFFORT', 'high'),
                                 *((k,str(v[0])) for k,v in AGENT_LIMITS.items())]}


def agent_options(config):
    result={}
    for key,(default,low,high) in AGENT_LIMITS.items():
        value=config.get(key,default)
        try:
            if isinstance(value,bool): raise ValueError()
            parsed=int(str(value))
            if not low<=parsed<=high: raise ValueError()
        except (ValueError,TypeError):
            raise ValueError(f'{key} 必须是 {low}–{high} 之间的整数。') from None
        result[key.removeprefix('AGENT_').lower()]=parsed
    effort=str(config.get('AGENT_REASONING_EFFORT','high')).strip().lower()
    if effort not in ('none','low','high','max'):
        raise ValueError('AGENT_REASONING_EFFORT 必须为 none、low、high 或 max。')
    result['reasoning_effort']=effort
    if result['max_request_chars']<result['max_evidence_chars']+16000:
        raise ValueError('AGENT_MAX_REQUEST_CHARS 至少比 AGENT_MAX_EVIDENCE_CHARS 多 16000，给工具说明及历史留出空间。')
    return result


def local_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('请先把导出文件放入程序目录下的 imports 文件夹；仅访问当前工作目录内的导入文件。')
    return path
