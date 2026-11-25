﻿#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import math
import shutil
import sys
from fractions import Fraction
from pathlib import Path
from typing import Optional

import soundfile as sf

from core.dtxkit import (CmdError, TICKS_PER_BEAT, die, extract_audio, log, make_bgm,
                    make_chart_from_events, make_preview, make_preview_image,
                    prepare_samples, prepare_video, probe, safe_name,
                    separate_drums, video_info, write_dtx)
from core.difficulty import estimate_level, parse_level_arg
from core.mapping import LANES, LANE_NAME, unmapped_name
from core.sheet_process import ScoreError, load_score, quantize_events_to_grid, suggest_grid_div
from core.align import align_score


def main(argv: Optional[list] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="Gera um chart DTX a partir de uma partitura (MusicXML/MIDI/PDF) "
                    "sincronizada com um arquivo de mídia.")
    ap.add_argument("input", type=Path, help="arquivo de mídia (mp4, mkv, mp3, wav, ...)")
    ap.add_argument("--score", type=Path, required=True,
                    help="partitura com a trilha de bateria (.musicxml/.xml/.mxl/.mid/.midi/.pdf)")
    ap.add_argument("-o", "--out", type=Path, default=Path("dtx_output"),
                    help="pasta de saída (padrão: dtx_output)")
    ap.add_argument("--title", help="título (padrão: partitura, tag do arquivo ou nome)")
    ap.add_argument("--artist", help="artista (padrão: partitura ou tag do arquivo)")
    ap.add_argument("--level", default="auto",
                    help="#DLEVEL: 'auto' estima pela densidade/padrões da partitura "
                         "(padrão) ou um número 1-999 (500 = 5.00 no DTXManiaNX)")
    # seleção da parte/trilha
    ap.add_argument("--part", help="parte da partitura (nome ou nº 1-based; padrão: percussão)")
    ap.add_argument("--track", help="trilha do MIDI (nome ou nº 1-based; padrão: percussão)")
    # sincronização
    ap.add_argument("--bpm", type=float, help="força o BPM (padrão: o da partitura)")
    ap.add_argument("--audio-offset-ms", type=float, default=0.0,
                    help="desloca a BGM/vídeo em relação às notas (+ se as notas parecem atrasadas)")
    ap.add_argument("--auto-align", action="store_true",
                    help="estima BPM/offset casando os onsets do áudio com as notas da partitura")
    # áudio/BGM
    ap.add_argument("--bgm", choices=["full", "isolated"], default="full",
                    help="full = áudio original (padrão); isolated = demucs remove a bateria")
    ap.add_argument("--model", default="htdemucs", help="modelo do demucs (--bgm isolated)")
    ap.add_argument("--shifts", type=int, default=1, help="shifts do demucs")
    ap.add_argument("--device", default="auto", help="auto, cpu ou cuda")
    ap.add_argument("--force", action="store_true", help="refaz a separação mesmo com cache")
    ap.add_argument("--bgm-format", choices=["ogg", "wav"], default="ogg")
    ap.add_argument("--bgm-volume", type=int, default=100, help="volume da BGM, 1-100")
    ap.add_argument("--drum-volume", type=int, default=70, help="volume dos samples, 1-100")
    ap.add_argument("--samples", type=Path, help="pasta com samples próprios (kick/snare/...)")
    # grid/compasso
    ap.add_argument("--grid", type=int, default=0,
                    help="subdivisões por tempo: 0=auto (da partitura), 4, 6, 8, 12, 16, 24, 48")
    ap.add_argument("--beats-per-bar", type=int, default=0,
                    help="tempos por compasso (0=auto, o da partitura)")
    # vídeo/preview
    ap.add_argument("--no-video", action="store_true", help="não inclui o vídeo")
    ap.add_argument("--video-height", type=int, default=720, help="altura máxima do vídeo")
    # outras
    ap.add_argument("--work-dir", type=Path, help="pasta de trabalho/cache (padrão: <out>/.work/<título>)")
    ap.add_argument("--clean", action="store_true", help="apaga a pasta de trabalho no final")
    ap.add_argument("--dtx-encoding", default="utf-16", help="codificação do .dtx (padrão utf-16)")
    a = ap.parse_args(argv)

    if not a.input.exists():
        die(f"Arquivo não encontrado: {a.input}")
    if not a.score.exists():
        die(f"Partitura não encontrada: {a.score}")
    if a.grid and (TICKS_PER_BEAT % a.grid):
        die(f"--grid precisa dividir {TICKS_PER_BEAT} (ex.: 4, 6, 8, 12, 16, 24, 48)")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        die("ffmpeg/ffprobe não encontrados no PATH.")

    info = probe(a.input)
    tags = {k.lower(): v for k, v in info.get("format", {}).get("tags", {}).items()}
    audio_len = None

    work_title = a.title or tags.get("title") or a.input.stem
    song_dir = a.out / safe_name(work_title)
    work = a.work_dir or (a.out / ".work" / safe_name(work_title))
    work.mkdir(parents=True, exist_ok=True)
    song_dir.mkdir(parents=True, exist_ok=True)

    try:
        log(f"[1/5] Extraindo áudio de {a.input.name}")
        mix = work / "mix.wav"
        extract_audio(a.input, mix)
        audio_len = float(sf.info(str(mix)).duration)

        log(f"[2/5] Lendo a partitura {a.score.name}")
        try:
            sc = load_score(a.score, work=work, part_hint=a.part, track_hint=a.track)
        except ScoreError as e:
            die(str(e))
        for w in sc.warnings:
            log(f"      AVISO: {w}")
        if sc.unmapped:
            tot = sum(sc.unmapped.values())
            nomes = ", ".join(f"{unmapped_name(p)} ({n}x)" for p, n in sorted(sc.unmapped.items()))
            log(f"      AVISO: {tot} nota(s) sem mapeamento GM->DTX ignoradas: {nomes}")

        title = a.title or sc.title or tags.get("title") or a.input.stem
        artist = a.artist or sc.artist or tags.get("artist") or "-"
        song_dir = a.out / safe_name(title)
        song_dir.mkdir(parents=True, exist_ok=True)

        n_beats = a.beats_per_bar or sc.beats_per_bar or 4
        grid_div = a.grid or suggest_grid_div(sc)
        if grid_div > TICKS_PER_BEAT:
            die(f"A partitura exige grid {grid_div} > {TICKS_PER_BEAT}.")
        moved = quantize_events_to_grid(sc, grid_div)
        if moved:
            log(f"      AVISO: {moved} nota(s) arredondadas para a grade de {grid_div} posições/tempo.")
        log(f"      {sc.n_notes} notas | BPM {sc.bpm or '?'} | compasso {n_beats}/4"
            f" | grid {grid_div} posições/tempo{' (auto)' if not a.grid else ''}")

        log(f"[3/5] Sincronizando partitura x áudio ({'auto' if a.auto_align else 'manual'})")
        try:
            al = align_score(sc, mix, a.auto_align, a.bpm, a.audio_offset_ms)
        except ValueError as e:
            die(str(e))
        spq = 60.0 / al.bpm
        bar = n_beats * spq
        q0_audio = al.offset_s
        k = max(1, math.ceil(q0_audio / bar - 1e-9))
        pad = k * bar - q0_audio
        pad_quarters = Fraction(pad / spq).limit_denominator(100000)
        log(f"      BPM {al.bpm:.3f} | início da partitura no áudio: {q0_audio * 1000:.0f} ms"
            f" | notas começam no compasso {k} | BGM atrasada {pad * 1000:.0f} ms"
            + (f" | casamento {al.quality:.0%}" if a.auto_align else ""))
        if a.auto_align and al.quality < 0.5:
            log("      AVISO: poucos onsets do áudio casaram com a partitura. Confira --bpm/"
                "--audio-offset-ms ou tente no modo manual.")

        if str(a.level).lower() == "auto":
            det = estimate_level([(e.quarter, e.lane) for e in sc.events], al.bpm)
            level = det.level
            log(f"      Level estimado: {det.difficulty:.2f} ({level}) | "
                f"{det.nps_avg:.1f} notas/s (pico {det.nps_peak:.0f}/s) | "
                f"16ths {det.frac_16:.0%} tercinas {det.frac_trip:.0%} "
                f"acordes {det.frac_chord:.0%}")
        else:
            level = parse_level_arg(str(a.level))

        log(f"[4/5] Preparando BGM ({a.bgm}) e samples")
        if a.bgm == "isolated":
            device = a.device
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"
            log(f"      Separando bateria x resto com {a.model} ({device}) - pode levar minutos")
            _drums, rest = separate_drums(mix, work, a.model, device, a.shifts, a.force)
            bgm_src = rest
        else:
            bgm_src = mix
        ext = a.bgm_format
        bgm_name = f"bgm.{ext}"
        bgm_len = make_bgm(bgm_src, song_dir / bgm_name, pad, ext, work)

        samples = prepare_samples(song_dir / "drums", a.samples, work)

        log("[5/5] Escrevendo DTX, vídeo e preview")
        chart, bar_ticks = make_chart_from_events(sc.events, n_beats, grid_div, pad_quarters)
        end_measure = max(int(math.ceil(bgm_len / bar)),
                          max((m for (m, _c) in chart), default=0) + 1,
                          int(math.ceil((audio_len + pad) / bar)))

        video_name = prev_name = prev_img = None
        if not a.no_video and video_info(a.input):
            video_name = "video.mp4"
            prepare_video(a.input, song_dir / video_name, pad, a.video_height)
        elif not a.no_video:
            log("      (sem faixa de vídeo no arquivo; seguindo sem vídeo)")
        try:
            make_preview(mix, song_dir / "preview.ogg", audio_len)
            prev_name = "preview.ogg"
            if video_name:
                make_preview_image(a.input, song_dir / "preview.jpg", audio_len)
                prev_img = "preview.jpg"
        except CmdError as e:
            log(f"      (preview ignorado: {str(e).splitlines()[0]})")

        dtx_path = song_dir / f"{safe_name(title)}.dtx"
        write_dtx(dtx_path, title=title, artist=artist, bpm=al.bpm, level=level,
                  chart=chart, bar_ticks=bar_ticks, n_beats=n_beats, end_measure=end_measure,
                  bgm_file=bgm_name, sample_files=samples, drum_volume=a.drum_volume,
                  bgm_volume=a.bgm_volume, video_file=video_name, preview_file=prev_name,
                  preview_image=prev_img, encoding=a.dtx_encoding,
                  comment=f"; Auto-gerado por score_to_dtx.py a partir de {a.score.name}.")
    except CmdError as e:
        die(str(e))

    counts = {ch: sum(len(v) for (m, c), v in chart.items() if c == ch) for ch in LANES}
    log("\nPronto!")
    log(f"  Pasta : {song_dir.resolve()}")
    log(f"  Chart : {dtx_path.name}  ({sum(counts.values())} notas, {end_measure} compassos)")
    log("  Notas : " + ", ".join(f"{LANE_NAME[c]}={n}" for c, n in counts.items() if n))
    log("  Copie a pasta inteira para a pasta Songs do DTXMania.")
    log("  Se as notas parecerem atrasadas/adiantadas em relação à música, ajuste com --audio-offset-ms.")
    if a.clean:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
