#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import urllib.request
import zipfile
from fractions import Fraction
from functools import reduce
from math import gcd
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf

from mapping import (LANES, LANE_NAME, LANE_PAN, LANE_SAMPLE,
                     HHC, SD, BD, HT, LT, CY, FT, HHO, RD, LC)

SR = 44100
TICKS_PER_BEAT = 48  # 48 ticks por tempo -> 192 por compasso 4/4 (resoluÃ§Ã£o do DTXCreator)


class CmdError(RuntimeError):
    pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


def die(msg: str) -> None:
    raise SystemExit(f"\nERRO: {msg}")


def run(cmd: List, capture: bool = False) -> str:
    cmd = [str(c) for c in cmd]
    try:
        r = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise CmdError(f"NÃ£o encontrei '{cmd[0]}'. Instale o ffmpeg e deixe-o no PATH.")
    except subprocess.CalledProcessError as e:
        raise CmdError(f"Falha ao executar: {' '.join(cmd[:6])} ...\n{(e.stderr or '')[-1500:]}")
    return r.stdout


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path])
    return json.loads(out)


def safe_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]+', "_", s).strip(" .")
    return s or "musica"


def b36(n: int, width: int = 2) -> str:
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out.rjust(width, "0")


def measure_id(m: int) -> str:
    return (b36(m // 100, 1) + f"{m % 100:02d}") if m >= 1000 else f"{m:03d}"


def extract_audio(src: Path, dst: Path) -> None:
    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn", "-ac", "2", "-ar", SR,
         "-c:a", "pcm_f32le", dst])


def separate_drums(wav_path: Path, work: Path, model_name: str, device: str, shifts: int,
                   force: bool = False) -> Tuple[Path, Path]:
    drums_p, rest_p = work / "drums.wav", work / "no_drums.wav"
    if drums_p.exists() and rest_p.exists() and not force:
        log("  (reaproveitando stems jÃ¡ separados; use --force para refazer)")
        return drums_p, rest_p
    try:
        import torch
        from demucs.pretrained import get_model
        from demucs.apply import apply_model
    except ImportError as e:
        die(f"torch/demucs nÃ£o encontrados ({e}). Instale com: pip install demucs")

    audio, sr = sf.read(str(wav_path), dtype="float32", always_2d=True)
    wav = torch.from_numpy(np.ascontiguousarray(audio.T))
    model = get_model(model_name)
    model.eval()
    if sr != model.samplerate:
        die(f"O modelo {model_name} espera {model.samplerate} Hz, mas o Ã¡udio estÃ¡ em {sr} Hz.")
    if "drums" not in model.sources:
        die(f"O modelo {model_name} nÃ£o tem o stem 'drums' (stems: {model.sources}).")

    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    with torch.no_grad():
        out = apply_model(model, ((wav - mean) / std)[None], shifts=shifts, split=True,
                          overlap=0.25, progress=True, device=device)[0]
    out = out * std + mean

    names = list(model.sources)
    i_d = names.index("drums")
    drums = out[i_d]
    rest = torch.stack([out[i] for i in range(len(names)) if i != i_d]).sum(0)
    sf.write(str(drums_p), drums.cpu().numpy().T, sr, subtype="FLOAT")
    sf.write(str(rest_p), rest.cpu().numpy().T, sr, subtype="FLOAT")
    return drums_p, rest_p


def make_chart_from_events(events, beats_per_bar: int, grid_div: int, pad_quarters: Fraction):
    bar_ticks = TICKS_PER_BEAT * beats_per_bar
    step = TICKS_PER_BEAT // grid_div
    chart: Dict[Tuple[int, str], List[int]] = {}
    for e in events:
        ticks = int(round(float(e.quarter + pad_quarters) * grid_div)) * step
        if ticks < 0:
            continue
        m, pos = divmod(ticks, bar_ticks)
        chart.setdefault((m, e.lane), []).append(pos)
    # remove duplicatas por (compasso, canal)
    for key in chart:
        chart[key] = sorted(set(chart[key]))
    return chart, bar_ticks


def pack_line(positions: List[int], bar_ticks: int, wav_id: str) -> str:
    g = reduce(gcd, positions, bar_ticks)
    res = bar_ticks // g
    slots = ["00"] * res
    for p in positions:
        slots[p // g] = wav_id
    return "".join(slots)


def write_dtx(path: Path, *, title: str, artist: str, bpm: float, level: int, chart, bar_ticks: int,
              n_beats: int, end_measure: int, bgm_file: str, sample_files: Dict[str, str],
              drum_volume: int, bgm_volume: int, video_file: Optional[str], preview_file: Optional[str],
              preview_image: Optional[str], encoding: str, comment: Optional[str] = None) -> None:
    L: List[str] = [
        comment or "; Auto-gerado por score_to_dtx.py (notas vindas da partitura).",
        f"#TITLE: {title}",
        f"#ARTIST: {artist}",
        "#COMMENT: Chart gerado a partir de partitura",
        f"#BPM: {bpm:.3f}",
        f"#DLEVEL: {level}",
    ]
    if preview_file:
        L.append(f"#PREVIEW: {preview_file}")
    if preview_image:
        L.append(f"#PREIMAGE: {preview_image}")
    L += ["", f"#WAV01: {bgm_file}", f"#VOLUME01: {bgm_volume}"]
    for ch in LANES:
        L += [f"#WAV{ch}: {sample_files[LANE_SAMPLE[ch]]}", f"#VOLUME{ch}: {drum_volume}",
              f"#PAN{ch}: {LANE_PAN[ch]}"]
    if video_file:
        L.append(f"#AVI01: {video_file}")
    L.append("")
    if n_beats != 4:
        L.append(f"#00002: {n_beats / 4:g}")
    L.append("#00001: 01")
    if video_file:
        L.append(f"#0005A: 01")
    for (m, ch) in sorted(chart):
        L.append(f"#{measure_id(m)}{ch}: {pack_line(sorted(chart[(m, ch)]), bar_ticks, ch)}")
    # chip invisÃ­vel no fim: garante que o chart sÃ³ termine depois do fim do Ã¡udio
    L.append(f"#{measure_id(end_measure)}33: {BD}")
    with open(path, "w", encoding=encoding, newline="\r\n") as fh:
        fh.write("\n".join(L) + "\n")


def encode_audio(src_wav: Path, dst: Path, fmt: str) -> None:
    if fmt == "wav":
        run(["ffmpeg", "-y", "-v", "error", "-i", src_wav, "-c:a", "pcm_s16le", dst])
        return
    try:
        run(["ffmpeg", "-y", "-v", "error", "-i", src_wav, "-c:a", "libvorbis", "-q:a", "6", dst])
    except CmdError:
        data, sr = sf.read(str(src_wav), dtype="float32", always_2d=True)
        sf.write(str(dst), data, sr, format="OGG", subtype="VORBIS")


def make_bgm(src_wav: Path, dst: Path, pad_s: float, fmt: str, work: Path) -> float:
    data, sr = sf.read(str(src_wav), dtype="float32", always_2d=True)
    pad_n = int(round(pad_s * sr))
    out = np.concatenate([np.zeros((pad_n, data.shape[1]), dtype=np.float32), data])
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 0.99:
        out = out * (0.99 / peak)
    tmp = work / "bgm_full.wav"
    sf.write(str(tmp), out, sr, subtype="FLOAT")
    encode_audio(tmp, dst, fmt)
    return len(out) / sr


def video_info(path: Path) -> Optional[Tuple[int, int]]:
    for s in probe(path).get("streams", []):
        if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic"):
            return int(s["width"]), int(s["height"])
    return None


def prepare_video(src: Path, dst: Path, pad_s: float, max_h: int) -> bool:
    info = video_info(src)
    if not info:
        return False
    w, h = info
    s = min(1.0, max_h / h)
    W = max(16, int(round(w * s / 16)) * 16)
    H = max(16, int(round(h * s / 16)) * 16)
    vf = f"tpad=start_duration={pad_s:.6f}:color=black,scale={W}:{H},format=yuv420p"
    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-an", "-vf", vf, "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "23", "-movflags", "+faststart", dst])
    return True


def make_preview(mix_wav: Path, dst: Path, duration: float) -> None:
    start = max(0.0, min(duration * 0.3, duration - 31.0))
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", "30", "-i", mix_wav,
         "-af", "afade=t=in:d=1,afade=t=out:st=29:d=1", "-c:a", "libvorbis", "-q:a", "4", dst])


def make_preview_image(video: Path, dst: Path, duration: float) -> None:
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{duration * 0.3:.2f}", "-i", video, "-frames:v", "1",
         "-vf", "scale=204:269:force_original_aspect_ratio=increase,crop=204:269", dst])


TEMPLATE_URL = "https://github.com/Thanyanit-J/AutoChartDTX/raw/main/SimfilesTemplate.zip"
SAMPLE_NAMES = ["kick", "snare", "hihat_close", "hihat_open", "high_tom", "low_tom",
                "floor_tom", "crash", "ride"]


def synth_sample(name: str) -> np.ndarray:
    rng = np.random.default_rng(7)
    tm = lambda d: np.arange(int(d * SR)) / SR

    def hp_noise(n, order=1):
        x = rng.standard_normal(n)
        for _ in range(order):
            x = np.diff(x, prepend=0.0)
        return x

    def sweep(f0, f1, tau, d):
        t = tm(d)
        f = f1 + (f0 - f1) * np.exp(-t / tau)
        return np.sin(2 * np.pi * np.cumsum(f) / SR)

    if name == "kick":
        t = tm(0.45); x = sweep(140, 45, 0.03, 0.45) * np.exp(-t / 0.12)
    elif name == "snare":
        t = tm(0.35); x = 0.6 * hp_noise(len(t)) * np.exp(-t / 0.07) + 0.7 * np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.05)
    elif name == "hihat_close":
        t = tm(0.12); x = hp_noise(len(t), 2) * np.exp(-t / 0.02)
    elif name == "hihat_open":
        t = tm(0.7); x = hp_noise(len(t), 2) * np.exp(-t / 0.18)
    elif name in ("high_tom", "low_tom", "floor_tom"):
        f = {"high_tom": 190, "low_tom": 140, "floor_tom": 100}[name]
        t = tm(0.6); x = sweep(f * 1.3, f, 0.04, 0.6) * np.exp(-t / 0.2)
    elif name == "crash":
        t = tm(1.8); x = hp_noise(len(t), 2) * np.exp(-t / 0.55)
    else:  # ride
        t = tm(1.3); x = hp_noise(len(t), 2) * np.exp(-t / 0.3) * 0.7 + 0.3 * np.sin(2 * np.pi * 820 * t) * np.exp(-t / 0.4)
    return (0.8 * x / np.max(np.abs(x))).astype(np.float32)


def prepare_samples(dest: Path, user_dir: Optional[Path], work: Path) -> Dict[str, str]:
    dest.mkdir(parents=True, exist_ok=True)
    found: Dict[str, str] = {}
    if user_dir:
        for p in sorted(Path(user_dir).iterdir()):
            if p.suffix.lower() in (".ogg", ".wav", ".mp3") and p.stem.lower() in SAMPLE_NAMES \
                    and p.stem.lower() not in found:
                shutil.copy(p, dest / p.name.lower())
                found[p.stem.lower()] = f"drums/{p.name.lower()}"
    missing = [n for n in SAMPLE_NAMES if n not in found]
    if missing:
        zf = None
        try:
            cache = work / "SimfilesTemplate.zip"
            if not cache.exists():
                with urllib.request.urlopen(TEMPLATE_URL, timeout=30) as r:
                    cache.write_bytes(r.read())
            zf = zipfile.ZipFile(io.BytesIO(cache.read_bytes()))
        except Exception as e:
            log(f"  (nÃ£o consegui baixar o template de samples: {e}; usando samples sintÃ©ticos)")
        for n in list(missing):
            if zf is not None:
                member = next((m for m in zf.namelist() if m.endswith(f"/drums/{n}.ogg")), None)
                if member:
                    (dest / f"{n}.ogg").write_bytes(zf.read(member))
                    found[n] = f"drums/{n}.ogg"
                    missing.remove(n)
        for n in missing:
            sf.write(str(dest / f"{n}.wav"), synth_sample(n), SR, subtype="PCM_16")
            found[n] = f"drums/{n}.wav"
    return found
