# my-transcriver

Transcritor em Python 3 para converter conjuntos de arquivos de áudio e vídeo em Markdown.

O caminho padrão usa o Whisper já hospedado em `https://whisper.inovacaosistemas.com.br`. O `faster-whisper` local continua disponível como modo opcional para uso offline.

## Arquitetura

Por padrão:

```text
arquivo de áudio/vídeo
        |
        v
https://whisper.inovacaosistemas.com.br/v1/audio/transcriptions
        |
        v
arquivo .md
```

O serviço é OpenAI-compatible e usa por padrão o modelo `Systran/faster-whisper-medium`.

## Requisitos

- Python 3.9 ou superior
- acesso ao servidor `whisper.inovacaosistemas.com.br`

Não é necessário baixar um modelo do Hugging Face no modo padrão.

## Instalação

```bash
git clone git@github.com:EDortta/my-transcriver.git
cd my-transcriver
git checkout development

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

Um diretório inteiro:

```bash
python3 transcribe.py ./gravacoes
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

Sobrescrever transcrições existentes:

```bash
python3 transcribe.py ./gravacoes --overwrite
```

## Servidor Whisper

O padrão é:

```text
https://whisper.inovacaosistemas.com.br
```

Pode ser alterado por argumento:

```bash
python3 transcribe.py audio.mp3 --server-url https://outro-whisper.exemplo.com
```

Ou por variável de ambiente:

```bash
export WHISPER_URL=https://whisper.inovacaosistemas.com.br
python3 transcribe.py audio.mp3
```

O endpoint utilizado é:

```text
POST /v1/audio/transcriptions
```

com multipart contendo `file`, `model` e, quando informado, `language`.

O serviço atual não exige autenticação. Se isso mudar, o cliente já aceita:

```bash
export WHISPER_API_KEY=...
```

## Backend local opcional

Para trabalhar offline, instale também o `faster-whisper`:

```bash
pip install -r requirements-local.txt
```

E execute:

```bash
python3 transcribe.py audio.mp3 --backend local
```

Nesse modo o modelo é baixado do Hugging Face na primeira utilização. Se aparecer o aviso sobre requisições não autenticadas ao HF Hub, ele não é erro; apenas informa que downloads anônimos têm limite menor. Opcionalmente:

```bash
export HF_TOKEN=seu_token
```

O `HF_TOKEN` não é necessário no backend remoto.

### GPU local

```bash
python3 transcribe.py ./gravacoes \
  --backend local \
  --device cuda \
  --compute-type float16
```

### Timestamps

Neste momento `--timestamps` está disponível no backend local:

```bash
python3 transcribe.py entrevista.mp3 --backend local --timestamps
```

No backend remoto o programa gera a transcrição normalmente, sem timestamps.

## Comportamento padrão

- backend: `remote`
- servidor: `https://whisper.inovacaosistemas.com.br`
- modelo remoto: `Systran/faster-whisper-medium`
- idioma: detecção automática
- timeout remoto: 3600 segundos
- saída: um `.md` por mídia
- arquivos `.md` existentes não são sobrescritos sem `--overwrite`
- erro em um arquivo não interrompe o restante do lote

## Formatos

O script reconhece formatos comuns de áudio e vídeo, incluindo MP3, WAV, M4A, AAC, FLAC, OGG, OPUS, WMA, MP4, MKV, MOV, AVI, WEBM, MPEG, MPG, M4V e 3GP.

## Saída Markdown

Exemplo:

```markdown
# Transcrição — entrevista.mp3

- Fonte: `entrevista.mp3`
- Backend: `remote`
- Idioma: `pt`
- Modelo: `Systran/faster-whisper-medium`

## Transcrição

Texto transcrito...
```
