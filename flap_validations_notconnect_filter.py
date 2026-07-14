import re
import csv
import pexpect

username = "izulfiqa"
password = "<redacted>"
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

def run_ssh_command(device, interface, username, password):
    print(f"\nSSHing into {device}...")
    child = pexpect.spawn(f"ssh {username}@{device}")
    i = child.expect(["assword:", pexpect.EOF, pexpect.TIMEOUT])

    if i == 0:
        child.sendline(password)
        j = child.expect(["#", pexpect.TIMEOUT])

        if j == 0:
            print(f"Running command 'sh int {interface} | include notconnect'")
            child.sendline(f"sh int {interface} | include notconnect")
            k = child.expect(["#", pexpect.TIMEOUT])

            if k == 0:
                output = child.before.decode('utf-8').strip().splitlines()  # Split output into lines
                print("Command output:")
                print("\n".join(line for line in output if line.strip()))

                return output  # Return output as a list of lines
            else:
                print(f"Timeout while running command on {device} for interface {interface}")
        else:
            print(f"Failed to login to {device}")
    else:
        print(f"Failed to connect to {device}")

    return []

def write_matching_rows(input_csv, output_csv, pairs, username, password):
    processed_pairs = set()  # Keep track of processed pairs for unique pairs
    with open(input_csv, 'r') as csvfile:
        reader = csv.reader(csvfile)
        header = next(reader)  # Grab the header from input CSV
        rows = list(reader)  # Store all rows in a list
        with open(output_csv, 'w', newline='') as outfile:
            writer = csv.writer(outfile)
            writer.writerow(header)  # Write the header to output CSV
            for device, interfaces in pairs:
                print("=" * 50)  # Print line of equals signs to separate device commands
                print(f"Processing device: {device}")
                for row in rows:
                    if device in row:
                        for interface in interfaces:
                            if interface in row and (device, interface) not in processed_pairs:
                                output_lines = run_ssh_command(device, interface, username, password)
                                if any("down" in line.split() for line in output_lines):  # If command output contains 'line protocol is down's, then ignore the row
                                    print(f"Ignoring row for device {device} and interface {interface} due to 'notconnect' in command output")
                                else:
                                    print("Writing row to Output CSV file.")
                                    writer.writerow(row)
                                    processed_pairs.add((device, interface))  # Add processed pair to set
                                    break  # Exit the loop after finding the first matching interface
    print("All devices processed.")

input_file = "aga1-q2-b12-failed.txt"
input_csv = "/Users/izulfiqa/autonet/autonet-plans/ord/ord6-cables.csv"
output_csv = "flap_output.csv"



with open(input_file, 'r') as file:
    input_text = file.readlines()

# Extract flapping interfaces and print on terminal
flapping_interfaces = extract_flapping_interfaces(input_text)

# Write matching rows to output CSV
write_matching_rows(input_csv, output_csv, flapping_interfaces.items(), username, password)
