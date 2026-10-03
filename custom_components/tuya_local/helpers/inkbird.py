"""Packed INT-14S-BW target settings, based on ha-inkbird-int14's layout."""

import json
import math
from base64 import b64decode
from collections import deque
from time import time

TARGET_FIELDS = {
    "food_high": (1, 0, 16),
    "food_low": (3, 0, 1),
    "ambient_high": (12, 11, 16),
    "ambient_low": (14, 11, 1),
}


def crc8_atm(data):
    """CRC-8/ATM: polynomial 0x07, initial value zero, no reflection/XOR."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def update_target_packet(raw, mask, endianness, value):
    """Change one threshold, enable its mode bit and preserve all other bytes."""
    if not isinstance(raw, bytes) or len(raw) != 21:
        raise ValueError("A current 21-byte target packet is required before writing")
    if crc8_atm(raw[:-1]) != raw[-1]:
        raise ValueError("The current target packet has an invalid checksum")
    if raw[0] not in (0, 1, 16, 17) or raw[11] not in (0, 1, 16, 17):
        raise ValueError("The target packet has an unsupported alarm mode")
    fields = {
        0xFFFF << (offset * 8): (offset, mode, bit)
        for offset, mode, bit in ((1, 0, 16), (3, 0, 1), (12, 11, 16), (14, 11, 1))
    }
    if endianness != "little" or mask not in fields:
        raise ValueError("Unsupported INT-14S target field")
    offset, mode, bit = fields[mask]
    packet = bytearray(raw)
    packet[offset : offset + 2] = int(round(value)).to_bytes(2, "little", signed=True)
    packet[mode] |= bit
    # Reports append CRC; commands contain only the 20-byte payload.
    return bytes(packet[:20])


def parse_targets(value):
    """Require a complete set of Fahrenheit thresholds; null disables an alarm."""
    try:
        targets = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Enter a JSON object with all four target fields in Fahrenheit"
        ) from exc
    if not isinstance(targets, dict) or set(targets) != set(TARGET_FIELDS):
        raise ValueError(
            "Required fields: food_high, food_low, ambient_high, ambient_low"
        )
    for key, target in targets.items():
        if target is not None and (
            type(target) not in (int, float)
            or not math.isfinite(target)
            or not 32 <= target <= 572
        ):
            raise ValueError(
                f"{key} must be 32ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Å“572 Fahrenheit, or null to disable"
            )
    for group in ("food", "ambient"):
        low, high = targets[group + "_low"], targets[group + "_high"]
        if low is not None and high is not None and low > high:
            raise ValueError(f"{group}_low must not exceed {group}_high")
    return targets


def build_targets_packet(value, current=None):
    """Write all four thresholds, initializing metadata when there is no report."""
    targets = parse_targets(value)
    if current is None:
        packet = bytearray(21)
        packet[7:11] = int(time()).to_bytes(4, "little")
    else:
        if (
            not isinstance(current, bytes)
            or len(current) != 21
            or crc8_atm(current[:-1]) != current[-1]
        ):
            raise ValueError("Invalid current target packet")
        packet = bytearray(current)
    packet[0] = packet[11] = 0
    for key, (offset, mode, bit) in TARGET_FIELDS.items():
        target = targets[key]
        raw = 0 if target is None else round(target * 10)
        packet[offset : offset + 2] = raw.to_bytes(2, "little", signed=True)
        if target is not None:
            packet[mode] |= bit
    # Reports append CRC; commands contain only the 20-byte payload.
    return bytes(packet[:20])


def targets_packet_text(packet):
    """Describe a valid packet as one editable JSON value."""
    if (
        not isinstance(packet, bytes)
        or len(packet) != 21
        or crc8_atm(packet[:-1]) != packet[-1]
    ):
        return None
    if packet[0] not in (0, 1, 16, 17) or packet[11] not in (0, 1, 16, 17):
        return None
    targets = {}
    for key, (offset, mode, bit) in TARGET_FIELDS.items():
        targets[key] = (
            int.from_bytes(packet[offset : offset + 2], "little", signed=True) / 10
            if packet[mode] & bit
            else None
        )
    return json.dumps(targets, separators=(",", ":"))


class TemperatureTrend:
    """Five-minute linear trend, resetting on disconnects or long data gaps."""

    def __init__(self):
        self.samples = deque()

    def add(self, timestamp, temperature):
        if temperature is None or not math.isfinite(temperature):
            self.samples.clear()
            return
        if self.samples and timestamp - self.samples[-1][0] > 90:
            self.samples.clear()
        if self.samples and timestamp <= self.samples[-1][0]:
            return
        self.samples.append((timestamp, temperature))
        while self.samples and timestamp - self.samples[0][0] > 300:
            self.samples.popleft()

    def minutes_to_target(self, target, now):
        if not self.samples or target is None or not math.isfinite(target):
            return None
        timestamp, current = self.samples[-1]
        if now - timestamp > 90:
            return None
        if current >= target:
            return 0
        if len(self.samples) < 3 or timestamp - self.samples[0][0] < 60:
            return None
        origin = self.samples[0][0]
        xs = [(stamp - origin) / 60 for stamp, _ in self.samples]
        ys = [temperature for _, temperature in self.samples]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        denominator = sum((x - mean_x) ** 2 for x in xs)
        rate = (
            sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
            / denominator
        )
        return round((target - current) / rate, 1) if rate > 0.01 else None


def battery_packet_ready(value):
    """A startup battery response is ready only when all five values are known."""
    if not isinstance(value, str):
        return False
    try:
        packet = b64decode(value, validate=True)
    except ValueError:
        return False
    return (
        len(packet) == 6
        and crc8_atm(packet[:-1]) == packet[-1]
        and all(0 <= percent <= 100 for percent in packet[:-1])
    )
