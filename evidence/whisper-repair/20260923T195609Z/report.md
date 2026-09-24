# Restauração do Whisper remoto

Data UTC: 20260923T195609Z
Host: esteban@whisper.inovacaosistemas.com.br
Imagem: ghcr.io/speaches-ai/speaches:latest-cpu
Modelo: Systran/faster-whisper-medium
Container: whisper-speaches
Bind: 127.0.0.1:8093 -> 8000
Cache persistente: whisper-hf-cache
Health local: ok
Models local exit: 0
Health público exit: 0

O modelo de STT foi garantido pelo repair e fica persistido no volume Docker.
