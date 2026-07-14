import re
import csv

# Function to extract device name and interfaces from input text
def extract_info(input_text):
    devices = []
    current_device = None
    total_interfaces = 0
    for line in input_text.split('\n'):
        if ('-t0-r' in line or '-t1-r' in line) and 'Message' not in line:
            if current_device and interfaces:  # Check if interfaces list is not empty
                devices.append((current_device, interfaces))
                total_interfaces += len(interfaces)
            current_device = line.strip().split(':')[0]
            interfaces = []
        elif 'alignment' in line:
            fec_data = re.findall(r"'(Ethernet\d+/\d+)': \{'lock_status': True", line)
            interfaces.extend(fec_data)
    if current_device and interfaces:  # Check if interfaces list is not empty
        devices.append((current_device, interfaces))
        total_interfaces += len(interfaces)
    return devices, total_interfaces

# Read input from the specified file
input_file = "aga1-q2-b12-failed.txt"
with open(input_file, 'r') as file:
    input_text = file.read()

# Extract devices and interfaces
devices, total_interfaces = extract_info(input_text)

# Open the CSV file for reading
CSV_FILE_PATH = '/Users/izulfiqa/autonet/autonet-plans/nrt/nrt3-cables.csv'
output_file = 'ncpcli_fec_scan_validations_output.csv'

# Initialize a list to store matching rows
matching_rows = []

# Grab header from the CSV file
with open(CSV_FILE_PATH, 'r') as csv_file:
    csv_reader = csv.reader(csv_file)
    header = next(csv_reader)  # Get the header row

    # Process each device and its interfaces
    for device, interfaces in devices:
        if interfaces:  # Only proceed if interfaces list is not empty
            print(f"Device Name: {device}")
            print("Interfaces:", interfaces)
            # Reset the file pointer to the beginning of the CSV file for each device
            csv_file.seek(0)
            next(csv_reader)  # Skip the header row again for each device

            # Check each row in the CSV file
            for row in csv_reader:
                # Check if the device and interface combination is in the CSV row
                if any(device in row and intf in row for intf in interfaces):
                    # Append the matching row to matching_rows
                    matching_rows.append(row)

# Write header and matching rows to the output CSV file
with open(output_file, 'w', newline='') as output_csv_file:
    csv_writer = csv.writer(output_csv_file)
    # Write the header row to the output CSV file
    csv_writer.writerow(header)
    # Write matching rows to the output file
    csv_writer.writerows(matching_rows)

print(f"Matching data saved to {output_file}")

# Print total number of devices and interfaces
total_devices = len(devices)
print(f"Total Devices: {total_devices}")
print(f"Total Interfaces: {total_interfaces}")