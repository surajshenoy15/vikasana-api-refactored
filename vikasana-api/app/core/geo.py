# app/core/geo.py
import re
from typing import Optional, Tuple
from math import radians, sin, cos, sqrt, atan2

def parse_google_maps_latlng(url: str) -> Optional[Tuple[float, float]]:
    """
    Best-effort extraction of coordinates from common Google Maps URL formats.

    Supported patterns:
    - .../@12.9716,77.5946,15z
    - ...?q=12.9716,77.5946
    - .../search/12.9716,77.5946
    """
    if not url:
        return None

    u = url.strip()

    m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", u)
    if m:
        return float(m.group(1)), float(m.group(2))

    m = re.search(r"[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)", u)
    if m:
        return float(m.group(1)), float(m.group(2))

    m = re.search(r"/search/(-?\d+\.\d+),(-?\d+\.\d+)", u)
    if m:
        return float(m.group(1)), float(m.group(2))

    return None


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance between two GPS points in meters."""
    R = 6371000.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c