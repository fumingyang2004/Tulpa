"""Local-only presentation for the desktop chat interface."""
import gradio as gr


THEME = gr.themes.Base(
    primary_hue='neutral', secondary_hue='neutral', neutral_hue='neutral',
    font=['Segoe UI', 'Microsoft YaHei UI', 'system-ui', 'sans-serif'],
    font_mono=['Consolas', 'monospace'], radius_size='lg',
).set(
    body_background_fill='#ffffff', body_background_fill_dark='#212121',
    background_fill_primary='#ffffff', background_fill_primary_dark='#212121',
    background_fill_secondary='#f7f7f8', background_fill_secondary_dark='#181818',
    body_text_color='#242424', body_text_color_dark='#ececec',
    body_text_color_subdued='#737373', body_text_color_subdued_dark='#a6a6a6',
    block_background_fill='transparent', block_background_fill_dark='transparent',
    block_border_width='0px', block_shadow='none',
    border_color_primary='#e8e8e8', border_color_primary_dark='#3b3b3b',
    input_background_fill='#ffffff', input_background_fill_dark='#252525',
    input_border_color='#e3e3e3', input_border_color_dark='#444444',
    input_border_color_focus='#888888', input_border_color_focus_dark='#888888',
    input_shadow='none', input_shadow_focus='none',
    button_primary_background_fill='#252525', button_primary_background_fill_dark='#eeeeee',
    button_primary_background_fill_hover='#414141', button_primary_background_fill_hover_dark='#d1d1d1',
    button_primary_text_color='#ffffff', button_primary_text_color_dark='#171717',
    button_primary_border_color='transparent', button_primary_border_color_dark='transparent',
    button_secondary_background_fill='transparent', button_secondary_background_fill_dark='transparent',
    button_secondary_background_fill_hover='#ededed', button_secondary_background_fill_hover_dark='#303030',
    button_secondary_border_color='#e5e5e5', button_secondary_border_color_dark='#404040',
    button_secondary_text_color='#444444', button_secondary_text_color_dark='#dddddd',
    button_large_radius='16px', button_small_radius='12px', chatbot_text_size='16px',
)

WELCOME = '''<div class="welcome">
<div class="welcome-mark" aria-hidden="true">聊</div>
<h1>从聊天里，找回线索。</h1>
<p>问问最近的安排，或接着上次的话题。<br>QQ 与微信里的原话，都有迹可循。</p>
</div>'''

CSS = r'''
html, body { margin: 0; overscroll-behavior: none; }
.gradio-container { max-width: none !important; padding: 0 !important; }
.gradio-container .main { padding: 0 !important; }
.gradio-container button, .gradio-container input, .gradio-container textarea { font-family: inherit; }
.gradio-container button:focus-visible, .gradio-container summary:focus-visible {
    outline: 2px solid var(--body-text-color); outline-offset: 3px;
}
#chat-sidebar { box-shadow: none; border-right: 1px solid var(--border-color-primary); }
#chat-sidebar .sidebar-content { padding: 24px 16px 16px; height: 100%; gap: 18px; }
#chat-sidebar .toggle-button { top: 17px; box-shadow: none; }
.sidebar-brand { display: flex; align-items: center; gap: 10px; padding: 0 7px 12px; font-size: 16px; font-weight: 650; }
.brand-mark { width: 28px; height: 28px; border: 1px solid var(--border-color-primary); border-radius: 9px; display: grid; place-items: center; font-weight: 500; }
#new-chat { justify-content: flex-start; padding: 10px 14px; font-size: 14px; min-height: 42px; }
#session-list { padding: 0; }
#session-list > label { font-size: 12px; color: var(--body-text-color-subdued); margin: 12px 8px 8px; }
#session-list .wrap { display: flex; flex-direction: column; gap: 3px; max-height: 32dvh; overflow-y: auto; flex-wrap: nowrap; }
#session-list label:not(:first-child), #session-list .wrap label { width: 100%; min-width: 0; }
#session-list .wrap label { margin: 0; padding: 10px; border: 0; border-radius: 9px; background: transparent; box-shadow: none; }
#session-list .wrap label:hover, #session-list .wrap label:has(input:checked) { background: var(--button-secondary-background-fill-hover); }
#session-list .wrap label:has(input:focus-visible) { outline: 2px solid var(--body-text-color); outline-offset: -2px; }
#session-list input { position: absolute; width: 1px; height: 1px; opacity: 0; }
#session-list .wrap span { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 208px; font-size: 13px; }
.sidebar-section { border-top: 1px solid var(--border-color-primary) !important; border-radius: 0 !important; background: transparent !important; }
.sidebar-section > button { padding: 14px 6px !important; }
#chat-sidebar .block { min-width: 0 !important; }
#chat-sidebar input, #chat-sidebar textarea { font-size: 13px; }
#chat-sidebar .prose, #data-status { font-size: 12px; line-height: 1.65; color: var(--body-text-color-subdued); }
.data-card { margin-bottom: 14px; }
.data-card strong { display: block; color: var(--body-text-color); font-size: 13px; }
.data-card small { display: block; margin-top: 4px; }
.sidebar-note { color: var(--body-text-color-subdued); font-size: 11px; padding: 6px; }
#chat-workspace { height: 100dvh; min-height: 420px; gap: 0; padding: 0 28px; overflow: hidden; }
#chat-topbar { flex: 0 0 auto; min-height: 66px; }
.chat-topbar-inner { display: flex; align-items: center; justify-content: space-between; height: 66px; padding-left: 32px; }
.chat-title { font-size: 17px; font-weight: 600; letter-spacing: -.3px; }
.chat-title span { color: var(--body-text-color-subdued); font-size: 12px; font-weight: 400; margin-left: 10px; }
.local-badge { font-size: 12px; color: var(--body-text-color-subdued); white-space: nowrap; }
.local-badge::before { content: ''; width: 6px; height: 6px; background: #639587; border-radius: 50%; display: inline-block; margin-right: 7px; }
#chat-thread { width: 100%; max-width: 840px; margin: 0 auto; border: 0; background: transparent; flex: 1 1 0% !important; min-height: 120px !important; box-shadow: none; border-radius: 0; }
#chat-thread .bubble-wrap, #chat-thread .panel-wrap { background: transparent; scrollbar-width: thin; }
#chat-thread .message-wrap { padding: 0 12px; }
#chat-thread .message-row.bubble { margin: 14px 0; max-width: 100%; }
#chat-thread .message-row.bubble.user-row { max-width: 85%; }
#chat-thread .message-row .user { border: none; border-radius: 22px; padding: 12px 20px; background: var(--background-fill-secondary); box-shadow: none; }
#chat-thread .message-row .bot { border: none; background: transparent; padding: 8px 2px; box-shadow: none; }
#chat-thread .message-wrap .prose.chatbot { opacity: 1; line-height: 1.85; }
#chat-thread .message { overflow-wrap: anywhere; }
#chat-thread .message-buttons { opacity: .65; }
.welcome { text-align: center; padding: 24px 16px; }
.welcome-mark { margin: 0 auto 22px; display: grid; place-items: center; width: 48px; height: 48px; border: 1px solid var(--border-color-primary); border-radius: 16px; font-size: 22px; }
#chat-thread .welcome h1 { color: var(--body-text-color); font-size: clamp(25px, 3vw, 34px); font-weight: 600; letter-spacing: -1px; margin: 0 0 16px; line-height: 1.4; }
#chat-thread .welcome p { color: var(--body-text-color-subdued); font-size: 14px; line-height: 1.9; margin: 0; }
.chat-answer .claim-text { white-space: pre-wrap; margin: 8px 0; }
.chat-answer .claim { margin-bottom: 20px; }
.chat-answer .citation-group { font-size: 13px; color: var(--body-text-color-subdued); margin: 4px 0 10px; }
.chat-answer summary { cursor: pointer; overflow-wrap: anywhere; }
.chat-answer .citation-group > summary { display: list-item; width: fit-content; border: 1px solid var(--border-color-primary); border-radius: 9px; padding: 2px 10px; list-style-position: inside; }
.chat-answer .citation-group[open] > summary { margin-bottom: 12px; }
.chat-answer .citation-source { margin: 6px 0; padding: 8px 12px; border-left: 2px solid var(--border-color-primary); }
.chat-answer .source-message { color: var(--body-text-color); font-size: 13px; line-height: 1.7; }
.chat-answer pre { padding: 0; background: transparent; border: none; }
.answer-note { color: var(--body-text-color-subdued); font-size: 12px; line-height: 1.7; }
.user-text { white-space: pre-wrap; }
.pending-answer { color: var(--body-text-color-subdued); font-size: 14px; }
#quick-prompts { max-width: 780px; margin: 0 auto 18px; gap: 10px; flex-wrap: wrap; flex: 0 0 auto; }
#quick-prompts button { font-size: 13px; min-width: 140px; padding: 12px 14px; font-weight: 400; }
#chat-workspace:has(#chat-thread .message) #quick-prompts { display: none; }
#composer-area { width: 100%; max-width: 800px; margin: 0 auto; flex: 0 0 auto; gap: 8px; padding: 0 8px; }
#composer-context { display: flex; align-items: center; flex-wrap: nowrap; gap: 8px; min-height: 26px; }
#range-summary { color: var(--body-text-color-subdued); font-size: 12px; min-width: 0; }
#range-summary p { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin: 0; }
#live-status { min-height: 0; }
.live-status { font-size: 12px; line-height: 1.6; display: flex; align-items: baseline; gap: 8px; color: var(--body-text-color-subdued); }
.live-status:empty { display: none; }
.status-dot { flex-shrink: 0; width: 6px; height: 6px; background: currentColor; border-radius: 50%; }
.live-status.busy .status-dot { animation: status-pulse 1.4s ease-in-out infinite; }
@keyframes status-pulse { 50% { opacity: .3; } }
#composer { border: 1px solid var(--border-color-primary); border-radius: 26px; background: var(--background-fill-secondary); padding: 10px 14px 10px; box-shadow: 0 3px 16px #00000005; }
#composer:focus-within { border-color: var(--input-border-color-focus); }
#question-box, #question-box > div, #question-box label { background: transparent; border: none; padding: 0; box-shadow: none; }
#question-box textarea { background: transparent; border: 0; outline: none !important; box-shadow: none !important; padding: 10px 8px; font-size: 16px; line-height: 1.6; resize: none; min-height: 48px; }
#composer-actions { gap: 8px; align-items: center; flex-wrap: nowrap; }
#composer-hint { color: var(--body-text-color-subdued); font-size: 11px; padding-left: 8px; min-width: 0; }
#send-button, #stop-button { border-radius: 20px; min-height: 36px; height: 36px; padding: 0 17px; min-width: 70px !important; flex: 0 0 auto !important; font-size: 13px; }
#privacy-note { color: var(--body-text-color-subdued); font-size: 11px; text-align: center; padding: 2px 0 12px; }
#turn-details { margin: 0; max-height: 38dvh; overflow-y: auto; border: 1px solid var(--border-color-primary); border-radius: 14px; background: var(--body-background-fill); }
#turn-details > button { padding: 8px 12px; font-size: 12px; }
#turn-details .tabitem { padding: 12px; }
#turn-details .prose, #turn-details textarea { font-size: 13px; line-height: 1.65; }
#turn-details details { margin: 8px 0; padding: 6px 0; }
#turn-details summary { cursor: pointer; }
#turn-details small { overflow-wrap: anywhere; }
#turn-details pre { white-space: pre-wrap; }
@media (max-width: 768px) {
    #chat-workspace { padding: 0 12px; }
    #chat-sidebar .sidebar-content { padding-right: 28px; }
    #session-list .wrap { max-height: 36dvh; }
    #session-list .wrap span { max-width: calc(100vw - 75px); }
    .chat-topbar-inner { padding-left: 36px; height: 56px; }
    #chat-topbar { min-height: 56px; }
    .chat-title span, #composer-hint { display: none; }
    #composer-actions { justify-content: flex-end; }
    #composer-area { padding: 0; }
    #quick-prompts { gap: 6px; margin-bottom: 10px; }
    #quick-prompts button { min-width: 100px; padding: 9px 8px; font-size: 12px; }
    #chat-thread .message-wrap { padding: 0 4px; }
    #chat-thread .welcome h1 { font-size: 25px; }
    #privacy-note { font-size: 10px; padding-bottom: max(8px, env(safe-area-inset-bottom)); }
}
@media (prefers-reduced-motion: reduce) { .status-dot { animation: none !important; } }
'''

# Only presentation: no network access, persistence, or access to chat data.
JS = '''() => {
    const init = () => {
        const sidebar = document.getElementById('chat-sidebar');
        const input = document.querySelector('#question-box textarea');
        if (!sidebar || !input) return false;
        if (window.matchMedia('(max-width: 768px)').matches && sidebar.classList.contains('open')) {
            sidebar.querySelector('.toggle-button')?.click();
        }
        sidebar.querySelector('.toggle-button')?.setAttribute('aria-label', '切换侧栏');
        input.setAttribute('aria-label', '向聊天记录提问');
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && (event.isComposing || event.keyCode === 229)) {
                event.stopImmediatePropagation();
            }
        }, true);
        return true;
    };
    if (!init()) {
        const observer = new MutationObserver(() => { if (init()) observer.disconnect(); });
        observer.observe(document.body, { childList: true, subtree: true });
        setTimeout(() => observer.disconnect(), 10000);
    }
}'''

FOCUS_COMPOSER_JS = '''() => {
    const sidebar = document.getElementById('chat-sidebar');
    if (window.matchMedia('(max-width: 768px)').matches && sidebar?.classList.contains('open')) {
        sidebar.querySelector('.toggle-button')?.click();
    }
    document.querySelector('#question-box textarea')?.focus();
}'''
