// Leitor de Faturas - página de revisão: PDF de um lado, campos editáveis do outro

const idFatura = location.pathname.split("/").pop();

const formulario = document.getElementById("formulario");
const linhasIva = document.getElementById("linhas-iva");
const modeloLinha = document.getElementById("modelo-linha-iva");
const conferencia = document.getElementById("conferencia");
const caixaPaga = document.getElementById("paga");
const estadoGravacao = document.getElementById("estado-gravacao");
const botaoConfirmar = document.getElementById("confirmar");
const botaoGuardar = document.getElementById("guardar");

const CAMPOS_TEXTO = ["fornecedor", "nif_fornecedor", "numero_fatura", "data_emissao", "data_vencimento", "moeda"];
const CAMPOS_NUMERO = ["valor_sem_iva", "total"];
const TOLERANCIA = 0.02; // a mesma do servidor

let sujo = false;          // há alterações por guardar?
let aGravar = false;
let proximaPorRever = null;

const numero = (input) => (input.value === "" ? null : Number(input.value));

// ---------- Alterações por guardar ----------

function marcarSujo(valor) {
  sujo = valor;
  estadoGravacao.classList.toggle("sujo", valor);
  estadoGravacao.textContent = valor ? "Alterações por guardar" : "Tudo guardado";
}

// Avisa antes de sair da página com alterações por guardar
window.addEventListener("beforeunload", (e) => {
  if (sujo) e.preventDefault();
});

// ---------- Erros junto a cada campo ----------

function limparErros() {
  for (const erro of formulario.querySelectorAll(".erro-campo")) erro.remove();
  for (const campo of formulario.querySelectorAll('[aria-invalid="true"]')) {
    campo.removeAttribute("aria-invalid");
    campo.removeAttribute("aria-describedby");
  }
}

function erroNoCampo(campo, texto) {
  campo.setAttribute("aria-invalid", "true");
  const erro = document.createElement("span");
  erro.className = "erro-campo";
  erro.id = `erro-${campo.id || campo.className}`;
  erro.append(icone("erro"), texto);
  campo.setAttribute("aria-describedby", erro.id);
  (campo.closest(".campo") || campo.closest("td") || campo.parentElement).appendChild(erro);
}

// ---------- Linhas de IVA ----------

function adicionarLinhaIva(linha = { taxa: "", base: "", valor: "" }) {
  const tr = modeloLinha.content.firstElementChild.cloneNode(true);
  tr.querySelector(".taxa").value = linha.taxa;
  tr.querySelector(".base").value = linha.base;
  tr.querySelector(".valor").value = linha.valor;
  tr.querySelector(".remover-celula").appendChild(
    botaoIcone("x", "Remover esta taxa", () => {
      tr.remove();
      marcarSujo(true);
      conferir();
    }, "perigo pequeno")
  );
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
    conferencia.replaceChildren();
    conferencia.className = "conferencia";
    return;
  }
  const somaIva = lerLinhasIva().reduce((soma, l) => soma + (l.valor || 0), 0);
  const calculado = semIva + somaIva;
  const diferenca = Math.round((total - calculado) * 100) / 100;

  const texto = `${formatarValor(semIva)} + IVA ${formatarValor(somaIva)} = ${formatarValor(calculado)}`;
  const confere = Math.abs(diferenca) <= TOLERANCIA;
  conferencia.className = `conferencia ${confere ? "ok" : "erro"}`;
  conferencia.replaceChildren(
    icone(confere ? "check" : "alerta"),
    confere ? `${texto}: confere com o total.` : `${texto}: difere ${formatarValor(diferenca)} do total.`
  );
}

// ---------- Mostrar a fatura ----------

function preencher(f) {
  const estado = descreverEstado(f);
  document.title = `${f.fornecedor || f.nome_original} · Leitor de Faturas`;
  document.getElementById("titulo").textContent = f.fornecedor || "Fornecedor por preencher";
  document.getElementById("subtitulo").textContent = [
    f.numero_fatura && `Fatura ${f.numero_fatura}`,
    f.atcud && f.atcud !== "0" ? `ATCUD ${f.atcud}` : null,
    ORIGENS[f.origem],
  ]
    .filter(Boolean)
    .join(" · ");
  document.getElementById("nome-pdf").textContent = f.nome_original;
  document.getElementById("etiqueta").replaceChildren(criarEtiqueta(estado.etiqueta, estado.classe, estado.icone));

  // Alertas (ou o erro da leitura, se não houve dados)
  const notas = f.alertas?.length ? f.alertas : f.erro ? [f.erro] : [];
  const lista = document.getElementById("lista-alertas");
  lista.replaceChildren(...notas.map((nota) => Object.assign(document.createElement("li"), { textContent: nota })));
  document.getElementById("alertas").hidden = notas.length === 0;

  for (const campo of CAMPOS_TEXTO) formulario[campo].value = f[campo] ?? "";
  for (const campo of CAMPOS_NUMERO) formulario[campo].value = f[campo] ?? "";
  if (!formulario.moeda.value) formulario.moeda.value = "EUR";

  linhasIva.replaceChildren();
  for (const linha of f.iva || []) adicionarLinhaIva(linha);
  if (!f.iva?.length) adicionarLinhaIva({ taxa: 23, base: "", valor: "" });

  caixaPaga.checked = f.paga;

  botaoConfirmar.replaceChildren(icone("check"), f.estado === "confirmada" ? "Guardar e manter confirmada" : "Confirmar dados");
  limparErros();
  conferir();
  marcarSujo(false);
}

async function carregar() {
  const pdfUrl = `/api/faturas/${idFatura}/pdf`;
  document.getElementById("pdf").src = pdfUrl;
  document.getElementById("abrir-pdf").href = pdfUrl;

  try {
    const resposta = await api(`/api/faturas/${idFatura}`);
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível obter a fatura."));
    preencher(await resposta.json());
    procurarProxima();
  } catch (erro) {
    document.getElementById("titulo").textContent = "Fatura não encontrada";
    document.querySelector(".revisao").hidden = true;
    notificar(erro.message, "erro");
  }
}

// Próxima fatura por rever (poupa cliques a quem tem muitas)
async function procurarProxima() {
  try {
    const resposta = await api("/api/faturas?estado=por%20rever");
    if (!resposta.ok) return;
    proximaPorRever = (await resposta.json()).find((f) => f.id !== idFatura) || null;
    const botao = document.getElementById("proxima");
    botao.hidden = !proximaPorRever;
    if (proximaPorRever) {
      botao.href = `/faturas/${proximaPorRever.id}`;
      botao.title = `Próxima por rever: ${proximaPorRever.fornecedor || proximaPorRever.nome_original}`;
    }
  } catch {
    // Sugestão opcional: ignorar falhas
  }
}

// ---------- Guardar / confirmar ----------

function validarNoNavegador(confirmar) {
  // Para guardar um rascunho bastam números e datas válidos; para confirmar, tudo preenchido
  const obrigatorios = confirmar
    ? ["fornecedor", "nif_fornecedor", "numero_fatura", "data_emissao", "valor_sem_iva", "total"]
    : ["data_emissao", "valor_sem_iva", "total"];
  let primeiro = null;
  for (const nome of obrigatorios) {
    const campo = formulario[nome];
    if (!campo.value.trim()) {
      erroNoCampo(campo, "Campo obrigatório.");
      primeiro ??= campo;
    }
  }
  for (const tr of linhasIva.querySelectorAll("tr")) {
    const campos = [...tr.querySelectorAll("input")];
    const preenchidos = campos.filter((c) => c.value !== "");
    if (preenchidos.length && preenchidos.length < campos.length) {
      for (const c of campos.filter((c) => c.value === "")) {
        c.setAttribute("aria-invalid", "true");
        primeiro ??= c;
      }
    }
  }
  if (primeiro) primeiro.focus();
  return primeiro === null;
}

async function gravar(confirmar) {
  if (aGravar) return;
  limparErros();
  if (!validarNoNavegador(confirmar)) {
    notificar(confirmar ? "Preencha os campos assinalados antes de confirmar." : "Corrija os campos assinalados.", "erro");
    return;
  }

  const corpo = { confirmar, iva: lerLinhasIva() };
  for (const campo of CAMPOS_TEXTO) corpo[campo] = formulario[campo].value.trim() || null;
  for (const campo of CAMPOS_NUMERO) corpo[campo] = numero(formulario[campo]);
  // Campos de texto obrigatórios no modelo: enviar texto vazio em vez de null
  for (const campo of ["fornecedor", "nif_fornecedor", "numero_fatura"]) corpo[campo] ??= "";

  const botao = confirmar ? botaoConfirmar : botaoGuardar;
  aGravar = true;
  ocupado(botao, true);
  try {
    const resposta = await api(`/api/faturas/${idFatura}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpo),
    });
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível guardar."));
    preencher(await resposta.json());
    if (confirmar) {
      await procurarProxima();
      notificar(
        proximaPorRever ? "Dados confirmados." : "Dados confirmados. Não há mais faturas por rever.",
        "ok",
        proximaPorRever ? { acao: { texto: "Rever a próxima →", href: `/faturas/${proximaPorRever.id}` }, duracao: 8000 } : {}
      );
    } else {
      notificar("Alterações guardadas.", "ok");
    }
  } catch (erro) {
    notificar(erro.message, "erro");
  } finally {
    ocupado(botao, false);
    aGravar = false;
  }
}

// O pagamento grava logo, sem precisar de "Guardar" (não altera os dados da fatura)
caixaPaga.addEventListener("change", async () => {
  caixaPaga.disabled = true;
  try {
    const resposta = await api(`/api/faturas/${idFatura}/pagamento`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paga: caixaPaga.checked }),
    });
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível atualizar o pagamento."));
    notificar(caixaPaga.checked ? "Marcada como paga." : "Marcada como por pagar.", "ok");
  } catch (erro) {
    caixaPaga.checked = !caixaPaga.checked; // volta atrás para não mostrar um estado falso
    notificar(erro.message, "erro");
  } finally {
    caixaPaga.disabled = false;
  }
});

formulario.addEventListener("input", (e) => {
  // Ao corrigir um campo assinalado, o aviso desaparece
  if (e.target.getAttribute("aria-invalid") === "true") {
    e.target.removeAttribute("aria-invalid");
    e.target.removeAttribute("aria-describedby");
    e.target.closest(".campo")?.querySelector(".erro-campo")?.remove();
  }
  marcarSujo(true);
  conferir();
});
formulario.addEventListener("submit", (e) => {
  e.preventDefault();
  gravar(true);
});
botaoGuardar.addEventListener("click", () => gravar(false));
document.getElementById("adicionar-iva").addEventListener("click", () => {
  adicionarLinhaIva().querySelector(".taxa").focus();
});

// Atalhos de teclado: Ctrl+S guarda, Ctrl+Enter confirma (Cmd no Mac)
document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || document.querySelector("dialog[open]")) return;
  if (e.key === "s" || e.key === "S") {
    e.preventDefault();
    gravar(false);
  } else if (e.key === "Enter") {
    e.preventDefault();
    gravar(true);
  }
});

carregar();
