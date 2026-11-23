// Dobra - página definir palavra passe

// O token vem depois do "#": essa parte do endereço nunca é enviada ao servidor
const token = location.hash.slice(1);
const formulario = document.getElementById("formulario");
const mensagem = document.getElementById("mensagem");
const paraQuem = document.getElementById("para-quem");
botaoVerPalavraPasse(formulario.palavra_passe);

function erro(texto) {
  mensagem.replaceChildren(icone("erro"), texto);
}

// As mesmas regras do servidor, mostradas enquanto se escreve
function avaliar(palavraPasse) {
  const tamanho = palavraPasse.length >= 10;
  const mistura = /\D/.test(palavraPasse) && /[^\p{L}]/u.test(palavraPasse) && palavraPasse.length > 0;
  let nivel = 0;
  if (palavraPasse) nivel = 1;
  if (tamanho && mistura) nivel = 2;
  if (tamanho && mistura && palavraPasse.length >= 14) nivel = 3;
  if (nivel === 3 && /[^\p{L}\p{N}]/u.test(palavraPasse)) nivel = 4;
  return { tamanho, mistura, nivel };
}

formulario.palavra_passe.addEventListener("input", () => {
  const { tamanho, mistura, nivel } = avaliar(formulario.palavra_passe.value);
  document.getElementById("regra-tamanho").classList.toggle("cumprida", tamanho);
  document.getElementById("regra-mistura").classList.toggle("cumprida", mistura);
  document.getElementById("forca").dataset.nivel = nivel;
});

async function verificarLink() {
  if (!token) {
    paraQuem.textContent = "";
    erro("Link incompleto. Copie o link inteiro que recebeu.");
    return;
  }
  const resposta = await fetch("/api/auth/link", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  if (!resposta.ok) {
    paraQuem.textContent = "";
    erro(await lerErro(resposta, "Este link já não é válido."));
    return;
  }
  const dados = await resposta.json();
  paraQuem.textContent = `${dados.nome} (${dados.email}) · ${dados.empresa}`;
  document.getElementById("utilizador").value = dados.email;
  formulario.hidden = false;
  formulario.palavra_passe.focus();
}

formulario.addEventListener("submit", async (e) => {
  e.preventDefault();
  mensagem.replaceChildren();
  const palavraPasse = formulario.palavra_passe.value;
  const { tamanho, mistura } = avaliar(palavraPasse);
  if (!tamanho) return erro("A palavra-passe tem de ter pelo menos 10 caracteres.");
  if (!mistura) return erro("Use letras e números (ou outros símbolos).");
  if (palavraPasse !== formulario.repetir.value) {
    formulario.repetir.focus();
    return erro("As palavras-passe não são iguais.");
  }

  const botao = formulario.querySelector('button[type="submit"]');
  ocupado(botao, true);
  const resposta = await fetch("/api/auth/definir-palavra-passe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, palavra_passe: palavraPasse }),
  });
  if (!resposta.ok) {
    ocupado(botao, false);
    return erro(await lerErro(resposta, "Não foi possível guardar a palavra-passe."));
  }
  // Tira o token do histórico do navegador antes de sair da página
  history.replaceState(null, "", location.pathname);
  location.href = "/";
});

verificarLink();
