import csv
import os
from datetime import datetime, timedelta
from helperAPI import Brokerage, printAndDiscord

HISTORY_FILE = "account_history.csv"

def save_history(broker_obj: Brokerage):
    """Saves the current account totals to the history file."""
    timestamp = datetime.now().isoformat()
    
    # Prepare data rows
    rows = []
    totals = broker_obj.get_account_totals()
    
    for parent_name, accounts in totals.items():
        for account_name, value in accounts.items():
            if account_name == "total":
                continue
            # We store: timestamp, broker (parent_name), account (account_name), value
            rows.append([timestamp, parent_name, account_name, value])
            
    # Write to CSV
    file_exists = os.path.isfile(HISTORY_FILE)
    with open(HISTORY_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "broker", "account", "value"])
        writer.writerows(rows)

def get_history_data():
    """Returns a dictionary with history data for reporting."""
    if not os.path.exists(HISTORY_FILE):
        return None

    data = []
    with open(HISTORY_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["value"] = float(row["value"])
            row["timestamp"] = datetime.fromisoformat(row["timestamp"])
            data.append(row)

    if not data:
        return {}

    # Group by broker and account
    accounts = {}
    for row in data:
        key = (row["broker"], row["account"])
        if key not in accounts:
            accounts[key] = []
        accounts[key].append(row)

    now = datetime.now()
    one_day_ago = now - timedelta(days=1)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    total_current = 0
    total_day_diff = 0
    total_week_diff = 0
    total_month_diff = 0
    
    account_details = []

    for key, history in accounts.items():
        broker, account = key
        history.sort(key=lambda x: x["timestamp"])
        
        current_entry = history[-1]
        current_val = current_entry["value"]
        
        prev_1d_entry = None
        prev_7d_entry = None
        prev_30d_entry = None
        
        for entry in reversed(history):
            if entry["timestamp"] <= one_day_ago and prev_1d_entry is None:
                prev_1d_entry = entry
            if entry["timestamp"] <= seven_days_ago and prev_7d_entry is None:
                prev_7d_entry = entry
            if entry["timestamp"] <= thirty_days_ago and prev_30d_entry is None:
                prev_30d_entry = entry
                break

        diff_1d = 0
        diff_7d = 0
        diff_30d = 0
        
        if prev_1d_entry:
            diff_1d = current_val - prev_1d_entry["value"]
            total_day_diff += diff_1d

        if prev_7d_entry:
            diff_7d = current_val - prev_7d_entry["value"]
            total_week_diff += diff_7d
        
        if prev_30d_entry:
            diff_30d = current_val - prev_30d_entry["value"]
            total_month_diff += diff_30d

        total_current += current_val
        
        account_details.append({
            "broker": broker,
            "account": account,
            "current_val": current_val,
            "diff_1d": diff_1d,
            "diff_7d": diff_7d,
            "diff_30d": diff_30d,
            "has_1d": prev_1d_entry is not None,
            "has_7d": prev_7d_entry is not None,
            "has_30d": prev_30d_entry is not None
        })

    return {
        "accounts": account_details,
        "total_current": total_current,
        "total_day_diff": total_day_diff,
        "total_week_diff": total_week_diff,
        "total_month_diff": total_month_diff
    }

def get_history_report():
    """Generates a report comparing current values to 1 day and 30 days ago."""
    data = get_history_data()
    
    if data is None:
        return "No history data found."
    if not data:
        return "History file is empty."
        
    report_lines = []
    report_lines.append("==============================")
    report_lines.append("Account Performance Tracking")
    report_lines.append("==============================")
    
    for acc in data["accounts"]:
        # Mask account number for display
        masked_account = acc["account"]
        if len(masked_account) > 4:
            masked_account = "x" * (len(masked_account) - 4) + masked_account[-4:]
            
        diff_1d_str = "N/A"
        if acc["has_1d"]:
            sign = "+" if acc["diff_1d"] >= 0 else "-"
            diff_1d_str = f"{sign}${abs(acc['diff_1d']):.2f}"
            
        diff_7d_str = "N/A"
        if acc["has_7d"]:
            sign = "+" if acc["diff_7d"] >= 0 else "-"
            diff_7d_str = f"{sign}${abs(acc['diff_7d']):.2f}"

        diff_30d_str = "N/A"
        if acc["has_30d"]:
            sign = "+" if acc["diff_30d"] >= 0 else "-"
            diff_30d_str = f"{sign}${abs(acc['diff_30d']):.2f}"

        report_lines.append(f"{acc['broker']} - {masked_account}")
        report_lines.append(f"  Current: ${acc['current_val']:.2f}")
        report_lines.append(f"  1 Day:   {diff_1d_str}")
        report_lines.append(f"  7 Days:  {diff_7d_str}")
        report_lines.append(f"  30 Days: {diff_30d_str}")
        report_lines.append("-" * 20)

    sign_day = "+" if data["total_day_diff"] >= 0 else "-"
    sign_week = "+" if data["total_week_diff"] >= 0 else "-"
    sign_month = "+" if data["total_month_diff"] >= 0 else "-"
    report_lines.append(f"Total Value: ${data['total_current']:.2f}")
    report_lines.append(f"Total 1 Day Change: {sign_day}${abs(data['total_day_diff']):.2f}")
    report_lines.append(f"Total 7 Day Change: {sign_week}${abs(data['total_week_diff']):.2f}")
    report_lines.append(f"Total 30 Day Change: {sign_month}${abs(data['total_month_diff']):.2f}")
    report_lines.append("==============================")
    
    return "\n".join(report_lines)
