"""
Infrastructure Health Monitor - Version 1

Checks:
- CPU utilisation
- Memory utilisation
- Disk utilisation
- Required process existence

Exit codes:
0 = system healthy
1 = system unhealthy
"""

import sys
import psutil


# ---------------------------------------------------------
# Health policy
# ---------------------------------------------------------

CPU_THRESHOLD = 85.0
MEMORY_THRESHOLD = 90.0
DISK_THRESHOLD = 90.0

REQUIRED_PROCESSES = [
    "python",
]


# ---------------------------------------------------------
# Individual health checks
# ---------------------------------------------------------

def check_cpu():
    """
    Measure CPU usage over a one-second sampling interval.

    Returns:
        tuple:
            current CPU percentage
            boolean representing health
    """

    cpu_percent = psutil.cpu_percent(interval=1)

    healthy = cpu_percent < CPU_THRESHOLD

    return cpu_percent, healthy


def check_memory():
    """
    Return current system memory utilisation.

    psutil.virtual_memory() provides several values including:
    - total
    - available
    - used
    - percent
    """

    memory = psutil.virtual_memory()

    memory_percent = memory.percent

    healthy = memory_percent < MEMORY_THRESHOLD

    return memory_percent, healthy


def check_disk():
    """
    Check utilisation of the filesystem containing '/'.

    On Linux this normally represents the root filesystem.

    Windows users may replace '/' with something such as 'C:\\'.
    """

    disk = psutil.disk_usage("/")

    disk_percent = disk.percent

    healthy = disk_percent < DISK_THRESHOLD

    return disk_percent, healthy


def check_process(process_name):
    """
    Search all running processes for a particular executable name.

    Returns:
        True if found
        False otherwise
    """

    for process in psutil.process_iter(["name"]):

        try:
            name = process.info["name"]

            if name and process_name.lower() in name.lower():
                return True

        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess,
        ):
            # Processes can disappear while we are inspecting them.
            # That is normal in a live operating system.
            continue

    return False


# ---------------------------------------------------------
# Output helper
# ---------------------------------------------------------

def print_result(name, value, healthy):
    """
    Produce consistent output for numerical health checks.
    """

    status = "OK" if healthy else "FAIL"

    print(
        f"{name:<10}"
        f"{value:>6.1f}% "
        f"[{status}]"
    )


# ---------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------

def main():

    overall_healthy = True

    print("Infrastructure Health Monitor")
    print("-" * 40)

    cpu_percent, cpu_healthy = check_cpu()

    print_result(
        "CPU",
        cpu_percent,
        cpu_healthy,
    )

    if not cpu_healthy:
        overall_healthy = False

    memory_percent, memory_healthy = check_memory()

    print_result(
        "Memory",
        memory_percent,
        memory_healthy,
    )

    if not memory_healthy:
        overall_healthy = False

    disk_percent, disk_healthy = check_disk()

    print_result(
        "Disk",
        disk_percent,
        disk_healthy,
    )

    if not disk_healthy:
        overall_healthy = False

    print("\nProcesses")
    print("-" * 40)

    for process_name in REQUIRED_PROCESSES:

        running = check_process(process_name)

        status = "OK" if running else "FAIL"

        print(
            f"{process_name:<20}"
            f"[{status}]"
        )

        if not running:
            overall_healthy = False

    print("-" * 40)

    if overall_healthy:
        print("Overall status: HEALTHY")

        return 0

    print("Overall status: UNHEALTHY")

    return 1


if __name__ == "__main__":
    sys.exit(main())
