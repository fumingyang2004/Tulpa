"""Bounded client-read options, independent of Agent/search filters."""

READ_LIMITS={
    'qq_days':dict(default=30,min=1,max=3650,label='QQ 历史天数'),
    'qq_per_chat':dict(default=500,min=1,max=10000,label='QQ 每会话条数'),
    'wechat_per_chat':dict(default=500,min=1,max=10000,label='微信每会话条数'),
}


def read_options(qq_days=30,qq_per_chat=500,wechat_per_chat=500):
    values=dict(qq_days=qq_days,qq_per_chat=qq_per_chat,wechat_per_chat=wechat_per_chat)
    result={}
    for key,rule in READ_LIMITS.items():
        value=values[key]
        try:
            if isinstance(value,bool) or value is None:raise ValueError()
            if isinstance(value,float):
                if not value.is_integer():raise ValueError()
                value=int(value)
            parsed=int(str(value))
            if not rule['min']<=parsed<=rule['max']:raise ValueError()
        except (ValueError,TypeError,OverflowError):
            raise ValueError(f'{rule["label"]}须为 {rule["min"]}–{rule["max"]} 之间的整数。') from None
        result[key]=parsed
    return result


def read_description(options):
    return (f'QQ：最近 {options["qq_days"]} 天，每会话最多 {options["qq_per_chat"]} 条文本，'
            f'另取最多 {options["qq_per_chat"]} 条支持的非文本消息；'
            f'微信：每会话最近 {options["wechat_per_chat"]} 条原始消息（含不支持的类型）。')
