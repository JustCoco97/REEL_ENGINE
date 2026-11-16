"""Frame-grid timing helpers shared by validation and rendering."""
from decimal import Decimal, ROUND_HALF_UP


def seconds_to_frames(seconds, fps):
    """Round a nonnegative time to the nearest output frame (ties round up)."""
    value = Decimal(str(seconds)) * Decimal(str(fps))
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def frames_to_seconds(frames, fps):
    """Return the canonical seconds value represented by an integer frame count."""
    return float(Decimal(int(frames)) / Decimal(str(fps)))


def align_seconds(seconds, fps, *, minimum_frames=0):
    frames = max(minimum_frames, seconds_to_frames(seconds, fps))
    return frames_to_seconds(frames, fps)
