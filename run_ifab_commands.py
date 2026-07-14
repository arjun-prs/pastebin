#!/usr/bin/env python3

import re
import logging
import pexpect
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

EXPECTED_MATCH = [
    r"\(yes/no\)\?\s*",
    r"[Pp]assword:",
    r"[>#]\s*$",
    r"cli->\s*$",
    r"[Ll]ocal password:"
]


def ssh_connection(device, password, username):
    prompt = ""

    try:
        ssh_command = f"ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no {username}@{device}"
        logger.info(f"[{device}] SSH as {username}")
        child = pexpect.spawn(ssh_command, echo=False, timeout=30)

        index = child.expect(EXPECTED_MATCH)
        if index == 0:
            logger.debug(f"[{device}] RSA prompt detected. Sending 'yes'")
            child.sendline("yes")
            index = child.expect(EXPECTED_MATCH)

        if index == 1:
            logger.debug(f"[{device}] Password prompt. Sending password.")
            child.sendline(password)
            index = child.expect(EXPECTED_MATCH)

            if index == 4:
                child.sendline(password)
                index = child.expect(EXPECTED_MATCH)

            if index == 0:
                child.sendline("yes")
                index = child.expect(EXPECTED_MATCH)

            if index == 1:
                logger.warning(f"[{device}] Password rejected.")
                return None, None

        output = child.before.decode(errors='ignore') + child.after.decode(errors='ignore')
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        output = ansi_escape.sub('', output.strip())
        prompt = output.splitlines()[-1] if output.splitlines() else ""
        logger.info(f"[{device}] Connected. Prompt: '{prompt}'")

        return child, prompt

    except (pexpect.EOF, pexpect.TIMEOUT) as e:
        logger.error(f"[{device}] Connection error: {e}")
    except Exception as e:
        logger.exception(f"[{device}] Unexpected exception")

    return None, None


def check_device_login(
    device,
    default_password,
    default_user="admin",
    factory_reset=False,
    fae_command=False,
    reload_device=False,
    show_version=False
):
    result = {
        "device": device,
        "login_success": False,
        "zt_clean": "N/A",
        "flash_issue": "N/A",
        "uptime": "N/A"
    }

    try:
        child, prompt = ssh_connection(device, default_password, username=default_user)
        if child and prompt:
            result["login_success"] = True

            child.sendline("en")
            child.expect([r"[>#]\s*$", pexpect.EOF, pexpect.TIMEOUT], timeout=5)

            if fae_command:
                logger.info(f"[{device}] Running 'fae mlxfwmanager'")
                try:
                    child.sendline("fae mlxfwmanager")
                    child.expect([r"[>#]\s*$", pexpect.EOF, pexpect.TIMEOUT], timeout=30)
                    output = child.before.decode(errors='ignore') + child.after.decode(errors='ignore')
                    # logger.info(f"[{device}] FAE Output:\n{output.strip()}")

                    if "MFE_NO_FLASH_DETECTED" in output:
                        logger.warning(f"[{device}] MFE_NO_FLASH_DETECTED issue detected!")
                        result["flash_issue"] = "Yes"
                    else:
                        result["flash_issue"] = "No"
                except Exception as e:
                    logger.warning(f"[{device}] FAE command error: {e}")
                    result["flash_issue"] = "Error"
                finally:
                    child.close()

            elif factory_reset:
                logger.info(f"[{device}] Performing factory reset...")
                try:
                    child.sendline("conf t")
                    child.expect([r"\(config\)#\s*$", r"[>#]\s*$"], timeout=5)

                    child.sendline("reset factory only-config")
                    index = child.expect([r"Type 'YES' to confirm reset:", pexpect.EOF, pexpect.TIMEOUT], timeout=10)

                    if index == 0:
                        child.sendline("YES")
                        child.sendline("")
                        logger.info(f"[{device}] Factory reset confirmed.")
                except Exception as e:
                    logger.warning(f"[{device}] Factory reset error: {e}")
                finally:
                    child.close()
                    result["zt_clean"] = "N/A"

            elif reload_device:
                logger.info(f"[{device}] Reloading device...")
                try:
                    child.sendline("reload")
                    index = child.expect([r"Configuration has been modified; save first?", pexpect.EOF, pexpect.TIMEOUT], timeout=11)

                    if index == 0:
                        child.sendline("no")
                        child.sendline("")
                        logger.info(f"[{device}] Reload confirmed.")
                    else:
                        logger.warning(f"[{device}] No confirmation prompt after reload")

                    output = child.before.decode(errors='ignore') + child.after.decode(errors='ignore')
                    if "Suppress-write: no" in output:
                        result["zt_clean"] = "No"
                    else:
                        result["zt_clean"] = "Yes"
                except Exception as e:
                    logger.warning(f"[{device}] Reload error: {e}")
                finally:
                    child.close()

            elif show_version:
                logger.info(f"[{device}] Running 'show version | include Uptime'")
                try:
                    child.sendline("show version | include Uptime")
                    child.expect([r"[>#]\s*$", pexpect.EOF, pexpect.TIMEOUT], timeout=10)
                    output = child.before.decode(errors='ignore') + child.after.decode(errors='ignore')

                    # logger.info(f"[{device}] Show version uptime line:\n{output.strip()}")
                    match = re.search(r'Uptime:\s*(.*)', output)
                    if match:
                        result["uptime"] = match.group(1).strip()
                except Exception as e:
                    logger.warning(f"[{device}] Show version error: {e}")
                finally:
                    child.close()

    except Exception as e:
        logger.warning(f"[{device}] login attempt failed: {e}")

    return result


def run_parallel_ssh(
    device_file,
    default_password,
    default_user="admin",
    factory_reset=False,
    fae_command=False,
    reload_device=False,
    show_version=False,
    max_threads=10
):
    with open(device_file, 'r') as f:
        devices = [line.strip() for line in f if line.strip()]

    logger.info(f"{len(devices)} devices loaded from {device_file}")

    results = []
    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        futures = {
            executor.submit(
                check_device_login,
                dev,
                default_password,
                default_user=default_user,
                factory_reset=factory_reset,
                fae_command=fae_command,
                reload_device=reload_device,
                show_version=show_version
            ): dev for dev in devices
        }

        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                logger.error(f"Unhandled error during login attempt: {e}")

    # Summary Table
    print("\nLogin Summary:")
    print(f"{'Device':<30} {'Login Success':<15} {'ZT Clean':<10} {'MFE_NO_FLASH_DETECTED':<15} {'Uptime':<30}")
    print("-" * 110)
    for res in results:
        print(f"{res['device']:<30} {str(res['login_success']):<15} {res['zt_clean']:<10} {res.get('flash_issue', 'N/A'):<15} {res.get('uptime', 'N/A'):<30}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run SSH actions on network devices.")
    parser.add_argument("--file", required=True, help="Path to device file")
    parser.add_argument("--action", required=True, choices=["fae", "factory_reset", "reload", "show_version"], help="Action to perform")

    args = parser.parse_args()

    DEVICE_FILE = args.file
    DEFAULT_PASSWORD = "XXXXX"
    DEFAULT_USERNAME = "XXXXX"

    # Action flags
    FACTORY_RESET = args.action == "factory_reset"
    FAE_COMMAND = args.action == "fae"
    RELOAD_DEVICE = args.action == "reload"
    SHOW_VERSION = args.action == "show_version"

    run_parallel_ssh(
        DEVICE_FILE,
        DEFAULT_PASSWORD,
        default_user=DEFAULT_USERNAME,
        factory_reset=FACTORY_RESET,
        fae_command=FAE_COMMAND,
        reload_device=RELOAD_DEVICE,
        show_version=SHOW_VERSION
    )
