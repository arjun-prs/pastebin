import re

'''
Please replace "aga1-q2-b12-failed.txt" file with the filename you want to extract data from.
usage: python3 data_extract_test_optics_new.py
'''
# Open the file in read mode
with open('aga1-q2-b12-failed.txt', 'r') as file:
    lines = file.readlines()

# Define flags and variables
results = []
i = 0

# Iterate through the lines to find "Name: test_optics" and the subsequent "Message: Failed:"
while i < len(lines) - 1:
    if "Name: test_optics" in lines[i] and "Message: Failed:" in lines[i + 1]:
        message_failed_line = lines[i + 1].strip()
        results.append(message_failed_line)
    i += 1

# Debug print to check extracted results
print("Extracted results:")
for res in results:
    print(res)

# Extract the relevant data from the results
inner_lists = []
for result in results:
    try:
        # Find the dictionary part in the result string
        dict_part = re.search(r'Message: Failed: (.*)', result).group(1)
        # Find the list of dictionaries in the "errors_object" key
        errors_object = re.search(r'"errors_object": (\[.*\])', dict_part).group(1)
        # Convert the string representation of the list of dictionaries to actual list of dictionaries
        errors_list = eval(errors_object)
        inner_lists.extend(errors_list)
    except (ValueError, SyntaxError, AttributeError) as e:
        print(f"Error processing line: {e}")
        pass

# Debug print to check extracted data
print("Extracted inner lists:")
for item in inner_lists:
    print(item)

# Write data to a CSV file
with open("test_optics_failures.csv", "w") as file:
    # Write headers
    header_elements = ['device', 'intf_name', 'input_power', 'output_power', 'device_phys']
    file.write(','.join(header_elements) + '\n')

    # Write data rows
    for item in inner_lists:
        row = [item.get(key, '') for key in header_elements]
        file.write(','.join(row) + '\n')

print("Data has been written to test_optics_failures.csv")
