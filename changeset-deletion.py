import json
import argparse


'''
Input file is autonet-pans phx-racks full file copy paste
'''
class EmptyOrInvalidJsonError(Exception):
    pass

def extract_aids(buildings_data, block_names, old_platform):
    aids = []
    for building in buildings_data.get("buildings", []):
        for block in building.get("blocks", []):
            if block.get("name") in block_names:
                all_data = block.get("all", {})
                for key, value in all_data.items():
                    if isinstance(value, dict) and value.get("platform") == old_platform:
                        aid = value.get("aid")
                        if aid:  # Ensure aid is not None
                            aids.append(aid)
    return aids

def print_green(text):
    print("\033[92m{}\033[00m" .format(text))

def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description="Create changeset to delete racks SKUs from rackmaps.json")

    parser.add_argument("-r", "--region", required=True, metavar="TEXT", help="Region name")
    parser.add_argument("--bldg-block", required=True, metavar="TEXT", help="Block names separated by commas (e.g., bldg14-block1,bldg15-block2)")
    parser.add_argument("--old_platform", required=True, metavar="TEXT", help="Rack SKU, e.g., net.ad_gfab_v1_400_t2_c1_1.02")
    parser.add_argument("--new_platform", required=True, metavar="TEXT", help="Rack SKU, e.g., net.ad_gfab_v1_400_t2_c1_1.02")
    parser.add_argument("--input_filename", required=True, metavar="TEXT", help="Input JSON file containing the data: copy the whole rackmaps file: e.g: rackmaps.json")

    args = parser.parse_args()

    input_filename = args.input_filename
    region = args.region
    block_names = args.bldg_block.split(",")
    old_platform = args.old_platform
    new_platform = args.old_platform

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

    # Extract AID values
    aids = extract_aids(data, block_names, old_platform)

    # Prepare JSON output
    output = {
        "region": region,
        "rackActions": {
            "actions": [{"actionType": "MODIFY", "aid": aid, "row": "__deleted__"} for aid in aids]
        },
        "blockActions": {"actions": []},
        "buildingActions": {"actions": []}
    }

    output_filename = f"{region}-changset-del.json"

    # Write output to JSON file
    with open(output_filename, 'w') as file:
        json.dump(output, file, indent=2)

    print_green(f"Output written to {output_filename}")

if __name__ == "__main__":
    main()