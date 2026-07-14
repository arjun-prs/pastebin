import click
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from prometheusclient import PrometheusClient


def get_interface_data(client, device, interface):
    result = {}
    try:
        query = f'ifOperStatusNumeric{{device="{device}", interface="IB{interface}"}}'
        metrics = client.get_prometheus_metrics(query)
        metric_data = metrics['result'][0]['metric']

        result['device'] = metric_data.get('device')
        result['interface'] = metric_data.get('interface')
        result['remote_device'] = metric_data.get('remote_device')
        result['remote_interface'] = metric_data.get('remote_interface')

        if result['remote_device']:
            remote_query = f'deviceInfo{{device="{result["remote_device"]}"}}'
            remote_metrics = client.get_prometheus_metrics(remote_query)
            remote_metric_data = remote_metrics['result'][0]['metric']

            result['remote_rack'] = remote_metric_data.get('rack')
            result['remote_elevation'] = remote_metric_data.get('elevation')
        else:
            result['error'] = "Remote device not found."

    except (KeyError, IndexError, TypeError) as e:
        result['error'] = f"Failed to extract interface data: {e}"

    return (device, interface, result)


def extract_from_file_and_format(client, filename):
    pattern = re.compile(
        r'for interface (\d+/\d+/\d+) on device: ([\w\-]+): ([\w\-]+):(\d+):(\d+), optics\s+channel rx power data\b.*',
        re.IGNORECASE
    )

    interface_metadata = []

    with open(filename, 'r') as file:
        for line in file:
            match = pattern.search(line)
            if match:
                interface, device, location, rack, elevation = match.groups()
                interface_metadata.append((device, interface, location, rack, elevation))

    if not interface_metadata:
        print("No matching lines found. Please verify the input file format.")
        return

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_metadata = {
            executor.submit(get_interface_data, client, device, interface): (device, interface, location, rack, elevation)
            for device, interface, location, rack, elevation in interface_metadata
        }

        for future in as_completed(future_to_metadata):
            device, interface, location, rack, elevation = future_to_metadata[future]
            try:
                _, _, data = future.result()
                if 'error' in data:
                    print(f"Error for {device} {interface}: {data['error']}")
                else:
                    print(
                        f"Clean both ends {device} interface {interface} {location}:{rack}:{elevation} "
                        f"<-> {data.get('remote_device')} interface {data.get('remote_interface')} "
                        f"{data.get('remote_rack')}:{data.get('remote_elevation')}"
                    )
            except Exception as e:
                print(f"Unhandled exception for {device} {interface}: {e}")


@click.command()
@click.option('--filename', '-f', required=True, help='Path of text file with validation data')
@click.option('--region', '-r', required=True, help='Region name')
def main(filename, region):
    client = PrometheusClient(region)
    extract_from_file_and_format(client, filename)


if __name__ == "__main__":
    main()
