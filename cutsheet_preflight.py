'''
Comprehensive tool for calculating and validating hardware configurations for a specific data center setup. 
It involves multiple steps, including configuration loading, user input, calculations, validation, and data reordering.
Usage: 
python cutsheet_preflight.py
Select Server Type:
1. B200
2. B300
3. GB200
4. GB300
Enter the number of your choice: 2

Select Fabric (main QFAB) Type:
1. QFAB3.0
2. QFAB3.0_ONOS
3. Multi-Planar Spectrum 8K GPU
4. Multi-Planar Spectrum 16K GPU
5. Multi-Planar Spectrum 32K GPU
Enter the number of your choice: 3

Select CFAB column configuration:
1. cfab_8_column
2. cfab_16_column
Enter the number of your choice: 2
Please Enter Required GPU Node/Racks : 511

************ Platform Required to Support  511 B300 nodes/racks in Fabric Type "Multi-Planar Spectrum 8K GPU" ************
Fabric Type: Multi-Planar Spectrum 8K GPU
   GPU Platform Details:
     Required GPU Platform: GPU_V5_X11_B300_R.03 (Count: 511)
     GPUs per node: 8
     Total GPUs offered: 4088
   Network Details (Main QFAB):
     T1 Platform: net.ad_spc4_planar_qfab_t1_1.01 (Count: 16)
     T0 Platform: net.ad_spc4_planar_qfab_t0_1.01 (Count: 16)
     IPR Platform: net.ad_spc4_planar_qfab_ipr_1.01 (Count: 1)
   Network Details (CFAB):
     T1 Platforms:
       - net.ad_cfab_v2_t1_t2_1.11 (Count: 2)
       - net.ad_cfab_v2_t1_t2_2.11 (Count: 2)
     T0 Platform: net.ad_cfab_t0_1.05 (Count: 8)

********************************************************************************
Do you want to validate required hardware vs location file from atlas? (y/n): y
Enter the path to the location CSV file: iad47.1.csv

********* performing validation for required vs available hardware ***********
GPU_V5_X11_B300_R.03 (Count: 511): passed ✅
net.ad_spc4_planar_qfab_t1_1.01 (Count: 16): passed ✅
net.ad_spc4_planar_qfab_t0_1.01 (Count: 16): passed ✅
net.ad_spc4_planar_qfab_ipr_1.01 (Count: 1): passed ✅
net.ad_cfab_v2_t1_t2_1.11 (Count: 2): passed ✅
net.ad_cfab_v2_t1_t2_2.11 (Count: 2): passed ✅
net.ad_cfab_t0_1.05 (Count: 8): passed ✅
net.ad_cfab_v2_t3_1.02 (Count: 16): missing platform in BOM page
net.oad_metro_core_zr_4.01 (Count: 8): missing platform in BOM page
net.ad_cfab_v2_nt1_nt2_1.03 (Count: 4): missing platform in BOM page
net.ad_cfab_v2_nt1_nt2_2.03 (Count: 4): missing platform in BOM page
net.ad_cfab_v2_t1_t2_1.04 (Count: 2): missing platform in BOM page
net.ad_cfab_v2_t1_t2_2.04 (Count: 2): missing platform in BOM page
aux.01 (Count: 1): missing platform in BOM page
aux.02 (Count: 1): missing platform in BOM page

********** Performing validation for Column [PLACEMENT_GROUP] ************
placement group 1: passed ✅
placement group 3: passed ✅
placement group 5: passed ✅
placement group 7: failed ❌ Failure reason: GPU_V5_X11_B300_R.03: Expected - 128, Available - 127
placement group 151: passed ✅
placement group 152: passed ✅
placement group 153: passed ✅
placement group 154: passed ✅
placement group 201: passed ✅
placement group is not in sequence. ❌

********** Performing validation for Column [CFAB_FABRIC_BLOCK] ************
cfab block 1: passed ✅
cfab block 7: passed ✅
cfab block 9: failed ❌ Failure reason: GPU_V5_X11_B300_R.03: Expected - 256, Available - 255

********** Performing validation for Column [QFAB_INSTANCE_ID] ************
cfab racks instance id 1: passed ✅
qfab racks instance id 2: passed ✅

********** Performing validation for Column [BLOCK_NAME] and [CFAB_FABRIC_BLOCK] ************
cfab block 1: passed ✅
cfab block 3: passed ✅
cfab block 5: passed ✅
cfab block 7: passed ✅
cfab block 9: passed ✅
'''

import json
import math
import sys
import pandas as pd
from rich import print
import os

# === CONFIGURABLE CONSTANTS ===
# Define column names
PLATFORM_COLUMN = 'PLATFORM'
PLACEMENT_GROUP_COLUMN = 'PLACEMENT_GROUP'
CFAB_BLOCK_COLUMN = 'CFAB_FABRIC_BLOCK'
QFAB_INSTANCE_ID = 'QFAB_INSTANCE_ID'
BLOCK_NAME = 'BLOCK_NAME'

def load_config(file_path: str) -> dict:
    try:
        with open(file_path) as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Config file '{file_path}' not found. Exiting.")
        sys.exit()

def get_user_input(prompt: str, options: list) -> str:
    print(prompt)
    for idx, opt in enumerate(options, 1):
        print(f"{idx}. {opt}")
    while True:
        try:
            choice = int(input("Enter the number of your choice: "))
            if 1 <= choice <= len(options):
                return options[choice-1]
            else:
                print(f"Please select a valid option (1-{len(options)}).")
        except (ValueError, KeyboardInterrupt):
            print("Invalid input or interrupted. Please enter a number.")
            sys.exit()

def calculate_qfab_platforms(required_servers: int, server_info: dict, qfab_conf: dict, fabric_type: str, server_type: str) -> dict:
    #gpu_platform, rail_group = get_server_info(qfab_conf, fabric_type, server_type)
    gpu_platform = server_info['platform']
    gpu_per_node = server_info['gpu_per_node']
    total_gpus = required_servers * gpu_per_node

    if fabric_type.startswith("Multi-Planar Spectrum") and server_type == "B300":
        t0_base = 128    #Number of GPU racks supported in a group of T0 racks
        t1_base = 1024   #Number of GPU racks supported in a block
    elif fabric_type.startswith("Multi-Planar Spectrum") and server_type in ("GB300", "GB200"):
        t0_base = 7
        t1_base = 1024
    elif fabric_type in ("QFAB3.0", "QFAB3.0_ONOS") and server_type == "B300":
        t0_base = 64
        t1_base = 512
    elif fabric_type in ("RTX6k/B40 QFAB3.0", "RTX6k/B40 QFAB3.0_ONOS") and server_type == "RTX6k/B40":
        t0_base = 21
        t1_base = 336
    elif fabric_type in ("B200 QFAB3.0", "B200 QFAB3.0_ONOS") and server_type == "B200":
        t0_base = 64
        t1_base = 1024
    elif fabric_type in ("B200 QFAB2.0",) and server_type == "B200":
        t0_base = 64
        t1_base = 128
    elif fabric_type in ("B200 QFAB2.1",) and server_type == "B200":
        t0_base = 256
        t1_base = 256
    elif fabric_type.startswith("GFAB6.0") and server_type in ("GB300", "GB200"):
        t0_base = 12
        t1_base = 12
    else:
        print(f"Unsupported configuration for QFAB calculation for {server_type} in fabric {fabric_type}.\nRefer to the BOM page for supported configurations.")
        sys.exit()

    if fabric_type.startswith("Multi-Planar Spectrum") and server_type in ("GB300", "GB200"):
        qfab_conf['t0_platform_required'] = 2  

    t1_groups = math.ceil(required_servers / t1_base)
    t1_total = t1_groups * qfab_conf['t1_platform_required']
    
    t0_groups = math.ceil(required_servers / t0_base)
    t0_total = t0_groups * qfab_conf['t0_platform_required']

    t1_platform = qfab_conf.get('t1_platform')
    t1_platforms = qfab_conf.get('t1_platforms')
    t0_platform = qfab_conf.get('t0_platform')

    group_size = {}
    if t1_platforms:  
        for t1_platform in t1_platforms:
            group_size[t1_platform] = qfab_conf['t1_platform_required']

    elif t1_platform: 
        group_size[t1_platform] = qfab_conf['t1_platform_required']
        
    group_size[t0_platform] = qfab_conf['t0_platform_required']
    
    if fabric_type.startswith("Multi-Planar Spectrum"):
        group_size[qfab_conf.get('ipr_platform')] = qfab_conf['ipr_platform_required']
    elif fabric_type.startswith("GFAB6.0"):
        group_size[qfab_conf.get('t2_platform')] = qfab_conf['t2_platform_required']

    #server_models_supported = qfab_conf.get('server_models_supported', [])
    rail_group = server_info['rail_group']
    group_size[gpu_platform] = rail_group

    return {
        'gpu_platform': gpu_platform,
        'gpu_per_node': gpu_per_node,
        'total_gpus': total_gpus,
        't0_count': t0_total,
        't1_count': t1_total,
        't1_platform': t1_platform,
        't1_platforms': t1_platforms,
        't0_platform': t0_platform,
        **({
            'ipr_count': qfab_conf['ipr_platform_required'],
            'ipr_platform': qfab_conf.get('ipr_platform')
        } if fabric_type.startswith("Multi-Planar Spectrum") else {}),
        **({
            't2_count': qfab_conf['t2_platform_required'],
            't2_platform': qfab_conf.get('t2_platform')
        } if fabric_type.startswith("GFAB6.0") else {}), 
        'group_size': group_size
    }

def calculate_cfab_platforms(required_servers: int, server_type: str, cfab_conf: dict, cfab_column: str) -> dict:
    if server_type in ("B300", "B200"):
        t0_base = 128
        t1_base = 128
    elif server_type in ("RTX6k/B40"):
        t1_base = 64
        t0_base = 128
    elif server_type in ("GB300", "GB200"):
        t0_base = 16
        t1_base = 16
    else:
        print(f"Unsupported server type {server_type} for CFAB.")
        sys.exit()

    if cfab_column == "cfab_16_column" and server_type in ("B300", "B200"):
        t1_base = 256

    t0_groups = math.ceil(required_servers / t0_base)
    t0_total = t0_groups * cfab_conf['t0_platform_required']
    t1_groups = math.ceil(required_servers / t1_base)
    t1_total = t1_groups * cfab_conf['t1_platform_required']

    t1_platform = cfab_conf.get('t1_platform')
    t1_platforms = cfab_conf.get('t1_platforms')
    t0_platform = cfab_conf.get('t0_platform')

    group_size = {}
    if t1_platforms:  
        for t1_platform in t1_platforms:
            group_size[t1_platform] = cfab_conf['t1_platform_required']
    elif t1_platform: 
        group_size[t1_platform] = cfab_conf['t1_platform_required']
    group_size[t0_platform] = cfab_conf['t0_platform_required']-1

    return {
        't0_count': t0_total,
        't1_count': t1_total,
        't1_platform': t1_platform,
        't1_platforms': t1_platforms,
        't0_platform': t0_platform,
        'group_size': group_size
    }

def print_hw_requirements(result_qfab: dict, result_cfab: dict, required_servers: int, server_type: str, fabric_type: str):
    print(f'\n************ Platform Required to Support [green] {required_servers} {server_type} nodes/racks in Fabric Type "{fabric_type}[/green]" ************')
    print(f"Fabric Type: {fabric_type}")
    #print("   GPU Platform Details:")
    print("   [green]GPU Platform Details:[/green]")
    print(f"     Required GPU Platform: {result_qfab['gpu_platform']} (Count: {required_servers})")
    print(f"     GPUs per node: {result_qfab['gpu_per_node']}")
    print(f"     Total GPUs offered: {result_qfab['total_gpus']}")
    print(f"   [yellow]Network Details (Main QFAB):[/yellow]")
    if result_qfab.get('t2_platform'):
        print(f"     T2 Platform: {result_qfab['t2_platform']} (Count: {result_qfab['t2_count']})")
    if result_qfab.get('t1_platforms'):
        print(f"     T1 Platforms:")
        for platform in result_qfab['t1_platforms']:
            print(f"       - {platform} (Count: {result_qfab['t1_count']})")
    elif result_qfab.get('t1_platform'):
        print(f"     T1 Platform: {result_qfab['t1_platform']} (Count: {result_qfab['t1_count']})")
    print(f"     T0 Platform: {result_qfab['t0_platform']} (Count: {result_qfab['t0_count']})")
    if result_qfab.get('ipr_platform'):
        print(f"     IPR Platform: {result_qfab['ipr_platform']} (Count: {result_qfab['ipr_count']})")

    print(f"   [cyan]Network Details (CFAB):[/cyan]")
    if result_cfab.get('t1_platforms'):
        print(f"     T1 Platforms:")
        for platform in result_cfab['t1_platforms']:
            print(f"       - {platform} (Count: {result_cfab['t1_count']})")
    elif result_cfab.get('t1_platform'):
        print(f"     T1 Platform: {result_cfab['t1_platform']} (Count: {result_cfab['t1_count']})")
    # Only print CFAB T0 when allowed
    if server_type not in ["RTX6k/B40"]:
        print(f"     T0 Platform: {result_cfab['t0_platform']} (Count: {result_cfab['t0_count']})")


def build_hw_required_info_dict(result_qfab: dict, result_cfab: dict, required_servers: int, server_type) -> dict:
    hw_required_info = {}
    hw_required_info[f"{result_qfab['gpu_platform']}"] = required_servers

    if result_qfab.get('t1_platforms'):
        for platform in result_qfab['t1_platforms']:
            hw_required_info[f"{platform}"] = result_qfab['t1_count']
    elif result_qfab.get('t1_platform'):
        hw_required_info[f"{result_qfab['t1_platform']}"] = result_qfab['t1_count']

    hw_required_info[f"{result_qfab['t0_platform']}"] = result_qfab['t0_count']

    if result_qfab.get('ipr_platform'):
        hw_required_info[f"{result_qfab['ipr_platform']}"] = result_qfab['ipr_count']

    if result_qfab.get('t2_platform'):
        hw_required_info[f"{result_qfab['t2_platform']}"] = result_qfab['t2_count']

    if result_cfab.get('t1_platforms'):
        for platform in result_cfab['t1_platforms']:
            hw_required_info[f"{platform}"] = result_cfab['t1_count']
    elif result_cfab.get('t1_platform'):
        hw_required_info[f"{result_cfab['t1_platform']}"] = result_cfab['t1_count']
    # Only add CFAB T0 when allowed
    if server_type not in ["RTX6k/B40"]:
        hw_required_info[f"{result_cfab['t0_platform']}"] = result_cfab['t0_count']

    return hw_required_info

def load_and_filter_data(file_path: str, platforms: list) -> pd.DataFrame:
    try:
        df = pd.read_csv(file_path)
        df = df[df['PLATFORM'].notna()]
        df = df[df['PLATFORM'].str.strip().isin(platforms)]
        return df
    except Exception as e:
        print(f"Failed to load or filter data: {e}")
        return None

def sort_and_reorder_data(df: pd.DataFrame, group_size: dict, rack_sorting_order: list) -> pd.DataFrame:
    platform_batches = {}
    for platform in rack_sorting_order:
        if platform in group_size:
            platform_batches[platform] = (
                df[df['PLATFORM'] == platform]
                .sort_values('RACK_NUMBER')
                .copy()
            )

    num_groups = max(
        (len(platform_batches[p]) + group_size[p] - 1) // group_size[p]  # ceil division
        for p in rack_sorting_order
        if p in platform_batches
    ) if rack_sorting_order else 0
    
    grouped_rows, used_idx = [], set()
    
    for group_i in range(num_groups):
        for platform in rack_sorting_order:
            if platform not in platform_batches:
                continue
            batch_size = group_size[platform]
            start = group_i * batch_size
            end = (group_i + 1) * batch_size
    
            # Only add if there is data left for this platform in this group
            if start >= len(platform_batches[platform]):
                continue
            
            batch = platform_batches[platform].iloc[start:end]
            grouped_rows.extend(batch.to_dict("records"))
            used_idx.update(batch.index)    
    
    leftover_rows = []
    for platform in rack_sorting_order:
        if platform in platform_batches:
            leftover = platform_batches[platform].loc[~platform_batches[platform].index.isin(used_idx)]
            leftover_rows.extend(leftover.to_dict('records'))
    grouped_rows += leftover_rows

    df_final = pd.DataFrame(grouped_rows).reset_index(drop=True)
    if "RACK_SORT_ORDER" in df_final.columns:
        df_final = df_final.drop(columns=["RACK_SORT_ORDER"])
    if "RACK_NUMBER" in df_final.columns:
        rack_idx = list(df_final.columns).index('RACK_NUMBER')
        df_final.insert(rack_idx + 1, 'RACK_SORT_ORDER', range(1, len(df_final) + 1))
    else:
        df_final['RACK_SORT_ORDER'] = range(1, len(df_final) + 1)
    return df_final

def check_for_core_network_data_hall(result_qfab, platform_info):
    return any(result_qfab['t1_platform'] in platform for platform in platform_info)

def check_gpu_availability(result_qfab, platform_info):
    """Check if GPU platform is available and prompt user if not"""
    gpu_platform = result_qfab.get('gpu_platform')
    if gpu_platform and not any(gpu_platform.lower() in platform.lower() for platform in platform_info):
        print(f"\n[red]GPU platform '{gpu_platform}' not found in location file. Is this expected? (y/n): [/red]", end='')
        user_input = input()
        if user_input.lower() != 'y':
            print("Exiting without validation.")
            sys.exit()
        return False
    return True


def location_file_reader(input_file, 
                           platform_column=PLATFORM_COLUMN, 
                           placement_group_column=PLACEMENT_GROUP_COLUMN, 
                           cfab_block_column=CFAB_BLOCK_COLUMN, 
                           instance_id=QFAB_INSTANCE_ID, 
                           block_name=BLOCK_NAME):
    try:
        # Load the CSV file 
        df = pd.read_csv(input_file, dtype= str)

        # convert columns to string and remove trailing .0
        columns_to_convert = [placement_group_column, cfab_block_column, instance_id, block_name]
        for column in columns_to_convert:
            df[column] = df[column].astype(str).str.replace('.0', '')

        # Analyze placement groups, cfab blocks, and qfab instance ids
        pg_platform_dict = count_platforms_by_group(df, placement_group_column, platform_column)
        cb_platform_dict = count_platforms_by_group(df, cfab_block_column, platform_column)
        qi_platform_dict = count_platforms_by_group(df, instance_id, platform_column)

        # Analyze block names
        bn_cb_platform_dict = get_block_name_and_cfab_block(df, block_name, cfab_block_column, platform_column)

        return pg_platform_dict, cb_platform_dict, qi_platform_dict, bn_cb_platform_dict

    except FileNotFoundError:
        print(f"File {input_file} not found.")
        return None
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        return None


def count_platforms_by_group(df, group_column, platform_column):
    group_platform_counts = df.groupby([group_column, platform_column]).size().reset_index(name='count')
    group_platform_dict = {}
    for group in sorted(group_platform_counts[group_column].unique(), key=lambda x: (not str(x).isdigit(), int(x) if str(x).isdigit() else x)):
        group_platform_dict[group] = group_platform_counts[group_platform_counts[group_column] == group].set_index(platform_column)['count'].to_dict()
    return group_platform_dict


def get_block_name_and_cfab_block(df, block_name, cfab_block_column, platform_column):
    bn_cb_platform_counts = df.groupby([block_name, cfab_block_column, platform_column]).size().reset_index(name='count')
    bn_cb_platform_dict = {}
    for bn in sorted(bn_cb_platform_counts[block_name].unique(), key=lambda x: (not str(x).isdigit(), int(x) if str(x).isdigit() else x)):
        bn_cb_platform_dict[bn] = {}
        for cb in sorted(bn_cb_platform_counts[bn_cb_platform_counts[block_name] == bn][cfab_block_column].unique(), key=lambda x: (not str(x).isdigit(), int(x) if str(x).isdigit() else x)):
            platforms = ', '.join(bn_cb_platform_counts[(bn_cb_platform_counts[block_name] == bn) & (bn_cb_platform_counts[cfab_block_column] == cb)][platform_column].tolist())
            bn_cb_platform_dict[bn][cb] = platforms
    return bn_cb_platform_dict

def validate_placement_groups(input_file,server_type, fabric_type, result_qfab, result_cfab):
    #pg_platform_dict = location_file_reader(input_file)
    pg_platform_dict, cb_platform_dict, qi_platform_dict, bn_cb_platform_dict = location_file_reader(input_file)
    placement_group_validation = {}

    for pg_str, platform_details in pg_platform_dict.items():
        if pg_str.isdigit():
            pg = int(pg_str)
            if fabric_type == "Multi-Planar Spectrum 8K GPU":
                qfabt1_pg_size = 4
            elif fabric_type == "Multi-Planar Spectrum 16K GPU":
                qfabt1_pg_size = 8
            elif fabric_type == "Multi-Planar Spectrum 32K GPU":
                qfabt1_pg_size = 16
                
            failed_details = {} # dict to have details incase of validation failed
            gpu_pg_validation = False
            qfabt0_pg_validation = False
            qfabt1_pg_validation = False
            qfab_ipr_pg_validation = False

            valid_keys = [
            result_qfab['gpu_platform'],
            result_qfab['t0_platform'],
            result_qfab['t1_platform'],
            result_qfab['ipr_platform']
            ]
            invalid_platforms = [platform for platform in platform_details if not any(valid_key in platform for valid_key in valid_keys)]

            if invalid_platforms:
                placement_group_validation[pg] = {
                    "status": "failed",
                    "failed_details": {
                        "reason": f"in-valid platform present {invalid_platforms}",
                    }
                }
                continue
            for platform, count in platform_details.items():
                # GPU PLATFORM
                if  result_qfab['gpu_platform'] in platform:
                    if count == result_qfab['group_size'][result_qfab['gpu_platform']]:
                        gpu_pg_validation = True
                    else:
                        failed_details[platform] = {"expected": result_qfab['group_size'][result_qfab['gpu_platform']], "available": count}
                            
                # QFAB T0           
                if result_qfab['t0_platform'] in platform:
                    if count == result_qfab['group_size'][result_qfab['t0_platform']]:
                        qfabt0_pg_validation = True
                    else:
                        failed_details[platform] = {"expected": result_qfab['group_size'][result_qfab['t0_platform']], "available": count}
                    
                # QFAB T1
                if result_qfab['t1_platform'] in platform and pg in range(151, 155): 
                    if count == qfabt1_pg_size:
                        qfabt1_pg_validation = True
                    else:
                        failed_details[platform] = {"expected": qfabt1_pg_size, "available": count}
                        
                # QFAB IPR        
                if result_qfab['ipr_platform'] in platform and pg == 201:
                    if count == result_qfab['ipr_count']:
                        qfab_ipr_pg_validation = True
                    else:
                        failed_details[platform] = {"expected": result_qfab['ipr_count'], "available": count}

            if (gpu_pg_validation and qfabt0_pg_validation) or qfabt1_pg_validation or qfab_ipr_pg_validation:
                placement_group_validation[pg] = {"status":"passed"}
                #print(f"validation passed for placement group {pg}")
            else:
                placement_group_validation[pg] = {"status":"failed", "failed_details": failed_details}
                #print(f"\nvalidation failed for placement group {pg}\n{platform_details}")
        elif pg_str == "nan":    #this to ignore blank line.
            continue
        else:
            print(f"[red]Skipping non-integer placement group: {pg_str}[/red] ❌")
    return placement_group_validation

def validate_cfab_blocks(input_file, server_type, result_qfab, result_cfab, cfab_column):
    """Validates CFAB blocks based on the provided input file, server type, and result dictionaries."""
    pg_platform_dict, cb_platform_dict, qi_platform_dict, bn_platform_dict = location_file_reader(input_file)

    if cfab_column == "cfab_16_column" and server_type in ["B300"]:
        result_cfab['group_size'][result_cfab['t0_platform']] = 4
    elif cfab_column == "cfab_16_column" and server_type in ["GB300"]:
        result_cfab['group_size'][result_cfab['t0_platform']] = 2
    elif cfab_column == "cfab_8_column" and server_type in ["B300"]:
        result_cfab['group_size'][result_cfab['t0_platform']] = 2
        
    if cfab_column == "cfab_16_column" and server_type in ["GB300", "GB200"]:     # adjusting to support 16 GPU in single CFAB block
        result_qfab["group_size"] = {k: (v * 2) + 2 for k, v in result_qfab["group_size"].items()}
    if cfab_column == "cfab_16_column" and server_type in ["B300"]:
        result_qfab['group_size'] = {key: value * 2 for key, value in result_qfab['group_size'].items()}

    cfab_block_validation = {}
    failed_details = {}
    
    # Iterate over each block in cb_platform_dict
    for block_str, platform_details in cb_platform_dict.items():
        if not block_str.isdigit():
            continue
        block = int(block_str)
        gpu_block_validation = False
        qfabt0_block_validation = False
        qfabt1_block_validation = False
        qfab_ipr_block_validation = False
        cfabt0_block_validation = False
        cfabt1_block_validation = True
        
        block_failed_details = {}
        
        if block == 1:
            valid_keys = {result_qfab['t1_platform'], result_qfab['ipr_platform']}
            if not any(any(valid_platform in platform for valid_platform in valid_keys) for platform in platform_details):
                continue
        else:
            valid_keys = {result_qfab['gpu_platform'], result_qfab['t0_platform'], result_cfab['t0_platform']}
            valid_keys = valid_keys.union(result_cfab.get('t1_platforms', []))
            if not any(any(valid_platform in platform for valid_platform in valid_keys) for platform in platform_details):
                continue

        for platform, count in platform_details.items():
            if block == 1:
                if result_qfab['t1_platform'] in platform:
                    qfabt1_block_validation = count == result_qfab['t1_count']
                    if not qfabt1_block_validation:
                        block_failed_details[platform] = {"expected": result_qfab['t1_count'], "available": count}

                if result_qfab['ipr_platform'] in platform:
                    qfab_ipr_block_validation = count == result_qfab['ipr_count']
                    if not qfab_ipr_block_validation:
                        block_failed_details[platform] = {"expected": result_qfab['ipr_count'], "available": count}
            else:
                if result_qfab['gpu_platform'] in platform:
                    gpu_block_validation = count <= result_qfab['group_size'][result_qfab['gpu_platform']]
                    if not gpu_block_validation:
                        block_failed_details[platform] = {"expected": result_qfab['group_size'][result_qfab['gpu_platform']], "available": count}

                if result_qfab['t0_platform'] in platform:
                    qfabt0_block_validation = count <= result_qfab['group_size'][result_qfab['t0_platform']]
                    if not qfabt0_block_validation:
                        block_failed_details[platform] = {"expected": result_qfab['group_size'][result_qfab['t0_platform']], "available": count}

                if result_cfab['t0_platform'] in platform:
                    cfabt0_block_validation = count == result_cfab['group_size'][result_cfab['t0_platform']]
                    if not cfabt0_block_validation:
                        block_failed_details[platform] = {"expected": result_cfab['group_size'][result_cfab['t0_platform']], "available": count}

                if 't1_platforms' in result_cfab:
                    for t1_platform in result_cfab['t1_platforms']:
                        if platform_details.get(t1_platform, 0) != result_cfab['group_size'][t1_platform]:
                            cfabt1_block_validation = False
                            block_failed_details[t1_platform] = {"expected": result_cfab['group_size'][t1_platform], "available": platform_details.get(t1_platform, 0)}

        # Update validation result for the current block
        if (block == 1 and qfabt1_block_validation and qfab_ipr_block_validation) or (block != 1 and gpu_block_validation and qfabt0_block_validation and cfabt0_block_validation and cfabt1_block_validation):
            cfab_block_validation[block] = {"status": "passed"}
        
        else:
            cfab_block_validation[block] = {"status": "failed", "failed_details": block_failed_details}
            failed_details.update(block_failed_details)

    return cfab_block_validation


def validate_qfab_instance_id(input_file, required_servers, result_qfab, result_cfab, core_network_dh, have_gpu):
    pg_platform_dict, cb_platform_dict, qi_platform_dict, bn_platform_dict = location_file_reader(input_file)
    instance_id_validation = {}
    
    for instance_id, platform_details in qi_platform_dict.items():
        if instance_id.isdigit():
            instance_id = int(instance_id)
        failed_details = {}
        
        if instance_id == 2:
            gpu_instance_validation = False
            qfabt0_instance_validation = False
            qfabt1_instance_validation = False
            qfab_ipr_instance_validation = False
            

            for platform, count in platform_details.items():
                if 't1_platform' in result_qfab and result_qfab['t1_platform'] in platform:
                    qfabt1_instance_validation = count == result_qfab.get('t1_count', 0)
                    #print(f"qfabt1_instance_validation: {qfabt1_instance_validation}")
                    if not qfabt1_instance_validation:
                        failed_details[platform] = {"expected": result_qfab.get('t1_count', 0), "available": count}
                if 'ipr_platform' in result_qfab and result_qfab['ipr_platform'] in platform:
                    qfab_ipr_instance_validation = count == result_qfab.get('ipr_count', 0)
                    #print(f"qfab_ipr_instance_validation: {qfab_ipr_instance_validation}")
                    if not qfab_ipr_instance_validation:
                        failed_details[platform] = {"expected": result_qfab.get('ipr_count', 0), "available": count}
                if 't0_platform' in result_qfab and result_qfab['t0_platform'] in platform:
                    qfabt0_instance_validation = count == result_qfab.get('t0_count', 0)
                    if not qfabt0_instance_validation:
                        failed_details[platform] = {"expected": result_qfab.get('t0_count', 0), "available": count}
                if 'gpu_platform' in result_qfab and result_qfab['gpu_platform'] in platform:
                    gpu_instance_validation = count == required_servers
                    if not gpu_instance_validation:
                        failed_details[platform] = {"expected": required_servers, "available": count}
            
            validation_status = all([gpu_instance_validation, qfabt0_instance_validation])
    
            if core_network_dh and not have_gpu:
                validation_status = qfabt1_instance_validation and qfab_ipr_instance_validation
            elif core_network_dh:
                validation_status = validation_status and qfabt1_instance_validation and qfab_ipr_instance_validation
            
        elif instance_id == 1:
            cfabt0_instance_validation = False
            cfabt1_instance_validation = True

            # t0: as before
            for platform, count in platform_details.items():
                if 't0_platform' in result_cfab and result_cfab['t0_platform'] in platform:
                    cfabt0_instance_validation = count == result_cfab.get('t0_count', 0)
                    if not cfabt0_instance_validation:
                        failed_details[platform] = {
                            "expected": result_cfab.get('t0_count', 0),
                            "available": count
                        }
                    
            # t1: every required t1_platform must be present and match count
            if 't1_platforms' in result_cfab:
                for t1_platform in result_cfab['t1_platforms']:
                    count = platform_details.get(t1_platform, 0)
                    if count != result_cfab.get('t1_count', 0):
                        cfabt1_instance_validation = False
                        failed_details[t1_platform] = {
                            "expected": result_cfab.get('t1_count', 0),
                            "available": count
                        }
            
            if core_network_dh and not have_gpu:
                continue
            validation_status = all([cfabt0_instance_validation, cfabt1_instance_validation])
            #print(validation_status)   
        instance_id_validation[instance_id] = {"status": "passed" if validation_status else "failed"}
        
        if not validation_status:
            instance_id_validation[instance_id]["failed_details"] = failed_details
    
    return instance_id_validation


def validate_block_name_and_cfab_block_name(input_file):
    pg_platform_dict, cb_platform_dict, qi_platform_dict, bn_cb_platform_dict = location_file_reader(input_file)
    block_name_validation = {}
    
    validation_status = True
    
    for block_name, cfab_fabric_block_details in bn_cb_platform_dict.items():
        if block_name == '0' or cfab_fabric_block_details == '0':
            continue
        
        validation_status = True
        failed_details = {}
        
        # Check if block_name has a leading 0
        if str(block_name).startswith('0'):
            validation_status = False
            failed_details["block_name_leading_zero"] = block_name
        
        # Check if cfab_fabric_block has a key with a leading 0  
        keys = list(cfab_fabric_block_details.keys())
        if len(keys) > 1:
            for key in keys:
                if str(key).startswith('0'):
                    validation_status = False
                    failed_details["cfab_fabric_block_leading_zero"] = key
        
        # Check if block_name is equal to both keys in cfab_fabric_block
        if len(keys) > 1:
            if str(block_name) != [str(key) for key in keys]:
                validation_status = False
                failed_details["block_name_mismatch"] = {"block_name": block_name, "cfab_fabric_block": cfab_fabric_block_details}
        
        block_name_validation[block_name] = {"status": "passed" if validation_status else "failed"}
        
        if not validation_status:
            block_name_validation[block_name]["failed_details"] = failed_details
    
    return block_name_validation
        

def get_failure_reasons(failed_details):
    failure_reasons = []
    for key, details in failed_details.items():
        if isinstance(details, dict) and 'expected' in details and 'available' in details:
            failure_reasons.append(f"{key}: Expected - {details['expected']}, Available - {details['available']}")
        elif key == "reason":
            failure_reasons.append(f"reason: {details}")
        elif isinstance(details, list):
            failure_reasons.append(f"{key}: {', '.join(details)}")
        else:
            failure_reasons.append(f"{key}: {details}")
    return ", ".join(failure_reasons)

def main():
    config = load_config('qfab_boms.json')
    
    # List down supported servers type
    server_options = list(config['server_models'].keys())
    
    server_type = get_user_input("\nSelect Server Type:", server_options)
    
    # List down only fabric offering for server type basis
    fabric_options = [
        k for k, v in config['network'].items()
        if not k.startswith("cfab2.0") 
            #and 'server_models_supported' in v
            and any(server_type in d for server in v.get("server_platform", []) for d in [server])
    ]
    if not fabric_options:
        print(f"No fabrics found that support server type: {server_type}")
        sys.exit()
            
    fabric_type = get_user_input("\nSelect Fabric (main QFAB) Type:", fabric_options)

    cfab_column_options = ["cfab_8_column", "cfab_16_column"]
    cfab_column = get_user_input("\nSelect CFAB column configuration:", cfab_column_options)

    while True:
        try:
            required_servers = int(input("Please Enter Required GPU Node/Racks : "))
            if required_servers > 0:
                break
            else:
                print("Please enter a positive integer.")
        except ValueError:
            print("Invalid input. Enter an integer value.")

    #server_info = config['server_models'][server_type]
    qfab_conf = config['network'][fabric_type]
    
    gpu_per_node = config['server_models'][server_type]['gpu_per_node']
    
    platform_map = None
    for entry in qfab_conf.get('server_platform', []):
        if server_type in entry:
            platform_map = entry[server_type]
            break
    if not platform_map:
        print(f"❌ Platform mapping for server '{server_type}' not found.")
        sys.exit(1)

    # platform_map will looks like {"GPU_V5_X11_B300_R.01:64": 64}
    gpu_platform = next(iter(platform_map.keys())).split(":")[0]
    rail_group   = int(next(iter(platform_map.values())))

    server_info = {
        "gpu_per_node": gpu_per_node,
        "platform":     gpu_platform,
        "rail_group":   rail_group
    }

    result_qfab = calculate_qfab_platforms(required_servers, server_info, qfab_conf, fabric_type, server_type)

    try:
        if server_type in ("B300", "B200", "RTX6k/B40"):
            cfab_conf = config['network']["cfab2.0_B300_B200"]
        elif server_type in ("GB300", "GB200"):
            cfab_conf = config['network']["cfab2.0_GB300_GB200"]
    except KeyError:
        print(f"No CFAB config found for {server_type}. Exiting.")
        sys.exit()

    result_cfab = calculate_cfab_platforms(required_servers, server_type, cfab_conf, cfab_column)

    # Print required hardware info
    print_hw_requirements(result_qfab, result_cfab, required_servers, server_type, fabric_type)

    rack_sorting_order = []
    t2_platform = result_qfab.get('t2_platform')
    if t2_platform:
        rack_sorting_order.append(t2_platform)

    t1_platforms = result_qfab.get('t1_platforms')
    if t1_platforms:
        rack_sorting_order.extend(t1_platforms)
    else:
        t1_platform = result_qfab.get('t1_platform')
        if t1_platform:
            rack_sorting_order.append(t1_platform)

    cfab_t1_platforms = result_cfab.get('t1_platforms')
    if cfab_t1_platforms:
        rack_sorting_order.extend(cfab_t1_platforms)
    else:
        cfab_t1_platform = result_cfab.get('t1_platform')
        if cfab_t1_platform:
            rack_sorting_order.append(cfab_t1_platform)

    cfab_t0_platform = result_cfab.get('t0_platform')
    if cfab_t0_platform:
        rack_sorting_order.append(cfab_t0_platform)

    qfab_t0_platform = result_qfab.get('t0_platform')
    if qfab_t0_platform:
        rack_sorting_order.append(qfab_t0_platform)

    order_server_platform = result_qfab.get('gpu_platform')
    if order_server_platform:
        rack_sorting_order.append(order_server_platform)

    hw_required_info = build_hw_required_info_dict(result_qfab, result_cfab, required_servers, server_type)
    
    print()
    print("*" * 80)
    print(f"[yellow]Do you want to validate required hardware vs location file from atlas? (y/n): [/yellow]", end='')
    validate = input()
    if validate.lower() != 'y':
        print("Exiting without location file validation.")
        sys.exit()
    
    input_file = input("Enter the path to the location CSV file: ")
    df = pd.read_csv(input_file)
    platform_counts = df[PLATFORM_COLUMN].value_counts()
    platform_info = platform_counts.to_dict()
    #print(platform_info)

    #print("\nPlatforms found in the CSV file:")
    #for platform, count in platform_counts.items():
    #    print(f"{platform} (Count: {count})")
    
    core_network_dh = check_for_core_network_data_hall(result_qfab, platform_info)
    
    have_gpu = check_gpu_availability(result_qfab,platform_info)
    
    print("\n********* performing validation for required vs available hardware ***********")
    if not core_network_dh:
        # start with T1 platforms (use list if present, else fall back to single)
        t1_platforms = result_qfab.get("t1_platforms") or [result_qfab.get("t1_platform")]
        exclude_platform = [p.lower() for p in t1_platforms if p]

        if fabric_type.startswith("Multi-Planar Spectrum"):
            ipr = result_qfab.get("ipr_platform")
            if ipr:
                exclude_platform.append(ipr.lower())

        elif fabric_type.startswith("GFAB"):
            t2 = result_qfab.get("t2_platform")
            if t2:
                exclude_platform.append(t2.lower())

        hw_required_info = {
            k: v
            for k, v in hw_required_info.items()
            if not any(p in k.lower() for p in exclude_platform)
        }

    elif core_network_dh:
        if not have_gpu:       # checking for data-hall does not have gpu 
            hw_required_info = {k: v for k, v in hw_required_info.items() if any(p.lower() in k.lower() for p in [result_qfab['t1_platform'], result_qfab['ipr_platform']])}

    result = {k: "passed" if hw_required_info.get(k) == platform_info.get(k) else "failed" if k in platform_info else "missing platform in csv(location file)" for k in hw_required_info}
    result.update({k: "Additional rack appear in the CSV file.)" for k in platform_info if k not in hw_required_info})
    for k, v in result.items():
        count = platform_info.get(k, "N/A")
        if v == "passed":
            print(f"[green]{k} (Count: {count}): {v}[/green] ✅")
        elif v == "failed":
            print(f"[red]{k} (Count: {count}): {v}[/red] ❌")
        elif v == "missing platform in csv(location file)":
            print(f"[red]{k} (Count: {count}): {v}[/red] ❌")
        else:
            print(f"{k} (Count: {count}): {v}")

    # RE-ORDER only if conndition meet
    if (server_type in ["B300","B200"] and fabric_type in ["QFAB3.0","QFAB3.0_ONOS","B200 QFAB3.0", "B200 QFAB3.0_ONOS"]):
        print("\n**********************************************************************************")
        rack_re_ordering = input("Do you want to do reordering of racks? (y/n): ")
        if rack_re_ordering.lower() != 'y':
            print("Exiting without atlas validation.")
            sys.exit()
        group_size = {**result_qfab['group_size'], **result_cfab['group_size']}
        df_final = sort_and_reorder_data(df, group_size, rack_sorting_order)

        # Get the directory path of the input file
        input_dir = os.path.dirname(input_file)

        # Create the output file path by joining the input directory with the desired output file name
        output_file = os.path.join(input_dir, f"sorted_{os.path.basename(input_file)}")

        df_final.to_csv(output_file, index=False)
        print(f"✅ Done! RACK_SORT_ORDER updated in {output_file}")
        
    # for multiplaner sites   
    elif fabric_type.startswith("Multi-Planar Spectrum"):
        print(f"\n********** Performing validation for Column [PLACEMENT_GROUP] ************")
        placement_group_validation_status = validate_placement_groups(input_file, server_type, fabric_type, result_qfab, result_cfab)
        #print(placement_group_validation_status)
        
        # Get all placement group IDs that are integers and less than 100
        ids = sorted([pg for pg in placement_group_validation_status.keys() if pg < 100])

        expected_id = None
        sequence_error = None
        if ids:
            expected_id = ids[0]  # Start with the lowest ID

        for placement_group, status in placement_group_validation_status.items():
            if int(placement_group) < 100:
                if expected_id is not None and int(placement_group) != expected_id:
                    sequence_error = "placement group is not in sequence."
                if int(placement_group) == expected_id:
                    expected_id += 1
                        
        for placement_group, status in placement_group_validation_status.items():
            if status['status'] == "passed":
                print(f"[green]placement group {placement_group}: {status['status']}[/green] ✅")
            if status['status'] == "failed":
                failure_reasons = get_failure_reasons(status.get("failed_details", {}))
                print(f"[red]placement group {placement_group}: {status['status']} ❌ failure {failure_reasons}[/red]")
        if sequence_error:
            print(f"[red]{sequence_error}[/red] ❌")
                
        print(f"\n********** Performing validation for Column [CFAB_FABRIC_BLOCK] ************")
        cfab_block_validation_status = validate_cfab_blocks(input_file, server_type, result_qfab, result_cfab, cfab_column)
        for block, status in cfab_block_validation_status.items():
            if not core_network_dh and int(block) == 1:
                continue
            if status['status'] == "passed":
                print(f"[green]cfab block {block}: {status['status']}[/green] ✅")
            if status['status'] == "failed":
                failure_reasons = get_failure_reasons(status.get("failed_details", {}))
                msg = f"Unexpected platforms found in block {block}" if int(block) == 0 else "failure"
                print(f"[red]cfab block {block}: {status['status']} ❌ {msg}: {failure_reasons}[/red]")
        
        print(f"\n********** Performing validation for Column [QFAB_INSTANCE_ID] ************")
        instance_id_validation = validate_qfab_instance_id(input_file, required_servers, result_qfab, result_cfab, core_network_dh, have_gpu)
        #print(instance_id_validation)
        for instance_id, status in instance_id_validation.items():
            if status['status'] == "passed":
                if instance_id == 1:
                    print(f"[green]qfab instance id {instance_id}: cfab racks: {status['status']}[/green] ✅")
                elif instance_id == 2:
                    print(f"[green]qfab instance id {instance_id}: qfab racks: {status['status']}[/green] ✅")
            if status['status'] == "failed":
                failure_reasons = get_failure_reasons(status.get("failed_details", {}))
                if instance_id == 1:
                    print(f"[red]qfab instance id {instance_id}: cfab racks: {status['status']} ❌ Failure reason: {failure_reasons}[/red]")
                elif instance_id == 2:
                    print(f"[red]qfab instance id {instance_id}: qfab racks: {status['status']} ❌ Failure reason: {failure_reasons}[/red]")
        
        print(f"\n********** Performing validation for Column [BLOCK_NAME] and [CFAB_FABRIC_BLOCK] ************")
        block_name_validation_status = validate_block_name_and_cfab_block_name(input_file)
        #print(block_name_validation_status)
        for block, status in block_name_validation_status.items():
            if status['status'] == "passed":
                print(f"[green]cfab block {block}: {status['status']}[/green] ✅")
            if status['status'] == "failed":
                failure_reasons = get_failure_reasons(status.get("failed_details", {}))
                print(f"[red]cfab block {block}: {status['status']} ❌ Failure reason: {failure_reasons}[/red]")
        
    else:
        print("🚪 No re‑ordering performed.")

if __name__ == "__main__":
    main()