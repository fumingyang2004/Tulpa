"""Verify saved image opt-out reaches both refresh subprocess paths."""
from contextlib import nullcontext
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from chatlocal.store import Store
from chatlocal.live import LiveManager
from chatlocal.sync import sync_messages


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='wechat-forward-') as tmp:
        store=Store(Path(tmp)/'chats.sqlite3')
        LiveManager(store).configure(dict(wechat_read_images=False))
        captured=[]
        def runner(command,**kwargs):
            captured.append(command)
            assert '--no-images' in command
            raise ValueError('fixture stops before any client access')
        def batches(target,platform,args,output,**kwargs):
            captured.append(args)
            assert platform=='wechat' and '--no-images' in args
            assert kwargs['key']['read_images'] is False
            raise ValueError('fixture stops before any client access')
        with patch('chatlocal.sync.ingestion_lock',return_value=nullcontext()), \
             patch('chatlocal.client_accounts.resolve_account',return_value='fixture_owner'), \
             patch('incremental_common.bootstrap_time',return_value=0):
            assert sync_messages(store,['wechat'],runner=runner)[0]['status']=='error'
            with patch('chatlocal.import_pipeline.run_batches',side_effect=batches):
                assert sync_messages(store,['wechat'])[0]['status']=='error'
        assert len(captured)==2
    print('PASS saved image opt-out reaches legacy and production refresh flags and resume identity; no client access')


if __name__=='__main__':main()
