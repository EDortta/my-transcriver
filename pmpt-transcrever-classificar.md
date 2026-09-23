## Propósito

Transcrever os áudios, identificar os participantes e armazenar os arquivos organizados em pastas específicas.

A **fase 2** será responsável por categorizar os arquivos de acordo com seu conteúdo, por exemplo:

- Relacionamentos;
- Negócios e trabalho;
- Terapia;
- Contas.

## Fase 0 — Avaliação dos métodos

Utilizar aleatoriamente áudios armazenados em:

```text
/home/esteban/Sync/Backups/Android/VoiceRecorder
```

O objetivo é avaliar quais métodos oferecem a melhor qualidade de transcrição e diarização neste computador.

Deve-se dar preferência a soluções desenvolvidas em linguagens compiladas, como **C**, **C++** ou **Go**, desde que possam ser utilizadas no ambiente `devel3`.

Os métodos serão comparados principalmente com base em:

- Qualidade da transcrição em português;
- Precisão da diarização;
- Velocidade de processamento;
- Consumo de memória e CPU;
- Facilidade de instalação e manutenção;
- Compatibilidade com o ambiente `devel3`.

## Fase 1 — Transcrição e diarização

Criar um script capaz de transcrever e diarizar todos os arquivos de áudio encontrados em:

```text
/home/esteban/Sync/Backups/Android/VoiceRecorder
```

O script também deverá permitir o processamento de áudios provenientes de outras fontes.

As transcrições deverão ser salvas em:

```text
/home/esteban/Sync/Projects/protegendo-a-torre/transcricoes
```

Os arquivos de áudio originais deverão ser movidos para:

```text
/home/esteban/Sync/Projects/protegendo-a-torre/audios
```

Tanto o arquivo de áudio movido quanto sua respectiva transcrição deverão utilizar o seguinte padrão de nomenclatura:

```text
YYYY-MM-DD-hh-mm-ss-<participante1>-<participante2>...
```

O nome deverá conter:

- Data e hora associadas ao áudio;
- Nome ou identificador de cada participante reconhecido pela diarização.

### Formato dos arquivos

- A transcrição deverá ser gerada em formato Markdown, com a extensão `.md`;
- O arquivo de áudio deverá manter sua extensão original, como `.mp3`, `.wav`, `.m4a` ou outra;
- O nome-base do áudio e da transcrição deverá ser o mesmo.

Exemplo:

```text
audios/2026-09-23-14-30-00-esteban-maria.m4a
transcricoes/2026-09-23-14-30-00-esteban-maria.md
```

## Controle de duplicidade

A pasta abaixo deverá armazenar os índices dos arquivos processados:

```text
/home/esteban/Sync/Projects/protegendo-a-torre/indice
```

Para cada arquivo, deverá ser calculado e armazenado um hash do seu conteúdo. Esse hash será utilizado para evitar o processamento duplicado de arquivos.

O controle por conteúdo é necessário porque o nome de um arquivo pode ser alterado ou porque o arquivo pode ser movido para outra pasta. Nesses casos, o sistema não deverá considerá-lo um novo arquivo se o conteúdo permanecer idêntico.