// Funções partilhadas pelas páginas (formatação e etiquetas de estado)

const formatoData = new Intl.DateTimeFormat("pt-PT", { day: "2-digit", month: "2-digit", year: "numeric" });

function formatarData(iso) {
  // "T00:00" força a hora local; sem isso a data é lida em UTC e pode recuar um dia
  return iso ? formatoData.format(new Date(`${iso}T00:00`)) : "—";
}

function formatarValor(valor, moeda) {
  if (valor === null || valor === undefined) return "—";
  try {
    return new Intl.NumberFormat("pt-PT", { style: "currency", currency: moeda || "EUR" }).format(valor);
  } catch {
    // Código de moeda desconhecido: mostra o número e o código tal como vieram
    return `${valor.toLocaleString("pt-PT", { minimumFractionDigits: 2 })} ${moeda}`;
  }
}

const ORIGENS = {
  qr: "lida do QR",
  ia: "lida por IA",
  "qr+ia": "lida do QR e por IA",
  "qr+cache": "lida do QR, fornecedor conhecido",
};

// Traduz o estado da fatura numa etiqueta (texto + cor) e nas notas a mostrar
function descreverEstado(f) {
  switch (f.estado_extracao) {
    case "sem_texto":
      return { etiqueta: "Digitalizada", classe: "erro", notas: [f.erro], podeLer: false };
    case "erro":
      return { etiqueta: "Erro na leitura", classe: "erro", notas: [f.erro], podeLer: true };
    case "pendente":
      return { etiqueta: "Por ler", classe: "neutro", notas: [], podeLer: true };
  }
  const alertas = f.alertas || [];
  // Lida só pelo QR: a IA falhou, vale a pena tentar outra vez
  const podeLer = f.origem === "qr";
  if (f.estado === "confirmada") {
    return { etiqueta: "Confirmada", classe: "ok", notas: alertas, podeLer: false };
  }
  return { etiqueta: "Por rever", classe: alertas.length ? "aviso" : "neutro", notas: alertas, podeLer };
}

// Lê a mensagem de erro do FastAPI (texto simples ou lista de erros de validação)
async function lerErro(resposta, predefinida) {
  const corpo = await resposta.json().catch(() => ({}));
  if (typeof corpo.detail === "string") return corpo.detail;
  if (Array.isArray(corpo.detail)) return "Há campos com valores inválidos. Verifique as datas e os números.";
  return predefinida;
}

// ---------- Sessão ----------

// Igual ao fetch, mas se a sessão tiver terminado (401) envia para a página de login
async function api(url, opcoes) {
  const resposta = await fetch(url, opcoes);
  if (resposta.status === 401) {
    location.href = `/entrar?voltar=${encodeURIComponent(location.pathname)}`;
    throw new Error("A sua sessão terminou.");
  }
  return resposta;
}

// Preenche a zona da conta no menu: nome, empresa, "Equipa" (só admins) e "Sair"
async function iniciarMenu() {
  const zona = document.getElementById("conta");
  if (!zona) return;
  try {
    const resposta = await api("/api/auth/eu");
    if (!resposta.ok) return;
    const eu = await resposta.json();
    zona.innerHTML = "";

    const menu = document.querySelector(".menu");
    if (eu.papel === "admin" && !eu.modo_demo && !menu.querySelector('a[href="/equipa"]')) {
      const equipa = document.createElement("a");
      equipa.href = "/equipa";
      equipa.textContent = "Equipa";
      if (location.pathname === "/equipa") equipa.setAttribute("aria-current", "page");
      menu.insertBefore(equipa, zona);
    }

    const quem = document.createElement("span");
    quem.className = "quem";
    quem.textContent = eu.modo_demo ? "Demonstração" : `${eu.nome} · ${eu.empresa}`;
    zona.appendChild(quem);

    if (!eu.modo_demo) {
      const sair = document.createElement("button");
      sair.type = "button";
      sair.className = "acao";
      sair.textContent = "Sair";
      sair.addEventListener("click", async () => {
        await fetch("/api/auth/sair", { method: "POST" });
        location.href = "/entrar";
      });
      zona.appendChild(sair);
    }
  } catch {
    // Sem menu de conta a página continua a funcionar
  }
}

document.addEventListener("DOMContentLoaded", iniciarMenu);
