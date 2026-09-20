import re
from playwright.sync_api import Page, expect


def test_example(page: Page) -> None:
    page.goto("https://www.myt.mu/sinformer/trafficwatch/")
    page.locator("#playerCaudanNorth101-VolumeControl-VolumeButton").click()
    page.locator("#playerCaudanNorth101-OverlayPlayIcon").click()
    page.locator("#playerCaudanNorth101-MediaController-FullscreenButton").click()
    page.locator("#playerCaudanNorth101-MediaController-FullscreenButton").click()
