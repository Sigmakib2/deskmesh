# DeskMesh — Full Product & Engineering Requirements

## 1. Project Overview

**DeskMesh** is an open-source multi-computer control and audio-sharing application for people who use multiple computers at one desk.

The goal is to let one user operate multiple computers using:

- One keyboard
- One mouse
- One headset or headphone setup

DeskMesh should work over the local network and make multiple computers feel like parts of one workstation.

The first implementation will target Windows and use Python.

---

# 2. Product Positioning

## Project Name

**DeskMesh**

## Tagline

**One keyboard. One mouse. One headset. All your computers.**

## Short Description

Share one keyboard, mouse, and headset across multiple computers over your local network.

## SEO-Friendly Description

Open-source software KVM for sharing keyboard, mouse, and system audio across multiple computers over LAN.

---

# 3. Core User Problem

Many users operate multiple computers at the same desk:

- Main workstation
- Secondary desktop
- Laptop
- Home server
- Personal PC
- Work PC
- Gaming PC

The typical setup becomes inconvenient because each computer may require:

- A separate keyboard
- A separate mouse
- Separate headphones or speakers
- Manual audio switching
- Manual physical cable switching

DeskMesh should remove this friction.

The user should be able to keep their keyboard, mouse, and headset connected to one primary workstation while controlling and hearing other computers over the LAN.

---

# 4. Long-Term Product Concept

```text
                     USER
                      │
        ┌─────────────┼─────────────┐
        │             │             │
     Keyboard        Mouse        Headset
        │             │             │
        └─────────────┼─────────────┘
                      │
                  DeskMesh
                      │
                  Local LAN
                      │
        ┌─────────────┼─────────────┐
        │             │             │
        ▼             ▼             ▼
      PC A          PC B          PC C
```

DeskMesh should eventually support:

- Keyboard sharing
- Mouse sharing
- System audio streaming
- Audio mixing
- Active-device audio following
- Microphone forwarding
- Clipboard sharing
- File transfer
- Multiple computers
- Automatic discovery
- Edge-based switching
- Cross-platform operation

The MVP should remain focused and reliable.

---

# 5. MVP Scope

The first production-worthy MVP should support:

- Windows 10
- Windows 11
- Two computers
- Same local network
- One primary/control PC
- One secondary PC
- Keyboard forwarding
- Mouse forwarding
- Hotkey-based computer switching
- Secondary PC system audio streamed to the primary PC
- Audio played through the primary PC's selected output device
- Authentication
- Heartbeats
- Safe disconnect handling
- Terminal-based status
- Config file
- Automated tests where practical

The MVP does **not** require:

- GUI
- System tray application
- Cross-platform support
- Clipboard synchronization
- File transfer
- Automatic edge switching
- Microphone forwarding
- Internet relay
- Cloud services
- Mobile support

---

# 6. Primary User Experience

Assume:

```text
PC A = Primary
PC B = Secondary
```

Physical devices are connected to PC A:

```text
Keyboard → PC A
Mouse → PC A
Headset → PC A
```

DeskMesh runs on both machines.

When PC A is active:

```text
Keyboard → PC A
Mouse → PC A
Audio:
  PC A audio → Headset
  PC B audio → optionally mixed into Headset
```

When PC B is active:

```text
Keyboard → PC B
Mouse → PC B
PC B audio → Headset connected to PC A
```

The user should not need to physically move cables or change devices.

---

# 7. Active Computer Concept

DeskMesh must maintain an explicit active-computer state.

Example:

```python
active_machine = "primary"
```

or:

```python
active_machine = "secondary"
```

When primary is active:

- Keyboard works locally
- Mouse works locally

When secondary is active:

- Keyboard events are forwarded
- Mouse events are forwarded
- Local input must be suppressed where appropriate
- Switching hotkeys must still work
- Emergency recovery must still work

---

# 8. Default Hotkeys

The initial default hotkeys should be:

```text
Ctrl + Alt + Right
→ Switch to secondary

Ctrl + Alt + Left
→ Switch to primary

Ctrl + Alt + Home
→ Emergency return to primary
```

Hotkeys must be configurable.

The emergency hotkey must always prioritize recovery.

---

# 9. Emergency Recovery Requirements

The user must never become permanently locked out of the primary PC.

The primary machine must automatically regain control when:

- Secondary connection fails
- Secondary app exits
- Secondary crashes
- Network connection disappears
- Authentication/session fails
- Heartbeat timeout occurs

The emergency hotkey must:

```text
Ctrl + Alt + Home
```

restore primary control immediately.

Primary-local input should always become usable if DeskMesh itself terminates.

---

# 10. Keyboard Requirements

DeskMesh must support at minimum:

```text
A-Z
0-9
Space
Enter
Backspace
Tab
Escape

Arrow keys

Shift
Ctrl
Alt
Windows key

F1-F12

Home
End
Page Up
Page Down
Insert
Delete

Caps Lock
Num Lock

Numpad keys
```

Keyboard combinations must work.

Examples:

```text
Ctrl + C
Ctrl + V
Ctrl + Z
Ctrl + Shift + S
Alt + Tab
Shift + Arrow
Windows + R
```

---

# 11. Keyboard State Tracking

DeskMesh must track pressed keys.

Example:

```python
pressed_keys = set()
```

When a key-down event is forwarded:

- Add it to tracked pressed keys

When a key-up event is forwarded:

- Remove it

On disconnect:

- Release all remotely-held modifier keys
- Release all remotely-held normal keys when possible

This prevents stuck keys such as:

```text
Ctrl
Shift
Alt
Windows
```

---

# 12. Mouse Requirements

Support:

- Relative movement
- Left button
- Right button
- Middle button
- Button down
- Button up
- Vertical scroll wheel
- Drag-and-drop

Future support may include:

- Horizontal wheel
- Mouse button 4
- Mouse button 5
- High-resolution wheel input
- Gaming mouse extra buttons

---

# 13. Relative Mouse Movement

Remote mouse control should use relative movement whenever possible.

Example:

```json
{
  "type": "mouse_move",
  "dx": 8,
  "dy": -3
}
```

Avoid depending primarily on absolute coordinates.

This helps when machines have different:

- Screen resolutions
- Monitor dimensions
- DPI scaling
- Multi-monitor layouts

Example:

```text
PC A
1920x1080 @ 100%

PC B
2560x1440 @ 125%
```

Relative movement should remain natural.

---

# 14. Mouse Button Events

Mouse button messages must distinguish between press and release.

Example:

```json
{
  "type": "mouse_button",
  "button": "left",
  "pressed": true
}
```

and:

```json
{
  "type": "mouse_button",
  "button": "left",
  "pressed": false
}
```

This is required for:

- Drag and drop
- Click-and-hold
- Selection
- Window resizing

---

# 15. Mouse Wheel Events

Example:

```json
{
  "type": "mouse_wheel",
  "delta": 120
}
```

Scrolling should behave similarly to normal Windows scrolling.

---

# 16. Local Input Suppression

When the secondary computer is active:

- Keyboard input intended for the secondary should not also type on the primary
- Mouse movement should not freely move the primary cursor
- Mouse clicks should not activate primary windows

The primary must still detect:

- Switch hotkeys
- Emergency return hotkey

Local suppression must be implemented carefully to avoid input lockout.

---

# 17. Input Technology

Use Python initially.

Target:

```text
Python 3.11+
```

Prefer native Windows APIs for final core input behavior.

Useful APIs may include:

```text
SendInput
SetWindowsHookEx
LowLevelKeyboardProc
LowLevelMouseProc
RegisterHotKey
Raw Input
```

These may be accessed through:

- ctypes
- pywin32

High-level libraries may be used during prototyping, but avoid relying heavily on PyAutoGUI for final core functionality.

---

# 18. Audio Requirements

DeskMesh must incorporate the system-audio streaming capability directly into the same project.

The MVP must allow:

```text
Secondary PC system audio
→ LAN
→ Primary PC
→ Primary headset/output device
```

The primary computer's normal local audio must continue to function.

---

# 19. Audio Capture

On Windows, secondary system audio should be captured using WASAPI loopback or equivalent.

Suggested approach:

- Python
- SoundCard library
- WASAPI loopback

The implementation should capture the selected playback device's system mix.

It must not require microphone capture for the MVP.

---

# 20. Audio Playback

The primary machine must:

- Receive remote audio
- Buffer packets
- Play through the selected output device
- Mix remote audio with normal local Windows audio
- Avoid taking exclusive control of the primary output device

The user's existing primary audio should continue normally.

---

# 21. Audio Format

A sensible initial target:

```text
Sample rate: 48 kHz
Channels: Stereo
Format: PCM16
```

Packet size should remain safely below the normal Ethernet MTU when practical.

The existing prototype strategy of small audio packets is acceptable.

---

# 22. Audio Networking

Audio should use UDP.

Reasons:

- Low latency
- Small packet loss is preferable to waiting for retransmission
- Real-time audio should not accumulate stale packets

The system should implement:

- Sequence numbers
- Session IDs
- Small jitter/reorder buffer
- Late-packet dropping
- Backlog control
- Basic underrun reporting

---

# 23. Audio Buffering

The primary should maintain a bounded jitter buffer.

The buffer must:

- Reorder packets
- Handle isolated missing packets
- Prevent unlimited backlog
- Drop stale packets when necessary
- Refill after starvation

Default buffering should prioritize stable normal LAN operation over ultra-low gaming latency.

The buffer amount should be configurable.

Example:

```text
50 ms
100 ms
150 ms
```

A sensible default may be:

```text
100 ms
```

until hardware testing justifies changing it.

---

# 24. Audio Mixing

DeskMesh should support remote stream volume.

For future multi-computer support:

```text
PC B volume: 70%
PC C volume: 40%
```

The mixer should:

- Sum active remote streams
- Prevent severe clipping
- Support per-device mute
- Support per-device volume

---

# 25. Audio Modes

The architecture should support the following modes, even if not all are required for the first MVP.

## Mix

Hear all connected remote computers.

```text
Audio Mode: MIX
```

## Follow Active

The currently-controlled PC is emphasized or unmuted.

```text
Audio Mode: FOLLOW
```

Example:

```text
Active PC: B

B audio: 100%
C audio: 20%
```

## Solo Active

Only hear the active machine's remote audio.

```text
Audio Mode: SOLO
```

For the two-PC MVP, a simple mix mode is sufficient, but the architecture should not prevent FOLLOW or SOLO later.

---

# 26. Audio Device Selection

DeskMesh should provide device listing.

Example:

```powershell
python deskmesh.py devices
```

It should show:

- Playback devices
- Loopback-capable output devices
- Device indices/names

The user should be able to choose:

- Secondary capture device
- Primary playback device

---

# 27. Microphone Forwarding

Microphone forwarding is a future feature and is **not required for the MVP**.

Future concept:

```text
Primary microphone
→ LAN
→ Secondary PC
```

Important limitation:

Making the forwarded microphone appear as a selectable Windows microphone in ordinary applications may require a virtual audio endpoint or driver.

Do not attempt this during the initial MVP.

---

# 28. High-Level Architecture

Every running DeskMesh installation should be considered a node.

```text
DeskMesh Node
├── Identity
├── Networking
├── Authentication
├── Capabilities
├── Input
├── Audio
└── Status
```

Avoid permanently hardcoding the program as two completely different applications.

Roles may differ during runtime, but the same codebase should run on all machines.

---

# 29. Node Capabilities

Each node should conceptually advertise capabilities.

Example:

```json
{
  "name": "DESKTOP-B",
  "capabilities": {
    "keyboard_receive": true,
    "mouse_receive": true,
    "audio_send": true,
    "audio_receive": true
  }
}
```

The first MVP may keep capability negotiation simple, but the protocol should remain extensible.

---

# 30. Suggested CLI

Primary:

```powershell
python deskmesh.py primary
```

Secondary:

```powershell
python deskmesh.py secondary --connect 192.168.1.10
```

Optional commands:

```powershell
python deskmesh.py devices
python deskmesh.py generate-key
python deskmesh.py status
```

Optional flags:

```text
--port
--audio-port
--device
--name
--key-file
--debug
--buffer-ms
```

Exact CLI structure may change if a cleaner interface emerges.

---

# 31. Networking Architecture

DeskMesh should use separate channels for different traffic types.

Recommended:

```text
TCP Control Channel
├── Authentication
├── Capability exchange
├── Keyboard events
├── Mouse buttons
├── Mouse wheel
├── Switching
├── Heartbeats
├── Status
└── Configuration negotiation

UDP Realtime Channel
├── Audio
└── Optional mouse movement later
```

The first input MVP may send all input events over TCP for simplicity.

If mouse movement latency becomes noticeable, movement events may later move to UDP.

---

# 32. Suggested Ports

Example defaults:

```text
47660 TCP control/input
47661 UDP audio
47662 UDP input movement (future)
```

Ports must be configurable.

Do not rely on port forwarding.

---

# 33. TCP Message Framing

Do not assume:

```python
socket.recv()
```

returns exactly one application message.

Use proper framing.

Preferred:

```text
4-byte length prefix
+
JSON payload
```

Example message:

```json
{
  "type": "keyboard",
  "key": "A",
  "pressed": true
}
```

Do not use pickle for network transport.

---

# 34. Protocol Message Types

Initial protocol should support message types such as:

```text
hello
authenticate
capabilities
heartbeat
keyboard
mouse_move
mouse_button
mouse_wheel
switch
status
disconnect
```

Audio packets should use a separate compact binary format.

---

# 35. Authentication

DeskMesh must not allow arbitrary LAN devices to inject input.

Authentication is required.

For MVP:

```text
deskmesh.key
```

may be generated on the primary and copied to trusted secondary machines.

Use:

```text
HMAC-SHA256
```

or equivalent secure authentication.

Never send raw passwords.

README must explicitly warn:

```text
Use DeskMesh only on trusted local networks.
Do not port-forward DeskMesh ports to the public internet.
```

---

# 36. Encryption

Full transport encryption is not mandatory for the first trusted-LAN MVP.

However:

- Authentication is mandatory
- Protocol design should allow encryption later
- Do not claim traffic is encrypted if it is not

Future versions may use:

- TLS
- Noise protocol
- Authenticated encryption

---

# 37. Heartbeats

The control connection should send a heartbeat.

Example:

```text
every 1 second
```

A peer should be considered disconnected after approximately:

```text
3-5 seconds
```

without a valid heartbeat.

Exact values should be configurable or easy to change.

---

# 38. Disconnect Behavior

When the secondary disconnects:

Primary must:

- Immediately restore local control
- Clear remote-active state
- Stop attempting to forward input
- Display connection status

Secondary must:

- Release held keys
- Release held mouse buttons
- Stop remote input injection

Audio:

- Remove stale audio stream
- Stop playback of that remote stream
- Avoid hanging buffers indefinitely

---

# 39. Connection Workflow

Suggested workflow:

```text
1. Primary starts
2. Primary listens
3. Secondary starts
4. Secondary connects to primary IP
5. Authentication occurs
6. Capability exchange occurs
7. Audio stream starts
8. Input switching becomes available
9. Heartbeats continue
```

Example primary output:

```text
DeskMesh Primary
Listening on 0.0.0.0:47660
Waiting for devices...
```

Secondary:

```text
Connecting to 192.168.1.10:47660...
Connected to DESKTOP-A
Authentication successful
Audio streaming active
```

Primary:

```text
Connected: DESKTOP-B
Audio stream: active
[ACTIVE] DESKTOP-A
```

---

# 40. Configuration

Support a configuration file such as:

```text
deskmesh.json
```

Example:

```json
{
  "name": "DESKTOP-A",
  "control_port": 47660,
  "audio_port": 47661,
  "switch_to_secondary": "ctrl+alt+right",
  "switch_to_primary": "ctrl+alt+left",
  "emergency_return": "ctrl+alt+home",
  "audio_mode": "mix",
  "buffer_ms": 100
}
```

Secondary example:

```json
{
  "name": "DESKTOP-B",
  "control_port": 47660,
  "audio_port": 47661,
  "audio_send": true
}
```

CLI options should override config-file values.

---

# 41. Suggested Repository Structure

```text
deskmesh/
│
├── deskmesh.py
├── REQUIREMENTS.md
├── README.md
├── requirements.txt
├── config.example.json
├── .gitignore
│
├── deskmesh/
│   ├── __init__.py
│   ├── config.py
│   ├── logging.py
│   │
│   ├── core/
│   │   ├── node.py
│   │   ├── state.py
│   │   ├── capabilities.py
│   │   └── lifecycle.py
│   │
│   ├── network/
│   │   ├── control.py
│   │   ├── protocol.py
│   │   ├── framing.py
│   │   ├── auth.py
│   │   ├── heartbeat.py
│   │   └── discovery.py
│   │
│   ├── input/
│   │   ├── hooks.py
│   │   ├── keyboard.py
│   │   ├── mouse.py
│   │   ├── injector.py
│   │   ├── hotkeys.py
│   │   └── switcher.py
│   │
│   ├── audio/
│   │   ├── capture.py
│   │   ├── sender.py
│   │   ├── receiver.py
│   │   ├── protocol.py
│   │   ├── buffer.py
│   │   ├── mixer.py
│   │   └── devices.py
│   │
│   └── platform/
│       └── windows/
│           ├── input_api.py
│           └── audio_api.py
│
└── tests/
    ├── test_protocol.py
    ├── test_framing.py
    ├── test_auth.py
    ├── test_state.py
    ├── test_key_state.py
    ├── test_audio_protocol.py
    └── test_audio_buffer.py
```

This is guidance, not a rigid requirement.

The main principle is separation between:

- Core state
- Networking
- Input capture
- Input injection
- Audio capture
- Audio playback
- Authentication
- Configuration
- OS-specific code

---

# 42. Concurrency

Input, networking, heartbeat, and audio must not block one another.

Likely concurrent tasks:

```text
Main lifecycle
Input hook
TCP control receiver
TCP sender
Heartbeat
Audio capture
UDP audio sender
UDP audio receiver
Audio playback
```

Use:

- threading
- asyncio

or a clean combination where justified.

Avoid uncontrolled thread proliferation.

Avoid busy loops.

---

# 43. Performance Priorities

DeskMesh should prioritize:

1. Reliability
2. Safe recovery
3. Responsive input
4. Stable audio
5. Low setup complexity
6. Low CPU usage

MVP does not need competitive-gaming latency.

Normal productivity use should feel responsive.

---

# 44. Queue Management

Never allow unbounded realtime queues.

Mouse movement:

- May be coalesced
- Older stale movement may be dropped

Keyboard:

- Key down/up events must not be intentionally dropped

Audio:

- Old packets should be dropped rather than accumulating long latency

---

# 45. Logging

Provide readable terminal logging.

Example:

```text
[INFO] DeskMesh starting
[INFO] Listening on TCP 47660
[INFO] Listening on UDP 47661
[INFO] Connected: DESKTOP-B
[INFO] Authentication successful
[AUDIO] Receiving DESKTOP-B
[ACTIVE] DESKTOP-A
[ACTIVE] DESKTOP-B
[WARN] DESKTOP-B heartbeat lost
[INFO] Restored local control
```

Do not print every input or audio packet by default.

Support:

```text
--debug
```

for verbose diagnostics.

---

# 46. Error Handling

Errors should be understandable.

Bad:

```text
WinError 10061
```

Better:

```text
Unable to connect to 192.168.1.10:47660.
Make sure DeskMesh is running on the primary computer and Windows Firewall allows Private-network access.
```

Handle clearly:

- Invalid IP
- Connection refused
- Port already in use
- Invalid key
- Missing key
- Authentication failure
- Unsupported OS
- Missing audio device
- Audio capture failure
- Playback device failure
- Secondary disconnect
- Primary disconnect
- Firewall-related connection failure where detectable

---

# 47. Windows Firewall Guidance

README should instruct users:

```text
Allow Python/DeskMesh on Private networks.
```

Do not recommend exposing DeskMesh on public networks.

Do not instruct users to port-forward DeskMesh.

---

# 48. Dependencies

Keep dependencies minimal.

Prefer standard library:

```text
socket
threading
asyncio
json
struct
hmac
hashlib
secrets
collections
ctypes
```

Potential external libraries:

```text
numpy
soundcard
pywin32
pynput
```

Only add dependencies that are genuinely necessary.

---

# 49. Windows-First Strategy

The initial project is Windows-first.

Do not sacrifice Windows quality for premature cross-platform abstractions.

However, isolate OS-specific logic under a platform layer so that future ports are realistic.

Example:

```text
deskmesh/platform/windows/
```

Later:

```text
deskmesh/platform/linux/
deskmesh/platform/macos/
```

---

# 50. Automatic Discovery — Future

The MVP may require the user to manually enter the primary LAN IP.

Future versions should support LAN discovery.

Potential approaches:

- UDP broadcast
- mDNS
- Zeroconf

Do not make discovery a blocker for the MVP.

---

# 51. Edge Switching — Future

After hotkey switching is stable, DeskMesh should support screen-edge switching.

Example layout:

```text
PC A → PC B
```

Moving through the right edge of PC A should activate PC B.

Moving back through the left edge should activate PC A.

Architecture should expose a generic function such as:

```python
switch_active_machine(target)
```

so hotkeys and edge switching use the same state logic.

---

# 52. Multi-PC Support — Future

Long-term topology:

```text
        PC C
         ↑
PC A → PC B → PC D
```

The architecture should eventually support three or more nodes.

Do not implement this until the two-PC case is stable.

---

# 53. Clipboard Sharing — Future

Future versions may synchronize:

- Text
- URLs
- Images
- File references

Clipboard functionality should remain optional.

Do not include it in the MVP.

---

# 54. File Transfer — Future

Future versions may support:

- Explicit send-file action
- Drag-and-drop
- Clipboard-based file transfer

This is outside the MVP.

---

# 55. Security Principles

DeskMesh controls keyboard and mouse input, so security must be taken seriously.

Required:

- Authentication
- Trusted-LAN assumption clearly documented
- No pickle networking
- No arbitrary code execution
- Validate protocol messages
- Validate lengths
- Validate device names
- Bound buffers and queues
- Reject malformed packets
- Avoid unauthenticated input injection

Future:

- Pairing codes
- Encrypted transport
- Trust management
- Device revocation

---

# 56. Testing Requirements

Automated tests should cover as much non-hardware logic as possible.

## Protocol Tests

Test:

- Encoding
- Decoding
- Invalid messages
- Partial TCP frames
- Multiple frames in one receive
- Oversized messages
- Unsupported message types

## Authentication Tests

Test:

- Valid key
- Invalid key
- Modified authentication tag
- Missing key

## State Tests

Test:

- Primary → Secondary
- Secondary → Primary
- Emergency return
- Disconnect recovery

## Key-State Tests

Test:

- Key press tracking
- Key release tracking
- Modifier cleanup
- Disconnect cleanup

## Audio Protocol Tests

Test:

- Packet encoding
- Packet decoding
- Sequence handling
- Invalid packet size
- Invalid authentication
- Session changes

## Buffer Tests

Test:

- Packet reorder
- Missing packet
- Backlog trimming
- Starvation
- Session reset

## Local Network Tests

Use localhost where practical.

Do not make automated tests move the real mouse or type into the developer's desktop unless explicitly invoked.

---

# 57. Hardware Validation Checklist

On two real Windows PCs, verify:

## Keyboard

- Normal letters
- Numbers
- Shift
- Ctrl
- Alt
- Windows key
- Function keys
- Ctrl+C
- Ctrl+V
- Alt+Tab
- Windows+R

## Mouse

- Movement
- Left click
- Right click
- Double click
- Drag
- Scroll

## Switching

- Primary → Secondary
- Secondary → Primary
- Emergency hotkey

## Recovery

- Kill secondary process
- Disconnect Ethernet/Wi-Fi
- Close laptop lid where applicable
- Restart secondary
- Restart primary

Primary input must recover safely.

## Audio

- Music
- YouTube
- Notification sounds
- Voice/video call audio
- Long-duration playback
- Wi-Fi test
- Ethernet test

Listen for:

- Crackling
- Distortion
- Dropouts
- Buffer buildup
- Excess latency

---

# 58. MVP Acceptance Criteria

DeskMesh MVP is successful when all of the following work on two Windows PCs.

## Setup

Primary:

```text
Keyboard connected
Mouse connected
Headset connected
DeskMesh Primary running
```

Secondary:

```text
DeskMesh Secondary running
Connected over LAN
```

## Acceptance Test 1

With primary active:

```text
Keyboard controls primary
Mouse controls primary
```

## Acceptance Test 2

Press:

```text
Ctrl + Alt + Right
```

DeskMesh reports:

```text
Active computer: Secondary
```

## Acceptance Test 3

Typing:

```text
Hello World
```

into Notepad appears on the secondary.

## Acceptance Test 4

Mouse movement controls the secondary.

## Acceptance Test 5

Remote:

```text
left click
right click
double click
drag-and-drop
scroll
```

all work.

## Acceptance Test 6

Common shortcuts work remotely.

Examples:

```text
Ctrl+C
Ctrl+V
Alt+Tab
Ctrl+Shift+S
Windows+R
```

subject to Windows security restrictions.

## Acceptance Test 7

Secondary system audio plays through the primary headset.

## Acceptance Test 8

Primary local audio continues normally while remote audio is active.

## Acceptance Test 9

Press:

```text
Ctrl + Alt + Left
```

and control returns to primary.

## Acceptance Test 10

Press:

```text
Ctrl + Alt + Home
```

at any point during remote control and primary control returns.

## Acceptance Test 11

Disconnect secondary networking.

Primary must automatically regain control.

## Acceptance Test 12

No Ctrl/Shift/Alt/Windows keys remain stuck after disconnect.

## Acceptance Test 13

Remote audio stream disappears safely after disconnect without hanging playback.

---

# 59. Explicit MVP Non-Goals

Do not implement these before the core MVP is stable:

```text
Linux support
macOS support
GUI
System tray
Clipboard
File transfer
Automatic discovery
Edge switching
3+ computers
Microphone forwarding
Virtual audio driver
Internet relay
Cloud accounts
Mobile apps
Remote desktop/video streaming
```

---

# 60. Development Order

Use the following implementation order.

## Phase 1 — Foundation

1. Repository structure
2. Configuration
3. Logging
4. TCP framing
5. Authentication
6. Connection lifecycle
7. Heartbeat

## Phase 2 — Keyboard

1. Keyboard capture
2. Keyboard transport
3. Keyboard injection
4. Modifier handling
5. Pressed-key tracking
6. Cleanup

## Phase 3 — Mouse

1. Mouse buttons
2. Mouse wheel
3. Relative movement
4. Local suppression
5. Drag support

## Phase 4 — Switching

1. Active-machine state
2. Hotkeys
3. Emergency recovery
4. Automatic local fallback

## Phase 5 — Audio

1. Device listing
2. WASAPI loopback capture
3. Audio packet protocol
4. UDP sender
5. UDP receiver
6. Jitter buffer
7. Playback
8. Mixing
9. Volume/mute

## Phase 6 — Integration

1. Input and audio in same lifecycle
2. Shared node identity
3. Shared authentication
4. Shared connection status
5. Graceful shutdown
6. Reconnect behavior

## Phase 7 — QA

1. Tests
2. Windows hardware validation
3. README
4. Troubleshooting
5. Packaging preparation

---

# 61. Coding Standards

Code should be:

- Readable
- Modular
- Type-hinted where useful
- Testable
- Documented
- Defensive

Avoid:

- Giant single-file implementation
- Hard-coded IP addresses
- Hard-coded machine names
- Unsafe pickle transport
- Silent exception swallowing
- Busy waiting
- Unbounded queues
- Excessive global state
- Tight coupling between audio and input subsystems

---

# 62. README Requirements

README should include:

- What DeskMesh is
- Screenshot or architecture diagram later
- Current project status
- Supported operating systems
- Features
- Installation
- Virtual environment setup
- Requirements
- Primary setup
- Secondary setup
- Finding LAN IP
- Firewall instructions
- Hotkeys
- Audio setup
- Device selection
- Configuration
- Security warning
- Troubleshooting
- Known limitations
- Roadmap
- Contributing
- License

Suggested README heading:

```markdown
# DeskMesh

One keyboard. One mouse. One headset. All your computers.
```

Suggested intro:

```text
DeskMesh is an open-source software KVM and audio-sharing tool that lets you control multiple computers using one keyboard, mouse, and headset over your local network.
```

---

# 63. Installation Experience

Initial development installation:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Primary:

```powershell
.venv\Scripts\python.exe deskmesh.py primary
```

Secondary:

```powershell
.venv\Scripts\python.exe deskmesh.py secondary --connect 192.168.1.10
```

Future versions should provide:

- Standalone executable
- Installer
- System tray startup
- Auto-start option

These are not required for the first MVP.

---

# 64. Compatibility With Existing Audio Prototype

There is an existing experimental project for forwarding Windows system audio between computers.

Relevant concepts that may be reused:

- Python
- NumPy
- SoundCard/WASAPI
- UDP
- HMAC authentication
- Named senders
- Session IDs
- Sequence numbers
- Reorder buffer
- Stream volume
- Stream mute
- Clipping protection

Do not blindly copy the old architecture.

Refactor reusable concepts into DeskMesh modules that fit the unified node architecture.

---

# 65. Product Direction After MVP

Possible roadmap:

## v0.1

```text
2 PCs
Keyboard
Mouse
Hotkeys
Remote system audio
CLI
```

## v0.2

```text
Better reconnect
Audio modes
Device persistence
Improved latency
Packaging
```

## v0.3

```text
Edge switching
LAN discovery
3+ PCs
```

## v0.4

```text
System tray GUI
Visual PC layout
Configuration UI
```

## v0.5

```text
Clipboard sharing
File transfer
```

## v0.6+

```text
Microphone forwarding
Linux
macOS
Cross-platform protocol
```

---

# 66. Design Principle

DeskMesh should not become a remote-desktop application.

It is not trying to stream the remote screen.

Its purpose is to make multiple nearby computers feel like one physical workspace.

The core experience is:

```text
ONE KEYBOARD
ONE MOUSE
ONE HEADSET
MULTIPLE COMPUTERS
```

---

# 67. Final Goal

DeskMesh should eventually provide the experience:

```text
                DeskMesh

     One keyboard. One mouse. One headset.

                       │
                       ▼

              All your computers.
```

The user should be able to sit at one desk and interact naturally with several machines without repeatedly:

- Switching physical peripherals
- Moving USB receivers
- Changing headphones
- Reconnecting cables
- Reconfiguring audio devices

DeskMesh should prioritize:

```text
Reliability
Responsiveness
Safety
Simple setup
Clean architecture
Local-first operation
Open-source maintainability
```

over unnecessary feature count.

---

# 68. First Coding-Agent Deliverable

The first coding agent should produce a functional repository, not only scaffolding.

Expected output:

```text
deskmesh.py
deskmesh/
requirements.txt
README.md
REQUIREMENTS.md
config.example.json
.gitignore
tests/
```

The agent must provide:

- Functional TCP connection
- Authentication
- Heartbeat
- Keyboard forwarding
- Mouse forwarding
- Safe hotkey switching
- Emergency recovery
- Initial audio forwarding
- Clear setup instructions
- Tests for protocol/state logic
- Known limitations

The agent should make reasonable engineering decisions where this document leaves implementation details open, while preserving the product goals and safety requirements defined here.
