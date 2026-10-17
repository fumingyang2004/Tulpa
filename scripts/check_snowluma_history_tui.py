"""Offline checks for source selection and the Docker history card."""
import asyncio

from check_terminal_tui import FullClient, TulpaApp, settle, SIZE
from textual.widgets import Input, Select


class Client(FullClient):
    def __init__(self):
        super().__init__()
        self.source = dict(source='snowluma', source_type='snowluma', source_label='SL Docker 历史消息',
                           source_ready=True, data_root='', keys_file='', requires_key=False)

    def get(self, path, **params):
        value = super().get(path, **params)
        if path == '/native': value['platforms']['qq'] = dict(self.source)
        return value

    def put(self, path, body):
        if path == '/native/qq':
            self.record('PUT', path, body)
            self.source.update(body)
            return dict(platforms=dict(qq=dict(self.source)))
        return super().put(path, body)


async def check():
    client = Client()
    app = TulpaApp(client=client)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        await pilot.press('0')
        await settle(app, pilot)
        card = str(app.query_one('#native-qq').render())
        assert 'SL Docker 历史消息' in card and '无需配置' in card
        app.query_one('#native-table').focus()
        await pilot.press('k')
        await settle(app, pilot)
        app.screen.dismiss('qq')
        await settle(app, pilot)
        assert not any('/extract' in p for m,p,_ in client.calls if m == 'POST')
        await pilot.press('p')
        await settle(app, pilot)
        app.screen.dismiss('qq')
        await settle(app, pilot)
        form = app.screen
        assert form.query_one('#f-source', Select).value == 'snowluma'
        form.query_one('#f-data-root', Input).value = ''
        form.action_submit()
        await settle(app, pilot)
        assert client.bodies('PUT', '/native/qq') == [dict(source='snowluma', data_root='')]
        assert app.current == 'native'
    print('PASS: Docker source card, no native key extraction, source form accepts an empty native path')


if __name__ == '__main__': asyncio.run(check())
