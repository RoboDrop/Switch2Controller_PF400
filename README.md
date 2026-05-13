# Nintendo Switch 2 Pro Controller → PF400

Jog a Precise Automation **PF400** robot with a **Nintendo Switch 2 Pro Controller** plugged into your computer via USB. Pure Python, no Steam, no extra drivers — just `hidapi` and `pyusb`.

The NS2 Pro Controller doesn't speak standard HID out of the box. This package includes a small USB-init routine (adapted from [dannydarvish/Switch2ProMac](https://github.com/dannydarvish/Switch2ProMac)) that flips it into HID mode, plus the jog script that talks to the robot's TCP Command Server (TCS) on port 10100.

## What's in the box

| File | What it does |
|---|---|
| `ns2_init.py` | Sends a magic USB sequence that switches the NS2 Pro into HID streaming mode. Run this every time the controller is plugged in or powered up. |
| `ns2_pf400.py` | The main script. Opens the controller via HID and streams `movej` commands to the robot. |
| `ns2_dpad_probe.py` | Helper. If your D-pad fires the wrong joint, run this — it asks you to press each direction and prints the bit each one toggles. Patch `decode_buttons` in `ns2_pf400.py` accordingly. |
| `requirements.txt` | Two pip packages: `hidapi`, `pyusb`. |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On macOS, `pyusb` needs `libusb`:
```bash
brew install libusb
```

On Linux, you'll likely need a udev rule so the script can claim the USB interface without root. Drop this at `/etc/udev/rules.d/99-ns2pro.rules` and reload (`sudo udevadm control --reload && sudo udevadm trigger`):
```
SUBSYSTEM=="usb", ATTRS{idVendor}=="057e", ATTRS{idProduct}=="2069", MODE="0666"
```

## Usage

1. **Plug in the controller** via USB-C.
2. **Initialize HID mode** (every plug-in / power-cycle):
   ```bash
   python3 ns2_init.py
   ```
   You should see ~300 reports stream in over 4 seconds — proof it's working.
3. **Make sure your robot is reachable and TCS is up on `192.168.0.1:10100`** (the default). Robot must be homed and the Virtual MCP in Computer Control mode.
4. **Run the jog script**:
   ```bash
   python3 ns2_pf400.py
   ```

Stop with the **B** button on the controller.

## Controls

| Input | Action |
|---|---|
| **D-pad up / down** | J1 vertical column (up = raise) |
| **Left stick X** | J2 shoulder |
| **Right stick X** | J3 elbow |
| **L / R** | J4 wrist rotate (digital, hold to jog) |
| **ZL / ZR** | J5 gripper close / open |
| **YL / YR** (3rd L/R pair / back paddles) | Speed scale − / + |
| **Plus** | Reset speed scale to 1.0 |
| **A** | Print current pose (for teach points) |
| **Y** | Toggle motor power (HP) on/off |
| **B** | Halt and quit |
| **Minus** | Emergency stop (halt + HP off, then quit) |

The left stick Y-axis and right stick Y-axis are ignored (J1 lives on the D-pad).

## Configuration

Everything you'd reasonably want to tune is in the `CONFIG` block at the top of `ns2_pf400.py`:

- **`ROBOT_HOST` / `ROBOT_PORT` / `ROBOT_NUM`** — TCS connection.
- **`JOINT_MIN` / `JOINT_MAX`** — joint soft limits. These are *measured for one specific PF400*. Yours will differ — sweep yours and update these. The script clamps targets to these bounds, but allows the robot to remain at out-of-range positions and only refuses to push further out.
- **`RATE`** — per-axis jog rate at full deflection (J1 mm/s, J2-J4 deg/s, J5 mm/s).
- **`DEADZONE`** — stick deadzone (0..1).
- **`TICK_HZ`** — command rate. 40 Hz is a good balance.
- **`TCS_PROFILE` / `TCS_MSPEED`** — motion profile and system master-speed. `InRange=-1` enables continuous-path blending — this is what makes jogging feel smooth. Don't change it to a positive value unless you want the robot to decelerate to a stop after every commanded segment.

## Mapping varies by controller (probably)

The NS2 Pro's HID layout is reverse-engineered, not documented. The bit positions in `decode_buttons` were verified empirically on one specific unit. If your D-pad fires the wrong joint or you get unexpected behavior:

```bash
python3 ns2_dpad_probe.py
```

It walks you through pressing each D-pad direction and prints the bit each one fires. Patch the four `if hat & 0xXX:` lines in `decode_buttons` to match.

For other buttons, set `NS2_DEBUG=1` and watch the button bitmap as you press things:

```bash
NS2_DEBUG=1 python3 ns2_pf400.py
```

## Why does the controller keep losing HID mode?

The init isn't persistent. The Switch 2 Pro Controller reverts to its native Nintendo protocol every time it loses power (USB unplug, battery dead, system sleep). Just re-run `ns2_init.py` whenever the script can't find the controller.

## Smoothness — the boring details

If you've used a similar script before and it felt stuttery, three settings dominate:

1. **`InRange = -1`** (blended motion) vs the default positive value. With a positive InRange, the controller decelerates to a near-stop at every commanded target, producing jerkiness when you stream commands rapidly. `-1` lets segments flow into each other.
2. **High accel** (`500` in the profile). Without it, the robot can't track rapid stick-direction changes.
3. **`mspeed 100`**. Many systems default to 50, which silently halves all profile speeds.

All three are pre-set in `TCS_PROFILE` and `TCS_MSPEED`.

## Credits

- USB-mode init reverse-engineering by [@dannydarvish](https://github.com/dannydarvish) in [Switch2ProMac](https://github.com/dannydarvish/Switch2ProMac).
- Robot control loop and TCS protocol from the Precise Automation PF400 / TCP Command Server documentation.
