import re
import sys
from switch_guid_map import switch_guid_map

guid_to_device = {v.lower(): k for k, v in switch_guid_map.items()}

port_mapping = {
    '38': 'IB1/19/2', '47': 'IB1/24/1', '48': 'IB1/24/2', '50': 'IB1/25/2', '57': 'IB1/29/1',
    '58': 'IB1/29/2', '30': 'IB1/15/2', '44': 'IB1/22/2', '56': 'IB1/28/2', '61': 'IB1/31/1',
    '63': 'IB1/32/1', '4': 'IB1/2/2', '24': 'IB1/12/2', '12': 'IB1/6/2', '14': 'IB1/7/2',
    '18': 'IB1/9/2', '34': 'IB1/17/2', '5': 'IB1/3/1', '6': 'IB1/3/2', '13': 'IB1/7/1',
    '28': 'IB1/14/2', '23': 'IB1/12/1', '37': 'IB1/19/1', '41': 'IB1/21/1', '46': 'IB1/23/2',
    '55': 'IB1/28/1', '19': 'IB1/10/1', '16': 'IB1/8/2', '36': 'IB1/18/2', '29': 'IB1/15/1',
    '32': 'IB1/16/2', '42': 'IB1/21/2', '49': 'IB1/25/1', '54': 'IB1/27/2', '64': 'IB1/32/2',
    '22': 'IB1/11/2', '10': 'IB1/5/2', '1': 'IB1/1/1', '31': 'IB1/16/1', '35': 'IB1/18/1',
    '39': 'IB1/20/1', '43': 'IB1/22/1', '62': 'IB1/31/2', '25': 'IB1/13/1', '33': 'IB1/17/1',
    '40': 'IB1/20/2', '45': 'IB1/23/1', '52': 'IB1/26/2', '59': 'IB1/30/1', '8': 'IB1/4/2',
    '26': 'IB1/13/2', '15': 'IB1/8/1', '17': 'IB1/9/1', '11': 'IB1/6/1', '51': 'IB1/26/1',
    '60': 'IB1/30/2', '9': 'IB1/5/1', '21': 'IB1/11/1', '3': 'IB1/2/1', '7': 'IB1/4/1',
    '20': 'IB1/10/2', '27': 'IB1/14/1', '53': 'IB1/27/1', '2': 'IB1/1/2'
}

def replace_guids_and_ports(line: str) -> str:
    matches = re.findall(r'(0x[a-fA-F0-9]+):(\d+)', line)
    
    for guid, port in matches:
        normalized_guid = guid.lower()
        device_name = guid_to_device.get(normalized_guid, normalized_guid)
        port_name = port_mapping.get(port, port)
        original = f"{guid}:{port}"
        replacement = f"{device_name}:{port_name}"
        line = line.replace(original, replacement)

    return line

def main(input_path: str):
    with open(input_path, "r") as f:
        for line in f:
            print(replace_guids_and_ports(line.strip()))

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python translate_links.py <input_file>")
    else:
        main(sys.argv[1])
