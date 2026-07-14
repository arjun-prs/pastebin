import re
import sys
import time
import argparse
import pexpect
from prometheusclient import PrometheusClient
import sqlite3
from getpass import getpass
from collections import OrderedDict
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import traceback
from dataclasses import dataclass

@dataclass
class Aggregates:
    """Aggregates represent the aggregate data for an SU."""
    t1_t0_links_down: Optional[int] = 0
    t2_t1_links_down: Optional[int] = 0
    t2_light_level_issues: Optional[int] = 0
    t1_light_level_issues: Optional[int] = 0
    t0_light_level_issues: Optional[int] = 0

    def __str__(self):
        return f"""
T1 <> T0 Links Down\t: {self.t1_t0_links_down}
T2 <> T1 Links Down\t: {self.t2_t1_links_down}
T0 Light level issues\t: {self.t0_light_level_issues}
T1 Light level issues\t: {self.t1_light_level_issues}
T2 Light level issues\t: {self.t2_light_level_issues}
"""

@dataclass
class Results:
    aggregates: Aggregates
    t1_t0_links_down: Optional[List[str]] = None
    t2_t1_links_down: Optional[List[str]] = None
    t2_light_level_issues: Optional[List[str]] = None
    t1_light_level_issues: Optional[List[str]] = None
    t0_light_level_issues: Optional[List[str]] = None


def strip_ansi(text: str) -> str:
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def find_test_lines(lines: List[str], test_patterns: List[str]) -> Dict[str, List[str]]:
    result = {}
    collecting = False
    current_test = None
    collected_lines = []
    test_counter = {}

    for line in lines:
        clean = strip_ansi(line).strip()

        found_test = False
        for pattern in test_patterns:
            match = re.match(r"Name:\s*(\S+)", clean)
            if match:
                test_name = match.group(1)
                if test_name in test_patterns and "temperature" not in test_name:
                    if collecting and current_test and collected_lines:
                        key = f"{current_test}-{test_counter[current_test]}"
                        result[key] = collected_lines

                    collecting = True
                    current_test = pattern
                    collected_lines = []
                    test_counter[current_test] = test_counter.get(current_test, 0) + 1
                    found_test = True
                    break

        if not found_test and collecting:
            if "Name:" in clean and not any(tp in clean for tp in test_patterns):
                if current_test and collected_lines:
                    key = f"{current_test}-{test_counter[current_test]}"
                    result[key] = collected_lines
                collecting = False
                current_test = None
                collected_lines = []
            elif clean:  # collect all non-empty lines
                collected_lines.append(clean)

    if collecting and current_test and collected_lines:
        key = f"{current_test}-{test_counter[current_test]}"
        result[key] = collected_lines

    return result


def remove_dups(optical_issues: List[str]) -> List[str]:
    # the dict is what makes these unique as the key must be unique
    uniques = dict()
    for issue in optical_issues:
        # we'll use the hostname/port combo as the key
        hostname_and_port = ':'.join(issue.split()[:2])
        # this is a little dirty as we'll update the value again 
        # and again until we hit the last occurrence (oh well!)
        uniques[hostname_and_port] = issue
    new_list = list()
    for hn_port_combo, issue in uniques.items():
        new_list.append(issue)
    print(f"before there were {len(optical_issues)}\nwith dups removed there are {len(new_list)}")
    return new_list

def collect_cabling_issues(output: Dict[str, List[str]], test_patterns: List[str]) -> Dict[str, List[str]]:
    final_result = OrderedDict()

    for section, lines in output.items():
        print(f"\n Rack: {section}")
        test_links = find_test_lines(lines, test_patterns)
        for test_key, links in test_links.items():
            full_key = f"{section}-{test_key}"
            final_result[full_key] = links

    return final_result


def ncpcli_connection_failed(output: str) -> bool:
    match = re.search(r".*<ERROR>.*Something went wrong:.*Problems connecting into region using command:.*", output)
    return bool(match)


def _run_healthchecks_by_region_and_rack_and_role(region: str, rack: str, role: str, yubikey_pin: str, ncp_output: bool) -> List[str]:
    cmd = f"ncpcli -r {region} interactive"
    prompt_match = f"ncpcli@{region}"
    lines = list()
    try:
        child = pexpect.spawn(cmd, encoding='utf-8')
        try:
            index = child.expect(["Enter yubikey", pexpect.EOF, pexpect.TIMEOUT], timeout=15)
            if index == 0:
                child.sendline(yubikey_pin)
            else:
                print("Yubikey prompt not shown. Continuing without PIN.")
        except pexpect.EOF:
            print(">>> EOF. The child process exited unexpectedly.")
            print(child.before)
            return [f"ERROR: Unexpected EOF during healthcheck on rack {rack}"]

        except pexpect.TIMEOUT:
            print(">>> Timeout. Last output before timeout:")
            print(child.before)
            return [f"ERROR: Timeout during healthcheck on rack {rack}"]

        except Exception as e:
            print(f"Unhandled exception during healthcheck on rack {rack}: {e}")
            return [f"ERROR: {str(e)}"]

        child.expect(prompt_match)
        child.sendline(f"update-device-list --devices-by-rack {rack} --devices-by-role {role}")
        child.expect(prompt_match)
        child.sendline("current-devices -v")
        child.expect_exact(prompt_match, timeout=300)
        output = child.before

        if "<ifabt" not in output:
            child.sendline(f"update-device-list --devices-by-rack {rack}")
            child.expect(prompt_match)
            child.sendline("current-devices -v")
            child.expect_exact(prompt_match, timeout=300)
            output = child.before
            if "<ifabt" in output:
                role = ""
                match = re.search(r"<\s*([a-zA-Z0-9_]+)\s*=", output)
                if match:
                    role = match.group(1)
                if role == "":
                    child.sendline("exit")
                    return []
                child.sendline(f"update-device-list --devices-by-rack {rack} --devices-by-role {role}")
                child.expect(prompt_match, timeout=90)

        if ncpcli_connection_failed(output):
            print("The connection to the region failed.")
            child.sendline("exit")
            return []

        child.sendline(f"healthcheck run-device-job --tags dcs_rack_validation,ls_rack_validation --wait")
        child.expect_exact(prompt_match, timeout=300)
        output = child.before
        if ncp_output:
            print("#" * 50)
            print("NCPCLI Healthcheck Output:")
            print("#" * 50)
            print(output)
            print("#" * 50)

        lines = output.splitlines()
        if lines:
            lines = lines[1:]

        child.sendline("exit")

    except pexpect.TIMEOUT:
        print(">>> Timeout. Last output before timeout:")
        print(child.before)

    return lines


def run_healthchecks_by_region_and_rack_and_role(region: str, racks: List[str], role: str, yubikey_pin: str, ncp_output: bool) -> Dict[str, List[str]]:
    start_all = time.perf_counter()
    results = OrderedDict()

    def run_for_rack(rack):
        try:
            print(f"Running healthchecks on rack {rack}...")
            start = time.perf_counter()
            details = _run_healthchecks_by_region_and_rack_and_role(
                region=region,
                rack=rack,
                role=role,
                yubikey_pin=yubikey_pin,
                ncp_output=ncp_output
            )
            end = time.perf_counter()
            print(f"Healthchecks for rack {rack} completed in {end - start:0.3f} seconds")
            return rack, details
        except Exception as e:
            print(f"Failed to run healthchecks for rack {rack}: {e}")
            traceback.print_exc()
            return rack, [f"ERROR: {str(e)}"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_rack = {
            executor.submit(run_for_rack, rack): rack for rack in racks
        }

        completed = {}
        for future in as_completed(future_to_rack):
            rack, details = future.result()
            completed[rack] = details

    for rack in racks:
        results[rack] = completed[rack]

    end_all = time.perf_counter()
    print(f"All {len(racks)} rack healthchecks completed in {end_all - start_all:0.3f} seconds")
    return results


def list_str(values):
    return values.split(',')


class RackElevationDB:
    def __init__(self, db_file):
        try:
            self.conn = sqlite3.connect(db_file)
        except Exception as e:
            print(f"Failed to load DB file: {e}")
            self.conn = None

    def get_rack_elevations_by_su(self, hostname: str, intf: str) -> str:
        if not self.conn:
            return "DB not connected"

        query = """
        SELECT 
            rack, 
            elevation 
        FROM device_info 
        WHERE device = ? AND interface = ?;
        """
        try:
            cur = self.conn.cursor()
            cur.execute(query, (hostname, intf))
            res = cur.fetchone()
            if not res:
                return "N/A"
            rack, ele = res
            return f"{rack}:{ele}"
        except Exception as e:
            return f"DB error: {e}"

    def get_remote_device_info(self, device: str, interface: str) -> Optional[Dict[str, str]]:
        query = """
            SELECT remote_device, remote_interface
            FROM connections
            WHERE device = ? AND interface = ?
        """
        cursor = self.conn.execute(query, (device, interface))
        row = cursor.fetchone()
        if row:
            return {"remote_device": row[0], "remote_interface": row[1]}
        return None

    def close(self):
        if self.conn:
            self.conn.close()

def prefetch_prometheus_data(client) -> Dict[str, List[Dict]]:
    all_data = {}

    queries = {
        'interface_status': 'ifOperStatusNumeric{remote_role!="compute", role=~"ifab.*"}',
        'device_info': 'deviceInfo{role=~"ifab.*"}'
    }

    for key, query in queries.items():
        try:
            print(f"Fetching Prometheus data for: {key}")
            result = client.get_prometheus_metrics(query)
            all_data[key] = result.get('result', [])
        except Exception as e:
            print(f"Failed to run Prometheus query [{key}]: {e}")
            all_data[key] = []

    return all_data


def get_interface_data_prefetched(device, interface, all_data):
    result = {}
    iface_name = f"IB{interface}"

    # Interface Status
    for metric in all_data.get('interface_status', []):
        m = metric.get('metric', {})
        if m.get('device') == device and m.get('interface') == iface_name:
            result.update(m)
            break

    if not result:
        result['error'] = "No interface metric data"
        return (device, interface, result)

    # Device Info - Local
    for metric in all_data.get('device_info', []):
        m = metric.get('metric', {})
        if m.get('device') == device:
            result['rack'] = m.get('rack')
            result['elevation'] = m.get('elevation')

    # Device Info - Remote
    remote_device = result.get("remote_device")
    if remote_device:
        for metric in all_data.get('device_info', []):
            m = metric.get('metric', {})
            if m.get('device') == remote_device:
                result['remote_rack'] = m.get('rack')
                result['remote_elevation'] = m.get('elevation')
                result['remote_role'] = m.get('role')

    return (device, interface, result)


def parse_link_line(line: str, db: RackElevationDB) -> str:
    pattern = r"Link\s+(\S+):\s+(IB\d+/\d+/\d+)\s+<==>\s+\{['\"](IB\d+/\d+/\d+)['\"]:\s+['\"](\S+)['\"]"
    match = re.search(pattern, line)
    if match:
        device_a, interface_a, interface_b, device_b = match.groups()
        rack_a = db.get_rack_elevations_by_su(device_a, interface_a)
        rack_b = db.get_rack_elevations_by_su(device_b, interface_b)
        return f"{device_a}: {interface_a} ({rack_a}) <==> {device_b}: {interface_b} ({rack_b})"
    return None


def parse_optics_line(line: str, all_prometheus_data: Dict) -> str:
    pattern = (
        r"interface\s+(\S+)\s+on device:\s+(\S+),\s+optics channel\s+(tx|rx)\s+power data\s+(-?\d+\.\d+)\s+(?:is\s+)?out of range low:\s+(-?\d+\.\d+);\s+high:\s+(-?\d+\.\d+)"
    )
    match = re.search(pattern, line)
    if match:
        intf, device, direction, value_str, _, _ = match.groups()
        try:
            value = float(value_str)
            if value == -40.0:
                return None

            _, _, data = get_interface_data_prefetched(device, intf, all_prometheus_data)

            if 'error' in data:
                return f"{device}: {intf} - {data['error']}"

            local_str = f"{device}: {intf} ({data.get('rack', '?')}:{data.get('elevation', '?')})"
            remote_device = data.get("remote_device")
            remote_interface = data.get("remote_interface")
            remote_str = ""

            if remote_device and remote_interface:
                remote_str = f" <==> {remote_device}: {remote_interface} ({data.get('remote_rack', '?')}:{data.get('remote_elevation', '?')})"

            return f"{local_str} [{direction.upper()} Power {value}] {remote_str}".strip()

        except Exception as e:
            return f"{device}: {intf} - Error parsing optics data: {e}"

    return None

def generate_aggregate_data(filtered_data: Dict[str, List[str]]) -> Aggregates:
    """generate_aggregate_data iterates through the top level data structure to get 
    aggregate counts, returning them to the caller.
    TODO: 
    - [ ] this could be a method
    - [ ] there are duplicates that need to be removed"""
    rx_power_issue_pattern = "for interface"
    t2_pattern = "-t2-"
    t1_pattern = "-t1-"
    t0_pattern = "-t0-"
    t1_t0_link_down_pattern = re.compile(r".*Link.*-t1-.*-t0-.*")
    t0_t1_link_down_pattern = re.compile(r".*Link.*-t0-.*-t1-.*")
    t2_t1_link_down_pattern = re.compile(r".*Link.*-t2-.*-t1-.*")
    t1_t2_link_down_pattern = re.compile(r".*Link.*-t1-.*-t2-.*")
    agg = Aggregates()
    for rack_test, lines in filtered_data.items():
        # TODO: not sure we need rack_test; could throw it away
        for ln in lines:
            if rx_power_issue_pattern in ln:
                if t2_pattern in ln:
                    agg.t2_light_level_issues += 1
                elif t1_pattern in ln:
                    agg.t1_light_level_issues += 1
                elif t0_pattern in ln:
                    agg.t0_light_level_issues += 1
            if t1_t0_link_down_pattern.search(ln) or t0_t1_link_down_pattern.search(ln):
                agg.t1_t0_links_down += 1
            if t2_t1_link_down_pattern.search(ln) or t1_t2_link_down_pattern.search(ln):
                agg.t2_t1_links_down += 1

    return agg


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", type=str, required=True)
    parser.add_argument("--racks", type=list_str, required=True)
    parser.add_argument("--role", type=str, required=True)
    parser.add_argument("--summaryonly", required=False, action='store_true')
    parser.add_argument("--noyubikey", required=False, action='store_true')
    parser.add_argument("--ncpoutput", required=False, action='store_true')
    parser.add_argument("--su_number", type=str, required=True)
    parser.add_argument("--tests", type=list_str, required=True, help="Comma-separated test names to run (e.g. test_interface_phy_ifab,test_ifab_optics)")
    args = parser.parse_args()

    yubikey_pin = '' if args.noyubikey else getpass("yubikey pin:")
    ncp_output = args.ncpoutput
    test_patterns = args.tests

    db_path = f"data/hsg/device_info_su{args.su_number}.db"

    db = RackElevationDB(db_path)
    results = run_healthchecks_by_region_and_rack_and_role(
        region=args.region,
        racks=args.racks,
        role=args.role,
        yubikey_pin=yubikey_pin,
        ncp_output=ncp_output
    )

    if len(results) == 0:
        print("the healthcheck did not run, exiting...")
        sys.exit(1)

    print(f"\n\n\n\n")

    filtered = collect_cabling_issues(results, test_patterns)

    region = args.region

    client = None
    prometheus_data = None

    if "test_ifab_optics" in test_patterns:
        client = PrometheusClient(region)
        prometheus_data = prefetch_prometheus_data(client)

    print("\n====Summary ====:\n")
    for test_key, lines in filtered.items():
        print(f"Test: {test_key}")
        for line in lines:
            if "Message" in line:
                line = line.replace("Message: Failed: Bad interfaces found for", "Link downs for device:")
            if line.startswith(": - "):
                line = line.replace(" -", "")
            if "Link" in line:
                parsed = parse_link_line(line, db)
                if parsed:
                    print(parsed)
                else:
                    print(line)
            elif "optics channel" in line:
                parsed = parse_optics_line(line, prometheus_data)
                if parsed:
                    print(parsed)
            else:
                print(line)
        print("-" * 60)

    aggregates = generate_aggregate_data(filtered)
    print(aggregates)