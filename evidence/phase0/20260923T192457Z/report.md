# Fase 0 — Benchmark de transcrição

- Rodada: `20260923T192457Z`
- Seed: `20260923`
- Fonte: `/home/esteban/Sync/Backups/Android/VoiceRecorder`
- Modelo local: `medium`
- Chunk remoto: `300s`

## Ambiente

- python: `3.10.12`
- ffmpeg: `/usr/bin/ffmpeg`
- ffprobe: `/usr/bin/ffprobe`
- gnu_time: `/usr/bin/time`
- whisper_cpp: `None`
- faster_whisper_python: `True`

## Amostras

| ID | Arquivo | SHA-256 | Bytes | Duração (s) |
|---|---|---|---:|---:|
| S01 | Fer choro 27 outubro 2024.m4a | `b35f779c23dbbe1f…` | 4461089 | 271.476 |

## Execuções

| Amostra | Motor | Estado | Parede (s) | CPU user | CPU sys | RSS máx. KiB | Saída/nota |
|---|---|---|---:|---:|---:|---:|---|
| S01 | remote | failed | 30.779 | 1.3 | 0.07 | 51648 | ver stderr.txt |
| S01 | local | ok | 237.952 | 529.39 | 47.92 | 2424656 | runs/S01/local/output/Fer choro 27 outubro 2024.md |

## Observação

Esta primeira rodada mede execução e produz transcrições lado a lado. Qualidade linguística será revisada manualmente porque ainda não existe uma transcrição de referência confiável. Diarização e identificação de voz entram nas próximas iterações da Fase 0.
