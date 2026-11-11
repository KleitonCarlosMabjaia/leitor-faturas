# Leitor de Faturas

[![Testes](https://github.com/KleitonCarlosMabjaia/leitor-faturas/actions/workflows/testes.yml/badge.svg)](https://github.com/KleitonCarlosMabjaia/leitor-faturas/actions/workflows/testes.yml)

Aplicação web para pequenas empresas portuguesas (restaurantes, oficinas, lojas, escritórios de contabilidade), com **acesso por convite e dados separados por empresa**, que **lê faturas de fornecedores em PDF**, extrai os dados automaticamente, guarda-os numa base de dados e mostra os gastos num dashboard, com exportação para Excel.

A leitura funciona em cascata, da fonte mais barata para a mais cara: primeiro o **código QR da AT** (gratuito e exato), depois uma **cache de fornecedores** por NIF e, só no fim, **IA** (API do Claude) para o que falta.

| Lista de faturas | Revisão | Dashboard (modo escuro) |
| --- | --- | --- |
| ![Lista de faturas com pesquisa, filtros por estado e etiquetas](docs/capturas/lista.png) | ![PDF da fatura ao lado dos campos editáveis](docs/capturas/revisao.png) | ![Indicadores, gasto por fornecedor e evolução mensal em modo escuro](docs/capturas/dashboard.png) |

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
- 102 testes automáticos (pytest), incluindo isolamento entre empresas e migrações. Correm sem Internet e sem chave da API.

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

`python -m app.demo` cria uma empresa própria ("... (demonstração)"), separada das empresas reais, com 30 faturas de 7 fornecedores fictícios (bebidas, carne, peixe e legumes), espalhadas pelos últimos 6 meses. Algumas ficam por rever, algumas por pagar e duas vencidas. Cada fatura tem um PDF gerado na hora, com texto e um QR da AT válido, por isso os PDFs (em `uploads/`) também servem para testar o carregamento.

- O comando mostra um link para definir a palavra-passe do utilizador `demo@demonstracao.pt`.
- `python -m app.demo --limpar` apaga **só** a empresa de demonstração e cria-a de novo.

Todos os nomes, moradas e NIFs são inventados.

## Testes

```bash
pip install -r requirements-dev.txt
playwright install chromium        # só da primeira vez

ruff check .                       # estilo e erros comuns
pytest                             # servidor: 102 testes, poucos segundos, sem navegador
pytest tests_ui                    # interface: 15 testes num Chromium a sério (Playwright)
```

Nos testes do servidor, cada teste usa uma base de dados temporária, a IA é substituída por uma função falsa e o bucket S3 por um objeto falso.

Os testes da interface arrancam a aplicação em modo de demonstração (base de dados e pasta temporárias) e verificam, num navegador: filtros, pesquisa, ordenação, a janela de confirmação ao apagar, o tema escuro, os atalhos e os erros na revisão, os gráficos, a ausência de erros de JavaScript e que nenhuma página tem deslocamento horizontal num telemóvel.

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
3. O Render lê o `render.yaml`, constrói a imagem e publica-a num endereço `https://leitor-faturas-xxxx.onrender.com`.

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
│       └── armazenamento.py # PDFs e cópias: pasta local ou bucket R2/S3
├── migracoes/               # migrações Alembic (versions/0001_esquema_inicial.py, ...)
├── static/                  # páginas (login, lista, revisão, dashboard, equipa), CSS e JS
├── tests/                   # 102 testes do servidor (pytest)
├── tests_ui/                # 15 testes da interface (Playwright)
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
- **Exportação segura.** Os textos vêm de PDFs de terceiros, por isso valores começados por `=`, `+`, `-` ou `@` são neutralizados para o Excel não os executar como fórmulas.

## Próximos passos

- Envio dos convites por email (SMTP, Resend ou Postmark).
- Quota de leituras por IA por empresa e registo de atividade (quem fez o quê).
- Pagamentos e planos por empresa (Stripe).
- Processamento em segundo plano para lotes grandes de faturas.
- Leitura por IA de faturas digitalizadas sem QR (envio da imagem da página).
