'''
To create input file: version-raw.txt
ncpcli@iad 2025-04-25 08:27:14> update-device-list --devices-by-role=qfabt2 --device-names-matching iad63* --device-state-matching in-service --device-state-matching maintenance
ncpcli@iad 2025-04-25 08:27:14> timeit devices compare-config --latest
'''

# File paths
input_file_path = 'version-raw.txt'
output_file_path = 'version-blocks.txt'

# Read the input file
with open(input_file_path, 'r') as file:
    lines = file.readlines()

# Extract lines between "Version:" and "Diff:"
extracted_lines = []
recording = False

for line in lines:
    stripped_line = line.strip()

    if stripped_line.startswith("Version:"):
        recording = True
        continue  # Skip the "Version:" line

    elif stripped_line.startswith("Diff:"):
        recording = False
        continue  # Skip the "Diff:" line

    if recording:
        extracted_lines.append(stripped_line)

# Write the extracted lines to a new file
with open(output_file_path, 'w') as out_file:
    for line in extracted_lines:
        out_file.write(line + '\n')

print(f"Extracted data saved to {output_file_path}")
