# DeskMesh

One keyboard. One mouse. One headset. All your computers.

DeskMesh shares the keyboard and mouse attached to a primary Windows PC with a secondary Windows PC over a local network. It also sends the secondary PC's system audio to the primary headset. This is an early MVP intended for two Windows 10/11 PCs on a trusted LAN. It has a terminal interface; there is no screen sharing, GUI, clipboard, or microphone forwarding.

## Quick setup

Put a copy of this folder on both PCs. Then, on each one, open its folder and run:

```powershell
.\deskmesh
```

Or just double-click `deskmesh.cmd`. That is the whole command, and it is the same on both PCs — there is no role to pick and no code to type.

The leading `.\` matters in PowerShell, which does not look in the current folder for programs; a bare `deskmesh` reports "not found" even with `deskmesh.cmd` sitting right there. In `cmd.exe` either spelling works.

Run it on the **PC with the keyboard, mouse and headset first**. On first launch DeskMesh creates a local Python environment and installs its dependencies. If Python is missing it offers to install it with winget. Then:

1. **The first PC** finds nothing on the network, so it offers to be the one that shares its keyboard. Press Enter. It prints its LAN address and waits. Windows may ask for administrator approval to add three firewall rules for **Private** networks. Your Windows network must be set to **Private**, not Public, or the other PC cannot reach this one — DeskMesh warns if it is not.
2. **The second PC** finds the first one and asks whether to let it take over. Press Enter. Both PCs then show the **same four digits**.
3. **Back on the first PC**, check the digits match and press `y`. Pairing is done; the second PC connects on its own.

Leave both terminals running. Later launches skip all of this and start straight away.

If the four digits do not match, press `n` — something is interfering with the connection. The digits confirm that the two PCs are talking directly to each other and not through someone in the middle, so this is worth a glance each time you pair.

DeskMesh saves the shared key in `deskmesh.key` on each PC and the role and main PC address in `deskmesh.json`. Both files stay local and are ignored by Git. The main PC only accepts pairing until it has paired once; after that, use `-Reconfigure` to pair a different PC.

To start over on either PC:

```powershell
.\deskmesh -Reconfigure
```

Changing the main PC's key requires reconfiguring the other PC too.

### If automatic pairing cannot work

Some networks block the broadcasts used to find the other PC, and some block the direct connection pairing needs. Both cases fall back to typing a code:

- If the second PC finds nothing, answer `n` to "share this PC's keyboard" and enter the main PC's IPv4 address by hand.
- If pairing itself fails, DeskMesh offers to take a **backup pairing code** instead. The main PC prints that code during setup, and can show it again with:

```powershell
.venv\Scripts\python.exe deskmesh.py pairing-code
```

Treat that code as a password: it is equivalent to the shared key. Type it only on the other PC.

The main PC accepts TCP port `47660` for control, UDP port `47661` for audio, and UDP port `47662` for discovery from its local subnet on a Private network. If your Windows network is set to Public or an organization controls the firewall, that policy may need to be changed by you or its administrator. DeskMesh never opens ports on your router. If audio is unavailable, you can test input alone with the manual commands below.

### Manual commands

`.\deskmesh` is recommended. Advanced users can still create a key with `deskmesh.py generate-key`, copy it privately to the other PC, and run `deskmesh.py primary` or `deskmesh.py secondary --connect MAIN_IP`. Add `--no-audio` after either role to test input without audio. These manual commands do not change the saved setup role.

`.\deskmesh` passes its arguments through to `start.ps1`, so `-Reconfigure`, `-DebugLog`, `-SkipFirewall` and `-SetupOnly` all work on it.

## Use

| Hotkey on primary keyboard | Action |
| --- | --- |
| Ctrl+Alt+Right | Control secondary |
| Ctrl+Alt+Left | Return to primary |
| Ctrl+Alt+Home | Emergency return to primary |

When connected, the primary accepts switching. Disconnects restore local control automatically. The secondary releases held remote keys and mouse buttons when its connection ends. Keep physical access to both PCs during first setup and test the emergency hotkey before depending on DeskMesh.

## Audio devices and configuration

Run `.venv\Scripts\python.exe deskmesh.py devices` on each PC to list playback and system-audio loopback devices. By default, the secondary captures its default speaker's system audio and the primary plays it through its default speaker. If a device cannot be uniquely identified, edit that PC's generated `deskmesh.json` and set `capture_device` or `playback_device` to a unique part of the displayed device name. You can also set names, ports, hotkeys, remote volume, and buffer size in the JSON file. CLI port and key-file flags override the config file. Both PCs must use the same ports. `remote_volume` ranges from 0 to 1; `buffer_ms` ranges from 40 to 500.

Hotkeys are `+`-separated and need at least two different keys. Recognised names are `ctrl`, `alt`, `shift`, `win`, the arrows, `home`, `end`, `pageup`, `pagedown`, `insert`, `delete`, `escape`, `space`, `tab`, `enter`, `backspace`, `pause`, `capslock`, `scrolllock`, `printscreen`, the letters `a`-`z`, the digits `0`-`9`, and `f1`-`f24` — so `ctrl+alt+j` and `ctrl+shift+f9` are both valid. `Ctrl+Alt+Home` always returns control to the primary even if you rebind `emergency_return`.

The primary's normal Windows audio continues through its usual output. Remote audio is played as another shared-mode application stream. Its UDP packets use PCM16 stereo at 48 kHz and a 100 ms reorder buffer by default.

## Troubleshooting

- **Connection refused or timeout:** Start the main PC first, verify its displayed IPv4 address, put both PCs on the same Private LAN, and approve the main PC firewall prompt. The other PC retries automatically.
- **Authentication failed:** The two PCs no longer share a key. Run `.\deskmesh -Reconfigure` on the main PC, then on the other one, and pair again.
- **"Already paired" when pairing a new PC:** The main PC closes its pairing window after the first success. Run `.\deskmesh -Reconfigure` there to open it again.
- **The four digits do not match:** Press `n`. Re-run pairing on a network you trust; mismatched digits mean the two PCs are not talking directly.
- **No remote audio:** Check that the secondary plays through the selected output, list devices on both PCs, and verify UDP `47661` is allowed through the primary firewall. Try a specific `capture_device` and `playback_device`. Loopback capture is silent while the secondary is playing nothing at all.
- **Input is not injected into an elevated app:** Windows prevents a normal-privilege process from injecting into higher-privilege windows. Run the secondary DeskMesh process at the needed privilege level only if you trust it.
- **Hotkeys fail:** Run the primary from an interactive desktop session. Windows secure desktop (such as the UAC prompt and lock screen) cannot be controlled by these hooks.
- **Hotkeys or audio behaving oddly:** Start both PCs with `.\deskmesh -DebugLog`. The main PC logs detected hotkeys and audio buffer health. Send the last few terminal lines from both PCs when reporting a problem.
- **Unexpected stop:** The same `-DebugLog` option provides detailed logs. Manual commands can use `deskmesh.py --debug primary`.

## Security and limits

Use DeskMesh only on trusted local networks. **Do not port-forward DeskMesh ports to the public internet.**

Pairing runs an ephemeral Diffie-Hellman exchange (RFC 3526 group 14), and both PCs show four digits derived from the resulting secret. Someone merely watching the network learns nothing from it. Someone actively sitting in the middle ends up with a different secret on each side, so the digits disagree — which is why checking them matters. The 32-byte shared key is then handed over wrapped under that secret, and afterwards it authenticates the TCP session and every UDP audio packet.

Input and audio traffic themselves are **not encrypted**. Anyone with the key or the backup pairing code can control the secondary while connected; protect both and delete `deskmesh.key` when retiring a PC. The main PC stops accepting pairing after the first success, so a PC that joins the network later cannot ask to be paired without you re-running setup.

This release is Windows-first, supports one secondary, and has no installer. LAN discovery uses broadcasts and may not work across subnets or on networks that block broadcast traffic; manual IP entry remains available. The app does not control UAC secure desktops.

Mouse movement is captured from low-level Windows hooks as a delta from a cursor parked at the centre of the primary's virtual desktop, so no direction is clipped by a screen edge, and is replayed on the secondary as an absolute virtual-desktop position so the secondary's pointer acceleration is not applied a second time. Switching to the secondary moves the primary's cursor to the centre of its desktop and returns it where you left it on the way back. Audio latency and mouse feel across different DPI settings still need validation on two physical PCs; no hardware validation has been performed in this repository. The secondary retries the primary after a disconnect, and backs off to 15 seconds when the pairing key does not match.

## Development

Run non-hardware tests with `py -m unittest discover -s tests -v`. Tests do not move the actual mouse or type into Windows. See [REQUIREMENTS.md](REQUIREMENTS.md) for MVP acceptance tests and the roadmap. Contributions and reproducible reports from two-PC hardware testing are welcome. DeskMesh is available under the [MIT License](LICENSE).
