// Leitor de Faturas - página de revisão: PDF de um lado, campos editáveis do outro

const idFatura = location.pathname.split("/").pop();

const formulario = document.getElementById("formulario");
const linhasIva = document.getElementById("linhas-iva");
const modeloLinha = document.getElementById("modelo-linha-iva");
const mensagem = document.getElementById("mensagem");
const conferencia = document.getElementById("conferencia");
const proxima = document.getElementById("proxima");
const caixaPaga = document.getElementById("paga");

const CAMPOS_TEXTO = ["fornecedor", "nif_fornecedor", "numero_fatura", "data_emissao", "data_vencimento", "moeda"];
const CAMPOS_NUMERO = ["valor_sem_iva", "total"];
const TOLERANCIA = 0.02; // a mesma do servidor

function mostrarMensagem(texto, tipo) {
  mensagem.textContent = texto;
  mensagem.className = `mensagem ${tipo || ""}`;
}

const numero = (input) => (input.value === "" ? null : Number(input.value));

// ---------- Linhas de IVA ----------

function adicionarLinhaIva(linha = { taxa: "", base: "", valor: "" }) {
  const tr = modeloLinha.content.firstElementChild.cloneNode(true);
  tr.querySelector(".taxa").value = linha.taxa;
  tr.querySelector(".base").value = linha.base;
  tr.querySelector(".valor").value = linha.valor;
  tr.querySelector(".remover").addEventListener("click", () => {
    tr.remove();
    conferir();
  });
  linhasIva.appendChild(tr);
  return tr;
}

function lerLinhasIva() {
  return [...linhasIva.querySelectorAll("tr")]
    .map((tr) => ({
      taxa: numero(tr.querySelector(".taxa")),
      base: numero(tr.querySelector(".base")),
      valor: numero(tr.querySelector(".valor")),
    }))
    .filter((l) => l.taxa !== null || l.base !== null || l.valor !== null); // ignora linhas vazias
}

// ---------- Conferência dos totais em tempo real ----------

function conferir() {
  const semIva = numero(formulario.valor_sem_iva);
  const total = numero(formulario.total);
  if (semIva === null || total === null) {
    conferencia.textContent = "";
    return;
  }
  const somaIva = lerLinhasIva().reduce((soma, l) => soma + (l.valor || 0), 0);
  const calculado = semIva + somaIva;
  const diferenca = Math.round((total - calculado) * 100) / 100;

  const texto = `${formatarValor(semIva)} + IVA ${formatarValor(somaIva)} = ${formatarValor(calculado)}`;
  if (Math.abs(diferenca) <= TOLERANCIA) {
    conferencia.textContent = `✓ ${texto}: confere com o total.`;
    conferencia.className = "conferencia ok";
  } else {
    conferencia.textContent = `✗ ${texto}: difere ${formatarValor(diferenca)} do total.`;
    conferencia.className = "conferencia erro";
  }
}

// ---------- Mostrar a fatura ----------

function preencher(f) {
  const estado = descreverEstado(f);
  document.title = `${f.fornecedor || f.nome_original} · Leitor de Faturas`;
  document.getElementById("titulo").textContent = f.fornecedor || "Fornecedor por preencher";
  document.getElementById("subtitulo").textContent = [
    f.nome_original,
    f.atcud && f.atcud !== "0" ? `ATCUD ${f.atcud}` : null,
    ORIGENS[f.origem],
  ]
    .filter(Boolean)
    .join(" · ");

  const etiqueta = document.getElementById("etiqueta");
  etiqueta.textContent = estado.etiqueta;
  etiqueta.className = `etiqueta ${estado.classe}`;
  etiqueta.hidden = false;

  // Alertas (ou o erro da leitura, se não houve dados)
  const notas = f.alertas?.length ? f.alertas : f.erro ? [f.erro] : [];
  const caixa = document.getElementById("alertas");
  const lista = document.getElementById("lista-alertas");
  lista.innerHTML = "";
  for (const nota of notas) {
    const li = document.createElement("li");
    li.textContent = nota;
    lista.appendChild(li);
  }
  caixa.hidden = notas.length === 0;

  for (const campo of CAMPOS_TEXTO) formulario[campo].value = f[campo] ?? "";
  for (const campo of CAMPOS_NUMERO) formulario[campo].value = f[campo] ?? "";
  if (!formulario.moeda.value) formulario.moeda.value = "EUR";

  linhasIva.innerHTML = "";
  for (const linha of f.iva || []) adicionarLinhaIva(linha);
  if (!f.iva?.length) adicionarLinhaIva({ taxa: 23, base: "", valor: "" });

  caixaPaga.checked = f.paga;

  document.getElementById("confirmar").textContent =
    f.estado === "confirmada" ? "Guardar e manter confirmada" : "Confirmar dados";
  conferir();
}

async function carregar() {
  const pdfUrl = `/api/faturas/${idFatura}/pdf`;
  document.getElementById("pdf").src = pdfUrl;
  document.getElementById("abrir-pdf").href = pdfUrl;

  try {
    const resposta = await fetch(`/api/faturas/${idFatura}`);
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível obter a fatura."));
    preencher(await resposta.json());
  } catch (erro) {
    document.getElementById("titulo").textContent = "Fatura não encontrada";
    formulario.hidden = true;
    mostrarMensagem(erro.message, "erro");
  }
}

// ---------- Guardar / confirmar ----------

function validarNoNavegador(confirmar) {
  // Para guardar um rascunho basta ter números e datas válidos; para confirmar, tudo preenchido
  const obrigatorios = confirmar
    ? ["fornecedor", "nif_fornecedor", "numero_fatura", "data_emissao", "valor_sem_iva", "total"]
    : ["data_emissao", "valor_sem_iva", "total"];
  for (const campo of obrigatorios) {
    if (!formulario[campo].value.trim()) {
      formulario[campo].focus();
      const rotulo = formulario.querySelector(`label[for="${campo}"]`).textContent;
      return `Preencha o campo "${rotulo}".`;
    }
  }
  if (lerLinhasIva().some((l) => l.taxa === null || l.base === null || l.valor === null)) {
    return "Complete ou remova as linhas de IVA incompletas.";
  }
  return null;
}

async function gravar(confirmar) {
  const erro = validarNoNavegador(confirmar);
  if (erro) {
    mostrarMensagem(erro, "erro");
    return;
  }

  const corpo = { confirmar, iva: lerLinhasIva() };
  for (const campo of CAMPOS_TEXTO) corpo[campo] = formulario[campo].value.trim() || null;
  for (const campo of CAMPOS_NUMERO) corpo[campo] = numero(formulario[campo]);
  // Campos de texto obrigatórios no modelo: enviar texto vazio em vez de null
  for (const campo of ["fornecedor", "nif_fornecedor", "numero_fatura"]) corpo[campo] ??= "";

  mostrarMensagem("A guardar…");
  try {
    const resposta = await fetch(`/api/faturas/${idFatura}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpo),
    });
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível guardar."));
    preencher(await resposta.json());
    mostrarMensagem(confirmar ? "Dados confirmados." : "Alterações guardadas.", "sucesso");
    if (confirmar) await sugerirProxima();
  } catch (erro) {
    mostrarMensagem(erro.message, "erro");
  }
}

// Depois de confirmar, oferece a próxima fatura por rever (poupa cliques a quem tem muitas)
async function sugerirProxima() {
  try {
    const resposta = await fetch("/api/faturas?estado=por%20rever");
    if (!resposta.ok) return;
    const outra = (await resposta.json()).find((f) => f.id !== idFatura);
    proxima.innerHTML = "";
    if (outra) {
      const ligacao = document.createElement("a");
      ligacao.href = `/faturas/${outra.id}`;
      ligacao.textContent = `Rever a próxima: ${outra.fornecedor || outra.nome_original} →`;
      proxima.appendChild(ligacao);
    } else {
      proxima.textContent = "Não há mais faturas por rever.";
    }
    proxima.hidden = false;
  } catch {
    // Sugestão opcional: ignorar falhas
  }
}

// O pagamento grava logo, sem precisar de "Guardar" (não altera os dados da fatura)
caixaPaga.addEventListener("change", async () => {
  try {
    const resposta = await fetch(`/api/faturas/${idFatura}/pagamento`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paga: caixaPaga.checked }),
    });
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível atualizar o pagamento."));
    mostrarMensagem(caixaPaga.checked ? "Marcada como paga." : "Marcada como por pagar.", "sucesso");
  } catch (erro) {
    caixaPaga.checked = !caixaPaga.checked; // volta atrás para não mostrar um estado falso
    mostrarMensagem(erro.message, "erro");
  }
});

formulario.addEventListener("input", conferir);
formulario.addEventListener("submit", (e) => {
  e.preventDefault();
  gravar(true);
});
document.getElementById("guardar").addEventListener("click", () => gravar(false));
document.getElementById("adicionar-iva").addEventListener("click", () => {
  adicionarLinhaIva().querySelector(".taxa").focus();
});

carregar();
