import re
# from os import getenv # was using this for the yubikey (not sure if that's a security risk, so defaulted to cli arg)
import sys
import time
from collections import OrderedDict
import argparse
import pexpect
from getpass import getpass
from typing import List, Dict
from prometheusclient import PrometheusClient
from concurrent.futures import ThreadPoolExecutor, as_completed

def extract_ifab_role(output: str) -> str:
    match = re.search(r"<\s*([a-zA-Z0-9_]+)\s*=", output)

    role = ""
    if match:
        role = match.group(1)
        print("Extracted:", role)
    else:
        print("Couldn't determine role.")

    return role

def ncpcli_connection_failed(output: str) -> bool:
    match = re.search(r".*<ERROR>.*Something went wrong:.*Problems connecting into region using command:.*", output)
    if match:
        return True
    return False

def _run_healthchecks_by_region_and_rack_and_role(region: str, rack: str ,role: str, yubikey_pin: str, ncp_output: bool) -> Dict[str, List[str]]:
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
        except pexpect.ExceptionPexpect as e:
            print(f"An error occurred: {e}")

        child.expect(prompt_match)
        child.sendline(f"update-device-list --devices-by-rack {rack} --devices-by-role {role}")
        child.expect(prompt_match)
        child.sendline("current-devices -v")

        # check for devices returned 
        child.expect_exact(prompt_match, timeout=300)  
        output = child.before

        if "<ifabt" not in output:
            print(f"there were no devices returned, trying to figure out which role we've got in rack {rack}...")
            child.sendline(f"update-device-list --devices-by-rack {rack}")
            child.expect(prompt_match)
            child.sendline("current-devices -v")
            child.expect_exact(prompt_match, timeout=300)  
            output = child.before 
            if "<ifabt" in output:
                print("getting role...")
                role = extract_ifab_role(output)
                if role == "":
                    print("There are no ifab devices in this rack. Logging out of ncpcli interactive session")
                    child.sendline("exit")
                    return [] # returning empty string for sanity check at the call site. 
                child.sendline(f"update-device-list --devices-by-rack {rack} --devices-by-role {role}")                
                child.expect(prompt_match, timeout=90)

        if ncpcli_connection_failed(output):
            print("The connection to the region failed.")
            child.sendline("exit")
            return [] # returning empty string for sanity check at the call site.

        child.sendline(f"healthcheck run-device-job --tags dcs_rack_validation,ls_rack_validation --wait")

        child.expect_exact(prompt_match, timeout=300)  
        output = child.before
        if ncp_output:
            print("#"*50)
            print("NCPCLI Healthcheck Output:")
            print("#"*50)
            print(output)
            print("#"*50)

        lines = output.splitlines()
        if lines:
            lines = lines[1:]  # remove the command itself from the result

        child.sendline("exit")

    except pexpect.TIMEOUT:
        print(">>> Timeout. Last output before timeout:")
        print(child.before)

    return lines

def run_healthchecks_by_region_and_rack_and_role(region: str, racks: List[str] ,role: str, yubikey_pin: str, ncp_output: bool) -> Dict[str, List[str]]:
    """run_healthcheck_by_region_and_rack_and_role drops into an ncpcli interactive sesion in the provided 
    region and runs the following healthcecks
    
    - dcs_rack_validation
    - ls_rack_validation

    It returns the output as a list of output lines. 
    """
    start_all = time.perf_counter()
    results = OrderedDict()

    def run_for_rack(rack):
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

    with ThreadPoolExecutor(max_workers=8) as executor:
        # Submit all rack tasks
        future_to_rack = {
            executor.submit(run_for_rack, rack): rack for rack in racks
        }

        # As they complete, store results in rack order
        completed = {}
        for future in as_completed(future_to_rack):
            rack, details = future.result()
            completed[rack] = details

    # Re-order by the original racks list
    for rack in racks:
        results[rack] = completed[rack]

    end_all = time.perf_counter()
    print(f"All {len(racks)} rack healthchecks completed in {end_all - start_all:0.3f} seconds")
    return results

def dump_file_into_mem(fn: str):
    """Pull the file into memory and return is a list of strings."""
    fd = list()
    try:
        with open(fn) as fh:
            fd = fh.read().splitlines()
    except Exception as e:
        print(f"failed to get json: {e}")
    return fd


def find_miscabled_links(lines: List[str]) -> List[str]:
    """Iterate through the lines and look for explicitly for lines illustrating cable problems."""
    miscabled_links = list()
    pattern = "Expected connection"
    for ln in lines:
        if pattern in ln:
            miscabled_links.append(ln)
    return miscabled_links     


def clean_line(ln: str) -> str:
    ns = ln.strip(": ")
    return ns


def cleanup_output(miscabled_links: List[str]) -> List[str]:
    """Sanitizes the output, breaking it out into two lines for easier digestion."""
    sanitized = list()
    for link in miscabled_links:
        cleaned = clean_line(link)
        # break into two lines
        split = list()
        try:
            split = cleaned.split("Current")
            split.reverse()
            for i in split:
                sanitized.append(i.replace("Expected connection - ", "\nTO \n\n").replace(" connection -", "\nplease swap/fix cable connected to: "))
            sanitized.append("\n\n################################################################################################")
        except IndexError as e:
            continue
        # nl = "\n".join(split)
        # sanitized.append(nl) 
    return sanitized


def remove_ignored_links(miscabled_links: List[str]) -> List[str]:
    """This may not be a long-lasting piece of logic. This was stricly for our urgent use case
    in HSG, where we were omitting certain links."""
    with_removed_links = list()
    pattern = re.compile(r'.*Expected connection.*-t1-.*IB1/(25|26|27|28|29|30|31|32).*=>.*')
    included_lines = [line for line in miscabled_links if not pattern.search(line)]
    for line in included_lines:
        with_removed_links.append(line)
    return with_removed_links


def get_excluded_links(sanitized_miscabled_links: List[str], with_removed_links: List[str]):
    """This is a helper function that may not be very useful. It provides a diff between all 
    of the miscabled links and those we cared about (at the time) for HSG."""
    return list(set(sanitized_miscabled_links).symmetric_difference(set(with_removed_links)))


def create_rack_elevation_cache(fn: str) -> Dict:
    """This pulls in the cabling file generated from the plan and creates a cache where you
    pass in the hostname and the port and you are returned the rack information."""
    cache = dict()
    ifab_devices = list()
    try:
        with open(fn) as fh:
            fd = fh.read().splitlines()
        for ln in fd:
            if "NVIDIA-MQM9700" in ln:
                ifab_devices.append(ln)
        for i in ifab_devices:
            split = i.split(",")
            # A side 
            a_side_rack = split[2]
            a_side_ru = split[3]
            cache[",".join(split[5:7])] = f"{a_side_rack},{a_side_ru}"
            # Z side 
            z_side_hostname = split[13]
            z_side_port = split[12]
            z_side_ru = split[15]
            z_side_rack = split[16]
            cache[f"{z_side_hostname},{z_side_port}"] = f"{z_side_rack},{z_side_ru}"
    except Exception as e:
        print(f"failed to open file: {e}")
    return cache

def map_device_and_port_to_rack_details(ln: str, cache: Dict) -> str:
    """This function takes in the raw data from the optical healthcheck and calls out to the cache to map 
    the hostname and port info to the rack info."""
    #for interface 1/11/2 on device: hsg3-i1-su1-t1-r33, optics channel rx power data -7.63463 is out of range low: -6.00153; high: 4.99989.
    #for interface 1/23/1 on device: hsg3-i1-su1-t1-r65 : hsg3:1709:5, optics channel rx power data -9.3968 is out of range low: -6.00153; high: 4.99989.
    #for interface 1/10/1 on device: hsg3-i1-su1-t1-r30, : hsg3:1705,7,  channel rx power data -9.52725 is out of range low: -6.00153; high: 4.99989.
    split = ln.split("optics")
    dev = split[0].split()[5]
    port = split[0].split()[2]
    bldg = dev.split("-")[0]
    k = f"{dev}IB{port}"
    first_part = split[0].strip(", ")
    rack_info = cache[k]
    rack_info = rack_info.replace(",", ":")
    return f"{first_part}: {bldg}:{rack_info}, optics {split[1]}"


def find_optical_issues(lines: List[str]) -> List[str]:
    """find_optical_issues loops through the raw data of the healthcheck file, looking for optical problems.
    There is a little bit of cleanup as well."""
    all_optical_issues = list()
    pattern = "optics channel rx power data"
    for ln in lines:
        if pattern in ln:
           all_optical_issues.append(ln)
    ignore_pattern = "optics channel rx power data -40.0"
    optical_issues = list() 
    for ln in all_optical_issues:
        if ignore_pattern not in ln:
            optical_issues.append(ln.strip("                 :"))

    return optical_issues 

def _create_cabling_issues_report(rack: str, details: List[str], summary_only: bool, client) -> None:
    miscabled_links = find_miscabled_links(details)
    sanitized_miscabled_links = cleanup_output(miscabled_links)
    with_removed_links = sanitized_miscabled_links                          ## Comment this line out when we add intelligence to selectively grab one column rack vs another. 
    # with_removed_links = remove_ignored_links(sanitized_miscabled_links)  ## Uncomment this line when we're ready to implement the above logic. 

    sections = []
    section = []
    for line in with_removed_links:
        section.append(line)
        if line.strip().startswith("###"):
            sections.append(section)
            section = []

    unknown_sections = []
    known_sections = []

    for sec in sections:
        if any('unknown' in line.lower() for line in sec):
            unknown_sections.append(sec)
        else:
            known_sections.append(sec)

    if not summary_only:
        print("=== LINK DOWN ===")
        for sec in unknown_sections:
            print("\n".join(sec)) 

        print("\n=== MISCABLED LINKS ===")
        for sec in known_sections:
            print("\n".join(sec))

    print(f"\n{rack}:")
    print("=== SUMMARY ===")
    print(f"Link down issue count: {len(unknown_sections)}")
    print(f"Miscabled link issue count: {len(known_sections)}")
    extract_from_file_and_format(client=client, lns=details, summary_only=summary_only)


def create_cabling_issues_report(results: Dict[str, str], client, summary_only: bool) -> None:
    for rack, details in results.items():
        _create_cabling_issues_report(rack=rack, details=details, summary_only=summary_only, client=client)



def create_optical_issues_report(lns: List[str], cable_fn: str) -> None:
    print(f"\n\n\n#optical issues")
    optical_issues = find_optical_issues(lns)
    cable_cache = create_rack_elevation_cache(cable_fn)
    # Instead of supplying multiple channels on the same port, 
    # we are going to only provide the results of 1 channel. 
    reduced_optical_issues = dict()
    for i in set(optical_issues):
        issue = map_device_and_port_to_rack_details(i, cable_cache)
        info = ' '.join(issue.split()[:6])
        reduced_optical_issues[info] = issue
    sorted_items = sorted(reduced_optical_issues.items())
    for _, v in sorted_items:
        print(v)

def list_str(values):
    """helper func for the racks CLI argument"""
    return values.split(',')


def get_interface_data(client, device, interface):
    result = {}
    try:
        query = f'ifOperStatusNumeric{{device="{device}", interface="IB{interface}", remote_role!="compute"}}'
        metrics = client.get_prometheus_metrics(query)
        metric_results = metrics.get('result', [])

        if not metric_results:
            result['error'] = "No interface metric data"
            return (device, interface, result)

        metric_data = metric_results[0].get('metric', {})
        result['device'] = metric_data.get('device')
        result['interface'] = metric_data.get('interface')
        result['remote_device'] = metric_data.get('remote_device')
        result['remote_interface'] = metric_data.get('remote_interface')
        result['remote_role'] = metric_data.get('remote_role')

        if result['device']:
            device_query = f'deviceInfo{{device="{result["device"]}"}}'
            device_metrics = client.get_prometheus_metrics(device_query)
            device_results = device_metrics.get('result', [])

            if device_results:
                device_metric_data = device_results[0].get('metric', {})
                result['rack'] = device_metric_data.get('rack')
                result['elevation'] = device_metric_data.get('elevation')

        if result.get('remote_device'):
            remote_query = f'deviceInfo{{device="{result["remote_device"]}"}}'
            remote_metrics = client.get_prometheus_metrics(remote_query)
            remote_results = remote_metrics.get('result', [])

            if not remote_results:
                result['error'] = f"Remote device {result['remote_device']} not found"
                return (device, interface, result)

            remote_metric_data = remote_results[0].get('metric', {})
            result['remote_rack'] = remote_metric_data.get('rack')
            result['remote_elevation'] = remote_metric_data.get('elevation')
            result['remote_role'] = remote_metric_data.get('role')

    except Exception as e:
        result['error'] = f"Failed to extract interface data: {e}"

    return (device, interface, result)


def extract_from_file_and_format(client, lns: List[str], summary_only: bool) -> int:
    pattern = re.compile(
        r'^\s*:?\s*for interface (\d+/\d+/\d+) on device: ([\w\-]+), optics\s+channel rx power data\b.*',
        re.IGNORECASE
    )

    interface_metadata = []

    for line in lns:
        match = pattern.search(line)
        if match:
            interface, device = match.groups()
            interface_metadata.append((device, interface))

    interface_metadata = list(set(interface_metadata))

    clean_entries = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_metadata = {
            executor.submit(get_interface_data, client, device, interface): (device, interface)
            for device, interface in interface_metadata
        }

        for future in as_completed(future_to_metadata):
            device, interface = future_to_metadata[future]
            try:
                _, _, data = future.result()
                if 'error' in data:
                    if data.get("remote_role") == "compute":
                        print(f"Skipping {device} {interface}: remote_role is compute")
                else:
                    clean_entries.append((device, interface, data))
            except Exception as e:
                print(f"Unhandled exception for {device} {interface}: {e}")

    issue_count = len(clean_entries)
    print(f"Optical link issue count: {issue_count}")

    if not summary_only:
        print("\n=== BAD LIGHT LEVELS ===")
        for device, interface, data in clean_entries:
            print(
                f"Clean both ends {device} interface {interface} "
                f"{data.get('rack')}:{data.get('elevation')} <-> "
                f"{data.get('remote_device')} interface {data.get('remote_interface')} "
                f"{data.get('remote_rack')}:{data.get('remote_elevation')}"
            )

    return issue_count


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", type=str, required=True, help="Region against which the healthcheck will run (e.g. hsg3)")
    parser.add_argument("--racks", type=list_str, required=True, help="Rack identifier (e.g. 1702,1703)")
    parser.add_argument("--role", type=str, required=True, help="Role (e.g. ifabt2)")
    parser.add_argument("--cablesfile", type=str, required=True, help="Cables file (e.g. /Users/christopherhern/plan/hsg/hsg3-cables.csv)")
    parser.add_argument("--summaryonly", required=False, action='store_true', help="Provide only the summary info, exlcuding the details")
    parser.add_argument("--noyubikey", required=False, action='store_true', help="Skips asking for yubikey for ncpcli health checks")
    parser.add_argument("--ncpoutput", required=False, action='store_true', help="Shows NCPCLI raw output")
    args = parser.parse_args()
    if args.noyubikey:
        yubikey_pin = ''
    else:
        yubikey_pin = getpass("yubikey pin:")
    if args.ncpoutput:
        ncp_output = True
    else:
        ncp_output = False
    results = run_healthchecks_by_region_and_rack_and_role(region=args.region, racks=args.racks, role=args.role, yubikey_pin=yubikey_pin, ncp_output=ncp_output)
    if len(results) == 0:
        print("the healthcheck did not run, exiting...")
        sys.exit(1)
    # give some space between logs and summary
    print(f"\n\n\n\n")
    # for l in lns:
    #     print(l)
    cable_fn = args.cablesfile
    # create_cabling_issues_report(results, args.summaryonly)
    # create_optical_issues_report(lns, cable_fn)

    region = args.region
    client = PrometheusClient(region)
    create_cabling_issues_report(results, client, args.summaryonly)
