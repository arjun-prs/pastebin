def truncate_data(row):
    # Keep only the first two elements (split by comma)
    return ','.join(row[:2])  # Join only the first two parts

def main():
    input_filename = 'version-blocks.txt'  # Change this to your input data file name
    output_filename = 'version-file-trim.txt'  # Output file name in text format

    try:
        with open(input_filename, mode='r') as infile, \
             open(output_filename, mode='w') as outfile:
            for line in infile:
                row = line.strip().split(',')  # Split by comma
                truncated_row = truncate_data(row)
                outfile.write(truncated_row + '\n')  # Write the truncated row without quotes

        print(f"Data has been processed and stored in {output_filename}.")

    except FileNotFoundError:
        print(f"The file {input_filename} was not found.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == '__main__':
    main()
