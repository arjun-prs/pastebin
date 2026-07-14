from typing import Dict, List
from dataclasses import dataclass
import re
import json
import sqlite3
import sys
import argparse

def create_cache(fn):
    # The idea here is to map a GUID/port combo to a hostname. 
    # If you pass in a GUID,port combo, you'll recieve a hostname. 
    # This will require some enrichment before the cache is queried. 
    # For instance, in the topospec output, we have a logical numer representing
    # the port, we'll need to convert it before querying the cache.
    result = {}

    with open(fn, 'r', newline='') as fh:
        lns = fh.read().splitlines()
        for ln in lns:
            # throw away the first line
            if "StartDevice" in ln or "HCA" in ln:
                continue
            fields = ln.split(",")
            if len(fields) != 17:
                # We're missing some fields
                continue
            a_side_hostname = fields[0]
            a_side_port = fields[6]
            a_side_guid = fields[-2]
            z_side_hostname = fields[7]
            z_side_port = fields[8]
            z_side_guid = fields[-1]
            # map guid,port as key
            result[f"{a_side_guid},{a_side_port}"] = a_side_hostname
            result[f"{z_side_guid},{z_side_port}"] = z_side_hostname

    return result


dev_info_fn = 'dev_info.csv' # TODO: this file needs to be updated as updates to the plan occur

class RackElevationDB:
    def __init__(self, db_file):
        try:
            conn = sqlite3.connect(db_file)
        except Exception as e:
            print(f"failed to load DB file: {e}")
        self.conn = conn

    def get_rack_elevations_by_su(self, hostname: str, intf: str) -> str:
        query = """ --fetch one
        select 
            rack, 
            elevation 
        from device_info 
        where device = ? and interface = ?;
        """
        cur = self.conn.cursor()
        cur.execute(query, (hostname, intf))
        res = cur.fetchone()
        if not res:
            return "no rows"
        rack, ele = res
        return f"{rack}:{ele}"
    
    def close(self):
        self.conn.close()

# port_mapping maps the logical port the UFM and fae commands use to a physical port that uses the IBx/y/z nomenclature. Very handy indeed. 
port_mapping = {'38': 'IB1/19/2', '47': 'IB1/24/1', '48': 'IB1/24/2', '50': 'IB1/25/2', '57': 'IB1/29/1', '58': 'IB1/29/2', '30': 'IB1/15/2', '44': 'IB1/22/2', '56': 'IB1/28/2', '61': 'IB1/31/1', '63': 'IB1/32/1', '4': 'IB1/2/2', '24': 'IB1/12/2', '12': 'IB1/6/2', '14': 'IB1/7/2', '18': 'IB1/9/2', '34': 'IB1/17/2', '5': 'IB1/3/1', '6': 'IB1/3/2', '13': 'IB1/7/1', '28': 'IB1/14/2', '23': 'IB1/12/1', '37': 'IB1/19/1', '41': 'IB1/21/1', '46': 'IB1/23/2', '55': 'IB1/28/1', '19': 'IB1/10/1', '16': 'IB1/8/2', '36': 'IB1/18/2', '29': 'IB1/15/1', '32': 'IB1/16/2', '42': 'IB1/21/2', '49': 'IB1/25/1', '54': 'IB1/27/2', '64': 'IB1/32/2', '22': 'IB1/11/2', '10': 'IB1/5/2', '1': 'IB1/1/1', '31': 'IB1/16/1', '35': 'IB1/18/1', '39': 'IB1/20/1', '43': 'IB1/22/1', '62': 'IB1/31/2', '25': 'IB1/13/1', '33': 'IB1/17/1', '40': 'IB1/20/2', '45': 'IB1/23/1', '52': 'IB1/26/2', '59': 'IB1/30/1', '8': 'IB1/4/2', '26': 'IB1/13/2', '15': 'IB1/8/1', '17': 'IB1/9/1', '11': 'IB1/6/1', '51': 'IB1/26/1', '60': 'IB1/30/2', '9': 'IB1/5/1', '21': 'IB1/11/1', '3': 'IB1/2/1', '7': 'IB1/4/1', '20': 'IB1/10/2', '27': 'IB1/14/1', '53': 'IB1/27/1', '2': 'IB1/1/2'}

# This is mock output we used for testing. 
output =  {'Changed Links': {'1': {'Changed Link': "GUID 0xb0cf0e0300d74440 (MF0;hsg3-i1-su1-t0-r17:MQM9700/U1) Port 35 to GUID 0xb0cf0e0300d34500 (MF0;hsg3-i1-su1-t1-r3:MQM9700/U1) Port 17 peer changed to GUID 0xb0cf0e0300d382c0 (MF0;hsg3-i1-su1-t1-r4:MQM9700/U1) Port 17"}, '2': {'Changed Link': "GUID 0xb0cf0e0300d74440 (MF0;hsg3-i1-su1-t0-r17:MQM9700/U1) Port 36 to GUID 0xb0cf0e0300d382c0 (MF0;hsg3-i1-su1-t1-r4:MQM9700/U1) Port 17 peer changed to GUID 0xb0cf0e0300d34500 (MF0;hsg3-i1-su1-t1-r3:MQM9700/U1) Port 17"}, '3': {'Changed Link': "GUID 0xb0cf0e0300d34500 (MF0;hsg3-i1-su1-t1-r3:MQM9700/U1) Port 17 to GUID 0xb0cf0e0300d74440 (MF0;hsg3-i1-su1-t0-r17:MQM9700/U1) Port 35 peer changed to GUID 0xb0cf0e0300d74440 (MF0;hsg3-i1-su1-t0-r17:MQM9700/U1) Port 36"}, '4': {'Changed Link': "GUID 0xb0cf0e0300d382c0 (MF0;hsg3-i1-su1-t1-r4:MQM9700/U1) Port 17 to GUID 0xb0cf0e0300d74440 (MF0;hsg3-i1-su1-t0-r17:MQM9700/U1) Port 36 peer changed to GUID 0xb0cf0e0300d74440 (MF0;hsg3-i1-su1-t0-r17:MQM9700/U1) Port 35"}}}

@dataclass
class MiscabledLink:
    """MiscabledLink represents the various fields that both the UFM topo api produces and that the DCO folk expect."""
    a_side_hostname_expected: str
    a_side_port_expected: str
    # a_side_rack_elevations_expected: Optional[str] = None # TODO: figure out why the static typing with defaults isn't working
    z_side_hostname_expected: str
    z_side_port_expected: str
    z_side_hostname_current: str
    z_side_port_current: str
    # z_side_rack_elevations_expected: Optional[str] = None # TODO: figure out why the static typing with defaults isn't working


def strip_ansi_sequences(text):
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

def enrich(miscabled_link: MiscabledLink, su: str) -> MiscabledLink:
    region = miscabled_link.a_side_hostname_expected[:4]
    # Check DB for SU rack elvations
    db = RackElevationDB(f"data/hsg/device_info_su{su}.db") # TODO: this is brittle; need to make this a variable and not hard coded. 
    a_side_rack_elevations = db.get_rack_elevations_by_su(miscabled_link.a_side_hostname_expected, miscabled_link.a_side_port_expected)
    # if no rows were returned, we don't (at the moment) care about this device. 
    if a_side_rack_elevations == "no rows":
        return None
    miscabled_link.a_side_rack_elevations_expected = f"{region}:{a_side_rack_elevations}"
    z_side_rack_elevations = db.get_rack_elevations_by_su(miscabled_link.z_side_hostname_expected, miscabled_link.z_side_port_expected)
    miscabled_link.z_side_rack_elevations_expected = f"{region}:{z_side_rack_elevations}"
    z_side_curren_rack_elevations = db.get_rack_elevations_by_su(miscabled_link.z_side_hostname_current, miscabled_link.z_side_port_current)
    miscabled_link.z_side_rack_elevations_current = f"{region}:{z_side_curren_rack_elevations}"
    db.close()
    return miscabled_link

def extract_guid_and_port(s):
    # GUID 0xe09d7303001df260 (Unknown) Port 1 

    # Regex to match "GUID <guid> (Unknown) Port <port>"
    # Check if both keywords exist
    if "GUID" in s and "(Unknown)" in s:
        match = re.search(r'GUID\s+(0x[^\s]+)\s+\(Unknown\)\s+Port\s+([^\s]+)', s)
        if match:
            guid = match.group(1)
            port = match.group(2)
            # translate logical port to physical port 
            port = port_mapping[port]
            return guid[2:].upper() if guid.startswith("0x") else guid.upper(), port
    return None, None

def replace_unknown_guid(s, new_value):
    # Replace (Unknown) with (new_value) only when it follows a GUID pattern
    return re.sub(r'(GUID\s+0x[^\s]+\s*)\(Unknown\)', rf'\1({new_value})', s)

def extract(cache: Dict[str, str], input: str) -> MiscabledLink:
    # @dhapatil you, sir, a wizard! Thank you for the insanely complex regex! 
    # {'Changed Link': 'GUID 0xb0cf0e0300d46600 (MF0;hsg3-i1-su1-t0-r1:MQM9700/U1) Port 2 to GUID 0xe09d73030025f5da (bio-2510xng04m HCA-1) Port 1 peer changed to GUID 0xe09d73030025f5de (bio-2510xng04m HCA-2) Port 1'}
    # pattern = r"GUID\s+\S+\s+\((?P<deviceA>[^)]+):[^)]+\)\s+Port\s+(\d+)\s+to\s+GUID\s+\S+\s+\((?P<expectedZ>[^)]+):[^)]+\)\s+Port\s+(\d+)\s+peer\s+changed\s+to\s+GUID\s+\S+\s+\((?P<actualZ>[^)]+):[^)]+\)\s+Port\s+(\d+)"

    # Look for Unknowns, extract the GUID and populate a valid hostname
    # if "(Unknown)" in input:
    #     guid, port = extract_guid_and_port(input)
    #     try:
    #         hostname = cache[f"{guid},{port}"]
    #     # replace Unknown with hostname
    #         input = replace_unknown_guid(input, hostname)
    #     except KeyError:
    #         print(f"{guid},{port} not in cache")

    pattern = r"GUID\s+\S+\s+\((?:MF0;)?([^)]+?)(?::[^)]+)?\)\s+Port\s+(\d+)\s+to\s+GUID\s+\S+\s+\((?:MF0;)?([^)]+?)(?::[^)]+)?\)\s+Port\s+(\d+)\s+peer\s+changed\s+to\s+GUID\s+\S+\s+\((?:MF0;)?([^)]+?)(?::[^)]+)?\)\s+Port\s+(\d+)"
    matches = re.findall(pattern, input)
    if matches:
        try:
            miscabled_link = MiscabledLink(
                a_side_hostname_expected=matches[0][0],
                a_side_port_expected=port_mapping[matches[0][1]],
                z_side_hostname_expected=matches[0][2],
                z_side_port_expected=port_mapping[matches[0][3]],
                z_side_hostname_current=matches[0][4],
                z_side_port_current=port_mapping[matches[0][5]],
            )
            # print("#" * 50)
            # print(f"{input}\n{miscabled_link.a_side_hostname_expected} | {miscabled_link.z_side_hostname_current}")
            # print(f"the match: {matches[0][0]}")
            # print("#" * 50)
        except IndexError as e:
            pass
            # print(f"split failed: {e}; {matches[0]}")
    else: 
        print("there is a problem with the regex")
        print(input)
        return

    return miscabled_link

def parse(cache: Dict[str, str], output: Dict[str, Dict[str, str]], su: str) -> List[MiscabledLink]:
    links = list()
    try:
        miscabled_links = output['Changed Links']
    except KeyError:
        print("there are no miscabled links")
        return None
    for _, v in miscabled_links.items():
        raw = v['Changed Link']
        # ignore HCA (host channel adapters); this is compute team's responsiblity 
        if "HCA-" in raw:
            continue
        # get rid of bio
        if "'bio" in raw:
            continue
        # extract the relevant bits from the raw output. 
        miscabled_link = extract(cache, raw)
        # if we couldn't get the relevant bits, there may be a flaw in our logic
        if not miscabled_link:
            print("there was a problem with extraction")
            # keep going through the list
            continue
        miscabled_link = enrich(miscabled_link=miscabled_link, su=su)
        if miscabled_link == None:
            continue
        links.append(miscabled_link)
    return links


def get_removed_links(output: Dict[str, Dict[str, str]]) -> List[str]:
    removed = []
    try:
        removed_links = output["Removed Links"]
    except KeyError:
        print("there are no removed links")
        return removed
    for _, v in removed_links.items():
        raw = v.get("Removed Link")
        if not raw or "HCA-" in raw or "'bio" in raw:
            continue
        removed.append(raw)
    return removed


def transform(links: List[MiscabledLink]) -> None:
    """
    transform takes the list of miscabled links and transforms the TopoSpec API output into the format requested by the DCO. 
    TODO: it might make sense to add logic that can write to stdout and to a file (if specified)
    """
    for l in links:
        print(f"please swap/fix cable connected to:  {l.a_side_hostname_expected}:{l.a_side_port_expected}:{l.a_side_rack_elevations_expected} <==> {{'{l.z_side_hostname_current}': '{l.z_side_port_current}'}}:{l.z_side_rack_elevations_current}") # TODO: add rack info 
        print(f"\nTO\n\n{l.a_side_hostname_expected}:{l.a_side_port_expected}:{l.a_side_rack_elevations_expected} <==> {{'{l.z_side_hostname_expected}': '{l.z_side_port_expected}'}}:{l.z_side_rack_elevations_expected}")
        print()
    return None


def transform_removed(removed: List[str]) -> None:
    for raw in removed:
        print(f"[REMOVED LINK] {raw}")
    print()


def generate_summary_of_top_level_keys(results: Dict[str, Dict]) -> None:
    print("\nSummary of Report:")
    print("-----------------------------------")
    added_links = results.get("Added Links", [])
    removed_links = results.get("Removed Links", [])
    changed_links = results.get("Changed Links", [])
    added_links_count  = len(added_links)
    removed_links_count = len(removed_links)
    changed_links_count = len(changed_links)
    print(f"Added Links count\t = {added_links_count}")
    print(f"Removed Links count\t = {removed_links_count}")
    print(f"Changed Links count\t = {changed_links_count}")
    print("\n\n") # provide some space between summary of report and rest of data

if __name__ == "__main__":
    dev_cache = create_cache(dev_info_fn)
    parser = argparse.ArgumentParser()
    parser.add_argument("--jsonfile", type=str, required=True, help="API output from curling on the UFM container on the host (e.g. topospec_api_output.json)")
    parser.add_argument("--linktype", choices=["changed", "removed", "both"], default="changed", help="Which type of link issues to print")
    parser.add_argument("--su", default="1", help="Which type su are you auditing?")
    args = parser.parse_args()


    with open(args.jsonfile, "r") as f:
        data = json.load(f)

    generate_summary_of_top_level_keys(data)

    changed_links = []
    removed_links = []

    if args.linktype in ("changed", "both"):
        changed_links = parse(dev_cache, data, args.su)
        if changed_links:
            transform(changed_links)

    if args.linktype in ("removed", "both"):
        removed_links = get_removed_links(data)
        if removed_links:
            transform_removed(removed_links)

    if (args.linktype == "changed" and not changed_links) or \
       (args.linktype == "removed" and not removed_links) or \
       (args.linktype == "both" and not (changed_links or removed_links)):
        sys.exit(41)
