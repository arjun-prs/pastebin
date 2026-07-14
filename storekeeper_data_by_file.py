import os
import json
import requests
import logging
import csv
import argparse
from prettytable import PrettyTable
from urllib.parse import urljoin

# Ensure the log directory exists
log_dir = 'log'
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(message)s',
    datefmt='%a, %d %b %Y %H:%M:%S',
    filename=os.path.join(log_dir, 'serial_check.log'),
    filemode='w'
)

# Base URL for the Storekeeper API
BASE_URL = "https://storekeeper.oci.oraclecorp.com/v1/assets/show?oracle_serial="

# CSV output file name
CSV_OUTPUT_FILE = "storekeeper_output.csv"


def log(message):
    logger.info(message)
    print(message)


def get_asset_by_serial(serial_number):
    """
    Retrieves asset information from the Storekeeper API based on the serial number.
    """
    full_url = urljoin(BASE_URL, serial_number)
    try:
        response = requests.get(full_url)
        response.raise_for_status()  # Raises HTTPError for bad responses
        asset_data = response.json()
        return asset_data
    except requests.exceptions.HTTPError as http_err:
        log(f"HTTP error occurred: {http_err}")
    except requests.exceptions.RequestException as req_err:
        log(f"Request error occurred: {req_err}")
    except json.JSONDecodeError as json_err:
        log(f"JSON decode error: {json_err}")
    return None


def display_assets_info(assets_data):
    """
    Displays multiple assets information in a table format with properties as columns.
    Also saves the output to a CSV file.
    """
    if not assets_data:
        log("No asset data to display.")
        return

    # Define the table with dynamic field names (column headers)
    field_names = [
        "Storekeeper ID", "Serial Number", "Type", "Platform", "Owner", "State",
        "Region", "Availability Domain", "Building", "Room", "Rack Number", "Elevation"
    ]

    # Display in PrettyTable format
    asset_table = PrettyTable(field_names)

    # Prepare data for CSV
    csv_data = []

    # Add rows for each asset's data
    for asset_data in assets_data:
        parent_data = asset_data.get("parent", {})
        properties = [
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
            parent_data.get("rack_number"),  # Extracted from parent
            asset_data.get("elevation")
        ]

        asset_table.add_row([value if value is not None else "N/A" for value in properties])
        csv_data.append([value if value is not None else "N/A" for value in properties])

    # Display the table in the console
    print(asset_table)

    # Write data to CSV
    save_to_csv(field_names, csv_data)


def save_to_csv(field_names, data):
    """
    Saves the asset data to a CSV file.
    """
    with open(CSV_OUTPUT_FILE, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(field_names)  # Write header
        writer.writerows(data)  # Write rows
    log(f"Output saved to {CSV_OUTPUT_FILE}")


def read_serial_numbers_from_file(file_path):
    """
    Reads serial numbers from a file, one per line.
    """
    if not os.path.exists(file_path):
        log(f"Error: File {file_path} does not exist.")
        raise FileNotFoundError(f"File {file_path} not found.")

    with open(file_path, 'r') as file:
        return [line.strip() for line in file if line.strip()]


def main():
    # Argument parser setup with custom metavar to show Serial_No.txt in the usage string
    parser = argparse.ArgumentParser(description='Process a file of serial numbers and fetch asset info from Storekeeper.')
    parser.add_argument(
        '--filename',
        type=str,
        required=True,
        metavar='Serial_No.txt',  # Example for the usage message
        help='The file containing the serial numbers (e.g., Serial_No.txt).'
    )

    # Parse the command-line arguments
    args = parser.parse_args()

    # Check if the filename argument is provided
    if not args.filename:
        log("Error: Missing filename argument.")
        print("Please type -h/--help for more information.")
        return

    # Read serial numbers from the provided file
    try:
        serial_numbers = read_serial_numbers_from_file(args.filename)
    except FileNotFoundError as e:
        log(f"Error: {e}")
        return

    if not serial_numbers:
        log("No serial numbers provided or the file is empty.")
        return

    # Retrieve asset information for each serial number
    assets_data = []
    for serial_number in serial_numbers:
        log(f"Retrieving data for serial number: {serial_number}")
        asset_data = get_asset_by_serial(serial_number)

        if asset_data and asset_data.get("serial") == serial_number:
            log(f"Serial number {serial_number} found in Storekeeper data.")
            assets_data.append(asset_data)
        else:
            log(f"Serial number {serial_number} not found in Storekeeper data.")

    # Display all asset data in a single table and save it to a CSV file
    display_assets_info(assets_data)


if __name__ == "__main__":
    try:
        main()
    except argparse.ArgumentError:
        print("Invalid arguments. Please type -h/--help for more information.")
