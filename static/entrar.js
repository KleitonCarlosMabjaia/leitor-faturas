// Dobra - página entrar

const formulario = document.getElementById("formulario");
const mensagem = document.getElementById("mensagem");
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
      body: JSON.stringify({ email: formulario.email.value, palavra_passe: formulario.palavra_passe.value }),
    });
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível entrar."));
    location.href = destinoSeguro();
  } catch (falha) {
    erro(falha.message);
    formulario.palavra_passe.value = "";
    formulario.palavra_passe.focus();
    ocupado(botao, false);
  }
});
