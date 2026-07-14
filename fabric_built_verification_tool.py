import os
import subprocess
import pexpect
import time
import re
import json
import requests
import csv
import logging
import argparse
from prettytable import PrettyTable
from urllib.parse import urljoin
import pandas as pd  # For reading the cutsheet
from termcolor import colored  # For colored output
from concurrent.futures import ThreadPoolExecutor, as_completed  # For parallel processing
from collections import defaultdict  # For indexing cutsheet data

# Maximum number of parallel SSH connections and API requests
MAX_WORKERS = 60


def display_note_and_get_acknowledgment():
    note = """
    This program interacts with devices via SSH to collect serial numbers and then fetches asset information from the Storekeeper API and matches hostnames with cutsheets to make sure devices are built with the right hostnames on the right rack elevation.

    Prerequisites:  
    Note:
    1 - Make sure the laptop is connected to OCI VPN and you have already executed rekey() on your laptop's CLI.
    2 - Ensure that 'jitpw' is working correctly. You can check manually by running command: jitpw -re phx
    3 - 1st time run package installations:
            pip install termcolor
            pip install requests prettytable
            pip install pandas
    Usage:
    python fabric_built_verification_tool.py --region phx --filename devices.txt --cutsheet phx14-cables.csv
    Result: Wrongly built devices will be shown in red in the output table and saved to 'wrong_built_devices.csv'.
    """
    print(note)
    user_input = input("Do you acknowledge the note and wish to proceed? (y/n): ").strip().lower()

    if user_input == 'y':
        print("Acknowledgment received. Proceeding with the script...")
    elif user_input == 'n':
        print("Exiting the script. Please acknowledge the note before proceeding.")
        exit(1)
    else:
        print("Invalid input. Please enter 'y' for yes or 'n' for no.")
        exit(1)


# Call this function at the start of the script
display_note_and_get_acknowledgment()


def get_jitpw_path():
    # This function finds the jitpw path
    jitpw_path = subprocess.run(['which', 'jitpw'], capture_output=True, text=True).stdout.strip()

    # If jitpw is found in the system path
    if jitpw_path:
        print(f"jitpw path found in system PATH: {jitpw_path}")
        return jitpw_path

    # Fallback paths to check
    fallback_paths = [
        os.path.expanduser('~/tools/jitpw/bin/jitpw'),
        os.path.expanduser('~/bin/jitpw')
    ]

    for path in fallback_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            print(f"jitpw found in fallback path: {path}")
            return path

    # Raise an error if jitpw is not found
    raise FileNotFoundError("jitpw not found in the system path or fallback paths.")


# Set the path to jitpw dynamically
try:
    PATH_TO_JITPW = get_jitpw_path()
except FileNotFoundError as e:
    print(e)

def run_jitpw_per_region(region):
    try:
        # Run the command
        result = subprocess.run([PATH_TO_JITPW, '-r', region], capture_output=True, text=True)

        # Check if the command ran successfully
        if result.returncode == 0:
            print(f"jitpw -r {region} command executed successfully.")
        else:
            print(f"jitpw -r {region} command failed with return code {result.returncode}: {result.stderr}")
    except FileNotFoundError as e:
        print(e)
    except Exception as e:
        print(f"An error occurred: {e}")


# Output filenames
host_serial_filename = "host_serial.txt"
csv_output_filename = "storekeeper_output.csv"
wrong_built_filename = "wrong_built_devices.csv"
commands_strings = ["show ver | grep 'Serial number:' | sed 's/Serial number: *//g'"]

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(message)s',
    datefmt='%a, %d %b %Y %H:%M:%S',
    filename='serial_check.log',
    filemode='w'
)

BASE_URL = "https://storekeeper.oci.oraclecorp.com/v1/assets/show?oracle_serial="


def log(message):
    logger.info(message)
    print(message)


# Step 1: Collect hostname:serial_number pairs using SSH
def read_hostnames(filename):
    with open(filename, "r") as file:
        return [line.strip().split()[0] for line in file.readlines() if line.strip()]


def authenticate_host(hostname):
    password_result = subprocess.run([PATH_TO_JITPW, '-e', hostname], capture_output=True, text=True)
    password = password_result.stdout.split('\n')[0]
    child = pexpect.spawn(f"ssh {hostname}", env={'TERM': 'dumb'})
    child.expect("assword:")
    time.sleep(0.5)
    child.sendline(password)
    child.expect('#')
    time.sleep(0.5)
    return child


def execute_command(child, command):
    child.sendline(command)
    time.sleep(0.5)
    child.expect('#')
    result = child.before.decode('utf-8', 'ignore')
    lines = result.splitlines()
    for line in lines:
        if "Serial number" not in line and line.strip() and not line.startswith(command):
            return line.strip()
    return None


# Function to handle collecting serial numbers in parallel
def collect_serial_number_for_host(hostname):
    try:
        child = authenticate_host(hostname)
        for command_string in commands_strings:
            serial_number = execute_command(child, command_string)
            if serial_number:
                return f"{hostname}:{serial_number}"
            else:
                return f"Failed to retrieve serial number for {hostname}"
    except Exception as e:
        return f"Error authenticating {hostname}: {str(e)}"
    finally:
        if 'child' in locals():
            child.close()


# Parallel processing using ThreadPoolExecutor for SSH connections
def collect_serial_numbers_parallel(hostnames):
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(collect_serial_number_for_host, hostname): hostname for hostname in hostnames}
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)
                print(result)

    return results


# Save the collected serial numbers to a file
def save_serial_numbers_to_file(results):
    with open(host_serial_filename, "w") as file:
        for result in results:
            file.write(result + "\n")


# Step 2: Fetch data from Storekeeper API in parallel
def get_asset_by_serial(serial_number):
    full_url = urljoin(BASE_URL, serial_number)
    log(f"Fetching asset data for serial number: {serial_number}")
    try:
        response = requests.get(full_url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        log(f"Error fetching asset data for {serial_number}: {e}")
    return None


# Fetch asset data from Storekeeper in parallel
def fetch_assets_parallel(serial_numbers):
    assets_data = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(get_asset_by_serial, serial_number): serial_number for serial_number in
                   serial_numbers}
        for future in as_completed(futures):
            asset_data = future.result()
            if asset_data:
                assets_data.append(asset_data)
    return assets_data


# Pre-index cutsheet data for faster lookup
def preindex_cutsheet(cutsheet_data):
    cutsheet_index = defaultdict(dict)

    for _, row in cutsheet_data.iterrows():
        device_a_rack = str(row["DeviceA Rack"]).strip()
        device_a_ru = str(row["DeviceA RU"]).strip()
        device_b_rack = str(row["DeviceB Rack"]).strip()
        device_b_ru = str(row["DeviceB RU"]).strip()

        if device_a_rack and device_a_ru:
            cutsheet_index[(device_a_rack, device_a_ru)] = row["DeviceA Name"]
        if device_b_rack and device_b_ru:
            cutsheet_index[(device_b_rack, device_b_ru)] = row["DeviceB Name"]

    return cutsheet_index


# Updated match_rack_elevation function to use pre-indexed cutsheet for fast lookup
def match_rack_elevation(asset_data, cutsheet_index):
    rack_number = str(asset_data.get("parent", {}).get("rack_number", "")).strip()
    elevation = str(asset_data.get("elevation", "")).strip()

    if not rack_number or not elevation:
        return "N/A"

    # Look up in pre-indexed cutsheet
    return cutsheet_index.get((rack_number, elevation), "N/A")


# Extract numeric part from hostname (for sorting based on the rack number)
def extract_numeric_part(hostname):
    match = re.search(r'-r(\d+)', hostname)
    return int(match.group(1)) if match else 0  # Return 0 if no match found


# Function to display and save the asset information, sorted by numeric part of Hostname from cutsheet
def display_assets_info(assets_data, cutsheet_data, serial_number_mapping):
    field_names = ["Hostname from cutsheet", "Hostname collected from device", "Storekeeper ID", "Serial Number",
                   "Type", "Platform", "Owner", "State", "Region", "Availability Domain", "Building", "Room",
                   "Rack Number", "Elevation"]

    csv_data = []
    wrong_builds = []  # Store rows that are wrongly built
    colored_table = PrettyTable(field_names)  # Use PrettyTable to display the final table with colors

    # Pre-index the cutsheet for fast matching
    cutsheet_index = preindex_cutsheet(cutsheet_data)

    # Collect the rows for sorting
    rows = []

    for asset_data in assets_data:
        parent_data = asset_data.get("parent", {})
        hostname_collected = serial_number_mapping.get(asset_data.get("serial"), "None")

        properties = [
            match_rack_elevation(asset_data, cutsheet_index),  # Match hostname from cutsheet
            hostname_collected,  # Use the collected hostname from the mapping
            asset_data.get("storekeeper_id"),
            asset_data.get("serial"),
            asset_data.get("type"),
            parent_data.get("platform"),  # Extracted from parent
            asset_data.get("owner"),
            asset_data.get("state"),
            asset_data.get("availability_domain_room", {}).get("region_name"),
            asset_data.get("availability_domain_room", {}).get("availability_domain_canonical_short_code"),
            asset_data.get("availability_domain_room", {}).get("building_name"),
            asset_data.get("availability_domain_room", {}).get("room_name"),
            parent_data.get("rack_number"),
            asset_data.get("elevation")
        ]

        rows.append(properties)

    # Sort rows by the numeric part of "Hostname from cutsheet" (first column)
    rows.sort(key=lambda x: extract_numeric_part(x[0]))

    # Build the final table with sorted rows
    for properties in rows:
        csv_data.append([value if value is not None else "N/A" for value in properties])

        # Color rows depending on whether the hostnames match or not
        if properties[0] == properties[1]:  # If Hostname from cutsheet matches Hostname collected from device
            colored_table.add_row([colored(val, "green") for val in properties])
        else:
            colored_table.add_row([colored(val, "red") for val in properties])
            wrong_builds.append(properties)  # Add to wrong build list

    # Print the final table with correct and incorrect builds colored
    print(colored_table)

    # Save all devices to CSV (sorted)
    save_to_csv(field_names, csv_data)

    # Save wrongly built devices to a separate file
    if wrong_builds:
        save_wrong_builds_to_csv(field_names, wrong_builds)
        log(colored(f"Wrong built devices Output saved to {wrong_built_filename}", "red"))
    else:
        print("\033[93mValidations passed!!!\033[0m")


# Save all data into a CSV file (sorted)
def save_to_csv(field_names, data):
    with open(csv_output_filename, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(field_names)
        writer.writerows(data)
    log(colored(f"Output saved to {csv_output_filename}", "green"))


# Save wrongly built devices into a separate CSV file
def save_wrong_builds_to_csv(field_names, wrong_builds):
    with open(wrong_built_filename, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(field_names)
        writer.writerows(wrong_builds)


# Step 3: Process serial numbers from the file and fetch asset data in parallel
def process_serial_numbers_from_file(cutsheet_filename):
    cutsheet_data = pd.read_csv(cutsheet_filename, dtype=str, low_memory=False)

    serial_numbers = []
    serial_number_mapping = {}

    # Read hostnames and serial numbers from host_serial.txt
    with open(host_serial_filename, "r") as file:
        lines = file.readlines()
        for line in lines:
            hostname, serial_number = line.strip().split(':', 1)  # Split by the colon between hostname and serial
            serial_numbers.append(serial_number)
            serial_number_mapping[serial_number] = hostname  # Map serial numbers to hostnames

    # Fetch asset data from Storekeeper in parallel
    assets_data = fetch_assets_parallel(serial_numbers)

    if assets_data:
        display_assets_info(assets_data, cutsheet_data, serial_number_mapping)


# Main function to run both steps with argparse for the hostname file and cutsheet
def main():
    parser = argparse.ArgumentParser(
        description='Collect serial numbers from devices and fetch asset info from Storekeeper.')
    parser.add_argument(
        '--region',
        type=str,
        required=True,
        metavar='iad,phx,nrt',  # Example for the usage message
        help='First 3 airport code alphabets from device name (e.g., iad,phx,nrt).'
    )
    parser.add_argument(
        '--filename',
        type=str,
        required=True,
        metavar='devices.txt',  # Example for the usage message
        help='The file containing the hostnames (e.g., devices.txt).'
    )
    parser.add_argument(
        '--cutsheet',
        type=str,
        required=True,
        metavar='phx14-cables.csv',  # Example for cutsheet usage
        help='The cutsheet CSV file containing rack and elevation information for matching (e.g., phx14-cables.csv).'
    )

    # Parse the command-line arguments
    args = parser.parse_args()

    # Call the function to run jitpw for the specified region
    try:
        run_jitpw_per_region(args.region)
    except FileNotFoundError as e:
        print(e)

    # Step 1: Collect serial numbers from devices via SSH
    hostnames = read_hostnames(args.filename)
    results = collect_serial_numbers_parallel(hostnames)

    # Save the results to the file
    save_serial_numbers_to_file(results)

    # Step 2: Process serial numbers and fetch data from Storekeeper API in parallel, and match cutsheet
    process_serial_numbers_from_file(args.cutsheet)


if __name__ == "__main__":
    main()
