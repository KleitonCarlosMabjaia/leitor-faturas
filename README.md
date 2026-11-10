# Leitor de Faturas

Aplicação web para pequenas empresas portuguesas (restaurantes, oficinas, lojas, escritórios de contabilidade) que **lê faturas de fornecedores em PDF**, extrai os dados automaticamente, guarda-os numa base de dados e mostra os gastos num dashboard, com exportação para Excel.

A leitura funciona em cascata, da fonte mais barata para a mais cara: primeiro o **código QR da AT** (gratuito e exato), depois uma **cache de fornecedores** por NIF e, só no fim, **IA** (API do Claude) para o que falta.

<!-- Capturas de ecrã: guardar em docs/capturas/ com estes nomes -->
| Lista de faturas | Revisão | Dashboard |
| --- | --- | --- |
| ![Lista de faturas com filtros e estados](docs/capturas/lista.png) | ![PDF ao lado dos campos editáveis](docs/capturas/revisao.png) | ![Indicadores e gráficos de gastos](docs/capturas/dashboard.png) |

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

**Qualidade**
- Interface em português de Portugal, modo claro e escuro, navegação por teclado, adaptada a telemóvel.
- 60 testes automáticos (pytest), que correm sem Internet e sem chave da API.

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
| Base de dados | SQLite com SQLAlchemy 2.0 |
| PDF e QR | pypdf, pdfplumber, pypdfium2, zxing-cpp |
| IA | SDK oficial `anthropic` (modelo configurável) |
| Exportação | openpyxl |
| Frontend | HTML, CSS e JavaScript, sem frameworks; Chart.js |
| Testes | pytest |
| Publicação | Docker, Render |

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

4. (Opcional) Criar os dados de demonstração, 30 faturas fictícias de um restaurante:

   ```bash
   python -m app.demo
   ```

5. Arrancar o servidor e abrir <http://127.0.0.1:8000>:

   ```bash
   uvicorn app.main:app --reload
   ```

A documentação automática da API fica em <http://127.0.0.1:8000/docs>.

## Dados de demonstração

`python -m app.demo` cria 30 faturas de 7 fornecedores fictícios (bebidas, carne, peixe e legumes), espalhadas pelos últimos 6 meses. Algumas ficam por rever, algumas por pagar e duas vencidas. Cada fatura tem um PDF gerado na hora, com texto e um QR da AT válido, por isso os PDFs (em `uploads/`) também servem para testar o carregamento.

- Só corre se a base de dados estiver vazia.
- `python -m app.demo --limpar` apaga **todas** as faturas e cria os dados de novo (pede confirmação).

Todos os nomes, moradas e NIFs são inventados.

## Testes

```bash
pytest
```

Cada teste usa uma base de dados temporária, e a IA é substituída por uma função falsa.

## Publicar (Render, gratuito)

O repositório inclui um `Dockerfile` e um `render.yaml`.

1. Pôr o projeto no GitHub (o `.gitignore` já exclui o `.env`, a base de dados e as faturas).
2. Em <https://render.com>, criar uma conta e escolher **New → Blueprint**, com o repositório.
3. O Render lê o `render.yaml`, constrói a imagem e publica-a num endereço `https://leitor-faturas-xxxx.onrender.com`.

A versão publicada corre em **modo de demonstração** (`MODO_DEMO=1`): arranca com as 30 faturas de exemplo e tem a **IA desligada**, para que os visitantes não gastem créditos da API. O QR continua a ser lido.

No plano gratuito, o servidor adormece ao fim de 15 minutos sem visitas (o primeiro acesso demora cerca de 1 minuto) e o disco não é permanente: a cada novo arranque, os dados voltam às faturas de demonstração. Para uma demonstração isto é uma vantagem, porque fica sempre limpa.

Para testar a imagem localmente:

```bash
docker build -t leitor-faturas .
docker run -p 8000:8000 -e MODO_DEMO=1 leitor-faturas
```

## API

| Método | Endereço | Descrição |
| --- | --- | --- |
| GET | `/api/saude` | Estado do servidor, IA configurada e modo de demonstração |
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
│   ├── db.py                # ligação à base de dados e migrações simples
│   ├── demo.py              # dados de demonstração (faturas e PDFs fictícios)
│   ├── modelos/
│   │   ├── fatura.py        # modelos Pydantic (DadosFatura, EdicaoFatura, LinhaIVA)
│   │   ├── tabelas.py       # tabelas fornecedores e faturas (SQLAlchemy)
│   │   └── esquemas.py      # formato das respostas da API
│   ├── rotas/
│   │   ├── faturas.py       # /api/faturas
│   │   ├── fornecedores.py  # /api/fornecedores
│   │   └── dashboard.py     # /api/dashboard
│   └── servicos/
│       ├── processamento.py # QR -> cache -> IA -> verificações -> duplicados
│       ├── qr.py            # leitura do QR da AT
│       ├── pdf.py           # extração de texto
│       ├── extracao_ia.py   # chamada à API do Claude
│       ├── validacao.py     # NIF e totais
│       ├── repositorio.py   # consultas à base de dados
│       ├── estatisticas.py  # números do dashboard
│       ├── exportacao.py    # CSV e Excel
│       └── armazenamento.py # ficheiros PDF
├── static/                  # páginas (lista, revisão, dashboard), CSS e JS
├── tests/                   # 60 testes pytest
├── Dockerfile
├── render.yaml
├── .env.example
└── requirements.txt
```

## Decisões técnicas

- **QR primeiro, IA no fim.** O QR é gratuito e determinístico. Com a cache de fornecedores, o custo de IA por fatura tende para zero à medida que os fornecedores habituais ficam conhecidos.
- **Saída estruturada com Pydantic.** O mesmo modelo gera o esquema enviado à IA e valida a resposta. A IA copia os valores e não os corrige: quem verifica as contas é o código, para os erros das faturas não ficarem escondidos.
- **Padrão repositório.** As rotas não escrevem consultas à base de dados. O `commit` é decidido pela rota, por isso uma operação falhada não deixa dados a meio.
- **pypdfium2 + zxing-cpp** em vez de pdf2image + pyzbar: instalam-se só com `pip`, sem Poppler nem DLLs, e funcionam igual no Windows e no Docker.
- **Exportação segura.** Os textos vêm de PDFs de terceiros, por isso valores começados por `=`, `+`, `-` ou `@` são neutralizados para o Excel não os executar como fórmulas.

## Próximos passos

- Contas de utilizador e várias empresas na mesma instalação (com os dados separados por empresa, por causa do RGPD).
- PostgreSQL (`URL_BASE_DADOS`) e migrações com Alembic.
- Processamento em segundo plano para lotes grandes de faturas.
- Leitura por IA de faturas digitalizadas sem QR (envio da imagem da página).
