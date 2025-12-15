#!/usr/bin/python3
# Argon ONE V3 / M.2 (Raspberry Pi 5) fan control
# Stable version with hysteresis, state file and journald logging

from __future__ import annotations

import os
import sys
import time
import signal
import threading
from dataclasses import dataclass
from typing import List

try:
    import smbus
except Exception:
    smbus = None

I2C_BUS = 1
I2C_ADDR = 0x1A
FAN_REG  = 0x80

CONF_PATH  = "/etc/argononed.conf"
STATE_PATH = "/run/argononed.last_speed"

POLL_SECONDS = 4.0
HYSTERESIS   = 4.0  # °C


@dataclass(frozen=True)
class Step:
    temp_c: float
    speed: int


def read_cpu_temp() -> float:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return 0.0


def parse_conf() -> List[Step]:
    steps: List[Step] = []
    if not os.path.exists(CONF_PATH):
        return [Step(45,20), Step(50,35), Step(55,55), Step(60,75), Step(65,100)]

    with open(CONF_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                t, s = line.split("=")
                steps.append(Step(float(t), int(s)))
            except Exception:
                pass

    steps.sort(key=lambda x: x.temp_c)
    return steps


def write_state(speed: int):
    try:
        with open(STATE_PATH, "w") as f:
            f.write(str(speed))
    except Exception:
        pass


def read_state():
    try:
        with open(STATE_PATH) as f:
            return int(f.read().strip())
    except Exception:
        return None


class FanController:
    def __init__(self):
        if smbus is None:
            raise RuntimeError("python3-smbus not available")
        self.bus = smbus.SMBus(I2C_BUS)
        self.lock = threading.Lock()
        self.last_speed = read_state()

    def set_speed(self, speed: int):
        speed = max(0, min(100, speed))
        with self.lock:
            if self.last_speed == speed:
                return
            self.bus.write_byte_data(I2C_ADDR, FAN_REG, speed)
            self.last_speed = speed
            write_state(speed)
            print(f"[argononed] fan_speed={speed}%", flush=True)

    def close(self):
        try:
            self.bus.close()
        except Exception:
            pass


def calc_target(temp: float, steps: List[Step], last: int | None) -> int:
    target = 0
    for st in steps:
        if temp >= st.temp_c:
            target = st.speed

    # hysteresis
    if last is not None:
        if target > last and temp < (next((s.temp_c for s in steps if s.speed == target), temp) - HYSTERESIS):
            return last
        if target < last and temp > (next((s.temp_c for s in steps if s.speed == last), temp) + HYSTERESIS):
            return last

    return target


def run_service():
    steps = parse_conf()
    fan = FanController()
    stop = False

    def stop_handler(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    try:
        while not stop:
            temp = read_cpu_temp()
            target = calc_target(temp, steps, fan.last_speed)
            fan.set_speed(target)
            time.sleep(POLL_SECONDS)
    finally:
        fan.close()


def main():
    if len(sys.argv) < 2:
        return 2

    cmd = sys.argv[1].upper()
    fan = FanController()

    try:
        if cmd == "SERVICE":
            run_service()
        elif cmd == "FANON":
            fan.set_speed(100)
        elif cmd == "FANOFF":
            fan.set_speed(0)
        elif cmd == "FANSPEED":
            fan.set_speed(int(sys.argv[2]))
        elif cmd == "STATUS":
            temp = read_cpu_temp()
            steps = parse_conf()
            target = calc_target(temp, steps, fan.last_speed)
            print(f"temp_c={temp:.2f} target_speed={target} last_speed={fan.last_speed}")
        else:
            return 2
    finally:
        fan.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
