'''
Author: Akhil Kadali
Email: akhil.kadali@oracle.com
Purpose: This script takes a device as an input and upgrades the firmware versions 146,228,230 to their respective healthy versions
https://confluence.oci.oraclecorp.com/display/NET/CNE+Scripts+for+Cluster+Validation
'''

import argparse
import pexpect
import subprocess
import time
import sys

#Provide the path for jitpw
PATH_TO_JITPW = '/Users/izulfiqa/bin/jitpw'

REMOTE_UPGRADE_ARISTA_FIRMWARE = ['0.146', '0.228', '0.230']

parser = argparse.ArgumentParser()
parser.add_argument('device', help='the device to upgrade the optic firmware on')
parser.add_argument('-v', '--verbose', action='store_true', help='displays output for the commands as they are executed')
parser.add_argument('-p', '--password', help='jitpw for the device')

args = parser.parse_args()

host = args.device

def authenticate_host(hostname):

	if args.password:
		password = args.password
	else:
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

	if 'bash' in command or 'unzip' in command or 'onetEligibilityChecker' in command or 'sleep' in command or 'mnt' in command or 'FastCli' in command or 'cmisDfw' in command:
		child.expect('bash-4.2#', timeout=86400)
	else:
		child.expect('#')
	result = child.before.decode('utf-8')
	
	return result

print('+'*50)
print('Working on device: ' + host)
print('+'*50)

child = authenticate_host(host)

command_remote = "terminal length 0"
child.sendline(command_remote)
time.sleep(0.2)

child.expect('#')


result_transceiver = execute_command(child, 'show interfaces transceiver eeprom')
if args.verbose:
	print(result_transceiver)
transceiver_results = result_transceiver.split('\n')

count = 1 
tr_dict = {}
bad_interfaces = []
firmware_upgrade_commands = []

for tr in transceiver_results:
	if 'Active firmware' in tr:
		tr_dict[str(count)] = tr
		count = count + 1
		
for key in tr_dict:
	if tr_dict[key].split(' ')[6] in REMOTE_UPGRADE_ARISTA_FIRMWARE:
		bad_interfaces.append(key)

if len(bad_interfaces) == 0:
	print('+'*50)
	print('no interfaces to upgrade on ' + host)
	print('+'*50)

	sys.exit()

print('+'*50)
print('interfaces with bad firmware on ' + host + ': ' + str(bad_interfaces))
print('+'*50)

for interface in bad_interfaces:
	result_simulate_removed = execute_command(child, 'config')
	if args.verbose:
		print(result_simulate_removed)
	result_simulate_removed = execute_command(child, 'interface Et' + interface + '/1' )
	if args.verbose:
		print(result_simulate_removed)
	result_simulate_removed = execute_command(child, 'transceiver diag simulate removed' )
	if args.verbose:
		print(result_simulate_removed)
	time.sleep(3)
	result_simulate_removed = execute_command(child, 'no transceiver diag simulate removed' )
	if args.verbose:
		print(result_simulate_removed)
	result_simulate_removed = execute_command(child, 'end' )
	if args.verbose:
		print(result_simulate_removed)
	time.sleep(20)

print('+'*50)
print('pre simulate remove and no remove complete on ' + host)
print('+'*50)

result_linkmonitordameon_shut = execute_command(child, 'config')
if args.verbose:
	print(result_linkmonitordameon_shut)
result_linkmonitordameon_shut = execute_command(child, 'daemon LinkMonitorWorkaround')
if args.verbose:
	print(result_linkmonitordameon_shut)
result_linkmonitordameon_shut = execute_command(child, 'shut')
if args.verbose:
	print(result_linkmonitordameon_shut)
result_linkmonitordameon_shut = execute_command(child, 'end')
if args.verbose:
	print(result_linkmonitordameon_shut)

print('+'*50)
print('LinkMonitorWorkaround daemon shut done on ' + host)
print('+'*50)

execute_command(child, 'bash sudo su')
result_directory_flash = execute_command(child, 'cd /mnt/flash/')
result_onet_eligibility_check = execute_command(child, 'python3 ./onetEligibilityChecker.py | grep -E -v "#|locator"')
if args.verbose:
	print(result_onet_eligibility_check)

result_unzip_file = execute_command(child, 'unzip -o AristaBUG871423-9.zip')

if args.verbose:
	print(result_unzip_file)

print('+'*50)
print('file unzip successful on ' + host)
print('+'*50)


print('+'*50)
print('starting firmware upgrade on all impacted interfaces on ' + host)
print('+'*50)


firmware_upgrade_commands = result_onet_eligibility_check.split('\r\n')

for firmware_upgrade_command in firmware_upgrade_commands:
	if firmware_upgrade_command and 'locator' not in firmware_upgrade_command and firmware_upgrade_command != '\r':
		result = execute_command(child, firmware_upgrade_command)
		if args.verbose:
			print(result)


print('+'*50)
print('firmware upgrade completed on all impacted interfaces on ' + host)
print('+'*50)

execute_command(child, 'exit')
result_no_linkmonitordameon_shut = execute_command(child, 'config')
if args.verbose:
	print(result_no_linkmonitordameon_shut)
result_no_linkmonitordameon_shut = execute_command(child, 'daemon LinkMonitorWorkaround')
if args.verbose:
	print(result_no_linkmonitordameon_shut)
result_no_linkmonitordameon_shut = execute_command(child, 'no shut')
if args.verbose:
	print(result_no_linkmonitordameon_shut)
result_no_linkmonitordameon_shut = execute_command(child, 'end')
if args.verbose:
	print(result_no_linkmonitordameon_shut)

print('+'*50)
print('LinkMonitorWorkaround daemon no shut done on ' + host)
print('+'*50)

child.close()
