#!/usr/bin/env python3
"""Probe the NS2 Pro's D-pad to find which bit each direction fires.

Walks through UP, DOWN, LEFT, RIGHT one at a time. For each, waits until
you press it, records the HAT bit value, and moves on.
"""
import sys
import time
import hid


VID = 0x057E
PIDS = (0x2069, 0x2073)


def open_dev():
    for pid in PIDS:
        if hid.enumerate(VID, pid):
            h = hid.device()
            h.open(VID, pid)
            return h, pid
    return None, None


def wait_for_hat(h, label):
    """Wait until D-pad shows a non-zero HAT, then wait for it to release.
    Returns the HAT bit value (1, 2, 4, or 8)."""
    print(f"\n>>> Press D-pad {label.upper()} (one press, then release) ...")
    h.set_nonblocking(False)
    # Drain any pending reports first (the previous direction's release etc.)
    deadline = time.time() + 0.3
    while time.time() < deadline:
        h.read(64, timeout_ms=50)
    # Now wait for a press.
    hat = 0
    while hat == 0:
        r = h.read(64, timeout_ms=1000)
        if r and len(r) >= 5 and r[0] == 0x09:
            hat = r[4] & 0x0F
    captured = hat
    print(f"    {label.upper()} = 0x{captured:02X}")
    # Wait for release.
    while hat != 0:
        r = h.read(64, timeout_ms=200)
        if r and len(r) >= 5 and r[0] == 0x09:
            hat = r[4] & 0x0F
    return captured


def main():
    h, pid = open_dev()
    if h is None:
        sys.exit("NS2 Pro not in HID mode. Run ~/ns2pro/.venv/bin/python ~/ns2pro/ns2_init.py first.")
    print(f"opened {VID:04x}:{pid:04x}")

    results = {}
    for label in ('up', 'down', 'left', 'right'):
        results[label] = wait_for_hat(h, label)

    print("\n=== D-pad mapping for this controller ===")
    for label, bit in results.items():
        print(f"  {label.upper():<6} = 0x{bit:02X}")

    # Inverse: bit -> label
    print("\nDecode lines to paste into ns2_pf400.py decode_buttons:")
    for label, bit in results.items():
        btn = {'up': 'BTN_DU', 'down': 'BTN_DD',
               'left': 'BTN_DL', 'right': 'BTN_DR'}[label]
        print(f"    if hat & 0x{bit:02X}: btn |= {btn}")

    h.close()


if __name__ == '__main__':
    main()
