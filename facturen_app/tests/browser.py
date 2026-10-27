"""Browsers voor de tests die een pagina echt uittekenen.

WebKit hoort bij de schermtests die op een iPhone lijken. Die browser trekt in
CI een zware lading systeempakketten mee en de job kwam daar niet meer uit.
Zonder binary slaan die tests over, zoals toen Playwright zelf niet
geïnstalleerd was. De meting van de inbox mag niet overslaan: die gebruikt de
browser die er wél is.
"""
import os
import shutil

import pytest

_WEBKIT = None


def webkit_of_overslaan():
    """sync_playwright, of een skip als WebKit er niet staat."""
    global _WEBKIT
    if _WEBKIT is False:
        pytest.skip("WebKit is niet geïnstalleerd")
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    if _WEBKIT is None:
        with sync_playwright() as p:
            pad = p.webkit.executable_path
        _WEBKIT = bool(pad and os.path.isfile(pad))
    if not _WEBKIT:
        pytest.skip("WebKit is niet geïnstalleerd")
    return sync_playwright


def playwright_verplicht():
    """De inbox-test faalt zonder Playwright, in plaats van stil over te slaan."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.fail("Playwright hoort bij de tests, anders meten we de breedte niet")
    return sync_playwright


def meetbrowser(p):
    """Chrome van de machine, anders Chromium van Playwright, anders WebKit.

    De volgorde is wat er in CI het eerst klaarstaat. Een download van WebKit
    hoort hier niet bij."""
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    for naam in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        pad = shutil.which(naam)
        if not pad:
            continue
        try:
            return p.chromium.launch(executable_path=pad, args=args)
        except Exception:
            continue
    chromium = p.chromium.executable_path
    if chromium and os.path.isfile(chromium):
        try:
            return p.chromium.launch(args=args)
        except Exception:
            pass
    webkit = p.webkit.executable_path
    if webkit and os.path.isfile(webkit):
        return p.webkit.launch()
    pytest.fail("Geen browser om de breedte te meten")
