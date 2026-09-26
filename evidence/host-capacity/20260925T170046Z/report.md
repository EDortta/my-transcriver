# Comparação de hosts para transcrição

- Rodada: `20260925T170046Z`
- Áudio: `/home/esteban/Sync/Backups/Android/VoiceRecorder/Esteban e Felipe Gaia 2026-07-31.m4a`
- Bytes: `11538442`
- SHA-256: `001bae3694971028f79e45b04c3a9899a9a3e17b0a486d229f6ce40e84fcf0b1`

## Candidatos de transcrição

| Posição | Candidato | Tipo | Estado | Parede (s) | Fator tempo real | Projeção 60min (min) | Saída/erro |
|---:|---|---|---|---:|---:|---:|---|
| 1 | devel3-local | local | ok | 837.808 | 1.1879 | 71.27 | transcription/devel3-local/output/Esteban e Felipe Gaia 2026-07-31.md |
| 2 | whisper-incus | remote | failed | 62.752 | 0.089 | 5.34 | servidor aquecendo, tente novamente em instantes
 |

## Capacidade bruta dos hosts

| Posição | Host | Estado | Score | CPU hash/s | Decode x tempo real | CPU cores | Mem disponível GiB | Observação |
|---:|---|---|---:|---:|---:|---:|---:|---|
| 1 | devel3 | ok | 11596.241 | 1002.75 | 1366.88 | 4 | 4.71 |  |
| 2 | dom1 | ok | 357.738 | 399.75 | n/d | 16 | 48.95 |  |

## Ferramentas detectadas

- `devel3`: ffmpeg=`/usr/bin/ffmpeg`, faster_whisper=`True`, vosk=`True`, whisper_cli=`None`
- `dom1`: ffmpeg=`None`, faster_whisper=`False`, vosk=`False`, whisper_cli=`None`

## Interpretação

A seção de transcrição mede o fluxo real que interessa para lote de áudio. A seção de hosts mede apenas capacidade bruta, útil para escolher onde instalar ou mover serviços, mas não substitui o benchmark real de `/v1/audio/transcriptions`.
