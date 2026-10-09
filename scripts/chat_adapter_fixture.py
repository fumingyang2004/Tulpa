"""Keep existing adapter regression cases below the new turn protocol.

These suites intentionally probe unread/forged native targets, media hashes and
adapter races. Planner/queue-to-adapter integration (without this seam) lives in
check_mcp_chat_turns.py. No runtime module imports this fixture.
"""
def use_adapter_layer(tools):
    def single(grant,name,args,cancel):
        if name=='send_chat_message':return tools.chat.send(grant,args,cancel)
        if name=='react_to_chat_message':return tools.chat.reactions.request(grant,args,cancel)
        return tools.chat.media.call(grant,name,args,cancel)[0]
    tools.chat.turns.single=single
