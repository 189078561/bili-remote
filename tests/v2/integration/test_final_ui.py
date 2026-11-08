"""Approved UI regression against the real isolated application, with synthetic storage."""
from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_browser_modules import browser_session
from test_browser_modules import live_server as shared_live_server

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / '.dev/final-ui'


@pytest.fixture
def live_server(settings):
    yield from shared_live_server.__wrapped__(settings)


def test_final_ui_navigation_and_task_drawer(live_server):
    base, _ = live_server
    playwright, browser, page, errors = browser_session(base, legacy=False)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        for width, height in ((1440, 900), (390, 844), (768, 1024), (1024, 768), (1280, 800)):
            page.set_viewport_size({'width': width, 'height': height})
            nav = page.locator('#app-navigation')
            expect(nav).to_be_visible()
            assert page.locator('.app-sidebar').count() == 0
            box = nav.bounding_box()
            assert box['y'] > height - 90 if width == 390 else box['y'] < 68
            for view in ('home', 'files', 'publish', 'download', 'settings'):
                selector = '#settings-entry' if view == 'settings' else f'.app-tabs [data-view={view}]'
                page.locator(selector).click()
                expect(page.locator(f'#{view}-view')).to_be_visible()
                expect(page.locator(selector)).to_have_attribute('aria-current', 'page')
            page.locator('.app-tabs [data-view=home]').click()
            page.screenshot(path=str(ARTIFACTS / f'phase1-shell-{width}.png'))
            page.locator('#task-center-toggle').click()
            expect(page.locator('#task-center')).to_be_visible()
            page.keyboard.press('Escape')
            expect(page.locator('#task-center')).to_be_hidden()
            assert page.evaluate('!document.querySelector("#app-navigation").inert')
        page.locator('.account-menu summary').click()
        page.locator('#theme-toggle').click()
        page.locator('.app-tabs [data-view=files]').click()
        expect(page.locator('.account-menu')).not_to_have_attribute('open', '')
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()
