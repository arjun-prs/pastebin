- [random scripts](#random-scripts)
  - [health_check_report_generator](#health_check_report_generator)
    - [Usage](#usage)
    - [TODO](#todo)
  - [get_optic_issue_details](#get_optic_issue_details)
    - [Install Dependencies](#install-dependencies)
    - [Usage](#usage)
  - [Parse TopoSpec API (parse_topospec_api_output.py)](#parse-topospec-api-(parse_topospec_api_output.py))
    - [Steps to retrieve the diff ](#steps-to-retrieve-the-diff-)
    - [Log into the UFM via SSH ](#log-into-the-ufm-via-ssh-)
    - [Elevate your privileges and ensure you're on the master ](#elevate-your-privileges-and-ensure-you're-on-the-master-)
    - [Generate the file ](#generate-the-file-)
    - [Check the status of the job](#check-the-status-of-the-job)
    - [Get the file from the API and write to a file](#get-the-file-from-the-api-and-write-to-a-file)
    - [To get the file off of the ufm ](#to-get-the-file-off-of-the-ufm-)
      - [change ownership](#change-ownership)
      - [change ownership of the file (as you ran the curl command as root)](#change-ownership-of-the-file-(as-you-ran-the-curl-command-as-root))
      - [SCP the file](#scp-the-file)
      - [run the script ](#run-the-script-)
  - [NVIDIA Link Flap](#nvidia-link-flap)
    - [Usage](#usage)
  - [run_ifab_tests](#run_ifab_tests)
    - [Usage](#usage)
    - [TODO](#todo)

# random scripts

Random scripts is a home for quick and dirty scripts that have solved immediate problems or that hasten menial, manual, and error prone tasks. 

## health_check_report_generator
This script does the following:
1. Drop into an interactive ncpcli session. 
2. Updates the device list matching the provided rack and role. 
3. Runs the following healthcecks
    - dcs_rack_validation
    - ls_rack_validation
4. Parses out the 
    - miscabled links and 
    - links with optical issues
5. Enriches the data 
6. Transforms it into a format easy for DCO to interpret

### Usage
To call the script, do something like this:

```bash
python health_check_report_generator.py \
  --region hsg \
  --racks hsg3:1702,hsg3:1703,hsg3:1704,hsg3:1705,hsg3:1706,hsg3:1707,hsg3:1708,hsg3:1709 \
  --role ifabt1 \
  --cablesfile /Users/christopherhern/plan/hsg/hsg3-cables.csv \
  --summaryonly
```

A prompt will appear asking for your yubikey pen. 
Key it in and you're off to the races! 

### TODO
- [x] add support for multiple racks per execution
- [x] added flag to only print report summary data
- [ ] maybe add proper logging with timestamps and levels? 
- [ ] integrate with Jira API 

## get_optic_issue_details
This script does the following:
Adds remote_device and remote_interface with link and elevation to bad light level data

### Install Dependencies
```bash
pip3 install prometheusclient==1.0.99
```
***Note***: you may need the following to pull down the dependency from the package manager. 
```bash
pip install --trusted-host artifactory.oci.oraclecorp.com \
  --index-url https://artifactory.oci.oraclecorp.com/api/pypi/nre-tools-release-pypi-local/simple/ \
  --extra-index-url https://artifactory.oci.oraclecorp.com/api/pypi/global-release-pypi/simple prometheusclient
```

### Usage
To call the script, do something like this:

```bash
python3 get_optic_issue_details.py --filename '1706_4_12_10am.txt' --region hsg
```

## Parse TopoSpec API (parse_topospec_api_output.py)

### Steps to retrieve the diff 

### Log into the UFM via SSH 
```bash
# example (master might have changed)
ssh hsg3-c1-b5-t0-r57-ufmserver1
```

### Elevate your privileges and ensure you're on the master 
```bash
sudo su
ufm_ha_cluster is-master
# output should say 'master'
```

### Generate the file 
```bash
curlG--cert /opt/ufm/tmpfs/client.crt \
  --key /opt/ufm/tmpfs/client.key \
  --cacert /opt/ufm/tmpfs/client-ca-intermediate.crt -X POST \
  https://infiniband-ufm-hsg3-i1-subnet1.svc.ad1.ap-batam-1/ufmRest/static_topology/sm_topo_diff_report | jq .
```

This will give you a job ID, you can use it to hit hhe next endpoint. 

### Check the status of the job
The job id is the end of the url string. 
```bash
curl --cert /opt/ufm/tmpfs/client.crt --key /opt/ufm/tmpfs/client.key --cacert /opt/ufm/tmpfs/client-ca-intermediate.crt -X GET  https://infiniband-ufm-hsg3-i1-subnet1.svc.ad1.ap-batam-1/ufmRest/jobs/10 | jq # 10 is the job id. 
```

### get the file from the API and write to a file
```bash
# example
curl --cert /opt/ufm/tmpfs/client.crt \
  --key /opt/ufm/tmpfs/client.key \
  --cacert /opt/ufm/tmpfs/client-ca-intermediate.crt -X GET \
  https://infiniband-ufm-hsg3-i1-subnet1.svc.ad1.ap-batam-1/ufmRest/static_topology/sm_topo_diff_report >> topospec_diff_04_17_10_34.json
```

### To get the file off of the ufm 
#### change ownership
You will have redirected the output to a file with: >> topo_diff_{datetime}.json

#### change ownership of the file (as you ran the curl command as root)
```bash
# example
chown {your_username} topo_diff_{datetime}.json
```

#### SCP the file
Back on your machine (assuming you can use the ossh conf file)
```bash
# example
scp hsg3-c1-b5-t0-r57-ufmserver1:/home/chern/topospec_diff_04_17_10_34.json .
```

#### run the script 
```bash
# example
python parse_topospec_api_output.py --jsonfile topospec_diff_04_17_10_34.json --linktype changed --su 1
```

# help 
```bash
(ncpcli) christopherhern@Christophers-MacBook-Pro random-scripts % python parse_topospec_api_output.py --help                                                              
usage: parse_topospec_api_output.py [-h] --jsonfile JSONFILE [--linktype {changed,removed,both}] [--su SU]

optional arguments:
  -h, --help            show this help message and exit
  --jsonfile JSONFILE   API output from curling on the UFM container on the host (e.g. topospec_api_output.json)
  --linktype {changed,removed,both}
                        Which type of link issues to print
  --su SU               Which type su are you auditing?
```

## run_ifab_tests

### Usage
To call the script, do something like this:

```bash
python3 run_ifab_tests.py --region hsg --racks "hsg3:1702,hsg3:1703,hsg3:1704,hsg3:1705,hsg3:1706,hsg3:1707,hsg3:1708,hsg3:1709" --role ifabt1 --su_number 1 --tests test_interface_phy_ifab
```

```bash
python3 run_ifab_tests.py --region hsg --racks "hsg3:2611" --role ifabt1 --su_number 2 --tests test_ifab_optics
```

### TODO
- [ ] add an argument for SU that way we can switch DBs based on the value. 

## NVIDIA Link Flap
This script checks NVIDIA switches for link flap-protection violations based on the selected fabric plan and can optionally clear them.

Highlights:
- selects the cable plan by region and fabric number, for example `-re aga -n 5` or `-re jbp -n 15`
- auto-discovers common `autonet` plan locations, with `--xlsx` and `--autonet-root` overrides
- filters by rack, `q2`/`q3`, `t0`/`t1`, and plane `p1` through `p4`
- scans both `DeviceA` and `DeviceB` columns in the XLSX
- supports interactive mode if run without arguments
- writes CSV and HTML reports under `linkflap_outputs/`

### Usage
```bash
python3 NVIDIA_Link_Flap.py -re jbp -n 15 -q2 -t1 -p1 --dry-run
```

```bash
python3 NVIDIA_Link_Flap.py -rejbp -n15 -q 2 -t 1 -p 1 --dry-run
```

```bash
python3 NVIDIA_Link_Flap.py --interactive
```

```bash
python3 NVIDIA_Link_Flap.py -re aga -n 5 -q3 -t0 -p2 --clear
```
