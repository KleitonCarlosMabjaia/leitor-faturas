// Dobra - documentação da API pública, gerada a partir do contrato OpenAPI (/api/v1/openapi.json)

(function () {
  const METODOS = ["get", "post", "put", "patch", "delete"];

  function el(tag, props = {}, ...filhos) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") e.className = v;
      else if (k === "texto") e.textContent = v;
      else e.setAttribute(k, v);
    }
    for (const f of filhos.flat()) if (f !== null && f !== undefined && f !== false) e.append(f instanceof Node ? f : String(f));
    return e;
  }

  function resolver(spec, esquema) {
    let s = esquema || {};
    for (let i = 0; i < 5 && s.$ref; i += 1) s = spec.components.schemas[s.$ref.split("/").pop()] || {};
    return s;
  }

  function tipo(spec, esquema) {
    const s = resolver(spec, esquema);
    if (s.anyOf) return s.anyOf.map((x) => tipo(spec, x)).filter((t) => t !== "null").join(" ou ");
    if (s.enum) return s.enum.map((v) => JSON.stringify(v)).join(" | ");
    if (s.type === "array") return `lista de ${tipo(spec, s.items)}`;
    if (s.format === "date") return "data";
    return { string: "texto", integer: "inteiro", number: "número", boolean: "verdadeiro/falso", object: "objeto" }[s.type] || s.type || "objeto";
  }

  function tabelaCampos(titulo, linhas) {
    if (!linhas.length) return null;
    return el("div", {}, el("h4", { texto: titulo }),
      el("table", {}, el("thead", {}, el("tr", {}, el("th", { scope: "col", texto: "Campo" }), el("th", { scope: "col", texto: "Tipo" }), el("th", { scope: "col", texto: "Descrição" }))),
        el("tbody", {}, linhas.map(([nome, t, obrig, desc]) => el("tr", {},
          el("td", {}, el("code", { texto: nome }), obrig ? el("span", { class: "obrigatorio", texto: " obrigatório" }) : null),
          el("td", { texto: t }), el("td", { texto: desc || "" }))))));
  }

  function camposDoCorpo(spec, esquema, prefixo = "") {
    const s = resolver(spec, esquema);
    const obrig = new Set(s.required || []);
    const linhas = [];
    for (const [nome, prop] of Object.entries(s.properties || {})) {
      const p = resolver(spec, prop);
      linhas.push([prefixo + nome, tipo(spec, prop), obrig.has(nome), prop.description || p.description]);
      if (p.type === "array" && resolver(spec, p.items).properties) linhas.push(...camposDoCorpo(spec, p.items, `${prefixo}${nome}[].`));
    }
    return linhas;
  }

  function rota(spec, caminho, metodo, op) {
    const id = `${metodo}-${caminho.replace(/[^a-z0-9]+/gi, "-")}`;
    const params = (op.parameters || []).map((p) => [p.name, tipo(spec, p.schema) + (p.in === "path" ? " (no endereço)" : ""), p.required, p.description || p.schema?.description]);
    const corpo = op.requestBody?.content?.["application/json"]?.schema;
    const exemplo = metodo === "get"
      ? `curl "${location.origin}${caminho}" \\\n  -H "Authorization: Bearer dobra_..."`
      : `curl -X ${metodo.toUpperCase()} "${location.origin}${caminho}" \\\n  -H "Authorization: Bearer dobra_..." \\\n  -H "Content-Type: application/json" \\\n  -d '{...}'`;
    return el("section", { class: "rota-api", id },
      el("h3", {}, el("span", { class: `metodo metodo-${metodo}`, texto: metodo.toUpperCase() }), " ", el("code", { texto: caminho })),
      op.summary ? el("p", { class: "resumo-rota", texto: op.summary }) : null,
      op.description ? el("p", { texto: op.description.replace(/\s+/g, " ").trim() }) : null,
      tabelaCampos("Parâmetros", params),
      corpo ? tabelaCampos("Corpo (JSON)", camposDoCorpo(spec, corpo)) : null,
      el("pre", {}, el("code", { texto: exemplo })));
  }

  async function iniciar() {
    const caixa = document.getElementById("rotas");
    document.getElementById("exemplo-curl").textContent =
      `curl ${location.origin}/api/v1/empresa \\\n  -H "Authorization: Bearer dobra_..."`;
    try {
      const resposta = await fetch("/api/v1/openapi.json");
      if (!resposta.ok) throw new Error();
      const spec = await resposta.json();
      const rotas = [];
      for (const [caminho, ops] of Object.entries(spec.paths)) {
        for (const m of METODOS) if (ops[m]) rotas.push(rota(spec, caminho, m, ops[m]));
      }
      const indice = el("ul", { class: "indice-api" }, rotas.map((r) => el("li", {}, el("a", { href: `#${r.id}` }, r.querySelector("h3").textContent))));
      caixa.replaceChildren(indice, ...rotas);
    } catch {
      caixa.replaceChildren(el("p", { texto: "Não foi possível carregar o contrato da API. Tente recarregar a página." }));
    }
  }

  iniciar();
})();
