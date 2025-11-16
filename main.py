import asyncio
import os
from datetime import datetime
from playwright.async_api import async_playwright
from dotenv import load_dotenv
from datetime import date, timedelta
import time



load_dotenv()  

CELAYIX_LOGIN_URL = "https://login.celayix.com"  
CELAYIX_SHIFTS_URL = "https://team-xpress.celayix.com/#/schedule/selfschedule"  

CLIENTID = os.getenv("CELAYIX_CLIENTID")
USERNAME = os.getenv("CELAYIX_USERNAME")
PASSWORD = os.getenv("CELAYIX_PASSWORD")

if not USERNAME or not PASSWORD:
    raise RuntimeError("Set CELAYIX_USERNAME and CELAYIX_PASSWORD in .env or env vars.")

#Helper functions
async def debug_dump(page):
    # Try to find any rows/buttons that look like self-schedule actions
    buttons = await page.query_selector_all("button")
    print(f"Found {len(buttons)} buttons")
    for i, btn in enumerate(buttons[:20]):  # only first 20 so it’s not insane
        text = await btn.inner_text()
        print(f"[{i}] BUTTON TEXT: {repr(text)}")

def get_next_week_end(days_ahead=6):
    today = date.today()
    print(today)
    return today + timedelta(days=days_ahead)
def format_celayix_date_short(d: date) -> str:
    # "11/23/25"
    return d.strftime("%m/%d/%y")

def shift_matches_rules(shift):
    """
    Simple filter function.
    shift = {
      "title": str,
      "location": str,
      "start": datetime | None,
      "end": datetime | None,
      "hours": float | None,
      "card": ElementHandle
    }
    """
    # if "Aria" not in shift["location"]:
    #     return False
    return True


async def login(page):
    await page.goto(CELAYIX_LOGIN_URL)
    # TODO: open DevTools in your browser to get the real selectors for username/password.
    await page.fill('input[name="clientID"]', CLIENTID)
    await page.fill('input[name="username"]', USERNAME)
    await page.fill('input[name="password"]', PASSWORD)
    await page.click("button[type=submit]")
    await page.wait_for_load_state("networkidle")


async def try_book_shifts(page, dry_run=True):
    shifts = await parse_shifts(page)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Found {len(shifts)} shift candidates")

    for shift in shifts:
        if not shift_matches_rules(shift):
            continue

        print(f"  MATCH: {shift['title']}")

        if dry_run:
            print("    → DRY-RUN: would check this box + click 'Schedule me'")
        else:
            # 1) select the shift by checking its checkbox
            try:
                await shift["checkbox"].check()
            except Exception:
                await shift["checkbox"].click()

            # 2) click the global 'Schedule me' button
            schedule_btn = await page.query_selector("button.cx-button:has-text('Schedule me')")
            if schedule_btn:
                await schedule_btn.click()
                print("    → CLICKED checkbox + 'Schedule me'")
            else:
                print("    → Could not find 'Schedule me' button.")

        # usually you only want one shift per cycle
        break


async def parse_shifts(page):
    """
    Treat each checkbox-like control in the self-scheduling grid as a potential shift.
    We'll refine filters later if needed.
    """
    shifts = []

    # 1) Try real checkbox inputs first
    checkboxes = await page.query_selector_all("input[type='checkbox']")
    print(f"DEBUG: found {len(checkboxes)} input[type='checkbox']")

    # 2) If nothing, try ARIA role="checkbox"
    if not checkboxes:
        aria_boxes = await page.query_selector_all("[role='checkbox']")
        print(f"DEBUG: found {len(aria_boxes)} [role='checkbox']")
        checkboxes = aria_boxes

    if not checkboxes:
        print("DEBUG: still no checkbox-like elements found; probably no open shifts for this range.")
        return []

    for i, cb in enumerate(checkboxes):
        # Try to grab the nearest container (row or div) for context
        row = await cb.evaluate_handle("el => el.closest('tr') || el.closest('div')")
        if not row:
            continue

        text = (await row.inner_text()).strip()
        if not text:
            continue

        # Optional: print a few samples so you can see what they look like
        if i < 5:
            print(f"[SHIFT {i}] {repr(text[:120])}")

        shift = {
            "title": text,
            "location": text,   # refine later if needed
            "start": None,
            "end": None,
            "hours": None,
            "checkbox": cb,
        }
        shifts.append(shift)

    print(f"DEBUG: treating {len(shifts)} checkbox-like elements as shift candidates")
    return shifts





async def try_book_shifts(page, dry_run=True):
    shifts = await parse_shifts(page)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Found {len(shifts)} shifts")

    for shift in shifts:
        if shift_matches_rules(shift):
            print(f"  MATCH: {shift['title']} @ {shift['location']}")
            if dry_run:
                print("    → DRY-RUN: would click Accept")
            else:
                # Update selector for accept button
                accept_btn = await shift["card"].query_selector(".accept-button")
                if accept_btn:
                    await accept_btn.click()
                    print("    → CLICKED Accept")
                else:
                    print("    → No accept button found for this card.")

async def apply_end_date_and_find(page, days_ahead=6):
    end = get_next_week_end(days_ahead)
    end_str = format_celayix_date_short(end)

    # This will clear and type into the end-date box
    await page.fill('#selfschedule-endDate', end_str)

    # Click the Find button
    await page.click('button.cx-button[type="submit"]')
    await page.wait_for_load_state("networkidle")

    print(f"Set end date to {end_str} and clicked Find")

async def main_loop(dry_run=True, interval_sec=5, days_ahead=6):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        await login(page)
        await page.goto(CELAYIX_SHIFTS_URL)
        await page.wait_for_load_state("networkidle")

        # Set end date to next week and run initial search
        await apply_end_date_and_find(page, days_ahead=days_ahead)
        
        await try_book_shifts(page, dry_run=dry_run)
        last_search_time = time.monotonic()


        while True:
            # enforce at least 10 seconds between searches
            now = time.monotonic()
            elapsed = now - last_search_time
            min_gap = 11.0  # Celayix requirement

            if elapsed < min_gap:
                await asyncio.sleep((min_gap - elapsed) + 0.3)  # small safety buffer

            await page.click('button.cx-button[type="submit"]')
            await page.wait_for_load_state("networkidle")
            last_search_time = time.monotonic()

            await try_book_shifts(page, dry_run=dry_run)





if __name__ == "__main__":
    # Start in dry-run mode for testing.
    asyncio.run(main_loop(dry_run=True, interval_sec=15))
