# Setup flaresolverr (Windows + Docker)

Flaresolverr é um container Docker que corre um Chromium headless. O
nosso código Python envia pedidos para ele, ele resolve o challenge
Cloudflare da Sofascore automaticamente, e devolve-nos a resposta.

Precisas disto porque:
- Sofascore protege a API com Cloudflare
- Sem flaresolverr, recebemos 403 Forbidden mesmo com curl_cffi
- Com flaresolverr, funciona como se fosses um utilizador normal

## Passo 1: Instalar Docker Desktop (uma única vez)

1. Vai a https://www.docker.com/products/docker-desktop/
2. Descarrega "Docker Desktop for Windows"
3. Instala com defaults (WSL 2 backend; aceita se perguntar)
4. Reinicia o PC se pedir
5. Abre Docker Desktop; espera 1-2 min até aparecer "Engine running" em baixo à esquerda

## Passo 2: Arrancar flaresolverr (uma única vez)

Abre PowerShell (não precisa de administrador):

```powershell
docker run -d --name flaresolverr -p 8191:8191 --restart unless-stopped ghcr.io/flaresolverr/flaresolverr:latest
```

- `-d`: corre em background
- `--name flaresolverr`: nome para parar/arrancar
- `-p 8191:8191`: expõe a porta 8191 (que o Python usa)
- `--restart unless-stopped`: reinicia sozinho se o PC reiniciar

A primeira vez descarrega ~500 MB. Demora 1-3 min conforme internet.

Verifica que está a correr:

```powershell
docker ps
```

Deves ver uma linha com `flaresolverr` e `Up X minutes`.

## Passo 3: Testar

No PowerShell:

```powershell
curl -X POST http://localhost:8191/v1 `
  -H "Content-Type: application/json" `
  -d '{\"cmd\":\"request.get\",\"url\":\"https://api.sofascore.com/api/v1/sport/football/events/live\",\"maxTimeout\":60000}'
```

Deves ver JSON com `"status": "ok"` e um `"solution"` dentro.

Se der erro de conexão: Docker Desktop pode não estar a correr.

## Passo 4: Usar com o scanner

```powershell
cd C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips
venv\Scripts\activate.bat
python scripts\scanner_live_v2.py --ev-minimo 0.05
```

Se vir "Flaresolverr OFF" no output: o container parou.
Para reiniciar: `docker start flaresolverr`

## Comandos úteis

| Comando | O que faz |
|-|-|
| `docker ps` | Lista containers a correr |
| `docker start flaresolverr` | Arranca o container (se parado) |
| `docker stop flaresolverr` | Pára-o (poupa RAM) |
| `docker logs flaresolverr --tail 50` | Vê os ultimos logs |
| `docker restart flaresolverr` | Reinicia (se encrava) |
| `docker rm -f flaresolverr` | Remove-o (depois reinstalas) |

## Recursos consumidos

- RAM: ~400 MB (Chromium headless)
- CPU: baixo em idle; pode subir 10-20% enquanto resolve challenges
- Disco: ~1 GB (image + cache)

Se o PC fica mais lento com Docker, podes parar entre fins-de-semana:

```powershell
docker stop flaresolverr
# ... quando precisares de novo ...
docker start flaresolverr
```

## Falhas comuns

**"Cannot connect to the Docker daemon"**:
Docker Desktop não está aberto. Abre o atalho "Docker Desktop" no menu
Iniciar e espera que arranque.

**"port is already allocated"**:
Já tens outro flaresolverr. `docker rm -f flaresolverr` e volta a correr
o comando do passo 2.

**403 persistente mesmo com flaresolverr**:
Cloudflare afinou. Para a imagem antiga, puxa a nova:
```powershell
docker pull ghcr.io/flaresolverr/flaresolverr:latest
docker rm -f flaresolverr
# volta a correr o comando do passo 2
```

**Sofascore mudou a API**:
Nada que o setup resolva. Nessa altura o código deste repo precisa
de actualizar — queixa-te-me e refino.
