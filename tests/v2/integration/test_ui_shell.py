import json
from pathlib import Path

from playwright.sync_api import expect
from test_browser_modules import browser_session, live_server


def test_shell_navigation_and_responsive_layout(live_server, tmp_path):
    base, app = live_server
    playwright, browser, page, errors = browser_session(base, legacy=False)
    output = tmp_path / 'screenshots'
    output.mkdir(exist_ok=True)
    try:
        for width in [390, 768, 1440]:
            page.set_viewport_size({'width': width, 'height': 1000})
            for view in ['home', 'files', 'publish', 'download', 'settings']:
                page.locator('#settings-entry' if view == 'settings' else f'.app-tab[data-view="{view}"]').click()
                expect(page.locator(f'#{view}-view')).to_be_visible()
                expect(page.locator('#app-page-label')).to_have_text({'home':'首页','files':'文件管理','publish':'发种工作台','download':'B站下载','settings':'设置'}[view])
                page.wait_for_timeout(150)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, view)
                assert page.locator(f'#app-content > #{view}-view').count()==1
                content=page.locator(f'#{view}-view').bounding_box()
                nav=page.locator('#app-navigation').bounding_box()
                if width <= 600:
                    # Mobile: navigation is a fixed bottom bar below the page body.
                    assert nav['y'] >= content['y'], (width, view, content, nav)
                else:
                    # Desktop: navigation is the fixed top bar above the page body.
                    assert nav['y'] + nav['height'] <= content['y'] + 1, (width, view, content, nav)
                page.screenshot(path=str(output / f'{view}-{width}.png'), full_page=True)
            page.locator('#task-center-toggle').click()
            expect(page.locator('#task-center')).to_be_visible()
            page.keyboard.press('Escape')
            expect(page.locator('#task-center')).to_be_hidden()
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_task_identity_notifications_and_failures(live_server):
    base, app = live_server
    playwright, browser, page, errors = browser_session(base)
    try:
        tasks = {
            'download': [{'id':7, 'title':'测试下载', 'status':'running', 'progress':20}],
            'processing': [{'id':7, 'task_type':'file_operation', 'status':'running'}],
        }
        page.route('**/api/jobs?*', lambda route: route.fulfill(content_type='application/json', body=json.dumps(tasks['download'])))
        page.route('**/api/tasks?*', lambda route: route.fulfill(content_type='application/json', body=json.dumps(tasks['processing'])))
        # Drain any requests started before interception, then load controlled responses.
        page.evaluate('AppTaskCenter.refresh()')
        page.evaluate('AppTaskCenter.refresh()')
        page.locator('#task-center-toggle').click()
        expect(page.locator('[data-task-key="download:7"]')).to_be_visible()
        expect(page.locator('[data-task-key="processing:7"]')).to_be_visible()
        tasks['processing'][0]['status'] = 'failed'
        page.evaluate('''() => {
            for(let i=0;i<2;i++) AppTaskCenter.receive('processing', [{id:7,task_type:'file_operation',status:'failed'}]);
        }''')
        expect(page.locator('#task-center-notices p')).to_have_count(1)
        expect(page.locator('[data-task-key="processing:7"]').get_by_role('button',name='重试',exact=True)).to_have_count(0)
        page.evaluate("AppTaskCenter.failed('download', Error('连接中断'))")
        expect(page.locator('#task-center-error')).to_contain_text('连接中断')
        expect(page.locator('#task-center-count')).to_have_text('!')
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()
