"""Browser flow through real local services; upstream media/provider are offline fixtures."""
from pathlib import Path
import sys
import subprocess
from playwright.sync_api import expect
from test_browser_modules import browser_session, live_server
from tests.v2.workbench.test_browser import show_panel
from tests.v2.naming.test_service_api import RecognitionProvider
from tests.v2.integration.test_final_ui import ARTIFACTS


def test_final_ui_download_to_publish_and_undo(live_server):
    base,app=live_server
    services=app.state.services
    # Only the external downloader process is replaced; worker, queue, file
    # registration, recognition and all downstream operations use real services.
    media=services.settings.data_dir/'synthetic-source.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=12:duration=3','-c:v','mpeg4','-y',str(media)],check=True)
    script=services.settings.data_dir/'offline-downloader.py'
    original=Path(__file__).resolve().parents[2]/'fake_ytdlp.py'
    script.write_text(original.read_text().replace('result.write_text("fake media", encoding="utf-8")',f'result.write_bytes(Path({str(media)!r}).read_bytes())'))
    services.settings.ytdlp_command=(sys.executable,str(script))
    services.v2.resolver.providers['tmdb']=RecognitionProvider()
    playwright,browser,page,errors=browser_session(base,legacy=False)
    try:
        page.locator('.app-tabs [data-view=download]').click()
        page.locator('#dv2-url').fill('https://www.bilibili.com/video/BV1xx411c7mD')
        page.locator('[data-parse]').click()
        expect(page.locator('[data-resolution]')).to_be_visible()
        page.locator('[data-download-form] button[type=submit]').click()
        row=page.locator('.dv2-job').first
        expect(row).to_have_attribute('data-status','completed',timeout=30000)
        job=services.database.list_jobs(limit=1)[0]
        source=Path(job['result_path']);original_bytes=source.read_bytes()
        assert original_bytes==media.read_bytes()
        row.get_by_role('button',name='打开文件管理',exact=True).click()
        expect(page.locator('#files-view')).to_be_visible()
        relative=source.relative_to(services.settings.download_root).as_posix()
        page.locator(f'#file-browser-host .file-row[data-path="{relative}"] .file-check').check()
        page.locator('#file-browser-host').get_by_role('button',name='进入工作台',exact=True).click()
        page.wait_for_function('V2Workbench.context && V2Workbench.preview')
        show_panel(page,'identity')
        page.locator('[data-wb=query]').fill('Film')
        page.locator('[data-wb=media-type]').select_option('movie')
        page.locator('[data-action=search]').click()
        page.locator('.wb-candidate').filter(has_text='Film').click()
        page.wait_for_function('V2Workbench.context?.identity?.confirmed === true')
        expect(page.locator('[data-sheet=identity]')).to_be_hidden()
        page.locator('[data-wb=selected-title]').fill('Synthetic Film')
        page.wait_for_function('V2Workbench.preview?.operations?.[0]?.target_relative_path.includes("Synthetic.Film") && V2Workbench.preview.can_apply')
        target=services.settings.download_root/page.evaluate('V2Workbench.preview.operations[0].target_relative_path')
        assert source.exists() and not target.exists()
        show_panel(page,'changes')
        page.locator('[data-action=apply]').click()
        page.wait_for_function('V2Workbench.resources?.[0]?.relative_path.includes("Synthetic.Film") && V2Workbench.context',timeout=30000)
        assert target.read_bytes()==original_bytes and not source.exists()
        show_panel(page)
        page.locator('[data-edit=release_title]').fill('合成影片 · 完整流程验收')
        page.locator('[data-edit=subtitle]').fill('本地合成媒体 / 人工副标题')
        page.locator('[data-edit=overview]').fill('唯一人工简介：下载、改名及工具结果共用同一资源。')
        show_panel(page,'images')
        page.locator('[data-wb=image-count]').fill('4')
        page.locator('[data-wb=image-mode]').select_option('collage')
        page.locator('[data-action=screenshots]').click()
        expect(page.locator('[data-wb=images] img').first).to_be_visible(timeout=30000)
        for width,height in ((1440,900),(390,844)):
            page.set_viewport_size({'width':width,'height':height})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(ARTIFACTS/f'flow-images-{width}.png'))
        show_panel(page,'report')
        page.locator('[data-action=report]').click()
        expect(page.locator('[data-wb=reports]')).to_contain_text('MediaInfo',timeout=30000)
        page.screenshot(path=str(ARTIFACTS/'flow-report-390.png'))
        show_panel(page,'torrent')
        page.locator('[data-action=torrent]').click()
        expect(page.get_by_role('link',name='下载种子',exact=True)).to_be_visible(timeout=30000)
        with page.expect_download() as download:
            page.get_by_role('link',name='下载种子',exact=True).click()
        assert download.value.suggested_filename.endswith('.torrent')
        page.screenshot(path=str(ARTIFACTS/'flow-torrent-390.png'))
        show_panel(page,'preview')
        expect(page.locator('[data-wb=output]')).to_contain_text('MediaInfo')
        expect(page.locator('[data-wb=local-images-preview] img')).to_be_visible()
        expect(page.locator('[data-wb=validation]')).to_contain_text('缩略图待上传图床')
        assert page.locator('[data-wb=output]').text_content().count('唯一人工简介')==1
        page.context.grant_permissions(['clipboard-read','clipboard-write'])
        page.locator('[data-copy=content]').click()
        assert '唯一人工简介' in page.evaluate('navigator.clipboard.readText()')
        page.screenshot(path=str(ARTIFACTS/'flow-preview-390.png'))
        # A rename started in the main review sheet must remain undoable from
        # naming details, just like a rename started inside the advanced editor.
        show_panel(page,'naming')
        panel=page.locator('[data-wb=naming-panel]')
        panel.get_by_role('button',name='预览撤销',exact=True).click()
        expect(panel.locator('.v2-naming__preview')).to_contain_text(source.name)
        panel.get_by_role('button',name='确认执行本次预览',exact=True).click()
        page.wait_for_function('(path)=>V2Workbench.resources?.[0]?.relative_path===path && V2Workbench.context',arg=relative,timeout=30000)
        assert source.read_bytes()==original_bytes and not target.exists()
        expect(page.locator('[data-edit=overview]')).to_have_value('唯一人工简介：下载、改名及工具结果共用同一资源。')
        assert not errors,errors
    finally:
        browser.close();playwright.stop()
