# file-to-dtx

Gera charts de bateria para o **DTXMania** a partir de **partituras** (MusicXML,
MIDI ou PDF), sincronizadas com um arquivo de mídia (mp3/mp4/wav...).

## Fluxo

1. Você fornece a mídia (mp3/mp4) e a partitura da bateria.
2. As notas da partitura viram o chart `.dtx` (peças mapeadas via General MIDI).
3. BPM/título/artista vêm da partitura, das tags do arquivo ou da linha de comando.
4. O resultado é uma pasta pronta para copiar para a pasta `Songs` do DTXMania.

## Uso

```
python score_to_dtx.py musica.mp4 --score bateria.musicxml
python score_to_dtx.py musica.mp3 --score bateria.mid --title "Nome" --artist "Banda"
python score_to_dtx.py musica.mp4 --score bateria.mid --auto-align
python score_to_dtx.py musica.mp4 --score bateria.pdf --bpm 124          # experimental
python score_to_dtx.py musica.mp4 --score bateria.musicxml --bgm isolated
```

Depois copie a pasta gerada (em `dtx_output/<título>/`) para a pasta `Songs` do DTXMania.

### Sincronização partitura x áudio

- **Manual (padrão)**: usa o BPM da partitura (ou `--bpm`). O início da partitura é
  assumido no início do áudio; ajuste fino com `--audio-offset-ms`
  (+ atrasa a BGM/vídeo, − adianta).
- **Automático (`--auto-align`)**: detecta os onsets do áudio e acha o deslocamento
  que melhor casa com as notas da partitura (reporta o % de casamento). O BPM da
  partitura tem prioridade; se não houver, é estimado do áudio.

Sempre é gerado 1 compasso de "lead-in" (contagem) antes das notas.

### Escolha da parte/trilha

- MusicXML/PDF: a parte de percussão é detectada pelo nome ("Drums", "Percussion",
  "Bateria"...) ou pelo canal MIDI 10. Use `--part "nome ou nº"` para escolher.
- MIDI: trilha marcada como percussão, ou a com mais notas GM de bateria.
  Use `--track "nome ou nº"` para escolher.

### Formatos de partitura

- **MIDI (.mid/.midi)** — o mais confiável. Exporte a trilha de bateria de qualquer
  editor. Pitches devem seguir o General MIDI drum map (canal 10).
- **MusicXML (.musicxml/.xml/.mxl)** — exporte do MuseScore/Finale/Sibelius.
  A identificação das peças usa `<midi-unpitched>` (export do MuseScore traz) ou o
  pitch escrito, quando a parte usa alturas.
- **PDF (.pdf)** — **experimental**. Usa o [oemer](https://github.com/BreezeWhite/oemer)
  (OMR). O oemer foi treinado para notação com alturas (piano/voz); **percussão
  frequentemente sai errada ou vazia**. Cada página vira um MusicXML separado e as
  páginas são emendadas no início de compasso — se uma página quebrar no meio de um
  compasso, o resultado desalinha.
  **Caminho recomendado para PDF: abrir no MuseScore e exportar MusicXML.**

### BGM

- `--bgm full` (padrão): o áudio original completo é a BGM. A bateria real do áudio
  toca junto com os samples do chart (é assim que a maioria dos charts do DTXMania
  funciona quando não há stem separado).
- `--bgm isolated`: roda o demucs para remover a bateria do áudio; a BGM fica sem
  bateria e só os samples do chart tocam (igual ao fluxo do mp4_to_dtx.py). Mais
  lento (minutos) e requer GPU para ser rápido.

## Limitações (v1)

- **BPM constante**: a partitura deve ter um andamento único. Mudanças de tempo
  (ritardando, mapas de tempo) não são suportadas — o primeiro andamento é usado e
  um aviso é emitido.
- **Fórmula de compasso única**: se a partitura tem várias fórmulas, a primeira é
  usada (com aviso). As posições em si continuam corretas em quarters; o que muda é
  apenas onde caem as linhas de compasso do .dtx.
- **Quiálteras**: grades de até 48 posições por tempo cobrem tercinas (3, 6, 12, 24)
  e fusas. Quiálteras de 5/7 são arredondadas para a grade (com aviso).
- **OMR de percussão (PDF)**: experimental, ver acima.

## Solução de problemas

| Sintoma | Solução |
|---------|---------|
| Notas atrasadas/adiantadas vs. música | `--audio-offset-ms +N` (atrasa BGM) ou `--auto-align` |
| BPM saiu pela metade/dobro | `--bpm <valor>` |
| "Não encontrei uma parte de bateria" | `--part "Drums"` (nome ou nº) / `--track` |
| Peças ignoradas (log) | A partitura usa pitches fora do GM drum map; reescreva no editor ou ajuste `mapping.py` |
| Dificuldade estranha no jogo | `--level` fixa um número 1-999; o padrão `auto` estima pela partitura (veja abaixo) |

### Dificuldade (`--level`)

O valor do `#DLEVEL` é o nível × 100: `750` aparece como 7.50 no DTXManiaNX (modo
decimal) e 75 no Skill Mode "Classic". O DTXCreator limita o campo a 999 e nível 0
some da seleção — por isso o resultado é sempre 100–999.

Não existe fórmula pública oficial (o "Estimate Difficulty" do DTXCreator vem do
DTXMania Online Stats e é fechado); a escala de referência é a do GITADORA
(0.00–9.99, tier MASTER ~7.00–7.50; na escala antiga 1–99, EXTREME ~40–70).

- **`--level auto`** (padrão): estima pela própria partitura, em
  [`difficulty.py`](./difficulty.py), usando:
  - `nps_avg` — notas por segundo (densidade média), interpolada numa curva de
    âncoras calibráveis (`NPS_ANCHORS`);
  - `nps_peak` — rajadas: máx. de notas em janela deslizante de 1 s vs. a média;
  - frações de semicolcheias/fusas (`frac_16`), tercinas (`frac_trip`) e
    acordes/flams (`frac_chord`) como ajuste de complexidade rítmica.

  O log mostra os componentes (ex.: `Level estimado: 7.30 (730) | 6.5 notas/s
  (pico 14/s) | 16ths 25% tercinas 10% acordes 15%`), para você julgar.
- **`--level 750`**: fixa o valor exatamente como antes.

É uma heurística de ponto de partida: calibre comparando com charts que você
conhece (o DTXCreator tem Edit → Estimate Difficulty como referência comparativa)
editando as constantes no topo de `difficulty.py`.

## Estrutura

```
file-to-dtx/
  score_to_dtx.py   # CLI principal
  sheet_process.py  # parser MusicXML/MIDI/PDF -> eventos (quarter, lane)
  mapping.py        # General MIDI drum map -> lanes DTX
  align.py          # sincronização manual e automática (onsets x partitura)
  difficulty.py     # estimativa do #DLEVEL pela densidade/padrões da partitura
  dtxkit.py         # empacotamento DTX: .dtx, BGM, vídeo, preview, samples
```
