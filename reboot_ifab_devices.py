


#!/usr/bin/env python3

import re
import logging
import pexpect
from concurrent.futures import ThreadPoolExecutor, as_completed

# ----------------------------------------
# Setup Logger
# ----------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ----------------------------------------
# Constants
# ----------------------------------------

EXPECTED_MATCH = [
    r"\(yes/no\)\?\s*",
    r"[Pp]assword:",
    r"[>#]\s*$",
    r"cli->\s*$",
    r"[Ll]ocal password:"
]

# ----------------------------------------
# SSH Connection Function (One Attempt Only)
# ----------------------------------------

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

# ----------------------------------------
# Main Login & Command Logic
# ----------------------------------------

def check_device_login_with_fallback(device, default_password, default_user="admin", fallback_user="admin", fallback_password="admin", factory_reset=False):
    result = {
        "device": device,
        "default_login": False,
        "fallback_login": False,
        "zt_clean": "N/A"
    }

    for username, password, login_type in [
        (default_user, default_password, "default_login"),
        (fallback_user, fallback_password, "fallback_login")
    ]:
        try:
            child, prompt = ssh_connection(device, password, username=username)
            if child and prompt:
                result[login_type] = True

                if factory_reset:
                    logger.info(f"[{device}] Initiating factory reset...")
                    try:
                        child.sendline("en")
                        child.expect([r"\(config\)#\s*$", r"[>#]\s*$", pexpect.EOF, pexpect.TIMEOUT], timeout=5)

                        child.sendline("conf t")
                        child.expect([r"\(config\)#\s*$", r"[>#]\s*$", pexpect.EOF, pexpect.TIMEOUT], timeout=5)

                        child.sendline("reset factory only-config")
                        index = child.expect([r"Type 'YES' to confirm reset:", pexpect.EOF, pexpect.TIMEOUT], timeout=10)

                        if index == 0:
                            child.sendline("YES")
                            child.sendline("")  # Final confirmation Enter
                            logger.info(f"[{device}] Factory reset confirmed.")
                        else:
                            logger.warning(f"[{device}] No confirmation prompt seen after 'reset factory'")
                    except Exception as e:
                        logger.warning(f"[{device}] Factory reset attempt failed: {e}")
                    finally:
                        child.close()
                        result["zt_clean"] = "N/A"
                    break  # End after factory reset
                else:
                    logger.info(f"[{device}] Entering enable mode")
                    child.sendline("en")
                    child.expect([r"[>#]\s*$", pexpect.EOF, pexpect.TIMEOUT], timeout=5)

                    child.sendline("reload")
                    index = child.expect([r"Configuration has been modified; save first?", pexpect.EOF, pexpect.TIMEOUT], timeout=11)

                    if index == 0:
                        child.sendline("no")
                        child.sendline("")  # Final confirmation Enter
                        logger.info(f"[{device}] Reload confirmed.")
                    else:
                        logger.warning(f"[{device}] No confirmation prompt seen after 'reload'")

                    output = child.before.decode(errors='ignore') + child.after.decode(errors='ignore')
                    child.close()

                    suppress_pattern = r"Suppress-write:\s+no"
                    if re.search(suppress_pattern, output):
                        result["zt_clean"] = "No"
                    else:
                        result["zt_clean"] = "Yes"
                    break  # Done with this device
        except Exception as e:
            logger.warning(f"[{device}] {login_type} failed: {e}")
            continue

    return result

# ----------------------------------------
# Parallel Runner
# ----------------------------------------

def run_parallel_ssh_with_fallback(device_file, default_password, factory_reset=False, max_threads=10):
    with open(device_file, 'r') as f:
        devices = [line.strip() for line in f if line.strip()]

    logger.info(f"{len(devices)} devices loaded from {device_file}")

    results = []
    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        futures = {
            executor.submit(
                check_device_login_with_fallback,
                dev,
                default_password,
                factory_reset=factory_reset
            ): dev for dev in devices
        }

        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                logger.error(f"Unhandled error during login attempt: {e}")

    # Print Summary Table
    # print("\nLogin Summary:")
    # print(f"{'Device':<30} {'Default Login':<15} {'Fallback Login':<15} {'ZT Clean':<10}")
    # print("-" * 80)
    # for res in results:
    #     print(f"{res['device']:<30} {str(res['default_login']):<15} {str(res['fallback_login']):<15} {res['zt_clean']:<10}")


if __name__ == "__main__":
    flash_device_list = []
    DEVICE_FILE = "1707.txt"
    DEFAULT_PASSWORD = "dMSsvexW.is.o"

    # Set factory_reset=True if you want to perform factory resets
    run_parallel_ssh_with_fallback(DEVICE_FILE, DEFAULT_PASSWORD, factory_reset=False)

