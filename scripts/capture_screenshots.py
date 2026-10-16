"""Drive the console end to end (document C) and capture screenshots for the deck.

Usage:
  python scripts/capture_screenshots.py http://127.0.0.1:8090            # local (AUTH_MODE=none)
  python scripts/capture_screenshots.py https://YOUR-SITE.web.app        # deployed (uses guest sign-in)
Screenshots go to docs/screens/.
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8090"
OUT = Path(__file__).resolve().parent.parent / "docs" / "screens"
OUT.mkdir(parents=True, exist_ok=True)


def main():
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(BASE + "/")
        guest = pg.locator("#guest")
        if guest.count() and guest.is_visible():
            pg.screenshot(path=str(OUT / "00_login.png"))
            guest.click()
        pg.wait_for_selector("[data-run='C']", timeout=30000)
        pg.screenshot(path=str(OUT / "01_home.png"))
        pg.click("[data-run='C']")
        pg.wait_for_selector(".trace li .ag", timeout=30000)
        time.sleep(2.5)
        pg.screenshot(path=str(OUT / "02_processing_trace.png"))
        pg.wait_for_selector("#approve", timeout=120000)
        time.sleep(1.5)
        pg.screenshot(path=str(OUT / "03_proposal_findings.png"))
        (pg.locator(".finding.CRITICAL a[data-page]") if pg.locator(".finding.CRITICAL a[data-page]").count() else pg.locator("#reason a[data-page]")).first.click()
        time.sleep(1.2)
        pg.screenshot(path=str(OUT / "04_clause_highlight_page9.png"))
        pg.click("#approve")
        pg.click(".modal .btn.primary")
        pg.wait_for_selector(".uncommitted.committed", timeout=30000)
        pg.wait_for_selector(".rec", timeout=30000)
        pg.click("#verify")
        pg.wait_for_selector(".chain", timeout=15000)
        time.sleep(0.8)
        pg.locator("#gate").evaluate("el => el.scrollTop = el.scrollHeight")
        pg.screenshot(path=str(OUT / "05_executed_audit_verified.png"))
        pg.click("#tamper")
        pg.wait_for_selector(".chain .bad", timeout=15000)
        pg.locator("#gate").evaluate("el => el.scrollTop = el.scrollHeight")
        pg.screenshot(path=str(OUT / "06_tamper_detected.png"))
        pg.goto(BASE + "/#/")
        pg.wait_for_selector("table.queue", timeout=15000)
        pg.screenshot(path=str(OUT / "07_queue.png"))
        mob = b.new_page(viewport={"width": 390, "height": 844})
        mob.goto(BASE + "/")
        time.sleep(3)
        mob.screenshot(path=str(OUT / "08_mobile.png"))
        b.close()
    print("console errors:", errors or "none")
    print("screens:", sorted(x.name for x in OUT.glob("*.png")))


if __name__ == "__main__":
    main()
