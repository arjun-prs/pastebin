input_file = 'aga1-q2-b12-failed.txt'
output_file = 'lldp_failures.txt'

unknown_lines = []
other_lines = []

with open(input_file, 'r') as file:
    for line in file:
        if "Expected Connection:" in line:
            expected_connection = line.strip().split(": ", 1)[1]
        elif "Current connection:" in line:
            current_connection = line.strip().split(": ", 1)[1]

            # Split the line by colon
            parts1 = current_connection.split(":")
            parts2 = expected_connection.split(":")


            if len(parts1) >= 1 and len(parts2) >= 1:
                if "Unknown" in current_connection:
                    updated_parts1 = parts1[:2] + parts1[3:6] + parts1[6:]
                    updated_current_connection = ":".join(updated_parts1)
                    updated_parts2 = parts2[:2] + parts2[3:6] + parts2[7:]
                    updated_expected_connection = ":".join(updated_parts2)
                    unknown_lines.append((updated_expected_connection, updated_current_connection))
                else:
                    print(parts1)
                    updated_parts1 = parts1[:2] + parts1[3:5] + parts1[7:]
                    print(updated_parts1)
                    updated_current_connection = ":".join(updated_parts1)
                    updated_parts2 = parts2[:2] + parts2[3:6] + parts2[7:]
                    updated_expected_connection = ":".join(updated_parts2)
                    other_lines.append((updated_expected_connection, updated_current_connection))


with open(output_file, 'w') as txt_file:
    for expected, current in unknown_lines:
        expected = expected.replace(":", ",").replace("<--->", ",")
        current = current.replace(":", ",").replace("<--->", ",")
        txt_file.write(f"Expected Connection,{expected},Current Connection,{current}\n")

    for expected, current in other_lines:
        expected = expected.replace(":", ",").replace("<--->", ",")
        current = current.replace(":", ",").replace("<--->", ",")
        txt_file.write(f"Expected Connection,{expected},Current Connection,{current}\n")