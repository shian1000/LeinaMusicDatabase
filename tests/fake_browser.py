"""A stand-in for the shared Selenium driver of the scraping fetchers: pages
are HTML strings by URL, a wait succeeds when its CSS selector matches the
current page (BeautifulSoup) and fails at once otherwise - no browser, no
network, no 8-second timeouts.

    browser = FakeBrowser({url: html, ...})
    browser.install(module, monkeypatch)   # get_global_driver, WebDriverWait, EC, time.sleep
"""
from types import SimpleNamespace

from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By


class FakeElement:
    def __init__(self, browser, on_click=None):
        self._browser = browser
        self._on_click = on_click

    def click(self):
        self._browser.clicks += 1
        if self._on_click:
            self._on_click()


class FakeBrowser:
    def __init__(self, pages: dict, on_click: dict | None = None):
        self.pages = pages
        self.on_click = on_click or {}  # url -> html shown after clicking on that page
        self.current_url = "about:blank"
        self.page_source = ""
        self.visited = []
        self.clicks = 0

    def get(self, url):
        self.visited.append(url)
        self.current_url = url
        self.page_source = self.pages.get(url, "<html></html>")

    def matches(self, by, selector) -> bool:
        if by == By.CSS_SELECTOR:
            return bool(BeautifulSoup(self.page_source, "html.parser").select(selector))
        # XPath: only the tag it ends with is checked, enough for "this chip is on the page"
        tag = selector.lstrip("/").split("[", 1)[0]
        return f"<{tag}" in self.page_source

    def element(self):
        after = self.on_click.get(self.current_url)

        def show():
            self.page_source = after
        return FakeElement(self, show if after is not None else None)

    def install(self, module, monkeypatch):
        browser = self

        def condition(kind):
            return lambda locator: (kind, locator)

        class FakeWait:
            def __init__(self, driver, timeout):
                pass

            def until(self, cond):
                if callable(cond):
                    if cond(browser):
                        return True
                    raise TimeoutException("condition never true")
                kind, (by, selector) = cond
                if not browser.matches(by, selector):
                    raise TimeoutException(f"no {selector}")
                return browser.element() if kind != "all" else [browser.element()]

        monkeypatch.setattr(module, "get_global_driver", lambda: browser)
        monkeypatch.setattr(module, "WebDriverWait", FakeWait)
        monkeypatch.setattr(module, "EC", SimpleNamespace(
            element_to_be_clickable=condition("clickable"),
            presence_of_all_elements_located=condition("all"),
            visibility_of_element_located=condition("visible"),
        ))
        monkeypatch.setattr(module.time, "sleep", lambda s: None)
        return self
