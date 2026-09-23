# Fase 0 — Benchmark de transcrição

- Rodada: `20260923T164203Z`
- Seed: `20260923`
- Fonte: `/home/esteban/Sync/Backups/Android/VoiceRecorder`

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
| S01 | Vanessa e Esteban 28 de agosto 2025.m4a | `0ab0e1bcd859b3dc…` | 70529637 | 4320.731 |
| S02 | Vanessa e Esteban 2025-12-04.m4a | `417a30fbec959ebe…` | 56155457 | 3439.756 |
| S03 | Vanessa e Esteban 2026-05-21.m4a | `b721e084309622a6…` | 95731139 | 5866.063 |
| S04 | Voice 007_1.m4a | `ebbbe9a2b6c2c413…` | 28178417 | 1725.079 |
| S05 | Vanessa e Esteban 2025-11-19.m4a | `e4f05baa3c6f8cd4…` | 82350398 | 5045.21 |

## Execuções

| Amostra | Motor | Estado | Parede (s) | CPU user | CPU sys | RSS máx. KiB | Saída/nota |
|---|---|---|---:|---:|---:|---:|---|
| S01 | remote | failed | 438.268 | 0.39 | 0.25 | 167880 | ver stderr.txt |
| S01 | local | failed | 10.624 | 3.38 | 0.95 | 476152 | ver stderr.txt |
| S02 | remote | failed | 348.733 | 0.32 | 0.31 | 139540 | ver stderr.txt |
| S02 | local | failed | 2.031 | 1.85 | 0.3 | 478416 | ver stderr.txt |
| S03 | remote | failed | 499.469 | 0.42 | 0.3 | 217132 | ver stderr.txt |
| S03 | local | failed | 4.775 | 1.99 | 0.34 | 473304 | ver stderr.txt |
| S04 | remote | failed | 161.27 | 0.18 | 0.07 | 85256 | ver stderr.txt |
| S04 | local | failed | 3.092 | 1.98 | 0.22 | 475436 | ver stderr.txt |
| S05 | remote | failed | 438.56 | 0.38 | 0.23 | 190948 | ver stderr.txt |
| S05 | local | failed | 2.121 | 1.87 | 0.35 | 475520 | ver stderr.txt |

## Observação

Esta primeira rodada mede execução e produz transcrições lado a lado. Qualidade linguística será revisada manualmente porque ainda não existe uma transcrição de referência confiável. Diarização e identificação de voz entram nas próximas iterações da Fase 0.
