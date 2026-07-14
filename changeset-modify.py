import json
import argparse

'''
Input file is rackmaps  full json file copy paste
'''
class EmptyOrInvalidJsonError(Exception):
    pass

def extract_aids(buildings_data, block_names, old_platforms):
    aids = []
    for building in buildings_data.get("buildings", []):
        building_name = building.get("name")
        for block in building.get("blocks", []):
            block_name = block.get("name")
            if block_name in block_names:
                all_data = block.get("all", {})
                for key, value in all_data.items():
                    if isinstance(value, dict) and value.get("platform") in old_platforms:
                        aid = value.get("aid")
                        if aid:  # Ensure aid is not None
                            aids.append({"actionType": "MODIFY", "aid": aid, "platform": value.get("platform"), "building": building_name, "block": block_name})
    return aids

def print_green(text):
    print("\033[92m{}\033[00m" .format(text))

def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description="Create changeset to delete racks SKUs from rackmaps.json")

    parser.add_argument("-r", "--region", required=True, metavar="TEXT", help="Region name")
    parser.add_argument("--block", required=True, metavar="TEXT", help="Block name(s) separated by commas (e.g., bldg14-block1,bldg15-block2)")
    parser.add_argument("--old_platform", required=True, metavar="TEXT", help="Old Rack SKU(s) separated by commas (e.g., net.ad_gfab_v1_400_t2_c1_1.02,net.ad_gfab_v1_400_t2_c1_1.03)")
    parser.add_argument("--new_platform", required=True, metavar="TEXT", help="New Rack SKU(s) separated by commas (e.g., net.ad_gfab_v1_400_t2_c1_1.01,net.ad_gfab_v1_400_t2_c1_1.02)")
    parser.add_argument("--input_filename", required=True, metavar="TEXT", help="Input JSON file containing the data: copy the whole rackmaps file: e.g: rackmaps.json")

    args = parser.parse_args()

    input_filename = args.input_filename
    region = args.region
    block_names = args.block.split(",")
    old_platforms = args.old_platform.split(",")
    new_platforms = args.new_platform.split(",")

    try:
        with open(input_filename, 'r') as file:
            data = file.read()
            if not data.strip():
                raise EmptyOrInvalidJsonError("Input JSON file is empty or not in the right format.")
            else:
                data = json.loads(data)
    except FileNotFoundError:
        print(f"Error: Input JSON file '{input_filename}' not found.")
        exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse JSON data in the input file '{input_filename}'.")
        print(f"Details: {e}")
        exit(1)
    except EmptyOrInvalidJsonError as e:
        print(f"Error: {e}")
        exit(1)

    # Initialize output dictionary
    output = {"region": region, "rackActions": {"actions": []}, "blockActions": {"actions": []}, "buildingActions": {"actions": []}}

    for old_platform, new_platform in zip(old_platforms, new_platforms):
        # Extract AID values for each old_platform
        aids = extract_aids(data, block_names, [old_platform])

        # Append actions for the current platform
        output["rackActions"]["actions"].extend([{"actionType": "MODIFY", "aid": aid["aid"], "platform": new_platform} for aid in aids])

    output_filename = f"{region}-changset-modify.json"

    # Write output to JSON file
    with open(output_filename, 'w') as file:
        json.dump(output, file, indent=2)

    print_green(f"Output written to {output_filename}")

if __name__ == "__main__":
    main()