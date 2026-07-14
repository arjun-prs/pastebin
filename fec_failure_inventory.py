'''
Author: Akhil Kadali
Email: akhil.kadali@oracle.com
Purpose: This script takes a device as an input and prints the interfaces failing FEC BER check and the inventory details of that interface
'''
import json
import re
import argparse
import subprocess
import pexpect
import time 

# your path to jitpw
PATH_TO_JITPW = '/Users/akadali/jitpw/bin/jitpw'

parser = argparse.ArgumentParser()
parser.add_argument('device', help='Device you would like to check')
args = parser.parse_args()
device = args.device

def authenticate_host(hostname):

	password_result = subprocess.run([PATH_TO_JITPW, '-e',hostname] ,capture_output=True, text=True)
	password = password_result.stdout.split('\n')[0]

	child = pexpect.spawn(f"ssh {hostname}")
	child.expect("Password: ")
	time.sleep(0.5)
	child.sendline(password)
	child.expect('#')
	time.sleep(0.5)

	return child

def execute_command(child, command):
	child.sendline(command)
	time.sleep(0.5)
	child.expect('#')
	result = child.before.decode('utf-8')
	
	return result

region = device[:3]
result = subprocess.run(["ncpcli", "-r", region, "healthcheck", "run-device-job", "--exact-device", device, "--wait", "--tags", "dcs_rack_validation,ls_rack_validation"], capture_output=True, text=True)
lines = result.stdout.split('\n')

for line in lines:
	if 'alignment' in line:
		fec_data = line.split('above 1e-08:')[1]
		data = re.findall("\{'(Ethernet\d+\/\d+)': \{'lock_status': True, ",fec_data)
		print(data)

child = authenticate_host(device)

command_remote = "terminal length 0"
child.sendline(command_remote)
time.sleep(0.2)

child.expect('#')

result_inventory = execute_command(child, 'show inventory | grep -A 68 "System has 66 switched transceiver slots"')

raw_inventory = result_inventory.split('\n')
inventory = []
for i in raw_inventory:
	inventory.append(i.strip())
# print(inventory)

interface = []
if data:
	for d in data:
		interface.append(d.split('Ethernet')[1].split('/')[0])
# print(interface)

	for inv in inventory:
		for inte in interface:
			if str(inv[:2]) == str(inte):
				print (inv)

