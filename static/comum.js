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
