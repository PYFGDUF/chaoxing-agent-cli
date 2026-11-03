from __future__ import annotations
import time
from urllib.parse import urlsplit
from . import parsers
from .session import CliError

HOME = "https://i.chaoxing.com/"


def validate_url(url):
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or not (p.hostname == "chaoxing.com" or p.hostname.endswith(".chaoxing.com")) or p.username or p.password:
        raise CliError("INVALID_ARGUMENT", "URL 需为 HTTPS 学习通域名。", 2)
    return url


class Browser:
    def __init__(self, session, headed=False, timeout=30000, login=False):
        self.session, self.headed, self.timeout, self.login = session, headed, timeout, login

    def __enter__(self):
        state = None if self.login else self.session.load()
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        try:
            try:
                self.browser = self.pw.chromium.launch(headless=not self.headed)
            except Exception as e:
                if "Executable doesn't exist" in str(e):
                    raise CliError("BROWSER_MISSING", "请运行 python -m playwright install chromium。", 1) from None
                raise
            self.context = self.browser.new_context(storage_state=state)
            self.context.set_default_timeout(self.timeout)
            self.page = self.context.new_page()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if hasattr(self, "browser"):
            self.browser.close()
        self.pw.stop()

    def check_auth(self):
        for frame in self.page.frames:
            if "passport" in frame.url and "login" in frame.url.lower():
                raise CliError("AUTH_REQUIRED", "登录态已过期，请运行 xxt auth login。", 3)
            if frame.locator('input[type="password"]').count():
                raise CliError("AUTH_REQUIRED", "页面要求登录，请运行 xxt auth login。", 3)
        text = self.page.locator("body").inner_text()
        if any(t in text for t in ("请输入验证码", "访问过于频繁", "安全验证")):
            raise CliError("VERIFICATION_REQUIRED", "页面要求人工验证，请在浏览器中完成后重试。", 3)

    def goto(self, url):
        self.page.goto(validate_url(url), wait_until="domcontentloaded", timeout=self.timeout)
        if not self.login:
            self.check_auth()

    def frame_with(self, selector, empty_text=()):
        deadline = time.monotonic() + self.timeout / 1000
        while time.monotonic() < deadline:
            self.check_auth()
            for frame in self.page.frames:
                try:
                    if frame.locator(selector).count():
                        return frame
                    if empty_text and any(t in frame.locator("body").inner_text(timeout=1000) for t in empty_text):
                        return frame
                except CliError:
                    raise
                except Exception:
                    continue
            self.page.wait_for_timeout(150)
        raise CliError("PAGE_UNRECOGNIZED", "等待页面内容超时，页面结构可能已更新。", 4)

    def courses(self):
        self.goto(HOME)
        frame = self.frame_with('[id^="course_"], #courseList li', ("暂无课程", "还没有课程"))
        items = parsers.courses(frame.content(), frame.url)
        return {"items": items, "count": len(items)}

    def homework_list(self, course_url, max_pages):
        self.goto(course_url)
        if "/work/list" not in self.page.url:
            nav = self.page.get_by_role("link", name="作业", exact=True)
            nav.wait_for(state="visible")
            nav.click()
        frame = self.frame_with('[data*="work/"], a[href*="work/task"]', ("暂无作业", "没有作业"))
        items, seen_pages = {}, set()
        total, pages, complete, reason = None, 0, False, "page_limit"
        while pages < max_pages:
            parsed = parsers.homeworks(frame.content(), frame.url)
            signature = tuple((i["id"], i["url"]) for i in parsed["items"])
            if signature in seen_pages:
                reason = "pagination_stalled"
                break
            seen_pages.add(signature)
            pages += 1
            total = parsed["reported_total"] if parsed["reported_total"] is not None else total
            items.update((i["id"], i) for i in parsed["items"])
            nxt = self.next_button(frame)
            if nxt is None:
                complete = total is None or len(items) == total
                reason = "end" if complete else "total_mismatch"
                break
            if pages < max_pages:
                nxt.click()
                self.wait_changed(frame, lambda: tuple((i["id"], i["url"]) for i in parsers.homeworks(frame.content(), frame.url)["items"]), signature)
        return {"items": list(items.values()), "count": len(items), "reported_total": total,
                "pages": pages, "complete": complete, "stop_reason": reason}

    @staticmethod
    def next_button(frame):
        for selector in ('a:has-text("下一页")', 'button:has-text("下一页")', '.next', '[aria-label="下一页"]'):
            for el in frame.locator(selector).all():
                if el.is_visible() and el.is_enabled() and el.get_attribute("aria-disabled") != "true":
                    cls = el.get_attribute("class") or ""
                    if "disabled" not in cls and el.get_attribute("disabled") is None:
                        return el
        return None

    def wait_changed(self, frame, read, old):
        deadline = time.monotonic() + self.timeout / 1000
        while time.monotonic() < deadline:
            self.check_auth()
            if read() != old:
                return
            self.page.wait_for_timeout(150)
        raise CliError("PAGINATION_STALLED", "翻页后内容未更新。", 4)

    def fetch(self, url, max_pages=100):
        self.goto(url)
        frame = self.frame_with(".questionLi, .singleQuesId, .TiMu, .question-item")
        result, seen, pages, complete = [], set(), 0, False
        while pages < max_pages:
            batch = parsers.questions(frame.content(), frame.url)
            if not batch:
                raise CliError("PAGE_UNRECOGNIZED", "页面未识别到题目内容。", 4)
            signature = tuple((q["id"], q["title"]) for q in batch)
            if signature in seen:
                raise CliError("PAGINATION_STALLED", "题目分页重复。", 4)
            seen.add(signature)
            pages += 1
            for q in batch:
                q["index"] = len(result) + 1
                result.append(q)
            nxt = self.next_button(frame)
            if nxt is None:
                complete = True
                break
            if pages < max_pages:
                nxt.click()
                self.wait_changed(frame, lambda: tuple((q["id"], q["title"]) for q in parsers.questions(frame.content(), frame.url)), signature)
        title = self.page.locator("h2").first.inner_text() if self.page.locator("h2").count() else self.page.title()
        body = self.page.locator("body").inner_text() + "\n" + frame.locator("body").inner_text()
        import re
        m = re.search(r"题量\s*[:：]\s*(\d+)", body)
        expected = int(m.group(1)) if m else None
        if expected is not None and len(result) != expected:
            complete = False
        return {"id": parsers.identity(url), "title": title, "url": self.page.url,
                "questions": result, "count": len(result), "reported_total": expected,
                "pages": pages, "complete": complete}
