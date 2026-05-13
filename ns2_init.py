#!/usr/bin/env python3
"""NS2 Pro Controller HID-mode initializer (Steam-free).

Adapted from https://github.com/dannydarvish/Switch2ProMac/blob/main/pro2steam.sh
Strips the Steam launch; just flips the controller into HID mode and verifies.
"""
import sys
import time
from typing import Optional

import usb.core
import usb.util
import hid


VENDOR_ID = 0x057E
PRODUCT_ID = 0x2069
PRODUCT_ID_ALT = 0x2073
USB_INTERFACE = 1

COMMANDS = [
    bytes([0x03, 0x91, 0x00, 0x0D, 0x00, 0x08, 0x00, 0x00, 0x01, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]),
    bytes([0x07, 0x91, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00]),
    bytes([0x16, 0x91, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00]),
    bytes([0x15, 0x91, 0x00, 0x01, 0x00, 0x0E, 0x00, 0x00, 0x00, 0x02, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]),
    bytes([0x15, 0x91, 0x00, 0x02, 0x00, 0x11, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]),
    bytes([0x15, 0x91, 0x00, 0x03, 0x00, 0x01, 0x00, 0x00, 0x00]),
    bytes([0x09, 0x91, 0x00, 0x07, 0x00, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
    bytes([0x0C, 0x91, 0x00, 0x02, 0x00, 0x04, 0x00, 0x00, 0x27, 0x00, 0x00, 0x00]),
    bytes([0x11, 0x91, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00]),
    bytes([0x0A, 0x91, 0x00, 0x08, 0x00, 0x14, 0x00, 0x00, 0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x35, 0x00, 0x46, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
    bytes([0x0C, 0x91, 0x00, 0x04, 0x00, 0x04, 0x00, 0x00, 0x27, 0x00, 0x00, 0x00]),
    bytes([0x03, 0x91, 0x00, 0x0A, 0x00, 0x04, 0x00, 0x00, 0x09, 0x00, 0x00, 0x00]),
    bytes([0x10, 0x91, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00]),
    bytes([0x01, 0x91, 0x00, 0x0C, 0x00, 0x00, 0x00, 0x00]),
    bytes([0x03, 0x91, 0x00, 0x01, 0x00, 0x00, 0x00]),
    bytes([0x0A, 0x91, 0x00, 0x02, 0x00, 0x04, 0x00, 0x00, 0x03, 0x00, 0x00]),
    bytes([0x09, 0x91, 0x00, 0x07, 0x00, 0x08, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
]


def find_device():
    for pid in (PRODUCT_ID, PRODUCT_ID_ALT):
        dev = usb.core.find(idVendor=VENDOR_ID, idProduct=pid)
        if dev:
            return dev, pid
    return None, None


def connect(dev):
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass
    cfg = dev.get_active_configuration()
    intf = cfg[(USB_INTERFACE, 0)]
    ep_out = ep_in = None
    for ep in intf:
        if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT:
            ep_out = ep.bEndpointAddress
        else:
            ep_in = ep.bEndpointAddress
    return ep_out, ep_in


def send_commands(dev, ep_out, ep_in):
    for i, cmd in enumerate(COMMANDS, 1):
        dev.write(ep_out, cmd, timeout=1000)
        if ep_in:
            try:
                time.sleep(0.01)
                dev.read(ep_in, 64, timeout=100)
            except usb.core.USBTimeoutError:
                pass
        time.sleep(0.05)
    print(f"  Sent {len(COMMANDS)} init commands")


def hid_present(pid):
    try:
        return len(hid.enumerate(VENDOR_ID, pid)) > 0
    except Exception:
        return False


def hid_test(pid, duration=3.0):
    print(f"\n  Reading HID reports for {duration}s — press buttons to test...")
    try:
        h = hid.device()
        h.open(VENDOR_ID, pid)
        h.set_nonblocking(True)
        start = time.time()
        n = 0
        last = None
        while time.time() - start < duration:
            r = h.read(64)
            if r:
                n += 1
                if r != last:
                    print(f"    report #{n}: {' '.join(f'{b:02X}' for b in r[:16])}...")
                    last = r
            time.sleep(0.01)
        h.close()
        print(f"  Got {n} reports total")
        return n > 0
    except IOError as e:
        print(f"  HID open failed: {e}", file=sys.stderr)
        return False


def main():
    print("[1/3] Finding controller...")
    dev, pid = find_device()
    if not dev:
        sys.exit("Controller not found over USB.")
    print(f"  Found {VENDOR_ID:#06x}:{pid:#06x}")

    if hid_present(pid):
        print("  Already in HID mode — testing reports...")
        if hid_test(pid, duration=2.0):
            print("\nAlready working. No init needed.")
            return

    print("\n[2/3] Sending init sequence on USB interface 1...")
    ep_out, ep_in = connect(dev)
    if not ep_out:
        sys.exit("No bulk OUT endpoint found.")
    send_commands(dev, ep_out, ep_in)
    usb.util.dispose_resources(dev)

    print("\n[3/3] Verifying HID mode...")
    time.sleep(2)
    if hid_present(pid):
        print("  Controller is in HID mode.")
        hid_test(pid, duration=4.0)
        print("\nDone — usable by any HID-aware app (emulators, browsers, SDL).")
    else:
        sys.exit("HID device did not appear after init.")


if __name__ == "__main__":
    main()
