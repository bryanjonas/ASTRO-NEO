#!/usr/bin/env python3
"""Plot a horizon mask JSON file as a polar chart.

Example:
    python scripts/plot_horizon.py scripts/site/horizon_38.681_-77.133.json \
        --output horizon_profile.png
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Visualize a horizon JSON profile.")
    parser.add_argument("json_path", type=Path, help="Path to horizon JSON file")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional image file to save (PNG). If omitted, shows the plot interactively.",
    )
    return parser.parse_args()


def load_profile(json_path: Path):
    data = json.loads(json_path.read_text())
    profile = data["outputs"]["horizon_profile"]
    az = np.array([item["A"] for item in profile])
    alt = np.array([item["H_hor"] for item in profile])
    return az, alt


def plot_horizon(az_deg: np.ndarray, alt_deg: np.ndarray, output: Path | None):
    # Convert azimuth from MPC convention (0=S, east negative) to standard polar (0=N, clockwise)
    az_standard = np.deg2rad((180 - az_deg) % 360)
    alt = alt_deg

    fig = plt.figure(figsize=(6, 6))
    ax = fig.add_subplot(111, polar=True)
    ax.plot(az_standard, alt, label="Horizon alt")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_rlabel_position(225)
    ax.set_title("Horizon Profile")
    ax.set_ylim(bottom=0)
    ax.grid(True)
    ax.legend(loc="lower left")

    if output:
        fig.savefig(output, bbox_inches="tight", dpi=150)
    else:
        plt.show()


def main() -> int:
    args = parse_args()
    az, alt = load_profile(args.json_path)
    plot_horizon(az, alt, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
