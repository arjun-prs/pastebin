# Author : nadeem.niyaz@oracle.com 

from datetime import datetime
import pexpect
import re
import getpass
import csv
import argparse


def ncpcli_conn(region,bldg,block):
    try:  
        child = pexpect.spawn(f"ncpcli -r {region} interactive")
        child.expect(pexpect.TIMEOUT, timeout=20) 

        pin = getpass.getpass(f"\U0001F511\033[92mEnter the Yubikey PIN:\033[0m ").strip()
        child.sendline(pin) 

        prompt_pattern = r'\w+@\w+\s\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}>'
        child.expect(prompt_pattern)
        child.sendline(f'update-device-list --device-names-matching={region}{bldg}-q2-b{block}-t1-r*\n') 
        child.expect(pexpect.TIMEOUT, timeout=10)  
        child.sendline(f'healthcheck run-device-job --tags dcs_rack_validation --wait\n')
        child.expect(pexpect.TIMEOUT, timeout=10) 

        message = """
        
                PLEASE WAIT    
             FETCHING LINK ISSUES 
        """
        print("\033[94m" + message + "\033[0m")
        print(f"State Started . . . . . . . . . . . \n")

        output_list = []

        while True:
            output = child.read_nonblocking(size=1024, timeout=3000).decode('utf-8')
            output_list.append(output) 
            
            if "Creating health check job with tags: dcs_rack_validation on 0 devices" in output:
                print("\033[91mError: Job failed. No devices could be selected in ncpcli. Exiting...\033[0m")
                child.close()
                exit(1)
  
            elif "<qfabt1=32>" in output or "<qfabt1=64>" in output: #success cond 
                break 
        
        dump = ''.join(output_list)  # Join collected output
        child.close()
        return dump 
    
    except pexpect.TIMEOUT:
        print(f"\033[91mError: Wrong Yubikey Pin or Pin Input Timeout .. Exiting \033[0m")
        child.close()
        exit(1)       
    except pexpect.EOF:
        print(f"\033[91mError: Unexpected error with the connection\033[0m")
        return None
    except Exception as e:
        print(f"\033[91mError: {str(e)}\033[0m")
        return None
    

def link_parsing(hcdump, linktype):   

    if not hcdump:
        print(f"Error: No data received for {linktype} links.")
        return [], 0  # Return empty results if hcdump is None or empty

    connections_list = []
    expected_connection = None
    count = 0
    expected_pattern = re.compile(rf"^Expected Connection:.*-{linktype}-.*")
    current_pattern = re.compile(rf"^Current connection:.*-{linktype}-.*|.*Unknown:Unknown.*")
   
    for line in hcdump.splitlines():  
        line = line.strip()
        
        if expected_pattern.match(line):
            expected_connection = line
            count += 1
             
        elif current_pattern.match(line):  
            if expected_connection:  
                connections_list.append([expected_connection])
                connections_list.append([line])
                connections_list.append([]) # to add newline in output txt file for formatting 
                expected_connection = None

    if linktype == "t0":
        print(f"Total Number of Down/Mismatched connections for T1<>T0 links: {count}")
    elif linktype == "t2":
        print(f"Total Number of Down/Mismatched connections for T2<>T1 links: {count}")

    return connections_list, count

def main():
    parser = argparse.ArgumentParser(
    description="Fetch and parse link issues using ncpcli.",
    epilog="Example usage: python script.py -r iad -g 31 -b 44"
)
    parser.add_argument("-r", "--region", required=True, help="Specify the region (e.g., iad, nrt).")
    parser.add_argument("-g", "--bldg", required=True, help="Specify the building number (e.g., 31 => iad31).")
    parser.add_argument("-b", "--block", required=True, help="Specify the block (e.g., 44 => Block44).")

    args = parser.parse_args()
    region, bldg, block = args.region.lower(), args.bldg, args.block 

    full_output = ncpcli_conn(region,bldg,block)

    time_now = datetime.now().strftime("%H:%M")
    print(f"\033[92m{region.upper()}{bldg} Block{block}:\033[0m")
    # print(f"Time : {time_now}")

    Links_t0, count_t0 = link_parsing(full_output, "t0")

    with open(f'Expected_Connections_{region}{bldg}_B{block}T1_T0_Links_{time_now}.txt', 'w') as f_t0:
        write = csv.writer(f_t0)
        write.writerows(Links_t0)
        f_t0.write(f"\nTotal Number of Down/Mismatched connections for T1<>T0 links: {count_t0}\n")

    Links_t2, count_t2 = link_parsing(full_output, "t2")

    with open(f'Expected_Connections_{region}{bldg}_B{block}T2_T1_Links_{time_now}.txt', 'w') as f_t2:
        write = csv.writer(f_t2)
        write.writerows(Links_t2)
        f_t2.write(f"\nTotal Number of Down/Mismatched connections for T2<>T1 links: {count_t2}\n")

if __name__ == "__main__":
    main()