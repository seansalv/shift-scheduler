import asyncio
import os
import sys
from pathlib import Path
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

        action_desc = "click this shift card + 'Schedule me'" if shift.get("card") else "check this box + click 'Schedule me'"
        if dry_run:
            print(f"    → DRY-RUN: would {action_desc}")
        else:
            interacted = False

            if shift.get("card"):
                await shift["card"].click()
                interacted = True
                print("    → CLICKED shift card")
            elif shift.get("checkbox"):
                try:
                    await shift["checkbox"].check()
                except Exception:
                    await shift["checkbox"].click()
                interacted = True
                print("    → Selected shift checkbox")

            if not interacted:
                print("    → No clickable element found for this shift; skipping it.")
                continue

            schedule_btn = await page.query_selector("button.cx-button:has-text('Schedule me')")
            if schedule_btn:
                await schedule_btn.click()
                print("    → CLICKED 'Schedule me'")
            else:
                print("    → Could not find 'Schedule me' button.")

        # usually you only want one shift per cycle
        break


async def parse_shifts(page):
    """
    Treat each shift card (or checkbox) in the self-scheduling grid as a potential shift.
    We'll refine filters later if needed.
    """
    shifts = []

    card_selector = "div.cx-list-item.pointer-cursor"
    cards = await page.query_selector_all(card_selector)
    if cards:
        print(f"DEBUG: found {len(cards)} clickable shift cards")
        for i, card in enumerate(cards):
            contents = await card.query_selector_all(".cx-item-contents")
            text_parts = []
            for content in contents:
                snippet = (await content.inner_text()).strip()
                if snippet:
                    text_parts.append(snippet)

            if not text_parts:
                card_text = (await card.inner_text()).strip()
                if card_text:
                    text_parts.append(card_text)

            title = " | ".join(text_parts) if text_parts else "Shift"
            if i < 5:
                print(f"[CARD {i}] {repr(title[:120])}")

            shift = {
                "title": title,
                "location": text_parts[0] if text_parts else title,
                "start": None,
                "end": None,
                "hours": None,
                "card": card,
            }
            shifts.append(shift)

        return shifts

    # Fallback: try real checkbox inputs first
    checkboxes = await page.query_selector_all("input[type='checkbox']")
    print(f"DEBUG: found {len(checkboxes)} input[type='checkbox']")

    # If nothing, try ARIA role="checkbox"
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






async def apply_end_date_and_find(page, days_ahead=6):
    end = get_next_week_end(days_ahead)
    end_str = format_celayix_date_short(end)

    # This will clear and type into the end-date box
    await page.fill('#selfschedule-endDate', end_str)

    # Click the Find button
    await page.click('button.cx-button[type="submit"]')
    await page.wait_for_load_state("networkidle")

    print(f"Set end date to {end_str} and clicked Find")


async def wait_for_find_button_ready(page, timeout=15000):
    """Wait for Find button to be enabled (cooldown finished)"""
    try:
        await page.wait_for_function(
            """
            () => {
                const btn = document.querySelector('button.cx-button[type="submit"]');
                return btn && !btn.disabled;
            }
            """,
            timeout=timeout
        )
        return True
    except Exception:
        return False


async def test_full_flow_dummy(dry_run=True, max_cycles=5):
    """
    Simulates the COMPLETE automation flow using the dummy page:
    - Login (simulated - skipped for dummy page)
    - Navigate to self-schedule page
    - Set end date
    - Initial Find click
    - Wait for loading
    - Try to book shifts
    - Loop: Wait for cooldown → Find → Loading → Book → Repeat
    
    This mirrors the real main_loop() but uses dummy page for testing.
    """
    dummy_login_file = Path(__file__).parent / "dummy_login.html"
    dummy_shifts_file = Path(__file__).parent / "dummy_shifts.html"
    
    if not dummy_login_file.exists():
        print(f"ERROR: Dummy login file not found at {dummy_login_file}")
        return
    if not dummy_shifts_file.exists():
        print(f"ERROR: Dummy shifts file not found at {dummy_shifts_file}")
        return
    
    dummy_login_url = dummy_login_file.resolve().as_uri()
    dummy_shifts_url = dummy_shifts_file.resolve().as_uri()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        print("\n" + "="*70)
        print("FULL AUTOMATION FLOW TEST (Using Dummy Pages)")
        print("="*70)
        print(f"Mode: {'DRY-RUN (simulation only)' if dry_run else 'LIVE (will actually click)'}")
        print(f"Max cycles: {max_cycles} (0 = infinite loop)")
        print("="*70 + "\n")
        
        # Step 1: Start at login page (same as original: login() function goes to login URL first)
        print("STEP 1: Navigate to Login Page")
        print(f"  → Navigating to login page...")
        print(f"  → URL: {dummy_login_url}")
        
        # Navigate to login page first thing
        await page.goto(dummy_login_url, wait_until="networkidle", timeout=30000)
        await page.wait_for_load_state("networkidle")

        # Verify we're on the login page
        current_url = page.url
        print(f"  → Current URL: {current_url}")
        
        login_input = await page.query_selector('input[name="clientID"]')
        if not login_input:
            print("  ✗ ERROR: Not on login page! Could not find login form.")
            print(f"  → Page title: {await page.title()}")
            await browser.close()
            return
        
        print("  ✓ Login page loaded and verified")
        print("  → You should see the login form in the browser now")
        await asyncio.sleep(2)  # Give user time to see the login page
        
        # Step 2: Login (same as original code: await login(page))
        print("\nSTEP 2: Login")
        print("  → Filling in login credentials...")
        await page.fill('input[name="clientID"]', CLIENTID or "test-client")
        await page.fill('input[name="username"]', USERNAME or "test-user")
        await page.fill('input[name="password"]', PASSWORD or "test-pass")
        
        print("  → Clicking login button...")
        await page.click("button[type=submit]")
        await page.wait_for_load_state("networkidle")
        print("  ✓ Login button clicked, waiting for response...")
        await asyncio.sleep(3)  # Delay after clicking login to see the action
        print("  ✓ Login completed")
        
        # Step 3: Navigate to self-schedule page (same as original code: await page.goto(CELAYIX_SHIFTS_URL))
        print("\nSTEP 3: Navigate to Self-Schedule Page")
        print(f"  → Navigating to self-schedule page...")
        print(f"  → [Using dummy page URL: {dummy_shifts_url}]")
        await page.goto(dummy_shifts_url, wait_until="networkidle")  # Using dummy URL instead of CELAYIX_SHIFTS_URL
        await page.wait_for_load_state("networkidle")
        print("  ✓ Navigation completed, self-schedule page loaded")
        await asyncio.sleep(3)  # Delay after navigating to self-schedule to see the page
        
        # Step 4: Set end date and run initial search (same as original: await apply_end_date_and_find(page, days_ahead=days_ahead))
        print("\nSTEP 4: Set End Date and Initial Search")
        print("  → Setting end date...")
        await asyncio.sleep(1)  # Delay before setting date
        print("  → Calling apply_end_date_and_find(page, days_ahead=6)...")
        await apply_end_date_and_find(page, days_ahead=6)
        
        # Wait for loading to complete (dummy page specific)
        print("  → Waiting for loading to complete...")
        try:
            await page.wait_for_selector(".cx-list-item.pointer-cursor", timeout=5000)
            await page.wait_for_timeout(200)
            print("  ✓ Initial search complete, shift appeared")
            await asyncio.sleep(1)  # Delay to see the shift appear
        except Exception as e:
            print(f"  ✗ Timeout waiting for shift: {e}")
            await browser.close()
            return
        
        # Step 5: Try to book shifts (initial attempt)
        print("\nSTEP 5: Attempt to Book Shifts (Initial)")
        await try_book_shifts(page, dry_run=dry_run)
        
        # Track search time for cooldown
        last_search_time = time.monotonic()
        cycle_count = 0

        # Step 6: Main loop - repeat search and booking
        print("\n" + "="*70)
        print("STEP 6: Main Loop - Continuous Search and Booking")
        print("="*70)

        while True:
            cycle_count += 1
            if max_cycles > 0 and cycle_count > max_cycles:
                print(f"\n✓ Reached max cycles ({max_cycles}), stopping...")
                break
            
            print(f"\n--- Cycle {cycle_count} ---")
            
            # Wait for cooldown (12 seconds minimum)
            now = time.monotonic()
            elapsed = now - last_search_time
            min_gap = 12.0  # Match the 12-second cooldown
            
            if elapsed < min_gap:
                wait_time = (min_gap - elapsed) + 0.3  # Small safety buffer
                print(f"  ⏳ Waiting {wait_time:.1f}s for cooldown (elapsed: {elapsed:.1f}s)...")
                await asyncio.sleep(wait_time)
            
            # Check if button is ready (might still be in cooldown from page's JavaScript)
            find_btn = await page.query_selector('button.cx-button[type="submit"]')
            if find_btn:
                is_disabled = await find_btn.is_disabled()
                if is_disabled:
                    button_text = await find_btn.inner_text()
                    print(f"  ⏳ Button still in cooldown: '{button_text}'")
                    print("  → Waiting for button to be enabled...")
                    ready = await wait_for_find_button_ready(page, timeout=15000)
                    if not ready:
                        print("  ✗ Timeout waiting for cooldown")
                        break
                    print("  ✓ Button is ready")
            
            # Click Find
            print(f"  → Clicking 'Find' button (cycle {cycle_count})...")
            await find_btn.click()
            last_search_time = time.monotonic()
            print("  ✓ Find clicked")
            
            # Wait for loading to complete
            print("  → Waiting for loading to complete...")
            try:
                await page.wait_for_selector(".cx-list-item.pointer-cursor", timeout=5000)
                await page.wait_for_timeout(200)
                print("  ✓ Loading complete, shift appeared")
            except Exception as e:
                print(f"  ✗ Timeout waiting for shift: {e}")
                break
            
            # Try to book shifts
            print("  → Attempting to book shifts...")
            await try_book_shifts(page, dry_run=dry_run)
        
        print("\n" + "="*70)
        print("FULL FLOW TEST COMPLETE")
        print("="*70)
        print(f"✓ Completed {cycle_count} cycles")
        print("✓ Tested complete flow: Navigate to Login → Login → Navigate to Self-Schedule → Set Date → Find → Load → Book → Loop")
        if dry_run:
            print("\n💡 Tip: Run with 'python main.py test --live' to actually click Schedule me")
        
        # Keep browser open briefly
        await asyncio.sleep(2)
        await browser.close()


async def test_dummy_page(dry_run=True, cycles=3):
    """
    Comprehensive test that simulates the full automation flow:
    - Multiple Find cycles
    - Cooldown handling (12 seconds)
    - Loading animations
    - Shift selection and booking
    - Proper waiting between searches
    
    Args:
        dry_run: If True, won't actually click Schedule me
        cycles: Number of complete cycles to test (Find → Select → Schedule → Wait → Repeat)
    """
    dummy_file = Path(__file__).parent / "dummy_shifts.html"
    if not dummy_file.exists():
        print(f"ERROR: Dummy file not found at {dummy_file}")
        return
    
    dummy_url = dummy_file.resolve().as_uri()
    print(f"Loading dummy page from: {dummy_url}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        # Load the dummy page
        await page.goto(dummy_url)
        await page.wait_for_load_state("networkidle")
        
        # Wait for initial shift to load (no loading animation on first load)
        await page.wait_for_selector(".cx-list-item.pointer-cursor", timeout=3000)
        
        print("\n" + "="*70)
        print("COMPREHENSIVE FLOW TEST")
        print("="*70)
        print(f"Testing {cycles} complete cycles")
        print(f"Mode: {'DRY-RUN (simulation only)' if dry_run else 'LIVE (will actually click)'}")
        print("="*70 + "\n")
        
        for cycle in range(1, cycles + 1):
            print(f"\n{'='*70}")
            print(f"CYCLE {cycle}/{cycles}")
            print(f"{'='*70}\n")
            
            # Step 1: Click Find button
            print(f"[Cycle {cycle}] Step 1: Clicking 'Find' button...")
            find_btn = await page.query_selector('button.cx-button[type="submit"]')
            
            if not find_btn:
                print("  ✗ Could not find 'Find' button")
                break
            
            # Check if button is disabled (cooldown active)
            is_disabled = await find_btn.is_disabled()
            if is_disabled:
                button_text = await find_btn.inner_text()
                print(f"  ⏳ Find button is in cooldown: '{button_text}'")
                print("  → Waiting for cooldown to finish...")
                ready = await wait_for_find_button_ready(page, timeout=15000)
                if not ready:
                    print("  ✗ Timeout waiting for cooldown")
                    break
                print("  ✓ Cooldown finished, button is ready")
            
            # Click Find
            await find_btn.click()
            print("  ✓ Find button clicked")
            
            # Step 2: Wait for loading to complete
            print(f"\n[Cycle {cycle}] Step 2: Waiting for loading to complete...")
            try:
                # Wait for shift card to appear (this happens after loading completes)
                await page.wait_for_selector(".cx-list-item.pointer-cursor", timeout=5000)
                await page.wait_for_timeout(200)  # Small buffer
                print("  ✓ Loading complete, shift appeared")
            except Exception as e:
                print(f"  ✗ Timeout waiting for shift to load: {e}")
                break
            
            # Step 3: Test booking logic
            print(f"\n[Cycle {cycle}] Step 3: Testing shift selection and 'Schedule me'...")
            await try_book_shifts(page, dry_run=dry_run)
            
            # Step 4: Wait before next cycle (if not last cycle)
            if cycle < cycles:
                print(f"\n[Cycle {cycle}] Step 4: Waiting for next cycle...")
                print("  → Waiting for 12-second cooldown...")
                # Wait a bit to see the result, then wait for cooldown
                await asyncio.sleep(1)
                ready = await wait_for_find_button_ready(page, timeout=15000)
                if ready:
                    print("  ✓ Ready for next cycle")
                else:
                    print("  ⚠ Cooldown still active, but continuing...")
        
        print("\n" + "="*70)
        print("TEST COMPLETE")
        print("="*70)
        print(f"\n✓ Completed {cycles} full cycles")
        print("✓ Tested: Find → Loading → Select → Schedule → Cooldown")
        if dry_run:
            print("\n💡 Tip: Run with 'python main.py test --live' to actually click Schedule me")
        
        # Keep browser open for a moment to see final state
        await asyncio.sleep(2)
        await browser.close()


def parse_target_time(time_str):
    """
    Parse a time string like "13:12" or "1:12:00" into a datetime for today.
    Returns None if parsing fails.
    """
    try:
        # Try formats: "HH:MM" or "HH:MM:SS"
        if len(time_str.split(':')) == 2:
            hour, minute = map(int, time_str.split(':'))
            target = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        elif len(time_str.split(':')) == 3:
            hour, minute, second = map(int, time_str.split(':'))
            target = datetime.now().replace(hour=hour, minute=minute, second=second, microsecond=0)
        else:
            return None
        
        # If time has passed today, assume it's for tomorrow
        if target < datetime.now():
            target += timedelta(days=1)
        
        return target
    except Exception:
        return None


async def wait_until_time(target_time, click_before_seconds=1.0):
    """
    Wait until a specific time, then return the exact moment to click.
    click_before_seconds: How many seconds before target_time to click (default 1 second)
    """
    now = datetime.now()
    click_time = target_time - timedelta(seconds=click_before_seconds)
    
    if click_time < now:
        print(f"⚠ Target time {target_time.strftime('%H:%M:%S')} has already passed")
        return False
    
    wait_seconds = (click_time - now).total_seconds()
    print(f"⏰ Waiting {wait_seconds:.2f} seconds until {click_time.strftime('%H:%M:%S')} (shifts drop at {target_time.strftime('%H:%M:%S')})")
    
    # Wait with high precision
    await asyncio.sleep(wait_seconds)
    
    # Fine-tune to get as close as possible to the target
    while datetime.now() < click_time:
        remaining = (click_time - datetime.now()).total_seconds()
        if remaining > 0.1:
            await asyncio.sleep(remaining * 0.5)  # Sleep half the remaining time
        else:
            # Very close, busy-wait for precision
            while datetime.now() < click_time:
                pass
    
    return True


async def click_find_at_precise_time(page, target_time_str, click_before_seconds=1.0):
    """
    Click the Find button at a precise time, just before shifts become available.
    
    Args:
        page: Playwright page object
        target_time_str: Time when shifts become available (e.g., "13:12" or "1:12:00")
        click_before_seconds: How many seconds before target_time to click (default 1.0)
    """
    target_time = parse_target_time(target_time_str)
    if not target_time:
        print(f"✗ Invalid time format: {target_time_str}. Use format like '13:12' or '1:12:00'")
        return False
    
    # Wait until the precise moment
    success = await wait_until_time(target_time, click_before_seconds)
    if not success:
        return False
    
    # Click Find at the precise moment
    print(f"🎯 Clicking 'Find' at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
    find_btn = await page.query_selector('button.cx-button[type="submit"]')
    if find_btn:
        is_disabled = await find_btn.is_disabled()
        if is_disabled:
            print(f"⚠ Find button is disabled, waiting for cooldown...")
            ready = await wait_for_find_button_ready(page, timeout=15000)
            if not ready:
                print("✗ Timeout waiting for button to be enabled")
                return False
        
        await find_btn.click()
        print(f"✓ Find clicked! Search active when shifts drop at {target_time.strftime('%H:%M:%S')}")
        return True
    else:
        print("✗ Could not find 'Find' button")
        return False


async def main_loop(dry_run=True, interval_sec=5, days_ahead=6, target_time=None, click_before_seconds=1.0):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        # Step 1: Setup - Login and navigate
        print("🔧 SETUP PHASE")
        print("  → Logging in...")
        await login(page)
        print("  → Navigating to self-schedule page...")
        await page.goto(CELAYIX_SHIFTS_URL)
        await page.wait_for_load_state("networkidle")
        print("  ✓ Setup complete\n")

        # Step 2: Set end date (but don't click Find yet)
        print("📅 SETTING END DATE")
        end = get_next_week_end(days_ahead)
        end_str = format_celayix_date_short(end)
        await page.fill('#selfschedule-endDate', end_str)
        print(f"  ✓ End date set to {end_str}\n")

        if target_time:
            # Precise timing mode: wait until target time, then click Find
            print(f"🎯 PRECISE TIMING MODE")
            print(f"   Target time (when shifts drop): {target_time}")
            print(f"   Will click 'Find' {click_before_seconds} seconds before")
            print(f"   Current time: {datetime.now().strftime('%H:%M:%S')}\n")
            
            # Wait until the precise moment and click Find
            success = await click_find_at_precise_time(page, target_time, click_before_seconds)
            if success:
                # Wait for shifts to appear (loading completes)
                print("  → Waiting for shifts to appear...")
                await page.wait_for_load_state("networkidle")
                
                # Wait for shift cards to appear
                try:
                    await page.wait_for_selector(".cx-list-item.pointer-cursor", timeout=5000)
                    print("  ✓ Shifts appeared!")
                except Exception as e:
                    print(f"  ⚠ No shifts found yet: {e}")
                
                # Now attempt to book
                print("\n📋 ATTEMPTING TO BOOK SHIFTS")
                await try_book_shifts(page, dry_run=dry_run)
                print(f"\n✓ Precise timing booking completed!")
            else:
                print(f"\n✗ Precise timing search failed")
        else:
            # Normal mode: continuous searching
            print("🔄 CONTINUOUS MODE")
            # Do initial search
            await page.click('button.cx-button[type="submit"]')
            await page.wait_for_load_state("networkidle")
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
        
        # Keep browser open for a moment to see results (only in precise timing mode)
        if target_time:
            print("\n⏸ Browser will stay open for 10 seconds to view results...")
            await asyncio.sleep(10)





if __name__ == "__main__":
    # Check if we should run the dummy test
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        dry_run = "--live" not in sys.argv
        
        # Check if user wants full flow test
        if "--full" in sys.argv:
            # Full flow test (simulates complete automation)
            max_cycles = 5
            for arg in sys.argv:
                if arg.startswith("--cycles="):
                    try:
                        max_cycles = int(arg.split("=")[1])
                    except ValueError:
                        pass
            
            print(f"Running FULL FLOW test (simulates complete automation)")
            print(f"  - Dry run: {dry_run}")
            print(f"  - Max cycles: {max_cycles} (0 = infinite)")
            print()
            asyncio.run(test_full_flow_dummy(dry_run=dry_run, max_cycles=max_cycles))
        else:
            # Simple test (just booking logic)
            cycles = 3
            for arg in sys.argv:
                if arg.startswith("--cycles="):
                    try:
                        cycles = int(arg.split("=")[1])
                    except ValueError:
                        pass
            
            print(f"Running simple dummy page test")
            print(f"  - Dry run: {dry_run}")
            print(f"  - Cycles: {cycles}")
            print("  💡 Use 'python main.py test --full' for complete flow test")
            print()
            asyncio.run(test_dummy_page(dry_run=dry_run, cycles=cycles))
    else:
        # Parse command line arguments for main loop
        dry_run = "--live" not in sys.argv
        days_ahead = 6
        target_time = None
        click_before_seconds = 1.0
        
        for arg in sys.argv[1:]:
            if arg.startswith("--days="):
                try:
                    days_ahead = int(arg.split("=")[1])
                except ValueError:
                    pass
            elif arg.startswith("--time="):
                target_time = arg.split("=")[1]
            elif arg.startswith("--click-before="):
                try:
                    click_before_seconds = float(arg.split("=")[1])
                except ValueError:
                    pass
        
        if target_time:
            print(f"🎯 PRECISE TIMING MODE")
            print(f"   Target time: {target_time}")
            print(f"   Click before: {click_before_seconds} seconds")
            print(f"   Dry run: {dry_run}")
            print()
        else:
            print(f"Running in continuous mode (dry_run={dry_run})")
            print(f"💡 Use --time=HH:MM to enable precise timing mode")
            print()
        
        asyncio.run(main_loop(dry_run=dry_run, interval_sec=15, days_ahead=days_ahead, 
                              target_time=target_time, click_before_seconds=click_before_seconds))
