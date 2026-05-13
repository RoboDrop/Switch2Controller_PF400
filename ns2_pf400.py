#!/usr/bin/env python3
"""Nintendo Switch 2 Pro Controller -> Precise Automation PF400 jog control.

Reads the NS2 Pro Controller over USB HID and live-jogs a PF400 robot via
the Precise Automation TCP Command Server (TCS) on port 10100.

Prereqs:
  - PF400 reachable on the network with TCS running on port 10100.
  - NS2 Pro Controller plugged in via USB and switched into HID mode by
    running `ns2_init.py` first. The controller reverts to its native
    Nintendo protocol after every power cycle, so re-init when needed.

Mapping (verified empirically — see ns2_dpad_probe.py if your controller
behaves differently):

  D-pad up/down   -> J1 (vertical column; up = raise)
  Left stick X    -> J2 (shoulder)
  Right stick X   -> J3 (elbow)
  L / R           -> J4 (wrist rotate left/right)
  ZL / ZR         -> J5 gripper close / open
  YL / YR         -> speed scale - / +  (3rd L/R pair / back paddles)
  A               -> print current pose (for teach)
  B               -> halt + quit
  Y               -> toggle motor power (HP)
  Minus           -> emergency stop (halt + HP off)
  Plus            -> reset speed scale to 1.0

Run:
  python3 ns2_pf400.py

Env knobs:
  NS2_DEBUG=1     -> print decoded button bits and stick values
"""

import os
import sys
import math
import time
import socket
import threading

import hid


# =====================================================================
# CONFIG — edit these for your robot / preferences
# =====================================================================

# --- Robot link ---
ROBOT_HOST = '192.168.0.1'   # PF400 IP
ROBOT_PORT = 10100           # TCS command port
ROBOT_NUM  = 1               # robot index (single-arm = 1)

# --- Joint soft limits (J1..J5). Measure on YOUR robot — these vary by
# unit, mounting, and firmware. The script clamps targets to these bounds
# but allows existing out-of-range positions to stay where they are. ---
JOINT_MIN = [ 10.0, -92.0,  70.0, -320.0,  53.0]   # J1 mm, J2-4 deg, J5 mm
JOINT_MAX = [754.0,  93.0, 348.0,  320.0, 134.0]

# --- Per-axis jog rate at full-stick / button hold. ---
RATE = [120.0, 25.0, 35.0, 60.0, 40.0]   # J1 mm/s, J2-4 deg/s, J5 mm/s

# --- Stick deadzone (normalized magnitude). ---
DEADZONE = 0.12

# --- Command rate. 40 Hz keeps the controller's motion queue near zero
# depth so there's no perceived lag. ---
TICK_HZ = 40

# --- Speed scale step + bounds (YL/YR adjust this; Plus resets to 1.0). ---
SPEED_SCALE_MIN  = 0.1
SPEED_SCALE_MAX  = 2.0
SPEED_SCALE_STEP = 0.2

# --- TCS motion profile. InRange=-1 enables continuous-path blending so
# successive movej commands flow into each other without stopping at
# every target. High accel/decel lets the robot track rapid stick input. ---
TCS_PROFILE = 'profile 1 100 100 500 500 0.05 0.05 -1 0'
TCS_MSPEED  = 100   # system master-speed (% scale on profile speeds)

# --- Switch 2 Pro Controller USB IDs. ---
NS2_VID  = 0x057E
NS2_PIDS = (0x2069, 0x2073)   # NS2 Pro and NSO GameCube fallback

# =====================================================================


# ---------- TCS client ----------

class TCS:
    """Tiny client for the PF400 TCP Command Server."""

    def __init__(self, host=ROBOT_HOST, port=ROBOT_PORT, robot=ROBOT_NUM):
        self.s = socket.socket()
        self.s.settimeout(8)
        self.s.connect((host, port))
        self.s.settimeout(3)
        self.robot = robot
        self.lock = threading.Lock()
        self._drain()

    def _drain(self):
        self.s.settimeout(0.2)
        try:
            while self.s.recv(4096):
                pass
        except (socket.timeout, BlockingIOError, OSError):
            pass
        self.s.settimeout(3)

    def cmd(self, line, timeout=3):
        with self.lock:
            self.s.settimeout(timeout)
            self.s.sendall((line + '\n').encode())
            buf = b''
            t_end = time.time() + timeout
            while time.time() < t_end:
                try:
                    chunk = self.s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    if b'\n' in buf:
                        break
                except socket.timeout:
                    break
            return buf.decode(errors='replace').strip()

    def init_session(self):
        for c in ['nop', f'attach {self.robot}', f'hp {self.robot}', 'mode 0']:
            print(f"  {c:<26} -> {self.cmd(c)}")
        # `hp 1` returns 0 immediately, but the controller takes ~1-3s to
        # actually energize. Commands sent during the transition all fail
        # silently, leaving the jog loop broken. Poll sysState until it
        # reads "21" (powered + homed) before proceeding.
        self._wait_ready()
        for c in [f'mspeed {TCS_MSPEED}', TCS_PROFILE]:
            print(f"  {c:<26} -> {self.cmd(c)}")

    def _wait_ready(self, timeout=10):
        deadline = time.time() + timeout
        last = ''
        while time.time() < deadline:
            last = self.cmd('sysState')
            parts = last.split()
            if parts and parts[-1] == '21':
                print(f"  sysState                   -> {last}")
                return
            time.sleep(0.3)
        print(f"  WARNING: sysState never reached 21 within {timeout}s (last={last!r})")
        print(f"  Robot may not be homed, or motor power didn't engage.")

    def wherej(self):
        r = self.cmd('wherej')
        try:
            return [float(x) for x in r.split()[1:6]]
        except (IndexError, ValueError):
            raise RuntimeError(f"wherej parse failed: {r!r}")

    def movej(self, joints, profile=1):
        return self.cmd(f"movej {profile} " + ' '.join(f"{j:.3f}" for j in joints))

    def hp_off(self):       return self.cmd('hp 0')
    def hp_on(self):        return self.cmd(f'hp {self.robot}')

    def halt(self):
        r = self.cmd('halt')
        if r.startswith('-'):
            r = self.cmd('estop')
        return r


# ---------- Controller layer ----------

# Button bitmap. Only the bits we actually use are defined.
BTN_A    = 1 << 0
BTN_B    = 1 << 1
BTN_Y    = 1 << 3
BTN_LB   = 1 << 4   # L
BTN_RB   = 1 << 5   # R
BTN_BACK = 1 << 6   # Minus
BTN_MENU = 1 << 7   # Plus
BTN_DU   = 1 << 10
BTN_DD   = 1 << 11
BTN_ZL   = 1 << 14
BTN_ZR   = 1 << 15
BTN_YL   = 1 << 16   # 3rd left shoulder / back paddle
BTN_YR   = 1 << 17   # 3rd right shoulder / back paddle


class Pad:
    """Thread-safe snapshot of the latest controller state."""
    def __init__(self):
        self.lx = self.ly = self.rx = self.ry = 0.0
        self.btn = 0
        self.lock = threading.Lock()

    def snapshot(self):
        with self.lock:
            return (self.lx, self.ly, self.rx, self.ry, self.btn)


def deadzone(v, dz=DEADZONE):
    if abs(v) < dz:
        return 0.0
    return math.copysign((abs(v) - dz) / (1.0 - dz), v)


def unpack_sticks(report):
    """Decode bytes 6..11 of an NS2 Pro HID report (12-bit packed sticks)."""
    lx = report[6] | ((report[7] & 0x0F) << 8)
    ly = (report[7] >> 4) | (report[8] << 4)
    rx = report[9] | ((report[10] & 0x0F) << 8)
    ry = (report[10] >> 4) | (report[11] << 4)
    return lx, ly, rx, ry


def decode_buttons(report):
    """Decode NS2 Pro button bitmap from bytes 3..5 of the HID report.

    Layout (verified via ns2_probe.py / ns2_dpad_probe.py on this controller):
      byte 3: B=0x01, A=0x02, Y=0x04, X=0x08,
              R=0x10, ZR=0x20, Plus=0x40, RStick=0x80
      byte 4: D-pad HAT in low nibble (0x01=down, 0x02=right, 0x04=left, 0x08=up),
              L=0x10, ZL=0x20, Minus=0x40, LStick=0x80
      byte 5: Home=0x01, Capture=0x02, YR=0x04, YL=0x08
    """
    if len(report) < 6:
        return 0
    b3, b4, b5 = report[3], report[4], report[5]
    btn = 0
    # Face + right shoulder cluster
    if b3 & 0x02: btn |= BTN_A
    if b3 & 0x01: btn |= BTN_B
    if b3 & 0x04: btn |= BTN_Y
    if b3 & 0x10: btn |= BTN_RB
    if b3 & 0x20: btn |= BTN_ZR
    if b3 & 0x40: btn |= BTN_MENU
    # Left shoulder cluster
    if b4 & 0x10: btn |= BTN_LB
    if b4 & 0x20: btn |= BTN_ZL
    if b4 & 0x40: btn |= BTN_BACK
    # D-pad HAT (byte 4 low nibble)
    hat = b4 & 0x0F
    if hat & 0x01: btn |= BTN_DD
    if hat & 0x08: btn |= BTN_DU
    # Back paddles
    if b5 & 0x08: btn |= BTN_YL
    if b5 & 0x04: btn |= BTN_YR
    return btn


def calibrate_center(h, n=20, timeout=0.5):
    """Average resting stick raw values so we can normalize around them."""
    samples = []
    deadline = time.time() + timeout
    while len(samples) < n and time.time() < deadline:
        r = h.read(64, timeout_ms=50)
        if r and len(r) >= 12 and r[0] == 0x09:
            samples.append(unpack_sticks(r))
    if not samples:
        return (2048, 2048, 2048, 2048)
    return tuple(int(sum(s[i] for s in samples) / len(samples)) for i in range(4))


def start_hid_reader(pad):
    """Open the NS2 Pro over HID and spawn a thread that streams state into `pad`."""
    h = None
    pid_used = None
    for pid in NS2_PIDS:
        if hid.enumerate(NS2_VID, pid):
            h = hid.device()
            h.open(NS2_VID, pid)
            pid_used = pid
            break
    if h is None:
        return None, "NS2 Pro Controller not in HID mode — run ns2_init.py first."

    h.set_nonblocking(False)
    print("  calibrating stick center...")
    cx_l, cy_l, cx_r, cy_r = calibrate_center(h)
    print(f"  centers: L=({cx_l},{cy_l}) R=({cx_r},{cy_r})")

    debug = os.environ.get('NS2_DEBUG', '') == '1'

    def normalize(raw, center):
        span = max(center, 4095 - center)
        return max(-1.0, min(1.0, (raw - center) / span))

    def reader():
        while True:
            try:
                report = h.read(64, timeout_ms=200)
            except Exception as e:
                print(f"hid read err: {e}", file=sys.stderr)
                return
            if not report or report[0] != 0x09 or len(report) < 12:
                continue
            lxr, lyr, rxr, ryr = unpack_sticks(report)
            btn = decode_buttons(report)
            with pad.lock:
                pad.lx = normalize(lxr, cx_l)
                pad.ly = normalize(lyr, cy_l)
                pad.rx = normalize(rxr, cx_r)
                pad.ry = normalize(ryr, cy_r)
                pad.btn = btn
            if debug:
                print(f"  L=({pad.lx:+.2f},{pad.ly:+.2f}) "
                      f"R=({pad.rx:+.2f},{pad.ry:+.2f}) "
                      f"btn={btn:018b} raw[3..5]="
                      f"{report[3]:02X} {report[4]:02X} {report[5]:02X}")

    threading.Thread(target=reader, daemon=True).start()
    return True, f"Switch 2 Pro {NS2_VID:04x}:{pid_used:04x}"


# ---------- Main loop ----------

def main():
    pad = Pad()
    print("opening controller...")
    ok, name = start_hid_reader(pad)
    if not ok:
        sys.exit(name)
    print(f"controller: {name}")

    print(f"connecting to PF400 at {ROBOT_HOST}:{ROBOT_PORT} ...")
    tcs = TCS()
    print("init session:")
    tcs.init_session()
    cur = tcs.wherej()
    print(f"start pose: {[round(x, 2) for x in cur]}")

    speed_scale = 1.0
    last_btn = 0
    period = 1.0 / TICK_HZ
    next_t = time.time()

    print("\nReady. Hold sticks/buttons to jog. "
          "B=halt+quit, Minus=e-stop, A=print pose.\n")

    while True:
        # Fixed-cadence pacing.
        next_t += period
        sleep = next_t - time.time()
        if sleep > 0:
            time.sleep(sleep)
        elif sleep < -period:
            next_t = time.time() + period   # don't try to catch up after a stall

        lx, ly, rx, ry, btn = pad.snapshot()
        pressed = btn & ~last_btn
        last_btn = btn

        # --- Edge-triggered actions ---
        if pressed & BTN_B:
            print("\nB pressed — halting.")
            try: tcs.halt()
            except Exception: pass
            break
        if pressed & BTN_BACK:
            print("\nMinus pressed — emergency stop.")
            try: tcs.halt(); tcs.hp_off()
            except Exception: pass
            break
        if pressed & BTN_A:
            try:
                pose = tcs.wherej()
                print("POSE:", ' '.join(f"{x:.3f}" for x in pose))
            except Exception as e:
                print("wherej fail:", e)
            continue
        if pressed & BTN_Y:
            try:
                power = (tcs.cmd('pd 230').split() or ['?'])[-1].strip()
                if power in ('1', '21'):
                    print("HP off ->", tcs.hp_off())
                else:
                    print("HP on  ->", tcs.hp_on())
            except Exception as e:
                print("hp toggle fail:", e)
            continue
        if pressed & BTN_YL:
            speed_scale = max(SPEED_SCALE_MIN, speed_scale - SPEED_SCALE_STEP)
            print(f"speed_scale = {speed_scale:.2f}")
        if pressed & BTN_YR:
            speed_scale = min(SPEED_SCALE_MAX, speed_scale + SPEED_SCALE_STEP)
            print(f"speed_scale = {speed_scale:.2f}")
        if pressed & BTN_MENU:
            speed_scale = 1.0
            print("speed_scale = 1.0")

        # --- Continuous-input axes ---
        v = [0.0] * 5
        if btn & BTN_DU: v[0] += RATE[0]
        if btn & BTN_DD: v[0] -= RATE[0]
        v[1] = deadzone(lx) * RATE[1]
        v[2] = deadzone(rx) * RATE[2]
        if btn & BTN_RB: v[3] += RATE[3]
        if btn & BTN_LB: v[3] -= RATE[3]
        if btn & BTN_ZR: v[4] += RATE[4]   # open  (J5 increases)
        if btn & BTN_ZL: v[4] -= RATE[4]   # close (J5 decreases)

        if not any(abs(x) > 1e-6 for x in v):
            continue

        new = list(cur)
        for i in range(5):
            if abs(v[i]) < 1e-6:
                continue
            new[i] += v[i] * period * speed_scale
            # Allow staying out-of-range, but don't push further out.
            lo = min(JOINT_MIN[i], cur[i])
            hi = max(JOINT_MAX[i], cur[i])
            new[i] = max(lo, min(hi, new[i]))

        if new == cur:
            continue

        try:
            r = tcs.movej(new, profile=1)
        except socket.timeout:
            print("movej timeout"); continue
        except Exception as e:
            print("movej err:", e); break

        if r.startswith('-'):
            # Print rejections at ~1 Hz instead of flooding the console.
            if int(time.time() * 4) % 4 == 0:
                print(f"reject {r} target={[round(x, 2) for x in new]}")
            try: cur = tcs.wherej()
            except Exception: pass
            continue

        cur = new

    print("done.")


if __name__ == '__main__':
    main()
