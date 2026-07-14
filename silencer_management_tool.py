'''The tool aimed at simplifying and accelerating use of silencers, especially as we handle an increasing number of builds. 
The goal is to reduce the manual effort and time we spend on routine silencer operations such as creation, expiration, and lookup.

Key features:
* Generates and executes all 4 required silencer commands (device and remote side for tiers) in a single run.
* Supports expiring multiple silencers at once by accepting a list of silencer IDs, especially useful for clearing an entire block in one command.
* You can view silencer details by device name, silencer ID, block, or tier. The output includes a clear summary with key information such as silencer status, created by, device list, start time, and end time.

Usage:

Run the script in your terminal:
  python3 silencer_management_tool


'''


import subprocess
import re
import json


def run_command(cmd):
    process = subprocess.Popen(' '.join(cmd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, text=True)
    output, error = process.communicate()
    if process.returncode != 0:
        print(f"Error executing command:\n{error.strip()}")
        return None
    return output.strip()

def get_devices_from_ncpcli(region, device_pattern, device_states, additional_args=None):
    cmd = [
        "ncpcli", "-r", region[:3],
        "devices", "list", "--devices", f'"{device_pattern}"'
    ]

    if additional_args:
        cmd.extend(additional_args)

    for state in device_states:
        cmd.extend(["--device-state-matching", state])

    print(f"\nRunning command:\n{' '.join(cmd)}")
    print("(Please enter your PIN if prompted)")

    output = run_command(cmd)
    if output is None:
        return []

    # Parse the output to extract device names
    lines = output.split('\n')
    devices = []
    parsing_devices = False
    for line in lines:
        if 'Name' in line and '|' in line:
            parsing_devices = True
        elif parsing_devices and '|' in line and '---' not in line:
            columns = line.strip().split('|')
            if len(columns) > 1:
                device_name = columns[1].strip()
                devices.append(device_name)

    return devices


def get_vendor_from_ncpcli(region, device_pattern):
    cmd = [
        "ncpcli", "-r", region[:3],
        "devices", "list", "--devices", f'"{device_pattern}"'
    ]

    print(f"\nFetching vendor information using command:\n{' '.join(cmd)}")
    print("(Please enter your PIN if prompted)")

    output = run_command(cmd)
    if output is None:
        return None

    output_lower = output.lower()
    vendors = set()

    if "arista" in output_lower or re.search(r"\beos\b", output_lower):
        vendors.add("arista")
    if "nvidia" in output_lower or re.search(r"\bcumulus\b", output_lower):
        vendors.add("nvidia")

    if len(vendors) == 1:
        vendor = vendors.pop()
        print(f"Detected vendor: {vendor.upper() if vendor == 'nvidia' else vendor.title()}")
        return vendor

    if len(vendors) > 1:
        print("Matched devices include both Arista and NVIDIA. Please narrow the regex before applying silencers.")
    else:
        print("Could not determine vendor from ncpcli output. Please verify the regex and try again.")

    return None


def get_devices(region, design, block, device_states, tier=None):
    block_prefix = "b" if not design.lower().startswith("i") else "su"
    device_pattern = f"{region}-{design}-{block_prefix}{block}"
    if tier:
        device_pattern += f"-t{tier}-r*"
    return get_devices_from_ncpcli(region, device_pattern, device_states)


def get_t2_devices(region, design, device_states):
    device_pattern = f"{region}-{design}-t2*"
    return get_devices_from_ncpcli(region, device_pattern, device_states)


def get_devices_by_rack(region, design, racks, device_states):
    rack_str = ",".join(racks)
    device_pattern = f"{region}-{design}*"
    additional_args = ["--rack", f'"{rack_str}"']
    return get_devices_from_ncpcli(region, device_pattern, device_states, additional_args)


def run_silencer_cmd(short_region, duration_minutes, ticket, devices, mode, guid=None):
    device_str = "|".join(devices)
    cmd = [
        "silencer", "create",
        "-r", short_region,
        "-d", str(duration_minutes),
        "-c", ticket,
        "-M", mode,
        f'"{device_str}"'
    ]

    if guid:
        cmd.extend(["-A", guid])

    print(f"\nRunning command:\n{' '.join(cmd)}")
    print("(Please enter your PIN if prompted)")

    output = run_command(cmd)
    if output is None:
        return None

    print(f"Output:\n{output}")
    match = re.search(r"Created silence (\S+)", output)
    if match:
        return match.group(1)
    else:
        print("Could not find silencer ID in output.")
        return None


def run_silencer_with_interface_cmd(short_region, duration_minutes, ticket, regex, interface, mode, guid=None):
    cmd = [
        "silencer", "create",
        "-r", short_region,
        "-d", str(duration_minutes),
        "-c", ticket,
        "-M", mode,
        f'"{regex}"',
        "-m", f"interface {interface}"
    ]

    if guid:
        cmd.extend(["-A", guid])

    print(f"\nRunning command:\n{' '.join(cmd)}")
    print("(Please enter your PIN if prompted)")

    output = run_command(cmd)
    if output is None:
        return None

    print(f"Output:\n{output}")
    match = re.search(r"Created silence (\S+)", output)
    if match:
        return match.group(1)
    else:
        print("Could not find silencer ID in output.")
        return None

def expire_silencer(region, silencer_id):
    cmd = ['silencer', 'expire', '-r', region, silencer_id]
    print(f"Running: {' '.join(cmd)} \n")

    # Run the command interactively, attaching stdin/stdout/stderr to your terminal
    result = subprocess.run(cmd)

    if result.returncode == 0:
        print(f"Silencer {silencer_id} expired successfully.")
    else:
        print(f"Failed to expire silencer {silencer_id}. Return code: {result.returncode}")


def view_silencer(region, silencer_id):
    cmd = [
        "silencer", "view",
        "-r", region,
        silencer_id
    ]

    print(f"\nRunning command:\n{' '.join(cmd)}")
    print("(Please enter your PIN if prompted)")

    output = run_command(cmd)
    if output is None:
        return

    print(f"Output:\n{output}")
    try:
        data = json.loads(output)
        print(f"=============================================================")
        print(f"\nSilencer ID: {data['data']['id']}")
        print(f"Created By: {data['data']['createdBy']}")
        print(f"Comment: {data['data']['comment']}")
        print(f"Starts At: {data['data']['startsAt']}")
        print(f"Ends At: {data['data']['endsAt']}")
        print(f"Status: {data['data']['status']['state']}")
        for matcher in data['data']['matchers']:
            print(f"rmatcher: {matcher['name']} = {matcher['value']}")
    except json.JSONDecodeError:
        print("Invalid JSON output.")

def search_silencer(region, device_name):
    cmd = [
        "silencer", "search",
        "-r", region,
        "-s", "active",
        "-m", "device",
        device_name
    ]

    print(f"\nRunning command:\n{' '.join(cmd)}")
    print("(Please enter your PIN if prompted)")

    output = run_command(cmd)
    if output is None:
        return

    print(f"Output:\n{output}")
    try:
        data = json.loads(output)
        if len(data) == 0:
            print("No active silencers found")
        else:
            for silencer in data:
                print(f"=============================================================")
                print(f"\nSilencer ID: {silencer['id']}")
                print(f"Created By: {silencer['createdBy']}")
                print(f"Comment: {silencer['comment']}")
                print(f"Starts At: {silencer['startsAt']}")
                print(f"Ends At: {silencer['endsAt']}")
                print(f"Status: {silencer['status']['state']}")
                for matcher in silencer['matchers']:
                    print(f"rmatcher: {matcher['name']} = {matcher['value']}")
    except json.JSONDecodeError:
        print("Invalid JSON output.")

def search_silencer_by_design_block(short_region, region, design, block, tier):
    block_prefix = "b" if not design.lower().startswith("i") else "su"
    if tier == "both":
        device_name = f"{region}-{design}-{block_prefix}{block}-t.*"
        search_silencer(short_region, device_name)
    else:
        device_name = f"{region}-{design}-{block_prefix}{block}-t{tier}-r.*"
        search_silencer(short_region, device_name)

def main():
    while True:
        print("\n=== Silencer Management Tool===")
        print("1. Applying Silencers using Regex")
        print("2. Applying Silencers on interface level using Regex")
        print("3. Applying Silencers on T0/T1 using |(or operator)")
        print("4. Applying Silencers on T2 using |(or operator)")
        print("5. Applying Silencers using Rack Locations and |(or operator)")
        print("6. Expiring Silencers")
        print("7. Viewing Silencers with ID")
        print("8. Viewing Silencers with Device Name")
        print("9. Viewing Silencers with Design and Block/Tier")
        print("10. Viewing Silencers for Multi-Planar Devices")
        print("11. Exit")
        choice = input("Enter your choice: ")

        if choice == "1":
            region = input("Enter region (e.g., xyv22): ").strip()
            short_region = region[:3]  # Truncate region to 3 characters
            duration_minutes = int(input("Enter number of minutes for silencer to be active: ").strip())
            ticket = input("Enter ticket number: ").strip()
            regex = input("Enter regex format (e.g., iad84-q1-b8-t1-r.*, iad49-q1-b40-t0-r(1[7-9]|2[0-9]|3[0-2])): ").strip()
            guid = input("Enter GUID (optional, press enter to skip): ").strip()
            guid = guid if guid else None

            # Print Prometheus commands to test the regex
            print("\n=== Test your regex in Prometheus using the following commands ===")
            print(f"count(deviceInfo{{device=~\"{regex}\"}}) by (fabric_block)")
            print(f"count(deviceInfo{{device=~\"{regex}\"}}) by (device)")

            print("\n=== Executing Commands ===")
            silencer_id_device = run_silencer_cmd(short_region, duration_minutes, ticket, [regex], "device", guid)
            silencer_id_remote_device = run_silencer_cmd(short_region, duration_minutes, ticket, [regex], "remote_device", guid)
            print(f"Device: {silencer_id_device}")
            print(f"Remote Device: {silencer_id_remote_device}")

        elif choice == "2":
            print("\n**WARNING: Please ensure you have your list of interfaces ready (e.g., 9/1, 9/5) and verify them before entering.**")
            region = input("Enter region (e.g., xyv22): ").strip()
            short_region = region[:3]  # Truncate region to 3 characters
            duration_minutes = int(input("Enter number of minutes for silencer to be active: ").strip())
            ticket = input("Enter ticket number: ").strip()
            regex = input("Enter regex format (e.g., iad84-q1-b8-t1-r.*): ").strip()
            interfaces = input("Enter interfaces on which silencers need to be applied (comma-separated, e.g., 9/1, 9/5): ").strip().split(',')
            vendor = get_vendor_from_ncpcli(region, regex.replace(".*", "*"))
            if not vendor:
                continue
            if vendor == "arista":
                interfaces = [f"Ethernet{i.strip()}" for i in interfaces]
            else:
                interfaces = [f"{i.strip()}" for i in interfaces]
            guid = input("Enter GUID (optional, press enter to skip): ").strip()
            guid = guid if guid else None

            # Print Prometheus commands to test the regex
            print("\n=== Test your regex in Prometheus using the following commands ===")
            print(f"count(deviceInfo{{device=~\"{regex}\"}}) by (fabric_block)")
            print(f"count(deviceInfo{{device=~\"{regex}\"}}) by (device)")

            print("\n=== Executing Commands ===")
            results = {}
            for interface in interfaces:
                silencer_id_device = run_silencer_with_interface_cmd(short_region, duration_minutes, ticket, regex, interface, "device", guid)
                silencer_id_remote_device = run_silencer_with_interface_cmd(short_region, duration_minutes, ticket, regex, interface, "remote_device", guid)
                results[interface] = {"Device": silencer_id_device, "Remote Device": silencer_id_remote_device}

            print("\n=== Results ===")
            for interface, silencer_ids in results.items():
                print(f"Interface: {interface}")
                print(f"Device: {silencer_ids['Device']}")
                print(f"Remote Device: {silencer_ids['Remote Device']}")
                print("-" * 50)

        elif choice == "3":
            region = input("Enter region (e.g., xyv22): ").strip()
            design = input("Enter cluster design (e.g., q2): ").strip()
            block = input("Enter block number/scale unit (e.g., 14): ").strip()

            tier = input("Enter tier (T1 or T0 or both): ").strip().lower()
            while tier not in ["t1", "t0", "both"]:
                tier = input("Invalid input. Please enter T1, T0 or both: ").strip().lower()

            device_states = input("Enter the desired state(s) of devices (comma-separated, e.g., new, deployed, in-service): ").strip().split(',')
            device_states = [state.strip() for state in device_states]

            duration_minutes = int(input("Enter number of minutes for silencer to be active: ").strip())
            ticket = input("Enter ticket number: ").strip()
            guid = input("Enter GUID (optional, press enter to skip): ").strip()
            guid = guid if guid else None

            print("\n=== Executing Commands ===")

            silencer_ids = {}

            if tier in ["t1", "both"]:
                t1_devices = get_devices(region, design, block, device_states, "1")
                print("\nTier 1 devices:")
                for device in t1_devices:
                    print(device)
                print(f"Total T1 devices: {len(t1_devices)}")
                confirm = input("Confirm device list (yes/no): ")
                if confirm.lower() == "yes":
                    short_region = region[:3]  # Truncate region to 3 characters
                    silencer_ids["T1_device"] = run_silencer_cmd(short_region, duration_minutes, ticket, t1_devices, "device", guid)
                    silencer_ids["T1_remote_device"] = run_silencer_cmd(short_region, duration_minutes, ticket, t1_devices, "remote_device", guid)

            if tier in ["t0", "both"]:
                t0_devices = get_devices(region, design, block, device_states, "0")
                print("\nTier 0 devices:")
                for device in t0_devices:
                    print(device)
                print(f"Total T0 devices: {len(t0_devices)}")
                confirm = input("Confirm device list (yes/no): ")
                if confirm.lower() == "yes":
                    short_region = region[:3]  # Truncate region to 3 characters
                    silencer_ids["T0_device"] = run_silencer_cmd(short_region, duration_minutes, ticket, t0_devices, "device", guid)
                    silencer_ids["T0_remote_device"] = run_silencer_cmd(short_region, duration_minutes, ticket, t0_devices, "remote_device", guid)

            print("\n=== Silencer IDs ===")
            for key, value in silencer_ids.items():
                if value:
                    print(f"{key}: {value}")

        elif choice == "4":
            region = input("Enter region (e.g., xxx45): ").strip()
            design = input("Enter cluster design (e.g., qx): ").strip()

            device_states = input("Enter the desired state(s) of devices (comma-separated, e.g., new, deployed, in-service): ").strip().split(',')
            device_states = [state.strip() for state in device_states]

            duration_minutes = int(input("Enter number of minutes for silencer to be active: ").strip())
            ticket = input("Enter ticket number: ").strip()
            guid = input("Enter GUID (optional, press enter to skip): ").strip()
            guid = guid if guid else None

            t2_devices = get_t2_devices(region, design, device_states)
            print("\nT2 devices:")
            for device in t2_devices:
                print(device)
            print(f"Total T2 devices: {len(t2_devices)}")
            confirm = input("Confirm device list (yes/no): ")
            if confirm.lower() == "yes":
                short_region = region[:3]  # Truncate region to 3 characters
                silencer_id_device = run_silencer_cmd(short_region, duration_minutes, ticket, t2_devices, "device", guid)
                silencer_id_remote_device = run_silencer_cmd(short_region, duration_minutes, ticket, t2_devices, "remote_device", guid)
                print(f"T2_device: {silencer_id_device}")
                print(f"T2_remote_device: {silencer_id_remote_device}")

        elif choice == "5":
            region = input("Enter region (e.g., xyv22): ").strip()
            design = input("Enter cluster design (e.g., q or i): ").strip().lower()
            while design not in ["q", "i"]:
                design = input("Invalid input. Please enter q or i: ").strip().lower()

            num_racks = int(input("Enter the number of racks: ").strip())
            racks = []
            for i in range(num_racks):
                rack = input(f"Enter rack {i+1} (e.g., xyv22:5009): ").strip()
                racks.append(rack)

            device_states = input("Enter the desired state(s) of devices (comma-separated, e.g., new, deployed, in-service): ").strip().split(',')
            device_states = [state.strip() for state in device_states]

            duration_minutes = int(input("Enter number of minutes for silencer to be active: ").strip())
            ticket = input("Enter ticket number: ").strip()
            guid = input("Enter GUID (optional, press enter to skip): ").strip()
            guid = guid if guid else None

            print("\n=== Executing Commands ===")

            devices = get_devices_by_rack(region, design, racks, device_states)
            print("\nDevices:")
            for device in devices:
                print(device)
            print(f"Total devices: {len(devices)}")
            confirm = input("Confirm device list (yes/no): ")
            if confirm.lower() == "yes":
                short_region = region[:3]  # Truncate region to 3 characters
                silencer_id_device = run_silencer_cmd(short_region, duration_minutes, ticket, devices, "device", guid)
                silencer_id_remote_device = run_silencer_cmd(short_region, duration_minutes, ticket, devices, "remote_device", guid)
                print(f"Device: {silencer_id_device}")
                print(f"Remote Device: {silencer_id_remote_device}")


        elif choice == "6":
            region = input("Enter region (e.g., xyv): ").strip()
            silencer_ids = input("Enter silencer ID(s) separated by comma: ").strip().split(',')
            silencer_ids = [id.strip() for id in silencer_ids]
            for silencer_id in silencer_ids:
                expire_silencer(region, silencer_id)

        elif choice == "7":
            region = input("Enter region (e.g., xyv): ").strip()
            silencer_ids = input("Enter silencer ID(s) separated by comma: ").strip().split(',')
            silencer_ids = [id.strip() for id in silencer_ids]
            for silencer_id in silencer_ids:
                view_silencer(region, silencer_id)

        elif choice == "8":
            region = input("Enter region (e.g., xyv): ").strip()
            device_name = input("Enter device name: ").strip()
            search_silencer(region, device_name)

        elif choice == "9":
            region = input("Enter region (e.g., xyv22): ").strip()
            short_region = region[:3]  # Truncate region to 3 characters
            design = input("Enter cluster design (e.g., q2): ").strip()
            block = input("Enter block number/scale unit (e.g., 14): ").strip()
            tier = input("Enter tier (T1 or T0 or both): ").strip().lower()
            while tier not in ["t1", "t0", "both"]:
                tier = input("Invalid input. Please enter T1, T0 or both: ").strip().lower()
            tier = tier.replace("t0", "0").replace("t1", "1")
            search_silencer_by_design_block(short_region, region, design, block, tier)

        elif choice == "10":
            region = input("Enter region (e.g., xyv22): ").strip()
            short_region = region[:3]  # Truncate region to 3 characters
            design = input("Enter cluster design (e.g., q2): ").strip()
            device_roles = input("Enter device roles (e.g., ip, t1, t0): ").strip().lower()
            while device_roles not in ["ip", "t1", "t0"]:
                device_roles = input("Invalid input. Please enter ip, t1 or t0: ").strip().lower()

            if device_roles == "ip":
                device_name = f"{region[:3]}{region[3:5]}-{design}-ip-.*"
                search_silencer(short_region, device_name)
            else:
                plane = input("Enter plane number (e.g., 1, 2, 3, 4, all): ").strip().lower()
                while plane not in ["1", "2", "3", "4", "all"]:
                    plane = input("Invalid input. Please enter 1, 2, 3, 4 or all: ").strip().lower()

                if plane == "all":
                    device_name = f"{region[:3]}{region[3:5]}-{design}-p[1-4]-{device_roles}-.*"
                else:
                    device_name = f"{region[:3]}{region[3:5]}-{design}-p{plane}-{device_roles}-.*"
                search_silencer(short_region, device_name)

        elif choice == "11":
            break

        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    main()
