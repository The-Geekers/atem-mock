# ATEM Mock

Experimental ATEM switcher emulator for testing Blackmagic ATEM Software Control without physical hardware.

## Initial target

Phase 0 focuses on proving that current ATEM Software Control can establish a network session with a software-emulated ATEM device.

Target test environment:

- macOS Apple Silicon
- ATEM Software Control 10.4.1
- UDP control protocol on port 9910

## Scope

Planned device families:

- ATEM Mini
- ATEM Mini Pro / ISO
- ATEM Mini Extreme / Extreme ISO
- ATEM Constellation family

The project is designed around a common protocol engine with model-specific capability profiles.

## Milestone 0

1. Listen on UDP/9910.
2. Parse the ATEM connection hello.
3. Return a valid session handshake.
4. Log all packets exchanged with ATEM Software Control.
5. Determine the minimum initialization state required for Software Control to remain connected.

No video processing is planned at this stage.

## Status

Early protocol research / proof of concept.
