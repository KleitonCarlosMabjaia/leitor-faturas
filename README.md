# Leitor de Faturas

[![Testes](https://github.com/KleitonCarlosMabjaia/leitor-faturas/actions/workflows/testes.yml/badge.svg)](https://github.com/KleitonCarlosMabjaia/leitor-faturas/actions/workflows/testes.yml)

Aplicação web para pequenas empresas portuguesas (restaurantes, oficinas, lojas, escritórios de contabilidade), com **acesso por convite e dados separados por empresa**, que **lê faturas de fornecedores em PDF**, extrai os dados automaticamente, **contabiliza-as segundo o SNC** (partidas dobradas, IVA por taxa, art. 21.º do CIVA) e mostra gastos, balancete, apuramento do IVA e prazos fiscais, com um **assistente de IA** que consulta a contabilidade e propõe lançamentos.

A leitura funciona em cascata, da fonte mais barata para a mais cara: primeiro o **código QR da AT** (gratuito e exato), depois uma **cache de fornecedores** por NIF e, só no fim, **IA** (API do Claude) para o que falta.

**[▶ Ver a demonstração](https://leitor-faturas-vwgk.onrender.com)** — sem login, com 30 faturas fictícias e a respetiva contabilidade. O servidor gratuito adormece quando não há visitas: a primeira abertura pode demorar cerca de 1 minuto.

| Lista de faturas | Revisão | Dashboard (modo escuro) |
| --- | --- | --- |
| ![Lista de faturas com pesquisa, filtros por estado e etiquetas](docs/capturas/lista.png) | ![PDF da fatura ao lado dos campos editáveis](docs/capturas/revisao.png) | ![Indicadores, gasto por fornecedor e evolução mensal em modo escuro](docs/capturas/dashboard.png) |
| **Contabilidade** | **Por contabilizar** | **Balancete (modo escuro)** |
| ![Resumo da contabilidade: indicadores, verificações automáticas com IVA não dedutível e tributação autónoma, e próximos prazos fiscais](docs/capturas/contabilidade.png) | ![Fatura com a conta SNC e o IVA sugeridos, classificação memorizada e o lançamento proposto em débito e crédito](docs/capturas/por-contabilizar.png) | ![Balancete analítico com débitos, créditos e saldos por conta, equilibrado, em modo escuro](docs/capturas/balancete.png) |
| **Reconciliação bancária** | **Mapa de reconciliação (modo escuro)** | **SAF-T (PT) da contabilidade** |
| ![Importação de extratos e movimentos do banco por reconciliar, cada um com a sugestão de associar a um lançamento ou lançar numa conta](docs/capturas/bancos.png) | ![Mapa de reconciliação bancária com a diferença entre o saldo do banco e o da contabilidade toda explicada, em modo escuro](docs/capturas/reconciliacao.png) | ![Exportação do SAF-T da contabilidade pronta a descarregar, com a configuração do NIF e da morada da sede](docs/capturas/saft.png) |

<sub>Capturas com os dados de demonstração (fornecedores e NIFs fictícios).</sub>

## Funcionalidades

**Leitura das faturas**
- Carregamento de PDFs por arrastar e largar (até 10 MB, vários de uma vez).
- Leitura do **código QR da AT** (Portaria n.º 195/2020): NIF, data, número, ATCUD, IVA por taxa (Continente, Açores e Madeira) e total. Funciona também em faturas digitalizadas.
- **Cache de fornecedores:** depois de confirmar uma fatura de um fornecedor, as faturas seguintes desse NIF ficam lidas só com o QR, sem custos de IA.
- **IA como último recurso:** o Claude lê o nome do fornecedor, o vencimento ou a fatura inteira quando não há QR. A resposta vem em formato estruturado, é validada com Pydantic e repetida uma vez se vier inválida.
- Texto extraído com pypdf, com pdfplumber como alternativa quando o texto vem mal formatado.

**Verificações**
- Validação do **NIF português** (prefixo atribuído e dígito de controlo).
- Alerta quando **valor sem IVA + IVA ≠ total** (tolerância de 0,02 €).
- **Deteção de duplicados** pelo NIF e número da fatura, ou pelo ATCUD.

**Revisão e gestão**
- Página de revisão com **o PDF ao lado dos campos editáveis** (incluindo as linhas de IVA), soma conferida em tempo real e botão "Confirmar dados".
- Estados "por rever" e "confirmada", marcação de faturas pagas.
- Filtros por fornecedor, mês e estado.

**Dashboard e exportação**
- Total gasto no mês, IVA suportado no mês e acumulado no ano, faturas por pagar (com as vencidas em destaque).
- Gráficos de **gasto por fornecedor** e de **evolução mensal** (Chart.js), com tabela alternativa para leitores de ecrã.
- **Exportação para Excel e CSV** com os filtros ativos: colunas de base e IVA por taxa, linha de totais e proteção contra *CSV injection*.

**Contabilidade (SNC)**
- **Plano de contas SNC** criado para cada empresa, com as subcontas de IVA dedutível e liquidado **divididas por taxa**, já alinhadas com os campos da declaração periódica. Subconta automática para cada fornecedor (2211xxxx; 2711xxxx nos fornecedores de investimentos).
- **Lançamentos em partidas dobradas**, numerados por diário e ano (COM, VEN, BAN, CX, OD). Nunca se apagam nem se alteram: anulam-se com **estorno**. Lançamentos desequilibrados, em contas agregadoras ou em **períodos fechados** são recusados.
- **Contabilização das faturas num clique, ou em lote.** A conta é sugerida em cascata: o que a aplicação **aprendeu** com esse fornecedor → palavras-chave no nome → **IA**, que lê a fatura e o plano de contas. A escolha fica memorizada por NIF.
- **IVA não dedutível automático (art. 21.º do CIVA):** gasóleo a 50%, gasolina, viaturas ligeiras, refeições e deslocações. A parte não dedutível vai para o gasto e fica explicada no lançamento. As notas de crédito invertem o lançamento.
- Marcar uma fatura como paga gera o **pagamento no banco**; desmarcar estorna-o. Uma fatura contabilizada fica protegida contra alterações.
- **Apuramento do IVA num clique** (2432/2433/2434 → 2435 → 2436 a pagar ou 2437 a recuperar, com reporte do crédito anterior) e **mapa de apoio à declaração periódica** (campos 1 a 6 e 20 a 24).
- **Relatórios:** balancete analítico (com exportação para Excel), extratos de conta com saldo linha a linha, diário com pesquisa, demonstração de resultados por naturezas e **contas correntes de fornecedores com antiguidade de saldos**.
- **Verificações automáticas** do dia a dia: faturas por contabilizar, IVA do período por apurar, caixa com saldo credor, fornecedores com saldo devedor, NIF inválido, **tributação autónoma estimada** das despesas de representação (art. 88.º do CIRC), retenção na fonte a pessoas singulares e faturas sem ATCUD.
- **Vendas (faturas de clientes)** importadas do **SAF-T de faturação** do programa certificado (faturas, faturas simplificadas, faturas-recibo, notas de crédito e de débito, documentos anulados e recibos) ou registadas à mão (por exemplo, faturas do Portal das Finanças). Ficam contabilizadas no diário de vendas: cliente a débito; vendas (711), serviços (721) e IVA liquidado por taxa a crédito. Recebimentos totais ou parciais, **contas correntes de clientes com antiguidade**, alerta de vendas vencidas e, na reconciliação bancária, a sugestão de **receber a fatura** quando o dinheiro entra no banco. A aplicação **não emite** faturas: isso exige um programa certificado pela AT (Portaria n.º 363/2010).
- **Inventários** pelo sistema de inventário periódico: contagens físicas importadas de CSV ou Excel (código, descrição, categoria, quantidade e custo) ou escritas à mão, inventário de abertura e **apuramento do CMVMC** (inventário inicial + compras líquidas − inventário final) para mercadorias e matérias-primas. As compras (31) passam para inventários (321/331) e o consumo para o custo das vendas (611/612), com a conta mostrada antes de lançar. Os inventários lançam-se por ordem e ficam protegidos depois de lançados.
- **Ativos fixos e depreciações**: ficha de cada ativo (conta, data de entrada em funcionamento, valor, valor residual e taxa, com taxas de referência do Decreto Regulamentar n.º 25/2009), **depreciações por quotas constantes em duodécimos** lançadas de uma vez até ao mês escolhido (642x/438x), sem repetir meses nem passar do valor depreciável, **abate** e **venda** (com IVA e ganho ou perda), mapa de depreciações do exercício e alertas de depreciações em atraso ou de ficha diferente da contabilidade.
- **Fecho do exercício**: lista de verificação antes de fechar (faturas e vendas por contabilizar, depreciações e inventário de 31/12, IVA apurado, bancos reconciliados), **estimativa de IRC** com parâmetros editáveis (taxa normal, taxa das PME nos primeiros 50 000 €, derrama, tributação autónoma e ajustes do quadro 07), **apuramento de resultados** a 31/12 (classes 6 e 7 para a 811, imposto na 8121/241 e resultado líquido na 818, com o tipo A no SAF-T), transferência do resultado para resultados transitados (561) no ano seguinte e reabertura do exercício.
- **Salários**: ficha de cada pessoa (pessoal ou órgãos sociais, NIF, NISS, vencimento, subsídio de alimentação em cartão ou numerário, taxa de retenção de IRS, taxas da Segurança Social e subsídios em duodécimos), **processamento mensal** com pré-visualização dos recibos e variáveis do mês (dias, prémios, faltas), subsídios de férias e de Natal, excesso do subsídio de alimentação sobre o limite isento sujeito a IRS e Segurança Social, **lançamento automático** (631/632 e 635 contra 231x, 2421 e 245), pagamentos dos salários, da Segurança Social e do IRS retido (com os prazos do dia 20), mapa anual para a DMR e a declaração à Segurança Social, alertas de meses por processar e pagamentos em atraso, e **acréscimo de férias e subsídio de férias** a 31/12.
- **Acréscimos e diferimentos**: diferimentos de gastos e rendimentos (281/282) repartidos pelos meses, com o reconhecimento de todos de uma vez e os cêntimos no último mês; acréscimos de gastos e rendimentos (2722/2721) com **reversão automática** no dia seguinte (estornar o acréscimo estorna a reversão). Ao contabilizar uma fatura de seguros, rendas ou assinaturas, a aplicação **propõe o diferimento**: lê o período de cobertura no texto da fatura (ou com a IA) ou assume 12 meses nos seguros, e o diferimento fica ligado à fatura. Os seguros contabilizados em lote sem diferimento aparecem numa lista para diferir.
- **Balanço** (demonstração da posição financeira, modelo do SNC simplificado) a qualquer data, com as contas de terceiros separadas pelo saldo de cada subconta e o resultado do período e os resultados transitados calculados.
- **Saldos de abertura** para empresas que vêm de outro programa: importação do balancete de fecho em CSV ou Excel (as linhas das contas agregadoras são ignoradas e as subcontas de terceiros em falta são criadas).
- **IVA autoliquidado**: serviços de fornecedores de outros Estados-membros (sugerido pelo número de IVA estrangeiro), aquisições intracomunitárias de bens e inversão do sujeito passivo (construção civil), com os campos 12/13, 16/17 e o quadro 06-A no mapa da declaração periódica.
- **Comunicação do inventário à AT**: ficheiro CSV da Portaria n.º 2/2015 gerado a partir da contagem.
- **Reconciliação bancária com importação de extratos** em CSV, Excel ou OFX, como os bancos os exportam: colunas, separadores, codificação, números ("1.234,56") e datas reconhecidos sozinhos, e a IA identifica as colunas de formatos desconhecidos. Reimportar o mesmo extrato não duplica movimentos. Para cada movimento há sugestões: **associar** a um lançamento com o mesmo valor e data próxima, **pagar a fatura** em aberto com esse total (o pagamento fica com a data do banco) ou **lançar** numa conta (comissões, Imposto do Selo, Segurança Social...), com a escolha **memorizada** para o mesmo tipo de movimento nos meses seguintes. A **reconciliação automática** só aplica o que não tem dúvidas, e o **mapa de reconciliação** explica a diferença entre o saldo do banco e o da contabilidade.
- **SAF-T (PT) da contabilidade** (versão 1.04_01, Portaria n.º 302/2016): plano de contas com saldos de abertura e de fecho, categorias GR/GA/GM e **códigos da taxonomia S**, fornecedores e todos os lançamentos do exercício. Antes de gerar, a aplicação diz o que falta (NIF, morada, contas sem taxonomia). O ficheiro é validado nos testes contra o **XSD oficial da AT**.
- **Calendário fiscal** (IVA mensal ou trimestral, SAF-T, DMR, retenções, Segurança Social, Modelo 22, IES, pagamentos por conta) com os prazos **ajustados a fins de semana e feriados**, incluindo a Páscoa e o Corpo de Deus.
- **Assistente com IA:** responde a perguntas ("quanto IVA vou pagar?", "a quem devo e há quanto tempo?") consultando o balancete, os extratos e o IVA através de ferramentas só de leitura, e **propõe lançamentos** que só ficam gravados depois de o utilizador carregar em "Lançar".

**Contas e várias empresas**
- Acesso **só por convite**, sem registo público. Cada empresa cliente vê apenas os seus dados.
- Palavras-passe com **Argon2**, sessão em cookie `HttpOnly` + `SameSite`, bloqueio depois de 5 tentativas falhadas.
- Papéis **administrador** (gere a equipa) e **membro**. Desativar uma conta corta o acesso de imediato.
- Links de convite de uso único, válidos 72 horas, com o token fora dos registos do servidor.

**Pronta para produção**
- **PostgreSQL** em produção (SQLite em desenvolvimento), com migrações **Alembic** aplicadas ao arrancar.
- PDFs num bucket privado **Cloudflare R2** (compatível com S3), numa pasta por empresa.
- **Cópia de segurança diária** da base de dados, num formato que se restaura em SQLite ou PostgreSQL.
- **Limite de pedidos** no login (por IP) e no envio de faturas (por utilizador).
- Registo de erros no **Sentry** (opcional), sem dados pessoais nem dados das faturas.

**Qualidade**
- Interface em português de Portugal, paleta grafite e âmbar, modo claro e escuro (automático ou escolhido no menu), adaptada a telemóvel.
- Pesquisa instantânea, filtros por estado com contadores, ordenação por coluna, fila de envio com o resultado de cada PDF e notificações.
- Revisão rápida: atalhos **Ctrl+S** (guardar) e **Ctrl+Enter** (confirmar), aviso de alterações por guardar, erros junto a cada campo e botão "Próxima por rever".
- 211 testes automáticos (pytest), incluindo isolamento entre empresas, migrações, as regras contabilísticas e a validação do SAF-T contra o XSD oficial e a leitura de extratos de vários formatos. Correm sem Internet e sem chave da API.

## Como funciona

```
PDF ──► QR da AT? ──sim──► fornecedor conhecido (cache)? ──sim──► lida, sem IA
            │                        │
            não                      não
            ▼                        ▼
      texto do PDF ──► IA (Claude) ──► combina: valores do QR têm prioridade
                                             │
                                             ▼
                     verificações (NIF, totais, duplicados) ──► base de dados ──► revisão
```

## Tecnologias

| Área | Tecnologia |
| --- | --- |
| Backend | Python 3.11+, FastAPI, Uvicorn, Pydantic |
| Segurança | Argon2 (argon2-cffi), sessões no servidor, cookies HttpOnly |
| Base de dados | SQLAlchemy 2.0; SQLite (desenvolvimento) e PostgreSQL com psycopg 3 (produção); Alembic |
| Ficheiros | Pasta local ou Cloudflare R2 / S3 (boto3) |
| PDF e QR | pypdf, pdfplumber, pypdfium2, zxing-cpp |
| IA | SDK oficial `anthropic` (modelo configurável) |
| Exportação | openpyxl |
| Frontend | HTML, CSS e JavaScript, sem frameworks; Chart.js; ícones SVG próprios |
| Testes | pytest, Playwright, ruff, GitHub Actions |
| Publicação | Docker, Render (web + tarefa agendada), Sentry |

## Como executar

1. Criar e ativar um ambiente virtual:

   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # macOS / Linux
   source .venv/bin/activate
   ```

2. Instalar as dependências:

   ```bash
   pip install -r requirements.txt
   ```

3. Copiar `.env.example` para `.env` e preencher `ANTHROPIC_API_KEY`. Sem chave, a aplicação funciona na mesma: lê o QR, mas não usa a IA.

4. Criar a primeira empresa e o convite do seu administrador:

   ```bash
   python -m app.admin criar-empresa --nome "A Minha Empresa, Lda." --admin-nome "Ana Silva" --admin-email ana@exemplo.pt
   ```

   O comando imprime um link. Abra-o para definir a palavra-passe.

5. (Opcional) Criar a empresa de demonstração, com 30 faturas fictícias de um restaurante:

   ```bash
   python -m app.demo
   ```

6. Arrancar o servidor e abrir <http://127.0.0.1:8000>:

   ```bash
   uvicorn app.main:app --reload
   ```

A documentação automática da API fica em <http://127.0.0.1:8000/docs>.

## Contas e administração

Não há registo público: és tu (o operador) quem cria as empresas clientes, na linha de comandos. Depois disso, o administrador de cada empresa convida os colegas na página **Equipa**.

| Comando | O que faz |
| --- | --- |
| `python -m app.admin criar-empresa --nome ... --admin-nome ... --admin-email ...` | Cria a empresa e mostra o link de convite do administrador |
| `python -m app.admin listar` | Lista as empresas, os utilizadores e o número de faturas |
| `python -m app.admin novo-link --email ...` | Novo link para definir a palavra-passe (por exemplo, se o administrador a esqueceu) |
| `python -m app.admin desativar-empresa --id N` | Corta o acesso de uma empresa sem apagar os dados (`ativar-empresa` repõe-no) |

Os links de convite são de uso único e expiram em 72 horas. Ainda não há envio de emails: o link é mostrado para ser enviado por um canal privado.

Faturas criadas antes das contas existirem passam para a primeira empresa que for criada.

## Dados de demonstração

`python -m app.demo` cria uma empresa própria ("... (demonstração)"), separada das empresas reais, com 30 faturas de 7 fornecedores fictícios (bebidas, carne, peixe e legumes), espalhadas pelos últimos 6 meses. Algumas ficam por rever, algumas por pagar e duas vencidas. Tem também um extrato bancário dos últimos dois meses por reconciliar (com comissões, Imposto do Selo e o pagamento de uma fatura vencida) e uma contabilidade completa: capital inicial, vendas mensais a 13% e 23%, rendas, salários, gasóleo (IVA a 50%), despesas de representação, as faturas contabilizadas (menos três, para haver trabalho pendente) e o apuramento e pagamento do IVA de cada trimestre terminado. Cada fatura tem um PDF gerado na hora, com texto e um QR da AT válido, por isso os PDFs (em `uploads/`) também servem para testar o carregamento.

- O comando mostra um link para definir a palavra-passe do utilizador `demo@demonstracao.pt`.
- `python -m app.demo --limpar` apaga **só** a empresa de demonstração e cria-a de novo.

Todos os nomes, moradas e NIFs são inventados.

## Testes

```bash
pip install -r requirements-dev.txt
playwright install chromium        # só da primeira vez

ruff check .                       # estilo e erros comuns
pytest                             # servidor: 211 testes, sem navegador
pytest tests_ui                    # interface: 50 testes num Chromium a sério (Playwright)
```

Nos testes do servidor, cada teste usa uma base de dados temporária, a IA nunca é chamada (mesmo que o `.env` tenha uma chave) e é substituída por uma função falsa e o bucket S3 por um objeto falso.

Os testes da interface arrancam a aplicação em modo de demonstração (base de dados e pasta temporárias) e verificam, num navegador: filtros, pesquisa, ordenação, a janela de confirmação ao apagar, o tema escuro, os atalhos e os erros na revisão, os gráficos, a contabilização de uma fatura, a recusa de um lançamento desequilibrado, a exportação do SAF-T, a reconciliação bancária, o registo e o recebimento de vendas, a importação de inventários, o registo de ativos e as depreciações, o fecho do exercício, o processamento e o pagamento de salários, os acréscimos e o balanço, a ausência de erros de JavaScript e que nenhuma página tem deslocamento horizontal num telemóvel.

O **GitHub Actions** corre tudo em cada push (`.github/workflows/testes.yml`), incluindo o teste de PostgreSQL com uma base de dados verdadeira.

Há também um teste com PostgreSQL verdadeiro (migrações e restauro de uma cópia feita em SQLite). Só corre se houver uma base de dados de teste, que é **apagada** pelo teste:

```bash
# PowerShell: $env:TESTE_POSTGRES_URL="postgresql://utilizador:palavra-passe@localhost/teste"
TESTE_POSTGRES_URL=postgresql://utilizador:palavra-passe@localhost/teste pytest -k postgresql
```

## Publicar a demonstração (Render, gratuito)

O repositório inclui um `Dockerfile` e um `render.yaml`.

1. Pôr o projeto no GitHub (o `.gitignore` já exclui o `.env`, a base de dados e as faturas).
2. Em <https://render.com>, criar uma conta e escolher **New → Blueprint**, com o repositório.
3. O Render lê o `render.yaml`, constrói a imagem e publica-a num endereço como <https://leitor-faturas-vwgk.onrender.com> (a demonstração deste projeto).

A versão publicada corre em **modo de demonstração** (`MODO_DEMO=1`): é aberta a todos, sem login, entra diretamente na empresa de demonstração e tem a **IA desligada**, para que os visitantes não gastem créditos da API. O QR continua a ser lido e a gestão da equipa está desligada.

Para clientes reais, publica-se **outro serviço**, sem `MODO_DEMO` (ver "Produção para clientes").

No plano gratuito, o servidor adormece ao fim de 15 minutos sem visitas (o primeiro acesso demora cerca de 1 minuto) e o disco não é permanente: a cada novo arranque, os dados voltam às faturas de demonstração. Para uma demonstração isto é uma vantagem, porque fica sempre limpa.

Para testar a imagem localmente:

```bash
docker build -t leitor-faturas .
docker run -p 8000:8000 -e MODO_DEMO=1 leitor-faturas
```

## Produção para clientes

A demonstração e a versão dos clientes são **serviços separados**, a partir do mesmo código. A dos clientes guarda tudo fora do contentor, porque o disco dos contentores apaga-se a cada nova versão:

| Peça | Serviço sugerido | Variáveis |
| --- | --- | --- |
| Aplicação | Render, plano pago (não adormece e tem Shell) | `URL_PUBLICO`, `AMBIENTE=producao` |
| Base de dados | PostgreSQL no Neon ou no Render | `URL_BASE_DADOS` (ou `DATABASE_URL`) |
| PDFs e cópias | Bucket privado no Cloudflare R2 | `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` |
| Cópia diária | Tarefa agendada do Render (`python -m app.copias criar`) | as mesmas da base de dados e do bucket |
| Erros | Sentry (opcional) | `SENTRY_DSN` |
| SAF-T | NIF e nome de quem produz o programa (opcional; sem eles, usam-se os da empresa) | `SAFT_NIF_PRODUTOR`, `SAFT_NOME_PRODUTOR` |

O `render.yaml` já descreve os três serviços. Os segredos ficam marcados com `sync: false`: o Render pede-os no painel e nunca ficam no repositório.

**Passos:**

1. Criar a base de dados PostgreSQL e copiar o endereço de ligação (com `?sslmode=require`).
2. No Cloudflare: **R2 → Create bucket** (privado), e depois **Manage API tokens** com permissão de leitura e escrita só nesse bucket.
3. No Render, **New → Blueprint** com o repositório e preencher as variáveis pedidas.
4. Ao arrancar, a aplicação cria as tabelas sozinha (`alembic upgrade head`).
5. Na **Shell** do serviço, criar o primeiro cliente: `python -m app.admin criar-empresa ...`

**Cópias de segurança:**

```bash
python -m app.copias criar                     # cópia agora (no bucket, em copias/)
python -m app.copias listar
python -m app.copias restaurar --chave copias/2026-11-11_030000.json.gz --substituir
```

A cópia é um JSON comprimido, independente do motor: uma cópia de SQLite restaura-se em PostgreSQL. As últimas 30 cópias são mantidas (`COPIAS_A_MANTER`). Os PDFs não entram na cópia porque ficam no bucket.

**Migrações**, depois de alterar `app/modelos/tabelas.py`:

```bash
alembic revision --autogenerate -m "o que mudou"   # rever o ficheiro gerado em migracoes/versions/
alembic upgrade head                                # ou simplesmente arrancar a aplicação
```

Um teste confirma que as migrações e os modelos não divergem.

## API

| Método | Endereço | Descrição |
| --- | --- | --- |
| GET | `/api/saude` | Estado do servidor e da base de dados (503 se não responder), IA e modo de demonstração. A única rota pública |
| POST | `/api/auth/entrar` | Inicia sessão (`email`, `palavra_passe`). 429 depois de muitas tentativas do mesmo IP |
| POST | `/api/auth/sair` | Termina a sessão |
| GET | `/api/auth/eu` | Utilizador e empresa da sessão |
| POST | `/api/auth/definir-palavra-passe` | Aceita um convite (`token`, `palavra_passe`) e inicia sessão |
| GET / POST | `/api/equipa` | Lista a equipa / convida alguém (só administradores) |
| POST | `/api/equipa/{id}/link` | Novo link de convite ou de reposição |
| PATCH | `/api/equipa/{id}` | Ativa, desativa ou muda o papel |
| POST | `/api/faturas` | Carrega um PDF (campo `ficheiro`) e extrai os dados. 409 se for repetida |
| GET | `/api/faturas` | Lista as faturas. Filtros: `fornecedor_id`, `mes` (AAAA-MM), `estado` |
| GET | `/api/faturas/exportar` | Exporta para `formato=xlsx` ou `csv`, com os mesmos filtros |
| GET | `/api/faturas/{id}` | Dados de uma fatura |
| GET | `/api/faturas/{id}/pdf` | PDF original |
| PUT | `/api/faturas/{id}` | Edita os dados; com `"confirmar": true` marca-a como confirmada |
| PATCH | `/api/faturas/{id}/pagamento` | Marca como paga ou por pagar (`{"paga": true}`) |
| POST | `/api/faturas/{id}/extrair` | Volta a ler a fatura |
| DELETE | `/api/faturas/{id}` | Apaga a fatura e o PDF |
| GET | `/api/fornecedores` | Fornecedores conhecidos |
| GET | `/api/dashboard` | Números do dashboard (`mes` opcional) |
| GET | `/api/contabilidade/resumo` | Indicadores, verificações automáticas e próximos prazos |
| GET | `/api/contabilidade/por-contabilizar` | Faturas confirmadas por lançar, com a classificação e o lançamento propostos |
| GET | `/api/contabilidade/faturas/{id}` | Lançamentos da fatura ou proposta (`ia=1` pede a sugestão à IA) |
| POST | `/api/contabilidade/faturas/{id}/contabilizar` | Lança a fatura (`conta_codigo`, `perfil_iva`, `regime_iva`, `taxa_autoliquidacao` e `diferimento` opcionais) |
| POST | `/api/contabilidade/contabilizar-lote` | Lança várias faturas com a sugestão (`ids`) |
| POST | `/api/contabilidade/faturas/{id}/pagamento` | Regista o pagamento (`data`, `conta` 11/12/13) |
| GET / POST | `/api/contabilidade/lancamentos` | Diário (filtros `de`, `ate`, `diario`, `pesquisa`) / lançamento manual |
| POST | `/api/contabilidade/lancamentos/{id}/estornar` | Estorna um lançamento |
| GET | `/api/contabilidade/balancete` | Balancete analítico (`/balancete/exportar` para Excel) |
| GET | `/api/contabilidade/extrato` | Extrato de uma conta e subcontas (`conta`, `de`, `ate`) |
| GET | `/api/contabilidade/iva` | Mapa da declaração periódica; `POST /api/contabilidade/iva/apurar` gera o apuramento |
| GET | `/api/contabilidade/resultados` | Demonstração de resultados por naturezas |
| GET | `/api/contabilidade/fornecedores` | Contas correntes e antiguidade de saldos |
| GET | `/api/contabilidade/obrigacoes` | Calendário fiscal |
| GET / POST | `/api/contabilidade/contas` | Plano de contas / nova subconta |
| PUT | `/api/contabilidade/configuracao` | Regime do IVA, período fechado, NIF e morada da sede (administradores) |
| GET / POST | `/api/contabilidade/vendas` | Vendas do período (filtros `estado`, `pesquisa`) / registar uma venda |
| POST | `/api/contabilidade/vendas/importar-saft` | Importa o SAF-T de faturação (faturas, NC, anulados e recibos) |
| GET | `/api/contabilidade/vendas/{id}` | Uma venda e os seus lançamentos |
| POST | `/api/contabilidade/vendas/{id}/contabilizar` · `/receber` | Contabiliza / regista um recebimento |
| GET | `/api/contabilidade/vendas/clientes` | Contas correntes de clientes e antiguidade |
| GET / POST | `/api/contabilidade/inventarios` | Contagens de inventário / nova contagem |
| POST | `/api/contabilidade/inventarios/importar` | Contagem a partir de CSV ou Excel (`ficheiro`, `data`, `categoria`) |
| GET / PUT / DELETE | `/api/contabilidade/inventarios/{id}` | Contagem com a pré-visualização do CMVMC / alterar / apagar |
| POST | `/api/contabilidade/inventarios/{id}/apurar` · `/inicial` | Lança o apuramento do CMVMC / o inventário de abertura |
| GET / POST | `/api/contabilidade/ativos` | Ativos com o mapa de depreciações (`ano`) / registar um ativo |
| POST | `/api/contabilidade/ativos/depreciar` | Lança as depreciações de todos os ativos até ao fim do mês de `ate` |
| POST | `/api/contabilidade/ativos/{id}/saida` | Abate (sem `valor_venda`) ou venda do ativo |
| GET / POST | `/api/contabilidade/fecho` | Verificações e estimativa do IRC (`ano` e parâmetros) / fechar o exercício |
| POST | `/api/contabilidade/fecho/transferir` · `/reabrir` | Resultado para resultados transitados / reabrir o exercício |
| GET | `/api/contabilidade/inventarios/{id}/at` | Ficheiro CSV da comunicação do inventário à AT |
| GET | `/api/contabilidade/salarios` | Pessoas, mapa anual, meses por processar e pagamentos em atraso (`ano`) |
| POST / PUT / DELETE | `/api/contabilidade/salarios/funcionarios[/{id}]` | Registar / alterar / apagar a ficha de uma pessoa |
| GET | `/api/contabilidade/salarios/{ano}/{mes}` | Processamento do mês ou pré-visualização dos recibos |
| POST | `/api/contabilidade/salarios/simular` · `/processar` | Recibos com as variáveis do mês / processar e lançar |
| POST | `/api/contabilidade/salarios/{ano}/{mes}/pagar` | Pagamento dos salários, da Segurança Social ou do IRS retido |
| POST | `/api/contabilidade/faturas/{id}/diferir` | Diferimento de uma fatura já contabilizada (`inicio`, `meses`) |
| GET / POST | `/api/contabilidade/salarios/ferias` | Estimativa / lançamento do acréscimo de férias a 31/12 (`ano`) |
| GET | `/api/contabilidade/periodizacao` | Diferimentos, acréscimos e o que está por reconhecer |
| POST | `/api/contabilidade/periodizacao/diferimentos` · `/reconhecer` · `/acrescimos` | Registar um diferimento / reconhecer até `ate` / lançar um acréscimo com reversão |
| GET | `/api/contabilidade/balanco` | Balanço a uma data (`ate`) |
| GET / POST | `/api/contabilidade/abertura` | Saldos de abertura lançados / lançar (`/abertura/importar` aceita CSV ou Excel) |
| GET | `/api/contabilidade/banco/contas` | Contas de bancos e caixa, saldo e movimentos por reconciliar |
| POST | `/api/contabilidade/banco/importar` | Importa um extrato (`ficheiro`, `conta`, `saldo_final` opcional) |
| GET | `/api/contabilidade/banco/movimentos` | Movimentos do extrato, com as sugestões de reconciliação |
| POST | `/api/contabilidade/banco/movimentos/{id}/associar` | Liga o movimento a um lançamento (`linha_id`) |
| POST | `/api/contabilidade/banco/movimentos/{id}/pagar-fatura` | Regista o pagamento da fatura e reconcilia |
| POST | `/api/contabilidade/banco/movimentos/{id}/lancar` | Lança o movimento numa conta (`conta`) e memoriza |
| POST | `/api/contabilidade/banco/movimentos/{id}/ignorar` · `/desfazer` | Ignora / volta a pôr por reconciliar |
| POST | `/api/contabilidade/banco/reconciliar-automaticamente` | Aplica só as sugestões seguras (`conta`) |
| GET | `/api/contabilidade/banco/reconciliacao` | Mapa de reconciliação bancária |
| GET / DELETE | `/api/contabilidade/banco/extratos` | Extratos importados / apagar uma importação |
| GET | `/api/contabilidade/saft/verificar` | O que falta para gerar o SAF-T (`ano`, ou `de` e `ate`) |
| GET | `/api/contabilidade/saft` | Ficheiro SAF-T (PT) 1.04_01 da contabilidade (`ano`, ou `de` e `ate`) |
| POST | `/api/contabilidade/assistente` | Pergunta ao assistente com IA (`pergunta`, `historico`) |

## Estrutura

```
leitor-faturas/
├── app/
│   ├── main.py              # cria a aplicação, liga as rotas, arranque
│   ├── config.py            # variáveis do .env
│   ├── db.py                # ligação à base de dados; aplica as migrações ao arrancar
│   ├── dependencias.py      # quem está a fazer o pedido (sessão e papel)
│   ├── admin.py             # comandos do operador (criar empresas, links)
│   ├── demo.py              # empresa de demonstração (faturas e PDFs fictícios)
│   ├── copias.py            # cópias de segurança (criar, listar, restaurar)
│   ├── modelos/
│   │   ├── fatura.py        # modelos Pydantic (DadosFatura, EdicaoFatura, LinhaIVA)
│   │   ├── tabelas.py       # tabelas: empresas, utilizadores, sessões, fornecedores, faturas
│   │   └── esquemas.py      # formato das respostas da API
│   ├── rotas/
│   │   ├── faturas.py       # /api/faturas
│   │   ├── fornecedores.py  # /api/fornecedores
│   │   ├── dashboard.py     # /api/dashboard
│   │   ├── contabilidade.py # /api/contabilidade
│   │   ├── auth.py          # /api/auth (entrar, sair, convites)
│   │   └── equipa.py        # /api/equipa
│   └── servicos/
│       ├── autenticacao.py  # palavras-passe, sessões e links de convite
│       ├── processamento.py # QR -> cache -> IA -> verificações -> duplicados
│       ├── qr.py            # leitura do QR da AT
│       ├── pdf.py           # extração de texto
│       ├── extracao_ia.py   # chamada à API do Claude
│       ├── validacao.py     # NIF e totais
│       ├── repositorio.py   # consultas à base de dados
│       ├── estatisticas.py  # números do dashboard
│       ├── exportacao.py    # CSV e Excel
│       ├── limites.py       # limite de pedidos (rate limiting)
│       ├── contabilidade/   # SNC: plano de contas, IVA, classificação, lançamentos, relatórios,
│       │                    #      verificações, calendário fiscal, SAF-T, extratos e
│       │                    #      reconciliação bancária e assistente com IA
│       └── armazenamento.py # PDFs e cópias: pasta local ou bucket R2/S3
├── migracoes/               # migrações Alembic (versions/0001_esquema_inicial.py, ...)
├── static/                  # páginas (login, lista, revisão, dashboard, equipa), CSS e JS
├── tests/                   # 211 testes do servidor (pytest); tests/dados/ tem o XSD oficial do SAF-T
├── tests_ui/                # 50 testes da interface (Playwright)
├── .github/workflows/       # CI: ruff + testes em cada push
├── alembic.ini
├── Dockerfile
├── render.yaml
├── .env.example
├── pyproject.toml           # configuração do ruff e do pytest
├── requirements-dev.txt     # ferramentas de desenvolvimento
└── requirements.txt
```

## Decisões técnicas

- **QR primeiro, IA no fim.** O QR é gratuito e determinístico. Com a cache de fornecedores, o custo de IA por fatura tende para zero à medida que os fornecedores habituais ficam conhecidos.
- **Saída estruturada com Pydantic.** O mesmo modelo gera o esquema enviado à IA e valida a resposta. A IA copia os valores e não os corrige: quem verifica as contas é o código, para os erros das faturas não ficarem escondidos.
- **Padrão repositório.** As rotas não escrevem consultas à base de dados. O `commit` é decidido pela rota, por isso uma operação falhada não deixa dados a meio.
- **pypdfium2 + zxing-cpp** em vez de pdf2image + pyzbar: instalam-se só com `pip`, sem Poppler nem DLLs, e funcionam igual no Windows e no Docker.
- **Isolamento por empresa em todas as consultas.** O repositório recebe sempre o `empresa_id` de quem está ligado, e uma fatura de outra empresa responde 404, não 403, para nem revelar que existe. Há testes dedicados a isso.
- **Sessões guardadas no servidor, com o token só como hash.** Ao contrário de um JWT, uma sessão pode ser cortada de imediato (sair, desativar a conta, mudar a palavra-passe), e quem ler a base de dados não fica com tokens utilizáveis.
- **Armazenamento atrás de uma interface.** As rotas só chamam `guardar_pdf`, `ler_pdf` e `apagar_pdf`; a implementação é a pasta local ou o R2, escolhida pela configuração. Os testes usam um bucket falso, sem rede.
- **PDFs servidos pela API, não por links diretos ao bucket.** O bucket fica privado e cada pedido passa pela verificação da sessão e da empresa. Com faturas de até 10 MB, o custo de passar pelo servidor é pequeno.
- **Migrações aplicadas ao arrancar.** Com uma só instância, é a forma mais simples de nunca publicar código novo com tabelas antigas. As bases de dados anteriores ao Alembic são reconhecidas e marcadas na revisão inicial.
- **Limite de pedidos em memória.** Chega para uma instância e não acrescenta serviços. Com várias instâncias, o contador passaria para o Redis.
- **Interface sem frameworks, com um sistema de cores.** Todas as cores, raios e sombras são variáveis CSS num só sítio; o modo escuro é o mesmo conjunto de variáveis com outros valores. Os contrastes cumprem a WCAG AA e o estado nunca é indicado só pela cor (há sempre ícone e texto).
- **Linhas retas no gráfico mensal.** Uma curva suavizada sugere valores entre os meses que não existem.
- **Contabilidade imutável.** Um lançamento nunca é alterado nem apagado: corrige-se com um estorno, e os relatórios contam os dois (anulam-se). O histórico fica completo, como espera um auditor, e o fecho de períodos é fácil de garantir.
- **Plano de contas pensado para o SAF-T.** As contas de 2 dígitos são sempre agregadoras (os movimentos vão para 111, 121, 511...) e cada conta de movimento tem um código de taxonomia; as subcontas herdam o da conta-mãe, como manda a Portaria. Planos antigos são atualizados ao abrir a contabilidade, sem mudar valores nem datas.
- **SAF-T validado contra o XSD oficial.** O esquema da AT usa XSD 1.1 (com `xs:assert`), que o `lxml` não suporta: os testes usam o `xmlschema`, que o valida por completo.
- **A aplicação não emite faturas.** Emitir exige um programa certificado pela AT; a contabilidade recebe as vendas pelo SAF-T de faturação, que todos os programas certificados exportam. O ficheiro de teste é gerado segundo o XSD oficial e validado contra ele, para a importação ser testada com a estrutura real.
- **Reconciliação automática conservadora.** Só reconcilia sozinha quando há um único lançamento ou uma única fatura com aquele valor e data próxima, ou quando o utilizador já ensinou a regra. As sugestões por palavras-chave ficam sempre para uma pessoa confirmar. Estornar um lançamento reconciliado volta a pôr o movimento do banco por reconciliar.
- **Regras fiscais num só ficheiro.** Taxas, escalões, perfis de dedutibilidade e campos da declaração periódica estão em `contabilidade/iva.py`: quando a lei mudar, muda-se um ficheiro.
- **A IA propõe, a pessoa decide.** O assistente só tem ferramentas de leitura; as propostas de lançamento são validadas com as mesmas regras de um lançamento manual, mas só são gravadas com um clique do utilizador. As chamadas usam o *fallback* do servidor da API (se o modelo recusar um pedido, outro modelo responde na mesma chamada).
- **Exportação segura.** Os textos vêm de PDFs de terceiros, por isso valores começados por `=`, `+`, `-` ou `@` são neutralizados para o Excel não os executar como fórmulas.

## Próximos passos

- Envio dos convites por email (SMTP, Resend ou Postmark).
- Quota de leituras por IA por empresa e registo de atividade (quem fez o quê).
- Pagamentos e planos por empresa (Stripe).
- Processamento em segundo plano para lotes grandes de faturas.
- Leitura por IA de faturas digitalizadas sem QR (envio da imagem da página).
