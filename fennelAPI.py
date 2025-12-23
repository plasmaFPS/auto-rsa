
import asyncio
import os
import traceback
from typing import cast
from fennel_invest_api import Fennel
from dotenv import load_dotenv

from helperAPI import (
    Brokerage,
    stockOrder,
    getOTPCodeDiscord,
    printAndDiscord,
    printHoldings,
    maskString,
)

def fennel_init(FENNEL_EXTERNAL=None, botObj=None, loop=None):
    """Initialize Fennel API."""
    # Initialize .env file
    load_dotenv()
    # Import Fennel account
    fennel_obj = Brokerage("Fennel")
    if not os.getenv("FENNEL") and FENNEL_EXTERNAL is None:
        print("Fennel not found, skipping...")
        return None
    
    FENNEL = (
        os.environ["FENNEL"].strip().split(",")
        if FENNEL_EXTERNAL is None
        else FENNEL_EXTERNAL.strip().split(",")
    )
    
    # Log in to Fennel account
    print("Logging in to Fennel...")
    for index, account in enumerate(FENNEL):
        name = f"Fennel {index + 1}"
        try:
            fb = login_fennel_account(account, index, botObj, loop, name)
            fennel_obj.set_logged_in_object(name, fb, "fb")
            populate_fennel_accounts(fennel_obj, fb, name, loop)
            print(f"{name}: Logged in")
        except Exception as e:
            print(f"Error logging into Fennel: {e}")
            print(traceback.format_exc())
            continue
    print("Logged into Fennel!")
    return fennel_obj


def login_fennel_account(
    account: str,
    index: int,
    botObj,
    loop,
    name: str,
) -> Fennel:
    fb = Fennel(filename=f"fennel{index + 1}.pkl", path="./creds/")
    try:
        if botObj is None or loop is None:
            fb.login(email=account, wait_for_code=True)
        else:
            fb.login(email=account, wait_for_code=False)
    except Exception as exc:
        if "2FA" not in str(exc) or botObj is None or loop is None:
            raise exc
            
        printAndDiscord(f"{name}: 2FA Code Required", loop)
        otp_code = asyncio.run_coroutine_threadsafe(
            getOTPCodeDiscord(botObj, name, timeout=300, loop=loop),
            loop,
        ).result()
        
        if otp_code is None:
            printAndDiscord(f"{name}: OTP code not received, aborting login", loop)
            raise exc
        fb.login(email=account, wait_for_code=False, code=otp_code)
    return fb


def populate_fennel_accounts(
    fennel_obj: Brokerage,
    fb: Fennel,
    name: str,
    loop,
) -> None:
    try:
        full_accounts = fb.get_full_accounts()
        populate_from_full_accounts(fennel_obj, name, full_accounts, loop)
    except AttributeError:
        # Fallback if get_full_accounts not available
        account_ids = fb.get_account_ids()
        populate_from_account_ids(fennel_obj, fb, name, account_ids, loop)


def populate_from_account_ids(
    fennel_obj: Brokerage,
    fb: Fennel,
    name: str,
    account_ids: list,
    loop,
) -> None:
    for account_index, account_id in enumerate(account_ids):
        account_name = f"Account {account_index + 1}"
        fennel_obj.set_account_number(name, account_name)
        try:
            summary = fb.get_portfolio_summary(account_id)
            total_cash = summary["cash"]["balance"]["canTrade"]
        except Exception:
             printAndDiscord(
                f"{name} {account_name}: Unable to fetch portfolio summary, using 0 total",
                loop,
            )
             total_cash = 0
             
        fennel_obj.set_account_totals(
            name,
            account_name,
            total_cash,
        )
        fennel_obj.set_logged_in_object(name, account_id, account_name)
        print(f"Found {account_name}")


def populate_from_full_accounts(
    fennel_obj: Brokerage,
    name: str,
    full_accounts: list,
    loop,
) -> None:
    for account_info in full_accounts:
        account_name = account_info["name"]
        fennel_obj.set_account_number(name, account_name)
        try:
            total_cash = account_info["portfolio"]["cash"]["balance"]["canTrade"]
        except KeyError:
            printAndDiscord(
                f"{name} {account_info.get('name', 'Account')}: Unable to read portfolio summary, using 0 total",
                loop,
            )
            total_cash = 0
        fennel_obj.set_account_totals(
            name,
            account_name,
            total_cash,
        )
        # Store account ID keyed by account name
        fennel_obj.set_logged_in_object(name, account_info["id"], account_name)
        print(f"Found {account_name}")


def fennel_holdings(fbo: Brokerage, loop=None) -> None:
    """Retrieve and display all Fennel account holdings."""
    for key in fbo.get_account_numbers():
        obj = cast("Fennel", fbo.get_logged_in_objects(key, "fb"))
        for account in fbo.get_account_numbers(key):
            try:
                # Get account holdings using the stored account_id
                account_id = fbo.get_logged_in_objects(key, account)
                positions = obj.get_stock_holdings(account_id)
                if positions:
                    for holding in positions:
                        qty = holding["investment"]["ownedShares"]
                        if float(qty) == 0:
                            continue
                        sym = holding["security"]["ticker"]
                        try:
                            price = holding["security"]["currentStockPrice"]
                            if price is None:
                                price = "N/A"
                        except:
                            price = "N/A"
                        fbo.set_holdings(key, account, sym, qty, price)
            except Exception as e:
                printAndDiscord(f"Error getting Fennel holdings: {e}", loop)
                print(traceback.format_exc())
                continue
    printHoldings(fbo, loop, mask=False)


def fennel_transaction(fbo: Brokerage, orderObj: stockOrder, loop=None) -> None:
    """Handle Fennel API transactions."""
    print()
    print("==============================")
    print("Fennel")
    print("==============================")
    print()
    print(f"Mode: {'DRY RUN' if orderObj.get_dry() else 'REAL TRADE'}")
    for s in orderObj.get_stocks():
        for key in fbo.get_account_numbers():
            printAndDiscord(
                f"{key}: {orderObj.get_action()}ing {orderObj.get_amount()} of {s}",
                loop,
            )
            for account in fbo.get_account_numbers(key):
                obj = cast("Fennel", fbo.get_logged_in_objects(key, "fb"))
                account_id = cast("str", fbo.get_logged_in_objects(key, account))
                try:
                    order = obj.place_order(
                        account_id=account_id,
                        ticker=s,
                        quantity=orderObj.get_amount(),
                        side=orderObj.get_action(),
                        dry_run=orderObj.get_dry(),
                    )
                    
                    if orderObj.get_dry():
                        message = "Dry Run Success"
                        if not order.get("dry_run_success", False):
                            message = "Dry Run Failed"
                    else:
                        message = "Success"
                        if order.get("data", {}).get("createOrder") != "pending":
                            message = order.get("data", {}).get("createOrder")
                            
                    printAndDiscord(
                        f"{key}: {orderObj.get_action()} {orderObj.get_amount()} of {s} in {account}: {message}",
                        loop,
                    )
                except Exception as e:
                    printAndDiscord(f"{key} {account}: Error placing order: {e}", loop)
                    print(traceback.format_exc())
                    continue
