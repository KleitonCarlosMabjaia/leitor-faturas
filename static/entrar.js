// Dobra - página entrar

const formulario = document.getElementById("formulario");
const mensagem = document.getElementById("mensagem");
const campoCodigo = document.getElementById("campo-codigo");
let pedirCodigo = false;
botaoVerPalavraPasse(formulario.palavra_passe);

function erro(texto) {
  mensagem.replaceChildren(icone("erro"), texto);
}

// Só aceita destinos dentro da própria aplicação ("/x"), nunca "//outro-site.com"
function destinoSeguro() {
  const voltar = new URLSearchParams(location.search).get("voltar") || "/";
  return voltar.startsWith("/") && !voltar.startsWith("//") ? voltar : "/";
}

formulario.addEventListener("submit", async (e) => {
  e.preventDefault();
  mensagem.replaceChildren();
  if (!formulario.email.value.trim() || !formulario.palavra_passe.value) {
    erro("Preencha o email e a palavra-passe.");
    (formulario.email.value.trim() ? formulario.palavra_passe : formulario.email).focus();
    return;
  }
  const botao = formulario.querySelector('button[type="submit"]');
  ocupado(botao, true);
  try {
    const resposta = await fetch("/api/auth/entrar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: formulario.email.value, palavra_passe: formulario.palavra_passe.value,
        codigo: pedirCodigo ? formulario.codigo.value : null,
      }),
    });
    // Conta com verificação em dois passos: a palavra-passe está certa, falta o código
    if (resposta.status === 401 && resposta.headers.get("X-Dois-Passos") === "necessario") {
      pedirCodigo = true;
      campoCodigo.hidden = false;
      formulario.email.readOnly = true;
      formulario.codigo.focus();
      ocupado(botao, false);
      return;
    }
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível entrar."));
    location.href = destinoSeguro();
  } catch (falha) {
    erro(falha.message);
    if (pedirCodigo) {
      formulario.codigo.value = "";
      formulario.codigo.focus();
    } else {
      formulario.palavra_passe.value = "";
      formulario.palavra_passe.focus();
    }
    ocupado(botao, false);
  }
});

// ---------- Esqueci-me da palavra-passe (só com o envio de emails configurado) ----------
const formRecuperar = document.getElementById("form-recuperar");
const sucessoRecuperar = document.getElementById("sucesso-recuperar");
const tituloEntrar = document.querySelector(".cartao-acesso h1");

function modoRecuperar(sim) {
  formulario.hidden = sim;
  formRecuperar.hidden = !sim;
  sucessoRecuperar.hidden = true;
  tituloEntrar.textContent = sim ? "Recuperar a palavra-passe" : "Entrar";
  document.querySelector(".cartao-acesso .subtitulo").hidden = sim;
  if (sim) {
    formRecuperar.email.value = formulario.email.value;
    formRecuperar.email.focus();
  } else {
    formulario.email.focus();
  }
}

fetch("/api/auth/opcoes").then((r) => (r.ok ? r.json() : {})).then((o) => {
  document.getElementById("ligacao-registo").hidden = !o.registo_aberto;
  if (!o.recuperacao_por_email) return;
  document.getElementById("esqueci").hidden = false;
  document.getElementById("nota-acesso").textContent =
    "O acesso é feito por convite. Para criar uma conta nova, fale com o administrador da sua empresa.";
}).catch(() => {});

document.getElementById("esqueci").addEventListener("click", () => modoRecuperar(true));
document.getElementById("voltar-entrar").addEventListener("click", () => modoRecuperar(false));
document.getElementById("voltar-entrar-2").addEventListener("click", () => modoRecuperar(false));

formRecuperar.addEventListener("submit", async (e) => {
  e.preventDefault();
  const aviso = document.getElementById("mensagem-recuperar");
  aviso.replaceChildren();
  if (!formRecuperar.email.value.trim()) {
    aviso.replaceChildren(icone("erro"), "Indique o email da sua conta.");
    formRecuperar.email.focus();
    return;
  }
  const b = formRecuperar.querySelector('button[type="submit"]');
  ocupado(b, true);
  try {
    const r = await fetch("/api/auth/recuperar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: formRecuperar.email.value }),
    });
    if (!r.ok) throw new Error(await lerErro(r, "Não foi possível enviar o link."));
    formRecuperar.hidden = true;
    sucessoRecuperar.hidden = false;
    document.getElementById("voltar-entrar-2").focus();
  } catch (falha) {
    aviso.replaceChildren(icone("erro"), falha.message);
  } finally {
    ocupado(b, false);
  }
});
