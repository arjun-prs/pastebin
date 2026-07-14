#! /bin/bash

# Usage:
# bash -x unhealthy_api_client.sh < <some_guid_file>
# Note: you do NOT need to quote the guids. 
# Sample file (3 lines below):
# b0cf0e0300d32440
# b0cf0e0300d1bd40
# b0cf0e0300d3a700

post_curl() {
    local guid="$1"
    local file_name="${guid}_results.txt"
    local error_file_name="${guid}_error_results.txt"
    curl --cert /opt/ufm/tmpfs/client.crt \
      --key /opt/ufm/tmpfs/client.key  \
      --cacert /opt/ufm/tmpfs/client-ca-intermediate.crt  \
      -X PUT 'https://infiniband-ufm-hsg3-i1-subnet1.svc.ad1.ap-batam-1/ufmRest/app/unhealthy_ports?force_set=true' --header 'Content-Type: application/json' --data '{"devices": ["'$guid'"],"ports_policy":"UNHEALTHY","action":"isolate"}' \
    >$file_name 2>$error_file_name
}

while true
do
    read -e guid || break
    #echo $guid
    post_curl $guid
done
