# Especificação — Transcrever, diarizar, identificar e classificar áudios

Versão: 2026-09-23

## Propósito

Construir um sistema para processar conjuntos de arquivos de áudio e vídeo, transcrevendo seu conteúdo, separando os participantes, identificando vozes já conhecidas e armazenando os resultados de forma organizada e rastreável.

O sistema deverá ser desenvolvido de forma incremental, começando pela avaliação das ferramentas disponíveis e evoluindo até a classificação automática do conteúdo.

## Princípios

O processamento deverá separar claramente quatro responsabilidades:

1. **Transcrição** — transformar fala em texto;
2. **Diarização** — determinar quem falou em cada trecho;
3. **Identificação de voz** — relacionar uma voz diarizada a uma pessoa conhecida;
4. **Classificação** — identificar os assuntos tratados no áudio.

Essas etapas podem e provavelmente irão utilizar ferramentas diferentes.

Não assumir que uma única biblioteca ou serviço seja necessariamente a melhor solução para todas elas.

## Identificação de participantes

A diarização deverá inicialmente produzir identificadores neutros:

```text
speaker-00
speaker-01
speaker-02
```

A identificação de uma pessoa pelo nome somente poderá ocorrer quando existir previamente uma amostra de voz confirmada dessa pessoa e houver correspondência acima do limite de confiança definido.

Exemplo:

```text
speaker-00 -> Esteban
speaker-01 -> Fernanda
```

### Voz desconhecida

Quando surgir uma voz para a qual não exista identificação confiável, manter somente o identificador neutro.

Nunca tentar adivinhar o nome da pessoa.

Após o processamento deverá ser possível informar manualmente, por exemplo:

```text
speaker-01 = Maria
```

Essa confirmação poderá criar ou atualizar o cadastro de voz de Maria.

A partir daí, futuros arquivos poderão reconhecer essa voz automaticamente quando a similaridade estiver acima do limite de confiança.

Em caso de dúvida, sempre preferir `speaker-XX` a atribuir uma identidade possivelmente incorreta.

## Cadastro de vozes

Manter um cadastro persistente de participantes conhecidos.

Cada participante poderá possuir uma ou mais amostras de voz confirmadas.

Exemplo conceitual:

```text
vozes/
  esteban/
    amostra-001
    amostra-002

  fernanda/
    amostra-001

  maria/
    amostra-001
```

O mecanismo concreto poderá utilizar embeddings ou outro método adequado de reconhecimento de voz.

Somente amostras cuja identidade tenha sido confirmada explicitamente pelo usuário poderão alimentar esse cadastro.

## Fase 0 — Avaliação dos métodos

Utilizar uma amostra aleatória e reproduzível de áudios armazenados em:

```text
/home/esteban/Sync/Backups/Android/VoiceRecorder
```

O objetivo é descobrir a melhor combinação de métodos para o ambiente `devel3`.

O serviço existente:

```text
https://whisper.inovacaosistemas.com.br
```

deverá participar da avaliação como uma das alternativas de transcrição.

Também avaliar soluções locais e, quando apropriado, implementações nativas/compiladas.

Dar preferência a soluções nativas ou compiladas quando apresentarem qualidade equivalente, mas não excluir uma solução melhor apenas por ser implementada em Python.

Comparar, conforme aplicável:

- qualidade da transcrição em português;
- precisão da diarização;
- capacidade de separar vozes semelhantes;
- precisão da identificação de vozes conhecidas;
- velocidade;
- consumo de memória;
- consumo de CPU;
- facilidade de instalação;
- facilidade de manutenção;
- compatibilidade com `devel3`;
- dependências externas e necessidade de rede.

Cada rodada deve produzir evidências reproduzíveis, incluindo os arquivos escolhidos, hashes, configuração dos motores, tempos, consumo de recursos, stdout, stderr e resultados produzidos.

Não inventar uma pontuação automática de qualidade quando não existir transcrição de referência. Nesse caso, produzir material para revisão humana lado a lado.

Ao final da Fase 0, documentar os resultados e a combinação recomendada de ferramentas para as etapas seguintes.

## Fase 1 — Ingestão, transcrição e diarização

Criar um script Python 3 capaz de processar todos os arquivos de áudio encontrados inicialmente em:

```text
/home/esteban/Sync/Backups/Android/VoiceRecorder
```

O script também deverá aceitar outros arquivos ou diretórios informados pela linha de comando.

### Pipeline

A sequência deverá ser:

```text
descobrir arquivo
-> calcular hash
-> verificar duplicidade
-> obter metadados
-> transcrever
-> diarizar
-> identificar vozes conhecidas
-> gerar Markdown
-> validar resultado
-> registrar índice
-> mover áudio original
```

O áudio original somente deverá ser movido depois que todas as etapas obrigatórias anteriores tiverem terminado com sucesso.

Em caso de falha, preservar o arquivo original no local de origem.

## Controle de duplicidade

Usar SHA-256 do conteúdo do arquivo.

O índice deverá ficar em:

```text
/home/esteban/Sync/Projects/protegendo-a-torre/indice
```

A duplicidade deve ser determinada pelo conteúdo, não pelo nome ou caminho.

Se o mesmo áudio aparecer com outro nome ou em outro diretório, ele não deverá ser processado novamente.

## Determinação de data e hora

Não confiar exclusivamente no `mtime` do sistema de arquivos.

Usar a seguinte ordem de preferência:

1. data/hora embutida nos metadados da mídia;
2. data/hora identificável no nome original;
3. `creation_time` ou equivalente nos metadados;
4. `mtime` como último recurso.

Registrar no Markdown qual fonte foi utilizada para determinar a data.

## Arquivos de saída

As transcrições deverão ser armazenadas em:

```text
/home/esteban/Sync/Projects/protegendo-a-torre/transcricoes
```

Os áudios processados deverão ser movidos para:

```text
/home/esteban/Sync/Projects/protegendo-a-torre/audios
```

O nome-base deverá seguir:

```text
YYYY-MM-DD-hh-mm-ss-<participante1>-<participante2>...
```

Quando a identidade for conhecida:

```text
2026-09-23-14-30-00-esteban-maria.m4a
2026-09-23-14-30-00-esteban-maria.md
```

Quando houver participantes ainda desconhecidos:

```text
2026-09-23-14-30-00-esteban-speaker-01.m4a
2026-09-23-14-30-00-esteban-speaker-01.md
```

Após uma identificação manual, o sistema deverá permitir atualizar metadados e, quando apropriado, renomear os arquivos de forma segura.

## Formato da transcrição

Gerar Markdown contendo metadados estruturados e a conversa diarizada.

Exemplo:

```markdown
---
data: 2026-09-23T14:30:00-03:00
arquivo_original: gravacao123.m4a
sha256: abcdef...
participantes:
  - esteban
  - speaker-01
---

# Transcrição

**Esteban — 00:00:03**

Texto...

**speaker-01 — 00:00:11**

Texto...
```

Preservar timestamps sempre que a ferramenta utilizada fornecer essa informação.

## Identificação posterior

Depois de uma transcrição que contenha participantes desconhecidos, apresentar de forma simples quais identificadores ainda precisam ser identificados.

Exemplo:

```text
Participantes não identificados:
speaker-01
speaker-02
```

Permitir então informar:

```text
speaker-01 = Maria
speaker-02 = João
```

Essas associações deverão ser persistidas somente após confirmação explícita.

## Fase 2 — Classificação

Depois que a Fase 1 estiver estável, classificar automaticamente cada transcrição.

A classificação deverá aceitar múltiplas categorias para o mesmo arquivo.

Categorias iniciais:

- relacionamentos;
- negócios e trabalho;
- terapia;
- contas.

Exemplo:

```yaml
categorias:
  - relacionamentos
  - terapia
  - contas
```

Não duplicar fisicamente o mesmo áudio ou Markdown em várias pastas apenas por pertencer a várias categorias.

Manter um único arquivo e construir índices ou metadados que permitam localizá-lo pelas diferentes categorias.

## Requisitos de segurança operacional

O processamento deverá ser idempotente.

Nunca:

- apagar o áudio original antes da conclusão do processamento;
- sobrescrever silenciosamente uma transcrição existente;
- atribuir nome a uma voz desconhecida por suposição;
- registrar como amostra conhecida uma voz não confirmada;
- processar novamente um arquivo cujo hash já esteja registrado.

Registrar erros e decisões relevantes para permitir auditoria e reprocessamento.
