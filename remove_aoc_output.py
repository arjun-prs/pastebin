# awk '/Name: test_valid_aoc_firmware/{print; getline; next} 1' aga1-q2-b12-failed.txt > remove_aoc_output_file.txt
def remove_next_line(filename):
    with open(filename, 'r') as f:
        lines = f.readlines()

    output_lines = []
    skip_next_line = False
    for line in lines:
        if skip_next_line:
            skip_next_line = False
            continue
        if "Name: test_valid_aoc_firmware" in line:
            output_lines.append(line)
            skip_next_line = True
            continue
        output_lines.append(line)

    with open('remove_aoc_output_file.txt', 'w') as f:
        f.writelines(output_lines)

remove_next_line('aga1-q2-b12-failed.txt')