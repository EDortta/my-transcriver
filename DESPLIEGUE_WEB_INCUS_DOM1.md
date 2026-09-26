# Despliegue de aplicativos web em Incus no dom1

Data: 2026-09-24

Este documento consolida como os aplicativos web devem ser publicados em
`dom1.inovacaosistemas.com.br` usando containers Incus. Ele descreve somente o
padrao generico de publicacao no dom1: nginx no host, container Incus por
aplicativo, porta local no host e validacao funcional.

Regra operacional: este runbook descreve o fluxo. Executar deploy, reload de
nginx, restart de servico ou alteracao remota continua exigindo aprovacao
explicita do operador.

## Modelo mental

O padrao e:

```text
Internet
  -> DNS publico do app
  -> nginx no host dom1, portas 80/443
  -> proxy_pass http://127.0.0.1:<porta-host>
  -> Incus proxy device no host
  -> container Incus do app
  -> servico interno do app, normalmente 127.0.0.1:<porta-app>
```

O `dom1` e o ponto de entrada publico/administrativo. O app nao precisa publicar
portas diretamente na internet; ele escuta dentro do container. O host expõe uma
porta local em `127.0.0.1` e o nginx faz o roteamento por dominio.

## Precedentes operacionais

### Aplicativo web simples

O formato mais direto para app web novo em Incus e:

- conecta em `esteban@dom1.inovacaosistemas.com.br`;
- usa `sudo bash` no host;
- cria ou reutiliza container Incus;
- instala dependencias dentro do container;
- cria `/etc/<app>.env`;
- cria banco local quando aplicavel;
- registra um servico systemd dentro do container;
- cria proxy device Incus:

```bash
incus config device add "$CONTAINER" webproxy proxy \
  listen="tcp:127.0.0.1:$HOST_PORT" \
  connect="tcp:127.0.0.1:8787" \
  bind=host
```

- cria vhost nginx no host em `/etc/nginx/sites-available/`;
- ativa symlink em `/etc/nginx/sites-enabled/`;
- testa `nginx -t`;
- recarrega com `service nginx reload`;
- valida localmente pelo host e pelo HTTPS publico resolvido para `127.0.0.1`.

Um script de deploy pode encapsular esses passos, mas ele precisa pertencer ao
projeto do aplicativo. Este runbook nao autoriza reutilizar scripts de outro
produto.

### Release atomica

Para apps com build local ou pacote de release, o padrao mais seguro e preparar
o artefato fora do container, enviar por `incus exec`, ativar em um diretorio
novo e so trocar para a versao nova depois do healthcheck local. Configuracoes,
segredos e bancos ficam fora da release de codigo.

## Topologia persistente por aplicativo

Cada aplicativo publicado no `dom1` deve manter no proprio repositorio um arquivo
de topologia sem segredos, por exemplo:

```text
web/deploy/dom1-topology.json
```

Esse arquivo registra o que foi efetivamente descoberto no servidor e passa a ser
a fonte de verdade operacional para deploys seguintes. Exemplo:

```json
{
  "schema": 1,
  "domain": "exemplo.inovacaosistemas.com.br",
  "host": "dom1.inovacaosistemas.com.br",
  "sshUser": "esteban",
  "incus": {
    "container": "exemplo-app",
    "state": "RUNNING",
    "webproxy": {
      "listen": "tcp:127.0.0.1:8195",
      "connect": "tcp:127.0.0.1:8787"
    }
  },
  "ports": {
    "host": 8195,
    "app": 8787
  },
  "paths": {
    "releaseRoot": "/opt/<app>/releases",
    "currentLink": "/opt/<app>/web"
  },
  "service": "<app>.service"
}
```

Regras:

- a topologia nao contem senhas, tokens ou chaves;
- ela deve ser descoberta no `dom1`, nao inventada localmente;
- uma porta em uso pelo proprio `webproxy` do aplicativo e valida e deve ser
  reutilizada;
- deploy normal usa a topologia persistida;
- uma opcao explicita, como `--refresh-topology`, deve redescobrir e atualizar
  o arquivo quando a infraestrutura mudar;
- nao procurar uma nova porta a cada deploy.

A descoberta deve consultar pelo menos:

```bash
sudo incus info <container>
sudo incus config device show <container>
sudo incus config device get <container> webproxy listen
sudo incus config device get <container> webproxy connect
```

## Separar provisionamento de deploy

Ha duas operacoes diferentes:

1. **Provisionamento inicial**: criar ou reservar o container, configurar rede,
   runtime, banco, servico systemd, proxy Incus, nginx e TLS.
2. **Deploy de release**: empacotar a versao autorizada, enviar o artefato,
   ativa-lo e validar o healthcheck.

Depois que a maquina estiver provisionada, deploys normais nao devem recriar
container, escolher porta, reinstalar infraestrutura ou reconfigurar nginx sem
necessidade.

O container tambem nao deve depender de credenciais GitHub para receber codigo.
Para repositorios privados, o padrao e gerar o artefato no ambiente de build
ja autenticado, enviar esse pacote ao `dom1` e usar `incus file push` para
coloca-lo no container.

Um fluxo recomendado de release e:

```text
checkout autorizado
  -> build local
  -> tar.gz + sha256
  -> scp para dom1
  -> incus file push para o container
  -> extracao em /opt/<app>/releases/<commit>
  -> healthcheck
  -> atualizacao de /opt/<app>/web
  -> restart do servico
  -> validacao local e publica
```

O checksum deve ser validado antes da ativacao. O commit ou identificador da
release deve ficar registrado no diretorio implantado para permitir auditoria e
rollback.

## Cuidados com automacao remota e Incus

Ao enviar um script remoto inteiro por `ssh ... bash -s`, o `stdin` continua
sendo o proprio script. Alguns comandos Incus podem ler YAML do `stdin` e
consumir inadvertidamente o restante do shell, produzindo erros como:

```text
yaml: line N: mapping values are not allowed in this context
```

Para fluxos longos, preferir enviar o script remoto para um arquivo temporario no
`dom1` e executa-lo de la:

```bash
scp deploy-remote.sh esteban@dom1:/tmp/deploy-remote.sh
ssh esteban@dom1 'sudo bash /tmp/deploy-remote.sh'
```

Se for inevitavel chamar `incus launch` ou `incus init` num contexto com
`stdin` compartilhado, redirecionar explicitamente:

```bash
incus launch ... </dev/null
```

## Fluxo canonico para um app web novo

### 1. Definir nomes e portas

Antes de tocar no servidor, definir:

```text
DOMAIN=<fqdn publico>
CONTAINER=<nome curto do container>
HOST_PORT=<porta local livre no dom1>
APP_PORT=<porta interna do app no container>
REPO_URL=<repo ou origem do artefato>
```

Regras:

- `HOST_PORT` deve escutar em `127.0.0.1`, nao em `0.0.0.0`.
- `APP_PORT` deve ser interno ao container.
- Nao reutilizar porta sem inventario remoto.
- Segredos ficam em `/etc/<app>.env` ou equivalente no container, nunca no repo.

### Inventario de porta no host

Antes de escolher ou reutilizar `HOST_PORT`, conferir quem ja esta ouvindo nela:

```bash
sudo ss -ltnp | grep ':<PORTA>'
sudo lsof -nP -iTCP:<PORTA> -sTCP:LISTEN
```

Para verificar se a porta pertence a um proxy Incus existente:

```bash
for c in $(sudo incus list --format csv -c n); do
  echo "=== $c ==="
  sudo incus config device show "$c" | grep -B2 -A4 '<PORTA>' || true
done
```

Se o proprio container alvo ja tiver um device `webproxy` escutando naquela
porta, isso nao e conflito: e o comportamento esperado. Confirmar com:

```bash
sudo incus config device show <container>
```

Nao escolher outra porta automaticamente so porque `ss` mostra a porta em uso.
Primeiro identificar o dono e registrar a porta como parte do inventario do
projeto.


### 2. Preparar ou criar o container

No host `dom1`, via aprovacao explicita:

```bash
incus info "$CONTAINER" || incus launch local:<imagem> "$CONTAINER" \
  -c limits.cpu=2 \
  -c limits.memory=4GiB
```

Se a imagem local nao existir, importar uma imagem base suportada pelo projeto
ou abortar e documentar o pre-requisito. Nao puxar uma imagem ou script de outro
produto sem revisar dependencias, usuarios, portas e politica de atualizacao.

Depois de criar o container, nao assumir que `RUNNING` significa rede pronta.
Confirmar IPv4, rota default e resolucao DNS antes de instalar dependencias. No
`dom1`, a bridge gerenciada observada foi `incusbr0`; se a imagem subir sem
DHCP IPv4 aplicado, usar Netplan para `eth0` antes do provisionamento.

### 3. Configurar proxy device Incus

O padrao de publicacao entre host e container:

```bash
incus config device add "$CONTAINER" webproxy proxy \
  listen="tcp:127.0.0.1:$HOST_PORT" \
  connect="tcp:127.0.0.1:$APP_PORT" \
  bind=host
```

Se o device ja existir:

```bash
incus config device set "$CONTAINER" webproxy \
  listen="tcp:127.0.0.1:$HOST_PORT" \
  connect="tcp:127.0.0.1:$APP_PORT"
```

### Diagnostico de rede do container

Um container pode aparecer como `RUNNING` e ainda assim nao ter IPv4. Antes de
culpar DNS, `apt` ou o proxy, conferir:

```bash
sudo incus exec <container> -- ip -br addr
sudo incus exec <container> -- ip route
sudo incus exec <container> -- cat /etc/resolv.conf
sudo incus config show <container> --expanded
sudo incus network show incusbr0
```

O estado saudavel esperado inclui:

```text
eth0    UP    10.114.81.x/24
default via 10.114.81.1 dev eth0
```

Se `eth0` estiver `UP` mas apenas com endereco `fe80::/64`, aplicar DHCP IPv4
via Netplan dentro do container:

```bash
sudo incus exec <container> -- bash -c '
mkdir -p /etc/netplan
cat >/etc/netplan/10-eth0.yaml <<EOF
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
      dhcp6: false
EOF
chmod 600 /etc/netplan/10-eth0.yaml
netplan generate
netplan apply
'
```

Depois validar por camadas:

```bash
sudo incus exec <container> -- ip -br addr
sudo incus exec <container> -- ip route
sudo incus exec <container> -- getent hosts archive.ubuntu.com
sudo incus exec <container> -- curl -I http://archive.ubuntu.com/ubuntu/
```

Somente apos IPv4, rota default, DNS e HTTP funcionarem deve o provisionamento
com `apt-get` continuar.

### 4. Provisionar a aplicacao dentro do container

O conteudo depende do projeto, mas a estrutura esperada e:

```bash
incus exec "$CONTAINER" -- bash -s <<'INNER'
set -Eeuo pipefail

# instalar runtime e dependencias do sistema
# criar usuario de servico
# receber artefato em /opt/<app>; evitar git clone dentro do container
# criar /etc/<app>.env com chmod 600
# criar banco local se aplicavel
# instalar unit systemd do app
# systemctl enable --now <app>.service
# curl -fsS http://127.0.0.1:<APP_PORT>/health
INNER
```

Se o projeto ja tiver script proprio de build/deploy, ele e a fonte de verdade
para compilar, empacotar e ativar a release. Este documento cobre apenas a
camada comum de publicacao no dom1.

### 5. Criar vhost nginx no host dom1

Modelo base:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name exemplo.inovacaosistemas.com.br;

    location /.well-known/acme-challenge/ {
        alias /var/www/html/.well-known/acme-challenge/;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name exemplo.inovacaosistemas.com.br;

    ssl_certificate     /etc/letsencrypt/live/exemplo.inovacaosistemas.com.br/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/exemplo.inovacaosistemas.com.br/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    client_max_body_size 25m;

    location / {
        proxy_pass http://127.0.0.1:<HOST_PORT>;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
}
```

Ativar:

```bash
ln -sfn /etc/nginx/sites-available/<arquivo>.conf /etc/nginx/sites-enabled/<arquivo>.conf
nginx -t
service nginx reload
```

No dom1, preferir `service nginx reload` ou `nginx -s reload`; nao assumir systemd.

### 6. TLS

Ordem recomendada:

1. Reutilizar certificado existente do dominio, se houver.
2. Usar `/etc/letsencrypt/live/<DOMAIN>/...`, se ja existir.
3. Se nao existir, criar vhost HTTP com `/.well-known/acme-challenge/`, emitir com
   `certbot certonly --webroot -w /var/www/html -d <DOMAIN>`, depois ativar HTTPS.

A porta 80 publica usada pelo ACME HTTP-01 deve chegar ao nginx do dom1. Se o
dominio tiver outro roteamento de borda, confirmar a origem antes de emitir ou
renovar certificado.

## Validacao minima

Nao declarar deploy pronto com base em `incus list`, `docker ps` ou unit `active`.
Validar o fluxo por camadas:

```bash
# dentro do container
curl -fsS http://127.0.0.1:<APP_PORT>/health

# no host dom1
curl -fsS http://127.0.0.1:<HOST_PORT>/health
curl -kfsS --resolve '<DOMAIN>:443:127.0.0.1' "https://<DOMAIN>/health"

# fora do host, quando DNS/TLS ja estiverem corretos
curl -fsS "https://<DOMAIN>/health"
```

Para apps com login, banco, jobs ou API, exercitar uma funcao real:

- login com usuario autorizado;
- consulta que toque o banco;
- endpoint principal da API;
- rota publica da tela;
- logs sem erro apos o primeiro acesso;
- artefato publico com marcador da versao nova, quando aplicavel.

Para apps com banco ou filas, validar tambem pelo menos uma acao que leia ou
grave dados reais. Para apps com autenticacao, testar login e uma rota
protegida.

## Rollback

Padrao seguro:

- criar snapshot Incus antes de cutover relevante;
- nao remover stack/volumes antigos ate validacao publica;
- ativar releases por troca atomica de diretorio quando possivel;
- preservar configs e bancos fora da release;
- se health local falhar, voltar a release anterior e reiniciar o servico.

Quando o app usa banco ou armazenamento local, manter volume/diretorio antigo
ate a validacao externa passar. Quando o app usa release atomica, preservar a
versao anterior e restaurar automaticamente se o healthcheck falhar.

## Checklist antes de qualquer execucao real

- [ ] Aprovacao explicita do operador para deploy/reload/restart.
- [ ] Branch correta (`development` ou branch autorizada pelo projeto).
- [ ] Arvore limpa, se o script exigir publicar exatamente o commit atual.
- [ ] DNS definido e apontando para a borda correta.
- [ ] Porta local livre no dom1.
- [ ] Container alvo identificado sem ambiguidade.
- [ ] Segredos resolvidos fora do repo.
- [ ] Plano de execucao lido antes de qualquer escrita remota.
- [ ] Rollback definido antes do cutover.
- [ ] Validacao funcional definida antes do deploy.

## O que nao fazer

- Nao publicar app escutando `0.0.0.0` no host se ele deve ficar atras do nginx.
- Nao editar nginx sem `nginx -t`.
- Nao tratar `systemctl` como certo no dom1; ele historicamente usa SysV para nginx.
- Nao concluir disponibilidade por container `RUNNING`.
- Nao misturar `frida` e `dom1`: a borda publica de alguns fluxos passa pelo dom1.
- Nao generalizar prova de um hostname para outro. Cada dominio precisa da sua
  propria matriz de DNS, vhost, porta, container e validacao publica.
- Nao executar deploy autonomo apos criar ou alterar script.
