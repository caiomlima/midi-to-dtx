#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import List, Optional, Tuple

import numpy as np

from sheet_process import Score


def log(msg: str = "") -> None:
    print(msg, flush=True)


@dataclass
class Alignment:
    bpm: float
    offset_s: float      # instante (s) no Ã¡udio correspondente ao quarter 0 da partitura
    quality: float       # fraÃ§Ã£o de notas da partitura com onset casado (0..1)
    method: str          # "manual" ou "auto"


def _onsets(y: np.ndarray, sr: int) -> np.ndarray:
    import librosa
    o = librosa.onset.onset_detect(y=y, sr=sr, units="time", backtrack=False,
                                   delta=0.07, wait=1, pre_avg=5, post_avg=5)
    return np.asarray(o, dtype=float)


def _density_per_sec(sc: Score, bpm: float) -> float:
    q = float(sc.quarter_end())
    if q <= 0:
        return 0.0
    dur = q * 60.0 / bpm
    return sc.n_notes / dur if dur > 0 else 0.0


def estimate_bpm(y: np.ndarray, sr: int, sc: Score, bpm_hint: Optional[float]) -> float:
    if bpm_hint:
        return float(bpm_hint)
    if sc.bpm:
        return float(sc.bpm)
    import librosa
    r = librosa.beat.beat_track(y=y, sr=sr, units="time")
    est = float(np.atleast_1d(r[0] if isinstance(r, tuple) else r)[0])
    if not sc.events:
        return est
    onset_rate = len(_onsets(y, sr)) / (len(y) / sr)
    best, best_err = est, None
    for cand in (est / 2, est, est * 2):
        if not (20 <= cand <= 350):
            continue
        err = abs(_density_per_sec(sc, cand) - onset_rate) / max(onset_rate, 1e-6)
        if best_err is None or err < best_err:
            best, best_err = cand, err
    return float(best)


def _match_quality(onsets: np.ndarray, note_times: np.ndarray, tol: float) -> float:
    if len(note_times) == 0 or len(onsets) == 0:
        return 0.0
    idx = np.searchsorted(onsets, note_times)
    lo = np.clip(idx - 1, 0, len(onsets) - 1)
    hi = np.clip(idx, 0, len(onsets) - 1)
    d = np.minimum(np.abs(onsets[lo] - note_times), np.abs(onsets[hi] - note_times))
    return float(np.mean(d <= tol))


def find_offset(onsets: np.ndarray, sc: Score, bpm: float, tol: float = 0.04,
                window: float = 10.0, step: float = 0.01) -> Tuple[float, float]:
    if len(onsets) == 0 or not sc.events:
        return 0.0, 0.0
    spq = 60.0 / bpm  # segundos por quarter
    note_q = np.array([float(e.quarter) for e in sc.events])
    # amostra sÃ³ um subconjunto espaÃ§ado para velocidade (mantÃ©m a forma da densidade)
    if len(note_q) > 3000:
        note_q = np.sort(np.random.default_rng(0).choice(note_q, 3000, replace=False))
    note_q = np.unique(note_q)

    best_off, best_q = 0.0, -1.0
    offs = np.arange(-window, window + step / 2, step)
    # avaliaÃ§Ã£o em duas passadas: gross (step*4) e fina em volta do melhor
    for cur_step, cur_offs in ((step * 4, offs), (step, None)):
        if cur_offs is None:
            cur_offs = np.arange(best_off - cur_step * 4, best_off + cur_step * 4 + 1e-9, cur_step)
        for off in cur_offs:
            q = _match_quality(onsets, note_q * spq + off, tol)
            if q > best_q:
                best_q, best_off = q, float(off)
    return best_off, best_q


def align_score(sc: Score, mix_wav_path, auto: bool, bpm_override: Optional[float],
                offset_ms: float) -> Alignment:
    import soundfile as sf

    bpm = float(bpm_override or sc.bpm or 0.0)
    if bpm <= 0 and not auto:
        raise ValueError("A partitura nÃ£o informa BPM e nenhum --bpm foi dado. "
                         "Passe --bpm ou use --auto-align.")

    if not auto:
        return Alignment(bpm=bpm, offset_s=offset_ms / 1000.0, quality=float("nan"),
                         method="manual")

    y, sr = sf.read(str(mix_wav_path), dtype="float32", always_2d=True)
    y = y.mean(axis=1)
    onsets = _onsets(y, sr)

    if bpm > 0:
        results = {}
        for cand in (bpm / 2, bpm, bpm * 2):
            if 20 <= cand <= 350:
                off, q = find_offset(onsets, sc, cand)
                results[round(cand, 3)] = (cand, off, q)
        base = results.get(round(bpm, 3)) or (bpm, 0.0, 0.0)
        cand, off, q = max(results.values(), key=lambda r: r[2])
        if q - base[2] > 0.15:
            log(f"      AVISO: o BPM {bpm:g} da partitura casou melhor com o Ã¡udio como {cand:g} "
                "(metade/dobro). Use --bpm para fixar outro valor.")
        else:
            cand, off, q = base
        bpm = cand
    else:
        bpm = estimate_bpm(y, sr, sc, None)
        off, q = find_offset(onsets, sc, bpm)
    # soma o offset manual informado (ajuste fino do usuÃ¡rio)
    off += offset_ms / 1000.0
    return Alignment(bpm=bpm, offset_s=off, quality=q, method="auto")
