# DeskMesh

One keyboard. One mouse. One headset. All your computers.

DeskMesh shares the keyboard and mouse attached to a primary Windows PC with a secondary Windows PC over a local network. It also sends the secondary PC's system audio to the primary headset. This is an early MVP intended for two Windows 10/11 PCs on a trusted LAN. It has a terminal interface; there is no screen sharing, GUI, clipboard, or microphone forwarding.

## Quick setup

Install Python 3.11 or newer on **both** PCs. Put a copy of this repository on each PC, open PowerShell in its folder, and run the **same command on both**:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

Run it on the **main PC first**. On first launch, DeskMesh creates a local Python environment, installs dependencies, and asks:

1. Choose **1: main/server** on the PC with the keyboard, mouse, and headset. It shows its LAN IPv4 address and a pairing code. Windows may ask for administrator approval to add three firewall rules for **Private** local networks.
2. Choose **2: other device** on the second PC. DeskMesh looks for the main PC automatically. If the network blocks discovery, enter the IPv4 address shown on the main PC. Enter its pairing code once. No key-file copying is needed.
3. Leave both terminals running. The same command starts the saved role immediately on later launches without asking again.

The pairing code is a secret. Type it only on the other PC; do not post it or share it on a public network. DeskMesh saves the derived key in `deskmesh.key` on each PC and saves the role and main PC address in `deskmesh.json`. Both files stay local and are ignored by Git.

To choose the role again, use:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Reconfigure
```

Changing the main PC's pairing key requires reconfiguring the other PC too. To display the existing pairing code again on the main PC:

```powershell
.venv\Scripts\python.exe deskmesh.py pairing-code
```

The main PC accepts TCP port `47660` for control, UDP port `47661` for audio, and UDP port `47662` for discovery from its local subnet on a Private network. If your Windows network is set to Public or an organization controls the firewall, that policy may need to be changed by you or its administrator. DeskMesh never opens ports on your router. If audio is unavailable, you can test input alone with the manual commands below.

### Manual commands

The one-command setup is recommended. Advanced users can still create a key with `deskmesh.py generate-key`, copy it privately to the other PC, and run `deskmesh.py primary` or `deskmesh.py secondary --connect MAIN_IP`. Add `--no-audio` after either role to test input without audio. These manual commands do not change the saved setup role.

## Use

| Hotkey on primary keyboard | Action |
| --- | --- |
| Ctrl+Alt+Right | Control secondary |
| Ctrl+Alt+Left | Return to primary |
| Ctrl+Alt+Home | Emergency return to primary |

When connected, the primary accepts switching. Disconnects restore local control automatically. The secondary releases held remote keys and mouse buttons when its connection ends. Keep physical access to both PCs during first setup and test the emergency hotkey before depending on DeskMesh.

## Audio devices and configuration

Run `.venv\Scripts\python.exe deskmesh.py devices` on each PC to list playback and system-audio loopback devices. By default, the secondary captures its default speaker's system audio and the primary plays it through its default speaker. If a device cannot be uniquely identified, edit that PC's generated `deskmesh.json` and set `capture_device` or `playback_device` to a unique part of the displayed device name. You can also set names, ports, hotkeys, remote volume, and buffer size in the JSON file. CLI port and key-file flags override the config file. Both PCs must use the same ports. `remote_volume` ranges from 0 to 1; `buffer_ms` ranges from 40 to 500.

The primary's normal Windows audio continues through its usual output. Remote audio is played as another shared-mode application stream. Its UDP packets use PCM16 stereo at 48 kHz and a 100 ms reorder buffer by default.

## Troubleshooting

- **Connection refused or timeout:** Start the main PC first, verify its displayed IPv4 address, put both PCs on the same Private LAN, and approve the main PC firewall prompt. The other PC retries automatically.
- **Authentication failed:** Reconfigure the other PC with the pairing code shown by `deskmesh.py pairing-code` on the main PC.
- **No remote audio:** Check that the secondary plays through the selected output, list devices on both PCs, and verify UDP `47661` is allowed through the primary firewall. Try a specific `capture_device` and `playback_device`.
- **Input is not injected into an elevated app:** Windows prevents a normal-privilege process from injecting into higher-privilege windows. Run the secondary DeskMesh process at the needed privilege level only if you trust it.
- **Hotkeys fail:** Run the primary from an interactive desktop session. Windows secure desktop (such as the UAC prompt and lock screen) cannot be controlled by these hooks.
- **Hotkeys or audio behaving oddly:** Start both PCs with `powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -DebugLog`. The main PC logs detected hotkeys and audio buffer health. Send the last few terminal lines from both PCs when reporting a problem.
- **Unexpected stop:** The same `-DebugLog` option provides detailed logs. Manual commands can use `deskmesh.py --debug primary`.

## Security and limits

Use DeskMesh only on trusted local networks. **Do not port-forward DeskMesh ports to the public internet.** The 128-bit random pairing code creates a 32-byte shared key that authenticates the TCP session and each UDP audio packet. Input and audio traffic are **not encrypted**. Anyone with the pairing code or key can control the secondary while connected; protect both and delete the key when retiring a PC.

This release is Windows-first, supports one secondary, and has no installer. LAN discovery uses broadcasts and may not work across subnets or on networks that block broadcast traffic; manual IP entry remains available. The app does not control UAC secure desktops. Mouse movement uses low-level Windows hooks and relative injection; mouse feel and audio latency still need validation on two physical PCs and across different mouse DPI settings. No hardware validation has been performed in this repository. The secondary retries the primary after a disconnect.

## Development

Run non-hardware tests with `py -m unittest discover -s tests -v`. Tests do not move the actual mouse or type into Windows. See [REQUIREMENTS.md](REQUIREMENTS.md) for MVP acceptance tests and the roadmap. Contributions and reproducible reports from two-PC hardware testing are welcome. DeskMesh is available under the [MIT License](LICENSE).
