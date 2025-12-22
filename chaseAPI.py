import asyncio
import datetime
import json
import os
import traceback
import psutil
import zendriver as uc
from zendriver import cdp
from zendriver.core import util
from zendriver.core.element import Element
from curl_cffi import requests
from dotenv import load_dotenv

from helperAPI import (
    Brokerage,
    getOTPCodeDiscord,
    printAndDiscord,
    printHoldings,
    stockOrder,
    maskString
)

load_dotenv()

# Controls detailed logging
DEBUG = os.getenv("CHASE_DEBUG", "false").lower() == "true"
COOKIES_PATH = "creds"

# --- URL Constants ---
LOGIN_URL = "https://secure05c.chase.com/web/auth/#/logon/logon/chaseOnline"
LANDING_PAGE = "https://secure.chase.com/web/auth/dashboard#/dashboard/overview"
TRADE_ENTRY_URL = "https://secure.chase.com/web/auth/dashboard#/dashboard/oi-trade/equity/entry"

# API Endpoints
API_ACCOUNT_LIST = "https://secure.chase.com/svc/rl/accounts/secure/v1/dashboard/module/list"
API_POSITIONS = "https://secure.chase.com/svc/wr/dwm/secure/gateway/investments/servicing/inquiry-maintenance/digital-investment-positions/v2/positions"

# Trading Endpoints
API_QUOTE = "https://secure.chase.com/svc/wr/dwm/secure/gateway/investments/servicing/inquiry-maintenance/digital-equity-quote/v1/quotes"
API_VALIDATE_BUY = "https://secure.chase.com/svc/wr/dwm/secure/gateway/investments/servicing/investor-servicing/digital-equity-trades/v1/buy-order-validations"
API_EXECUTE_BUY = "https://secure.chase.com/svc/wr/dwm/secure/gateway/investments/servicing/investor-servicing/digital-equity-trades/v1/buy-orders"
API_VALIDATE_SELL = "https://secure.chase.com/svc/wr/dwm/secure/gateway/investments/servicing/investor-servicing/digital-equity-trades/v1/sell-order-validations"
API_EXECUTE_SELL = "https://secure.chase.com/svc/wr/dwm/secure/gateway/investments/servicing/investor-servicing/digital-equity-trades/v1/sell-orders"

def log(message):
    if DEBUG:
        print(f"[CHASE DEBUG] {message}")

async def clean_existing_chrome_processes():  
    """Kill any existing Chrome processes that might be from previous crashes"""  
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):  
        try:  
            if proc.info['name'] and 'chrome' in proc.info['name'].lower():  
                cmdline = proc.info['cmdline']  
                if cmdline and any('--remote-debugging-port' in arg for arg in cmdline):  
                    print(f"Killing orphaned Chrome process: {proc.info['pid']}")  
                    proc.kill()  
        except (psutil.NoSuchProcess, psutil.AccessDenied):  
            pass

def create_creds_folder():
    if not os.path.exists(COOKIES_PATH):
        os.makedirs(COOKIES_PATH)

async def chase_error(error: str, page=None, discord_loop=None, browser=None):
    print(f"Chase Error: {error}")
    if page:
        try:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_name = f"chase_error_{timestamp}.png"
            await page.save_screenshot(filename=screenshot_name)
            
            html_name = f"chase_error_{timestamp}.html"
            content = await page.get_content()
            with open(html_name, "w", encoding="utf-8") as f:
                f.write(content)
                
            log(f"Debug artifacts saved: {screenshot_name}, {html_name}")
        except Exception as e:
            print(f"Failed to save debug artifacts: {e}")

    if discord_loop:
        printAndDiscord(f"Chase Error: {error}", discord_loop)

# ==========================================
# DOM Helpers
# ==========================================

async def find_shadow_element(page, selector):
    """Traverses Shadow DOM using zendriver native utils."""
    try:
        doc = await page.send(cdp.dom.get_document(-1, True))
        shadow_hosts = util.filter_recurse_all(
            doc, lambda n: hasattr(n, "shadow_roots") and bool(n.shadow_roots)
        )
        for host_node in shadow_hosts:
            if not host_node.shadow_roots: continue
            shadow_root_node = host_node.shadow_roots[0]
            shadow_element = Element(shadow_root_node, page, shadow_root_node)
            try:
                target = await shadow_element.query_selector(selector)
                if target: return target
            except Exception: continue
    except Exception:
        pass
    return None

async def js_click(element):
    try:
        await element.apply("e => e.click()")
        return True
    except Exception:
        return False

# ==========================================
# Main Logic
# ==========================================

def chase_run(orderObj=None, command=None, botObj=None, loop=None, CHASE_EXTERNAL=None, DOCKER=False, **kwargs):
    print("Starting Chase run process...")
    load_dotenv()
    create_creds_folder()
    
    # Create a new local event loop for this thread
    local_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(local_loop)
    
    discord_loop = loop

    if not os.getenv("CHASE") and CHASE_EXTERNAL is None:
        print("CHASE environment variable not found.")
        local_loop.close()
        return None

    accounts_env = (os.environ.get("CHASE", "") if CHASE_EXTERNAL is None else CHASE_EXTERNAL).strip().split(",")
    chase_brokerage_obj = Brokerage("CHASE")
    
    if command is None:
        action_to_perform = "_holdings"
    else:
        _, action_to_perform = command

    try:
        # Run the async process using the local_loop
        populated_obj = local_loop.run_until_complete(
            _async_chase_run_wrapper(
                accounts_env, chase_brokerage_obj, action_to_perform, botObj, discord_loop, orderObj, DOCKER
            )
        )
        if populated_obj and orderObj:
            orderObj.set_logged_in(populated_obj, 'chase')
        return populated_obj

    except Exception as e:
        print(f"Critical error in Chase run: {e}")
        traceback.print_exc()
        return chase_brokerage_obj
    finally:
        try:
            local_loop.close()
            log("Local event loop closed.")
        except Exception as e_loop:
            log(f"Error closing local loop: {e_loop}")

async def _async_chase_run_wrapper(accounts_env, brokerage_obj: Brokerage, action, botObj, discord_loop, orderObj, DOCKER=False):
    headless = os.getenv("HEADLESS", "true").lower() == "true"
    
    for acc_idx, account_cred_str in enumerate(accounts_env):
        account_name_key = f"Chase {acc_idx + 1}"
        browser = None
        page = None
        
        try:
            browser_args = []
            if DOCKER:
                browser_args.extend(["--disable-dev-shm-usage", "--disable-gpu", "--window-size=1920,1080"])
            elif headless:
                browser_args.extend(["--headless=new", "--window-size=1920,1080", 
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
                "--disable-blink-features=AutomationControlled",
                "--disable-site-isolation-trials",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-session-crashed-bubble",
                "--disable-infobars",
                "--disable-features=TranslateUI,VizDisplayCompositor",
                "--no-first-run",
                "--disable-default-apps",
                "--disable-extensions",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1920,1080"])
            else:
                browser_args.extend([  
                    "--start-maximized",  
                    "--disable-session-crashed-bubble",
                    "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
                    "--disable-blink-features=AutomationControlled",  
                    "--disable-infobars",  
                    "--disable-features=TranslateUI,VizDisplayCompositor",
                    "--no-first-run",  
                    "--disable-default-apps",
                    "--disable-extensions"
                ])

            profile_path = os.path.abspath(os.path.join(COOKIES_PATH, f"ZenChase_{acc_idx + 1}"))
            
            browser_args.append("--force-device-scale-factor=0.8")

            log(f"Starting browser for {account_name_key}...")
            await clean_existing_chrome_processes()
            browser = await uc.start(browser_args=browser_args, user_data_dir=profile_path)
            page = await browser.get(LOGIN_URL) if not browser.tabs else await browser.tabs[0].get(LOGIN_URL)

            # 1. Perform Browser Login (UI Interaction)
            creds = account_cred_str.split(":")
            success = await chase_login_ui(page, creds[0], creds[1], creds[2] if len(creds)>2 else "0000", account_name_key, botObj, discord_loop)
            if not success: raise Exception("Login failed.")

            brokerage_obj.set_logged_in_object(account_name_key, browser)

            # 2. Get Cookies & API Setup
            cookies = await browser.cookies.get_all()
            cookies_dict = {c.name: c.value for c in cookies}
            
            # API Step A: Get Accounts Map
            await fetch_accounts_api(cookies_dict, brokerage_obj, account_name_key, discord_loop)

            # API Step B: Action
            if action == "_holdings":
                await fetch_holdings_api(cookies_dict, brokerage_obj, account_name_key, discord_loop)
            elif action == "_transaction":
                log(f"Navigating to Trade UI for {account_name_key} to prime cookies...")
                await navigate_to_trade_context(page)
                
                # Refresh cookies after navigation
                cookies = await browser.cookies.get_all()
                cookies_dict = {c.name: c.value for c in cookies}
                
                await chase_transaction(cookies_dict, brokerage_obj, orderObj, account_name_key, discord_loop)

        except Exception as e:
            await chase_error(f"Error in {account_name_key}: {e}", page, discord_loop, browser)
        finally:  
            if browser:  
                try:  
                    await asyncio.sleep(2)
                    for tab in browser.tabs:  
                        try: await tab.close()  
                        except: pass  
                    await asyncio.sleep(1)
                    await browser.stop()  
                except Exception as e:  
                    log(f"Browser stop error: {e}")  
                try:  
                    if browser._process: browser._process.kill()  
                except: pass

    return brokerage_obj

# ==========================================
# Navigation & Login Logic
# ==========================================

async def chase_login_ui(page, username, password, last_four, name, botObj, discord_loop):
    log(f"Logging in via UI for {name}...")
    await page.sleep(3)

    if "dashboard" in page.url:
        log("Already logged in.")
        return True

    # 1. Enter Credentials
    try:
        user_box = await safe_find(page, "#userId-input-field-input", timeout=5)
        pass_box = await safe_find(page, "#password-input-field-input", timeout=5)
        
        if user_box and pass_box:
            await user_box.clear_input_by_deleting()
            await user_box.send_keys(username)
            await pass_box.send_keys(password)
            btn = await safe_find(page, "#signin-button", timeout=5)
            if btn: await btn.click()
            await page.sleep(5)
    except Exception as e:
        log(f"Cred entry error (ignorable if logged in): {e}")

    # 2FA LOOP: Check for various 2FA screens
    max_retries = 5  # Increased retries slightly
    for i in range(max_retries):
        if "dashboard" in page.url: return True
        if "esasiOptout" in page.url: return True
        
        log(f"2FA Check Cycle {i+1}/{max_retries}...")

        # Method 1: List Item (Get a text vs Get a call)
        list_sms = await safe_find(page, "#sms", timeout=2)
        if list_sms:
            log("Handling 'Confirm Identity' (List Mode)...")
            await handle_list_verification(page)
            await page.sleep(3)
            continue

        # Method 2: Radio Button (Choose phone number)
        radio_group = await safe_find(page, "#eligibleTextContacts", timeout=2)
        if radio_group:
            log("Handling 'Confirm Identity' (Radio Mode)...")
            await handle_radio_verification(page)
            await page.sleep(3)
            continue

        # Method 3: Dropdown (Unrecognized Device)
        dropdown = await safe_find(page, "#header-simplerAuth-dropdownoptions-styledselect", timeout=2)
        if dropdown:
            log("Handling 'Unrecognized Device' (Dropdown Mode)...")
            await handle_dropdown_verification(page)
            await page.sleep(3)
            continue

        # Method 4: Push Notification (New - Mobile App)
        push_notification = await safe_find(page, "#inAppSend", timeout=2)
        if push_notification:
            log("Handling 'Confirm using mobile app' (Push Mode)...")
            await handle_push_verification(page)
            
            # Wait loop: Wait up to 120 seconds for user to approve on phone
            log(f"Waiting 2 minutes for user approval for {name}...")
            if botObj and discord_loop:
                printAndDiscord(f"[{name}] Chase Mobile App Push sent. Please approve within 2 minutes.", discord_loop)
            
            # Check every 5 seconds if we are logged in
            for _ in range(24): 
                if "dashboard" in page.url:
                    log("Push notification approved, dashboard loaded!")
                    return True
                await page.sleep(5)
            continue

        # Method 5: OTP Input
        otp_input = await safe_find(page, "#otpInput", timeout=2)
        if otp_input:
            log("Handling OTP Input...")
            if botObj is None:
                print(f"\n[ACTION REQUIRED] Enter Chase 2FA Code for {name}: ")
                # Use run_in_executor with the CURRENT loop (which is the local_loop we set)
                code = await asyncio.get_event_loop().run_in_executor(None, input)
            else:
                future = asyncio.run_coroutine_threadsafe(
                    getOTPCodeDiscord(botObj, name, code_len=8, timeout=300, loop=discord_loop),
                    discord_loop
                )
                code = await asyncio.wrap_future(future)
            
            if code:
                await otp_input.send_keys(str(code))
                next_btn = await safe_find(page, "#next-content", timeout=5)
                if next_btn:
                    try: await next_btn.click()
                    except: await js_click(next_btn)
                await page.sleep(5)
            continue
        
        await page.sleep(1)

    return "dashboard" in page.url

# --- Helpers for finding elements safely ---
async def safe_find(page, selector, timeout=3):
    try:
        return await page.find(selector, timeout=timeout)
    except:
        return None

# --- 2FA Handlers (Using robust JS execution) ---

async def handle_list_verification(page):
    """Handles the 'Get a text' list item selection."""
    try:
        # Use JS to click directly, bypassing Element object issues
        await page.evaluate("""
            (function() {
                var el = document.querySelector('#sms');
                if (el) el.click();
            })();
        """)
        log("Clicked #sms via JS")
        
        await page.sleep(1)
        
        # Click Next button if it exists
        await page.evaluate("""
            (function() {
                var btn = document.querySelector('#next-content');
                if (btn) btn.click();
            })();
        """)
        log("Clicked #next-content via JS (if present)")

    except Exception as e:
        log(f"Error in list handler: {e}")

async def handle_radio_verification(page):
    """Handles the 'Choose your mobile number' radio selection."""
    try:
        # Click the first label that looks like a phone number mask
        await page.evaluate("""
            (function() {
                var labels = document.querySelectorAll('label');
                for (var i = 0; i < labels.length; i++) {
                    if (labels[i].textContent.includes('xxx-')) {
                        labels[i].click();
                        break;
                    }
                }
            })();
        """)
        log("Clicked phone number radio via JS")
        
        await page.sleep(1)
        
        await page.evaluate("""
            (function() {
                var btn = document.querySelector('#next-content');
                if (btn) btn.click();
            })();
        """)
        log("Clicked #next-content via JS")

    except Exception as e:
        log(f"Error in radio handler: {e}")

async def handle_dropdown_verification(page):
    """Handles the 'We don't recognize this device' dropdown."""
    try:
        await page.evaluate("""
            (function() {
                var trigger = document.querySelector('#header-simplerAuth-dropdownoptions-styledselect');
                if (trigger) trigger.click();
            })();
        """)
        await page.sleep(1)
        
        # Select first option in dropdown
        await page.evaluate("""
            (function() {
                var options = document.querySelectorAll('#ul-list-container-simplerAuth-dropdownoptions-styledselect a.option');
                for (var i = 0; i < options.length; i++) {
                    if (!options[i].className.includes('groupLabelContainer')) {
                        options[i].click();
                        break;
                    }
                }
            })();
        """)
        
        await page.sleep(1)
        
        await page.evaluate("""
            (function() {
                var btn = document.querySelector('#requestIdentificationCode');
                if (btn) btn.click();
            })();
        """)
    except Exception as e:
        log(f"Error in dropdown handler: {e}")

async def handle_push_verification(page):
    """Handles the 'Confirm using our mobile app' selection."""
    try:
        # Use JS to click directly
        await page.evaluate("""
            (function() {
                var el = document.querySelector('#inAppSend');
                if (el) el.click();
            })();
        """)
        log("Clicked #inAppSend via JS")
        
        await page.sleep(1)
        
        # Sometimes there is a 'Next' button after selecting, 
        # sometimes it sends immediately. We try clicking next just in case.
        await page.evaluate("""
            (function() {
                var btn = document.querySelector('#next-content');
                if (btn) btn.click();
            })();
        """)
    except Exception as e:
        log(f"Error in push handler: {e}")

async def navigate_to_trade_context(page):
    """Navigates to the trade entry page to prime session cookies."""
    try:
        log(f"Navigating to {TRADE_ENTRY_URL}")
        await page.get(TRADE_ENTRY_URL)
        await page.sleep(8) 
        
        if "entry" not in page.url and "dashboard" not in page.url:
            log("Warning: Might not be on trade page. Transactions might fail.")
    except Exception as e:
        log(f"Navigation error: {e}")

# ==========================================
# API Logic
# ==========================================

def get_base_headers():
    return {
        'accept': 'application/json, text/plain, */*',
        'content-type': 'application/json',
        'referer': 'https://secure.chase.com/web/auth/dashboard',
        'x-jpmc-csrf-token': 'NONE',
        'x-jpmc-channel': 'id=C30',
        'origin': 'https://secure.chase.com',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36'
    }

async def fetch_accounts_api(cookies, brokerage_obj: Brokerage, login_key, discord_loop):
    headers = get_base_headers()
    headers['content-type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
    data = 'context=WEB_CPO_OVERVIEW_DASHBOARD&selectorIdType=ACCOUNT_GROUP'

    try:
        response = requests.post(API_ACCOUNT_LIST, headers=headers, cookies=cookies, data=data, impersonate="chrome")
        if response.status_code != 200: return

        resp_json = response.json()
        cache = resp_json.get("cache", [])
        for item in cache:
            if "investmentAccountOverviews" in item.get("response", {}):
                details = item["response"]["investmentAccountOverviews"][0].get("investmentAccountDetails", [])
                for acct in details:
                    acc_id = str(acct.get("accountId"))
                    mask = acct.get("mask", "")
                    val = float(acct.get("accountValue", 0))
                    
                    if not hasattr(brokerage_obj, "_chase_id_map"): brokerage_obj._chase_id_map = {}
                    brokerage_obj._chase_id_map[mask] = acc_id
                    brokerage_obj.set_account_number(login_key, mask) 
                    brokerage_obj.set_account_totals(login_key, mask, val)
    except Exception as e:
        printAndDiscord(f"Chase API Error (Accounts): {e}", discord_loop)

async def fetch_holdings_api(cookies, brokerage_obj: Brokerage, login_key, discord_loop):
    if not hasattr(brokerage_obj, "_chase_id_map"): return
    headers = get_base_headers()

    for display_name, acc_id in brokerage_obj._chase_id_map.items():
        payload = {
            "selectorIdentifier": acc_id,
            "selectorCode": "ACCOUNT",
            "taxLotIndicator": False, "currencyCode": "", "voluntaryCorporateActionIndicator": False,
            "intradayUpdateIndicator": True, "pinnedPositionIndicator": True
        }
        try:
            response = requests.post(API_POSITIONS, headers=headers, cookies=cookies, json=payload, impersonate="chrome")
            if response.status_code != 200: continue
            
            data = response.json()
            for pos in data.get("positions", []):
                symbol = "UNKNOWN"
                if "Cash" in pos.get("instrumentLongName", ""): symbol = "CASH"
                else:
                    comps = pos.get("positionComponents", [])
                    if comps: symbol = comps[0].get("securityIdDetail", [{}])[0].get("symbolSecurityIdentifier", "UNKNOWN")
                
                qty = float(pos.get("tradedUnitQuantity", 0))
                price = float(pos.get("marketPrice", {}).get("baseValueAmount", 0))
                if symbol and qty > 0:
                    brokerage_obj.set_holdings(login_key, display_name, symbol, qty, price)
        except Exception: pass

    printHoldings(brokerage_obj, discord_loop)

# ==========================================
# Transaction Logic
# ==========================================

async def get_stock_quote(cookies, symbol):
    headers = get_base_headers()
    url = f"{API_QUOTE}?security-symbol-code={symbol}&security-validate-indicator=true&dollar-based-trading-include-indicator=true"
    
    try:
        response = requests.get(url, headers=headers, cookies=cookies, impersonate="chrome")
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        log(f"Quote error: {e}")
    return None

async def chase_transaction(cookies, brokerage_obj: Brokerage, orderObj: stockOrder, login_key, discord_loop):
    if not hasattr(brokerage_obj, "_chase_id_map"):
        log("No account mapping found for transaction.")
        return

    for mask, internal_id in brokerage_obj._chase_id_map.items():
        for symbol in orderObj.get_stocks():
            action = orderObj.get_action().upper()
            quantity = orderObj.get_amount()
            dry_run = orderObj.get_dry()
            
            log(f"Processing {action} {quantity} {symbol} for account {mask} ({internal_id})")
            printAndDiscord(f"{login_key}: {action.lower()}ing {quantity} of {symbol}", discord_loop)

            try:
                # 1. Get Quote
                quote_data = await get_stock_quote(cookies, symbol)
                if not quote_data:
                    printAndDiscord(f"{login_key} {mask}: Skipping {symbol}: Could not fetch quote.", discord_loop)
                    continue
                
                current_price = float(quote_data.get("lastTradePriceAmount", 0))
                if current_price == 0:
                     if action == "BUY": current_price = float(quote_data.get("askPriceAmount", 0))
                     else: current_price = float(quote_data.get("bidPriceAmount", 0))

                log(f"Quote for {symbol}: {current_price}")

                if dry_run:
                    printAndDiscord(f"{login_key} {mask}: [DRY RUN] Would {action} {quantity} of {symbol} @ ~${current_price}", discord_loop)
                    continue

                # 2. Execute
                result = await execute_trade_api(cookies, internal_id, symbol, action, quantity, current_price, discord_loop, login_key, mask)
                
                if result:
                    printAndDiscord(f"{login_key}: {action} {quantity} of {symbol} in {mask}: Success", discord_loop)
                else:
                    printAndDiscord(f"{login_key}: Failed to execute {action} for {symbol} in {mask}", discord_loop)

            except Exception as e:
                printAndDiscord(f"{login_key} {mask}: Error trading {symbol}: {e}", discord_loop)
                traceback.print_exc()

async def execute_trade_api(cookies, account_id, symbol, action, quantity, current_price, discord_loop, login_key, mask):
    headers = get_base_headers()
    
    if action == "BUY":
        url_validate = API_VALIDATE_BUY
        url_execute = API_EXECUTE_BUY
    elif action == "SELL":
        url_validate = API_VALIDATE_SELL
        url_execute = API_EXECUTE_SELL
    else:
        return False

    # --- AGGRESSIVE PRICING LOGIC ---
    if current_price > 1.00:
        order_type = "MARKET"
        limit_price = None
        printAndDiscord(f"{login_key} {mask}: Price ${current_price} > $1.00. Using MARKET order.", discord_loop)
    else:
        order_type = "LIMIT"
        if action == "BUY":
            limit_price = round(current_price + 0.01, 2)
        else:
            limit_price = round(current_price - 0.01, 2)
            if limit_price < 0.01: limit_price = 0.01
        printAndDiscord(f"{login_key} {mask}: Price ${current_price} < $1.00. Using LIMIT order @ ${limit_price} (Aggressive).", discord_loop)

    payload_validate = {
        "accountIdentifier": int(account_id),
        "marketPriceAmount": current_price, 
        "orderQuantity": quantity,
        "accountTypeCode": "CASH",
        "timeInForceCode": "DAY",
        "securitySymbolCode": symbol,
        "tradeChannelName": "DESKTOP",
        "dollarBasedTradingEligibleIndicator": False,
        "orderTypeCode": order_type
    }

    if order_type == "LIMIT":
        payload_validate["limitPriceAmount"] = limit_price

    if action == "SELL":
        payload_validate["tradeActionName"] = "SELL"

    log(f"Validating order: {json.dumps(payload_validate)}")
    
    try:
        # STEP 1: VALIDATION
        resp_val = requests.post(url_validate, headers=headers, cookies=cookies, json=payload_validate, impersonate="chrome")
        
        if resp_val.status_code != 200:
            log(f"Validation Failed ({resp_val.status_code}): {resp_val.text}")
            printAndDiscord(f"{login_key} {mask}: Trade Validation Failed: {resp_val.text[:200]}", discord_loop)
            return False
            
        val_data = resp_val.json()

        # --- HARD STOP CHECK for RSA BLOCKS ---
        error_msgs = val_data.get("tradeErrorMessages", [])
        for err in error_msgs:
            if "pending a corporate action" in err or "R02675A" in err:
                log(f"HARD STOP triggered by error: {err}")
                printAndDiscord(f"{login_key} {mask} BLOCKED: {symbol} is pending corporate action/RSA. Trade aborted.", discord_loop)
                return False

        exchange_id = val_data.get("financialInformationExchangeSystemOrderIdentifier")
        
        if not exchange_id:
            log(f"Validation passed but no Exchange ID returned: {val_data}")
            return False
            
        log(f"Validation successful. Exchange ID: {exchange_id}")

        # STEP 2: EXECUTION
        payload_execute = payload_validate.copy()
        payload_execute["financialInformationExchangeSystemOrderIdentifier"] = exchange_id
        
        resp_exec = requests.post(url_execute, headers=headers, cookies=cookies, json=payload_execute, impersonate="chrome")
        
        if resp_exec.status_code != 200:
            log(f"Execution Failed ({resp_exec.status_code}): {resp_exec.text}")
            printAndDiscord(f"{login_key} {mask}: Execution Failed: {resp_exec.text[:200]}", discord_loop)
            return False
            
        exec_data = resp_exec.json()
        order_id = exec_data.get("orderIdentifier")
        log(f"Order Executed. ID: {order_id}")
        return True

    except Exception as e:
        log(f"API Exception during trade: {e}")
        return False
