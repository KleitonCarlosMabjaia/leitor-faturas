// Dobra - subscrição: plano atual, período experimental, escolha de plano (Stripe) e faturas

const NOMES_ESTADO = {
  experimental: ["Período experimental", "info", "relogio"],
  ativa: ["Ativa", "ok", "check"],
  em_atraso: ["Pagamento em atraso", "erro", "alerta"],
  cancelada: ["Cancelada", "aviso", "alerta"],
};
const BENEFICIOS = {
  essencial: ["Leitura de faturas (QR da AT e IA)", "Análise de compras e faturas por pagar", "Exportação para Excel e CSV", "2 utilizadores"],
  contabilidade: ["Tudo do Essencial", "Lançamentos SNC, balancete e extratos", "IVA, bancos, vendas e SAF-T",
    "Salários, ativos fixos e inventários", "Fecho do exercício e Assistente IA", "5 utilizadores"],
  gabinete: ["Tudo do plano Contabilidade", "Utilizadores ilimitados", "Apoio prioritário e migração de dados", "Condições à medida"],
};

async function irParaStripe(caminho, corpo, b) {
  ocupado(b, true);
  try {
    const { url } = await enviar(caminho, corpo);
    location.href = url;
  } catch (erro) {
    notificar(erro.message, "erro");
    ocupado(b, false);
  }
}

function cartaoEstado(d) {
  const [texto, classe, nomeIcone] = NOMES_ESTADO[d.estado] || NOMES_ESTADO.ativa;
  const detalhes = [];
  if (d.faturacao_ativa && d.estado === "experimental" && d.dias_experimentais !== null) {
    const n = d.dias_experimentais;
    detalhes.push(n < 0 ? `O período experimental terminou a ${formatarData(d.experimental_ate)}`
      : `${n === 0 ? "Último dia" : `${n} dia${n === 1 ? "" : "s"}`} de período experimental (até ${formatarData(d.experimental_ate)})`);
  }
  if (d.fim_periodo) detalhes.push(`${d.cancela_no_fim ? "Termina" : "Renova"} a ${formatarData(d.fim_periodo)}`);
  const utilizadores = d.max_utilizadores ? `${d.utilizadores_ativos} de ${d.max_utilizadores} utilizadores` : `${d.utilizadores_ativos} utilizadores (sem limite)`;
  const percentagem = d.max_utilizadores ? Math.min(100, Math.round((d.utilizadores_ativos / d.max_utilizadores) * 100)) : 0;
  return h("section", { class: "cartao cartao-plano-atual", "aria-labelledby": "titulo-plano-atual" },
    h("div", { class: "cartao-cabecalho" },
      h("div", {},
        h("p", { class: "sobre-plano", texto: "Plano atual" }),
        h("h2", { id: "titulo-plano-atual" }, d.plano_nome, " ", criarEtiqueta(texto, classe, nomeIcone)),
        detalhes.length ? h("p", { class: "subtitulo", texto: detalhes.join(" · ") }) : null),
      d.pode_gerir && d.tem_cliente_stripe ? h("button", {
        type: "button", class: "botao secundario",
        onclick: (e) => irParaStripe("/api/subscricao/portal", {}, e.currentTarget),
      }, icone("carteira"), "Gerir pagamento e faturas") : null),
    d.so_leitura ? h("div", { class: "alerta erro", role: "alert" }, icone("alerta"),
      h("div", {}, h("strong", { texto: "Só leitura" }), h("p", { texto: `${d.so_leitura} Pode consultar e exportar tudo.` }))) : null,
    h("div", { class: "uso-plano" },
      h("span", { texto: utilizadores }),
      d.max_utilizadores ? h("div", { class: "barra-progresso", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100",
        "aria-valuenow": String(percentagem), "aria-label": "Utilizadores do plano" }, h("span", { style: `width:${percentagem}%` })) : null));
}

function cartaoPlanos(d) {
  const planos = d.planos.map((p) => {
    const atual = p.chave === d.plano && d.estado !== "cancelada";
    let acao;
    if (!p.checkout) {
      acao = h("a", { class: "botao secundario largo", href: "/apresentacao#planos" }, "Falar connosco");
    } else if (atual && d.estado === "ativa") {
      acao = h("button", { type: "button", class: "botao secundario largo", disabled: true }, "Plano atual");
    } else if (!d.faturacao_ativa) {
      acao = h("button", { type: "button", class: "botao secundario largo", disabled: true, title: "Os pagamentos online ainda não estão configurados" }, "Pagamentos em breve");
    } else if (!d.pode_gerir) {
      acao = h("p", { class: "ajuda", texto: "Peça a um administrador da empresa para escolher o plano." });
    } else {
      acao = h("button", { type: "button", class: `botao largo${p.chave === "contabilidade" ? "" : " secundario"}`,
        onclick: (e) => irParaStripe("/api/subscricao/checkout", { plano: p.chave }, e.currentTarget) },
      atual ? "Subscrever este plano" : `Mudar para ${p.nome}`);
    }
    return h("article", { class: `plano-cartao${p.chave === "contabilidade" ? " destaque" : ""}${atual ? " atual" : ""}` },
      p.chave === "contabilidade" ? h("span", { class: "plano-etiqueta", texto: "Mais escolhido" }) : null,
      h("h3", { texto: p.nome }),
      h("p", { class: "ajuda", texto: p.descricao }),
      h("p", { class: "plano-preco" }, p.preco_mensal ? h("strong", { texto: `${p.preco_mensal} €` }) : h("strong", { texto: "Sob consulta" }),
        p.preco_mensal ? h("span", { texto: "/mês + IVA" }) : null),
      h("ul", { class: "lista-beneficios" }, (BENEFICIOS[p.chave] || []).map((b) => h("li", {}, icone("check"), b))),
      acao);
  });
  return h("section", { class: "cartao", "aria-labelledby": "titulo-planos" },
    h("div", { class: "cartao-cabecalho" }, h("div", {},
      h("h2", { id: "titulo-planos", texto: "Planos" }),
      h("p", { class: "subtitulo", texto: "Pagamento mensal seguro, processado pelo Stripe. Sem fidelização: cancele quando quiser." }))),
    h("div", { class: "grelha-planos" }, planos),
    d.faturacao_ativa ? null : h("p", { class: "ajuda", texto: "Os pagamentos online ainda não estão ativos neste servidor: a aplicação funciona sem limites de tempo." }));
}

// Faturas certificadas dos pagamentos (emitidas no Facturalusa, com o PDF)
function cartaoFaturas(f) {
  if (!f.faturas.length) return null;
  const estado = (x) => x.estado === "emitida"
    ? criarEtiqueta(x.enviada ? "Emitida e enviada" : "Emitida", "ok", "check")
    : criarEtiqueta(x.estado === "erro" ? "Por emitir" : "A emitir", "aviso", "relogio");
  return h("section", { class: "cartao" },
    h("div", { class: "cartao-cabecalho" }, h("div", {},
      h("h2", { texto: "Faturas" }),
      h("p", { class: "subtitulo", texto: "Faturas-recibo certificadas pela AT de cada pagamento, enviadas também por email." }))),
    h("div", { class: "tabela-caixa" }, h("table", { class: "tabela responsiva" },
      h("thead", {}, h("tr", {}, ...["Data", "Documento", "Descrição", "Total", "Estado", ""].map((x, i) =>
        h("th", { scope: "col", class: i === 3 ? "num" : null }, x ? x : h("span", { class: "visualmente-oculto", texto: "PDF" }))))),
      h("tbody", {}, f.faturas.map((x) => h("tr", {},
        h("td", { "data-rotulo": "Data", texto: formatarData(x.data) }),
        h("td", { "data-rotulo": "Documento", texto: x.numero || "—" }),
        h("td", { "data-rotulo": "Descrição", texto: x.descricao }),
        h("td", { class: "num", "data-rotulo": "Total", texto: formatarValor(x.total ?? 0, "EUR") }),
        h("td", { "data-rotulo": "Estado" }, estado(x)),
        h("td", {}, x.url_pdf ? h("a", { class: "botao secundario pequeno", href: x.url_pdf, target: "_blank", rel: "noopener" },
          icone("descarregar"), "PDF") : null)))))));
}

async function carregarSubscricao() {
  const zona = document.getElementById("subscricao");
  try {
    const d = await obter("/api/subscricao");
    const recurso = new URLSearchParams(location.search).get("recurso");
    const aviso = recurso === "contabilidade" && !d.funcionalidades.includes("contabilidade")
      ? h("div", { class: "alerta info destaque-upgrade", role: "status" }, icone("faisca"),
        h("div", {}, h("strong", { texto: "A contabilidade faz parte do plano Contabilidade" }),
          h("p", { texto: "Lançamentos SNC, IVA, bancos, salários, balanço e SAF-T, a partir das faturas que já carrega." })))
      : null;
    const faturas = await obter("/api/subscricao/faturas").catch(() => ({ faturas: [] }));
    preencher(zona, aviso, cartaoEstado(d), cartaoFaturas(faturas), cartaoPlanos(d));
    if (new URLSearchParams(location.search).get("pagamento") === "concluido") {
      notificar("Pagamento recebido. Obrigado! A subscrição fica ativa dentro de instantes.", "ok");
    }
  } catch (erro) {
    notificar(erro.message, "erro");
  }
}

carregarSubscricao();
