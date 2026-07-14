import argparse
import json

def flatten_if_list(value):
    # If it's a list and the list contains dictionaries, return the first dictionary
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value[0] if value else {}
    return value

def special_handling_for_fabrics(data):
    # Special handling to nest fabrics inside an 'actions' list
    if 'fabrics' in data:
        data['fabrics'] = {'actions': [data['fabrics']]}
    return data

def extract_and_update_racks(buildings_data, old_sku, new_sku, bldg_block):
    rack_actions = []
    target_building = bldg_block.split("-")[0]  # Extract the building name from bldg_block
    for building_data in buildings_data.get("buildings", []):
        if building_data.get("name") == target_building:
            for block in building_data.get("blocks", []):
                if block.get("name") == bldg_block:
                    all_block = block.get("all", {})
                    for phy, data in all_block.items():
                        if data.get("platform") == old_sku:
                            updated_data = {k: flatten_if_list(v) for k, v in data.items()}
                            updated_data = special_handling_for_fabrics(updated_data)  # Handle fabrics structure
                            updated_data["platform"] = new_sku  # Update the platform
                            if "aid" in updated_data:
                                del updated_data["aid"]  # Remove the aid key
                            action_entry = {
                                "actionType": "ADD",
                                "phys": phy,
                                "platform": new_sku,
                                "building": target_building,
                                "block": bldg_block,
                                "row": "all",
                                **updated_data  # Include remaining updated data dynamically
                            }
                            rack_actions.append(action_entry)
    return rack_actions

def main():
    parser = argparse.ArgumentParser(description="Create changeset to update rack SKUs in rackmaps.json")
    parser.add_argument("-r", "--region", required=True, help="Region name")
    parser.add_argument("--bldg_block", required=True, help="Block names separated by commas (e.g., bldg14-block1,bldg15-block2)")
    parser.add_argument("--old_rack_sku", required=True, help="Old Rack SKU to be updated")
    parser.add_argument("--new_rack_sku", required=True, help="New Rack SKU to replace the old SKU")
    parser.add_argument("--input_filename", required=True, help="Input JSON file containing the data")

    args = parser.parse_args()

    with open(args.input_filename, 'r') as file:
        data = json.load(file)

    rack_actions = []
    block_names = args.bldg_block.split(",")
    for block_name in block_names:
        rack_actions.extend(extract_and_update_racks(data, args.old_rack_sku, args.new_rack_sku, block_name))

    output = {
        "region": args.region,
        "rackActions": {"actions": rack_actions},
        "blockActions": {"actions": []},
        "buildingActions": {"actions": []}
    }

    output_filename = f"{args.region}-changeset-add.json"
    with open(output_filename, 'w') as file:
        json.dump(output, file, indent=2)

    print(f"Output written to {output_filename}")

if __name__ == "__main__":
    main()