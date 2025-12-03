import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright
from dotenv import load_dotenv
from datetime import date, timedelta
import time
import json
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    print("⚠ aiohttp not installed. Install with: pip install aiohttp")
    print("   Falling back to Playwright-only mode (slower for bot competition)")



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

def parse_start_date(date_str: str = None) -> date:
    """
    Parse start date from MM/DD/YY format or default to today
    Returns: date object
    """
    if date_str:
        try:
            # Try parsing MM/DD/YY format
            return datetime.strptime(date_str, "%m/%d/%y").date()
        except ValueError:
            try:
                # Try parsing MM/DD/YYYY format
                return datetime.strptime(date_str, "%m/%d/%Y").date()
            except ValueError:
                print(f"⚠️  Could not parse start date '{date_str}', defaulting to today")
                return date.today()
    return date.today()

def parse_end_date(date_str: str = None, days_ahead: int = 6) -> date:
    """
    Parse end date from MM/DD/YY format or default to today + days_ahead
    Returns: date object
    """
    if date_str:
        try:
            # Try parsing MM/DD/YY format
            return datetime.strptime(date_str, "%m/%d/%y").date()
        except ValueError:
            try:
                # Try parsing MM/DD/YYYY format
                return datetime.strptime(date_str, "%m/%d/%Y").date()
            except ValueError:
                print(f"⚠️  Could not parse end date '{date_str}', defaulting to today + {days_ahead} days")
                return get_next_week_end(days_ahead)
    return get_next_week_end(days_ahead)

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


# ============================================================================
# BOT COMPETITION OPTIMIZATIONS: Hybrid API Approach
# ============================================================================

class APIEndpointCapture:
    """Captures API endpoints from network requests for direct HTTP calls (bot competition mode)"""
    def __init__(self):
        self.find_endpoint = None
        self.find_method = None
        self.find_headers = None
        self.find_body = None
        self.booking_endpoint = None
        self.booking_method = None
        self.booking_headers = None
        self.booking_body = None
        self.captured = False
    
    async def setup_interception(self, page):
        """Set up network request interception to capture API endpoints"""
        async def handle_request(request):
            url = request.url
            method = request.method
            
            # Capture "Find" API call (search for shifts)
            if any(keyword in url.lower() for keyword in ['selfschedule', 'search', 'find', 'shift', 'schedule']):
                if method in ['POST', 'GET', 'PUT']:
                    # Only capture if we don't have one yet, or if this looks more relevant
                    if not self.find_endpoint or 'selfschedule' in url.lower():
                        self.find_endpoint = url
                        self.find_method = method
                        self.find_headers = dict(request.headers)
                        
                        # Try multiple methods to capture request body
                        body = None
                        try:
                            body = await request.post_data()
                        except:
                            pass
                        
                        # If post_data() didn't work, try getting it from the request body buffer
                        if not body:
                            try:
                                post_data_buffer = request.post_data_buffer
                                if post_data_buffer:
                                    body = post_data_buffer.decode('utf-8')
                            except:
                                pass
                        
                        # If still no body, try to get it from the request
                        if not body and method == 'POST':
                            try:
                                # Intercept the route to capture the body
                                pass  # Will handle in route interception
                            except:
                                pass
                        
                        self.find_body = body
                        print(f"\n{'='*80}")
                        print(f"🔍 Captured FIND endpoint:")
                        print(f"   Method: {method}")
                        print(f"   Full URL: {url}")
                        print(f"   Headers:")
                        for key, value in self.find_headers.items():
                            # Truncate sensitive values but show structure
                            if key.lower() in ['cookie', 'authorization', 'x-csrf-token']:
                                print(f"      {key}: {value[:50]}..." if len(str(value)) > 50 else f"      {key}: {value}")
                            else:
                                print(f"      {key}: {value}")
                        if self.find_body:
                            body_preview = self.find_body[:500] if len(self.find_body) > 500 else self.find_body
                            print(f"   Request Body ({len(self.find_body)} chars): {body_preview}")
                            if len(self.find_body) > 500:
                                print(f"      ... (truncated, {len(self.find_body) - 500} more chars)")
                        else:
                            print(f"   Request Body: None - will construct from dates")
                        print(f"{'='*80}\n")
            
            # Capture booking API call
            if any(keyword in url.lower() for keyword in ['book', 'schedule', 'assign', 'claim']):
                if method in ['POST', 'PUT']:
                    self.booking_endpoint = url
                    self.booking_method = method
                    self.booking_headers = dict(request.headers)
                    
                    # Try multiple methods to capture request body
                    body = None
                    try:
                        body = await request.post_data()
                    except:
                        pass
                    
                    if not body:
                        try:
                            post_data_buffer = request.post_data_buffer
                            if post_data_buffer:
                                body = post_data_buffer.decode('utf-8')
                        except:
                            pass
                    
                    self.booking_body = body
                    print(f"\n{'='*80}")
                    print(f"🔍 Captured BOOKING endpoint:")
                    print(f"   Method: {method}")
                    print(f"   Full URL: {url}")
                    print(f"   Headers:")
                    for key, value in self.booking_headers.items():
                        # Truncate sensitive values but show structure
                        if key.lower() in ['cookie', 'authorization', 'x-csrf-token']:
                            print(f"      {key}: {value[:50]}..." if len(str(value)) > 50 else f"      {key}: {value}")
                        else:
                            print(f"      {key}: {value}")
                    if self.booking_body:
                        body_preview = self.booking_body[:500] if len(self.booking_body) > 500 else self.booking_body
                        print(f"   Request Body ({len(self.booking_body)} chars): {body_preview}")
                        if len(self.booking_body) > 500:
                            print(f"      ... (truncated, {len(self.booking_body) - 500} more chars)")
                    else:
                        print(f"   Request Body: None")
                    print(f"{'='*80}\n")
        
        # Also intercept responses to see what was actually sent
        async def handle_response(response):
            url = response.url
            request = response.request
            
            # Check if this is a Find request and we don't have the body yet
            if any(keyword in url.lower() for keyword in ['selfschedule', 'search', 'find', 'shift', 'schedule']):
                if request.method == 'POST' and (not self.find_body or self.find_body is None):
                    # Try to get request body from the response's request object
                    try:
                        post_data = await request.post_data()
                        if post_data:
                            self.find_body = post_data
                            print(f"   ✅ Captured FIND request body from response: {post_data[:200]}...")
                    except:
                        pass
        
        page.on('request', handle_request)
        page.on('response', handle_response)
    
    def is_ready(self):
        """Check if we've captured the necessary endpoints"""
        return self.find_endpoint is not None


async def extract_cookies_from_page(page):
    """Extract cookies from Playwright page for use in direct HTTP requests"""
    cookies = await page.context.cookies()
    cookie_dict = {}
    for cookie in cookies:
        cookie_dict[cookie['name']] = cookie['value']
    return cookie_dict


async def search_shifts_via_api(session, api_capture, end_date_str, timeout=5000, start_date_str=None):
    """
    BOT COMPETITION: Search for shifts using direct API call (5-20ms vs 50-200ms)
    Returns shifts as JSON (much faster than DOM parsing)
    """
    if not api_capture.find_endpoint:
        return None, "No find endpoint captured"
    
    try:
        headers = dict(api_capture.find_headers) if api_capture.find_headers else {}
        # Remove stale Cookie header - let aiohttp.ClientSession manage cookies from Playwright session
        headers.pop('Cookie', None)
        headers.pop('cookie', None)
        
        # Ensure Content-Type is set for POST requests with JSON body
        if api_capture.find_method in ['POST', 'PUT']:
            if 'content-type' not in headers and 'Content-Type' not in headers:
                headers['Content-Type'] = 'application/json; charset=UTF-8'
        
        url = api_capture.find_endpoint
        body = api_capture.find_body
        
        # Modify body/params with start and end dates
        if api_capture.find_method == 'GET':
            separator = '&' if '?' in url else '?'
            if start_date_str:
                url = f"{url}{separator}startDate={start_date_str}&endDate={end_date_str}"
            else:
                url = f"{url}{separator}endDate={end_date_str}"
        else:
            # POST/PUT request - construct or modify body
            if not body:
                # Construct body from scratch if not captured
                body_json = {
                    'endDate': end_date_str
                }
                if start_date_str:
                    body_json['startDate'] = start_date_str
                body = json.dumps(body_json)
                print(f"   ⚠ Request body was None - constructed: {body}")
            else:
                # Modify existing body
                try:
                    body_json = json.loads(body) if isinstance(body, str) else (body if isinstance(body, dict) else {})
                    if isinstance(body_json, dict):
                        body_json['endDate'] = end_date_str
                        if start_date_str:
                            body_json['startDate'] = start_date_str
                        body = json.dumps(body_json)
                except Exception as e:
                    # If parsing fails, construct new body
                    print(f"   ⚠ Failed to parse existing body: {e} - constructing new body")
                    body_json = {
                        'endDate': end_date_str
                    }
                    if start_date_str:
                        body_json['startDate'] = start_date_str
                    body = json.dumps(body_json)
        
        print(f"\n📡 Sending FIND API request:")
        print(f"   Method: {api_capture.find_method}")
        print(f"   URL: {url}")
        print(f"   Modified Body: {body[:200] if body else 'None'}...")
        
        start_time = time.monotonic()
        
        # For POST/PUT with JSON, use json parameter; for GET or non-JSON, use data
        request_kwargs = {
            'method': api_capture.find_method,
            'url': url,
            'headers': headers,
            'timeout': aiohttp.ClientTimeout(total=timeout/1000)
        }
        
        if api_capture.find_method in ['POST', 'PUT'] and body:
            try:
                # Try to parse as JSON and use json parameter
                body_json = json.loads(body) if isinstance(body, str) else body
                request_kwargs['json'] = body_json
            except:
                # If not JSON, use data parameter
                request_kwargs['data'] = body
        elif api_capture.find_method == 'GET':
            # GET requests don't have body
            pass
        else:
            # Fallback: use data
            if body:
                request_kwargs['data'] = body
        
        async with session.request(**request_kwargs) as response:
            duration = (time.monotonic() - start_time) * 1000
            
            print(f"   Response Status: {response.status}")
            print(f"   Response Time: {duration:.1f}ms")
            
            if response.status == 200:
                result_json = await response.json()
                print(f"   Response Body (first 300 chars): {str(result_json)[:300]}...")
                
                # DEBUG: Print full response structure
                print(f"\n   🔍 FULL RESPONSE STRUCTURE:")
                print(f"   Response type: {type(result_json)}")
                if isinstance(result_json, dict):
                    print(f"   Top-level keys: {list(result_json.keys())}")
                    # Print each key and its type/sample
                    for key, value in result_json.items():
                        if isinstance(value, dict):
                            print(f"      {key}: dict with keys: {list(value.keys())[:10]}")
                        elif isinstance(value, list):
                            print(f"      {key}: list with {len(value)} items")
                            if value:
                                print(f"         First item type: {type(value[0])}")
                                if isinstance(value[0], dict):
                                    print(f"         First item keys: {list(value[0].keys())[:10]}")
                        else:
                            print(f"      {key}: {type(value).__name__} = {str(value)[:100]}")
                elif isinstance(result_json, list):
                    print(f"   Response is a list with {len(result_json)} items")
                    if result_json:
                        print(f"   First item type: {type(result_json[0])}")
                        if isinstance(result_json[0], dict):
                            print(f"   First item keys: {list(result_json[0].keys())[:10]}")
                
                print(f"   → ✅ API search complete (took {duration:.1f}ms)")
                return result_json, None
            else:
                result_text = await response.text()
                print(f"   Error Response: {result_text[:200]}...")
                return None, f"API returned {response.status}: {result_text[:100]}"
    except asyncio.TimeoutError:
        return None, "API request timeout"
    except Exception as e:
        return None, f"API error: {e}"


async def book_via_api_single(session, api_capture, shift_id, offset_ms=0, dry_run=True):
    """
    BOT COMPETITION: Single booking attempt via direct API call
    offset_ms: Small timing offset for parallel attempts
    """
    if not api_capture.booking_endpoint:
        return False, "No booking endpoint captured"
    
    if offset_ms > 0:
        await asyncio.sleep(offset_ms / 1000)
    
    if dry_run:
        return True, "Dry run"
    
    try:
        headers = dict(api_capture.booking_headers) if api_capture.booking_headers else {}
        # Remove stale Cookie header - let aiohttp.ClientSession manage cookies from Playwright session
        headers.pop('Cookie', None)
        headers.pop('cookie', None)
        body = api_capture.booking_body
        
        # Modify body with shift_id
        if body and shift_id:
            try:
                body_json = json.loads(body) if isinstance(body, str) else body
                if isinstance(body_json, dict):
                    # Try common field names
                    for field in ['shiftId', 'id', '_id', 'shift_id', 'shiftID']:
                        if field in body_json or not any(k in body_json for k in ['shiftId', 'id', '_id']):
                            body_json[field] = shift_id
                            break
                    body = json.dumps(body_json)
            except:
                pass
        
        print(f"   📤 Booking Request #{offset_ms//2 + 1}: {api_capture.booking_method} {api_capture.booking_endpoint}")
        print(f"      Body: {body[:200] if body else 'None'}...")
        
        start_time = time.monotonic()
        async with session.request(
            method=api_capture.booking_method,
            url=api_capture.booking_endpoint,
            headers=headers,
            data=body
        ) as response:
            duration = (time.monotonic() - start_time) * 1000
            result = await response.text()
            
            print(f"      Response: {response.status} ({duration:.1f}ms) - {result[:100]}...")
            
            if response.status in [200, 201]:
                return True, f"Success ({duration:.1f}ms)"
            else:
                return False, f"Failed {response.status} ({duration:.1f}ms)"
    except Exception as e:
        return False, str(e)


async def book_via_api_parallel(session, api_capture, shift_id, num_attempts=5, dry_run=True):
    """
    BOT COMPETITION: Parallel booking attempts for maximum success rate
    Sends multiple requests simultaneously with slight timing offsets
    """
    if not api_capture.booking_endpoint:
        return False, "No booking endpoint captured"
    
    if dry_run:
        print(f"    → DRY-RUN: would send {num_attempts} parallel booking requests")
        return True, "Dry run"
    
    print(f"\n🚀 Sending {num_attempts} parallel booking requests for shift_id={shift_id}")
    
    # Create parallel tasks with slight offsets (0ms, 2ms, 4ms, 6ms, 8ms)
    tasks = [
        book_via_api_single(session, api_capture, shift_id, offset_ms=i*2, dry_run=False)
        for i in range(num_attempts)
    ]
    
    start_time = time.monotonic()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    total_duration = (time.monotonic() - start_time) * 1000
    
    # Check if any succeeded
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            continue
        success, msg = result
        if success:
            print(f"    → ✅ BOOKED via API (attempt {i+1}, {total_duration:.1f}ms total)")
            return True, msg
    
    print(f"    → ✗ All {num_attempts} parallel attempts failed ({total_duration:.1f}ms)")
    return False, "All attempts failed"


async def try_book_shifts_hybrid(page, api_capture, end_date_str, dry_run=True, timeout=5000, parallel_attempts=5, target_drop_time=None, start_date_str=None):
    """
    BOT COMPETITION: Fastest booking method using direct API calls
    - Uses direct API calls (no DOM parsing) = 5-20ms
    - Parallel booking attempts for maximum success rate
    - Falls back to Playwright if API not available
    - target_drop_time: If provided, waits until this time before searching (for precise timing)
    """
    if not AIOHTTP_AVAILABLE:
        print("  → Falling back to Playwright (aiohttp not available)")
        return await try_book_shifts_optimized(page, dry_run, timeout)
    
    if not api_capture.is_ready():
        print("  → API endpoints not captured, falling back to Playwright")
        return await try_book_shifts_optimized(page, dry_run, timeout)
    
    # If target_drop_time is provided, wait until shifts have actually dropped
    if target_drop_time:
        now = datetime.now()
        if now < target_drop_time:
            wait_seconds = (target_drop_time - now).total_seconds()
            print(f"  ⏳ Waiting {wait_seconds:.2f}s until shifts drop at {target_drop_time.strftime('%H:%M:%S')}...")
            await asyncio.sleep(wait_seconds)
        elif (now - target_drop_time).total_seconds() < 1:
            # Just dropped, wait a tiny bit for server to process
            await asyncio.sleep(0.1)
    
    print(f"\n{'='*80}")
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 🚀 BOT COMPETITION MODE: Direct API calls...")
    if start_date_str:
        print(f"   Start Date: {start_date_str}")
    print(f"   End Date: {end_date_str}")
    print(f"   Target Drop Time: {target_drop_time.strftime('%H:%M:%S') if target_drop_time else 'N/A'}")
    print(f"   Parallel Attempts: {parallel_attempts}")
    print(f"{'='*80}")
    
    cookies = await extract_cookies_from_page(page)
    print(f"   Extracted {len(cookies)} cookies from session")
    
    async with aiohttp.ClientSession(cookies=cookies) as session:
        # Step 1: Search for shifts via API (ultra-fast)
        start_time = time.monotonic()
        shifts_data, error = await search_shifts_via_api(session, api_capture, end_date_str, timeout, start_date_str)
        
        if error:
            print(f"    ⚠ API search failed: {error}")
            print("    → Falling back to Playwright...")
            return await try_book_shifts_optimized(page, dry_run, timeout)
        
        if not shifts_data:
            elapsed = (time.monotonic() - start_time) * 1000
            print(f"    ⏱ No shifts found via API after {elapsed:.0f}ms")
            return False
        
        # Step 2: Parse shifts from JSON (instant, no DOM parsing)
        shifts = []
        if isinstance(shifts_data, list):
            shifts = shifts_data
        elif isinstance(shifts_data, dict):
            # Try multiple possible locations for shifts
            shifts = (
                shifts_data.get('shifts') or
                shifts_data.get('data') or
                shifts_data.get('items') or
                shifts_data.get('results') or
                []
            )
            
            # If not found at top level, check nested structures
            if not shifts:
                # Check pcContextString if it exists
                if 'pcContextString' in shifts_data:
                    pc_context = shifts_data['pcContextString']
                    if isinstance(pc_context, dict):
                        shifts = (
                            pc_context.get('shifts') or
                            pc_context.get('data') or
                            pc_context.get('items') or
                            pc_context.get('results') or
                            []
                        )
                        # Check nested dsContext
                        if not shifts and 'dsContext' in pc_context:
                            ds_context = pc_context['dsContext']
                            if isinstance(ds_context, dict):
                                shifts = (
                                    ds_context.get('shifts') or
                                    ds_context.get('data') or
                                    ds_context.get('items') or
                                    ds_context.get('results') or
                                    []
                                )
                                # Check ttContext array
                                if not shifts and 'ttContext' in ds_context:
                                    tt_context = ds_context['ttContext']
                                    if isinstance(tt_context, list) and tt_context:
                                        # Look for shift data in context items
                                        for item in tt_context:
                                            if isinstance(item, dict):
                                                # Check if this item contains shift data
                                                if 'shifts' in item or 'data' in item:
                                                    shifts = item.get('shifts') or item.get('data') or []
                                                    if shifts:
                                                        break
            
            # Check pcOutDataSetString for shifts (this is where Celayix actually stores shifts!)
            if not shifts and 'pcOutDataSetString' in shifts_data:
                pc_out_data = shifts_data['pcOutDataSetString']
                print(f"   🔍 Checking pcOutDataSetString structure...")
                if isinstance(pc_out_data, dict):
                    print(f"      pcOutDataSetString keys: {list(pc_out_data.keys())}")
                    # Check dsShiftGroups - this is the actual shifts array
                    if 'dsShiftGroups' in pc_out_data:
                        ds_shift_groups = pc_out_data['dsShiftGroups']
                        print(f"      dsShiftGroups type: {type(ds_shift_groups)}")
                        if isinstance(ds_shift_groups, list):
                            shifts = ds_shift_groups
                            print(f"      ✅ Found shifts as list in dsShiftGroups: {len(shifts)} items")
                        elif isinstance(ds_shift_groups, dict):
                            print(f"      dsShiftGroups dict keys: {list(ds_shift_groups.keys())}")
                            # Might be nested further - check common keys
                            shifts = (
                                ds_shift_groups.get('shifts') or
                                ds_shift_groups.get('data') or
                                ds_shift_groups.get('items') or
                                ds_shift_groups.get('ttShiftGroups') or
                                []
                            )
                            # If still not found, check if it's a dict with array values
                            if not shifts:
                                for key, value in ds_shift_groups.items():
                                    if isinstance(value, list) and value:
                                        shifts = value
                                        print(f"      ✅ Found shifts in pcOutDataSetString['dsShiftGroups']['{key}']: {len(shifts)} items")
                                        break
                                    elif isinstance(value, dict):
                                        # Check one level deeper
                                        for sub_key, sub_value in value.items():
                                            if isinstance(sub_value, list) and sub_value:
                                                shifts = sub_value
                                                print(f"      ✅ Found shifts in pcOutDataSetString['dsShiftGroups']['{key}']['{sub_key}']: {len(shifts)} items")
                                                break
                                        if shifts:
                                            break
        
        print(f"   Found {len(shifts)} shift(s) in API response")
        if shifts:
            print(f"   First shift data: {str(shifts[0])[:200]}...")
            # DEBUG: Print structure of first shift
            if isinstance(shifts[0], dict):
                print(f"   First shift keys: {list(shifts[0].keys())[:20]}")
        
        if not shifts:
            print(f"    ⚠ No shifts in API response")
            print(f"    🔍 DEBUG: Full response structure printed above - check where shifts might be located")
            return False
        
        # Step 3: Get first shift ID
        first_shift = shifts[0]
        shift_id = None
        if isinstance(first_shift, dict):
            # Try multiple possible field names for shift ID
            shift_id = (
                first_shift.get('shiftid') or  # Celayix uses lowercase 'shiftid'
                first_shift.get('id') or
                first_shift.get('shiftId') or
                first_shift.get('_id') or
                first_shift.get('shift_id') or
                first_shift.get('ShiftID') or
                first_shift.get('ShiftId')
            )
            print(f"   Extracted shift_id: {shift_id}")
            print(f"   Full shift keys: {list(first_shift.keys())}")
        
        if not shift_id:
            print(f"    ⚠ Could not extract shift ID from API response")
            print(f"    → Response keys: {list(first_shift.keys()) if isinstance(first_shift, dict) else 'not a dict'}")
            print(f"    Full first shift object: {first_shift}")
            return await try_book_shifts_optimized(page, dry_run, timeout)
        
        # Step 4: Book via API with parallel attempts (maximum speed + success rate)
        elapsed = (time.monotonic() - start_time) * 1000
        print(f"    ⚡ Shift found via API in {elapsed:.1f}ms: ID={shift_id}")

        # Display detailed shift information
        print(f"\n    📋 SHIFT INFORMATION:")
        print(f"    {'='*60}")
        if isinstance(first_shift, dict):
            # Common Celayix shift fields (case-insensitive lookup)
            shift_info = {}
            for key, value in first_shift.items():
                key_lower = key.lower()
                # Map common field names
                if key_lower in ['shiftid', 'id', 'shift_id', '_id']:
                    shift_info['Shift ID'] = value
                elif key_lower in ['shdate', 'date', 'shiftdate', 'shift_date']:
                    shift_info['Date'] = value
                elif key_lower in ['tmstart', 'starttime', 'start_time', 'time_start', 'start']:
                    shift_info['Start Time'] = value
                elif key_lower in ['tmend', 'endtime', 'end_time', 'time_end', 'end']:
                    shift_info['End Time'] = value
                elif key_lower in ['location', 'loc', 'locationname', 'location_name']:
                    shift_info['Location'] = value
                elif key_lower in ['role', 'position', 'job', 'jobtitle', 'job_title', 'title']:
                    shift_info['Role'] = value
                elif key_lower in ['department', 'dept', 'departmentname', 'department_name']:
                    shift_info['Department'] = value
                elif key_lower in ['payrate', 'pay_rate', 'rate', 'hourlyrate', 'hourly_rate', 'wage']:
                    shift_info['Pay Rate'] = value
                elif key_lower in ['hours', 'duration', 'length']:
                    shift_info['Hours'] = value
                elif key_lower in ['shifttype', 'shift_type', 'type']:
                    shift_info['Shift Type'] = value
                elif key_lower in ['description', 'desc', 'notes']:
                    shift_info['Description'] = value
            
            # Print mapped fields
            for label, value in shift_info.items():
                print(f"    {label:15}: {value}")
            
            # Print any remaining fields that weren't mapped
            printed_keys = set()
            for key in ['shiftid', 'id', 'shift_id', '_id', 'shdate', 'date', 'shiftdate', 'shift_date',
                       'tmstart', 'starttime', 'start_time', 'time_start', 'start',
                       'tmend', 'endtime', 'end_time', 'time_end', 'end',
                       'location', 'loc', 'locationname', 'location_name',
                       'role', 'position', 'job', 'jobtitle', 'job_title', 'title',
                       'department', 'dept', 'departmentname', 'department_name',
                       'payrate', 'pay_rate', 'rate', 'hourlyrate', 'hourly_rate', 'wage',
                       'hours', 'duration', 'length', 'shifttype', 'shift_type', 'type',
                       'description', 'desc', 'notes']:
                printed_keys.add(key.lower())
            
            remaining_fields = {k: v for k, v in first_shift.items() if k.lower() not in printed_keys}
            if remaining_fields:
                print(f"\n    Additional Fields:")
                for key, value in remaining_fields.items():
                    # Truncate long values for readability
                    value_str = str(value)
                    if len(value_str) > 50:
                        value_str = value_str[:47] + "..."
                    print(f"    {key:15}: {value_str}")
        else:
            print(f"    Raw shift data: {first_shift}")
        
        print(f"    {'='*60}\n")

        print(f"    ✅ Shift ID captured successfully: {shift_id}")
        print(f"    🛑 Skipping booking (test mode)")
        return True
        
        booking_success, booking_result = await book_via_api_parallel(
            session, api_capture, shift_id, num_attempts=parallel_attempts, dry_run=dry_run
        )
        
        if booking_success:
            total_duration = (time.monotonic() - start_time) * 1000
            print(f"    ✅ TOTAL BOT-COMPETITION TIME: {total_duration:.1f}ms (API + Parallel)")
            return True
        else:
            print(f"    ⚠ API booking failed, falling back to Playwright...")
            return await try_book_shifts_optimized(page, dry_run, timeout)


async def capture_api_endpoints(page, api_capture):
    """
    Capture API endpoints by doing a test Find click and intercepting network requests.
    This allows us to use direct API calls for bot competition.
    """
    print("🔍 BOT COMPETITION: Capturing API endpoints...")
    
    await api_capture.setup_interception(page)
    
    find_btn = await page.query_selector('button.cx-button[type="submit"]')
    if not find_btn:
        print("  ✗ Could not find 'Find' button for endpoint capture")
        return False
    
    is_disabled = await find_btn.is_disabled()
    if is_disabled:
        print("  ⏳ Waiting for Find button cooldown before capturing endpoints...")
        ready = await wait_for_find_button_ready(page, timeout=15000)
        if not ready:
            print("  ✗ Timeout waiting for button")
            return False
    
    print("  → Performing test Find click to capture API endpoint...")
    await find_btn.click()
    
    # Wait for requests to be captured (increase wait time for reliability)
    await asyncio.sleep(3)
    
    # Also wait for network to be idle
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except:
        pass
    
    if api_capture.is_ready():
        print(f"\n{'='*80}")
        print(f"✅ API ENDPOINT CAPTURE SUMMARY:")
        print(f"   FIND Endpoint: {api_capture.find_method} {api_capture.find_endpoint}")
        if api_capture.booking_endpoint:
            print(f"   BOOKING Endpoint: {api_capture.booking_method} {api_capture.booking_endpoint}")
        else:
            print(f"   ⚠ BOOKING Endpoint: Not captured yet (will be captured when you click 'Schedule me')")
        print(f"{'='*80}\n")
        return True
    else:
        print("  ⚠ Could not capture API endpoints (may not be available)")
        print("  → Will fall back to Playwright-only mode (slower)")
        return False


async def try_book_shifts_optimized(page, dry_run=True, timeout=5000):
    """
    OPTIMIZED for hot-drop scenarios:
    - Aggressively polls for shift cards (every 10-50ms)
    - Clicks first matching shift IMMEDIATELY when it appears
    - Pre-prepared selectors for minimal latency
    - No parsing all shifts first - click-first strategy
    """
    start_time = time.monotonic()
    card_selector = "div.cx-list-item.pointer-cursor"
    schedule_btn_selector = "button.cx-button:has-text('Schedule me')"
    
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] 🔥 HOT-DROP MODE: Aggressively watching for shifts...")
    
    # Aggressive polling: check every 10ms for maximum speed
    while (time.monotonic() - start_time) * 1000 < timeout:
        # Check for shift cards immediately
        cards = await page.query_selector_all(card_selector)
        
        if cards:
            # Found shift(s)! Click the first one immediately
            first_card = cards[0]
            
            # Quick validation: get basic info
            try:
                card_text = (await first_card.inner_text()).strip()
                print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] ⚡ SHIFT DETECTED: {card_text[:60]}...")
            except:
                pass
            
            if dry_run:
                print(f"    → DRY-RUN: would click shift card + 'Schedule me'")
                return True
            
            # Click shift card IMMEDIATELY
            click_start = time.monotonic()
            await first_card.click()
            click_duration = (time.monotonic() - click_start) * 1000
            print(f"    → CLICKED shift card (took {click_duration:.1f}ms)")
            
            # Immediately look for Schedule me button (already have selector ready)
            schedule_btn = await page.query_selector(schedule_btn_selector)
            if schedule_btn:
                schedule_start = time.monotonic()
                await schedule_btn.click()
                schedule_duration = (time.monotonic() - schedule_start) * 1000
                total_duration = (time.monotonic() - start_time) * 1000
                print(f"    → CLICKED 'Schedule me' (took {schedule_duration:.1f}ms)")
                print(f"    ✅ TOTAL BOOKING TIME: {total_duration:.1f}ms from shift appearance")
                return True
            else:
                print(f"    ⚠ Shift clicked but 'Schedule me' button not found yet")
                # Fallback: wait a tiny bit and retry
                await asyncio.sleep(0.05)
                schedule_btn = await page.query_selector(schedule_btn_selector)
                if schedule_btn:
                    await schedule_btn.click()
                    print(f"    → CLICKED 'Schedule me' (retry successful)")
                    return True
                else:
                    print(f"    ✗ Could not find 'Schedule me' button")
                    return False
        
        # No shifts yet - wait 1-2ms and check again (ultra-aggressive polling for bot competition)
        await asyncio.sleep(0.001)  # 1ms polling for maximum speed
    
    # Timeout - no shifts found
    elapsed = (time.monotonic() - start_time) * 1000
    print(f"    ⏱ No shifts found after {elapsed:.0f}ms of aggressive polling")
    return False


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

    # Try multiple selectors for shift cards
    card_selectors = [
        "div.cx-list-item.pointer-cursor",
        "div.cx-list-item[class*='pointer-cursor']",
        ".cx-list-item.pointer-cursor",
        ".cx-list-item[class*='pointer-cursor']",
        "div.cx-list-item"  # Fallback: any cx-list-item
    ]
    
    # Wait a bit for shifts to load after Find click
    await asyncio.sleep(0.5)
    
    cards = []
    for selector in card_selectors:
        cards = await page.query_selector_all(selector)
        if cards:
            print(f"DEBUG: found {len(cards)} clickable shift cards using selector: {selector}")
            break
    
    if cards:
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






async def apply_end_date_and_find(page, days_ahead=6, start_date=None, end_date=None):
    """
    Set start and end dates, then click Find
    start_date: date object or None (defaults to today)
    end_date: date object or None (defaults to today + days_ahead)
    """
    # Set end date (default to today + days_ahead if not provided)
    if end_date is None:
        end_date = get_next_week_end(days_ahead)
    end_str = format_celayix_date_short(end_date)

    # Set start date (default to today if not provided)
    if start_date is None:
        start_date = date.today()
    start_str = format_celayix_date_short(start_date)

    # Set start date
    await page.fill('#selfschedule-startDate', start_str)
    
    # Set end date
    await page.fill('#selfschedule-endDate', end_str)

    # Click the Find button
    await page.click('button.cx-button[type="submit"]')
    await page.wait_for_load_state("networkidle")

    print(f"Set start date to {start_str}, end date to {end_str} and clicked Find")


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


async def test_full_flow_dummy(dry_run=True, max_cycles=5, target_time=None, click_before_seconds=5, start_date=None, end_date=None, days_ahead=6):
    """
    Simulates the COMPLETE automation flow using the dummy page:
    - Login
    - Navigate to self-schedule page
    - Set end date
    - If target_time: Wait until precise time, then click Find
    - If no target_time: Initial Find click, then loop
    - Wait for loading
    - Try to book shifts
    
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
        
        # Step 4: Set start and end dates (but don't click Find yet)
        print("\nSTEP 4: Set Dates")
        print("  → Setting dates...")
        await asyncio.sleep(1)  # Delay before setting date
        # Use provided end_date or default to today + days_ahead
        if end_date is None:
            end_date = get_next_week_end(days_ahead)
        end_str = format_celayix_date_short(end_date)
        # Use provided start_date or default to today
        if start_date is None:
            start_date = date.today()
        start_str = format_celayix_date_short(start_date)
        await page.fill('#selfschedule-startDate', start_str)
        await page.fill('#selfschedule-endDate', end_str)
        print(f"  ✓ Start date set to {start_str}, end date set to {end_str}\n")

        if target_time:
            # BOT COMPETITION: Try to capture API endpoints for direct HTTP calls
            api_capture = APIEndpointCapture()
            hybrid_mode_available = False
            
            if AIOHTTP_AVAILABLE:
                print("🔬 BOT COMPETITION: Attempting to capture API endpoints...")
                hybrid_mode_available = await capture_api_endpoints(page, api_capture)
                if hybrid_mode_available:
                    print("  ✅ Bot competition mode enabled - using direct API calls (5-20ms)")
                    print("  ✅ Parallel booking attempts for maximum success rate\n")
                else:
                    print("  ⚠ Bot competition mode unavailable - using Playwright-only (50-200ms)\n")
            else:
                print("  ⚠ aiohttp not installed - using Playwright-only mode (slower)\n")
                print("  💡 Install with: pip install aiohttp for bot competition mode\n")
            
            # Precise timing mode: wait until target time, then click Find
            print(f"🎯 PRECISE TIMING MODE (BOT COMPETITION OPTIMIZED)")
            print(f"   Target time (when shifts drop): {target_time}")
            print(f"   Will click 'Find' {click_before_seconds} seconds before")
            print(f"   Current time: {datetime.now().strftime('%H:%M:%S')}\n")
            
            # Wait until the precise moment and click Find (or call API directly)
            success = await click_find_at_precise_time(
                page, target_time, click_before_seconds, 
                use_api=hybrid_mode_available, api_capture=api_capture, end_date_str=end_str
            )
            if success:
                # BOT COMPETITION: Use direct API calls if available, else fall back to Playwright
                if hybrid_mode_available:
                    print("\n📋 BOT COMPETITION MODE: Using direct API calls + parallel attempts...")
                    # Parse target_time to datetime for waiting
                    target_drop_datetime = parse_target_time(target_time)
                    booking_success = await try_book_shifts_hybrid(
                        page, api_capture, end_str, dry_run=dry_run, timeout=5000, 
                        parallel_attempts=5, target_drop_time=target_drop_datetime, start_date_str=start_str
                    )
                else:
                    # OPTIMIZED: Start watching for shifts IMMEDIATELY (parallel waiting)
                    print("\n📋 HOT-DROP MODE: Aggressively watching for shifts (no networkidle wait)...")
                    booking_success = await try_book_shifts_optimized(page, dry_run=dry_run, timeout=5000)
                
                if booking_success:
                    print(f"\n✅ BOT COMPETITION BOOKING COMPLETED!")
                else:
                    print(f"\n⚠ No shifts found in hot-drop window")
                
                # Keep browser open to see results
                print("\n⏸ Browser will stay open for 10 seconds to view results...")
                await asyncio.sleep(10)
                await browser.close()
                return
            else:
                print(f"\n✗ Precise timing search failed")
                await browser.close()
                return
        
        # Normal mode: do initial search and loop
        print("\nSTEP 5: Initial Search")
        print("  → Clicking 'Find' button for initial search...")
        await page.click('button.cx-button[type="submit"]')
        
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
        
        # Step 6: Try to book shifts (initial attempt)
        print("\nSTEP 6: Attempt to Book Shifts (Initial)")
        await try_book_shifts(page, dry_run=dry_run)
        
        # Track search time for cooldown
        last_search_time = time.monotonic()
        cycle_count = 0

        # Step 7: Main loop - repeat search and booking
        print("\n" + "="*70)
        print("STEP 7: Main Loop - Continuous Search and Booking")
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
    Accounts for system delays to click exactly when clock shows the target second.
    """
    now = datetime.now()
    click_time = target_time - timedelta(seconds=click_before_seconds)
    
    if click_time < now:
        print(f"⚠ Target time {target_time.strftime('%H:%M:%S')} has already passed")
        return False
    
    wait_seconds = (click_time - now).total_seconds()
    print(f"⏰ Waiting {wait_seconds:.2f} seconds until {click_time.strftime('%H:%M:%S')} (shifts drop at {target_time.strftime('%H:%M:%S')})")
    
    # Wait with high precision, accounting for system delays
    if wait_seconds > 0.5:
        # For longer waits, use asyncio.sleep but leave a smaller buffer for precision
        # Reduced buffer significantly to get much closer before busy-waiting
        await asyncio.sleep(max(0, wait_seconds - 0.4))
    
    # Fine-tune to get as close as possible to the target
    # Account for click execution delay and system delays by aiming earlier
    # 
    # ADJUSTMENT GUIDE:
    # - If clicking too LATE: Increase precision_buffer (e.g., 1000ms, 1500ms)
    # - If clicking too EARLY: Decrease precision_buffer (e.g., 500ms, 300ms)
    # This is subtracted from click_time, so larger = clicks earlier
    precision_buffer = timedelta(milliseconds=4000)  # Adjust this if timing is still off (larger = clicks earlier)
    target_click_time = click_time - precision_buffer
    
    # More aggressive busy-wait for better precision
    while datetime.now() < target_click_time:
        remaining = (target_click_time - datetime.now()).total_seconds()
        if remaining > 0.01:  # Even smaller threshold for more precision
            await asyncio.sleep(min(remaining * 0.15, 0.01))  # Even smaller chunks for better precision
        else:
            # Very close, busy-wait for maximum precision
            while datetime.now() < target_click_time:
                pass
    
    return True


async def click_find_at_precise_time(page, target_time_str, click_before_seconds=2.1, use_api=False, api_capture=None, end_date_str=None):
    """
    Click the Find button at a precise time, just before shifts become available.
    BOT COMPETITION: Supports direct API calls for maximum speed.
    
    Args:
        page: Playwright page object
        target_time_str: Time when shifts become available (e.g., "13:12" or "1:12:00")
        click_before_seconds: How many seconds before target_time to click (default 2.1)
        use_api: If True and api_capture is ready, use direct API call instead of clicking
        api_capture: APIEndpointCapture instance for hybrid mode
        end_date_str: End date string for API calls
    """
    target_time = parse_target_time(target_time_str)
    if not target_time:
        print(f"✗ Invalid time format: {target_time_str}. Use format like '13:12' or '1:12:00'")
        return False
    
    # BOT COMPETITION: In API mode, we don't need to "click" - just return success
    # The actual search will happen in try_book_shifts_hybrid after shifts drop
    if use_api and api_capture and api_capture.is_ready() and AIOHTTP_AVAILABLE:
        # Just wait until the precise time (no API call needed here)
        # The search will happen in try_book_shifts_hybrid after shifts drop
        success = await wait_until_time(target_time, click_before_seconds)
        if not success:
            return False
        
        actual_time = datetime.now()
        print(f"🎯 Ready for bot competition mode at {actual_time.strftime('%H:%M:%S.%f')[:-3]}")
        print(f"✓ Will search via API after shifts drop at {target_time.strftime('%H:%M:%S')}")
        return True
    
    # FALLBACK: Use Playwright click
    find_btn = await page.query_selector('button.cx-button[type="submit"]')
    if not find_btn:
        print("✗ Could not find 'Find' button")
        return False
    
    is_disabled = await find_btn.is_disabled()
    if is_disabled:
        print(f"⚠ Find button is disabled, waiting for cooldown...")
        ready = await wait_for_find_button_ready(page, timeout=15000)
        if not ready:
            print("✗ Timeout waiting for button to be enabled")
            return False
    
    success = await wait_until_time(target_time, click_before_seconds)
    if not success:
        return False
    
    actual_click_time = datetime.now()
    print(f"🎯 Clicking 'Find' at {actual_click_time.strftime('%H:%M:%S.%f')[:-3]}")
    await find_btn.click()
    after_click_time = datetime.now()
    click_duration = (after_click_time - actual_click_time).total_seconds() * 1000
    print(f"✓ Find clicked! (took {click_duration:.1f}ms) Search active when shifts drop at {target_time.strftime('%H:%M:%S')}")
    return True


async def main_loop(dry_run=True, interval_sec=5, days_ahead=6, target_time=None, click_before_seconds=2.1, start_date=None, end_date=None):
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

        # Step 2: Set start and end dates (but don't click Find yet)
        print("📅 SETTING DATES")
        # Use provided end_date or default to today + days_ahead
        if end_date is None:
            end_date = get_next_week_end(days_ahead)
        end_str = format_celayix_date_short(end_date)
        # Use provided start_date or default to today
        if start_date is None:
            start_date = date.today()
        start_str = format_celayix_date_short(start_date)
        await page.fill('#selfschedule-startDate', start_str)
        await page.fill('#selfschedule-endDate', end_str)
        print(f"  ✓ Start date set to {start_str}, end date set to {end_str}\n")

        if target_time:
            # BOT COMPETITION: Try to capture API endpoints for direct HTTP calls
            api_capture = APIEndpointCapture()
            hybrid_mode_available = False
            
            if AIOHTTP_AVAILABLE:
                print("🔬 BOT COMPETITION: Attempting to capture API endpoints...")
                hybrid_mode_available = await capture_api_endpoints(page, api_capture)
                if hybrid_mode_available:
                    print("  ✅ Bot competition mode enabled - using direct API calls (5-20ms)")
                    print("  ✅ Parallel booking attempts for maximum success rate\n")
                else:
                    print("  ⚠ Bot competition mode unavailable - using Playwright-only (50-200ms)\n")
            else:
                print("  ⚠ aiohttp not installed - using Playwright-only mode (slower)\n")
                print("  💡 Install with: pip install aiohttp for bot competition mode\n")
            
            # Precise timing mode: wait until target time, then click Find
            print(f"🎯 PRECISE TIMING MODE (BOT COMPETITION OPTIMIZED)")
            print(f"   Target time (when shifts drop): {target_time}")
            print(f"   Will click 'Find' {click_before_seconds} seconds before")
            print(f"   Current time: {datetime.now().strftime('%H:%M:%S')}\n")
            
            # Wait until the precise moment and click Find (or call API directly)
            success = await click_find_at_precise_time(
                page, target_time, click_before_seconds, 
                use_api=hybrid_mode_available, api_capture=api_capture, end_date_str=end_str
            )
            if success:
                # BOT COMPETITION: Use direct API calls if available, else fall back to Playwright
                if hybrid_mode_available:
                    print("\n📋 BOT COMPETITION MODE: Using direct API calls + parallel attempts...")
                    # Parse target_time to datetime for waiting
                    target_drop_datetime = parse_target_time(target_time)
                    booking_success = await try_book_shifts_hybrid(
                        page, api_capture, end_str, dry_run=dry_run, timeout=5000, 
                        parallel_attempts=5, target_drop_time=target_drop_datetime, start_date_str=start_str
                    )
                else:
                    # OPTIMIZED: Start watching for shifts IMMEDIATELY (parallel waiting)
                    print("\n📋 HOT-DROP MODE: Aggressively watching for shifts (no networkidle wait)...")
                    booking_success = await try_book_shifts_optimized(page, dry_run=dry_run, timeout=5000)
                
                if booking_success:
                    print(f"\n✅ BOT COMPETITION BOOKING COMPLETED!")
                else:
                    print(f"\n⚠ No shifts found in hot-drop window")
            else:
                print(f"\n✗ Precise timing search failed")
        else:
            # Normal mode: continuous searching
            print("🔄 CONTINUOUS MODE")
            
            # BOT COMPETITION: Try to capture API endpoints for direct HTTP calls
            api_capture = APIEndpointCapture()
            hybrid_mode_available = False
            
            if AIOHTTP_AVAILABLE:
                print("🔬 BOT COMPETITION: Attempting to capture API endpoints...")
                hybrid_mode_available = await capture_api_endpoints(page, api_capture)
                if hybrid_mode_available:
                    print("  ✅ Bot competition mode enabled - using direct API calls (5-20ms)")
                    print("  ✅ Parallel booking attempts for maximum success rate\n")
                else:
                    print("  ⚠ Bot competition mode unavailable - using Playwright-only (50-200ms)\n")
            else:
                print("  ⚠ aiohttp not installed - using Playwright-only mode (slower)\n")
                print("  💡 Install with: pip install aiohttp for bot competition mode\n")
            
            # Do initial search
            await page.click('button.cx-button[type="submit"]')
            await page.wait_for_load_state("networkidle")
            
            # Wait a bit for shifts to load and for endpoints to be captured
            await asyncio.sleep(2)
            
            # Re-check if endpoints were captured after Find click (in case initial capture failed)
            if not hybrid_mode_available and api_capture.is_ready():
                print("  ✅ API endpoints captured after Find click - enabling bot competition mode!")
                hybrid_mode_available = True
            
            # Wait for API cooldown (5 seconds) before making API calls
            # The API has a 5-second rate limit, so we need to wait after clicking Find
            if hybrid_mode_available:
                print("  ⏳ Waiting 6 seconds for API cooldown (5s required + 1s buffer)...")
                await asyncio.sleep(6)
            
            # Try bot competition mode if available, else fall back to Playwright
            if hybrid_mode_available:
                print("\n📋 BOT COMPETITION MODE: Using direct API calls...")
                booking_success = await try_book_shifts_hybrid(
                    page, api_capture, end_str, dry_run=dry_run, timeout=5000, 
                    parallel_attempts=5, start_date_str=start_str
                )
                if not booking_success:
                    print("  → Falling back to Playwright mode...")
                    await try_book_shifts(page, dry_run=dry_run)
            else:
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
            
            # Wait a bit for shifts to load and for endpoints to be captured
            await asyncio.sleep(2)
            
            # Re-check if endpoints were captured after Find click (in case initial capture failed)
            if not hybrid_mode_available and api_capture.is_ready():
                print("  ✅ API endpoints captured after Find click - enabling bot competition mode!")
                hybrid_mode_available = True
            
            # Wait for API cooldown before making API calls (if using hybrid mode)
            # Note: We already wait 11+ seconds between searches, but add extra safety buffer
            if hybrid_mode_available:
                print("  ⏳ Waiting 6 seconds for API cooldown...")
                await asyncio.sleep(6)

            # Try bot competition mode if available, else fall back to Playwright
            if hybrid_mode_available:
                booking_success = await try_book_shifts_hybrid(
                    page, api_capture, end_str, dry_run=dry_run, timeout=5000, 
                    parallel_attempts=5, start_date_str=start_str
                )
                if not booking_success:
                    await try_book_shifts(page, dry_run=dry_run)
            else:
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
            target_time = None
            click_before_seconds = 2.1
            
            start_date_str = None
            end_date_str = None
            days_ahead = 6
            for arg in sys.argv:
                if arg.startswith("--cycles="):
                    try:
                        max_cycles = int(arg.split("=")[1])
                    except ValueError:
                        pass
                elif arg.startswith("--time="):
                    target_time = arg.split("=")[1]
                elif arg.startswith("--click-before="):
                    try:
                        click_before_seconds = float(arg.split("=")[1])
                    except ValueError:
                        pass
                elif arg.startswith("--start-date="):
                    start_date_str = arg.split("=")[1]
                elif arg.startswith("--end-date="):
                    end_date_str = arg.split("=")[1]
                elif arg.startswith("--days="):
                    try:
                        days_ahead = int(arg.split("=")[1])
                    except ValueError:
                        pass
            
            # Parse dates
            start_date = parse_start_date(start_date_str) if start_date_str else None
            end_date = parse_end_date(end_date_str, days_ahead) if end_date_str else None
            
            print(f"Running FULL FLOW test (simulates complete automation)")
            print(f"  - Dry run: {dry_run}")
            if target_time:
                print(f"  - Precise timing: {target_time} (click {click_before_seconds}s before)")
            else:
                print(f"  - Max cycles: {max_cycles} (0 = infinite)")
            if start_date:
                print(f"  - Start date: {format_celayix_date_short(start_date)}")
            if end_date:
                print(f"  - End date: {format_celayix_date_short(end_date)}")
            print()
            asyncio.run(test_full_flow_dummy(dry_run=dry_run, max_cycles=max_cycles, 
                                            target_time=target_time, click_before_seconds=click_before_seconds,
                                            start_date=start_date, end_date=end_date, days_ahead=days_ahead))
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
        click_before_seconds = 2.1
        start_date_str = None
        end_date_str = None
        
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
            elif arg.startswith("--start-date="):
                start_date_str = arg.split("=")[1]
            elif arg.startswith("--end-date="):
                end_date_str = arg.split("=")[1]
        
        # Parse dates
        start_date = parse_start_date(start_date_str) if start_date_str else None
        end_date = parse_end_date(end_date_str, days_ahead) if end_date_str else None
        
        # Display mode info
        if target_time:
            print(f"🎯 PRECISE TIMING MODE")
            print(f"   Target time: {target_time}")
            print(f"   Click before: {click_before_seconds} seconds")
            print(f"   Dry run: {dry_run}")
            if start_date:
                print(f"   Start date: {format_celayix_date_short(start_date)}")
            if end_date:
                print(f"   End date: {format_celayix_date_short(end_date)}")
            print()
        else:
            print(f"Running in continuous mode (dry_run={dry_run})")
            print(f"💡 Use --time=HH:MM to enable precise timing mode")
            if start_date:
                print(f"   Start date: {format_celayix_date_short(start_date)}")
            if end_date:
                print(f"   End date: {format_celayix_date_short(end_date)}")
            print()
        
        asyncio.run(main_loop(dry_run=dry_run, interval_sec=15, days_ahead=days_ahead, 
                              target_time=target_time, click_before_seconds=click_before_seconds,
                              start_date=start_date, end_date=end_date))
