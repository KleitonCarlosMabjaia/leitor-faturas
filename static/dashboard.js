// Leitor de Faturas - dashboard: indicadores, gráficos (Chart.js) e faturas por pagar

const seletorMes = document.getElementById("mes");
const mensagem = document.getElementById("mensagem");
const nomesMeses = new Intl.DateTimeFormat("pt-PT", { month: "long", year: "numeric" });
const nomesMesesCurtos = new Intl.DateTimeFormat("pt-PT", { month: "short" });

let graficoFornecedores = null;
let graficoEvolucao = null;
let ultimosDados = null;

function nomeMes(chave, curto = false) {
  const [ano, mes] = chave.split("-").map(Number);
  const data = new Date(ano, mes - 1, 1);
  return curto ? nomesMesesCurtos.format(data).replace(".", "") : nomesMeses.format(data);
}

function mostrarMensagem(texto, tipo) {
  mensagem.textContent = texto;
  mensagem.className = `mensagem ${tipo || ""}`;
}

// As cores vêm do CSS, por isso os gráficos seguem o modo claro/escuro
function cores() {
  const estilo = getComputedStyle(document.documentElement);
  const ler = (nome) => estilo.getPropertyValue(nome).trim();
  return { serie: ler("--grafico"), texto: ler("--tinta-suave"), grelha: ler("--linha"), fundo: ler("--folha") };
}

const euros = (valor) => formatarValor(valor, "EUR");

// ---------- Indicadores ----------

function preencherIndicadores(d) {
  document.getElementById("rotulo-total-mes").textContent = `Gasto em ${nomeMes(d.mes)}`;
  document.getElementById("total-mes").textContent = euros(d.total_mes);
  document.getElementById("faturas-mes").textContent = d.faturas_mes === 1 ? "1 fatura" : `${d.faturas_mes} faturas`;
  document.getElementById("iva-mes").textContent = euros(d.iva_mes);
  document.getElementById("rotulo-iva-ano").textContent = `IVA acumulado em ${d.ano}`;
  document.getElementById("iva-ano").textContent = euros(d.iva_ano);
  document.getElementById("nota-iva-ano").textContent = `de janeiro a ${nomeMes(d.mes).split(" ")[0]}`;

  const pagar = d.por_pagar;
  document.getElementById("por-pagar").textContent = euros(pagar.total);
  const nota = document.getElementById("nota-por-pagar");
  nota.textContent = pagar.vencidas
    ? `${pagar.quantidade} faturas · ⚠ ${pagar.vencidas} vencida${pagar.vencidas > 1 ? "s" : ""} (${euros(pagar.total_vencidas)})`
    : `${pagar.quantidade} fatura${pagar.quantidade === 1 ? "" : "s"}, nenhuma vencida`;
  nota.classList.toggle("alerta", pagar.vencidas > 0);
}

// ---------- Gráficos ----------

function opcoesComuns(c) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? false : { duration: 300 },
    plugins: {
      legend: { display: false }, // uma só série: o título do cartão já diz o que é
      tooltip: {
        backgroundColor: c.fundo,
        titleColor: c.texto,
        bodyColor: c.texto,
        borderColor: c.grelha,
        borderWidth: 1,
        padding: 10,
        displayColors: false,
        callbacks: { label: (ctx) => euros(ctx.parsed.x ?? ctx.parsed.y) },
      },
    },
  };
}

function desenharFornecedores(d, c) {
  const vazio = d.por_fornecedor.length === 0;
  document.getElementById("vazio-fornecedores").hidden = !vazio;
  document.getElementById("grafico-fornecedores").parentElement.hidden = vazio;

  const tabela = document.getElementById("tabela-fornecedores");
  tabela.innerHTML = "";
  for (const linha of d.por_fornecedor) {
    const tr = tabela.insertRow();
    tr.insertCell().textContent = linha.fornecedor;
    const valor = tr.insertCell();
    valor.className = "num";
    valor.textContent = euros(linha.total);
  }

  graficoFornecedores?.destroy();
  if (vazio) return;
  const opcoes = opcoesComuns(c);
  graficoFornecedores = new Chart(document.getElementById("grafico-fornecedores"), {
    type: "bar",
    data: {
      labels: d.por_fornecedor.map((l) => l.fornecedor),
      datasets: [{
        data: d.por_fornecedor.map((l) => l.total),
        backgroundColor: c.serie,
        hoverBackgroundColor: c.serie,
        borderRadius: 4,            // ponta arredondada...
        borderSkipped: "start",     // ...só do lado do valor, não junto ao eixo
        maxBarThickness: 22,        // barras finas
        categoryPercentage: 0.8,
      }],
    },
    options: {
      ...opcoes,
      indexAxis: "y", // barras horizontais: os nomes dos fornecedores são compridos
      scales: {
        x: {
          beginAtZero: true,
          grid: { color: c.grelha },
          border: { display: false },
          ticks: { color: c.texto, callback: (v) => `${Number(v).toLocaleString("pt-PT")} €` },
        },
        y: {
          grid: { display: false },
          border: { color: c.grelha },
          ticks: {
            color: c.texto,
            // Nomes muito compridos são cortados no eixo (aparecem completos no tooltip e na tabela)
            callback(valor) {
              const nome = this.getLabelForValue(valor);
              return nome.length > 26 ? `${nome.slice(0, 25)}…` : nome;
            },
          },
        },
      },
    },
  });
  const maior = d.por_fornecedor[0];
  document.getElementById("grafico-fornecedores").setAttribute(
    "aria-label",
    `Gráfico de barras do gasto por fornecedor. O maior é ${maior.fornecedor}, com ${euros(maior.total)}.`
  );
}

function desenharEvolucao(d, c) {
  const tabela = document.getElementById("tabela-evolucao");
  tabela.innerHTML = "";
  for (const linha of d.evolucao) {
    const tr = tabela.insertRow();
    tr.insertCell().textContent = nomeMes(linha.mes);
    const valor = tr.insertCell();
    valor.className = "num";
    valor.textContent = euros(linha.total);
  }

  graficoEvolucao?.destroy();
  const opcoes = opcoesComuns(c);
  graficoEvolucao = new Chart(document.getElementById("grafico-evolucao"), {
    type: "line",
    data: {
      labels: d.evolucao.map((l) => nomeMes(l.mes, true)),
      datasets: [{
        data: d.evolucao.map((l) => l.total),
        borderColor: c.serie,
        backgroundColor: c.serie,
        borderWidth: 2,
        pointRadius: 4,          // 8 px de diâmetro
        pointHoverRadius: 6,
        pointBorderColor: c.fundo,
        pointBorderWidth: 2,     // anel da cor do fundo à volta de cada ponto
        tension: 0.25,
      }],
    },
    options: {
      ...opcoes,
      interaction: { mode: "index", intersect: false }, // tooltip ao passar em qualquer ponto da vertical
      plugins: {
        ...opcoes.plugins,
        tooltip: {
          ...opcoes.plugins.tooltip,
          callbacks: {
            title: (itens) => nomeMes(d.evolucao[itens[0].dataIndex].mes),
            label: (ctx) => euros(ctx.parsed.y),
          },
        },
      },
      scales: {
        x: { grid: { display: false }, border: { color: c.grelha }, ticks: { color: c.texto } },
        y: {
          beginAtZero: true,
          grid: { color: c.grelha },
          border: { display: false },
          ticks: { color: c.texto, maxTicksLimit: 5, callback: (v) => `${Number(v).toLocaleString("pt-PT")} €` },
        },
      },
    },
  });
  const maior = d.evolucao.reduce((a, b) => (b.total > a.total ? b : a));
  document.getElementById("grafico-evolucao").setAttribute(
    "aria-label",
    `Gráfico de linha do gasto nos últimos 12 meses. O mês mais alto foi ${nomeMes(maior.mes)}, com ${euros(maior.total)}.`
  );
}

// ---------- Faturas por pagar ----------

function desenharPorPagar(d) {
  const corpo = document.getElementById("linhas-pagar");
  corpo.innerHTML = "";
  for (const f of d.por_pagar.faturas) {
    const tr = corpo.insertRow();

    const fornecedor = tr.insertCell();
    fornecedor.className = "nome";
    const ligacao = document.createElement("a");
    ligacao.href = `/faturas/${f.id}`;
    ligacao.className = "titulo";
    ligacao.textContent = f.fornecedor;
    fornecedor.appendChild(ligacao);
    if (f.numero_fatura) {
      const detalhe = document.createElement("span");
      detalhe.className = "detalhe";
      detalhe.textContent = `Fatura ${f.numero_fatura}`;
      fornecedor.appendChild(detalhe);
    }

    const vencimento = tr.insertCell();
    vencimento.textContent = formatarData(f.data_vencimento);
    if (f.vencida) {
      const etiqueta = document.createElement("span");
      etiqueta.className = "etiqueta erro";
      etiqueta.textContent = "⚠ Vencida";
      vencimento.append(" ", etiqueta);
    }

    const total = tr.insertCell();
    total.className = "num";
    total.textContent = euros(f.total);

    const acoes = tr.insertCell();
    acoes.className = "num acoes";
    const botao = document.createElement("button");
    botao.type = "button";
    botao.className = "acao repetir";
    botao.textContent = "Marcar como paga";
    botao.setAttribute("aria-label", `Marcar como paga a fatura de ${f.fornecedor}`);
    botao.addEventListener("click", () => marcarPaga(f));
    acoes.appendChild(botao);
  }
  document.getElementById("vazio-pagar").hidden = d.por_pagar.faturas.length > 0;
}

async function marcarPaga(f) {
  try {
    const resposta = await api(`/api/faturas/${f.id}/pagamento`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paga: true }),
    });
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível atualizar a fatura."));
    mostrarMensagem(`Fatura de ${f.fornecedor} marcada como paga.`, "sucesso");
    await carregar(seletorMes.value);
  } catch (erro) {
    mostrarMensagem(erro.message, "erro");
  }
}

// ---------- Carregar ----------

function desenhar() {
  if (!ultimosDados) return;
  const c = cores();
  desenharFornecedores(ultimosDados, c);
  desenharEvolucao(ultimosDados, c);
}

async function carregar(mes) {
  try {
    const resposta = await api(`/api/dashboard${mes ? `?mes=${mes}` : ""}`);
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível obter os dados."));
    const d = await resposta.json();
    ultimosDados = d;

    // Seletor de mês: os meses com faturas (e o escolhido, mesmo que não tenha)
    const meses = d.meses_disponiveis.includes(d.mes) ? d.meses_disponiveis : [d.mes, ...d.meses_disponiveis];
    seletorMes.innerHTML = "";
    for (const m of meses) seletorMes.add(new Option(nomeMes(m), m));
    seletorMes.value = d.mes;

    preencherIndicadores(d);
    desenharPorPagar(d);
    if (typeof Chart === "undefined") {
      mostrarMensagem("Não foi possível carregar os gráficos (sem Internet?). Os valores estão nas tabelas.", "erro");
      document.querySelectorAll(".graficos details").forEach((el) => (el.open = true));
      return;
    }
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    desenhar();
  } catch (erro) {
    mostrarMensagem(erro.message || "Não foi possível obter os dados. Confirme que o servidor está a correr.", "erro");
  }
}

seletorMes.addEventListener("change", () => carregar(seletorMes.value));
// Se o sistema mudar entre modo claro e escuro, redesenha com as cores novas
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", desenhar);

carregar();
