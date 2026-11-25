#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import xml.etree.ElementTree as ET

from core.mapping import map_pitch, unmapped_name

Q_RES = 192

DRUM_PART_HINTS = re.compile(r"drum|perc|bater|schlagzeug|batterie|tambor", re.I)

STEP_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def log(msg: str = "") -> None:
    print(msg, flush=True)


class ScoreError(RuntimeError):
    pass


@dataclass
class NoteEvent:
    quarter: Fraction
    lane: str


@dataclass
class Score:
    events: List[NoteEvent] = field(default_factory=list)
    bpm: Optional[float] = None
    beats_per_bar: int = 4
    title: Optional[str] = None
    artist: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    unmapped: Dict[int, int] = field(default_factory=dict)

    @property
    def n_notes(self) -> int:
        return len(self.events)

    def lane_counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for e in self.events:
            out[e.lane] = out.get(e.lane, 0) + 1
        return out

    def quarter_end(self) -> Fraction:
        return max((e.quarter for e in self.events), default=Fraction(0))


def _quantize(q: float) -> Fraction:
    return Fraction(int(round(float(q) * Q_RES)), Q_RES)


def _dedup_sort(events: List[NoteEvent]) -> List[NoteEvent]:
    events.sort(key=lambda e: (e.quarter, e.lane))
    out: List[NoteEvent] = []
    for e in events:
        if out and out[-1].quarter == e.quarter and out[-1].lane == e.lane:
            continue
        out.append(e)
    return out


def _add_unmapped(sc: Score, pitch: int) -> None:
    sc.unmapped[int(pitch)] = sc.unmapped.get(int(pitch), 0) + 1


def _musicxml_path(path: Path, work: Optional[Path]) -> Path:
    if path.suffix.lower() != ".mxl":
        return path
    if work is None:
        raise ScoreError(".mxl precisa de --work-dir definido.")
    work.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        root = None
        try:
            mf = zf.read("META-INF/container.xml").decode("utf-8", "replace")
            m = re.search(r'full-path="([^"]+)"', mf)
            if m:
                root = m.group(1)
        except KeyError:
            pass
        if root is None or root not in names:
            cands = [n for n in names if n.lower().endswith((".xml",)) and "container" not in n.lower()]
            if not cands:
                raise ScoreError(f"Não encontrei o MusicXML dentro de {path.name}.")
            root = cands[0]
        out = work / (Path(root).stem + ".musicxml")
        out.write_bytes(zf.read(root))
        return out


def _pitch_to_midi(step: str, alter: float, octave: int) -> int:
    return (octave + 1) * 12 + STEP_SEMITONE[step.upper()] + int(round(alter))


def _pick_xml_drum_part(root: ET.Element, part_hint: Optional[str]) -> Tuple[ET.Element, Dict[str, int], List[str]]:
    warnings: List[str] = []
    partlist = root.find("part-list")
    score_parts = list(partlist.findall("score-part")) if partlist is not None else []
    if not score_parts:
        raise ScoreError("O MusicXML não tem nenhuma parte (<score-part>).")

    infos = []
    for sp in score_parts:
        pid = sp.get("id")
        name = (sp.findtext("part-name") or "") + " " + (sp.findtext("part-abbreviation") or "")
        inst_names = [t.text or "" for t in sp.findall("score-instrument/instrument-name")]
        inst_pitch: Dict[str, int] = {}
        drum_channel = False
        for mi in sp.findall("midi-instrument"):
            iid = mi.get("id")
            unp = mi.findtext("midi-unpitched")
            chan = mi.findtext("midi-channel")
            if chan and chan.strip() == "10":
                drum_channel = True
            if iid and unp:
                try:
                    inst_pitch[iid] = int(unp)
                except ValueError:
                    pass
        is_drum = bool(inst_pitch) or drum_channel or DRUM_PART_HINTS.search(name) \
            or any(DRUM_PART_HINTS.search(n) for n in inst_names)
        infos.append({"id": pid, "name": name.strip(), "inst_pitch": inst_pitch,
                      "is_drum": is_drum, "inst_names": inst_names})

    chosen = None
    if part_hint:
        if part_hint.isdigit():
            idx = int(part_hint) - 1
            if 0 <= idx < len(infos):
                chosen = infos[idx]
            else:
                raise ScoreError(f"--part {part_hint} fora do alcance (a partitura tem {len(infos)} partes).")
        else:
            for inf in infos:
                if part_hint.lower() in inf["name"].lower() or any(part_hint.lower() in n.lower() for n in inf["inst_names"]):
                    chosen = inf
                    break
            if chosen is None:
                raise ScoreError(f"--part '{part_hint}' não encontrado. Partes disponíveis: "
                                 + ", ".join(i["name"] or i["id"] for i in infos))
    else:
        drums = [i for i in infos if i["is_drum"]]
        if len(drums) == 1:
            chosen = drums[0]
        elif len(drums) > 1:
            chosen = drums[0]
            warnings.append(f"Encontrei {len(drums)} partes de percussão "
                            f"({', '.join(i['name'] or i['id'] for i in drums)}); usando a primeira. "
                            "Use --part para escolher.")
        elif len(infos) == 1:
            chosen = infos[0]
            warnings.append("Não identifiquei uma parte de bateria pelo nome/instrumento; "
                            "usando a única parte disponível.")
        else:
            raise ScoreError("Não encontrei uma parte de bateria/percussão. Partes disponíveis: "
                             + ", ".join(i["name"] or i["id"] for i in infos)
                             + ". Use --part <nome ou nº> para escolher.")

    parts = {p.get("id"): p for p in root.findall("part")}
    if chosen["id"] not in parts:
        raise ScoreError(f"A parte '{chosen['id']}' não existe no corpo do MusicXML.")
    return parts[chosen["id"]], chosen["inst_pitch"], warnings


def _xml_metadata(root: ET.Element) -> Tuple[Optional[str], Optional[str]]:
    title = root.findtext("work/work-title") or root.findtext("movement-title")
    artist = None
    for c in root.findall("identification/creator"):
        if (c.get("type") or "").lower() in ("composer", "arranger", ""):
            artist = (c.text or "").strip() or None
            if artist:
                break
    return (title.strip() if title else None), artist


def _xml_tempo(part: ET.Element, root: ET.Element, warnings: List[str]) -> Optional[float]:
    bpms: List[float] = []
    for snd in part.iter("sound"):
        t = snd.get("tempo")
        if t:
            try:
                v = float(t)
                if 20 <= v <= 350:
                    bpms.append(v)
            except ValueError:
                pass
    for pm in part.iter("per-minute"):
        try:
            v = float((pm.text or "").strip())
            if 20 <= v <= 350:
                bpms.append(v)
        except ValueError:
            pass
    if not bpms:
        return None
    if len(set(round(b) for b in bpms)) > 1:
        warnings.append(f"A partitura tem {len(set(bpms))} andamentos diferentes; usando o primeiro "
                        f"({bpms[0]:g}). O chart gerado usa BPM constante — se a música tem "
                        "mudanças de tempo, passe --bpm e confira a sincronização.")
    return bpms[0]


def _xml_time_signature(part: ET.Element, warnings: List[str]) -> int:
    sigs: List[Tuple[int, int]] = []
    for tm in part.iter("time"):
        try:
            num = int(tm.findtext("beats"))
            den = int(tm.findtext("beat-type"))
            if (num, den) not in sigs:
                sigs.append((num, den))
        except (TypeError, ValueError):
            pass
    if not sigs:
        warnings.append("Sem fórmula de compasso na partitura; usando 4/4.")
        return 4
    if len(sigs) > 1:
        warnings.append("A partitura tem mais de uma fórmula de compasso "
                        f"({', '.join(f'{n}/{d}' for n, d in sigs)}); usando a primeira "
                        f"({sigs[0][0]}/{sigs[0][1]}). Confira a sincronização.")
    num, den = sigs[0]
    if den != 4:
        warnings.append(f"Fórmula de compasso {num}/{den}: as posições são medidas em semínimas; "
                        "confira --beats-per-bar e o BPM gerado.")
    return num


def _load_musicxml(path: Path, part_hint: Optional[str], work: Optional[Path]) -> Score:
    xml_path = _musicxml_path(Path(path), work)
    try:
        root = ET.parse(str(xml_path)).getroot()
    except ET.ParseError as e:
        raise ScoreError(f"Falha ao ler o MusicXML {xml_path.name}: {e}")
    if root.tag != "score-partwise":
        if root.tag == "score-timewise":
            raise ScoreError("MusicXML timewise não suportado; exporte como partwise.")
        raise ScoreError(f"Arquivo não parece um MusicXML (raiz <{root.tag}>).")

    part, inst_pitch, warnings = _pick_xml_drum_part(root, part_hint)
    sc = Score()
    sc.warnings = warnings

    divisions = 1
    pos_div = 0
    pending_chord_pos: Optional[int] = None
    last_onset: Optional[int] = None

    for measure in part.findall("measure"):
        for el in measure:
            tag = el.tag
            if tag == "attributes":
                d = el.findtext("divisions")
                if d:
                    try:
                        divisions = int(d)
                    except ValueError:
                        pass
            elif tag in ("backup",):
                d = int(el.findtext("duration") or "0")
                pos_div = max(0, pos_div - d)
            elif tag == "forward":
                d = int(el.findtext("duration") or "0")
                pos_div += d
            elif tag == "note":
                dur = int(el.findtext("duration") or "0")
                is_chord = el.find("chord") is not None
                if el.find("rest") is not None and not is_chord:
                    pos_div += dur
                    continue
                onset = last_onset if is_chord and last_onset is not None else pos_div
                last_onset = onset
                gm: Optional[int] = None
                iel = el.find("instrument")
                iid = iel.get("id") if iel is not None else None
                if iid and iid in inst_pitch:
                    gm = inst_pitch[iid]
                if gm is None:
                    pel = el.find("pitch")
                    if pel is not None:
                        step = pel.findtext("step")
                        octv = pel.findtext("octave")
                        alter = float(pel.findtext("alter") or "0")
                        if step and octv:
                            gm = _pitch_to_midi(step, alter, int(octv))
                if gm is not None:
                    lane = map_pitch(gm)
                    if lane is None:
                        _add_unmapped(sc, gm)
                    else:
                        sc.events.append(NoteEvent(quarter=_quantize(onset / divisions), lane=lane))
                pos_div = onset + dur

    sc.events = _dedup_sort(sc.events)
    if not sc.events:
        raise ScoreError("Nenhuma nota de bateria reconhecida na parte selecionada. Verifique se a "
                         "parte é a bateria (use --part) e se as notas usam pitches do General MIDI "
                         "drum map (35-81) ou <midi-unpitched>.")

    sc.bpm = _xml_tempo(part, root, sc.warnings)
    sc.beats_per_bar = _xml_time_signature(part, sc.warnings)
    sc.title, sc.artist = _xml_metadata(root)

    _check_close_notes(sc)
    return sc


def _load_midi(path: Path, track_hint: Optional[str]) -> Score:
    try:
        import pretty_midi
    except ImportError:
        raise ScoreError("pretty_midi não encontrado (necessário para MIDI). "
                         "Instale com: pip install pretty_midi")
    try:
        pm = pretty_midi.PrettyMIDI(str(path))
    except Exception as e:
        raise ScoreError(f"Falha ao ler o MIDI {path.name}: {e}")

    insts = pm.instruments
    if not insts:
        raise ScoreError("O MIDI não tem trilhas.")

    sc = Score()
    inst = None
    if track_hint:
        if track_hint.isdigit():
            idx = int(track_hint) - 1
            if 0 <= idx < len(insts):
                inst = insts[idx]
            else:
                raise ScoreError(f"--track {track_hint} fora do alcance (o MIDI tem {len(insts)} trilhas).")
        else:
            for i in insts:
                if track_hint.lower() in (i.name or "").lower():
                    inst = i
                    break
            if inst is None:
                raise ScoreError(f"--track '{track_hint}' não encontrada. Trilhas: "
                                 + ", ".join(i.name or "?" for i in insts))
    else:
        drums = [i for i in insts if i.is_drum]
        if len(drums) == 1:
            inst = drums[0]
        elif len(drums) > 1:
            inst = max(drums, key=lambda i: len(i.notes))
            sc.warnings.append(f"MIDI com {len(drums)} trilhas de percussão; usando "
                               f"'{inst.name or '?'}'. Use --track para escolher outra.")
        elif len(insts) == 1:
            inst = insts[0]
        else:
            scored = sorted(((sum(1 for n in i.notes if map_pitch(n.pitch)), i) for i in insts),
                            key=lambda x: -x[0])
            if not scored or scored[0][0] == 0:
                raise ScoreError("Não encontrei nenhuma nota de bateria GM no MIDI. Trilhas: "
                                 + ", ".join(i.name or "?" for i in insts))
            inst = scored[0][1]
            sc.warnings.append(f"Nenhuma trilha marcada como percussão; usando '{inst.name or '?'}' "
                               "(mais notas de bateria). Use --track para escolher outra.")

    times, bpms = pm.get_tempo_changes()
    bpm = float(bpms[0]) if len(bpms) else None
    if bpm and (bpm < 20 or bpm > 350):
        sc.warnings.append(f"BPM {bpm:g} do MIDI parece inválido; ignorando (passe --bpm).")
        bpm = None
    if bpm and len(bpms) > 1 and (max(bpms) - min(bpms)) > 2:
        sc.warnings.append(f"O MIDI tem mudanças de andamento; usando o BPM inicial ({bpm:g}). "
                           "Confira a sincronização ou passe --bpm.")
    sc.bpm = bpm

    try:
        ts_changes = pm.time_signature_changes
        if ts_changes:
            sigs = list(dict.fromkeys((t.numerator, t.denominator) for t in ts_changes))
            if len(sigs) > 1:
                sc.warnings.append("O MIDI tem mais de uma fórmula de compasso "
                                   f"({', '.join(f'{n}/{d}' for n, d in sigs)}); usando a primeira.")
            sc.beats_per_bar = int(sigs[0][0])
    except Exception:
        pass

    for n in inst.notes:
        lane = map_pitch(n.pitch)
        if lane is None:
            _add_unmapped(sc, n.pitch)
            continue
        # pretty_midi: tick / resolution = semínimas (quarters)
        sc.events.append(NoteEvent(quarter=_quantize(pm.time_to_tick(n.start) / pm.resolution),
                                   lane=lane))
    sc.events = _dedup_sort(sc.events)
    if not sc.events:
        raise ScoreError(f"Nenhuma nota de bateria GM reconhecida na trilha '{inst.name or '?'}'. "
                         "Use --track para escolher outra.")
    return sc


def pdf_to_pages(pdf: Path, work: Path) -> List[Path]:
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            raise ScoreError("pymupdf não encontrado (necessário para PDF). "
                             "Instale com: pip install pymupdf")
    doc = pymupdf.open(str(pdf))
    pages: List[Path] = []
    for i, page in enumerate(doc):
        out = work / f"{pdf.stem}_p{i + 1}.png"
        if not out.exists():
            pix = page.get_pixmap(dpi=200)
            pix.save(str(out))
        pages.append(out)
    doc.close()
    if not pages:
        raise ScoreError(f"O PDF {pdf.name} não tem páginas.")
    return pages


def omer_page(img: Path, out_base: Path) -> Optional[Path]:
    try:
        import oemer  # noqa: F401
    except ImportError:
        raise ScoreError("oemer não encontrado (necessário para PDF). Instale com: pip install oemer\n"
                         "Obs.: o OMR do oemer é experimental para percussão; o caminho recomendado é "
                         "abrir o PDF no MuseScore e exportar MusicXML.")
    for cand in (Path(str(out_base) + ".musicxml"), Path(str(out_base) + ".xml")):
        if cand.exists():
            log(f"  (reaproveitando OMR já convertido: {cand.name})")
            return cand
    log(f"  Convertendo {img.name} -> MusicXML com oemer (experimental, pode levar minutos)...")
    out_file = Path(str(out_base) + ".musicxml")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    oemer_exe = shutil.which("oemer")
    cmd = [oemer_exe] if oemer_exe else [sys.executable, "-m", "oemer"]
    # o oemer trata -o como pasta se não terminar em .musicxml
    r = subprocess.run(cmd + [str(img), "-o", str(out_file)], capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "")[-800:]
        raise ScoreError(f"oemer falhou em {img.name} (código {r.returncode}):\n{tail}")
    for cand in (out_file, Path(str(out_base) + ".xml")):
        if cand.exists():
            return cand
    raise ScoreError(f"oemer terminou mas não gerou MusicXML para {img.name}.")


def _merge_page(base: Score, page: Score, start_quarter: Fraction) -> None:
    for e in page.events:
        base.events.append(NoteEvent(quarter=e.quarter + start_quarter, lane=e.lane))


def _load_pdf(pdf: Path, work: Path, part_hint: Optional[str]) -> Score:
    pages = pdf_to_pages(pdf, work)
    base: Optional[Score] = None
    cursor = Fraction(0)
    for i, img in enumerate(pages):
        out_base = work / (pdf.stem + f"_p{i + 1}")
        xml_path = omer_page(img, out_base)
        try:
            page = _load_musicxml(xml_path, part_hint, work)
        except ScoreError as e:
            if base is None:
                msg = str(e)
                if "bateria" in msg.lower():
                    msg += ("\n  Dica: o OMR costuma ler partituras de bateria como se fossem "
                            "notas com altura (ex.: parte 'Piano' com F4/C5/G5 nas posições do "
                            "pentagrama). O caminho confiável para PDF é abrir no MuseScore e "
                            "exportar MusicXML, ou usar o MIDI exportado pelo editor.")
                raise ScoreError(msg)
            log(f"  AVISO: página {i + 1} não rendeu notas ({str(e).splitlines()[0]}); ignorada.")
            continue
        if base is None:
            base = page
        else:
            _merge_page(base, page, cursor)
        # próxima página continua no início do compasso seguinte ao fim desta
        bar_q = page.beats_per_bar or 4
        end_local = page.quarter_end()
        cursor += Fraction(math.ceil(float(end_local) / bar_q) * bar_q)
    if base is None:
        raise ScoreError("Nenhuma página do PDF rendeu notas de bateria. O OMR de percussão é "
                         "experimental; recomenda-se abrir o PDF no MuseScore e exportar MusicXML.")
    base.events = _dedup_sort(base.events)
    if len(pages) > 1:
        base.warnings.append(f"PDF com {len(pages)} páginas mescladas: cada página foi alinhada no "
                             "início de compasso. Confira se nenhuma página quebra compassos no meio.")
    return base


def _check_close_notes(sc: Score) -> None:
    dup = 0
    for i in range(1, len(sc.events)):
        d = sc.events[i].quarter - sc.events[i - 1].quarter
        if sc.events[i].lane == sc.events[i - 1].lane and 0 < d < Fraction(1, 32):
            dup += 1
    if dup > 4:
        sc.warnings.append(f"{dup} pares de notas muito próximas na mesma lane — a grade do DTX "
                           "pode arredondá-las. Confira vozes duplicadas na partitura.")


def load_score(path: Path, work: Optional[Path] = None, part_hint: Optional[str] = None,
               track_hint: Optional[str] = None) -> Score:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        if work is None:
            raise ScoreError("PDF precisa de uma pasta de trabalho (--out cria uma automaticamente).")
        sc = _load_pdf(path, work, part_hint or track_hint)
        sc.warnings.append("Partitura vinda de OMR (PDF): confira as notas com atenção — "
                           "o reconhecimento de percussão do oemer é experimental.")
        _check_close_notes(sc)
        return sc

    if suffix in (".mid", ".midi"):
        sc = _load_midi(path, track_hint or part_hint)
    elif suffix in (".musicxml", ".xml", ".mxl"):
        sc = _load_musicxml(path, part_hint or track_hint, work)
    else:
        raise ScoreError(f"Formato de partitura não suportado: {suffix} "
                         "(use .musicxml, .xml, .mxl, .mid, .midi ou .pdf)")

    _check_close_notes(sc)
    return sc


def suggest_grid_div(sc: Score) -> int:
    lcm = 1
    for e in sc.events:
        d = (e.quarter - int(e.quarter)).denominator
        lcm = lcm * d // _gcd(lcm, d)
    for cand in (1, 2, 3, 4, 6, 8, 12, 16, 24, 48):
        if cand >= lcm:
            return cand
    return 48


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def quantize_events_to_grid(sc: Score, grid_div: int) -> int:
    moved = 0
    step = Fraction(1, grid_div)
    snapped = []
    for e in sc.events:
        pos = round(e.quarter / step) * step
        if pos != e.quarter:
            moved += 1
        snapped.append(NoteEvent(quarter=pos, lane=e.lane))
    sc.events = _dedup_sort(snapped)
    return moved
