'''
This script takes data in input text file: "auto-fill-input.txt"
sample:
nrt3-q1-b6-t1-r14 Ethernet59/1
nrt3-q1-b6-t1-r15 Ethernet60/1
nrt3-q1-b6-t1-r16 Ethernet61/1

Usage:
Create "auto-fill-input.txt" file in same folder as script and update "csv_file_path" variable in below code and run the script with Python

python3 cutsheet_data_fill_auto_input.py
'''

import csv
import re


def extract_last_digits(device_name):
    # Extract the last digit(s) from the device name
    last_digits = re.findall(r'\d+', device_name)
    return int(last_digits[-1]) if last_digits else -1


# Load the input text from a file
with open('auto-fill-input.txt', 'r') as input_file:
    input_lines = input_file.readlines()

# Filter device name and interface from the input lines
devices = []
interfaces = []
for line in input_lines:
    # Check if the line contains device and interface information
    if "device" in line and "interface" in line:
        # Case 1: Extract device and interface from line
        device_match = re.search(r'device="([^"]+)"', line)
        interface_match = re.search(r'interface="([^"]+)"', line)
        if device_match and interface_match:
            devices.append(device_match.group(1))
            interfaces.append(interface_match.group(1))
    else:
        # Case 2: Try to split line using comma, space, or tab as separators
        parts = re.split(r'[, \t]+', line.strip())
        if len(parts) == 2:
            devices.append(parts[0])
            interfaces.append(parts[1])

# Create a list to store matching lines
matching_lines = []

# Load data from the CSV file
csv_file_path = '/Users/izulfiqa/autonet/autonet-plans/nrt/nrt3-cables.csv'
with open(csv_file_path, 'r') as csv_file:
    csv_reader = csv.reader(csv_file)
    # Extract the header from the CSV file
    header = next(csv_reader)

    for row in csv_reader:
        # Check if the device and interface combination is in the CSV row and are adjacent
        for device, interface in zip(devices, interfaces):
            try:
                # Find the index of the device in the row
                device_index = row.index(device)
                # Check if the interface is right next to the device
                if row[device_index + 1] == interface or row[device_index - 1] == interface:
                    matching_lines.append(row)
                    break  # Found a match, no need to check other pairs
            except ValueError:
                # Device not found in this row, skip to next device-interface pair
                continue
            except IndexError:
                # Interface index out of bounds, this means device was found at the end of the row
                continue

# Sort the matching lines based on the last digit(s) of the device name
sorted_matching_lines = sorted(matching_lines,
                               key=lambda x: extract_last_digits(x[5]))  # Assuming device name is at index 5

# Check if matching lines were found and sorted
if sorted_matching_lines:
    # Write header row and data to a new CSV file
    with open('matching_lines.csv', 'w', newline='') as output_csv_file:
        csv_writer = csv.writer(output_csv_file)
        csv_writer.writerow(header)  # Write the original header from the cutsheet
        csv_writer.writerows(sorted_matching_lines)  # Write the sorted matching lines

    print("Matching lines saved to matching_lines.csv")
else:
    print("No matching lines found.")