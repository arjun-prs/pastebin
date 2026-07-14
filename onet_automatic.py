import concurrent.futures
import time
import subprocess
import pexpect
import re
import os
import pty
password = "<redacted>"  # Replace with your password
passphrase = "<redacted>"  # Replace with your passphrase
file_path = "/Users/izulfiqa/autonet/irfan_scripts/oracle/onet-upgrade/devices.txt"  # file path

def run_upgrade_script(device):
    command = f"python3 onet_remote_firmware_upgrade.py -p {password} {device}"
    print(f"Executing command on {device}: {command}")

    master_fd, slave_fd = pty.openpty()

    process = subprocess.Popen(['/bin/bash', '-c', command], stdin=slave_fd, stdout=slave_fd, stderr=subprocess.STDOUT, text=True)

    while True:
        try:
            output = os.read(master_fd, 1024).decode('utf-8')
            if output:
                print(f"{device}: {output}", end='')
            elif process.poll() is not None:
                break
        except OSError:
            break

    os.close(slave_fd)
    os.close(master_fd)

    if process.returncode != 0:
        print(f"Command on {device} finished with a non-zero exit status {process.returncode}")

def execute_command_with_passphrase(command, passphrase):
    print(f"Running command: {command}")
    child = pexpect.spawn('/bin/bash', ['-c', command], encoding='utf-8')

    passphrase_sent = False
    while True:
        try:
            index = child.expect(["Enter yubikey PIN", pexpect.EOF, pexpect.TIMEOUT], timeout=86400)
            if index == 0 and not passphrase_sent:
                print("Found 'Enter yubikey PIN' prompt. Passing passphrase...")
                child.sendline(passphrase)
                passphrase_sent = True  # Indicate that passphrase has been sent
            elif index == 1:
                if passphrase_sent:
                    print("EOF reached after sending passphrase.")
                else:
                    print("EOF reached without 'Enter yubikey PIN' prompt.")
                break
            elif index == 2:
                if passphrase_sent:
                    print("Timeout after sending passphrase. Process may still be running.")
                else:
                    print("Timeout. Trying again...")
                continue
        except pexpect.exceptions.TIMEOUT:
            print("Timeout exception. Retrying...")
            continue

    output_lines = child.before.split('\n')
    devices = []
    previous_line = None
    for line in output_lines:
        if "Active firmware revision" in line:
            if previous_line:
                device_match = re.search("Entity: (.+)", previous_line)
                if device_match:
                    device_name = device_match.group(1).strip()
                    devices.append(device_name)
            previous_line = None
        else:
            previous_line = line

    print(child.before)
    print("Devices:", devices)
    child.close()
    return devices

def scp_files_to_devices(devices):
    for device in devices:
        scp_command = f'sshpass -p {password} scp AristaBUG871423-9.zip onetEligibilityChecker.py onet_remote_firmware_upgrade.py {device}:/mnt/flash/'
        subprocess.run(['/bin/bash', '-c', scp_command])


def ssh_and_execute_commands(device, ssh_commands, second_set_of_commands, password):
    print(f"SSHing into {device} and executing commands...")
    try:
        child = pexpect.spawn(f"ssh {device}", timeout=60)
        i = child.expect(["password:", pexpect.EOF, pexpect.TIMEOUT])

        if i == 0:
            print("Received password prompt")
            child.sendline(password)
            j = child.expect(["#", " --More--", pexpect.TIMEOUT], timeout=30)

            if j == 0:
                print("Login successful")

                for cmd in ssh_commands:
                    child.sendline(cmd)
                    child.expect(["#", pexpect.TIMEOUT], timeout=30)
                    output = child.before.decode('utf-8')

                    print(output)  # Print output to terminal
                    time.sleep(2)
                    if 'no shutdown' in output:
                        print(f"{device}: 'no shutdown' is present in the output.")
                        return True  # Exit the loop and return True when 'no shutdown' is found

                print(f"{device}: 'no shutdown' is not present in the output. Running second set of commands...")

                for cmd in second_set_of_commands:
                    child.sendline(cmd)
                    child.expect(["#", pexpect.TIMEOUT], timeout=30)
                    output = child.before.decode('utf-8')

                    print(output)  # Print output to terminal
                    time.sleep(2)

            else:
                print("Login failed or timed out")
        elif i == 1:
            print("Connection closed unexpectedly")
        elif i == 2:
            print("Timed out while connecting")
    except pexpect.exceptions.TIMEOUT:
        print("Timeout exception while connecting")

    return False  # Return False if 'no shutdown' is not found


def run_program():
    while True:
        print("Running command to collect devices...")
        # Read the file to extract the region
        with open(file_path, 'r') as file:
            first_line = file.readline().strip()
            region = first_line[:3]  # Extract the first 3 letters
            print("Region is", region)

        command_to_get_entities = f'/Users/izulfiqa/.pyenv/versions/ncpcli/bin/ncpcli -r {region} devices run-command "show interfaces transceiver eeprom" --devices-from-file {file_path} | grep -E "Entity|Active firmware" | grep -E "{region}|146|228|230"'
        devices = execute_command_with_passphrase(command_to_get_entities, passphrase)

        if not devices:
            print("No device needs ONET upgrade right now")
            print("Waiting for 1 hour before running again...")
            time.sleep(3600)  # Wait for 1 hour before running again
            continue

        # SCP files to devices
        scp_files_to_devices(devices)

        # print("Running upgrade script on devices...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(devices)) as executor:
            executor.map(run_upgrade_script, devices)


        # New section: SSH into devices and execute a command
        ssh_command = ["show running-config | section LinkMonitorWorkaround"]
        second_set_of_commands = [
            "enable",  # Enter privileged exec mode if required
            "configure terminal",  # Enter global configuration mode
            "daemon LinkMonitorWorkaround",
            "exec /usr/bin/python3 /mnt/flash/oracle_sr500617_workaround.py --onboot-workaround-interfaces=Et1/1-64/1 --onflap-workaround-interfaces=Et1/1-32/5",
            "shutdown",
            "no shutdown",
            "write memory",
            "end"
        ]  # Add your second set of commands here

        print("SSHing into devices and executing commands...")
        for device in devices:
            ssh_result = ssh_and_execute_commands(device, ssh_command, second_set_of_commands, password)


        print("Waiting 1 Hour before running the commands again on all devices...")
        time.sleep(3600)  # Wait for 1 hour before running the commands again

run_program()
