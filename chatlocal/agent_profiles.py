"""Server-owned presets shared by the slider and the per-turn Agent config."""
from .config import agent_options


DEFAULT_PROFILE='deep'
_KEYS=('REASONING_EFFORT','MAX_REQUESTS','MAX_TOOL_CALLS','MAX_MESSAGES',
       'MAX_EVIDENCE_CHARS','MAX_REQUEST_CHARS','MAX_SECONDS','MAX_OUTPUT_TOKENS','STREAM_IDLE_SECONDS')
_PRESETS=(
    ('quick','快速','适合找一条消息、确认简单事实。优先较快完成。',
     ('none',6,10,100,24000,60000,150,4096,30)),
    ('standard','标准','适合日常问答，适量展开上下文和补查。',
     ('low',8,18,180,48000,130000,300,8192,60)),
    ('deep','深入','适合梳理人物、事情经过和多处证据。',
     ('high',12,30,300,72000,200000,600,16384,90)),
    ('thorough','充分','适合复杂问题和更多交叉核对，通常更慢、用量更高。',
     ('max',20,50,500,120000,360000,900,32768,120)),
)


def profile_config(config,profile):
    if profile is None:return dict(config)
    if not isinstance(profile,str):raise ValueError('分析强度无效，请重新选择档位。')
    preset=next((p for p in _PRESETS if p[0]==profile),None)
    if preset is None:raise ValueError('分析强度无效，请重新选择档位。')
    # Override budget only. Credentials, model, source scope and history stay intact.
    return dict(config,**{'AGENT_'+key:str(value) for key,value in zip(_KEYS,preset[3])})


def profile_label(profile):
    return next((p[1] for p in _PRESETS if p[0]==profile),'配置默认')


def profile_catalog():
    # No .env values or credentials are exposed by this endpoint.
    return dict(default=DEFAULT_PROFILE,profiles=[dict(id=p[0],label=p[1],description=p[2],
        budget=agent_options(profile_config({},p[0]))) for p in _PRESETS])
