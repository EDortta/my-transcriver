# my-transcriver

Transcritor local em Python 3 para converter conjuntos de arquivos de áudio e vídeo em Markdown.

O projeto usa [faster-whisper](https://github.com/SYSTRAN/faster-whisper), executa localmente e não exige envio do conteúdo para uma API externa.

## Requisitos

- Python 3.9 ou superior
- Espaço em disco para o modelo Whisper escolhido

`faster-whisper` usa PyAV para decodificar áudio, portanto não exige uma instalação separada do FFmpeg para os formatos suportados.

## Instalação

```bash
git clone git@github.com:EDortta/my-transcriver.git
cd my-transcriver
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Uso rápido

Um arquivo:

```bash
python3 transcribe.py entrevista.mp3
```

Vários arquivos:

```bash
python3 transcribe.py audio1.mp3 video1.mp4 reuniao.m4a
```

Um ou mais diretórios:

```bash
python3 transcribe.py ./gravacoes ./videos
```

Os diretórios são percorridos recursivamente por padrão.

Forçar português:

```bash
python3 transcribe.py ./gravacoes --language pt
```

Salvar tudo em outro diretório:

```bash
python3 transcribe.py ./gravacoes --output-dir ./transcricoes
```

Incluir timestamps por segmento:

```bash
python3 transcribe.py ./gravacoes --timestamps
```

Usar um modelo maior:

```bash
python3 transcribe.py ./gravacoes --model medium
```

Usar GPU NVIDIA compatível:

```bash
python3 transcribe.py ./gravacoes --device cuda --compute-type float16
```

## Comportamento padrão

- modelo: `small`
- dispositivo: `cpu`
- precisão: `int8`
- idioma: detectado automaticamente
- filtro de silêncio/voz: habilitado
- saída: arquivo `.md` ao lado de cada mídia de origem
- arquivos `.md` existentes: não são sobrescritos sem `--overwrite`

## Formatos

O script procura formatos comuns de áudio e vídeo, incluindo MP3, WAV, M4A, AAC, FLAC, OGG, OPUS, WMA, MP4, MKV, MOV, AVI, WEBM, MPEG, MPG, M4V e 3GP.

## Exemplos

```bash
# Diretório inteiro, português, sobrescrevendo resultados antigos
python3 transcribe.py ~/Videos/aulas --language pt --overwrite

# CPU com modelo medium
python3 transcribe.py palestra.mp4 --model medium --compute-type int8

# GPU e timestamps
python3 transcribe.py entrevistas/ --device cuda --compute-type float16 --timestamps
```

## Saída Markdown

Cada arquivo gera um documento semelhante a:

```markdown
# Transcrição — entrevista.mp3

- Fonte: `entrevista.mp3`
- Idioma: `pt`
- Duração: `00:42:18`
- Modelo: `small`

## Transcrição

Texto transcrito...
```

Com `--timestamps`, cada segmento inclui o intervalo de tempo correspondente.
