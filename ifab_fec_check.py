'''
Author: Akhil Kadali
Email: akhil.kadali@oracle.com
Purpose: This script takes a file with device names and cutsheet file as input and verifies the fec is healthy on all the ports on the devices
'''

import pexpect
import time
import re
import argparse
import subprocess
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

PATH_TO_JITPW = '~/jitpw/bin/jitpw'
COMMAND_TEMPLATE = "fae mlxlink -d /dev/mst/mt54002_pci_cr0 -p {port} -c"
BER_THRESHOLD = 5e-12

def strip_state_output(text):
    return re.sub(r'\x1b\[[0-9;]+m', '', text)

def get_password(hostname, password_arg, jitpw_path):
    if password_arg:
        return password_arg
    elif jitpw_path:
        expanded_path = os.path.expanduser(jitpw_path)
        result = subprocess.run([expanded_path, "-e", hostname], capture_output=True, text=True)
        return result.stdout.split('\n')[0]
    else:
        raise ValueError("Password or path to JIT password tool must be provided")

def authenticate_host(hostname, password):
    try:
        child = pexpect.spawn(f"ssh {hostname}", timeout=5)
        child.expect("Password:")
        time.sleep(0.2)
        child.sendline(password)
        child.expect('>')
        time.sleep(0.2)
        child.sendline('enable')
        child.expect('#')
        return child
    except Exception as e:
        print(f"[ERROR] Could not authenticate to {hostname}: {e}")
        return None

def parse_symbol_ber(output):
    match = re.search(r"^Symbol BER\s+:\s+([0-9.Ee+-]+|N/A|n/a)", output, re.MULTILINE)
    return match.group(1) if match else None

def parse_link_state(output):
    match = re.search(r"^State\s+:\s+(.+)", output, re.MULTILINE)
    return match.group(1).strip() if match else ""

def is_ignored_port(state, ber):
    return (state != "Active") or (ber is not None and ber.upper() == "N/A")

def get_ports_to_check(hostname, su_number):
    if re.search(r'-i\d+-t2-c\d+-r\d+', hostname):
        region = hostname[:3].lower()
        if region == 'hsg':
            if su_number:
                if not (1 <= su_number <= 6):
                    raise ValueError("For HSG devices, --su must be between 1 and 6")
                start_cage = (su_number - 1) * 4 + 1
                cages = list(range(start_cage, start_cage + 4))
                ports = [f"{cage}/{lane}" for cage in cages for lane in (1, 2)]
                return ports
            else:
                return [f"{i}/{j}" for i in range(1, 33) for j in (1, 2)]
        elif region == 'fyv':
            return [f"{i}/{j}" for i in range(1, 33) for j in (1, 2)]
        else:
            return [f"{i}/{j}" for i in range(1, 33) for j in (1, 2)]
    else:
        return [f"{i}/{j}" for i in range(1, 33) for j in (1, 2)]

def run_checks_on_device(hostname, password, su_number):
    result = {
        'hostname': hostname,
        'failed_ports': {},
        'errored_ports': {},
        'ignored_ports': {},
        'passed_ports': {}
    }

    child = authenticate_host(hostname, password)
    if not child:
        for port in get_ports_to_check(hostname, su_number):
            result['errored_ports'][f"1/{port}"] = "SSH_FAIL"
        return result

    ports_to_check = get_ports_to_check(hostname, su_number)
    fw_error_detected = False
    for port in ports_to_check:
        if fw_error_detected:
            result['errored_ports'][f"1/{port}"] = "FW_UNSUPPORTED"
            continue
        port_id = f"1/{port}"
        cmd = COMMAND_TEMPLATE.format(port=port)
        try:
            child.sendline(cmd)
            child.expect('#', timeout=2)
            raw_output = child.before.decode('utf-8')
            output = strip_state_output(raw_output).strip()

            if "-E- FW burnt on device does not support generic access register" in output:
                print(f"[ERROR] {hostname} FW does not support required access: {port_id}")
                for p in ports_to_check:
                    result['errored_ports'][f"1/{p}"] = "FW_UNSUPPORTED"
                fw_error_detected = True
                break

            state = parse_link_state(output)
            ber = parse_symbol_ber(output)

            if is_ignored_port(state, ber):
                result['ignored_ports'][port_id] = state or "Disabled"
                continue

            if ber is None or ber.upper() == "N/A":
                result['errored_ports'][port_id] = ber or "N/A"
            elif float(ber) >= BER_THRESHOLD:
                result['failed_ports'][port_id] = ber
            else:
                result['passed_ports'][port_id] = ber
        except Exception as e:
            print(f"[DEBUG] Exception on {hostname} port {port_id}: {e}")
            result['errored_ports'][port_id] = "EXCEPTION"

    child.close()
    return result

def main():
    parser = argparse.ArgumentParser(description="Check Symbol BER on device ports")
    parser.add_argument('filename', help='File with device hostnames (one per line)')
    parser.add_argument('--cutsheet', required=True, help='Path to cutsheet CSV file')
    parser.add_argument('--show-passed', action='store_true', help='Show ports that passed the BER threshold')
    parser.add_argument('--show-ignored', action='store_true', help='Show ports that were ignored (non-active or N/A)')
    parser.add_argument('--su', type=int, help='Only check ports for a specific SU (for HSG T2 devices, 1-6 only)')

    auth_group = parser.add_mutually_exclusive_group()
    auth_group.add_argument('--password', help='Password from jitpw')
    auth_group.add_argument('--jitpw', default=PATH_TO_JITPW, help='Path to JIT password tool (default: ~/jitpw/bin/jitpw)')

    args = parser.parse_args()

    with open(args.filename, 'r') as f:
        hostnames = [line.strip() for line in f if line.strip()]

    print(f"Running on {len(hostnames)} devices...")

    results = []
    with ThreadPoolExecutor(max_workers=9) as executor:
        futures = []
        for host in hostnames:
            password = get_password(host, args.password, args.jitpw)
            futures.append(executor.submit(run_checks_on_device, host, password, args.su))

        for future in as_completed(futures):
            results.append(future.result())

    total_devices = len(results)
    passed_devices = failed_devices = errored_devices = 0
    for res in results:
        errored = res['errored_ports']
        failed = res['failed_ports']
        if errored and all(v in ("SSH_FAIL", "FW_UNSUPPORTED") for v in errored.values()):
            errored_devices += 1
        elif failed or errored:
            failed_devices += 1
        else:
            passed_devices += 1

    print(f"\n{'=' * 60}")
    print("Device Summary")
    print(f"Total Devices     : {total_devices}")
    print(f"Devices PASSED    : {passed_devices}")
    print(f"Devices FAILED    : {failed_devices}")
    print(f"Devices ERRORED   : {errored_devices}")
    print(f"{'=' * 60}")

    for res in results:
        if (not res['failed_ports'] and not res['errored_ports']):
            if args.show_passed or args.show_ignored:
                print(f"{'#' * 60}")
                print(f"{res['hostname']} PASSED")
                if args.show_passed:
                    print("  Passed Ports :", res['passed_ports'])
                if args.show_ignored:
                    print("  Ignored Ports:", res['ignored_ports'])
                print(f"{'-' * 60}")
            continue

        print(f"{'#' * 60}")
        print(f"{res['hostname']} FAIL")
        print("  Failed Ports :", res['failed_ports'])
        print("  Errored Ports:", res['errored_ports'])
        if args.show_ignored:
            print("  Ignored Ports:", res['ignored_ports'])
        if args.show_passed:
            print("  Passed Ports :", res['passed_ports'])
        print(f"{'-' * 60}")

    # Cable cleaning output generation
    try:
        with open(args.cutsheet, 'r') as f:
            cutsheet_lines = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"Cutsheet file not found: {args.cutsheet}")
        cutsheet_lines = []

    cleaning_recommendations = []
    for res in results:
        device = res['hostname']
        for port in res['failed_ports']:
            parts = port.split("/")
            if len(parts) == 3:
                port_clean = f"IB{parts[0]}/{parts[1]}/{parts[2]}"
            else:
                continue
            match = None
            for line in cutsheet_lines:
                fields = line.split(',')
                if len(fields) < 18:
                    continue
                near_device, near_port = fields[5].strip(), fields[6].strip()
                far_port, far_device = fields[12].strip(), fields[13].strip()
                if (device == near_device and port_clean == near_port) or (device == far_device and port_clean == far_port):
                    match = fields
                    break
            if match:
                near_rack = match[2].strip()
                near_u = match[3].strip()
                near_device = match[5].strip()
                near_port = match[6].strip()
                far_port = match[12].strip()
                far_device = match[13].strip()
                far_u = match[15].strip()
                far_rack = match[16].strip()
                cleaning_recommendations.append(
                    f"Clean both ends and any patch panels in the middle "
                    f"[{near_rack}:{near_u}] {near_device} {near_port} <> "
                    f"[{far_rack}:{far_u}] {far_device} {far_port}"
                )
            else:
                print(f"[WARNING] No match found in cutsheet for {device} {port_clean}")

    cleaning_recommendations = list(set(cleaning_recommendations))

    if cleaning_recommendations:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fec_output_file = f"bad_fec_{timestamp}.txt"
        with open(fec_output_file, "w") as f:
            f.write("\n".join(cleaning_recommendations))
        print(f"\nCable cleaning recommendations saved to: {fec_output_file}")
        for line in cleaning_recommendations:
            print(line)

if __name__ == "__main__":
    main()
