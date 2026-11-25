#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, List, Sequence, Tuple

NPS_ANCHORS: List[Tuple[float, float]] = [
    (1.0, 1.0),
    (2.0, 2.5),
    (3.0, 3.8),
    (4.0, 4.8),
    (5.0, 5.8),
    (6.5, 7.0),
    (8.0, 8.0),
    (10.0, 9.0),
]

# Peso das rajadas: burst de 1s muito acima da mÃ©dia pesa atÃ© PEAK_WEIGHT nÃ­veis.
PEAK_WEIGHT = 0.35
PEAK_RATIO_LO = 1.6     # razÃ£o pico/mÃ©dia a partir da qual comeÃ§a a pesar
PEAK_RATIO_SPAN = 1.4   # largura da rampa (em razÃ£o pico/mÃ©dia)

# Peso da complexidade rÃ­tmica (subdivisÃµes e acordes simultÃ¢neos).
CX_WEIGHT = 0.45
TRIP_WEIGHT = 0.7       # tercinas contam menos que 16ths puros
CHORD_WEIGHT = 0.3      # flams/acordes contam menos ainda

MIN_DIFF, MAX_DIFF = 1.0, 9.99
MIN_LEVEL, MAX_LEVEL = 100, 999


@dataclass
class LevelDetail:
    nps_avg: float
    nps_peak: float
    frac_16: float
    frac_trip: float
    frac_chord: float
    base: float
    peak_adj: float
    cx_adj: float
    difficulty: float
    level: int

    def as_dict(self) -> Dict[str, float]:
        return {
            "nps_avg": self.nps_avg, "nps_peak": self.nps_peak,
            "frac_16": self.frac_16, "frac_trip": self.frac_trip,
            "frac_chord": self.frac_chord,
            "base": self.base, "peak_adj": self.peak_adj, "cx_adj": self.cx_adj,
            "difficulty": self.difficulty, "level": self.level,
        }


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _interp(x: float) -> float:
    a = NPS_ANCHORS
    if x <= a[0][0]:
        return a[0][1]
    if x >= a[-1][0]:
        return a[-1][1]
    for (x0, y0), (x1, y1) in zip(a, a[1:]):
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return a[-1][1]


def _metrics(events: Sequence[Tuple[Fraction, str]], spq: float) -> Dict[str, float]:
    quarters_f = [Fraction(q) for q, _ in events]
    lanes = [lane for _, lane in events]
    if not quarters_f:
        return {"nps_avg": 0.0, "nps_peak": 0.0, "frac_16": 0.0,
                "frac_trip": 0.0, "frac_chord": 0.0}
    quarters = [float(q) for q in quarters_f]

    dur_s = max(quarters) * spq
    n = len(quarters)
    nps_avg = n / dur_s if dur_s > 0 else 0.0

    sorted_q = sorted(quarters)
    nps_peak = 0.0
    if spq > 0:
        win = 1.0 / spq
        j = 0
        for i in range(len(sorted_q)):
            while sorted_q[i] - sorted_q[j] > win:
                j += 1
            nps_peak = max(nps_peak, (i - j + 1) / 1.0)  # 1 s

    uniq_q = sorted(set(quarters_f))
    n16 = ntrip = 0
    for q in uniq_q:
        frac = q - int(q)
        d = frac.denominator
        if d in (1, 2):
            continue
        e = d
        while e % 2 == 0:
            e //= 2
        if e % 3 == 0:
            ntrip += 1
        else:
            n16 += 1
    nu = len(uniq_q)
    frac_16 = n16 / nu if nu else 0.0
    frac_trip = ntrip / nu if nu else 0.0

    onset_counts = Counter(quarters)
    nchord = sum(1 for c in onset_counts.values() if c >= 2)
    frac_chord = nchord / len(onset_counts) if onset_counts else 0.0

    return {"nps_avg": nps_avg, "nps_peak": nps_peak, "frac_16": frac_16,
            "frac_trip": frac_trip, "frac_chord": frac_chord}


def estimate_level(events: Sequence[Tuple[Fraction, str]], bpm: float) -> LevelDetail:
    if not events:
        return LevelDetail(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, MIN_DIFF, MIN_LEVEL)
    if bpm <= 0:
        raise ValueError("bpm deve ser > 0 para estimar o level.")
    spq = 60.0 / bpm
    m = _metrics(events, spq)

    base = _interp(m["nps_avg"])
    ratio = (m["nps_peak"] / m["nps_avg"]) if m["nps_avg"] > 0 else 0.0
    peak_adj = PEAK_WEIGHT * _clamp((ratio - PEAK_RATIO_LO) / PEAK_RATIO_SPAN, 0.0, 1.0)
    cx = _clamp(m["frac_16"] + TRIP_WEIGHT * m["frac_trip"] + CHORD_WEIGHT * m["frac_chord"],
                0.0, 1.0)
    cx_adj = CX_WEIGHT * cx
    difficulty = _clamp(base + peak_adj + cx_adj, MIN_DIFF, MAX_DIFF)
    level = int(_clamp(round(difficulty * 100), MIN_LEVEL, MAX_LEVEL))

    return LevelDetail(m["nps_avg"], m["nps_peak"], m["frac_16"], m["frac_trip"],
                       m["frac_chord"], base, peak_adj, cx_adj, difficulty, level)


def parse_level_arg(value: str) -> int:
    try:
        lv = int(value)
    except ValueError:
        raise SystemExit(f"--level precisa ser 'auto' ou um nÃºmero 1-999; recebi '{value}'.")
    if not (1 <= lv <= 999):
        raise SystemExit(f"--level precisa estar entre 1 e 999 (DTXCreator limita a 999); recebi {lv}.")
    return lv
