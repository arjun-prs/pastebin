import re
import csv

def extract_flapping_interfaces(input_text):
    flapping_interfaces = {}
    total_devices = 0
    total_interfaces = 0
    for line in input_text:
        if "Flapping interfaces:" in line:
            matches = re.findall(r"'device': '(.*?)',.*?'interface': '(.*?)'", line)
            for match in matches:
                device = match[0]
                interface = match[1]
                if "-t0-r" in device or "-t1-r" in device:
                    if device not in flapping_interfaces:
                        flapping_interfaces[device] = []  # Use a list to store interfaces
                    flapping_interfaces[device].append(interface)
                    total_interfaces += 1
            total_devices += 1
    # Print device and interfaces under each device group
    for device, interfaces in flapping_interfaces.items():
        print(f"Device: {device}")
        for interface in interfaces:
            print(f"Interface: {interface}")
        print(f"Total Interfaces: {len(interfaces)}")  # Print total number of interfaces
        print()  # Add a blank line after printing all interfaces for a device
    # Print total number of devices and interfaces
    print(f"Total devices: {total_devices}, Total interfaces: {total_interfaces}")
    return flapping_interfaces

def write_matching_rows(input_csv, output_csv, pairs):
    processed_pairs = set()  # Keep track of processed pairs for unique pairs
    with open(input_csv, 'r') as csvfile:
        reader = csv.reader(csvfile)
        header = next(reader)  # Grab the header from input CSV
        with open(output_csv, 'w', newline='') as outfile:
            writer = csv.writer(outfile)
            writer.writerow(header)  # Write the header to output CSV
            for row in reader:
                for pair in pairs:
                    if pair[0] in row and pair[1] in row and pair not in processed_pairs:
                        writer.writerow(row)
                        processed_pairs.add(pair)  # Add processed pair to set

input_file = "aga1-q2-b12-failed.txt"
input_csv = "/Users/izulfiqa/autonet/autonet-plans/nrt/nrt3-cables.csv"
output_csv = "flap_output.csv"

with open(input_file, 'r') as file:
    input_text = file.readlines()

# Extract flapping interfaces and print on terminal
flapping_interfaces = extract_flapping_interfaces(input_text)

# Write matching rows to output CSV
write_matching_rows(input_csv, output_csv, [(device, interface) for device, interfaces in flapping_interfaces.items() for interface in interfaces])