# Comparação de hosts para transcrição

- Rodada: `smoke-hosts-20260925T155245Z`
- Áudio: `/home/esteban/Sync/Backups/Android/VoiceRecorder/Esteban e Felipe Gaia 2026-07-31.m4a`
- Bytes: `11538442`
- SHA-256: `001bae3694971028f79e45b04c3a9899a9a3e17b0a486d229f6ce40e84fcf0b1`

## Candidatos de transcrição

| Posição | Candidato | Tipo | Estado | Parede (s) | Fator tempo real | Projeção 60min (min) | Saída/erro |
|---:|---|---|---|---:|---:|---:|---|

## Capacidade bruta dos hosts

| Posição | Host | Estado | Score | CPU hash/s | Decode x tempo real | CPU cores | Mem disponível GiB | Observação |
|---:|---|---|---:|---:|---:|---:|---:|---|
| 1 | devel3 | ok | 11637.262 | 1036.0 | 1369.54 | 4 | 3.77 |  |
| 2 | dom1 | ok | 363.182 | 401.0 | n/d | 16 | 51.27 |  |

## Ferramentas detectadas

- `devel3`: ffmpeg=`/usr/bin/ffmpeg`, faster_whisper=`True`, vosk=`True`, whisper_cli=`None`
- `dom1`: ffmpeg=`None`, faster_whisper=`False`, vosk=`False`, whisper_cli=`None`

## Interpretação

A seção de transcrição mede o fluxo real que interessa para lote de áudio. A seção de hosts mede apenas capacidade bruta, útil para escolher onde instalar ou mover serviços, mas não substitui o benchmark real de `/v1/audio/transcriptions`.
