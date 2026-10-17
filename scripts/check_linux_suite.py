"""Consolidated isolated Linux regression run; no live accounts or paid models."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
CHECKS=('reader_install','linux_distribution','linux_full','terminal_backend','terminal_launcher','terminal_instances','terminal_tui','tui_instances','tui_easteregg','terminal_integration','voice','live',
        'artifacts','workspace_api','workspaces','workspace_ide','workspace_view','watch_dialogue',
        'watch_sync','watch_batches','tulpa','tulpa_api','reply_copilot','reply_agent','reply_history',
        'reply_quote','investigation','agent_budget','agent_failure',
        'qq_history','qq_replies','qq_labels','qq_storage','wechat_export','wechat_media_live','qq_media_live',
        'mcp','mcp_picker','mcp_chat','mcp_chat_turns','mcp_chat_bridge','mcp_chat_prompts','mcp_language_learning','mcp_language_usage','mcp_reactions',
        'snowluma_managed','snowluma_managed_transport','snowluma_setup','snowluma_docker','snowluma_docker_tui','snowluma_history','snowluma_history_tui',
        'group_admin','client_accounts','data_controls','refresh_chat','reader_cache',
        'import_batches','chat_browser','overview','qq_account_diagnostics','identity','people',
        'mcp_chat_media','mcp_image_import','mcp_qq_extensions','mcp_grant_edit','mcp_chat_targets','mcp_downloads','mcp_onebot','mcp_actions','mcp_auto_learning',
        'mcp_sticker_library','mcp_expression_evidence','mcp_language_integration','mcp_jargon_matching',
        'mcp_jargon_pipeline','mcp_multi_group_learning')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('checks',nargs='*',choices=CHECKS);args=parser.parse_args()
    folder=ROOT/'.tmp/linux-acceptance';folder.mkdir(parents=True,exist_ok=True)
    rows=[]
    for name in args.checks or CHECKS:
        start=time.monotonic();log=folder/(name+'.log')
        try:
            with log.open('w') as output:
                result=subprocess.run([sys.executable,str(ROOT/'scripts'/('check_'+name+'.py'))],cwd=ROOT,
                    stdout=output,stderr=subprocess.STDOUT,timeout=240)
            ok=result.returncode==0
        except subprocess.TimeoutExpired:ok=False
        row=dict(check=name,ok=ok,seconds=round(time.monotonic()-start,2),log=str(log.relative_to(ROOT)))
        rows.append(row);print(('PASS' if ok else 'FAIL')+' '+name+' '+str(row['seconds'])+'s',flush=True)
    (folder/'results.json').write_text(json.dumps(rows,indent=2)+'\n')
    failed=[r['check'] for r in rows if not r['ok']]
    print('Completed:',len(rows),'Failed:',', '.join(failed) or 'none',flush=True)
    return 1 if failed else 0


if __name__=='__main__':raise SystemExit(main())
