import asyncio
import datetime
import os
import re
import psutil
import traceback
from bs4 import BeautifulSoup
import zendriver as uc
from zendriver import SpecialKeys, KeyEvents, KeyPressEvent 
from dotenv import load_dotenv

from helperAPI import (
    Brokerage,
    getOTPCodeDiscord,
    printAndDiscord,
    printHoldings,
    stockOrder,
)

load_dotenv()

# Controls detailed logging. Set to "true" in your .env file to enable.
DEBUG = os.getenv("WELLSFARGO_DEBUG", "false").lower() == "true"

def log(message):
    """Prints a message to the console if DEBUG is True."""
    if DEBUG:
        print(f"[DEBUG] {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}")

COOKIES_PATH = "creds"


def create_creds_folder():
    """Create the 'creds' folder if it doesn't exist."""
    log("Checking if 'creds' folder exists.")
    if not os.path.exists(COOKIES_PATH):
        os.makedirs(COOKIES_PATH)
        log("'creds' folder created.")


async def wellsfargo_error(error: str, page=None, discord_loop=None, browser=None):
    print(f"Wells Fargo Error: {error}")
    log(f"Error encountered: {error}")
    if page:
        try:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_name = f"wells-fargo-error-{timestamp}.png"
            await page.save_screenshot(filename=screenshot_name)
            print(f"Screenshot saved: {screenshot_name}")
            log(f"Screenshot saved to {screenshot_name}")

            html_name = f"wells-fargo-error-{timestamp}.html"
            content = await page.get_content()
            with open(html_name, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"HTML saved: {html_name}")
            log(f"HTML saved to {html_name}")
        except Exception as e:
            print(f"Failed to save debug artifacts: {e}")
            log(f"Failed to save debug artifacts: {e}")
    if discord_loop:
        printAndDiscord(f"Wells Fargo Error: {error}\n{traceback.format_exc()}", discord_loop)
    else:
        print(traceback.format_exc())
    if browser:
        try:
            await browser.stop()
            log("Browser stopped due to error.")
        except Exception as e_stop_err:
            print(f"Error trying to stop browser in wellsfargo_error: {e_stop_err}")


async def get_current_url(page, discord_loop):
    """Get the current page URL by evaluating JavaScript."""
    log("Attempting to get current URL.")
    try:
        await page.wait_for_ready_state("complete", timeout=20)
        await page.select("body")
        
        # Run JavaScript to get the current URL
        current_url = await page.evaluate("window.location.href")
        log(f"Current URL is: {current_url}")
        return current_url
    except Exception as e:
        await wellsfargo_error(
            f"Error fetching the current URL {e}", page=page, discord_loop=discord_loop
        )
        return None


def wellsfargo_run(orderObj=None, command=None, botObj=None, loop=None, WELLSFARGO_EXTERNAL=None, DOCKER=False, **kwargs):
    """
    Main function to run Wells Fargo operations using asyncio.
    This function itself is synchronous and designed to be called from a synchronous context (like autoRSA's fun_run).
    It then runs the async parts using a local event loop.
    """
    print("Starting Wells Fargo run process...")
    log("wellsfargo_run initiated.")
    
    # Create a new local event loop for this thread/process
    local_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(local_loop)
    
    create_creds_folder()
    discord_loop = loop

    if not os.getenv("WELLSFARGO") and WELLSFARGO_EXTERNAL is None:
        errmsg = "WELLSFARGO environment variable not found."
        print(errmsg)
        if discord_loop:
            printAndDiscord(errmsg, discord_loop)
        log("WELLSFARGO environment variable not set. Exiting.")
        local_loop.close()
        return None

    accounts_env_str = os.environ.get("WELLSFARGO", "") if WELLSFARGO_EXTERNAL is None else WELLSFARGO_EXTERNAL
    accounts_env = accounts_env_str.strip().split(",")
    log(f"Found {len(accounts_env)} Wells Fargo account(s) in environment variables.")

    final_wf_brokerage_obj = Brokerage("WELLSFARGO")

    if command is None:
        action_to_perform = "_holdings"
    else:
        _, action_to_perform = command
    log(f"Action to perform: {action_to_perform}")

    try:
        # Run the async process using the local_loop
        populated_obj = local_loop.run_until_complete(
            _async_wellsfargo_run_wrapper(
                accounts_env,
                final_wf_brokerage_obj,
                action_to_perform,
                botObj,
                discord_loop,
                orderObj,
                DOCKER
            )
        )
        
        if populated_obj and orderObj:
            orderObj.set_logged_in(populated_obj, 'wellsfargo')
            log("Populated brokerage object set in main orderObj.")
            
        return populated_obj

    except Exception as e:
        print(f"Critical error in Wells Fargo async run wrapper: {e}")
        log(f"Critical error in async wrapper: {e}\n{traceback.format_exc()}")
        print(traceback.format_exc())
        return final_wf_brokerage_obj
    finally:
        # Clean up the loop
        try:
            local_loop.close()
            log("Local event loop closed.")
        except Exception as e_loop:
            log(f"Error closing local loop: {e_loop}")


async def handle_wellsfargo_2fa(page: uc.Tab, botObj, discord_loop):
    """Handles the Wells Fargo 2FA 'Verify Your Identity' page, including the new push notification flow."""
    log("2FA page detected. Starting 2FA process.")
    try:
        # === NEW PUSH NOTIFICATION LOGIC ===
        try:
            # Wait for the page to load and check for the push notification text
            await page.wait_for_ready_state("complete")
            await page.wait()
            await page.sleep(1)
            content = await page.get_content()

            if "We sent a notification to your phone" in content:
                log("Push notification page detected.")
                printAndDiscord(
                    "Wells Fargo sent a push notification. **Please check your mobile device and approve it.** Waiting up to 2 minutes...",
                    discord_loop,
                )

                # Wait for up to 120 seconds, checking the URL every 2 seconds
                for _ in range(60):  # 60 * 2 seconds = 120 seconds
                    await page.wait_for_ready_state("complete")
                    await page.wait()
                    current_url = await get_current_url(page, discord_loop)
                    if "brokoverview" in current_url:
                        log("Push notification approved. Login successful.")
                        return  # Successfully logged in via push

                # If the loop finishes, we timed out
                log("Push notification timed out (120 seconds).")
                printAndDiscord(
                    "Push notification timed out. Attempting 'Try another method'...",
                    discord_loop,
                )

                # Click "Try another method"
                try_another_method_btn = await page.select(
                    "#buttonTryAnotherMethod", timeout=10
                )
                if not try_another_method_btn:
                    raise Exception(
                        "Push notification timed out, but could not find the 'Try another method' button."
                    )

                await try_another_method_btn.mouse_click()
                log("Clicked 'Try another method'. Waiting for options page.")
                await page.wait_for_ready_state("complete")
                await page.wait()

            else:
                log("Push notification text not found. Proceeding with standard 2FA.")

        except Exception as e_push:
            # An actual error during the push check, but we can still try the other method
            log(
                f"Error during push notification check: {e_push}. Will attempt standard 2FA."
            )

        log("Step 1: Looking for 'Mobile' phone number option in list...")
        mobile_selected = False
        try:
            contact_options = await page.select_all('[role="listitem"] button', timeout=5)
            
            if contact_options:
                for option in contact_options:
                    # Check text content for "Mobile"
                    option_text = option.text_all if hasattr(option, 'text_all') else await option.get_text()
                    if "Mobile" in option_text:
                        log("Found 'Mobile' option. Clicking it.")
                        await option.mouse_click()
                        mobile_selected = True
                        await page.wait_for_ready_state("complete")
                        await page.wait()
                        break
            else:
                log("No contact options list found (or timed out).")
        
        except Exception as e_mobile:
            log(f"Error checking/clicking 'Mobile' option: {e_mobile}. Proceeding to next check.")

        # Step 2: Click "Text me a code" / "Get Code" button
        log("Step 2: Looking for 'Text me a code' button (#optionSMS button)...")
        try:
            # We look for the button the user specifically mentioned.
            text_me_btn = await page.find("#optionSMS button", timeout=5)
            
            if text_me_btn:
                await text_me_btn.mouse_click()
                log("Clicked 'Text me a code' (#optionSMS). Waiting for OTP page.")
                await page.wait_for_ready_state("complete")
                await page.wait()
            else:
                log("'#optionSMS button' not found.")
                if mobile_selected:
                    log("Mobile was selected. Assuming we are proceeding to OTP entry or code was sent.")
        
        except Exception as e_text:
            log(f"Error checking/clicking 'Text me a code' button: {e_text}. Proceeding.")
        
        # Step 3: Get the OTP code
        log("Requesting OTP code.")
        
        if botObj and discord_loop:
            # Discord Bot Mode
            log("Requesting OTP code from Discord.")
            future = asyncio.run_coroutine_threadsafe(
                getOTPCodeDiscord(botObj, "Wells Fargo", timeout=300, loop=discord_loop),
                discord_loop
            )
            otp_code = await asyncio.wrap_future(future)
        else:
            # CLI / Terminal Mode
            print("\n!!! WELLS FARGO OTP REQUIRED !!!")
            # Use run_in_executor to avoid blocking the event loop entirely
            otp_code = await asyncio.get_event_loop().run_in_executor(None, input, "Please enter Wells Fargo OTP code: ")
        
        if not otp_code:
            raise Exception("Did not receive Wells Fargo OTP code.")
        log("OTP code received.")

        # Step 4: Enter the OTP code
        log("Entering OTP code into the input field.")
        otp_input = await page.select("#otp", timeout=10)
        if not otp_input:
            raise Exception("Could not find the OTP input field with id='otp'.")
        await otp_input.send_keys(otp_code)

        # Step 5: Click the 'Continue' button
        log("Clicking the Continue button.")
        continue_button = await page.select('button[type="submit"]', timeout=10)
        if not continue_button:
            raise Exception("Could not find the final 'Continue' submit button.")

        await continue_button.mouse_click()
        log("2FA process submitted successfully.")
        # Wait for the login to complete and the main page to load
        await page.wait_for_ready_state("complete")
        await page.wait()

    except Exception as e:
        # Re-raise to be caught by the main error handler for screenshots
        raise Exception(f"Error during Wells Fargo 2FA process: {e}")


async def _async_wellsfargo_run_wrapper(accounts_env, wf_brokerage_obj_to_populate: Brokerage, action_to_perform, botObj, discord_loop, orderObj, DOCKER=False):
    headless = os.getenv("HEADLESS", "true").lower() == "true"
    print(f"Headless mode is {'enabled' if headless else 'disabled'}.")
    log(f"Headless mode: {headless}")

    for acc_idx, account_cred_str in enumerate(accounts_env):
        account_name_key = f"WELLSFARGO {acc_idx + 1}"
        cookie_filename = os.path.join(COOKIES_PATH, f"{account_name_key.replace(' ', '_')}_cookies.pkl")
        browser = None
        page = None
        log(f"Starting process for account: {account_name_key}")

        try:
            browser_args = []
            if DOCKER:
                browser_args.extend(["--disable-dev-shm-usage", "--disable-gpu", "--window-size=1920,1080"])
            elif headless:
                browser_args.extend(["--headless=new", "--window-size=1920,1080"])
            else:
                browser_args.extend([  
                    "--start-maximized",  
                    "--disable-session-crashed-bubble",  
                    "--disable-infobars",  
                    "--disable-features=TranslateUI,VizDisplayCompositor",
                    "--no-first-run",  
                    "--disable-default-apps",
                    "--disable-extensions",
                ])

            # Create a unique profile path for each account
            profile_path = os.path.abspath(os.path.join(COOKIES_PATH, f"ZenWellsFargo_{acc_idx + 1}"))
            if not os.path.exists(profile_path):
                os.makedirs(profile_path)

            browser_args.append("--force-device-scale-factor=0.8")

            log("Starting browser...")
            browser = await uc.start(browser_args=browser_args, user_data_dir=profile_path)
            
            if not browser.tabs:
                page = await browser() 
            else:
                page = browser.tabs[0]
            log("Browser started and page object acquired.")

            await wellsfargo_init(
                account_cred_str,
                account_name_key,
                cookie_filename,
                botObj,
                browser,
                page, 
                wf_brokerage_obj_to_populate,
                discord_loop
            )

            if wf_brokerage_obj_to_populate.get_logged_in_objects(account_name_key):
                log(f"Login successful for {account_name_key}. Proceeding with action.")
                if not page or page.closed: 
                    if browser.tabs:
                        page = browser.tabs[0]
                    else:
                        page = await browser()

                if action_to_perform == "_holdings":
                    await wellsfargo_holdings(
                        wf_brokerage_obj_to_populate,
                        account_name_key,
                        browser,
                        page, 
                        discord_loop
                    )
                elif action_to_perform == "_transaction":
                    log(f"Calling wellsfargo_transaction for {account_name_key}.")
                    await wellsfargo_transaction(
                        wf_brokerage_obj_to_populate,
                        orderObj,
                        account_name_key,
                        browser,
                        page,
                        discord_loop
                    )
                print(f"Async process for account {account_name_key} completed.")

        except Exception as e:
            current_page_for_error = page if page and not page.closed else (browser.tabs[0] if browser and browser.tabs else None)
            await wellsfargo_error(f"Error during async Wells Fargo run for {account_name_key}: {e}", browser=browser, discord_loop=discord_loop, page=current_page_for_error)
        finally:
            if browser:
                try:
                    await asyncio.sleep(2)
                    # Close all tabs individually to prevent exit hangs
                    if browser.tabs:
                        for tab in browser.tabs:
                            try: await tab.close()
                            except: pass
                    await asyncio.sleep(1)
                    
                    # Standard stop
                    await browser.stop()
                    print(f"Browser stopped for {account_name_key}.")
                    log(f"Browser stopped for {account_name_key}.")
                except Exception as e_stop:
                    log(f"Browser stop error for {account_name_key}: {e_stop}")
                
                # Failsafe: Force kill the process if it still exists
                try:
                    if hasattr(browser, '_process') and browser._process:
                        browser._process.kill()
                except: pass

            browser = None
            page = None
    return wf_brokerage_obj_to_populate


async def wellsfargo_init(account_cred_str: str, account_name_key: str, cookie_filename: str, botObj, browser: uc.Browser, page: uc.Tab, wf_brokerage_obj: Brokerage, discord_loop):
    print(f"Initializing Wells Fargo login for {account_name_key}...")
    log(f"wellsfargo_init started for {account_name_key}.")
    try:
        credentials = account_cred_str.split(":")
        log("Credentials parsed.")

        if len(credentials) < 2:
            raise ValueError(f"Credential string for {account_name_key} is not in the expected format 'username:password[:phone_suffix]' Got: '{account_cred_str}'")

        phone_suffix_for_2fa = credentials[2] if len(credentials) > 2 else None
        log(f"2FA phone suffix: {'Provided' if phone_suffix_for_2fa else 'Not provided'}")

        log("Navigating to Wells Fargo Advisors homepage.")
        await page.get("https://www.wellsfargoadvisors.com/online-access/signon.htm")

        log("Locating and filling username field.")
        await page.wait_for_ready_state("complete")
        await page.wait()
        username_field = await page.select("input[id=j_username]")
        await username_field.mouse_click()
        await username_field.clear_input()
        if not username_field:
            raise Exception("Unable to locate the username input field")
        await username_field.send_keys(credentials[0])

        log("Locating and filling password field.")
        password_field = await page.select("input[id=j_password]")
        if not password_field:
            raise Exception("Unable to locate the password input field")
        await password_field.send_keys(credentials[1])

        await page.wait_for_ready_state("complete")
        await page.wait()

        log("Clicking login button.")
        login_button = await page.select(".button.button--login.button--signOn", timeout=10)
        if not login_button:
            raise Exception("Login button not found.")
        await login_button.mouse_click()
        
        # Give the page a moment to redirect after login click
        await page.wait_for_ready_state("complete", timeout=20)
        await page.wait()
        await page.sleep(1)

        current_url = await get_current_url(page, discord_loop)

        # Check if we landed on the 2FA / Identity Verification page
        if "dest=INTERDICTION" in current_url:
            await handle_wellsfargo_2fa(page, botObj, discord_loop)
            # After 2FA, get the URL again to confirm we've moved on
            current_url = await get_current_url(page, discord_loop)

        if "login" in current_url.lower() and "brokoverview" not in current_url.lower():

            error_message_on_page = "N/A"
            try: 
                error_element_selectors = [".alert-msg-summary p", "#messagetext", ".messageHyberLinkClass"]
                for selector in error_element_selectors:
                    error_element = await page.select(selector, timeout=1_000)
                    if error_element:
                        error_message_on_page = (await error_element.text_content()).strip()
                        log(f"Found error message on page with selector '{selector}': {error_message_on_page}")
                        break
            except asyncio.TimeoutError:
                log(f"No specific error message element found on page {current_url}.")
            except Exception as e_err_msg:
                log(f"Exception while trying to get error message: {e_err_msg}")

            detailed_error_msg = f"Login failed for {account_name_key}. Ended on URL: {current_url}. Page hint: '{error_message_on_page}'"
            print(f"ERROR: {detailed_error_msg}")
            if discord_loop:
                printAndDiscord(detailed_error_msg, discord_loop)
            
            wf_brokerage_obj.set_logged_in_object(account_name_key, None)
            return

        log("Login appears successful. Setting logged in object.")
        wf_brokerage_obj.set_logged_in_object(account_name_key, browser)
        await fetch_initial_account_data(page, wf_brokerage_obj, account_name_key, discord_loop)

    except Exception as e:
        current_page_for_error = page if page and not page.closed else None
        await wellsfargo_error(f"Error during Wells Fargo init for {account_name_key}: {e}", current_page_for_error, discord_loop, browser=None)
        wf_brokerage_obj.set_logged_in_object(account_name_key, None)

    log(f"wellsfargo_init finished for {account_name_key}.")


async def fetch_initial_account_data(page: uc.Tab, wf_brokerage_obj: Brokerage, account_name_key: str, discord_loop):
    log(f"Fetching initial account data for {account_name_key}.")
    try:
        await page.wait_for_ready_state("complete")
        await page.wait()
        await page.sleep(2)
        await page.select("#account-summary", timeout=10)
        current_url = await get_current_url(page, discord_loop)
        
        x_param_match = re.search(r'_x=([^&]+)', current_url)
        x_param = f"_x={x_param_match.group(1)}" if x_param_match else ""
        log(f"Extracted x_param: '{x_param}' from URL.")

        content = await page.get_content()
        soup = BeautifulSoup(content, 'html.parser')
        
        account_rows = soup.select('tr[data-p_account]')
        log(f"Found {len(account_rows)} potential account rows on summary page.")
        
        if not account_rows:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_name = f"wells-fargo-no-accounts-found-{timestamp}.png"
            await page.save_screenshot(filename=screenshot_name)
            log(f"Saved screenshot for no-accounts-found case to '{screenshot_name}'")
            
            msg = f"No account rows found for {account_name_key} on summary page. URL: {current_url}"
            printAndDiscord(msg, discord_loop)
            return
        
        for row in account_rows:
            try:
                account_index = row.get('data-p_account', '').strip()
                if account_index == "-1":
                    log("Skipping 'All Accounts' summary row.")
                    continue
                log(f"Processing row for account index: {account_index}")
                    
                account_name_el = row.select_one('[role="rowheader"]')
                if not account_name_el:
                    log("Skipping row, no rowheader found.")
                    continue
                    
                nickname_el = account_name_el.select_one('.ellipsis')
                nickname = nickname_el.get_text(strip=True) if nickname_el else "N/A"
                
                account_number_div = account_name_el.select_one('div:not(.ellipsis-container)')
                account_number = account_number_div.get_text(strip=True).replace('*', '') if account_number_div else "N/A"
                
                balance_cells = row.select('td[data-sort-value]')
                balance_text = balance_cells[-1].get_text(strip=True) if balance_cells else "$0.00"
                balance = float(balance_text.replace("$", "").replace(",", ""))
                
                account_id = f"{nickname} {account_number}"
                log(f"Processed Account: ID='{account_id}', Balance=${balance}")
                
                wf_brokerage_obj.set_account_number(account_name_key, account_id)
                wf_brokerage_obj.set_account_totals(account_name_key, account_id, balance)
                
                if not hasattr(wf_brokerage_obj, '_account_indices'):
                    wf_brokerage_obj._account_indices = {}
                if account_name_key not in wf_brokerage_obj._account_indices:
                    wf_brokerage_obj._account_indices[account_name_key] = {}
                
                wf_brokerage_obj._account_indices[account_name_key][account_id] = {
                    'index': account_index,
                    'x_param': x_param
                }
                log(f"Stored index '{account_index}' and x_param for account '{account_id}'.")
                
            except Exception as e_block:
                await wellsfargo_error(f"Error processing one account row for {account_name_key}: {e_block}", page, discord_loop)
        
        if not wf_brokerage_obj.get_account_numbers(account_name_key):
            printAndDiscord(f"No accounts successfully processed for {account_name_key} from summary.", discord_loop)
            
    except Exception as e:
        await wellsfargo_error(f"Error fetching initial account data for {account_name_key}: {e}", page, discord_loop)

async def _select_dropdown_option(page, dropdown_opener_selector, option_value, timeout=10):
    """
    Helper to select an option from a custom dropdown.
    """
    try:
        log(f"Clicking dropdown '{dropdown_opener_selector}' and selecting '{option_value}'.")
        await page.wait_for_ready_state("complete")
        await page.wait()
        opener = await page.select(dropdown_opener_selector, timeout=timeout)
        await opener.scroll_into_view()
        await opener.mouse_click()
        
        # All usages follow the a[data-val='...'] pattern
        option_selector = f"a[data-val='{option_value}']"
        option = await page.select(option_selector, timeout=timeout)
        await option.scroll_into_view()
        await option.mouse_click()
        
    except asyncio.TimeoutError:
        raise Exception(f"Failed to select '{option_value}' from dropdown '{dropdown_opener_selector}'.")


async def wellsfargo_transaction(wf_brokerage_obj: Brokerage, orderObj: stockOrder, account_name_key: str, browser: uc.Browser, page: uc.Tab, discord_loop):
    log("wellsfargo_transaction started.")
    try:
        if not wf_brokerage_obj.get_logged_in_objects(account_name_key):
            printAndDiscord(f"Not logged in for {account_name_key}, cannot perform transaction.", discord_loop)
            log("Transaction aborted: Not logged in.")
            return

        current_url = await get_current_url(page, discord_loop)
        x_param_match = re.search(r'(_x=[^&]+)', current_url)
        if not x_param_match:
            raise Exception("Could not find the dynamic '_x' parameter in the current URL.")
        
        dynamic_x_param = x_param_match.group(1)
        log(f"Captured dynamic parameter for trade URL: {dynamic_x_param}")

        processed_accounts = set()
        account_index = 0

        while True:
            trade_url = f"https://wfawellstrade.wellsfargo.com/BW/equity.do?account={account_index}&symbol=&selectedAction=&{dynamic_x_param}"
            log(f"Navigating to trade URL for account index {account_index}: {trade_url}")
            await page.get(trade_url)
            await page.wait_for_ready_state("complete")
            await page.wait()

            try:
                # This call finds the element(s)
                acct_mask_element_or_list = await page.select(".acctmask", timeout=5)
                
                final_element = None
                if isinstance(acct_mask_element_or_list, list):
                    if acct_mask_element_or_list:
                        final_element = acct_mask_element_or_list[0]
                else:
                    final_element = acct_mask_element_or_list

                if not final_element:
                    log(f"Element '.acctmask' returned None or empty list for index {account_index}. Assuming end of accounts and breaking loop.")
                    break

                full_text = final_element.text_all
                
                account_mask = re.sub(r'Account ending with', '', full_text).strip().replace('*', '')
                log(f"Found account mask on page: *{account_mask}")

            except asyncio.TimeoutError:
                log(f"Timed out waiting for account mask on page for index {account_index}. Assuming end of accounts and breaking loop.")
                break
            
            if account_mask in processed_accounts:
                log(f"Detected repeated account mask: *{account_mask}. Ending transaction loop.")
                printAndDiscord(f"Finished processing all unique accounts for {account_name_key}.", discord_loop)
                break
            
            processed_accounts.add(account_mask)
            log(f"New account found: *{account_mask}. Stored for processing.")

            for stock_symbol in orderObj.get_stocks():

                trade_url = f"https://wfawellstrade.wellsfargo.com/BW/equity.do?account={account_index}&symbol=&selectedAction=&{dynamic_x_param}"
                log(f"Navigating to fresh trade URL for stock '{stock_symbol}': {trade_url}")
                await page.get(trade_url)
                await page.wait_for_ready_state("complete")
                await page.wait()
                await page.select("#eqentryfrm", timeout=10)

                log(f"Processing order for stock: {stock_symbol} in account *{account_mask}")
                action = orderObj.get_action().capitalize()
                quantity = orderObj.get_amount()
                is_dry_run = orderObj.get_dry()
                
                printAndDiscord(f"Preparing to {action} {quantity} share(s) of {stock_symbol} for account ending in *{account_mask}.", discord_loop)

                # 1. Select Action
                try:
                    log("Clicking dropdown '#BuySellBtn' and selecting '{action}'.")
                    action_opener = await page.select("#BuySellBtn", timeout=10)
                    await action_opener.scroll_into_view()
                    await _select_dropdown_option(page, "#BuySellBtn", action)
                except asyncio.TimeoutError:
                    raise Exception("Failed to find the Action dropdown '#BuySellBtn'.")

                # 2. Enter Symbol
                try:
                    log(f"Entering symbol: {stock_symbol}")
                    symbol_input = await page.select("#Symbol", timeout=10)
                    await symbol_input.scroll_into_view()
                    await symbol_input.send_keys(stock_symbol)

                    await symbol_input.send_keys(SpecialKeys.TAB)

                    log("Waiting for quote to load...")
                    await page.select("#prevdata", timeout=10)
                except asyncio.TimeoutError:
                    raise Exception("Failed to find symbol input field.")

                # 2a. Check Owned Shares for Sell Orders
                if action == "Sell":
                    try:
                        log("Checking number of shares owned.")
                        # Selector for the span containing the number of shares
                        shares_element = await page.select("#currentSharesOwned .numshares", timeout=10)
                        
                        if not shares_element:
                            raise Exception("Could not find the 'shares owned' element on the page.")

                        owned_shares_text = shares_element.text_all.strip()
                        owned_shares = int(owned_shares_text)
                        log(f"Account owns {owned_shares} shares of {stock_symbol}.")

                        # Check if we have enough shares to sell
                        if owned_shares == 0:
                            message = f"Skipped selling {stock_symbol} in account *{account_mask}: You own 0 shares."
                            printAndDiscord(message, discord_loop)
                            log(message)
                            continue # Skip to the next stock
                        
                        if quantity > owned_shares:
                            message = f"Skipped selling {stock_symbol} in account *{account_mask}: Order quantity ({quantity}) exceeds shares owned ({owned_shares})."
                            printAndDiscord(message, discord_loop)
                            log(message)
                            continue # Skip to the next stock

                    except asyncio.TimeoutError:
                        log("Warning: Timed out waiting for the 'shares owned' element. Proceeding with caution.")
                    except Exception as e:
                        # Catch other errors like failing to parse the number
                        error_message = f"An error occurred while checking owned shares for {stock_symbol}: {e}"
                        printAndDiscord(error_message, discord_loop)
                        log(error_message)
                        continue # Skip to the next stock

                # 3. Get Quote & Determine Order Type
                try:
                    # The 'select' method will wait up to 10 seconds for the element to appear.
                    last_price_element = await page.select("#last", timeout=10)
                    await last_price_element.scroll_into_view()

                    # Add a check to ensure the element was actually found before using it.
                    if not last_price_element:
                        raise Exception("Price element #last could not be found after 10 seconds. The page may not have loaded the quote correctly.")
                    
                    last_price_str = last_price_element.get('value')
                    if not last_price_str: 
                        raise Exception("Price element #last was found, but its value is empty.")

                    last_price = float(last_price_str)
                    log(f"Last price for {stock_symbol} is ${last_price}")
                except asyncio.TimeoutError:
                    # This will catch the timeout from page.select and provide a clearer error.
                    raise Exception("Timed out waiting for the last price element (#last) to appear.")

                order_type = "Market" if last_price >= 2.00 else "Limit"
                log(f"Order type determined: {order_type}")

                # 4. Enter Quantity
                try:
                    log(f"Entering quantity: {quantity}")
                    # First, select the input element by its ID
                    quantity_input = await page.select("#OrderQuantity", timeout=10)
                    await quantity_input.scroll_into_view()
                    
                    # Clear any default value (like '0') that might be in the box
                    await quantity_input.clear_input()

                    # Then, type the new quantity into the element
                    payloads = KeyEvents.from_text(str(int(quantity)), KeyPressEvent.DOWN_AND_UP)  
                    await quantity_input.send_keys(payloads)
                    await quantity_input.send_keys(SpecialKeys.TAB)

                except asyncio.TimeoutError:
                    raise Exception("Failed to find the Quantity input field '#OrderQuantity'.")
                
                # 5. Select Order Type
                try:
                    log("Clicking dropdown '#OrderTypeBtn' and selecting '{order_type}'.")
                    ordertype = await page.select("#OrderTypeBtn", timeout=10)
                    await ordertype.scroll_into_view()
                    await _select_dropdown_option(page, "#OrderTypeBtn", order_type)
                except asyncio.TimeoutError:
                    raise Exception("Failed to find the Order Type dropdown '#OrderTypeBtn'.")

                # 6. Enter Limit Price
                if order_type == "Limit":
                    try:
                        limit_price = round(last_price + 0.01, 2) if action == "Buy" else round(last_price - 0.01, 2)
                        log(f"Calculating and entering Limit Price: ${limit_price}")
                        limit_input = await page.select("#Price", timeout=10)
                        await limit_input.scroll_into_view()
                        await limit_input.send_keys(str(limit_price))
                    except asyncio.TimeoutError:
                        raise Exception("Failed to find Limit Price input field.")
                
                # 7. Select Timing
                time_of_day =   await page.select("#TIFBtn", timeout=10)
                await time_of_day.scroll_into_view()
                await _select_dropdown_option(page, "#TIFBtn", "Day")

                # 8. Preview Order
                try:
                    log("Clicking PREVIEW ORDER button.")
                    preview_button = await page.select("#actionbtnContinue", timeout=10)
                    await preview_button.scroll_into_view()
                    await preview_button.mouse_click()
                    log("Preview Order button clicked. Waiting for next page to load.")
                    await page.wait_for_ready_state("complete", timeout=20)
                    await page.wait()
                    await page.sleep(0.25)
                except asyncio.TimeoutError:
                    raise Exception("Failed to find or click the Preview Order button.")

                # 9. Check for Confirmation Button (Hard Error Path)
                try:
                    # The key check: Can we find the final SUBMIT button?
                    # If we can't, it means we hit a hard error page.
                    confirm_button = await page.select(".btn-wfa-primary.btn-wfa-submit", timeout=10)
                    await confirm_button.scroll_into_view()
                    log("Confirmation button found. The trade is placeable.")
                except asyncio.TimeoutError:
                    # This is the HARD ERROR path. The submit button doesn't exist.
                    log("Confirmation button NOT found. Assuming a hard trade error occurred.")
                    try:
                        # Now, find the error message to report it.
                        error_element = await page.select(".alert-msg-summary p", timeout=10)
                        error_text = error_element.text_all.strip().replace("\n", " ")
                        full_error_message = f"Wells Fargo HARD Error for {stock_symbol}: {error_text}"
                        printAndDiscord(full_error_message, discord_loop)
                    except asyncio.TimeoutError:
                        # Fallback if we can't even find the error text
                        printAndDiscord(f"Wells Fargo HARD Error for {stock_symbol}: Confirmation page did not load, and no specific error message was found.", discord_loop)
                    
                    log(f"Skipping final confirmation for {stock_symbol} due to hard error.")
                    continue # Skip to the next stock

                # 10. Check for Soft Warnings
                try:
                    # If we got here, the trade is placeable. Now we check for non-critical warnings.
                    warning_element = await page.select(".alert-msg-summary p", timeout=2)
                    if warning_element:
                        warning_text = warning_element.text_all.strip().replace("\n", " ")
                        full_warning_message = f"Wells Fargo Warning for {stock_symbol}: {warning_text}"
                        printAndDiscord(full_warning_message, discord_loop)
                        log(full_warning_message)
                except asyncio.TimeoutError:
                    # This is the normal path when no warnings are present.
                    log("No soft warnings found on the page.")

                # 11. Final Confirmation
                if is_dry_run:
                    printAndDiscord(f"[DRY RUN] Successfully previewed order for {action} {quantity} of {stock_symbol}. Final confirmation skipped.", discord_loop)
                    log("Dry run complete. Skipping final confirmation click.")
                else:
                    try:
                        log("Attempting to click the final 'SUBMIT ORDER' button.")
                        # We use the 'confirm_button' element we already found in step 9
                        await confirm_button.mouse_click()
                        printAndDiscord(f"Successfully placed order for {action} {quantity} of {stock_symbol}.", discord_loop)
                        log("Final order placed successfully.")
                        await page.wait_for_ready_state("complete", timeout=20)
                        await page.wait()
                        await page.sleep(0.25)
                    except Exception as e:
                        # This catch is for potential errors during the final click itself
                        raise Exception(f"An error occurred while clicking the final 'SUBMIT ORDER' button: {e}")
            
            account_index += 1

    except Exception as e:
        await wellsfargo_error(f"Error during Wells Fargo transaction logic for {account_name_key}: {e}", page, discord_loop, browser)


async def wellsfargo_holdings(wf_brokerage_obj: Brokerage, account_name_key: str, browser: uc.Browser, page: uc.Tab, discord_loop):
    log(f"wellsfargo_holdings started for {account_name_key}.")
    try:
        if not wf_brokerage_obj.get_logged_in_objects(account_name_key):
            printAndDiscord(f"Not logged in for {account_name_key}, cannot fetch holdings.", discord_loop)
            return

        registered_accounts = wf_brokerage_obj.get_account_numbers(account_name_key)
        log(f"Starting holdings check for {len(registered_accounts)} accounts in '{account_name_key}'.")

        if not registered_accounts:
            printAndDiscord(f"No accounts registered for {account_name_key} from init.", discord_loop)
            return

        current_url = await get_current_url(page, discord_loop)
        x_param_match = re.search(r'_x=([^&]+)', current_url)
        current_x_param = f"_x={x_param_match.group(1)}" if x_param_match else ""

        for account_id in registered_accounts:
            try:
                if not hasattr(wf_brokerage_obj, '_account_indices'):
                    log("CRITICAL - _account_indices attribute not found on brokerage object. Cannot look up account index.")
                    continue
                    
                account_data = wf_brokerage_obj._account_indices.get(account_name_key, {}).get(account_id, {})
                account_index = account_data.get('index', '')
                stored_x_param = account_data.get('x_param', '')
                
                log(f"Processing holdings for account '{account_id}'. Stored index: '{account_index}'.")

                if not account_index:
                    log(f"Skipping account '{account_id}' - no account index stored.")
                    continue
                
                x_param_to_use = stored_x_param if stored_x_param else current_x_param
                
                holdings_url = f"https://wfawellstrade.wellsfargo.com/BW/holdings.do?account={account_index}"
                if x_param_to_use:
                    holdings_url += f"&{x_param_to_use}"
                
                log(f"Navigating to holdings URL: {holdings_url}")
                await page.get(holdings_url)
                await page.wait_for_ready_state("complete")
                await page.wait()
                await page.find("row m-0")
                
                await extract_holdings_from_table(page, wf_brokerage_obj, account_name_key, account_id, discord_loop)
                
            except Exception as e_account:
                await wellsfargo_error(f"Error processing account {account_id}: {e_account}", page, discord_loop)
        
        printHoldings(wf_brokerage_obj, discord_loop)
        log("printHoldings called.")
        
    except Exception as e:
        current_page_for_error = page if page and not page.closed else (browser.tabs[0] if browser and browser.tabs else None)
        await wellsfargo_error(f"Error fetching Wells Fargo holdings for {account_name_key}: {e}", current_page_for_error, discord_loop, browser)

async def extract_holdings_from_table(page: uc.Tab, wf_brokerage_obj: Brokerage, login_key: str, current_wf_account_id: str, discord_loop):
    log(f"Extracting holdings from table for account '{current_wf_account_id}'.")
    try:
        content = await page.get_content()
        soup = BeautifulSoup(content, 'html.parser')
        
        holding_rows = soup.select('tbody > tr.level1')
        log(f"Found {len(holding_rows)} holding rows in table.")

        for row in holding_rows:
            try:
                symbol_el = row.select_one('a.navlink.quickquote')
                if not symbol_el:
                    continue 
                symbol = symbol_el.text.replace(",popup", "").strip()
                
                name_el = row.select_one('td[role="rowheader"] .data-content > div:last-child')
                name = name_el.get_text(strip=True) if name_el else "N/A" 

                all_numeric_cells = row.select('td.datanumeric')
                
                quantity = 0.0
                price = 0.0

                if len(all_numeric_cells) > 2:
                    qty_div = all_numeric_cells[1].select_one('div:first-child')
                    if qty_div:
                        quantity_text = qty_div.get_text(strip=True)
                        quantity = float(quantity_text)
                    
                    price_div = all_numeric_cells[2].select_one('div:first-child')
                    if price_div:
                        price_text = price_div.get_text(strip=True).replace('$', '').replace(',', '')
                        price = float(price_text)

                if symbol and quantity > 0:
                    log(f"Found holding: {quantity} of {symbol} @ ${price}")
                    wf_brokerage_obj.set_holdings(login_key, current_wf_account_id, symbol, quantity, price)
                    
            except Exception as e_row:
                await wellsfargo_error(f"Error processing a specific holdings row: {e_row}", page, discord_loop)

    except Exception as e_table:
        await wellsfargo_error(f"Error during main extraction logic: {e_table}", page, discord_loop)
