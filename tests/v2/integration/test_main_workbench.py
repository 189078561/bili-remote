import subprocess
import re
from playwright.sync_api import expect
from test_browser_modules import browser_session, live_server


def test_main_page_real_resource_naming_publish_and_rule_memory(live_server):
    base,app=live_server
    path=app.state.services.settings.download_root/'Media'/'episode01.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=10:duration=3','-c:v','mpeg4','-y',str(path)],check=True)
    playwright,browser,page,errors=browser_session(base,legacy=False)
    writes=[]
    page.on('request',lambda request: writes.append(request.url) if request.method in {'POST','PUT','PATCH'} else None)
    try:
        expect(page.locator('#file-browser-host .file-row[data-path="Media"]')).to_be_visible()
        page.locator('#file-browser-host .file-row[data-path="Media"]').click()
        page.locator('#file-browser-host .file-row[data-path="Media/episode01.mp4"] .file-check').check()
        page.locator('#file-browser-host').get_by_role('button',name='进入发种工作台').click()
        expect(page.locator('[data-wb="context-summary"]')).to_contain_text('1 个媒体文件',timeout=15000)
        page.locator('.wb-adjust > summary',has_text='调整识别与规则').click()
        page.locator('[data-wb="selected-title"]').fill('Synthetic Work')
        page.locator('[data-wb="season"]').fill('1')
        page.locator('[data-wb="episode"]').fill('1')
        page.locator('[data-action="overrides"]').click()
        expect(page.locator('[data-edit="release_title"]')).to_have_value(re.compile('Synthetic Work'))
        page.locator('[data-edit="overview"]').fill('合成资源简介')
        expect(page.locator('[data-wb="output"]')).to_contain_text('合成资源简介')
        page.locator('[data-action="naming"]').click()
        expect(page.locator('[data-wb="rename-list"]')).to_contain_text('Synthetic.Work')
        assert path.exists()  # Preview never writes.
        page.locator('[data-wb="save-rules"]').click()
        expect(page.locator('#app-notifications')).to_contain_text('规则已保存')
        assert app.state.services.v2.memory.get()['layers']
        page.locator('[data-edit="release_title"]').fill('Manual keeps this')
        page.locator('[data-action="resolve"]').click()
        expect(page.locator('[data-edit="release_title"]')).to_have_value('Manual keeps this')
        page.reload()
        page.locator('[data-view="publish"]').click()
        expect(page.locator('[data-edit="release_title"]')).to_have_value('')
        assert not any(any(endpoint in url for endpoint in ['/api/releases','/api/publish/sessions','/api/media/metadata']) for url in writes)
        assert not errors,errors
    finally:
        browser.close();playwright.stop()
