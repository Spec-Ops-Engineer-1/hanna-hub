#!/usr/bin/env python3
"""
Hanna HI6000 Bridge Script
Scrapes the meter's built-in web server and pushes readings to Hanna Hub.

Usage:
    python3 bridge.py --meter-ip 192.168.1.100 --hub-url https://your-app.up.railway.app --api-key your-key

The script polls the meter's web page every few seconds, parses the HTML
for measurement values, and POSTs them to the Hanna Hub API.
"""

import argparse
import re
import sys
import time
from datetime import datetime, timezone

import httpx


def scrape_meter(ip: str) -> dict:
    """Fetch the HI6000 web server page and parse measurement values."""
    url = f"http://{ip}/"
    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    html = resp.text

    readings = {}

    # The HI6000 web server embeds readings in the HTML.
    # Common patterns in embedded instrument web pages:
    # Look for labeled value pairs in various formats.
    patterns = {
        "ph": [
            r"pH[:\s]*</?\w*>?\s*([\d.]+)",
            r"pH\s*[=:]\s*([\d.]+)",
            r'"pH"[:\s]*([\d.]+)',
        ],
        "orp_mv": [
            r"ORP[:\s]*</?\w*>?\s*([+-]?[\d.]+)\s*mV",
            r"mV[:\s]*</?\w*>?\s*([+-]?[\d.]+)",
            r"ORP\s*[=:]\s*([+-]?[\d.]+)",
            r'"ORP"[:\s]*([+-]?[\d.]+)',
            r'"mV"[:\s]*([+-]?[\d.]+)',
        ],
        "do_mgl": [
            r"DO[:\s]*</?\w*>?\s*([\d.]+)\s*mg/[Ll]",
            r"Dissolved\s+Oxygen[:\s]*</?\w*>?\s*([\d.]+)\s*mg",
            r"DO\s*[=:]\s*([\d.]+)\s*mg",
            r'"DO"[:\s]*([\d.]+)',
        ],
        "do_pct": [
            r"DO[:\s]*</?\w*>?\s*([\d.]+)\s*%",
            r"Saturation[:\s]*</?\w*>?\s*([\d.]+)\s*%",
        ],
        "ec_us": [
            r"EC[:\s]*</?\w*>?\s*([\d.]+)\s*[µu]S",
            r"Conductivity[:\s]*</?\w*>?\s*([\d.]+)",
            r'"EC"[:\s]*([\d.]+)',
        ],
        "tds_mgl": [
            r"TDS[:\s]*</?\w*>?\s*([\d.]+)\s*mg",
            r"TDS\s*[=:]\s*([\d.]+)",
        ],
        "temp_c": [
            r"[Tt]emp(?:erature)?[:\s]*</?\w*>?\s*([\d.]+)\s*[°]?C",
            r"[Tt]emp\s*[=:]\s*([\d.]+)",
            r'"[Tt]emp"[:\s]*([\d.]+)',
            r"([\d.]+)\s*°C",
        ],
        "ise_value": [
            r"ISE[:\s]*</?\w*>?\s*([\d.]+)",
            r"Ion[:\s]*</?\w*>?\s*([\d.]+)",
        ],
    }

    for param, regexes in patterns.items():
        for regex in regexes:
            match = re.search(regex, html, re.IGNORECASE)
            if match:
                try:
                    readings[param] = float(match.group(1))
                except ValueError:
                    pass
                break

    return readings


def push_reading(hub_url: str, api_key: str, readings: dict) -> dict:
    """POST a reading to Hanna Hub."""
    readings["source"] = "bridge"
    readings["timestamp"] = datetime.now(timezone.utc).isoformat()
    resp = httpx.post(
        f"{hub_url}/api/readings",
        params={"key": api_key},
        json=readings,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Hanna HI6000 Bridge")
    parser.add_argument("--meter-ip", required=True, help="IP address of the HI6000 meter")
    parser.add_argument("--hub-url", required=True, help="URL of the Hanna Hub server")
    parser.add_argument("--api-key", default="hanna-hub-key", help="API key for Hanna Hub")
    parser.add_argument("--interval", type=int, default=5, help="Polling interval in seconds (default: 5)")
    parser.add_argument("--once", action="store_true", help="Run once and exit (for testing)")
    args = parser.parse_args()

    hub_url = args.hub_url.rstrip("/")
    print(f"Hanna HI6000 Bridge")
    print(f"  Meter: http://{args.meter_ip}/")
    print(f"  Hub:   {hub_url}")
    print(f"  Interval: {args.interval}s")
    print()

    while True:
        try:
            readings = scrape_meter(args.meter_ip)
            if readings:
                result = push_reading(hub_url, args.api_key, readings)
                ts = datetime.now().strftime("%H:%M:%S")
                vals = " | ".join(f"{k}={v}" for k, v in readings.items())
                print(f"[{ts}] #{result.get('id', '?')}: {vals}")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] No readings parsed from meter page")
        except httpx.ConnectError:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Cannot connect to meter at {args.meter_ip}")
        except httpx.HTTPStatusError as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] HTTP error: {e}")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Error: {e}")

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
