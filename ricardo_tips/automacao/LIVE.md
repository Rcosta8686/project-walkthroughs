# Scanner LIVE — guia pratico

## O que faz

Durante os jogos (in-play), procura o mesmo tipo de divergencia que o
scanner pre-match: **Pinnacle (sharp) subiu/desceu o preço, as softs
ainda não ajustaram**. Essa janela de atraso é onde está o edge.

Diferenças face ao pre-match:

| | Pre-match (sexta/sabado) | Live (durante jogo) |
|-|-|-|
| Quando | 1x por semana | Loop continuo durante jogos |
| Endpoint | `/sports/{sport}/odds` | `/events/{id}/odds` (1x por jogo) |
| Mercados | h2h, totals, spreads | +btts, +double_chance |
| EV minimo | 8% (default config) | 3% (edges menores, afinam rapido) |
| Credits | ~15-30 por puxada | **~10 por jogo por scan** |
| Variancia | Alta (jogo inteiro) | Baixa (minutos restantes) |

## Como usar

Em Portugal/Suíça, à hora dos jogos (ex: sábado 15h, 17h, 20h; domingo
idem; segunda 20h):

1. Dupla-clica em `scanner_live.bat` (ou corre `python scripts/scanner_live.py`)
2. A cada 2 minutos imprime jogos in-play detectados e sugestões novas
3. Se aparecer uma sugestão com EV ≥ 5%, abre a casa (betano_uk, skybet…)
   e verifica se a odd ainda está disponível
4. Se SIM, aposta o teu stake habitual (€0.50)
5. Se SUSPENSO ou odd caiu, ignora e segue para a próxima
6. **Sai com Ctrl+C** no fim dos jogos

## Flags uteis

```cmd
REM Scan menos frequente (poupa credits, menos fresh)
scanner_live.bat --intervalo-s 180

REM EV minimo mais exigente (so sugestoes >5 por cento)
scanner_live.bat --ev-minimo 0.05

REM So mercados confortaveis (sem 1x2, menos variancia)
scanner_live.bat --mercados-scanner golos,btts,dupla,handicap

REM Correr so 10 ciclos e sair sozinho
scanner_live.bat --ciclos 10

REM Combinar: ciclos de 180s, so mercados confortaveis, EV min 5 por cento
scanner_live.bat --intervalo-s 180 --mercados-scanner golos,btts,dupla,handicap --ev-minimo 0.05
```

## Custo em credits

Cálculo: `jogos_in_play × regions × markets` por scan.

Com defaults (eu+uk = 2 regions, 5 markets, intervalo 120s):

| Jogos in-play | Credits/scan | Credits/hora | Credits/dia (6h jogos) |
|-|-|-|-|
| 2 jogos | 20 | 600 | 3.600 |
| 5 jogos | 50 | 1.500 | 9.000 |
| 10 jogos | 100 | 3.000 | 18.000 |

Com o teu plano pago (20.000 credits/mês), um fim-de-semana completo
de scanning (sábado+domingo, ~5h cada) com 5 jogos simultâneos
consome ~15.000 credits. Dá para 1 fim-de-semana por mês de live
intenso + o pre-match normal.

**Para poupar**: `--intervalo-s 300` (5 min) ÷2.5 o custo; `--regions eu`
sozinho corta ao meio.

## O P&L live é diferente

Não existe um `resolver_sugestoes_live.py` ainda — as sugestões live
são **snapshots do momento**, não apostas finais. Se apostares, mete
manualmente no CSV principal (`dados/tracking/sugestoes.csv`) ou corre
o script dessa semana e marca ali.

## Caveats importantes

1. **Softs suspendem mercados** em momentos chave (golo acabado de
   marcar, cartão vermelho, penálti). A sugestão pode aparecer no
   terminal e a casa não aceitar quando lá chegas. É normal — ignora.

2. **Pinnacle move primeiro, softs seguem**. Se o edge é +4% às 20h30,
   provavelmente é +0% às 20h32. Decide rápido ou passa.

3. **Pinnacle margem in-play é maior** (~3-4%, vs ~2% pre-match). A
   prob fair que calculamos é levemente conservadora.

4. **Não aumentes stakes só porque é live**. A variância é menor por
   aposta mas o volume é maior → mesma gestão de bankroll.
