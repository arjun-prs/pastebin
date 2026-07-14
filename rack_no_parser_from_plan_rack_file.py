import re
import argparse

def parse_arguments():
    parser = argparse.ArgumentParser(description="Parse input file and extract Rack Info")
    parser.add_argument("--block", type=str, help="e.g: bldg14-block1")
    parser.add_argument("--old_rack_sku", type=str, help="e.g: net.ad_gfab_v1_400_t2_c1_1.02")
    parser.add_argument("--new_rack_sku", type=str, help="e.g: net.ad_gfab_v1_400_t2_1.01")
    parser.add_argument("--input_file", type=str, help="Input file path")
    return parser.parse_args()

def parse_numeric_value(line):
    match = re.search(r'\[(\d+)\]', line)
    if match:
        return match.group(1).zfill(4)  # Format rack number with leading zeros
    return None

def main():
    args = parse_arguments()
    input_file = args.input_file

    block_parts = args.block.strip().split('-')  # Split block into parts
    old_rack_sku = args.old_rack_sku
    new_rack_sku = args.new_rack_sku

    output_lines = []
    output_lines.append("||Original Platform||New Platform||Rack No.||\n")

    with open(input_file, "r") as f_in:
        lines = f_in.readlines()

    for line in lines:
        line_parts = line.strip().split('-')  # Split line into parts
        if all(part in line_parts for part in block_parts) and old_rack_sku in line:
            rack_no = parse_numeric_value(line)
            if rack_no is not None:
                output_lines.append(f"||{old_rack_sku}||{new_rack_sku}|| {rack_no} ||\n")

    output_filename = "rack_platform-change.txt"
    with open(output_filename, "w") as f_out:
        f_out.writelines(output_lines)

    print("\033[92mOutput file '{}' has been saved.\033[0m".format(output_filename))

if __name__ == "__main__":
    main()