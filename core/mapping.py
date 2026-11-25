#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Dict, Optional

# Canais (lanes) do DTX (idênticos aos do mp4_to_dtx.py)
HHC, SD, BD, HT, LT, CY, FT, HHO, RD, LC = "11", "12", "13", "14", "15", "16", "17", "18", "19", "1A"
LANES = [HHC, SD, BD, HT, LT, CY, FT, HHO, RD, LC]

LANE_SAMPLE = {HHC: "hihat_close", SD: "snare", BD: "kick", HT: "high_tom", LT: "low_tom",
               CY: "crash", FT: "floor_tom", HHO: "hihat_open", RD: "ride", LC: "crash"}
LANE_PAN = {HHC: -40, HHO: -40, SD: 0, BD: 0, HT: -25, LT: 15, FT: 35, CY: 45, RD: 55, LC: -55}
LANE_NAME = {HHC: "HH fechado", SD: "Caixa", BD: "Bumbo", HT: "Tom agudo", LT: "Tom médio",
             CY: "Prato (crash)", FT: "Tom de chão", HHO: "HH aberto", RD: "Ride", LC: "Crash esq."}

# --------------------------------------------------------------------------- General MIDI drum map
# pitch MIDI -> lane DTX. Comentários com o nome GM oficial.
GM_DRUM_MAP: Dict[int, str] = {
    # Bumbo
    35: BD,   # Acoustic Bass Drum
    36: BD,   # Bass Drum 1
    # Caixa
    38: SD,   # Acoustic Snare
    40: SD,   # Electric Snare
    37: SD,   # Side Stick (rimshot) -> caixa
    # Hi-hat
    42: HHC,  # Closed Hi-Hat
    44: HHC,  # Pedal Hi-Hat
    22: HHC,  # Splash Hi-Hat (curto) -> HH fechado
    46: HHO,  # Open Hi-Hat
    26: HHO,  # Splash Hi-Hat (variante) -> HH aberto
    # Toms
    48: HT,   # Hi-Mid Tom
    50: HT,   # High Tom
    47: LT,   # Low-Mid Tom
    45: LT,   # Low Tom
    43: FT,   # High Floor Tom
    41: FT,   # Low Floor Tom
    # Pratos
    49: CY,   # Crash Cymbal 1
    57: CY,   # Crash Cymbal 2
    52: CY,   # Chinese Cymbal -> crash
    55: CY,   # Splash Cymbal -> crash
    51: RD,   # Ride Cymbal 1
    59: RD,   # Ride Cymbal 2
    53: RD,   # Ride Bell -> ride
}

# Nomes GM para mensagens de log
GM_NAMES: Dict[int, str] = {
    22: "Splash Hi-Hat", 26: "Splash Hi-Hat", 35: "Acoustic Bass Drum", 36: "Bass Drum 1",
    37: "Side Stick", 38: "Acoustic Snare", 40: "Electric Snare", 41: "Low Floor Tom",
    42: "Closed Hi-Hat", 43: "High Floor Tom", 44: "Pedal Hi-Hat", 45: "Low Tom",
    46: "Open Hi-Hat", 47: "Low-Mid Tom", 48: "Hi-Mid Tom", 49: "Crash Cymbal 1",
    50: "High Tom", 51: "Ride Cymbal 1", 52: "Chinese Cymbal", 53: "Ride Bell",
    55: "Splash Cymbal", 57: "Crash Cymbal 2", 59: "Ride Cymbal 2",
}


def map_pitch(pitch: int) -> Optional[str]:
    return GM_DRUM_MAP.get(int(pitch))


def unmapped_name(pitch: int) -> str:
    return GM_NAMES.get(int(pitch), f"pitch {int(pitch)}")
