# Nelson Dane
# Script to automate RSA stock purchases

# Import libraries
import asyncio
import os
import sys
import traceback

# Check Python version (minimum 3.10, maximum 3.13)
print("Python version:", sys.version)
if sys.version_info < (3, 10) or sys.version_info >= (3, 14):
    print("Error: Python version must be minimum 3.10 or maximum 3.13")
print()

try:
    import discord
    from discord.ext import commands
    from dotenv import load_dotenv

    # Custom API libraries
    from bbaeAPI import *
    from chaseAPI import *
    from dspacAPI import *
    from fennelAPI import *
    from fidelityAPI import *
    from firstradeAPI import *
    from helperAPI import (
        ThreadHandler,
        check_package_versions,
        maskString,
        printAndDiscord,
        stockOrder,
        updater,
    )
    from plynkAPI import *
    from publicAPI import *
    from robinhoodAPI import *
    from schwabAPI import *
    from sofiAPI import *
    from tastyAPI import *
    from tornadoAPI import *
    from tradierAPI import *
    from vanguardAPI import *
    from webullAPI import * # API ISSUES WHEN TRADING, SEE webullAPI.py FOR DETAILS
    from wellsfargoAPI import *
    from historyAPI import save_history, get_history_report
except Exception as e:
    print(f"Error importing libraries: {e}")
    print(traceback.format_exc())
    print("Please run 'pip install -r requirements.txt'")
    sys.exit(1)

# Initialize .env file
load_dotenv()

# Global variables
SUPPORTED_BROKERS = [
    "bbae",
    "chase",
    "dspac",
    "fennel",
    "fidelity",
    "firstrade",
    "plynk",
    "public",
    "robinhood",
    "schwab",
    "sofi",
    "tastytrade",
    "tornado",
    "tradier",
    "vanguard",
    "webull",
    "wellsfargo",
]
EXTENDED_HOURS = [
    "public",
    "sofi",
    "vanguard",
    "fidelity",
]
DISCORD_BOT = False
DOCKER_MODE = False
DANGER_MODE = False


# Account nicknames
def nicknames(broker):
    if broker == "bb":
        return "bbae"
    if broker == "ds":
        return "dspac"
    if broker in ["fid", "fido"]:
        return "fidelity"
    if broker == "ft":
        return "firstrade"
    if broker == "rh":
        return "robinhood"
    if broker == "tasty":
        return "tastytrade"
    if broker == "vg":
        return "vanguard"
    if broker == "wb":
        return "webull"
    if broker == "wf":
        return "wellsfargo"
    return broker


# Runs the specified function for each broker in the list
# broker name + type of function
def fun_run(orderObj: stockOrder, command, botObj=None, loop=None):
    if command in [("_init", "_holdings"), ("_init", "_transaction")]:
        totalValue = 0
        for broker in orderObj.get_brokers():
            if broker in orderObj.get_notbrokers():
                continue
            broker = nicknames(broker)
            first_command, second_command = command
            try:
                # Initialize broker
                fun_name = broker + first_command
                if broker.lower() == "tornado":
                    # Requires docker mode argument and loop
                    orderObj.set_logged_in(
                        globals()[fun_name](DOCKER=DOCKER_MODE, loop=loop),
                        broker,
                    )

                elif broker.lower() in [
                    "bbae",
                    "dspac",
                    "fennel",
                    "firstrade",
                    "public",
                    "robinhood",
                ]:
                    # Requires bot object and loop
                    orderObj.set_logged_in(
                        globals()[fun_name](botObj=botObj, loop=loop), broker
                    )
                elif broker.lower() in ["chase", "vanguard"]:
                    # These use existing logic without DOCKER arg
                    fun_name = broker + "_run"
                    th = ThreadHandler(
                        globals()[fun_name],
                        orderObj=orderObj,
                        command=command,
                        botObj=botObj,
                        loop=loop,
                    )
                    th.start()
                    th.join()
                    _, err = th.get_result()
                    if err is not None:
                        raise Exception(
                            "Error in "
                            + fun_name
                            + ": Function did not complete successfully."
                        )
                        
                elif broker.lower() in ["sofi", "wellsfargo", "fidelity"]:
                    # These now accept the DOCKER argument
                    fun_name = broker + "_run"
                    th = ThreadHandler(
                        globals()[fun_name],
                        orderObj=orderObj,
                        command=command,
                        botObj=botObj,
                        loop=loop,
                        DOCKER=DOCKER_MODE, # Pass the global DOCKER_MODE flag
                    )
                    th.start()
                    th.join()
                    _, err = th.get_result()
                    if err is not None:
                        raise Exception(f"Error in {fun_name}: {err}")
                
                else:
                    orderObj.set_logged_in(globals()[fun_name](), broker)

                print()
                if broker.lower() not in ["chase", "fidelity", "sofi", "vanguard", "wellsfargo"]:
                    # Verify broker is logged in
                    orderObj.order_validate(preLogin=False)
                    logged_in_broker = orderObj.get_logged_in(broker)
                    if logged_in_broker is None:
                        print(f"Error: {broker} not logged in, skipping...")
                        continue
                    # Get holdings or complete transaction
                    if second_command == "_holdings":
                        fun_name = broker + second_command
                        globals()[fun_name](logged_in_broker, loop)
                    elif second_command == "_transaction":
                        fun_name = broker + second_command
                        globals()[fun_name](
                            logged_in_broker,
                            orderObj,
                            loop,
                        )
                        printAndDiscord(
                            f"All {broker.capitalize()} transactions complete",
                            loop,
                        )
                logged_in_broker = orderObj.get_logged_in(broker)

                if logged_in_broker is not None:
                    totalValue += sum(
                        account["total"]
                        for account in orderObj.get_logged_in(broker)
                        .get_account_totals()
                        .values()
                    )
                    # Save history
                    if "_holdings" in command:
                        save_history(logged_in_broker)
            except Exception as ex:
                print(traceback.format_exc())
                print(f"Error in {fun_name} with {broker}: {ex}")
                print(orderObj)
            print()

        # Print final total value and closing message
        if "_holdings" in command:
            printAndDiscord(
                f"Total Value of All Accounts: ${format(totalValue, '0.2f')}", loop
            )
        printAndDiscord("All commands complete in all brokers", loop)
    else:
        print(f"Error: {command} is not a valid command")


# Parse input arguments and update the order object
def argParser(args: list) -> stockOrder:
    args = [x.lower() for x in args]
    # Initialize order object
    orderObj = stockOrder()
    # If first argument is holdings, set holdings to true
    if args[0] == "holdings":
        orderObj.set_holdings(True)
        # Next argument is brokers
        if args[1] == "all":
            orderObj.set_brokers(SUPPORTED_BROKERS)
        elif args[1] == "extended":
            orderObj.set_brokers(EXTENDED_HOURS)
        else:
            for broker in args[1].split(","):
                orderObj.set_brokers(nicknames(broker))
        # If next argument is not, set not broker
        if len(args) > 3 and args[2] == "not":
            for broker in args[3].split(","):
                if nicknames(broker) in SUPPORTED_BROKERS:
                    orderObj.set_notbrokers(nicknames(broker))
        return orderObj
    # Otherwise: action, amount, stock, broker, (optional) not broker, (optional) dry
    orderObj.set_action(args[0])
    orderObj.set_amount(args[1])
    for stock in args[2].split(","):
        if stock != "":
            orderObj.set_stock(stock)
    # Next argument is a broker, set broker
    if args[3] == "all":
        orderObj.set_brokers(SUPPORTED_BROKERS)
    elif args[3] == "extended":
        orderObj.set_brokers(EXTENDED_HOURS)
    else:
        for broker in args[3].split(","):
            if nicknames(broker) in SUPPORTED_BROKERS:
                orderObj.set_brokers(nicknames(broker))
    # If next argument is not, set not broker
    if len(args) > 4 and args[4] == "not":
        for broker in args[5].split(","):
            if nicknames(broker) in SUPPORTED_BROKERS:
                orderObj.set_notbrokers(nicknames(broker))
    # If next argument is false, set dry to false
    if args[-1] == "false":
        orderObj.set_dry(False)
    # Validate order object
    orderObj.order_validate(preLogin=True)
    return orderObj


if __name__ == "__main__":
    # Determine if ran from command line
    if len(sys.argv) == 1:  # If no arguments, do nothing
        print("No arguments given, see README for usage")
        sys.exit(1)
    # Check if danger mode is enabled
    if os.getenv("DANGER_MODE", "").lower() == "true":
        DANGER_MODE = True
        print("DANGER MODE ENABLED")
        print()
    # If docker argument, run docker bot
    if sys.argv[1].lower() == "docker":
        print("Running bot from docker")
        DOCKER_MODE = DISCORD_BOT = True
    # If discord argument, run discord bot, no docker, no prompt
    elif sys.argv[1].lower() == "discord":
        updater()
        check_package_versions()
        print("Running Discord bot from command line")
        DISCORD_BOT = True
    else:  # If any other argument, run bot, no docker or discord bot
        updater()
        check_package_versions()
        print("Running bot from command line")
        print()
        if sys.argv[1].lower() == "history":
            print(get_history_report())
            sys.exit(0)
        cliOrderObj = argParser(sys.argv[1:])
        if not cliOrderObj.get_holdings():
            print(f"Action: {cliOrderObj.get_action()}")
            print(f"Amount: {cliOrderObj.get_amount()}")
            print(f"Stock: {cliOrderObj.get_stocks()}")
            print(f"Time: {cliOrderObj.get_time()}")
            print(f"Price: {cliOrderObj.get_price()}")
            print(f"Broker: {cliOrderObj.get_brokers()}")
            print(f"Not Broker: {cliOrderObj.get_notbrokers()}")
            print(f"DRY: {cliOrderObj.get_dry()}")
            print()
            print("If correct, press enter to continue...")
            try:
                if not DANGER_MODE:
                    input("Otherwise, press ctrl+c to exit")
                    print()
            except KeyboardInterrupt:
                print()
                print("Exiting, no orders placed")
                sys.exit(0)
        # Validate order object
        cliOrderObj.order_validate(preLogin=True)
        # Get holdings or complete transaction
        if cliOrderObj.get_holdings():
            fun_run(cliOrderObj, ("_init", "_holdings"))
        else:
            fun_run(cliOrderObj, ("_init", "_transaction"))
        sys.exit(0)

    # If discord bot, run discord bot
    if DISCORD_BOT:
        # Get discord token and channel from .env file
        if not os.environ["DISCORD_TOKEN"]:
            raise Exception("DISCORD_TOKEN not found in .env file, please add it")
        if not os.environ["DISCORD_CHANNEL"]:
            raise Exception("DISCORD_CHANNEL not found in .env file, please add it")
        DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
        DISCORD_CHANNEL = int(os.getenv("DISCORD_CHANNEL"))
        # Initialize discord bot
        intents = discord.Intents.default()
        intents.message_content = True
        # Discord bot command prefix
        bot = commands.Bot(command_prefix="!", intents=intents)
        bot.remove_command("help")
        print()
        print("Discord bot is started...")
        print()

        # Bot event when bot is ready
        @bot.event
        async def on_ready():
            channel = bot.get_channel(DISCORD_CHANNEL)
            if channel is None:
                print(
                    "ERROR: Invalid channel ID, please check your DISCORD_CHANNEL in your .env file and try again"
                )
                os._exit(1)  # Special exit code to restart docker container
            await channel.send("Discord bot is started...")

        # Process the message only if it's from the specified channel
        # Bypass process_commands to allow bot messages
        @bot.event
        async def on_message(message):
            if message.channel.id == DISCORD_CHANNEL and message.author != bot.user:
                ctx = await bot.get_context(message)
                # the type of the invocation context's bot attribute will be correct
                await bot.invoke(ctx)  # type: ignore

        # Bot ping-pong
        @bot.command(name="ping")
        async def ping(ctx):
            print("ponged")
            await ctx.send("pong")

        # Help command
        @bot.command()
        async def help(ctx):
            # String of available commands
            await ctx.send(
                "Available RSA commands:\n"
                "!ping\n"
                "!help\n"
                "!rsa history\n"
                "!rsa holdings [all|<broker1>,<broker2>,...] [not broker1,broker2,...]\n"
                "!rsa [buy|sell] [amount] [stock1|stock1,stock2] [all|<broker1>,<broker2>,...] [not broker1,broker2,...] [DRY: true|false]\n"
                "!restart"
            )

        # Main RSA command
        @bot.command(name="rsa")
        async def rsa(ctx, *args):
            if args and args[0].lower() == "history":
                from historyAPI import get_history_data
                data = await bot.loop.run_in_executor(None, get_history_data)
                
                if data is None:
                    await ctx.send("No history data found.")
                    return
                if not data:
                    await ctx.send("History file is empty.")
                    return

                # Parse arguments
                mode = "summary" # default
                target_brokers = []
                
                if len(args) > 1:
                    if args[1].lower() == "all":
                        mode = "detailed"
                    else:
                        mode = "detailed"
                        target_brokers = [b.strip().lower() for b in args[1].split(",")]
                        # Handle nicknames
                        target_brokers = [nicknames(b) for b in target_brokers]

                embeds_to_send = []
                current_embed = discord.Embed(
                    title="Account Performance Tracking",
                    color=3447003
                )
                field_count = 0

                # Process Data
                items_to_display = []
                
                if mode == "summary":
                    # Aggregate by broker
                    broker_totals = {}
                    for acc in data["accounts"]:
                        broker = acc["broker"]
                        if broker not in broker_totals:
                            broker_totals[broker] = {
                                "current_val": 0,
                                "diff_1d": 0,
                                "diff_7d": 0,
                                "diff_14d": 0,
                                "diff_30d": 0,
                                "has_1d": False,
                                "has_7d": False,
                                "has_14d": False,
                                "has_30d": False
                            }
                        
                        broker_totals[broker]["current_val"] += acc["current_val"]
                        if acc["has_1d"]:
                            broker_totals[broker]["diff_1d"] += acc["diff_1d"]
                            broker_totals[broker]["has_1d"] = True
                        if acc["has_7d"]:
                            broker_totals[broker]["diff_7d"] += acc["diff_7d"]
                            broker_totals[broker]["has_7d"] = True
                        if acc["has_14d"]:
                            broker_totals[broker]["diff_14d"] += acc["diff_14d"]
                            broker_totals[broker]["has_14d"] = True
                        if acc["has_30d"]:
                            broker_totals[broker]["diff_30d"] += acc["diff_30d"]
                            broker_totals[broker]["has_30d"] = True
                    
                    for broker, totals in broker_totals.items():
                        items_to_display.append({
                            "name": broker,
                            "current_val": totals["current_val"],
                            "diff_1d": totals["diff_1d"],
                            "diff_7d": totals["diff_7d"],
                            "diff_14d": totals["diff_14d"],
                            "diff_30d": totals["diff_30d"],
                            "has_1d": totals["has_1d"],
                            "has_7d": totals["has_7d"],
                            "has_14d": totals["has_14d"],
                            "has_30d": totals["has_30d"]
                        })
                        
                else: # Detailed mode (all or filtered)
                    for acc in data["accounts"]:
                        if target_brokers and acc["broker"].lower() not in target_brokers:
                            # Try checking if the target broker is a substring of the account broker (e.g. "Schwab 1" vs "schwab")
                            # Or if the nickname mapping was correct but the data has full names.
                            # The data["accounts"] has "broker" field which comes from Brokerage.get_name().
                            # In schwabAPI.py, it sets name as "Schwab {index}".
                            # So "schwab" won't match "Schwab 1".
                            
                            # Let's try a more flexible match:
                            # 1. Exact match (already tried)
                            # 2. Starts with (e.g. "Schwab" matches "Schwab 1")
                            
                            match = False
                            acc_broker_lower = acc["broker"].lower()
                            for tb in target_brokers:
                                if tb in acc_broker_lower: # "schwab" in "schwab 1" -> True
                                    match = True
                                    break
                            
                            if not match:
                                continue
                            
                        items_to_display.append({
                            "name": f"{acc['broker']} - {maskString(acc['account'])}",
                            "current_val": acc["current_val"],
                            "diff_1d": acc["diff_1d"],
                            "diff_7d": acc["diff_7d"],
                            "diff_14d": acc["diff_14d"],
                            "diff_30d": acc["diff_30d"],
                            "has_1d": acc["has_1d"],
                            "has_7d": acc["has_7d"],
                            "has_14d": acc["has_14d"],
                            "has_30d": acc["has_30d"]
                        })

                if not items_to_display:
                     await ctx.send("No accounts found matching criteria.")
                     return

                # Build Embeds
                for item in items_to_display:
                    if field_count >= 24:
                        embeds_to_send.append(current_embed)
                        current_embed = discord.Embed(
                            title="Account Performance Tracking (Cont.)",
                            color=3447003
                        )
                        field_count = 0
                    
                    diff_1d_str = "N/A"
                    if item["has_1d"]:
                        sign = "+" if item["diff_1d"] >= 0 else "-"
                        diff_1d_str = f"{sign}${abs(item['diff_1d']):.2f}"

                    diff_7d_str = "N/A"
                    if item["has_7d"]:
                        sign = "+" if item["diff_7d"] >= 0 else "-"
                        diff_7d_str = f"{sign}${abs(item['diff_7d']):.2f}"

                    diff_14d_str = "N/A"
                    if item["has_14d"]:
                        sign = "+" if item["diff_14d"] >= 0 else "-"
                        diff_14d_str = f"{sign}${abs(item['diff_14d']):.2f}"
                        
                    diff_30d_str = "N/A"
                    if item["has_30d"]:
                        sign = "+" if item["diff_30d"] >= 0 else "-"
                        diff_30d_str = f"{sign}${abs(item['diff_30d']):.2f}"
                    
                    field_value = f"Current: ${item['current_val']:.2f}\n1 Day: {diff_1d_str}\n7 Days: {diff_7d_str}\n14 Days: {diff_14d_str}\n30 Days: {diff_30d_str}"
                    current_embed.add_field(
                        name=item["name"],
                        value=field_value,
                        inline=False
                    )
                    field_count += 1
                
                # Add Total Portfolio
                if field_count >= 25:
                    embeds_to_send.append(current_embed)
                    current_embed = discord.Embed(
                        title="Account Performance Tracking (Summary)",
                        color=3447003
                    )
                
                sign_day = "+" if data["total_day_diff"] >= 0 else "-"
                sign_week = "+" if data["total_week_diff"] >= 0 else "-"
                sign_two_week = "+" if data["total_two_week_diff"] >= 0 else "-"
                sign_month = "+" if data["total_month_diff"] >= 0 else "-"
                
                current_embed.add_field(
                    name="Total Portfolio",
                    value=f"Value: ${data['total_current']:.2f}\n1 Day Change: {sign_day}${abs(data['total_day_diff']):.2f}\n7 Day Change: {sign_week}${abs(data['total_week_diff']):.2f}\n14 Day Change: {sign_two_week}${abs(data['total_two_week_diff']):.2f}\n30 Day Change: {sign_month}${abs(data['total_month_diff']):.2f}",
                    inline=False
                )
                embeds_to_send.append(current_embed)
                
                for embed in embeds_to_send:
                    await ctx.send(embed=embed)
                return

            discOrdObj = await bot.loop.run_in_executor(None, argParser, args)
            event_loop = asyncio.get_event_loop()
            try:
                # Validate order object
                discOrdObj.order_validate(preLogin=True)
                # Get holdings or complete transaction
                if discOrdObj.get_holdings():
                    # Run Holdings
                    await bot.loop.run_in_executor(
                        None,
                        fun_run,
                        discOrdObj,
                        ("_init", "_holdings"),
                        bot,
                        event_loop,
                    )
                else:
                    # Run Transaction
                    await bot.loop.run_in_executor(
                        None,
                        fun_run,
                        discOrdObj,
                        ("_init", "_transaction"),
                        bot,
                        event_loop,
                    )
            except Exception as err:
                print(traceback.format_exc())
                print(f"Error placing order: {err}")
                if ctx:
                    await ctx.send(f"Error placing order: {err}")

        # Restart command
        @bot.command(name="restart")
        async def restart(ctx):
            print("Restarting...")
            print()
            await ctx.send("Restarting...")
            await bot.close()
            if DOCKER_MODE:
                os._exit(0)  # Special exit code to restart docker container
            else:
                os.execv(sys.executable, [sys.executable] + sys.argv)

        # Catch bad commands
        @bot.event
        async def on_command_error(ctx, error):
            print(f"Command Error: {error}")
            await ctx.send(f"Command Error: {error}")
            # Print help command
            print("Type '!help' for a list of commands")
            await ctx.send("Type '!help' for a list of commands")

        # Run Discord bot
        bot.run(DISCORD_TOKEN)
        print("Discord bot is running...")
        print()
