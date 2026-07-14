"""
Overview:
-------
This script is designed to analyze the network topology of a specific region, building, and block, and determine the count of missing network links. 
It uses the ncpcli command-line tool to download the necessary rackmap and topology files, and then parses the data to identify the fabric version and
count the links between different tiers (T0-T1 and T1-T2).

The script supports multiple fabric versions, including QFAB, GFAB and provides detailed output on the expected and actual link counts, as well as the
number of missing links.

Usage:
------
The script takes three main arguments:

-r or --region: The region code (e.g., cwl15)
-b or --block: The block number
-i or --design: The design identifier (default: 2)

Run via CLI:
python random-scripts/fabric_link_count.py -r iad49 -b 40 -i 1

Purpose:
-------
The purpose of this script is to provide a quick and easy way to analyze the network topology and identify potential issues with missing links.

Example Output:
---------------
******** Processing for fabric version and links count in block. ******
region iad49, block 40, fabric version: qfab3.0
expected links between t0_t1: 8192
actual links between t0_t1:  6144
missing links between t0_t1: 2048
"""


import subprocess
import os
from pathlib import Path
import json
import re
import json
import argparse

def run_command(cmd):
    """ncpcli command runner"""
    process = subprocess.Popen(' '.join(cmd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, text=True)
    output, error = process.communicate()
    if process.returncode != 0:
        print(f"Error executing command:\n{error.strip()}")
        return None
    return output.strip()

def find_and_download_rackmap(region_code):
    """
    Checks for the existence of a rackmap file in the user's home directory
    corresponding to the given region code. If found, deletes the existing
    file to ensure the latest version is downloaded. Afterwards, initiates a 
    command to download the updated rackmap file using 'ncpcli arw rackmap get'.
    Prints out status messages throughout the process.

    Args:
        region_code (str): The region code for which the rackmap is needed.

    Returns:
        list: Returns an empty list if downloading the rackmap fails (output is None).
    """

    # Check if the expected rackmap file exists in user's home
    print("\n****** Working to download rackmap file *****")
    rackmap_path = Path(__file__).parent / f"{region_code}.rackmap"

    if rackmap_path.exists():
        print(f"Rackmap {rackmap_path} found, deleting to get latest one")
        try:
            rackmap_path.unlink()
            print(f"Deleted existing rackmap file: {rackmap_path}")
        except OSError as e:
            print(f"Error deleting rackmap file: {e}")

    print(f"Downloading latest rackmap file from autonet")
    cmd = [
        "ncpcli arw rackmap get", region_code[:3],
    ]
    print(f"\nRunning command:\n{' '.join(cmd)}")
    print("(Please enter your PIN if prompted)")

    output = run_command(cmd)
    print(output)
    if output is None:
        print(f"Failed to download rackmap for region {region_code}")
        return []
    

def find_and_download_topology(region_code):
    """
    Checks for the existence of a topology file in the user's home directory 
    corresponding to the given region code. If found, deletes the existing 
    file to ensure the latest version is downloaded. Afterwards, initiates a 
    command to download the updated topology file using 'ncpcli prm plan download'.

    Args:
        region_code (str): The region code for which the topology is needed.

    Returns:
        list: Returns an empty list if downloading topo fails.
    """
    print("\n****** Working to download topo file this process may take some time,pls wait accordingly. ***** \n")
    #topo_path = Path.home() / f"autonet/autonet-plans/{region_code}"
    #topo_file = Path.home() / f"{topo_path}/{region_code}.topo"
    topo_file = Path(__file__).parent / f"{region_code}.topo"
    if topo_file.exists():
        print(f"Topo file {topo_file} found, deleting to get latest one")
        try:
            topo_file.unlink()
            print(f"Deleted existing topo file: {topo_file}")
        except OSError as e:
            print(f"Error deleting topo file: {e}")

    cmd = [
        f"ncpcli prm plan download-content -c {region_code}.topo -r {region_code} -o {region_code}.topo --latest"
        #f"ncpcli prm plan download {region_code[:3]} --latest --dir {topo_path}",
    ]
    print(f"\nRunning command:\n{' '.join(cmd)}")
    print("(Please enter your PIN if prompted)")
    output = run_command(cmd)
    print(output)
    if output is None:
        print(f"Failed to download topo for region {region_code}")
        return []
    

def collect_platforms(region_code, bldg, blk):
    """
    Extracts all platform names associated with the specified building and block numbers from rackmap file.

    Args:
        region_code (str): The region identifier for which to fetch data.
        bldg (int): The building number (as int, e.g., 12 for bldg12) to match.
        blk (int): The block number to match.

    Returns:
        list: A list of platform names (as lowercase, stripped strings) associated with the
            specified building and block; returns an empty list if no data found or file missing.
    """
    # Step 1: Download and load rackmap data
    find_and_download_rackmap(region_code)
    rackmap_file = Path(__file__).parent / f"{region_code}.rackmap"
    if not rackmap_file.exists():
        print(f"File not found: {rackmap_file}")
        return []

    with open(rackmap_file) as f:
        data = json.load(f)

    buildings = data.get("buildings", [])
    platform_text = []
    # Step 2: Process each building in the region
    for building_obj in buildings:
        if not isinstance(building_obj, dict):
            print(f"Invalid building object in rackmap data for region {region_code}")
            continue

        building_name = building_obj.get("name", "")
        if not building_name.startswith("bldg"):
            continue

        blocks = building_obj.get("blocks", [])
        for block in blocks:
            if not isinstance(block, dict):
                print(f"Invalid block object in building {building_name} for region {region_code}")
                continue
            name = block.get("name", "")         
            match = re.search(r"block(\d+)", name)
            blk_num = int(match.group(1)) if match else None
            if blk_num is None:
                continue

            # Only process the matching building and block
            if f"bldg{bldg}" == building_name and blk == blk_num:
                all_section = block.get("all", {})
                for entry in all_section.values():
                    platform = entry.get("platform")
                    if platform:
                        platform_text.append(platform.lower().strip())
    if not platform_text:
        print(f"No platform data found for building {bldg} and block {blk} in region {region_code}")
    return platform_text

import json

def determine_fabric_version(platform_text):
    """
    Determines QFAB/GFAB/IFAB version from platform text using pattern matching.
    
    Args:
        platform_text (str): Platform string from rackmap data

    Returns:
        dict: A dictionary containing the fabric version and link count

    Examples:
        determine_fabric_version("net.ad_qfabv3_t0_platform") -> {"fabric_version": "qfab3.0", "link_count": <link_count>}
        determine_fabric_version("net.ad_ifabv1_t0_platform") -> {"fabric_version": "ifab1.1", "link_count": <link_count>}
    """
    platform_text = '\n'.join(platform_text)
    text = platform_text.lower().strip()
    current_dir = os.getcwd()
    platforms_file = os.path.join(current_dir, 'platforms_fabric_mapping.json')
    try:
        with open(platforms_file, 'r') as f:
            platforms = json.load(f)
    except Exception as e:
        print(f"Error opening or parsing JSON file: {e}")
        return None  # Return None or raise an exception to handle the error

    # fabric version basis on platform
    for entry in platforms:
        if entry['platform'].lower() in text:
            return {
                "fabric_version": entry['fabric_version'],
                "expected_link_count": entry['link_count']
            }

    # If no match is found, return a default value or raise an exception
    return {"fabric_version": "Unknown", "link_count": None}


def parse_topo_file_and_convert_into_json(region_code):
    """
    Parses a topology file for the given region code and converts its contents into a JSON-like dictionary.

    Args:
        region_code (str): The region code for which the topology file is to be parsed.

    Returns:
        dict: A dictionary representing the topology data, where each key is a node and its corresponding value is another dictionary containing a list of ports.
        If the topology file is not found, an empty dictionary is returned.
    """
    find_and_download_topology(region_code)
    topo_file = Path(__file__).parent / f"{region_code}.topo"
    if not topo_file.exists():
        print(f"File not found: {topo_file}")
        return []
    try:
        with open(topo_file, 'r') as f:
            lines = [line.rstrip('\n') for line in f]
    except OSError as e:
        print(f"Error reading topology file: {e}")
        return {}
    result = {}
    current_node = None
    in_ports = False
    ports = []
    try:
        for line in lines:
            # Start of a new node
            if not line.startswith('\t') and line.endswith(':'):
                # Save previous node
                if current_node and ports:
                    result[current_node] = {"ports": ports}
                current_node = line[:-1]
                ports = []
                in_ports = False
            elif line.strip() == 'ports:':
                in_ports = True
            elif in_ports and line.startswith('\t\t'):  # port lines are indented with 2 tabs
                port_line = line.strip()
                if '->' in port_line:
                    port, conn = [p.strip() for p in port_line.split('->', 1)]
                else:
                    port = port_line
                    conn = None
                ports.append({'name': port, 'connected_to': conn})

    except Exception as e:
        print(f"Error parsing topology file: {e}")
        return []
    # For the last node in the file
    if current_node and ports:
        result[current_node] = {"ports": ports}
    return result


#print(parse_topo_file_and_convert_into_json('cwl'))
def get_links_count_between_t0_t1(region_code,bldg,design,blk,topo_data):
    """
    Counts the number of links between T0 and T1 devices for a specified region, building,
    design, block, and (optionally) tier by analyzing network topology data.

    Args:
        region_code (str): The region identifier.
        bldg (str): Building identifier used in device naming.
        design (str): Design string for pattern matching device names.
        blk (str): Block identifier used in device naming.

    Returns:
        int: Number of links found that connect matching T0 devices to T1 devices via Ethernet.
    """
    block_prefix = "b" if not design.lower().startswith("i") else "su"
    device_pattern = f"{region_code}{bldg}-{design}-{block_prefix}{blk}"
    t0_devices = re.compile(rf"^{re.escape(device_pattern)}-t0-r\d+$")

    # Regex for connected_to field (to t1 devices)
    t0_t1_links= re.compile(rf"^{re.escape(device_pattern)}-t1-r\d+:Ethernet")

    all_links = []
    #data = parse_topo_file_and_convert_into_json(region_code)
    if not topo_data:
        print(f"Failed to parse data from {region_code}.topo file for {region_code}")
    try:
        # Iterate all device keys and find matches
        for device_name in topo_data:
            if t0_devices.match(device_name):
                device = topo_data[device_name]
                # Defensive: check for "ports" in this device's structure
                if device and "ports" in device:
                    for port in device["ports"]:
                        link = port.get("connected_to")
                        if link is not None:
                            all_links.append(link)
    except Exception as e:
        print(f"Error in getting links between t0-t1 due to {e}")
    # Now filter for t0<-->t1 links
    filtered_t0_t1_links = [link for link in all_links if t0_t1_links.match(link)]
    if not filtered_t0_t1_links:
        print("Error: no link found between t0-t1") 
    return int(len(filtered_t0_t1_links))



def get_links_count_between_t1_t2(region_code,bldg,design,blk,topo_data):
    """
    Counts the number of links between T1 and T2 devices for a specified region, building,
    design, block, and (optionally) tier by analyzing parsed network topology data.

    Args:
        region_code (str): The region identifier.
        bldg (str): Building identifier used in device naming.
        design (str): Design string for pattern matching device names.
        blk (str): Block identifier used in device naming.

    Returns:
        int: The number of links found that connect matching T1 devices to T2 devices via Ethernet.
    """
    block_prefix = "b" if not design.lower().startswith("i") else "su"
    device_pattern = f"{region_code}{bldg}-{design}-{block_prefix}{blk}"
    t2_device_pattern = f"{region_code}{bldg}-{design}"

    # Regex to filter on t1 devices    
    t1_devices = re.compile(rf"^{re.escape(device_pattern)}-t1-r\d+$")
    # Regex for connected_to field (to t2 devices)
    t1_t2_links= re.compile(rf"^{re.escape(t2_device_pattern)}-t2-c\d+-r\d+:Ethernet")
    
    all_links = []
    #data = parse_topo_file_and_convert_into_json(region_code)
    if not topo_data:
        print(f"Failed to parse data from {region_code}.topo file for {region_code}")
    try:
        # Iterate all device keys and find matches
        for device_name in topo_data:
            if t1_devices.match(device_name):
                device = topo_data[device_name]
                # Defensive: check for "ports" in this device's structure
                if device and "ports" in device:
                    for port in device["ports"]:
                        link = port.get("connected_to")
                        if link is not None:
                            all_links.append(link)
    except Exception as e:
        print(f"Error in getting links between t1-t2 due to {e}")
    # filter for t1-t2 links
    filtered_t1_t2_links = [link for link in all_links if t1_t2_links.match(link)]
    if not filtered_t1_t2_links:
        print("Error: no link found between t1-t2") 
    return len(filtered_t1_t2_links)


def get_missing_links_count(region_code, bldg, design, blk): 
    """
    Prints the fabric version and count of missing network links for a specified region, building, design, and block.

    Args:
        region_code (str): Region identifier.
        bldg (str): Building identifier.
        design (str): Design string.
        blk (str): Block identifier.
    """
    try:
        platform_text = collect_platforms(region_code, bldg, blk)
    except Exception as e:
        print(f"Error getting platform informantion: {e}")
        return

    if not platform_text:
        print(f"platform informantion not found for region {region_code}, building {bldg}, and block {blk}")
        return
    
    try:
        fabric_version_info = determine_fabric_version(platform_text)
        fabric_version = fabric_version_info["fabric_version"]
        expected_link_count = fabric_version_info["expected_link_count"]
    except Exception as e:
        print(f"Error determining fabric version: {e}")
        return

    if not fabric_version:
        print(f"Fabric version not found for region {region_code}, building {bldg}, and block {blk}")
        return
    # Parse the topology file once
    topo_data = parse_topo_file_and_convert_into_json(region_code)

    # Handling QFAB fabric versions
    print("\n******** Processing for fabric version and links count in block. ******")
    if fabric_version.lower().startswith('q'):
        expected_links_t0_t1 = expected_link_count
        if expected_links_t0_t1 is None:
            print(f"Expected link count not found for QFAB fabric version {fabric_version}")
            return
        try:
            actual_links_t0_t1 = get_links_count_between_t0_t1(region_code, bldg, design, blk,topo_data)
        except Exception as e:
            print(f"Error getting actual link count: {e}")
            return
        missing_links_between_t0_t1 = expected_links_t0_t1 - actual_links_t0_t1
        print(f'region {region_code}{bldg}, block {blk}, fabric version: {fabric_version}')
        print(f'expected links between t0_t1: {expected_links_t0_t1}')
        print(f'actual links between t0_t1:  {actual_links_t0_t1}')
        print(f'missing links between t0_t1: {missing_links_between_t0_t1}')

    # Handling GFAB fabric versions
    elif fabric_version.lower().startswith('g'):
        expected_links_t0_t1 = expected_link_count['t0_t1']
        expected_links_t1_t2 = expected_link_count['t0_t1']
        if expected_links_t0_t1 is None or expected_links_t1_t2 is None:
            print(f"Expected link count not found for GFAB fabric version {fabric_version}")
            return
        try:
            actual_links_t0_t1 = get_links_count_between_t0_t1(region_code, bldg, design, blk, topo_data)
            actual_links_t1_t2 = get_links_count_between_t1_t2(region_code, bldg, design, blk, topo_data)
        except Exception as e:
            print(f"Error getting actual link count: {e}")
            return
        missing_links_between_t0_t1 = expected_links_t0_t1 - actual_links_t0_t1
        missing_links_between_t1_t2 = expected_links_t1_t2 - actual_links_t1_t2
        print(f'region {region_code}, block {blk}, fabric version: {fabric_version}')
        print(f'expected links between t0_t1: {expected_links_t0_t1}')
        print(f'actual links between t0_t1:  {actual_links_t0_t1}')
        print(f'missing links between t0_t1: {missing_links_between_t0_t1}')

        print(f'\nexpected links between t1_t2: {expected_links_t1_t2}')
        print(f'actual links between t1_t2:  {actual_links_t1_t2}')
        print(f'missing links between t1_t2: {missing_links_between_t1_t2}')

    # Handling IFAB fabric versions
    elif fabric_version.lower().startswith('i'):
        print(f'Fabric version: {fabric_version}, is not supported')
    else:
        print(f"Unsupported fabric version: {fabric_version}")


def main():
    parser = argparse.ArgumentParser(description='Get missing links count')
    parser.add_argument('-r', '--region', required=True, help='Region code (e.g., cwl15)')
    parser.add_argument('-b', '--block', required=True, type=int, help='Block number')
    parser.add_argument('-i', '--design', type=int, default=2, help='Design identifier (default: 2)')

    args = parser.parse_args()

    
    region_code = args.region[:3]
    bldg = args.region[3:]  # Extract building number from region code
    design = f'q{args.design}'  # Convert design identifier to string (e.g., q2)
    blk = args.block

    get_missing_links_count(region_code, bldg, design, blk)

if __name__ == "__main__":
    main()
