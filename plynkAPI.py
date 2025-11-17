# plynkAPI.py

import asyncio
import os
import traceback

from dotenv import load_dotenv
from plynk_api import Plynk #type: ignore

from helperAPI import (
    Brokerage,
    getOTPCodeDiscord,
    maskString,
    printAndDiscord,
    printHoldings,
    stockOrder,
)


def plynk_init(PLYNK_EXTERNAL=None, botObj=None, loop=None):
    """
    Initializes the Plynk session and logs in the user.
    """
    load_dotenv()
    plynk_obj = Brokerage("Plynk")
    if not os.getenv("PLYNK") and PLYNK_EXTERNAL is None:
        print("Plynk not found, skipping...")
        return None
        
    PLYNK_DEBUG = os.getenv("PLYNK_DEBUG", "false").lower() == "true"

    PLYNK = (
        os.environ["PLYNK"].strip().split(",")
        if PLYNK_EXTERNAL is None
        else PLYNK_EXTERNAL.strip().split(",")
    )
    print("Logging in to Plynk...")
    for index, account in enumerate(PLYNK):
        name = f"Plynk {index + 1}"
        try:
            username, password = account.split(":")
            plynk = Plynk(
                username=username,
                password=password,
                filename=f"plynk-creds-{index + 1}.pkl",
                path="./creds/",
                debug=PLYNK_DEBUG,
            )

            def get_otp_code():
                """
                Callback function to get OTP code from Discord.
                """
                if botObj is not None and loop is not None:
                    return asyncio.run_coroutine_threadsafe(
                        getOTPCodeDiscord(botObj, name, timeout=300, loop=loop), loop
                    ).result()
                else:
                    return input("Enter OTP code: ")

            try:
                plynk.login(otp_callback=get_otp_code)
            except RuntimeError as e:
                print(f"Failed to login to Plynk: {e}")
                continue

            account_number = plynk.account_number
            account_total = plynk.get_account_total(account_number)

            plynk_obj.set_logged_in_object(name, plynk)
            plynk_obj.set_account_number(name, account_number)
            plynk_obj.set_account_totals(name, account_number, account_total)

            print(f"Logged into Plynk! Account: {maskString(account_number)}")

        except Exception as e:
            print(f"Error logging into Plynk: {e}")
            print(traceback.format_exc())
            continue

    return plynk_obj


def plynk_holdings(pbo: Brokerage, loop=None):
    """
    Fetches and displays the holdings for each Plynk account.
    """
    # First, fetch and populate all holdings data
    for key in pbo.get_account_numbers():
        for account_number in pbo.get_account_numbers(key):
            plynk: Plynk = pbo.get_logged_in_objects(key)
            try:
                holdings = plynk.get_account_holdings(account_number)
                for holding in holdings:
                    security_info = holding.get("security", {})
                    stock_symbol = security_info.get("symbol")

                    if not stock_symbol:
                        holding_name = security_info.get("name", "Unknown Holding")
                        print(f"Skipping a holding ('{holding_name}') because it has no ticker symbol.", loop)
                        continue
                    
                    current_value = holding["currentValue"]
                    quantity = holding["securityCount"]
                    price = (
                        current_value / quantity if quantity > 0 else 0
                    )
                    pbo.set_holdings(
                        key, account_number, stock_symbol, quantity, price
                    )
            except Exception as e:
                printAndDiscord(f"Error getting Plynk holdings: {e}", loop)
                print(traceback.format_exc())
                continue

    # Print holdings using the helper function
    printHoldings(pbo, loop=loop)

def plynk_transaction(pbo: Brokerage, orderObj: stockOrder, loop=None):
    """
    Executes a transaction (buy or sell) on a Plynk account.
    """
    print()
    print("==============================")
    print("Plynk")
    print("==============================")
    print()

    for stock in orderObj.get_stocks():
        for key in pbo.get_account_numbers():
            action = orderObj.get_action().lower()
            amount = orderObj.get_amount()
            printAndDiscord(f"{key}: {action}ing {amount} of {stock}", loop)

            for account_number in pbo.get_account_numbers(key):
                plynk: Plynk = pbo.get_logged_in_objects(key)
                try:
                    stock_price = plynk.get_stock_price(stock)
                    if stock_price < 1.0 and action == "buy":
                        printAndDiscord(
                            f"{stock} is under $1. Plynk requires buying by dollar amount for stocks under $1. This is not yet implemented.",
                            loop,
                        )
                        continue

                    order = plynk.place_order_quantity(
                        account_number=account_number,
                        ticker=stock,
                        quantity=amount,
                        side=action,
                        dry_run=orderObj.get_dry(),
                    )
                    
                    if orderObj.get_dry():
                        message = "Dry Run Success" if order.get("dry_run_success") else "Dry Run Failed"
                    else:
                        message = "Order Placed" if order else "Order Failed"

                    printAndDiscord(
                        f"{key}: {orderObj.get_action().capitalize()} {amount} of {stock} in {maskString(account_number)}: {message}",
                        loop,
                    )

                except Exception as e:
                    printAndDiscord(
                        f"{key} {maskString(account_number)}: Error placing order: {e}",
                        loop,
                    )
                    print(traceback.format_exc())
                    continue
