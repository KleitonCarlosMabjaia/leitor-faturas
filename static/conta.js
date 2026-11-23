// Dobra - a minha conta: verificação em dois passos, palavra-passe e sessões abertas

const formatoDataHora = new Intl.DateTimeFormat("pt-PT", { dateStyle: "short", timeStyle: "short" });
let dadosConta = null;

async function carregarConta() {
  dadosConta = await obter("/api/conta");
  document.getElementById("conta-resumo").textContent =
    `${dadosConta.nome} · ${dadosConta.email} · ${dadosConta.papel === "admin" ? "Administrador" : "Membro"} de ${dadosConta.empresa}`;
  mostrarDoisPassos();
  mostrarSessoes();
  document.getElementById("alertas-email").checked = dadosConta.alertas_email;
  document.getElementById("nota-email").hidden = dadosConta.email_configurado;
  if (dadosConta.modo_demo) {
    for (const el of document.querySelectorAll("main button, main input")) el.disabled = true;
    notificar("Na demonstração a conta não pode ser alterada.", "info");
  }
}

// ---------- Verificação em dois passos ----------

function mostrarDoisPassos() {
  const zona = document.getElementById("dois-passos");
  const estado = document.getElementById("estado-dois-passos");
  estado.replaceChildren(dadosConta.dois_passos
    ? criarEtiqueta("Ligada", "ok", "check")
    : criarEtiqueta("Desligada", "aviso", "alerta"));
  if (dadosConta.dois_passos) {
    const restantes = dadosConta.codigos_restantes;
    preencher(zona,
      h("p", { class: "ajuda" }, `Códigos de recuperação por usar: ${restantes} de 8. `,
        restantes <= 2 ? h("strong", { texto: "Gere novos códigos." }) : null),
      h("div", { class: "acoes-linha" },
        botao("Gerar novos códigos de recuperação", gerarCodigos, "botao secundario", "atualizar"),
        botao("Desligar", desligarDoisPassos, "botao fantasma")));
  } else {
    preencher(zona, h("div", { class: "acoes-linha" },
      botao("Ligar a verificação em dois passos", ligarDoisPassos, "botao", "cadeado")));
  }
}

function campoPalavraPasse(id) {
  return campoJanela(id, "Confirme a sua palavra-passe",
    h("input", { type: "password", id, autocomplete: "current-password", required: true }));
}

function ligarDoisPassos() {
  janelaTemporaria("Ligar a verificação em dois passos", h("div", {},
    h("p", { class: "ajuda", texto: "Por segurança, confirme primeiro a sua palavra-passe." }),
    campoPalavraPasse("dp-palavra-passe")), async () => {
    const r = await enviar("/api/conta/dois-passos/iniciar", { palavra_passe: document.getElementById("dp-palavra-passe").value });
    setTimeout(() => mostrarQr(r), 0);
  }, "Continuar");
}

function mostrarQr(r) {
  const codigo = h("input", {
    id: "dp-codigo", inputmode: "numeric", autocomplete: "one-time-code", maxlength: "6", pattern: "[0-9]{6}",
    class: "campo-codigo", required: true, placeholder: "000000",
  });
  janelaTemporaria("Ler o código QR", h("div", { class: "passos-qr" },
    h("ol", {},
      h("li", { texto: "Abra a app autenticadora no telemóvel e escolha adicionar conta." }),
      h("li", { texto: "Leia este código QR (ou escreva a chave abaixo)." }),
      h("li", { texto: "Escreva o código de 6 dígitos que a app mostra." })),
    h("img", { src: r.qr, alt: "Código QR para a app autenticadora", class: "imagem-qr", width: "200", height: "200" }),
    h("p", { class: "ajuda" }, "Chave: ", h("code", { class: "chave-totp", texto: r.segredo.replace(/(.{4})/g, "$1 ").trim() })),
    campoJanela("dp-codigo", "Código da app", codigo)), async () => {
    const res = await enviar("/api/conta/dois-passos/ativar", { codigo: codigo.value });
    await carregarConta();
    setTimeout(() => mostrarCodigos(res.codigos_recuperacao, true), 0);
  }, "Ligar");
  setTimeout(() => codigo.focus(), 50);
}

function mostrarCodigos(codigos, novo) {
  const texto = codigos.join("\n");
  const descarregar = h("a", {
    class: "botao secundario", download: "dobra-codigos-de-recuperacao.txt",
    href: URL.createObjectURL(new Blob([`Dobra - códigos de recuperação\n${dadosConta.email}\n\n${texto}\n`], { type: "text/plain" })),
  }, icone("descarregar"), "Descarregar");
  const copiar = botao("Copiar", async () => {
    await navigator.clipboard.writeText(texto);
    notificar("Códigos copiados.", "ok");
  }, "botao secundario", "copiar");
  janelaTemporaria(novo ? "Verificação em dois passos ligada" : "Novos códigos de recuperação", h("div", {},
    h("p", { class: "ajuda", texto: "Guarde estes códigos num sítio seguro. Cada um serve uma vez para entrar se perder o telemóvel. Não voltam a ser mostrados." }),
    h("ul", { class: "codigos-recuperacao" }, codigos.map((c) => h("li", {}, h("code", { texto: c })))),
    h("div", { class: "acoes-linha" }, copiar, descarregar)), async () => {}, "Já guardei os códigos");
}

function gerarCodigos() {
  janelaTemporaria("Gerar novos códigos de recuperação", h("div", {},
    h("p", { class: "ajuda", texto: "Os códigos antigos deixam de funcionar." }),
    campoPalavraPasse("dp-palavra-passe")), async () => {
    const r = await enviar("/api/conta/dois-passos/codigos", { palavra_passe: document.getElementById("dp-palavra-passe").value });
    await carregarConta();
    setTimeout(() => mostrarCodigos(r.codigos_recuperacao, false), 0);
  }, "Gerar");
}

function desligarDoisPassos() {
  janelaTemporaria("Desligar a verificação em dois passos", h("div", {},
    h("p", { class: "ajuda", texto: "A conta fica protegida só pela palavra-passe." }),
    campoPalavraPasse("dp-palavra-passe"),
    campoJanela("dp-codigo", "Código da app ou de recuperação",
      h("input", { id: "dp-codigo", autocomplete: "one-time-code", maxlength: "12", required: true }))), async () => {
    await enviar("/api/conta/dois-passos/desativar", {
      palavra_passe: document.getElementById("dp-palavra-passe").value, codigo: document.getElementById("dp-codigo").value,
    });
    notificar("Verificação em dois passos desligada.", "aviso");
    await carregarConta();
  }, "Desligar");
}

// ---------- Sessões ----------

function mostrarSessoes() {
  const lista = document.getElementById("sessoes");
  const sessoes = dadosConta.sessoes;
  document.getElementById("terminar-outras").hidden = !sessoes.some((s) => !s.atual);
  if (!sessoes.length) {
    preencher(lista, h("li", { class: "ajuda", texto: "Sem sessões para mostrar." }));
    return;
  }
  preencher(lista, sessoes.map((s) => h("li", { class: "sessao" },
    h("span", { class: "sessao-icone" }, icone(/iPhone|Android/.test(s.dispositivo) ? "telemovel" : "computador")),
    h("div", { class: "sessao-texto" },
      h("strong", {}, s.dispositivo, s.atual ? " " : null, s.atual ? criarEtiqueta("Este dispositivo", "ok") : null),
      h("span", { class: "ajuda", texto: `Último uso ${formatoDataHora.format(new Date(s.ultimo_uso))}${s.ip ? ` · ${s.ip}` : ""} · desde ${formatoDataHora.format(new Date(s.criada_em))}` })),
    s.atual ? null : botao("Terminar", async () => {
      await enviar(`/api/conta/sessoes/${s.id}/terminar`);
      notificar("Sessão terminada.", "ok");
      await carregarConta();
    }))));
}

// ---------- Palavra-passe ----------

function iniciarPalavraPasse() {
  const form = document.getElementById("form-palavra-passe");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const atualCampo = document.getElementById("pp-atual");
    const novaCampo = document.getElementById("pp-nova");
    if (!atualCampo.value || !novaCampo.value) {
      notificar("Preencha a palavra-passe atual e a nova.", "erro");
      return;
    }
    const b = form.querySelector("button");
    ocupado(b, true);
    try {
      await enviar("/api/conta/palavra-passe", { atual: atualCampo.value, nova: novaCampo.value });
      form.reset();
      notificar("Palavra-passe mudada. As outras sessões foram terminadas.", "ok");
      await carregarConta();
    } catch (erro) {
      notificar(erro.message, "erro");
    } finally {
      ocupado(b, false);
    }
  });
  botaoVerPalavraPasse(document.getElementById("pp-nova"));
  document.getElementById("terminar-outras").addEventListener("click", async () => {
    const ok = await confirmar({ titulo: "Terminar as outras sessões?", texto: "Os outros dispositivos terão de voltar a entrar.", botao: "Terminar" });
    if (!ok) return;
    const r = await enviar("/api/conta/sessoes/terminar-outras");
    notificar(`${r.terminadas} sessão(ões) terminada(s).`, "ok");
    await carregarConta();
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  iniciarPalavraPasse();
  document.getElementById("alertas-email").addEventListener("change", async (e) => {
    try {
      await enviar("/api/conta/preferencias", { alertas_email: e.target.checked }, "PUT");
      notificar(e.target.checked ? "Alertas de prazos ligados." : "Alertas de prazos desligados.", "ok");
    } catch (erro) {
      e.target.checked = !e.target.checked;
      notificar(erro.message, "erro");
    }
  });
  try {
    await carregarConta();
  } catch (erro) {
    notificar(erro.message, "erro");
  }
});
