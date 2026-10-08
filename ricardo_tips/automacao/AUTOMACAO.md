# Automação com Windows Task Scheduler

Os ficheiros `.bat` nesta pasta correm o pipeline completo (puxar odds,
scanner, resolver, P&L) e gravam o output em ficheiros `.txt` no teu Desktop.

Para que o Windows corra isto automaticamente nas sextas e segundas de
manhã, segue estes passos **uma única vez**.

---

## Passo 1: Testar que os .bat funcionam manualmente

Antes de agendar, confirma que correm na mão:

1. Vai à pasta `C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips\automacao\`
2. **Clica duas vezes** em `apostas_sexta.bat` — deve abrir uma janela preta por alguns segundos e fechar sozinha
3. Vai ao teu **Desktop** — deve ter um ficheiro novo chamado `apostas_sugestoes.txt`
4. Abre-o — deves ver o output completo (sugestões para o fim-de-semana)

Se funcionar, continua. Se der erro, cola o output que aparece na janela preta.

---

## Passo 2: Configurar o Task Scheduler

**Para a corrida de SEXTA (sugestões):**

1. Pressiona `Windows + R` → escreve `taskschd.msc` → Enter
2. No painel direito, clica em **"Create Basic Task..."** (Criar Tarefa Básica)
3. **Name**: `Ricardo Tips - Sugestoes Sexta`
4. **Description**: `Puxa sugestoes de apostas para o fim-de-semana`
5. Clica **Next**
6. **Trigger**: escolhe **Weekly** (Semanalmente) → Next
7. **Start**: hoje às `10:00:00`
8. **Recur every**: `1 week`
9. **Marca a caixa SEXTA** (Friday) → Next
10. **Action**: `Start a program` → Next
11. **Program/script**: `C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips\automacao\apostas_sexta.bat`
    - (Podes usar o botão **Browse** para encontrar)
12. **Start in (optional)**: `C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips`
13. Clica **Next** → **Finish**

**Para a corrida de SEGUNDA (resolver + P&L):**

Repete os passos acima, mas:
- **Name**: `Ricardo Tips - PnL Segunda`
- **Trigger weekly**: marca **SEGUNDA (Monday)**
- **Start time**: `10:00:00`
- **Program**: `C:\Users\erica\Documents\GitHub\project-walkthroughs\ricardo_tips\automacao\apostas_segunda.bat`

---

## Passo 3: Confirmar que está activo

1. No Task Scheduler, no painel esquerdo, clica em **"Task Scheduler Library"**
2. Deves ver as duas tarefas: `Ricardo Tips - Sugestoes Sexta` e `Ricardo Tips - PnL Segunda`
3. Clica com direito → `Run` em cada uma para testar imediatamente
4. Verifica se o Desktop fica com os ficheiros `.txt`

---

## Como usar na prática

**Sexta às ~10h**: abres o `apostas_sugestoes.txt` do Desktop e vês a lista.
Se decidires apostar, vais às casas de apostas e apostas €0.50 nas sugestões
"toleráveis". O CSV guarda tudo automaticamente.

**Segunda às ~10h**: abres `apostas_pnl.txt` do Desktop e vês quais ganhaste,
ROI acumulado, breakdown por casa/liga.

**O computador tem que estar ligado** à hora agendada. Se estiver desligado,
o Task Scheduler NÃO corre. Alternativas:
- Deixa o PC ligado (a tarefa consome <5 segundos)
- Marca a caixa **"Run task as soon as possible after a scheduled start is missed"**
  nas propriedades da tarefa (edita a tarefa → aba Settings)

---

## Problemas comuns

**O .bat abre e fecha em meio segundo sem output no Desktop**:
- O venv pode não existir. Confirma que `venv\Scripts\activate.bat` existe
  dentro da pasta `ricardo_tips`.

**"git pull" falha**:
- Normal se o diretório não é git repo. Comenta a linha `git pull >> ...`
  nos dois .bat se tiver a dar problemas.

**Odds API diz "no credits"**:
- Credits do mês esgotaram. Resetam dia 1 do próximo mês.
