# Fase 0 — Benchmark

Primeira implementação da Fase 0 definida em `../pmpt-transcrever-classificar.md`.

Este corte mede **transcrição**. Diarização e identificação de voz serão adicionadas nas próximas iterações usando o mesmo formato de evidências.

## O que faz

- sorteia uma amostra reproduzível do VoiceRecorder;
- calcula SHA-256 dos arquivos escolhidos;
- obtém duração com `ffprobe`, quando disponível;
- inventaria ferramentas relevantes instaladas no `devel3`;
- compara o Whisper remoto e o `faster-whisper` local;
- envia o remoto em chunks de 300 segundos por padrão, evitando 413/timeout em áudios longos;
- mede tempo de parede, CPU e RSS máximo via `/usr/bin/time -v`, quando disponível;
- grava stdout, stderr e cada transcrição;
- produz `results.json`, `report.md` e `review-template.md`.

Por padrão, o benchmark **não permite download de modelo do Hugging Face**. Para o motor local ele define `HF_HUB_OFFLINE=1`. O modelo `medium` já foi validado no `devel3` em modo offline; portanto a próxima rodada não deve consultar o HF Hub.

A primeira rodada de 2026-09-23 foi diagnóstica: o remoto falhou com HTTP 413/502 ao receber arquivos longos inteiros, e o local falhou porque o modelo ainda não estava cacheado. Ambos os pontos foram corrigidos antes da rodada seguinte.

## Executar

```bash
git checkout development
git pull

HF_HUB_OFFLINE=1 python3 phase0/benchmark.py
```

A rodada padrão usa 5 arquivos, seed `20260923` e os motores `remote,local`.

Somente Whisper remoto:

```bash
python3 phase0/benchmark.py --engines remote
```

Permitir que o modelo local seja baixado, se necessário:

```bash
python3 phase0/benchmark.py --allow-model-download
```

Aumentar a amostra:

```bash
python3 phase0/benchmark.py --samples 10 --seed 20260923
```

Fazer apenas a seleção e os comandos, sem transcrever:

```bash
python3 phase0/benchmark.py --dry-run
```

## Evidências

Cada execução cria:

```text
evidence/phase0/YYYYMMDDTHHMMSSZ/
  results.json
  report.md
  review-template.md
  runs/
    S01/
      remote/
      local/
```

Os áudios originais nunca são movidos ou modificados nesta fase.

## Smoke test recomendado

Antes de repetir as cinco amostras, valide nos dois motores o áudio que já funcionou localmente:

```bash
HF_HUB_OFFLINE=1 python3 phase0/benchmark.py \
  --file "/home/esteban/Sync/Backups/Android/VoiceRecorder/Fer choro 27 outubro 2024.m4a" \
  --engines remote,local \
  --remote-chunk-seconds 300
```

Se os dois resultados forem `ok`, execute a rodada completa:

```bash
HF_HUB_OFFLINE=1 python3 phase0/benchmark.py \
  --samples 5 \
  --seed 20260923 \
  --engines remote,local \
  --remote-chunk-seconds 300
```
