# ATEM Mock

Virtual Blackmagic ATEM control-plane emulator.

The goal is to let software such as ATEM Software Control, Companion and other
ATEM clients behave as if a physical switcher were present on the network.

No video processing is performed. ATEM Mock emulates the network control plane
and switcher state.

## Current status

Working proof of concept:

- ATEM UDP/9910 handshake
- ATEM Software Control connection
- full reference initialization stream
- Program selection
- Preview selection
- CUT
- AUTO (currently completed instantly)
- state feedback to the ATEM client
- multiple simultaneous ATEM control clients
- shared virtual switcher state
- model profile registry

The currently validated reference profile is:

- ATEM Television Studio HD

## Quick start

```bash
python3 atem_mock.py
```

ATEM Software Control on the same machine can connect to:

```
127.0.0.1
```

From another machine, connect to the IP address of the computer running
ATEM Mock.

## Models

List known model profiles:

```bash
python3 atem_mock.py --list-models
```

Launch a specific implemented profile:

```bash
python3 atem_mock.py --model tvstudio-hd
```

The registry already reserves profiles for:

- ATEM Mini
- ATEM Mini Pro
- ATEM Mini Pro ISO
- ATEM Mini Extreme
- ATEM Mini Extreme ISO
- ATEM 1 M/E Constellation HD
- ATEM 2 M/E Constellation HD
- ATEM 4 M/E Constellation HD

Profiles marked `PLANNED` are intentionally not selectable yet. We will only
mark a model `READY` once its topology and initialization state are sufficiently
accurate for ATEM clients.

## Multi-client operation

ATEM Mock now treats controllers like multiple control surfaces connected to one
physical switcher.

For example:

```
ATEM Software Control ─┐
Companion ─────────────┼── UDP/9910 ── ATEM Mock
Custom ATEM client ────┘
```

A Program/Preview change made by one client is broadcast to the other connected
clients.

This is the foundation required for configuring Companion against a virtual
ATEM without physical hardware.

## Architecture direction

```
ATEM Mock
├── protocol engine
│   ├── UDP sessions
│   ├── handshake / ACK
│   ├── command parsing
│   └── state broadcast
├── switcher state
│   ├── Program / Preview
│   ├── transitions
│   ├── keyers
│   ├── Aux
│   ├── SuperSource
│   └── audio / Fairlight
└── model profiles
    ├── Mini family
    ├── Mini Extreme family
    └── Constellation family
```

## Roadmap

Next milestones:

1. validate simultaneous ATEM Software Control + Companion
2. proper reliable packet queues / retransmission handling
3. real AUTO transition progression
4. DSK / USK / Aux control
5. first accurate ATEM Mini Extreme profile
6. Constellation profiles
7. save/load virtual switcher configuration and state
8. desktop launcher with model selector

## Reference

The initial working bootstrap stream is based on the public pyAtemSim work and
a captured ATEM Television Studio HD initialization sequence.

The original single-client working POC is preserved in:

```
reference/atem_mock_single_client.py
```
