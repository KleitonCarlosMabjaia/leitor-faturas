// Dobra - registo online de uma empresa nova (período experimental)

const formRegisto = document.getElementById("formulario");
const avisoRegisto = document.getElementById("mensagem");
botaoVerPalavraPasse(formRegisto.palavra_passe);

// O plano escolhido na página de apresentação vem no endereço (?plano=essencial)
const planoPedido = new URLSearchParams(location.search).get("plano");
if (planoPedido === "essencial" || planoPedido === "contabilidade") {
  formRegisto.querySelector(`input[name=plano][value=${planoPedido}]`).checked = true;
}

function erroRegisto(texto, campo) {
  avisoRegisto.replaceChildren(icone("erro"), texto);
  campo?.focus();
}

formRegisto.addEventListener("submit", async (e) => {
  e.preventDefault();
  avisoRegisto.replaceChildren();
  const f = formRegisto;
  for (const campo of [f.empresa, f.nif, f.nome, f.email, f.palavra_passe]) {
    if (!campo.value.trim()) return erroRegisto("Preencha todos os campos.", campo);
  }
  if (!f.aceito_termos.checked) return erroRegisto("Para criar a conta, aceite os termos de utilização.", f.aceito_termos);
  const b = f.querySelector('button[type="submit"]');
  ocupado(b, true);
  try {
    const plano = f.querySelector("input[name=plano]:checked").value;
    const r = await fetch("/api/registo", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        empresa: f.empresa.value, nif: f.nif.value, nome: f.nome.value, email: f.email.value,
        palavra_passe: f.palavra_passe.value, plano, aceito_termos: true,
      }),
    });
    if (!r.ok) throw new Error(await lerErro(r, "Não foi possível criar a conta."));
    location.href = "/gestao#painel";
  } catch (falha) {
    erroRegisto(falha.message);
    ocupado(b, false);
  }
});
