"""Interval arithmetic used to avoid double-counting time.

Every function works on lists of (start, end) tuples of pandas Timestamps.
Intervals are half-open [start, end); touching intervals are merged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import pandas as pd

Interval = tuple[pd.Timestamp, pd.Timestamp]


@dataclass(frozen=True)
class Period:
    """Reporting period [start, end). Both bounds are naive timestamps."""

    start: pd.Timestamp
    end: pd.Timestamp

    def __post_init__(self):
        if self.end <= self.start:
            raise ValueError("Period end must be after start")

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0

    @property
    def label(self) -> str:
        last = (self.end - pd.Timedelta(seconds=1)).date()
        return f"{self.start.date()} to {last} ({self.hours / 24:.0f} days)"


def make_period(start_date, end_date) -> Period:
    """Period covering whole days: start_date 00:00 up to the end of end_date (inclusive)."""
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize() + pd.Timedelta(days=1)
    return Period(start, end)


def clip_interval(start, end, p_start, p_end) -> Optional[Interval]:
    s, e = max(start, p_start), min(end, p_end)
    return (s, e) if e > s else None


def merge_intervals(intervals: Iterable[Interval]) -> list[Interval]:
    """Union of intervals: overlapping or touching ones become a single interval."""
    items = sorted((s, e) for s, e in intervals if e > s)
    merged: list[Interval] = []
    for s, e in items:
        if merged and s <= merged[-1][1]:
            if e > merged[-1][1]:
                merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


def clip_list(intervals: Iterable[Interval], p_start, p_end) -> list[Interval]:
    out = []
    for s, e in intervals:
        c = clip_interval(s, e, p_start, p_end)
        if c:
            out.append(c)
    return out


def subtract_intervals(base: list[Interval], cut: list[Interval]) -> list[Interval]:
    """base minus cut. Both arguments must already be merged (sorted, disjoint)."""
    result: list[Interval] = []
    for b_start, b_end in base:
        cursor = b_start
        for c_start, c_end in cut:
            if c_end <= cursor:
                continue
            if c_start >= b_end:
                break
            if c_start > cursor:
                result.append((cursor, c_start))
            cursor = max(cursor, c_end)
            if cursor >= b_end:
                break
        if cursor < b_end:
            result.append((cursor, b_end))
    return result


def total_seconds(intervals: Iterable[Interval]) -> float:
    return float(sum((e - s).total_seconds() for s, e in intervals))


def total_hours(intervals: Iterable[Interval]) -> float:
    return total_seconds(intervals) / 3600.0
