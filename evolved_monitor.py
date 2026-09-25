#!/usr/bin/env python3
"""
Infrastructure Health Monitor - Version 3 (Production)

Checks:
- CPU / Memory / Disk utilisation
- Required process existence
- Required TCP port availability
- HTTP endpoint health
- Outputs: human-readable, JSON, or Prometheus metrics
- Alerting via webhook
- Configurable via external YAML file
- Full logging to file and console

Exit codes:
0 = system healthy
1 = system unhealthy
"""

import sys
import os
import json
import time
import socket
import logging
import argparse
from datetime import timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

import yaml
import psutil


# ---------------------------------------------------------
# CONFIGURATION LOADER
# ---------------------------------------------------------

def load_config(config_path="config.yaml"):
    """
    Read the YAML config file and return it as a Python dictionary.
    If the file is missing, exit with a clear error.
    """
    path = Path(config_path)

    if not path.exists():
        print(f"FATAL: Config file '{config_path}' not found.")
        sys.exit(2)

    with open(path, "r") as f:
        config = yaml.safe_load(f)

    return config


# ---------------------------------------------------------
# LOGGING SETUP
# ---------------------------------------------------------

def setup_logging(config):
    """
    Configure Python's logging system.
    Logs go to BOTH the console AND a file simultaneously.
    """
    log_level = config.get("logging", {}).get("level", "INFO")
    log_file  = config.get("logging", {}).get("file", "health_monitor.log")

    # Ensure the log directory exists
    log_dir = Path(log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s [%(levelname)-8s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )

    return logging.getLogger("health_monitor")


# ---------------------------------------------------------
# INDIVIDUAL HEALTH CHECKS
# ---------------------------------------------------------

def check_cpu(threshold):
    cpu_percent = psutil.cpu_percent(interval=1)
    healthy = cpu_percent < threshold
    return cpu_percent, healthy


def check_memory(threshold):
    memory_percent = psutil.virtual_memory().percent
    healthy = memory_percent < threshold
    return memory_percent, healthy


def check_disk(disk_path, threshold):
    disk_percent = psutil.disk_usage(disk_path).percent
    healthy = disk_percent < threshold
    return disk_percent, healthy


def check_process(process_name):
    """Search running processes for a name (case-insensitive)."""
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
            continue
    return False


def check_port(port, host="127.0.0.1", timeout=2):
    """
    Check if a TCP port is open and accepting connections.
    We literally try to open a network socket to that port.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            result = s.connect_ex((host, port))
            return result == 0
    except socket.error:
        return False


def check_http(url, expected_status=200, timeout=5):
    """
    Make a real HTTP GET request and check the status code.
    Returns (status_code, healthy).
    """
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout) as response:
            status_code = response.getcode()
            healthy = status_code == expected_status
            return status_code, healthy

    except HTTPError as e:
        # HTTPError means the server responded, but with an error code
        # e.g., 404, 500. The server IS reachable, just unhealthy.
        return e.code, False

    except URLError:
        # URLError means the server is completely unreachable.
        # DNS failure, connection refused, timeout, etc.
        return 0, False

    except Exception:
        return 0, False


def check_uptime():
    boot_time = psutil.boot_time()
    current_time = time.time()
    uptime_seconds = int(current_time - boot_time)
    return str(timedelta(seconds=uptime_seconds))


# ---------------------------------------------------------
# ALERTING
# ---------------------------------------------------------

def send_alert(config, hostname, failures, logger):
    """
    Send an alert to a webhook (e.g., Slack, PagerDuty, Discord).
    Only fires if alerting is enabled and there are actual failures.
    """
    alert_config = config.get("alerting", {})
    enabled = alert_config.get("enabled", False)
    webhook_url = alert_config.get("webhook_url", "")

    if not enabled or not failures:
        return

    if not webhook_url:
        logger.warning("Alerting is enabled but no webhook_url configured.")
        return

    message = (
        f"HEALTH ALERT on {hostname}\n"
        f"Failures:\n" + "\n".join(f"  - {f}" for f in failures)
    )

    payload = json.dumps({"text": message}).encode("utf-8")

    try:
        req = Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            logger.info(f"Alert sent successfully. Status: {resp.getcode()}")
    except Exception as e:
        logger.error(f"Failed to send alert: {e}")


# ---------------------------------------------------------
# OUTPUT FORMATTERS
# ---------------------------------------------------------

def output_human(results, hostname, uptime):
    """Pretty-print results for human eyes."""
    print(f"Host: {hostname}")
    print("Infrastructure Health Monitor")
    print("-" * 50)
    print(f"Uptime: {uptime}\n")

    for check in results["resource_checks"]:
        status = "OK" if check["healthy"] else "FAIL"
        print(
            f"{check['name']:<12}"
            f"{check['value']:>6.1f}% "
            f"[{status}]"
        )

    print(f"\n{'Processes':<12}")
    print("-" * 50)
    for p in results["process_checks"]:
        status = "OK" if p["healthy"] else "FAIL"
        print(f"{p['name']:<20}[{status}]")

    print(f"\n{'Ports':<12}")
    print("-" * 50)
    for p in results["port_checks"]:
        status = "OPEN" if p["healthy"] else "CLOSED"
        print(f"Port {p['name']:<14}[{status}]")

    print(f"\n{'HTTP':<12}")
    print("-" * 50)
    for h in results["http_checks"]:
        status = "OK" if h["healthy"] else "FAIL"
        print(f"{h['name']:<40}[{h['value']}] [{status}]")

    print("-" * 50)

    if results["overall_healthy"]:
        print("Overall status: HEALTHY")
    else:
        print("Overall status: UNHEALTHY")


def output_json(results, hostname, uptime):
    """Print results as JSON for machine consumption."""
    output = {
        "hostname": hostname,
        "uptime": uptime,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "overall_healthy": results["overall_healthy"],
        "checks": results,
    }
    print(json.dumps(output, indent=2))


def output_prometheus(results, hostname):
    """
    Print results in Prometheus exposition format.
    Prometheus scrapes this output and stores it as time-series data.
    """
    lines = []

    # Resource gauges
    for check in results["resource_checks"]:
        metric_name = f"infra_{check['name'].lower().replace(' ', '_')}_percent"
        lines.append(f"# HELP {metric_name} {check['name']} utilisation percentage")
        lines.append(f"# TYPE {metric_name} gauge")
        lines.append(f'{metric_name}{{host="{hostname}"}} {check["value"]}')

    # Process checks (1 = running, 0 = not running)
    for p in results["process_checks"]:
        lines.append("# HELP infra_process_running Whether required process is running")
        lines.append("# TYPE infra_process_running gauge")
        val = 1 if p["healthy"] else 0
        lines.append(f'infra_process_running{{host="{hostname}",process="{p["name"]}"}} {val}')

    # Port checks
    for p in results["port_checks"]:
        val = 1 if p["healthy"] else 0
        lines.append(f'infra_port_open{{host="{hostname}",port="{p["name"]}"}} {val}')

    # HTTP checks
    for h in results["http_checks"]:
        val = 1 if h["healthy"] else 0
        lines.append(f'infra_http_up{{host="{hostname}",url="{h["name"]}"}} {val}')

    # Overall health
    overall_val = 1 if results["overall_healthy"] else 0
    lines.append(f'infra_overall_healthy{{host="{hostname}"}} {overall_val}')

    print("\n".join(lines))


# ---------------------------------------------------------
# MAIN ORCHESTRATION
# ---------------------------------------------------------

def main():
    # --- Command-line argument parsing ---
    parser = argparse.ArgumentParser(
        description="Infrastructure Health Monitor"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config file (default: config.yaml)",
    )
    parser.add_argument(
        "--format",
        choices=["human", "json", "prometheus"],
        default="human",
        help="Output format (default: human)",
    )
    args = parser.parse_args()

    # --- Load config and setup logging ---
    config = load_config(args.config)
    logger = setup_logging(config)

    hostname = socket.gethostname()
    uptime = check_uptime()
    overall_healthy = True
    failures = []

    logger.info(f"Starting health check on {hostname}")

    # --- Prepare result containers ---
    results = {
        "resource_checks": [],
        "process_checks": [],
        "port_checks": [],
        "http_checks": [],
        "overall_healthy": True,
    }

    # --- 1. Resource checks (CPU, Memory, Disk) ---
    thresholds = config.get("thresholds", {})
    disk_path = config.get("disk_path", "/")

    resource_defs = [
        ("CPU",    check_cpu(thresholds.get("cpu", 85.0))),
        ("Memory", check_memory(thresholds.get("memory", 90.0))),
        ("Disk",   check_disk(disk_path, thresholds.get("disk", 90.0))),
    ]

    for name, (value, healthy) in resource_defs:
        results["resource_checks"].append({
            "name": name,
            "value": value,
            "healthy": healthy,
        })
        if not healthy:
            overall_healthy = False
            failures.append(f"{name} at {value:.1f}% exceeds threshold")
            logger.warning(f"{name} check FAILED: {value:.1f}%")
        else:
            logger.info(f"{name} check passed: {value:.1f}%")

    # --- 2. Process checks ---
    required_processes = config.get("required_processes", [])
    for proc_name in required_processes:
        running = check_process(proc_name)
        results["process_checks"].append({
            "name": proc_name,
            "healthy": running,
        })
        if not running:
            overall_healthy = False
            failures.append(f"Process '{proc_name}' not found")
            logger.warning(f"Process check FAILED: '{proc_name}' not running")
        else:
            logger.info(f"Process check passed: '{proc_name}' is running")

    # --- 3. Port checks ---
    required_ports = config.get("required_ports", [])
    for port in required_ports:
        open_state = check_port(port)
        results["port_checks"].append({
            "name": str(port),
            "healthy": open_state,
        })
        if not open_state:
            overall_healthy = False
            failures.append(f"Port {port} is not open")
            logger.warning(f"Port check FAILED: {port} is closed")
        else:
            logger.info(f"Port check passed: {port} is open")

    # --- 4. HTTP endpoint checks ---
    http_endpoints = config.get("http_endpoints", [])
    for endpoint in http_endpoints:
        url = endpoint["url"]
        expected = endpoint.get("expected_status", 200)
        timeout = endpoint.get("timeout", 5)

        status_code, healthy = check_http(url, expected, timeout)
        results["http_checks"].append({
            "name": url,
            "value": status_code,
            "healthy": healthy,
        })
        if not healthy:
            overall_healthy = False
            failures.append(f"HTTP {url} returned {status_code}")
            logger.warning(f"HTTP check FAILED: {url} returned {status_code}")
        else:
            logger.info(f"HTTP check passed: {url} returned {status_code}")

    # --- Finalise results ---
    results["overall_healthy"] = overall_healthy

    # --- Alerting ---
    if not overall_healthy:
        send_alert(config, hostname, failures, logger)

    # --- Output ---
    if args.format == "json":
        output_json(results, hostname, uptime)
    elif args.format == "prometheus":
        output_prometheus(results, hostname)
    else:
        output_human(results, hostname, uptime)

    # --- Exit ---
    if overall_healthy:
        logger.info("Overall status: HEALTHY")
        return 0

    logger.critical(f"Overall status: UNHEALTHY. Failures: {failures}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
