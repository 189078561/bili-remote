"""Final product: real local UI/API; synthetic media, no external credentials."""
from pathlib import Path
import json
import re
import subprocess

import pytest
from playwright.sync_api import expect
from test_browser_modules import browser_session, live_server  # noqa: F401
from tests.v2.workbench.test_browser import show_panel

ARTIFACTS = Path(__file__).resolve().parents[3] / '.dev' / 'final-product-visual'
SIZES = [(1440,900),(1280,800),(1024,768),(768,1024),(390,844)]


def navigate(page, view):
    show_panel(page)
    page.locator('#settings-entry' if view == 'settings' else f'.app-tab[data-view="{view}"]').click()
    expect(page.locator(f'#{view}-view')).to_be_visible()


def seed(app):
    path = app.state.services.settings.download_root / 'Motion.Study.S01E01.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=12:duration=2',
                    '-c:v','mpeg4','-y',str(path)], check=True)
    return path


@pytest.mark.parametrize('width,height', SIZES)
@pytest.mark.parametrize('theme', ['light','dark'])
def test_five_page_visual_matrix(live_server, width, height, theme):
    base,app=live_server
    source=seed(app)
    playwright,browser,page,errors=browser_session(base,legacy=False)
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    geometry={}
    try:
        page.set_viewport_size({'width':width,'height':height})
        page.emulate_media(color_scheme=theme,reduced_motion='reduce')
        page.wait_for_function('window.AppResources && window.V2Workbench')
        navigate(page,'home')
        expect(page.locator('#home-resources')).to_contain_text(source.name)
        page.locator('#home-resources .home-resource').filter(has_text=source.name).get_by_role('button',name='进入工作台',exact=True).click()
        page.wait_for_function('V2Workbench.preview && V2Workbench.context')
        expect(page.locator('[data-sheet=identity]')).to_be_hidden()
        expect(page.locator('[data-layout=identity]')).to_be_visible()
        expect(page.locator('[data-sheet=resources]')).to_be_hidden()
        page.locator('[data-wb=selected-title]').fill('Motion Study')
        expect(page.locator('[data-wb=rename-list]')).to_contain_text('Motion.Study',timeout=15000)
        page.locator('[data-edit=release_title]').fill('Motion Study · 色彩与运动')
        page.locator('[data-edit=subtitle]').fill('本地合成短片 · 媒体参数来自当前文件')
        page.locator('[data-edit=overview]').fill('本地生成的测试视频，用于检查媒体处理流程。\n这里的人工编辑会在页面切换后保留。')
        expect(page.locator('[data-wb=output]')).to_contain_text('人工编辑')
        for view in ['home','files','publish','download','settings']:
            navigate(page,view)
            if view=='home':expect(page.locator('#home-system-status')).to_have_text('目录与任务服务可用')
            if view=='settings':
                expect(page.locator('#settings-group-nav button')).to_have_count(10)
                expect(page.locator('#settings-savebar')).to_be_hidden()
            page.evaluate('scrollTo(0,0)')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(view,width,theme)
            color=page.evaluate('getComputedStyle(document.body).backgroundColor')
            assert color==('rgb(22, 29, 26)' if theme=='dark' else 'rgb(245, 245, 241)'),color
            geometry[view]={'body':color,'bounds':page.locator(f'#{view}-view').bounding_box()}
            page.screenshot(path=str(ARTIFACTS/f'{view}-{width}x{height}-{theme}.png'),full_page=True)
        navigate(page,'publish')
        expect(page.locator('[data-edit=overview]')).to_have_value(re.compile('人工编辑'))
        show_panel(page,'preview')
        page.screenshot(path=str(ARTIFACTS/f'preview-{width}x{height}-{theme}.png'))
        page.context.grant_permissions(['clipboard-read','clipboard-write'])
        page.locator('[data-copy=content]').click()
        assert '人工编辑' in page.evaluate('navigator.clipboard.readText()')
        show_panel(page)
        assert source.exists()
        assert not errors,errors
        (ARTIFACTS/f'geometry-{width}x{height}-{theme}.json').write_text(json.dumps(geometry,ensure_ascii=False,indent=2))
    finally:
        browser.close();playwright.stop()


def test_theme_rules_and_drawer_keyboard(live_server):
    base,app=live_server
    playwright,browser,page,errors=browser_session(base,legacy=False)
    try:
        page.wait_for_function('window.AppResources')
        page.locator('.account-menu summary').click()
        page.locator('#theme-toggle').click()  # system -> light
        page.locator('#theme-toggle').click()  # light -> dark
        expect(page.locator('html')).to_have_attribute('data-theme','dark')
        page.reload()
        expect(page.locator('html')).to_have_attribute('data-theme','dark')
        navigate(page,'settings')
        page.locator('[data-group=rename]').click()
        page.locator('[data-rule-key=source]').fill('WEBRip')
        page.get_by_role('button',name='保存全局规则',exact=True).click()
        expect(page.locator('.rule-editor [role=status]')).to_contain_text('全局规则已保存')
        page.locator('[data-group=subtitle]').click()
        page.locator('[data-rule-key=template_id]').select_option('movie')
        page.locator('[data-rule-key=cast_limit]').select_option('5')
        page.get_by_role('button',name='保存全局规则',exact=True).click()
        expect(page.locator('.rule-editor [role=status]')).to_contain_text('全局规则已保存')
        rules=app.state.services.v2.memory.get()['layers'][0]
        assert rules['naming_rule']['source']['value']=='WEBRip'
        assert rules['subtitle_rule']['cast_limit']==5
        page.reload();navigate(page,'settings')
        page.locator('[data-group=rename]').click()
        expect(page.locator('[data-rule-key=source]')).to_have_value('WEBRip')
        page.locator('#task-center-toggle').click()
        expect(page.locator('#task-center-close')).to_be_focused()
        assert page.locator('.app-shell').evaluate('n=>n.inert')
        page.keyboard.press('Shift+Tab')
        assert page.locator('#task-center').evaluate('n=>n.contains(document.activeElement)')
        page.keyboard.press('Escape')
        expect(page.locator('#task-center')).to_be_hidden()
        assert not page.locator('.app-shell').evaluate('n=>n.inert')
        expect(page.locator('#task-center-toggle')).to_be_focused()
        assert not errors,errors
    finally:
        browser.close();playwright.stop()


def test_completed_download_opens_actual_files_and_same_workbench(live_server):
    base,app=live_server
    source=seed(app)
    db=app.state.services.database
    job=db.create_job(source_url='https://www.bilibili.com/video/BV1xx411c7mD',kind='manual',output_template='%(title)s.%(ext)s',output_dir='',title='合成下载完成记录')
    db.update_job(job,status='completed',result_path=str(source),progress=100)
    playwright,browser,page,errors=browser_session(base,legacy=False)
    try:
        page.wait_for_function('window.AppResources')
        navigate(page,'download')
        row=page.locator(f'.dv2-job[data-job-id="{job}"]')
        row.get_by_role('button',name='打开文件管理',exact=True).click()
        expect(page.locator('#files-view')).to_be_visible()
        expect(page.locator(f'#file-browser-host .file-row[data-path="{source.name}"]')).to_be_visible()
        navigate(page,'download')
        row.get_by_role('button',name='进入工作台',exact=True).click()
        page.wait_for_function('V2Workbench.context && V2Workbench.preview')
        assert page.evaluate('V2Workbench.resources[0].relative_path')==source.name
        assert page.evaluate('V2Workbench.context.origins[0].job_id')==job
        expect(page.locator('[data-sheet=resources]')).to_be_hidden()
        assert source.exists()
        assert not errors,errors
    finally:
        browser.close();playwright.stop()
