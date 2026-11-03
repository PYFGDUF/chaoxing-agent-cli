"""Real local Chromium checks for extraction and pagination regressions."""
import pytest
from xxt_cli.browser import Browser
from xxt_cli.session import CliError
from xxt_cli import parsers


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:
            pytest.skip('Chromium unavailable: ' + type(e).__name__)
        page = browser.new_page()
        yield page
        browser.close()


def test_next_disabled(page):
    page.set_content('<a class="next disabled">下一页</a>')
    assert Browser.next_button(page) is None
    page.set_content('<button>下一页</button>')
    assert Browser.next_button(page) is not None


def test_auth_and_verification(page):
    b = Browser(None)
    b.page = page
    page.set_content('<input type="password">')
    with pytest.raises(CliError, match='登录'):
        b.check_auth()
    page.set_content('安全验证')
    with pytest.raises(CliError) as err:
        b.check_auth()
    assert err.value.code == 'VERIFICATION_REQUIRED'


def test_list_pagination(page):
    page.set_content('''<span>0/2</span><ul><li data="https://mooc1.chaoxing.com/work/task?workId=1"><p class="overHidden2">first</p></li></ul>
    <button onclick="document.querySelector('li').setAttribute('data','https://mooc1.chaoxing.com/work/task?workId=2');document.querySelector('p').textContent='second';this.remove()">下一页</button>''')
    html = page.content()
    page.route('https://mooc1.chaoxing.com/**', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
    page.goto('https://mooc1.chaoxing.com/work/list')
    b = Browser(None, timeout=1000)
    b.page = page
    b.goto = lambda _: None
    b.frame_with = lambda *_: page
    data = b.homework_list('https://mooc1.chaoxing.com/work/list', 10)
    assert data['complete']
    assert data['count'] == 2
    assert data['pages'] == 2


def test_question_page_limit(page):
    page.set_content('<div class="questionLi"><h3>简答题</h3></div><button>下一页</button>')
    b = Browser(None)
    b.page = page
    b.goto = lambda _: None
    b.frame_with = lambda *_: page
    data = b.fetch('https://mooc1.chaoxing.com/work/view?workId=1', 1)
    assert data['complete'] is False


def test_numeric_paginator_arrow(page):
    page.set_content('<div class="pageDiv"><ul><li class="xl-prevPage xl-disabled"></li><li class="xl-active">1</li><li>2</li><li class="xl-nextPage"></li></ul></div><style>li {display:inline-block;width:20px;height:20px}</style>')
    assert Browser.next_button(page).get_attribute('class') == 'xl-nextPage'
    page.locator('.xl-nextPage').evaluate("el => el.classList.add('xl-disabled')")
    assert Browser.next_button(page) is None


def test_pagination_waits_through_empty_intermediate_state(page):
    page.set_content('<div id="rows">old</div>')
    b=Browser(None,timeout=2000)
    b.page=page
    page.evaluate("() => {setTimeout(() => document.querySelector('#rows').textContent='', 30);setTimeout(() => document.querySelector('#rows').textContent='new', 220)}")
    b.wait_changed(page,lambda:page.locator('#rows').inner_text(),'old')
    assert page.locator('#rows').inner_text()=='new'
