import pandas as pd 
import argparse
import os
import sys

def filter_by_gpu_rack(cutsheet_file, gpu_racks,device_name_pattern):
    try: 
        if not os.path.isfile(cutsheet_file):
            print(f"Error: File '{cutsheet_file}' not found.")
            sys.exit(1)

        df = pd.read_excel(cutsheet_file)

        df.columns = df.columns.str.strip()

        filtered_df = df[df['DeviceA Rack'].isin(gpu_racks)]
        
        if filtered_df.empty:
            print(f"No data found for gpu racks: {gpu_racks}")
            sys.exit(0)

        device_df = filtered_df[['DeviceA Rack','DeviceB Name', 'DeviceB Rack']]
        unique_df = device_df.drop_duplicates() # remove dups
        
        
        unique_df = unique_df[unique_df['DeviceB Name'].str.contains(device_name_pattern, regex=True, na=False)]

        print ("\n \033[92m GPU racks <=> Device Mapping:\n \033[0m ")
        renamed_df = unique_df.rename(columns={'DeviceA Rack' : 'GPU Rack Number' ,
                                               'DeviceB Name' : 'Device Hostname' ,
                                                'DeviceB Rack': 'Device Rack Number'})
        print(renamed_df.to_string(index=False))

    except Exception as err:
        print(f"Unexpected Error: {err}")
        sys.exit(1)

def parse_args():
    parser = argparse.ArgumentParser (description="to fetch GPU racks linking with qfab/gfab devices with their rack numbers",
    epilog="Example usage:gpuRack_deviceLinking.py -cutsheet_file ~/autonet/autonet-plans/hsg/hsg3-cables.xlsx -racks 3201,3203")

    parser.add_argument("-cutsheet_file", required=True, help="Path to input cutsheet file e.g; ~/autonet/autonet-plans/hsg/hsg3-cables.xlsx")
    parser.add_argument("-gpu_racks", required=True, help="Comma-separated list of rack names e.g; 3503,3903")
    return parser.parse_args()

def main():
    args = parse_args()

    racks_raw = args.gpu_racks.split(",")
    racks_list = []
        
    for rack in racks_raw:
        cleaned_rack = rack.strip()
        if cleaned_rack:
            racks_list.append(cleaned_rack)
    
    if not racks_list:
        print("Error: No valid GPU racks provided")
        sys.exit(1)
    
    if not args.cutsheet_file.lower().endswith(".xlsx"):
        print("Error: Only .xlsx cutsheet files are supported")
        sys.exit(1)

    device_name_pattern = r'-[iq]\d-'

    filter_by_gpu_rack(args.cutsheet_file, racks_list, device_name_pattern)  


if __name__ == "__main__":
    main()