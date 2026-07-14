'''
This script takes text input file which has data produced by the following ncpcli commands:

update-device-list --device-names-matching "*" --role qfabt0 --role qfabt1 --role qfabt2 --state deployed --state in-service --state maintenance
devices run-command 'show inventory'  | grep "Entity|^  [0-9][0-9 ]*[ ].[A-Za-z].*[ ].[A-Z0-9\-].*[ ].[A-Z0-9\-].*[ ].[A-Z0-9][A-Z0-9 ].*$"  | grep -v "FINISAR|Accelight|O-NET"

Data Sample:

ncpcli@aga 2024-08-26 10:08:49> devices run-command 'show inventory'  | grep "Entity|^  [0-9][0-9 ]*[ ].[A-Za-z].*[ ].[A-Z0-9\-].*[ ].[A-Z0-9\-].*[ ].[A-Z0-9][A-Z0-9 ].*$"  | grep -v "FINISAR|Accelight|O-NET"
10:09:00 - WARNING [thr=6425997312]- das_client: Url: https://127.0.0.1:51460/v1/devices/aga1-q1-b2-t0-r7/command?name=show+inventory&format=text Response: 502 {"message":"problems reaching with addresses ['10.160.7.83 (OlympusDeviceUnreachable(ConnectionException(\"Socket error during eAPI authentication: HTTPSConnectionPool(host=\\'10.160.7.83\\', port=443): Max retries exceeded with url: /login (Caused by NewConnectionError(\\'<urllib3.connection.HTTPSConnection object at 0x7fa7c8fd6640>: Failed to establish a new connection: [Errno 113] No route to host\\'))\")))']","name":"aga1-q1-b2-t0-r7"}

Entity: aga1-q1-b2-t0-r1
  1    Amphenol         NDAAFF-O103      APE22191038WR9   F
  2    Amphenol         NDAAFF-O103      APE22191038WSK   F
  3    Amphenol         NDAAFF-O103      APE22191038WV9   F
  4    Amphenol         NDAAFG-O106      APE2301106970E   B


Usage:
1: Update file path, you can add 1 or more than one files in the scrip.

# List of file paths to process
file_paths = [
    'lhr_optics_audit.txt',
    'fra_optics_audit.txt',
    'phx_optics_audit.txt',
    'aga_optics_audit.txt',
    'iad_optics_audit.txt',
    'ord_optics_audit.txt',
    'gru_optics_audit.txt',
    'kix_optics_audit.txt',
    'sgu_optics_audit.txt',
    'sjc_optics_audit.txt',
    'syd_optics_audit.txt',
    'vcp_optics_audit.txt'
]

2: Run Script
python3 optics_audit_Excel_report.py

'''

import xlsxwriter
from collections import defaultdict
import re

# Initialize dictionaries to store counts across all files
block_manufacturer_optics_count = defaultdict(lambda: defaultdict(int))
manufacturer_region_optics_count = defaultdict(lambda: defaultdict(int))
manufacturer_optics_count = defaultdict(int)
dc_manufacturer_optics_count = defaultdict(lambda: defaultdict(int))  # New dictionary for aggregated DC data
region_total_optics_count = defaultdict(int)  # Dictionary to store total optics per region
manufacturer_total_optics_count = defaultdict(int)  # Dictionary to store total optics per manufacturer across all regions

# Variable to store the total count across all files
total_optics_count = 0

# Regular expression to check if a word contains a digit
has_numbers = re.compile(r'\d')

def process_file(file_path):
    global total_optics_count
    with open(file_path, 'r') as file:
        current_block = None
        current_dc = None

        for line in file:
            line = line.strip()

            # Skip lines containing "qfabt", "<", ">", "="
            if any(keyword in line for keyword in ["qfabt", "<", ">", "="]):
                continue

            if line.startswith("Entity:"):
                # Check if the Entity line contains "t2-c"
                if "t2-c" in line:
                    current_block = "GFAB"
                    match = re.search(r'Entity: (\w+)', line)
                    current_dc = match.group(1) if match else None
                else:
                    # Extract DC and Block from the Entity line using regex
                    match = re.search(r'Entity: (\w+)-\w+-b(\d+)-', line)
                    if match:
                        current_dc = match.group(1)
                        current_block = match.group(2)
                    else:
                        # If "b" is not present, check for "GFAB"
                        match = re.search(r'Entity: (\w+)', line)
                        current_dc = match.group(1) if match else None
                        current_block = "GFAB" if "t2-c" in line else None
            elif line and current_block and current_dc:
                if "ERROR" not in line:
                    # Extract the first word and check if the second word contains any digits
                    parts = line.split()
                    if len(parts) >= 2:
                        manufacturer = parts[1]
                        if len(parts) > 2 and not has_numbers.search(parts[2]):
                            manufacturer += f" {parts[2]}"  # Only add the second word if it doesn't contain numbers

                        # Update counts
                        block_manufacturer_optics_count[(current_dc, current_block)][manufacturer] += 1
                        manufacturer_region_optics_count[manufacturer][current_dc[:3]] += 1
                        manufacturer_total_optics_count[manufacturer] += 1  # Update total optics per manufacturer across all regions
                        dc_manufacturer_optics_count[current_dc][manufacturer] += 1  # Update DC summary dictionary
                        region_total_optics_count[current_dc[:3]] += 1  # Update total optics per region
                        total_optics_count += 1

# List of file paths to process
file_paths = [
    'lhr_optics_audit.txt',
    'fra_optics_audit.txt',
    'phx_optics_audit.txt',
    'aga_optics_audit.txt',
    'iad_optics_audit.txt',
    'ord_optics_audit.txt',
    'gru_optics_audit.txt',
    'kix_optics_audit.txt',
    'sgu_optics_audit.txt',
    'sjc_optics_audit.txt',
    'syd_optics_audit.txt',
    'vcp_optics_audit.txt'
]

# Process each file
for file_path in file_paths:
    process_file(file_path)

# Write to Excel file
excel_filename = 'optics_audit.xlsx'
workbook = xlsxwriter.Workbook(excel_filename)

# Define a format for headers
header_format = workbook.add_format({'bold': True, 'bg_color': '#D9D9D9', 'border': 1})
total_format = workbook.add_format({'bold': True, 'bg_color': '#C0C0C0', 'border': 1})

### First Tab: Optics by Block ###
optics_by_block_sheet = workbook.add_worksheet('Optics by Block')

# Write headers for Optics by Block
headers = ['Region', 'DC', 'Block', 'Manufacturer', 'Optic_Count']
for col_num, header in enumerate(headers):
    optics_by_block_sheet.write(0, col_num, header, header_format)

row = 1
block_total_sum = 0
for (dc, block), manufacturers in sorted(block_manufacturer_optics_count.items()):
    region = dc[:3]  # Get the first 3 letters for the region
    for manufacturer, count in sorted(manufacturers.items()):
        optics_by_block_sheet.write(row, 0, region)
        optics_by_block_sheet.write(row, 1, dc)
        optics_by_block_sheet.write(row, 2, block)
        optics_by_block_sheet.write(row, 3, manufacturer)
        optics_by_block_sheet.write(row, 4, count)
        block_total_sum += count
        row += 1

# Write total in Optics by Block
optics_by_block_sheet.write(row, 0, 'Total', total_format)
optics_by_block_sheet.write(row, 4, block_total_sum, total_format)

### Second Tab: Optics by DC ###
dc_summary_sheet = workbook.add_worksheet('Optics by DC')

# Write headers for Optics by DC
headers = ['Region', 'DC', 'Manufacturer', 'Optic_Count']
for col_num, header in enumerate(headers):
    dc_summary_sheet.write(0, col_num, header, header_format)

row = 1
dc_total_sum = 0
for dc, manufacturers in sorted(dc_manufacturer_optics_count.items()):
    region = dc[:3]  # Get the first 3 letters for the region
    for manufacturer, count in sorted(manufacturers.items()):
        dc_summary_sheet.write(row, 0, region)
        dc_summary_sheet.write(row, 1, dc)
        dc_summary_sheet.write(row, 2, manufacturer)
        dc_summary_sheet.write(row, 3, count)
        dc_total_sum += count
        row += 1

# Write total in DC Summary
dc_summary_sheet.write(row, 0, 'Total', total_format)
dc_summary_sheet.write(row, 3, dc_total_sum, total_format)

### Third Tab: Optics by Region ###
region_summary_sheet = workbook.add_worksheet('Optics by Region')

# Write headers for Optics by Region - Table 1
headers = ['Region', 'Manufacturer', 'Optic_Count']
for col_num, header in enumerate(headers):
    region_summary_sheet.write(0, col_num, header, header_format)

row = 1
region_table1_sum = 0
for manufacturer, regions in sorted(manufacturer_region_optics_count.items()):
    for region, count in sorted(regions.items()):
        region_summary_sheet.write(row, 0, region)
        region_summary_sheet.write(row, 1, manufacturer)
        region_summary_sheet.write(row, 2, count)
        region_table1_sum += count
        row += 1

# Write total in Region Summary Table 1
region_summary_sheet.write(row, 0, 'Total', total_format)
region_summary_sheet.write(row, 2, region_table1_sum, total_format)

# Write headers for the second table starting from column H
region_summary_sheet.write(0, 7, 'Region', header_format)
region_summary_sheet.write(0, 8, 'Total Optic_Count', header_format)

row = 1
region_table2_sum = 0
for region, total_count in sorted(region_total_optics_count.items()):
    region_summary_sheet.write(row, 7, region)
    region_summary_sheet.write(row, 8, total_count)
    region_table2_sum += total_count
    row += 1

# Write the total at the end of Table 2
region_summary_sheet.write(row, 7, 'Total', total_format)
region_summary_sheet.write(row, 8, region_table2_sum, total_format)

### Fourth Tab: Optics by Manufacturer ###
manufacturer_summary_sheet = workbook.add_worksheet('Optics by Manufacturer')

#### Table 1: Manufacturer, Region, Optic_Count ####
# Write headers for Table 1
manufacturer_summary_sheet.write(0, 0, 'Manufacturer', header_format)
manufacturer_summary_sheet.write(0, 1, 'Region', header_format)
manufacturer_summary_sheet.write(0, 2, 'Optic_Count', header_format)

row = 1
manufacturer_table1_sum = 0
for manufacturer, regions in sorted(manufacturer_region_optics_count.items()):
    for region, count in sorted(regions.items()):
        manufacturer_summary_sheet.write(row, 0, manufacturer)
        manufacturer_summary_sheet.write(row, 1, region)
        manufacturer_summary_sheet.write(row, 2, count)
        manufacturer_table1_sum += count
        row += 1

# Write total in Table 1
manufacturer_summary_sheet.write(row, 0, 'Total', total_format)
manufacturer_summary_sheet.write(row, 2, manufacturer_table1_sum, total_format)

#### Table 2: Manufacturer, Total Optic_Count (Across All Regions) ####
# Write headers for Table 2 starting from column H
manufacturer_summary_sheet.write(0, 7, 'Manufacturer', header_format)
manufacturer_summary_sheet.write(0, 8, 'Optic_Count', header_format)

row = 1
manufacturer_table2_sum = 0
for manufacturer, total_count in sorted(manufacturer_total_optics_count.items()):
    manufacturer_summary_sheet.write(row, 7, manufacturer)
    manufacturer_summary_sheet.write(row, 8, total_count)
    manufacturer_table2_sum += total_count
    row += 1

# Write total in Table 2
manufacturer_summary_sheet.write(row, 7, 'Total', total_format)
manufacturer_summary_sheet.write(row, 8, manufacturer_table2_sum, total_format)

# Close the workbook
workbook.close()

print(f"Excel file created: {excel_filename}")
