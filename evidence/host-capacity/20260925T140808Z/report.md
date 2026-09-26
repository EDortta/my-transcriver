# Comparação de hosts para transcrição

- Rodada: `20260925T140808Z`
- Áudio: `/home/esteban/Sync/Backups/Android/VoiceRecorder/Esteban e Felipe Gaia 2026-07-31.m4a`
- Bytes: `11538442`
- SHA-256: `001bae3694971028f79e45b04c3a9899a9a3e17b0a486d229f6ce40e84fcf0b1`

## Ranking

| Posição | Host | Estado | Score | CPU hash/s | Decode x tempo real | CPU cores | Mem disponível GiB | Observação |
|---:|---|---|---:|---:|---:|---:|---:|---|
| 1 | devel3 | ok | 11362.019 | 989.5 | 1338.35 | 4 | 6.02 |  |
| 2 | dom1 | ok | 361.99 | 396.88 | n/d | 16 | 52.01 |  |
| 3 | dom0a | failed | 0.0 | n/d | n/d | n/d | 0.00 | benchmark |

## Ferramentas detectadas

- `devel3`: ffmpeg=`/usr/bin/ffmpeg`, faster_whisper=`True`, vosk=`True`, whisper_cli=`None`
- `dom1`: ffmpeg=`None`, faster_whisper=`False`, vosk=`False`, whisper_cli=`None`
- `dom0a`: ffmpeg=`None`, faster_whisper=`None`, vosk=`None`, whisper_cli=`None`

## Interpretação

O score combina o microbenchmark de CPU, velocidade de decodificação com ffmpeg e memória disponível. Para uma decisão final, prefira o primeiro colocado que também tenha `faster_whisper`, `vosk` ou `whisper-cli` instalados, ou que aceite a instalação do backend escolhido.
