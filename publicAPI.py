import os
import traceback
import uuid
from decimal import Decimal, ROUND_HALF_UP # Added ROUND_HALF_UP
from datetime import datetime, time
import pytz

from dotenv import load_dotenv
from public_api_sdk import (
    InstrumentType,
    OrderExpirationRequest,
    OrderInstrument,
    OrderRequest,
    PublicApiClient,
    AccountType,
    PreflightRequest,
    PreflightResponse,
    OrderSide,
    OrderType,
    TimeInForce,
)
from public_api_sdk.auth_config import ApiKeyAuthConfig
from email_validator import validate_email, EmailNotValidError

from helperAPI import (
    Brokerage,
    maskString,
    printAndDiscord,
    printHoldings,
    stockOrder,
)

load_dotenv()
DEBUG = os.getenv("PUBLIC_DEBUG", "false").lower() == "true"


def fetch_public_price(obj: PublicApiClient, symbol: str, account_id: str, order_action: str, session_type: str) -> float:
    """Fetches the appropriate price for a symbol based on session and action."""
    try:
        instrument = OrderInstrument(symbol=symbol, type=InstrumentType.EQUITY)
        quotes = obj.get_quotes(instruments=[instrument], account_id=account_id)

        if not quotes:
            raise Exception("No quote returned.")

        quote = quotes[0]
        price = None

        if session_type == "EXTENDED":
            log_debug(f"Extended hours price fetch for {symbol}: Action={order_action}, Ask={quote.ask}, Bid={quote.bid}, Last={quote.last}")
            if order_action == "BUY":
                price = quote.ask or quote.last or quote.bid
                log_debug(f"Prioritizing Ask for BUY: selected {price}")
            elif order_action == "SELL":
                price = quote.bid or quote.last or quote.ask
                log_debug(f"Prioritizing Bid for SELL: selected {price}")
            else:
                price = quote.last or quote.ask or quote.bid
                log_debug(f"Default price selection: selected {price}")
        else: # CORE session
            price = quote.last or quote.ask or quote.bid
            log_debug(f"Core hours price fetch for {symbol}: selected {price}")

        if price is None:
            raise Exception("Quote returned, but couldn't determine a valid price (last, ask, bid are all null).")

        price_float = float(price)
        log_debug(f"Fetched price for {symbol} ({session_type}/{order_action}): {price_float}")
        return price_float

    except Exception as e:
        printAndDiscord(f"Error fetching price for {symbol}: {e}", None)
        return 0.0


def log_debug(message):
    """Prints a message to the console only if DEBUG is True in .env."""
    if DEBUG:
        print(f"[DEBUG] {message}")


def get_public_trading_session():
    """Determines the correct Public.com trading session based on the current time."""
    try:
        et_timezone = pytz.timezone("America/New_York")
        now_et = datetime.now(et_timezone)

        if now_et.weekday() >= 5:
            log_debug("Market is closed (weekend). Defaulting to CORE.")
            return "CORE"

        core_start = time(9, 30)
        core_end = time(16, 0)
        ext_start = time(8, 0)
        ext_end = time(20, 0)
        current_time = now_et.time()

        if core_start <= current_time < core_end:
            log_debug("Market is open: CORE.")
            return "CORE"
        elif ext_start <= current_time < ext_end:
            log_debug("Market is in extended session: EXTENDED.")
            return "EXTENDED"
        else:
            log_debug("Market is closed (outside all sessions). Defaulting to CORE.")
            return "CORE"

    except Exception as e:
        print(f"Error determining trading session: {e}. Defaulting to CORE.")
        return "CORE"


def public_init(PUBLIC_EXTERNAL: str | None = None, botObj=None, loop=None):
    # (Initialization logic remains the same)
    load_dotenv()
    public_obj = Brokerage("Public")
    if not os.getenv("PUBLIC_BROKER") and PUBLIC_EXTERNAL is None:
        print("Public not found, skipping...")
        return None
    PUBLIC = (
        os.environ["PUBLIC_BROKER"].strip().split(",")
        if PUBLIC_EXTERNAL is None
        else PUBLIC_EXTERNAL.strip().split(",")
    )
    print("Logging in to Public...")
    for index, account_key in enumerate(PUBLIC): # Renamed 'account' to 'account_key' for clarity
        name = f"Public {index + 1}"
        try:
            test_account = account_key.split(":")[0]
            try:
                validate_email(test_account)
                printAndDiscord(
                    f"{name}: Public no longer supports email login. Please switch to API tokens.", loop
                )
                continue
            except EmailNotValidError:
                pass
            pb = PublicApiClient(ApiKeyAuthConfig(api_secret_key=account_key))
            public_obj.set_logged_in_object(name, pb, "pb")
            accounts = pb.get_accounts().accounts
            for pub_account in accounts:
                public_obj.set_account_number(name, pub_account.account_id)
                print(f"{name}: Found account {maskString(pub_account.account_id)}")
                public_obj.set_account_type(
                    name, pub_account.account_id, pub_account.account_type
                )
                portfolio = pb.get_portfolio(account_id=pub_account.account_id) # Renamed 'cash' to 'portfolio'
                # Ensure buying_power and cash_only_buying_power exist before converting
                bp = portfolio.buying_power
                cash_bp_value = bp.cash_only_buying_power if bp and bp.cash_only_buying_power is not None else Decimal("0.0")
                public_obj.set_account_totals(
                    name,
                    pub_account.account_id,
                    float(cash_bp_value),
                )
        except Exception as e:
            print(f"Error logging in to {name}: {e}") # Include name in error
            print(traceback.format_exc())
            continue
    print("Logged in to Public!")
    return public_obj


def public_holdings(pbo: Brokerage, loop=None):
    # (Holdings logic remains the same)
    for key in pbo.get_account_numbers():
        for account_id in pbo.get_account_numbers(key): # Renamed 'account' to 'account_id'
            obj: PublicApiClient = pbo.get_logged_in_objects(key, "pb")
            try:
                positions = obj.get_portfolio(account_id=account_id).positions
                if positions: # Simplified check
                    for holding in positions:
                        sym = holding.instrument.symbol
                        qty = float(holding.quantity)
                        # Safely access last_price
                        current_price_val = None
                        if holding.last_price and holding.last_price.last_price is not None:
                             current_price_val = float(holding.last_price.last_price)

                        pbo.set_holdings(key, account_id, sym, qty, current_price_val if current_price_val is not None else "N/A")
            except Exception as e:
                printAndDiscord(f"{key} ({maskString(account_id)}): Error getting holdings: {e}", loop) # Include account in error
                traceback.print_exc()
                continue
    printHoldings(pbo, loop)


# --- Refactored Transaction Logic ---
def public_transaction(pbo: Brokerage, orderObj: stockOrder, loop=None):
    print("\n==============================\nPublic\n==============================\n")
    session_type = get_public_trading_session()
    is_dry_run = orderObj.get_dry()
    mode_prefix = "DRY RUN" if is_dry_run else "LIVE"

    for s in orderObj.get_stocks():
        for key in pbo.get_account_numbers():
            printAndDiscord(f"{key}: {orderObj.get_action()}ing {orderObj.get_amount()} of {s}", loop)

            for account_id in pbo.get_account_numbers(key):
                account_type = pbo.get_account_types(key, account_id)
                print_account = maskString(account_id)

                tradable_accounts = [AccountType.BROKERAGE, AccountType.ROTH_IRA, AccountType.TRADITIONAL_IRA]
                if account_type not in tradable_accounts:
                    print(f"{print_account}: Skipping, account type is {account_type} (non-tradable).")
                    continue

                obj: PublicApiClient = pbo.get_logged_in_objects(key, "pb")
                order_action = orderObj.get_action().upper()
                order_quantity_decimal = Decimal(str(orderObj.get_amount())) # Use Decimal directly

                try:
                    # --- CORE Session Logic ---
                    if session_type == "CORE":
                        log_prefix = f"{mode_prefix} (CORE) [{print_account}]"
                        printAndDiscord(f"{log_prefix}: Preparing MARKET order for {s}...", loop)

                        # Core Preflight (Optional but good practice)
                        core_preflight_request = PreflightRequest(
                            instrument=OrderInstrument(symbol=s, type=InstrumentType.EQUITY),
                            order_side=OrderSide(order_action),
                            order_type=OrderType.MARKET,
                            expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY),
                            quantity=order_quantity_decimal,
                        )
                        pf_response = obj.perform_preflight_calculation(core_preflight_request, account_id=account_id)
                        log_debug(f"{log_prefix}: Preflight estimated commission: ${pf_response.estimated_commission}")

                        if not is_dry_run:
                            # Place CORE Market Order
                            core_order_request = OrderRequest(
                                order_id=str(uuid.uuid4()),
                                instrument=OrderInstrument(symbol=s, type=InstrumentType.EQUITY),
                                order_side=OrderSide(order_action),
                                order_type=OrderType.MARKET,
                                expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY),
                                quantity=order_quantity_decimal,
                            )
                            obj.place_order(core_order_request, account_id=account_id)
                            printAndDiscord(f"{log_prefix}: MARKET order for {s} placed successfully.", loop)
                        else:
                            printAndDiscord(f"{log_prefix}: Preflight check for {s} complete.", loop)

                    # --- EXTENDED Session Logic ---
                    elif session_type == "EXTENDED":
                        log_prefix = f"{mode_prefix} (EXTENDED) [{print_account}]"
                        printAndDiscord(f"{log_prefix}: Preparing LIMIT order preflight for {s}...", loop)

                        # 1. Fetch Price
                        price = fetch_public_price(obj, s, account_id, order_action, session_type)
                        if price == 0.0:
                            raise Exception("Could not fetch valid price.")

                        # 2. Calculate Limit Price (Add/Subtract $0.01)
                        price_decimal = Decimal(str(price))
                        limit_price = Decimal("0.00") # Initialize
                        if order_action == "BUY":
                            limit_price = price_decimal + Decimal("0.01")
                            log_debug(f"{log_prefix}: Calculated BUY limit price {limit_price} (Base: {price}) -> Serialized: {limit_price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}")
                        else: # SELL
                            limit_price_raw = price_decimal - Decimal("0.01")
                            limit_price = max(limit_price_raw, Decimal("0.0001")) # Ensure > 0
                            log_debug(f"{log_prefix}: Calculated SELL limit price {limit_price} (Base: {price}) -> Serialized: {limit_price.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}")

                        printAndDiscord(f"{log_prefix}: Using calculated limit price {limit_price} (Base: {price}) for preflight/order.", loop)

                        # 3. Perform Preflight Check (Mandatory for Extended Hours)
                        ext_preflight_request = PreflightRequest(
                            instrument=OrderInstrument(symbol=s, type=InstrumentType.EQUITY),
                            order_side=OrderSide(order_action),
                            order_type=OrderType.LIMIT, # Must be LIMIT
                            expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY), # DAY is correct
                            quantity=order_quantity_decimal,
                            limit_price=limit_price # Use calculated limit price
                        )
                        pf_response = obj.perform_preflight_calculation(ext_preflight_request, account_id=account_id)
                        commission = pf_response.estimated_commission or Decimal("0.00") # Default to 0 if None

                        printAndDiscord(f"{log_prefix}: Preflight estimated commission for {s}: ${commission}", loop)

                        # 4. Check Commission and Decide Action
                        if commission != Decimal("0.00"):
                            printAndDiscord(f"{log_prefix}: Trade BLOCKED for {s}. Commission is ${commission}.", loop)
                            continue # Skip this account/stock if commission found

                        # 5. Place Order (if not dry run and commission is zero)
                        if not is_dry_run:
                            printAndDiscord(f"{log_prefix}: Preflight OK. Placing LIMIT order for {s} at calculated limit {limit_price}...", loop)
                            ext_order_request = OrderRequest(
                                order_id=str(uuid.uuid4()),
                                instrument=OrderInstrument(symbol=s, type=InstrumentType.EQUITY),
                                order_side=OrderSide(order_action),
                                order_type=OrderType.LIMIT, # Must be LIMIT
                                expiration=OrderExpirationRequest(time_in_force=TimeInForce.DAY),
                                quantity=order_quantity_decimal,
                                limit_price=limit_price # Use calculated limit price
                            )
                            obj.place_order(ext_order_request, account_id=account_id)
                            printAndDiscord(f"{log_prefix}: LIMIT order for {s} placed successfully.", loop)
                        else:
                            # Dry run completed successfully with zero commission
                            printAndDiscord(f"{log_prefix}: Commission is ZERO. Trade would be placed.", loop)

                except Exception as e:
                    error_prefix = f"{mode_prefix} [{print_account}]"
                    printAndDiscord(f"{error_prefix}: Error processing order for {s}: {e}", loop)
                    traceback.print_exc()
                    continue # Continue to next account/stock on error
