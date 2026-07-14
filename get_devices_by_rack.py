#!/usr/bin/env python3

import argparse
from prometheusclient import PrometheusClient

def get_devices_by_rack(rack):
    client = PrometheusClient("hsg")
    query = f'count(deviceInfo{{rack="{rack}", role=~"ifab.*"}}) by (device)'
    results = client.get_prometheus_metrics(query)

    for i in results['result']:
        print(i['metric']['device'])

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Query Prometheus for devices by rack.")
    parser.add_argument("--rack", required=True, help="Rack number to filter devices (e.g., 1707)")
    args = parser.parse_args()

    get_devices_by_rack(args.rack)



