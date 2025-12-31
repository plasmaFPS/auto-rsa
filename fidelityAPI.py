import asyncio
import datetime
import psutil  
import os
import traceback
import json
import re
import csv
import glob
import math
import pyotp
import pytz
import zendriver as uc
from zendriver.core.keys import KeyEvents, SpecialKeys, KeyModifiers
from zendriver import cdp
from zendriver import KeyPressEvent  
from zendriver.core import util
from zendriver.core.element import Element
from dotenv import load_dotenv
import random  

from helperAPI import (
    Brokerage,
    getOTPCodeDiscord,
    printAndDiscord,
    printHoldings,
    stockOrder,
    maskString,
)

load_dotenv()

# Controls detailed logging
DEBUG = os.getenv("FIDELITY_DEBUG", "false").lower() == "true"
COOKIES_PATH = "creds"

# --- URL Constants ---
LOGIN_URL = "https://digital.fidelity.com/prgw/digital/login/full-page?AuthRedUrl=https://digital.fidelity.com/ftgw/digital/portfolio/summary"
LANDING_PAGE = "https://digital.fidelity.com/ftgw/digital/portfolio/summary"
POSITIONS_URL = "https://digital.fidelity.com/ftgw/digital/portfolio/positions"
TRADE_URL = "https://digital.fidelity.com/ftgw/digital/trade-equity/index/orderEntry"

def log(message):
    if DEBUG:
        print(f"[FIDELITY DEBUG] {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}")

def create_creds_folder():
    if not os.path.exists(COOKIES_PATH):
        os.makedirs(COOKIES_PATH)

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

async def fidelity_error(error: str, page=None, discord_loop=None, browser=None):
    print(f"Fidelity Error: {error}")
    log(f"Error encountered: {error}")
    if page:
        try:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_name = f"fidelity_error_{timestamp}.png"
            await page.save_screenshot(filename=screenshot_name)
            
            html_name = f"fidelity_error_{timestamp}.html"
            content = await page.get_content()
            with open(html_name, "w", encoding="utf-8") as f:
                f.write(content)
                
            log(f"Debug artifacts saved: {screenshot_name}, {html_name}")
        except Exception as e:
            print(f"Failed to save debug artifacts: {e}")

    if discord_loop:
        printAndDiscord(f"Fidelity Error: {error}", discord_loop)
    
    if browser:
        try:
            await browser.stop()
        except:
            pass

async def get_current_url(page):
    try:
        return await page.evaluate("window.location.href")
    except:
        return ""

async def type_with_random_delay(element, text, min_delay=0.05, max_delay=0.15):  
    """Type text with random delays between characters"""  
    # Get the payloads for each character  
    payloads = KeyEvents.from_text(text, KeyPressEvent.DOWN_AND_UP)  
      
    for payload in payloads:  
        await element._tab.send(cdp.input_.dispatch_key_event(**payload))  
        await asyncio.sleep(random.uniform(min_delay, max_delay))  

def fidelity_run(orderObj=None, command=None, botObj=None, loop=None, FIDELITY_EXTERNAL=None, DOCKER=False, **kwargs):
    print("Starting Fidelity run process...")
    log("fidelity_run initiated.")
    create_creds_folder()
    
    # Create a dedicated event loop for this run
    local_fidelity_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(local_fidelity_loop)
    
    discord_loop = loop

    if not os.getenv("FIDELITY") and FIDELITY_EXTERNAL is None:
        print("FIDELITY environment variable not found.")
        local_fidelity_loop.close()
        return None

    accounts_env = (os.environ.get("FIDELITY", "") if FIDELITY_EXTERNAL is None else FIDELITY_EXTERNAL).strip().split(",")
    fidelity_brokerage_obj = Brokerage("FIDELITY")
    
    # Initialize safely
    fidelity_brokerage_obj.fidelity_accounts = []
    
    if command is None:
        action_to_perform = "_holdings"
    else:
        _, action_to_perform = command

    try:
        populated_obj = local_fidelity_loop.run_until_complete(
            _async_fidelity_run_wrapper(
                accounts_env, fidelity_brokerage_obj, action_to_perform, botObj, discord_loop, orderObj, DOCKER
            )
        )
        
        if populated_obj and orderObj:
            orderObj.set_logged_in(populated_obj, 'fidelity')
        return populated_obj

    except Exception as e:
        print(f"Critical error in Fidelity run: {e}")
        traceback.print_exc()
        return fidelity_brokerage_obj
    finally:
        try:
            local_fidelity_loop.close()
            log("Local event loop closed.")
        except Exception as e_loop:
            log(f"Error closing local loop: {e_loop}")

async def _async_fidelity_run_wrapper(accounts_env, brokerage_obj: Brokerage, action, botObj, discord_loop, orderObj, DOCKER=False):
    headless = os.getenv("HEADLESS", "true").lower() == "true"
    
    for acc_idx, account_cred_str in enumerate(accounts_env):
        account_name_key = f"Fidelity {acc_idx + 1}"
        browser = None
        page = None
        
        try:
            browser_args = []
            if DOCKER:
                browser_args.extend(["--disable-dev-shm-usage", "--disable-gpu", "--window-size=1920,1080"])
            elif headless:
                browser_args.extend(["--headless=new", "--window-size=1920,1080", 
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
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
                browser_args.extend(["--start-maximized", "--disable-session-crashed-bubble", "--disable-infobars"])

            profile_path = os.path.abspath(os.path.join(COOKIES_PATH, f"ZenFidelity_{acc_idx + 1}"))
            
            browser_args.append("--force-device-scale-factor=0.8")

            log(f"Starting browser for {account_name_key}...")
            await clean_existing_chrome_processes()
            browser = await uc.start(browser_args=browser_args, user_data_dir=profile_path)
            page = await browser.get(LOGIN_URL) if not browser.tabs else await browser.tabs[0].get(LOGIN_URL)

            # Login
            success = await fidelity_login(page, account_cred_str, account_name_key, botObj, discord_loop)
            if not success:
                raise Exception("Login failed.")

            log(f"Login successful for {account_name_key}!")
            brokerage_obj.set_logged_in_object(account_name_key, browser)

            # Action
            if action == "_holdings":
                await fetch_holdings(page, brokerage_obj, account_name_key, discord_loop)
            elif action == "_transaction":
                # Ensure accounts are fetched before transaction
                await fetch_accounts(page, brokerage_obj, name=account_name_key, loop=discord_loop)
                await fidelity_transaction(page, brokerage_obj, orderObj, account_name_key, discord_loop)

        except Exception as e:
            await fidelity_error(f"Error in {account_name_key}: {e}", page, discord_loop, browser)
        finally:  
            if browser:  
                try:  
                    await asyncio.sleep(2)
                    for tab in browser.tabs: 
                        try: await tab.close()  
                        except: pass  
                    await asyncio.sleep(1)
                    await browser.stop()  
                except: pass

    return brokerage_obj

async def fidelity_login(page, account_cred_str, name, botObj, discord_loop):
    creds = account_cred_str.split(":")
    username = creds[0]
    password = creds[1]
    totp_secret = creds[2] if len(creds) > 2 else None
    
    log(f"Logging in {name}...")
    
    curr_url = await get_current_url(page)
    # Check if we are already logged in (must NOT be on the login page)
    if "ftgw/digital/portfolio/summary" in curr_url and "login" not in curr_url:
        log("Already logged in.")
        return True

    try:
        user_input = await page.select("#dom-username-input", timeout=5)
        if not user_input:
             user_input = await page.select("input[name='username']", timeout=2)
             if not user_input:
                 user_input = await page.select("#userId-input", timeout=2)

        if user_input:  
            await user_input.mouse_move()  
            await asyncio.sleep(random.uniform(0.1, 0.3))  
            await user_input.mouse_click()  
            await asyncio.sleep(random.uniform(0.1, 0.3))  
            await user_input.clear_input_by_deleting()  
            await type_with_random_delay(user_input, username)  

        pass_input = await page.select("#dom-pswd-input", timeout=5)
        if not pass_input:
            pass_input = await page.select("#password", timeout=5)
            
        if pass_input:  
            await pass_input.mouse_move()  
            await asyncio.sleep(random.uniform(0.1, 0.3))  
            await pass_input.mouse_click()  
            await asyncio.sleep(random.uniform(0.1, 0.3))  
            await pass_input.clear_input_by_deleting()  
            await type_with_random_delay(pass_input, password)
        
        login_btn = await page.select("#dom-login-button", timeout=3)
        if login_btn:
            await login_btn.mouse_move()
            await asyncio.sleep(random.uniform(0.1, 0.3))
            await login_btn.mouse_click()
            await asyncio.sleep(random.uniform(0.1, 0.3))

        # Efficient wait loop for Redirection or 2FA
        log("Waiting for login result (Redirect or 2FA)...")
        start_time = datetime.datetime.now()
        
        # Extended wait for 2FA detection logic
        while (datetime.datetime.now() - start_time).seconds < 45:
            curr_url = await get_current_url(page)
            
            if "ftgw/digital/portfolio/summary" in curr_url and "login" not in curr_url:
                log("Redirected to summary. Login Complete.")
                await page.select("#accountDetails", timeout=10)
                return True
            
            # Check for various 2FA indicators
            # 1. TOTP Input
            # 2. Push Header/Text
            # 3. Channel Selection Header/Text
            # 4. OTP Input
            
            is_2fa = await page.evaluate("""
                (function() {
                    if (document.getElementById('dom-totp-security-code-input')) return true;
                    if (document.getElementById('dom-push-authenticator-header')) return true;
                    if (document.getElementById('dom-channel-list-header')) return true;
                    if (document.getElementById('dom-otp-code-input')) return true;
                    if (document.querySelector('input[type="tel"]')) return true;
                    
                    const buttons = document.querySelectorAll('button');
                    for (const btn of buttons) {
                        if (btn.innerText.includes('Text me') || btn.innerText.includes('Call me')) return true;
                    }
                    
                    const headers = document.querySelectorAll('h1');
                    for (const h of headers) {
                         if (h.innerText.includes("notification to the Fidelity")) return true;
                         if (h.innerText.includes("verify it's you")) return true;
                    }
                    
                    return false;
                })();
            """)
            
            if is_2fa:
                log("2FA Challenge Detected. Initiating handler...")
                if await handle_2fa(page, botObj, discord_loop, totp_secret, name):
                    return True
                else:
                    return False
            
            await page.sleep(0.25)
            await page.wait_for_ready_state("complete", timeout=10)
            await page.wait()
            await page.sleep(0.25)
            
        log("Login timed out or failed to redirect.")
        return False

    except Exception as e:
        log(f"Login exception: {e}")
        return False

async def handle_2fa(page, botObj, discord_loop, totp_secret, name):
    try:
        # Give a moment for the 2FA UI to fully stabilize
        await page.sleep(0.25)
        await page.wait_for_ready_state("complete", timeout=10)
        await page.wait()
        await page.sleep(0.25)
        
        # -----------------------------------------------------
        # CASE 1: In-App Push Notification
        # -----------------------------------------------------
        # Look for header: "We'll send a notification to the Fidelity Investments app..."
        push_header = None
        try:
            push_header = await page.select("#dom-push-authenticator-header", timeout=1)
        except:
            push_header = None
            
        if push_header:
            log("Push Notification 2FA detected.")
            
            # 1. Click 'Don't ask me again' if present
            await page.evaluate("""
                (function() {
                    const cb = document.getElementById('dom-trust-device-checkbox');
                    if (cb && !cb.checked) {
                        cb.click();
                    }
                })();
            """)

            await page.sleep(0.25)
            await page.wait_for_ready_state("complete", timeout=10)
            await page.wait()
            await page.sleep(0.25)
            
            # 2. Click 'Send notification' button
            send_btn = await page.select("#dom-push-primary-button", timeout=2)
            if send_btn:
                log("Clicking 'Send notification' button...")
                await send_btn.mouse_click()
                
                # 3. Notify user
                msg = f"{name}: Fidelity Push Notification Sent. Please approve in app within 2 minutes."
                log(msg)
                if botObj and discord_loop:
                    printAndDiscord(msg, discord_loop)
                
                # 4. Wait loop for redirect (2 mins)
                log("Waiting for push approval...")
                for _ in range(24): # 24 * 5s = 120s
                    await page.sleep(5)
                    curr_url = await get_current_url(page)
                    if "ftgw/digital/portfolio/summary" in curr_url and "login" not in curr_url:
                        log("Push approved, redirected successfully.")
                        return True
                
                log("Push notification timed out.")
                return False
            else:
                log("Push notification button not found!")

        # -----------------------------------------------------
        # CASE 2: SMS/Call Channel Selection
        # -----------------------------------------------------
        # Look for header: "To verify it's you, we'll send a temporary code..."
        channel_header = None
        try:
            channel_header = await page.select("#dom-channel-list-header", timeout=1)
        except:
            channel_header = None
            
        if channel_header:
            log("SMS/Call Selection 2FA detected.")
            
            # Select "Text me the code"
            text_btn = await page.select("#dom-channel-list-primary-button", timeout=2)
            if text_btn:
                log("Clicking 'Text me the code'...")
                await text_btn.mouse_move()
                await text_btn.mouse_click()
                # Wait for the input screen to load
                await page.sleep(0.25)
                await page.wait_for_ready_state("complete", timeout=10)
                await page.wait()
                await page.sleep(0.25)
            else:
                log("Text button not found, checking secondary options...")
                # Could try to find secondary button if primary isn't text, but primary is usually text
        
        # -----------------------------------------------------
        # CASE 3: SMS Input Screen
        # -----------------------------------------------------
        # Can appear directly or after Case 2
        otp_input = None
        try:
            otp_input = await page.select("#dom-otp-code-input", timeout=1)
        except:
            otp_input = None
            
        if otp_input:
            log("SMS OTP Input detected.")
            
            code = None
            if botObj and discord_loop:
                 future = asyncio.run_coroutine_threadsafe(
                    getOTPCodeDiscord(botObj, name, code_len=6, timeout=300, loop=discord_loop),
                    discord_loop
                 )
                 code = await asyncio.wrap_future(future)
            else:
                 code = await asyncio.get_event_loop().run_in_executor(None, input, f"Enter Fidelity SMS Code for {name}: ")
            
            if code:
                log(f"Entering SMS code...")
                await otp_input.clear_input()
                await otp_input.send_keys(code)
                
                # Click 'Don't ask me again'
                await page.evaluate("""
                    (function() {
                        const cb = document.getElementById('dom-trust-device-checkbox');
                        if (cb && !cb.checked) {
                            cb.click();
                        }
                    })();
                """)
                await page.sleep(0.25)

                # Click Submit
                submit_btn = await page.select("#dom-otp-code-submit-button", timeout=2)
                if submit_btn:
                    await submit_btn.mouse_move()
                    await submit_btn.mouse_click()
                    await page.sleep(5)
                    
                    # Wait for redirect
                    for _ in range(10):
                        curr_url = await get_current_url(page)
                        if "ftgw/digital/portfolio/summary" in curr_url and "login" not in curr_url:
                            return True
                        await page.sleep(0.25)
                        await page.wait_for_ready_state("complete", timeout=10)
                        await page.wait()
                        await page.sleep(0.25)
            
            return False

        # -----------------------------------------------------
        # CASE 4: TOTP Authenticator (VIP Access / App Code)
        # -----------------------------------------------------
        auth_input = None
        try:
            auth_input = await page.select("#dom-totp-security-code-input", timeout=1)
        except:
            auth_input = None
            
        if auth_input:
            log("TOTP Authenticator detected.")
            code = None
            
            if totp_secret and totp_secret.lower() != "na":
                try:
                    totp = pyotp.TOTP(totp_secret.replace(" ", ""))
                    code = totp.now()
                    log("Generated TOTP code locally.")
                except Exception as e:
                    log(f"Failed to generate TOTP code: {e}")
            
            if not code:
                 if botObj and discord_loop:
                     future = asyncio.run_coroutine_threadsafe(
                        getOTPCodeDiscord(botObj, name, code_len=6, timeout=300, loop=discord_loop),
                        discord_loop
                     )
                     code = await asyncio.wrap_future(future)
                 else:
                     code = await asyncio.get_event_loop().run_in_executor(None, input, f"Enter Fidelity TOTP for {name}: ")
            
            if code:
                await auth_input.mouse_move()
                await auth_input.mouse_click()
                await auth_input.send_keys(code)
                
                # Trust Device
                await page.evaluate("""
                    (function() {
                        const cb = document.getElementById('dom-trust-device-checkbox');
                        if (cb && !cb.checked) {
                            cb.click();
                        }
                    })();
                """)
                await page.sleep(0.25)

                # Submit (Try specific ID first, fallback to generic logic if ID changed)
                # The old script used 'dom-totp-code-continue-button', assuming it's still valid or similar
                continue_btn = await page.select("#dom-totp-code-continue-button", timeout=5)
                if continue_btn:
                    await continue_btn.mouse_move()
                    await continue_btn.mouse_click()
                    await page.sleep(5)
                
                # Wait for redirect
                for _ in range(15):
                    curr_url = await get_current_url(page)
                    if "ftgw/digital/portfolio/summary" in curr_url and "login" not in curr_url:
                        return True
                    await page.sleep(1)
                return False

    except Exception as e:
        log(f"2FA Error: {e}")
        traceback.print_exc()
    return False

async def set_download_path(page, path):
    try:
        await page.send(cdp.browser.set_download_behavior(
            behavior="allow",
            download_path=path,
            events_enabled=True
        ))
    except Exception as e:
        log(f"Failed to set download path: {e}")

async def fetch_accounts(page, brokerage_obj, name, loop):
    """
    Captures accounts via UI scraping on the Trade page.
    """
    log("Fetching accounts info via UI...")
    found_accounts = []

    try:
        if TRADE_URL not in await get_current_url(page):
            await page.get(TRADE_URL)
        
        dropdown_selector = "#dest-acct-dropdown"
        
        # Wait for dropdown
        await page.sleep(0.25)
        await page.wait_for_ready_state("complete", timeout=10)
        await page.wait()
        await page.sleep(0.25)

        found_dd = False
        for _ in range(20):
            if await page.evaluate(f'document.querySelector("{dropdown_selector}") !== null'):
                found_dd = True
                break
            await page.sleep(0.25)
        
        if found_dd:
            # Open dropdown to load list
            await page.evaluate(f'document.querySelector("{dropdown_selector}").click()')
            
            # Scrape items
            scraped_data = await page.evaluate("""
                (function() {
                    const list = document.getElementById("ett-acct-sel-list");
                    if (!list) return [];
                    const buttons = list.querySelectorAll('div[role="option"] button');
                    let results = [];
                    for (let btn of buttons) {
                        results.push(btn.innerText.trim());
                    }
                    return results;
                })();
            """)
            
            # Parse scraped strings "Nickname (Number)"
            for item in scraped_data:
                match = re.search(r'(.*?)\s*\((Z?\d+)\)', item)
                if match:
                    nickname = match.group(1).strip()
                    acct_num = match.group(2).strip()
                    found_accounts.append({
                        "acctNum": acct_num,
                        "name": nickname,
                        "type": "Unknown", 
                        "desc": "Scraped"
                    })
                    log(f"Captured Account (UI): {nickname} ({acct_num})")
        else:
            log("Trade dropdown not found for account fetch.")
    except Exception as e:
        log(f"UI Account Fetch Error: {e}")
        traceback.print_exc()

    brokerage_obj.fidelity_accounts = found_accounts
    log(f"Total Accounts Captured: {len(found_accounts)}")

async def fetch_holdings(page, brokerage_obj, name, loop):
    log("Fetching holdings via CSV...")
    download_dir = os.path.join(os.getcwd(), "temp_downloads")
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)
    
    try:
         for f in glob.glob(os.path.join(download_dir, "*.csv")):
             os.remove(f)
    except: pass

    try:
        await set_download_path(page, download_dir)
        
        if "positions" not in await get_current_url(page):
            await page.get(POSITIONS_URL)
            await page.sleep(0.25)
            await page.wait_for_ready_state("complete")
            await page.wait()
            await page.sleep(0.25)
            
        await page.evaluate("""
            (function() {
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    if (btn.innerText.includes('Available Actions')) {
                        btn.click();
                        return;
                    }
                }
                const uses = document.querySelectorAll('use');
                for (const u of uses) {
                    const href = u.getAttribute('href') || u.getAttribute('xlink:href') || '';
                    if (href.includes('nav__overflow-vertical')) {
                        const btn = u.closest('button');
                        if (btn) {
                            btn.click();
                            return;
                        }
                    }
                }
            })();
        """)
        
        await page.sleep(2)
        
        await page.evaluate("""
            (function() {
               const buttons = document.querySelectorAll('button, [role="menuitem"]');
               for (const btn of buttons) {
                   if (btn.innerText.trim() === 'Download') {
                       btn.click();
                       return;
                   }
               }
            })();
        """)
        
        log("Waiting for CSV download...")
        downloaded_file = None
        for _ in range(30):
            files = glob.glob(os.path.join(download_dir, "*.csv"))
            if files:
                files.sort(key=os.path.getmtime, reverse=True)
                downloaded_file = files[0]
                await asyncio.sleep(1) 
                break
            await asyncio.sleep(1)
            
        if not downloaded_file:
            printAndDiscord(f"{name}: Download timed out.", loop)
            return
            
        account_totals = {}
        seen_accounts = set()
        
        with open(downloaded_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                acc_num = row.get("Account Number")
                if not acc_num or "and" in acc_num or str(acc_num).startswith("Y") or not row.get("Symbol"):
                    continue
                
                acc_name = row.get("Account Name", "Current Portfolio")
                symbol = row.get("Symbol")
                desc = row.get("Description", "")
                
                def clean_num(v):
                    if not v: return 0.0
                    try:
                        clean_v = str(v).replace("$", "").replace(",", "").replace("%", "").strip()
                        if clean_v in ["--", "n/a", "N/A", "-"]:
                            return 0.0
                        return float(clean_v or 0)
                    except ValueError:
                        return 0.0

                qty = clean_num(row.get("Quantity"))
                last_price = clean_num(row.get("Last Price"))
                current_val = clean_num(row.get("Current Value"))
                
                if "Pending" in str(symbol) or "Pending" in desc:
                     pass
                if not symbol and "Cash" in desc:
                     symbol = "CASH"
                     
                if acc_num not in seen_accounts:
                    brokerage_obj.set_account_number(name, acc_num)
                    brokerage_obj.set_account_type(name, acc_num, acc_name)
                    seen_accounts.add(acc_num)
                
                if acc_num not in account_totals:
                    account_totals[acc_num] = 0.0
                account_totals[acc_num] += current_val
                
                if symbol and (qty > 0 or current_val > 0):
                    if not symbol: symbol = "OTHER"
                    brokerage_obj.set_holdings(name, acc_num, symbol, qty, last_price)

        for acc_num, total in account_totals.items():
            brokerage_obj.set_account_totals(name, acc_num, total)
            
        printHoldings(brokerage_obj, loop)

        try:
             f.close()
             os.remove(downloaded_file)
        except: pass
        
    except Exception as e:
        printAndDiscord(f"{name} Holdings Error: {e}", loop)
        traceback.print_exc()

async def fidelity_transaction(page, brokerage_obj, orderObj, name, loop):
    log("Starting transaction loop...")
    
    # Ensure accounts are populated
    if not hasattr(brokerage_obj, 'fidelity_accounts') or not brokerage_obj.fidelity_accounts:
        log("Account list not populated, fetching...")
        await fetch_accounts(page, brokerage_obj, name, loop)

    if not hasattr(brokerage_obj, 'fidelity_accounts') or not brokerage_obj.fidelity_accounts:
        log("Failed to fetch accounts or no accounts found. Aborting transaction.")
        return

    accounts_to_process = []
    # Note: stockOrder class doesn't usually carry a specific account number, 
    # but we keep this check for compatibility if the object differs.
    if hasattr(orderObj, 'account_number') and orderObj.account_number:
        accounts_to_process = [a for a in brokerage_obj.fidelity_accounts if a['acctNum'] == orderObj.account_number]
        if not accounts_to_process:
            printAndDiscord(f"{name}: Specified account {orderObj.account_number} not found.", loop)
            return
    else:
        accounts_to_process = brokerage_obj.fidelity_accounts

    # --- Use Getters for stockOrder Object ---
    try:
        action_val = orderObj.get_action()
        quantity_val = orderObj.get_amount()
        is_dry_run = orderObj.get_dry()
        symbols = orderObj.get_stocks()
    except AttributeError:
        # Fallback if orderObj is not the expected class
        log("Warning: orderObj missing expected getters, trying attributes...")
        action_val = getattr(orderObj, 'action', getattr(orderObj, 'order_type', None))
        quantity_val = getattr(orderObj, 'quantity', None)
        is_dry_run = getattr(orderObj, 'dry_run', False)
        single_sym = getattr(orderObj, 'symbol', None)
        symbols = [single_sym] if single_sym else []

    if not action_val or quantity_val is None or not symbols:
        log(f"Critical Error: Missing Order Details. Action: {action_val}, Qty: {quantity_val}, Symbols: {symbols}")
        return
        
    action_upper = action_val.upper()
    
    # Iterate through symbols to support list of stocks
    for symbol in symbols:
        log(f"Processing Symbol: {symbol} ({action_upper})")
        
        for account in accounts_to_process:
            acct_num = account['acctNum']
            acct_name = account.get('name', 'Unknown')
            log(f"--- Processing Account: {acct_name} ({acct_num}) for {symbol} ---")
            printAndDiscord(f"{name}: {action_upper}ing {quantity_val} of {symbol} in {acct_name} ({acct_num})", loop)
            
            try:
                await page.get(TRADE_URL)
                await page.select("#previewOrderBtn", timeout=10)
                
                dropdown_selector = "#dest-acct-dropdown"
                for _ in range(20):
                    try:
                        if await page.evaluate(f'document.querySelector("{dropdown_selector}") !== null'):
                            break
                    except: pass

                log(f"Selecting Account: {acct_num}")
                await page.evaluate(f'document.querySelector("{dropdown_selector}").click()')
                
                js_select_account = f"""
                (function() {{
                    const list = document.getElementById("ett-acct-sel-list");
                    if (!list) return "List not found";
                    
                    const buttons = list.querySelectorAll('div[role="option"] button');
                    for (let btn of buttons) {{
                        if (btn.innerText.includes("{acct_num}")) {{
                            btn.click();
                            return "Clicked";
                        }}
                    }}
                    return "Account not found in list";
                }})();
                """
                result = await page.evaluate(js_select_account)
                if result != "Clicked":
                    log(f"Failed to select account {acct_num}. Skipping.")
                    continue

                # ---------------------------------------------------------
                # EXTENDED HOURS LOGIC (Updated: Toggle Priority + Time Fallback)
                # ---------------------------------------------------------
                et_tz = pytz.timezone('US/Eastern')
                now_et = datetime.datetime.now(et_tz)
                current_time = now_et.time()
                is_extended_time = False

                toggle_row = None
                switch_root = None
                try:
                    toggle_row = await page.select('.eq-ticket__extended-hrs-toggle-row_dest', timeout=1)
                except:
                    pass

                toggle_element_exists = toggle_row is not None
  
                if toggle_element_exists:  
                    log("Extended Hours Toggle Element DETECTED. Forcing Extended Hours Mode.")  
                    is_extended_time = True  
      
                    # 2. Check if toggle is ON using element properties  
                    is_toggled_on = False
                    switch_root = await page.select('.eq-ticket__extendedhour-toggle', timeout=5)  
                    if switch_root:  
                        # Check if element has the 'pvd-switch--on' class  
                        is_toggled_on = 'pvd-switch--on' in switch_root.attrs.get('class', '')  
      
                    if not is_toggled_on:  
                        # Fallback: check aria-checked attribute on button 
                        try: 
                            toggle_btn = await page.select("#eq-ticket_extendedhour", timeout=2)  
                            if toggle_btn and toggle_btn.attrs.get('aria-checked') == 'true':  
                                is_toggled_on = True
                        except:
                            pass
      
                    if is_toggled_on:  
                        log("Extended Hours Switch is already ON.")  
                    else:  
                        log("Extended Hours Switch is OFF. Clicking to Enable...")  
                        try:  
                            # Try clicking the button ID first 
                            btn_clicked = False 
                            try:
                                toggle_btn = await page.select("#eq-ticket_extendedhour", timeout=2)  
                                if toggle_btn is not None:  
                                    await toggle_btn.mouse_move()
                                    await toggle_btn.mouse_click()
                                    btn_clicked = True
                            except:
                                pass

                            if not btn_clicked:  
                                # Fallback to clicking the switch wrapper  
                                try:
                                    switch_wrapper = await page.select('.eq-ticket__extendedhour-toggle', timeout=2)  
                                    if switch_wrapper is not None:  
                                        await switch_wrapper.mouse_move()
                                        await switch_wrapper.mouse_click()
                                except:
                                    pass

                            await page.sleep(0.25)
                            await page.wait_for_ready_state("complete", timeout=10)
                            await page.wait()
                            await page.sleep(0.25)
                        except Exception as e:  
                            log(f"Error toggling Extended Hours switch: {e}")
                else:
                    log("Extended Hours Toggle Element NOT DETECTED. Proceeding with Normal Order logic.")

                current_price = 0.0
                
                log(f"Entering symbol: {symbol}")
                
                # Reverting to send_keys method per user request
                symbol_input = await page.select("#eq-ticket-dest-symbol")  
                if symbol_input:
                    await symbol_input.send_keys(symbol)
                    # Correct way to press Enter  
                    await symbol_input.send_keys(SpecialKeys.ENTER)
                
                log("Waiting for price data via DOM parsing...")
                await page.sleep(0.25)
                await page.wait_for_ready_state("complete", timeout=10)
                await page.wait()
                await page.sleep(0.25)
                
                # Parse DOM for Price (Last, Bid, Ask)
                price_data = await page.evaluate("""
                    (function() {
                        function parsePrice(text) {
                            if (!text) return 0.0;
                            return parseFloat(text.replace(/[$,]/g, '').trim()) || 0.0;
                        }

                        let last = 0.0, bid = 0.0, ask = 0.0;
                        
                        // Last Price
                        const lastEl = document.querySelector('.last-price');
                        if (lastEl) last = parsePrice(lastEl.innerText);

                        // Bid/Ask blocks
                        const blocks = document.querySelectorAll('.eq-ticket__quote--block');
                        for (let block of blocks) {
                            const title = block.querySelector('.block-title');
                            const num = block.querySelector('.number');
                            if (title && num) {
                                if (title.innerText.includes('Bid')) {
                                    bid = parsePrice(num.innerText);
                                } else if (title.innerText.includes('Ask')) {
                                    ask = parsePrice(num.innerText);
                                }
                            }
                        }
                        return { last: last, bid: bid, ask: ask };
                    })();
                """)
                
                last_price = price_data.get('last', 0.0)
                bid_price = price_data.get('bid', 0.0)
                ask_price = price_data.get('ask', 0.0)
                
                log(f"Scraped Prices - Last: {last_price}, Bid: {bid_price}, Ask: {ask_price}")
                
                # Determine Price to Use based on Action
                # BUY -> Use Ask (paying), Fallback to Last
                # SELL -> Use Bid (receiving), Fallback to Last
                if action_upper == "BUY":
                    if ask_price > 0:
                        current_price = ask_price
                    else:
                        current_price = last_price
                elif action_upper == "SELL":
                    if bid_price > 0:
                        current_price = bid_price
                    else:
                        current_price = last_price
                else:
                    current_price = last_price
                
                log(f"Selected Reference Price for {action_upper}: {current_price}")
                printAndDiscord(f"{name}: Current price for {symbol} is ${current_price}", loop)

                # ---------------------------------------------------------
                # NEW: ENSURE EXPANDED TICKET MODE
                # ---------------------------------------------------------
                log("Verifying Ticket Mode (Expanded vs Simplified)...")
                
                try:
                    # Check if 'View simplified ticket' button is present
                    # If present, it means we are CURRENTLY in Expanded Mode (Desired State)
                    is_expanded_mode = await page.select("#show-fewer-trade-selections", timeout=1)

                    if is_expanded_mode:
                        log("Expanded ticket mode detected (Active). Proceeding.")
                    else:
                        log("Expanded ticket mode NOT detected. Looking for 'View expanded ticket' button...")
                        
                        # Check for the 'View expanded ticket' button
                        expand_btn = await page.select("#show-more-trade-selections", timeout=5)
                        
                        if expand_btn:
                            log("Found 'View expanded ticket' button. Clicking to switch modes...")
                            await expand_btn.scroll_into_view()
                            await expand_btn.mouse_move()
                            await expand_btn.mouse_click()
                            await page.select("#show-fewer-trade-selections", timeout=1)
                        else:
                            log("Warning: 'View expanded ticket' button not found. Layout might differ or already strictly enforced.")

                except Exception as e:
                    log(f"Error ensuring Expanded Ticket Mode: {e}")
                # ---------------------------------------------------------

                log(f"Selecting Action: {action_upper}")
          
                # 1. Open the dropdown
                action_dropdown = await page.select("#dest-dropdownlist-button-action", timeout=10)
                await action_dropdown.scroll_into_view()
                await action_dropdown.mouse_move()
                await action_dropdown.mouse_click()
                
                # 2. Robust Selection via JavaScript
                # Fidelity uses specific IDs: #Action0 = Buy, #Action1 = Sell
                if action_upper == "BUY":  
                    buy_option = await page.select("#Action0", timeout=10)
                    await buy_option.scroll_into_view()
                    await buy_option.mouse_move()
                    await buy_option.mouse_click()  
                elif action_upper == "SELL":  
                    sell_option = await page.select("#Action1", timeout=10)
                    await sell_option.scroll_into_view()
                    await sell_option.mouse_move()
                    await sell_option.mouse_click()
                

                log(f"Entering Quantity: {quantity_val}")
                qty_input = await page.select("#eqt-shared-quantity", timeout=10)
                if qty_input:
                    await qty_input.clear_input()
                    await qty_input.send_keys(str(quantity_val))

                order_type_to_use = "Market"
                limit_price_to_use = None

                # Check for Penny Stock Rule (Price < $1 often requires Limit on Fidelity)
                if current_price > 0 and current_price < 1.00 and action_upper == 'BUY':
                    log("Price < $1.00. Forcing LIMIT order.")
                    order_type_to_use = "Limit"
                # ---------------------------------------------------------
                # EXTENDED HOURS ADJUSTMENTS (Moved logic kept here for order type)
                # ---------------------------------------------------------
                if is_extended_time:
                    # 2. Force LIMIT Order (Market is unavailable in Ext Hours)
                    if order_type_to_use != "Limit":
                        log("Extended Hours requires LIMIT order. Converting Market -> Limit.")
                        order_type_to_use = "Limit"
                        
                    # 3. Calculate Limit Price (if user didn't provide one)
                    # We behave like a 'Market' order by setting a Limit at the current Ask/Bid
                    if not limit_price_to_use:
                        base_price = last_price
                        
                        if action_upper == "BUY":
                            # Buying: Use Ask Price (or Last + 0.01 buffer)
                            base_price = ask_price if ask_price > 0 else last_price
                            # Fidelity Extended Hours Requirement: Max 2 decimal places
                            # Round UP (ceil) to nearest penny to ensure fill and meet requirement
                            limit_price_to_use = math.ceil(base_price * 100) / 100.0
                        
                        elif action_upper == "SELL":
                            # Selling: Use Bid Price (or Last - 0.01 buffer)
                            base_price = bid_price if bid_price > 0 else last_price
                            # Fidelity Extended Hours Requirement: Max 2 decimal places
                            # Round DOWN (floor) to nearest penny
                            limit_price_to_use = math.floor(base_price * 100) / 100.0
                                    
                        log(f"Calculated Extended Hours Limit Price: {limit_price_to_use}")

                # ---------------------------------------------------------
                # ORDER TYPE SELECTION (Updated)
                # ---------------------------------------------------------
                log(f"Setting Order Type: {order_type_to_use}")

                # 1. Open the Order Type Dropdown
                type_dropdown = await page.select("#dest-dropdownlist-button-ordertype", timeout=10)
                await type_dropdown.mouse_move()
                await type_dropdown.mouse_click()
                
                # 2. Select the specific option using mouse_click
                # Mapping based on IDs: #Order-type0=Market, #Order-type1=Limit, #Order-type3=Stop Loss, #Order-type4=Stop Limit
                
                if order_type_to_use == "Limit":
                    # Robustly find 'Limit' option, prioritizing text match
                    # This handles both Standard (usually Type1) and Extended Hours (usually Type0)
                    target_id = await page.evaluate("""
                       (function() {
                           const options = document.querySelectorAll('div[role="option"]');
                           for (const opt of options) {
                               if (opt.innerText.trim() === 'Limit') return opt.id;
                           }
                           return null;
                       })();
                    """)
                    
                    if target_id:
                        option = await page.select(f"#{target_id}", timeout=10)
                        if option: await option.mouse_click()
                    else:
                        # Fallback if text search failed
                        fallback_id = "#Order-type0" if is_extended_time else "#Order-type1"
                        option = await page.select(fallback_id, timeout=10)
                        if option: await option.mouse_click()
                elif order_type_to_use == "Stop Loss":
                    option = await page.select("#Order-type3", timeout=10)
                    await option.mouse_click()
                elif order_type_to_use == "Stop Limit":
                    option = await page.select("#Order-type4", timeout=10)
                    await option.mouse_click()
                else:
                    # Default to Market if "Market" or unknown
                    option = await page.select("#Order-type0", timeout=10)
                    await option.mouse_click()

                # 4. Handle Limit Price Input (Only if Limit was selected)
                if order_type_to_use == "Limit":
                    # Wait a moment for the Limit Price input to appear/become active
                    limit_input = await page.select("#eqt-mts-limit-price", timeout=10)  
                    await limit_input.mouse_click()
                    await limit_input.focus()
                    await limit_input.clear_input_by_deleting()
                    if limit_price_to_use is None:
                        limit_price_to_use = current_price
                    await limit_input.send_keys(str(limit_price_to_use))
                    await page.mouse_click(0,0)

                log("Previewing Order...")
                
                # Click Preview Button
                preview = await page.select("#previewOrderBtn", timeout=10)
                await preview.mouse_move()
                await preview.mouse_click()
                
                # Wait for potential error modal or success state
                await page.sleep(0.25)
                await page.wait_for_ready_state("complete", timeout=10)
                await page.wait()
                await page.sleep(0.25)
                
                # Check for "Place Order" button first (Success Indicator)
                place_order_btn = None
                try:
                    place_order_btn = await page.select("#placeOrderBtn", timeout=2)
                except:
                    place_order_btn = None
                    
                preview_error = None

                if not place_order_btn:
                    log("Place Order button not found. Checking for Error Modal...")
                    # Search for the error content div directly
                    error_content = await page.select(".pvd-inline-alert__content", timeout=2)
                    if error_content:
                        # Found the error text container. Try to read it.
                        try:
                            preview_error = await page.evaluate("document.querySelector('.pvd-inline-alert__content').innerText")
                        except:
                            preview_error = "Error detected (Could not parse text)"
                    else:
                        # Fallback: Check if the modal dialog exists at all
                        modal_dialog = await page.select(".pvd-modal__dialog", timeout=2)
                        if modal_dialog:
                            preview_error = "Error Modal Detected (details could not be parsed)"

                if preview_error:
                    log(f"Preview Failed for {acct_num}: {preview_error}")
                    # Try to close modal
                    close_btn = await page.select(".pvd-modal__close-button", timeout=2)
                    if close_btn:
                        await close_btn.mouse_move()
                        await close_btn.mouse_click()
                    continue
                
                log("Preview Successful (No error modal detected).")

                if is_dry_run:
                    log(f"Dry Run Active. Order NOT placed for {acct_num}.")
                    printAndDiscord(f"{name} [{acct_num}]: [DRY RUN] Would {action_upper} {quantity_val} of {symbol} @ ~${limit_price_to_use if limit_price_to_use else current_price}", loop)
                else:
                    log(f"Placing Order for {acct_num}...")
                    
                    place_order_btn = await page.select("#placeOrderBtn", timeout=10)
                    
                    if place_order_btn:
                        try:
                            await place_order_btn.mouse_move()
                            await place_order_btn.mouse_click()
                            
                            # Wait for confirmation
                            log("Order placed. Waiting for confirmation...")
                            confirmation_success = False
                            
                            # Wait up to 10 seconds for confirmation indicators
                            for _ in range(20):
                                await page.sleep(0.5)
                                
                                # Check for "Order Received" header/text or verified success indicators
                                success_check = await page.evaluate("""
                                    (function() {
                                        const bodyText = document.body.innerText;
                                        if (bodyText.includes('Order Received') || bodyText.includes('Confirmation')) return true;
                                        if (document.querySelector('.pvd-inline-alert__content--success')) return true;
                                        return false;
                                    })();
                                """)
                                
                                if success_check:
                                    confirmation_success = True
                                    break
                                
                                # Check if we got redirected to Confirmation page (URL change)
                                curr_url = await page.evaluate("window.location.href")
                                if "confirmation" in curr_url.lower():
                                    confirmation_success = True
                                    break

                            if confirmation_success:
                                msg = f"{name}: SUCCESS - Order for {quantity_val} {symbol} placed successfully in {acct_name} ({acct_num})"
                                log(msg)
                                printAndDiscord(msg, loop)
                            else:
                                msg = f"{name}: WARNING - Order button clicked but confirmation not detected for {symbol} in {acct_name} ({acct_num}). Please verify manually."
                                log(msg)
                                printAndDiscord(msg, loop)

                        except Exception as e:
                            log(f"Place order interactions failed: {e}")
                            printAndDiscord(f"{name}: FAILED to place order for {symbol} in {acct_name} ({acct_num}). Error: {e}", loop)
                    else:
                        msg = f"{name}: ERROR - Place Order button not found for {symbol} in {acct_name}"
                        log(msg)
                        printAndDiscord(msg, loop)

            except Exception as e:
                log(f"Exception processing account {acct_num}: {e}")
                traceback.print_exc()
    
    log("Transaction loop complete.")
