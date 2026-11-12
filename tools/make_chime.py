#!/usr/bin/env python3
"""Makes ui/sounds/chime.wav: the city alert sound, three fast rising "whoops" like a red alert, about 1.6 seconds,
made for this project."""

import math
import struct
import sys
import wave
from pathlib import Path

RATE = 44100
WHOOP, GAP, COUNT = 0.42, 0.12, 3  # seconds of one whoop, seconds of quiet after it, how many
LOW, RISE = 320.0, 780.0  # a whoop rises from LOW to LOW + RISE hertz
LENGTH = COUNT * (WHOOP + GAP)


def samples():
    phase = 0.0
    for i in range(int(RATE * LENGTH)):
        local = (i / RATE) % (WHOOP + GAP)
        if local >= WHOOP:
            yield 0.0
            continue
        phase += 2 * math.pi * (LOW + RISE * (local / WHOOP) ** 0.8) / RATE
        envelope = min(local / 0.015, 1.0) * min((WHOOP - local) / 0.04, 1.0)
        yield envelope * (math.sin(phase) + 0.45 * math.sin(3 * phase) + 0.2 * math.sin(5 * phase)) * 0.3


def main(target):
    frames = b"".join(struct.pack("<h", int(max(-1, min(1, value)) * 32767)) for value in samples())
    with wave.open(str(target), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(frames)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "ui/sounds/chime.wav")
