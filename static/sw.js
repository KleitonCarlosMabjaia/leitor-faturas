// Dobra - service worker: app instalável e funcionamento básico sem rede.
//
// - Ficheiros estáticos (CSS, JS, tipos de letra, ícones): da cache primeiro; têm a versão no
//   endereço, por isso nunca ficam desatualizados.
// - Páginas e dados (GET /api/...): da rede primeiro; sem rede, mostra a última versão guardada
//   (só leitura). Sem nada guardado, a página "Sem ligação".
// - Pedidos que alteram dados nunca passam pela cache.
// - Ao terminar a sessão, a página pede para apagar as páginas e os dados guardados.

const VERSAO = "2026-11-24.14";
const ESTATICOS = `dobra-estaticos-${VERSAO}`;
const PAGINAS = "dobra-paginas";
const DADOS = "dobra-dados";
const PRECACHE = [
  "/offline",
  `/static/site.css?v=${VERSAO}`,  // a página "Sem ligação" usa os estilos das páginas públicas
  "/static/logo.svg",
  "/static/fontes/inter-latin.woff2",
  "/static/icones/icone-192.png",
];
// Nunca guardar: sessão, pagamentos, ficheiros PDF e exportações (podem ser grandes ou sensíveis)
const NAO_GUARDAR = [/^\/api\/auth\//, /^\/api\/stripe\//, /^\/api\/subscricao/, /\/pdf$/, /\/exportar/, /^\/api\/conta(\/|$)/,
  /\/seguranca-social$/, /\.csv$/];

self.addEventListener("install", (evento) => {
  evento.waitUntil(caches.open(ESTATICOS).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil((async () => {
    for (const nome of await caches.keys()) {
      if (nome.startsWith("dobra-estaticos-") && nome !== ESTATICOS) await caches.delete(nome);
    }
    await self.clients.claim();
  })());
});

self.addEventListener("message", (evento) => {
  if (evento.data === "limpar-dados") {
    evento.waitUntil(Promise.all([caches.delete(PAGINAS), caches.delete(DADOS)]));
  }
});

async function redePrimeiro(pedido, cache) {
  try {
    const resposta = await fetch(pedido);
    if (resposta.ok && resposta.type === "basic") {
      const copia = resposta.clone();
      caches.open(cache).then((c) => c.put(pedido, copia));
    }
    return resposta;
  } catch (erro) {
    const guardada = await caches.match(pedido, { cacheName: cache });
    if (guardada) {
      // Marca a resposta como vinda da cache, para a página avisar que está sem ligação
      const cabecalhos = new Headers(guardada.headers);
      cabecalhos.set("X-Dobra-Sem-Rede", "1");
      return new Response(await guardada.blob(), { status: guardada.status, headers: cabecalhos });
    }
    throw erro;
  }
}

self.addEventListener("fetch", (evento) => {
  const pedido = evento.request;
  const url = new URL(pedido.url);
  if (pedido.method !== "GET" || url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/")) {
    evento.respondWith(caches.match(pedido).then((guardada) => guardada || fetch(pedido).then((resposta) => {
      if (resposta.ok) {
        const copia = resposta.clone();
        caches.open(ESTATICOS).then((c) => c.put(pedido, copia));
      }
      return resposta;
    })));
    return;
  }

  if (pedido.mode === "navigate") {
    evento.respondWith(redePrimeiro(pedido, PAGINAS).catch(async () => (await caches.match("/offline")) || Response.error()));
    return;
  }

  if (url.pathname.startsWith("/api/") && !NAO_GUARDAR.some((r) => r.test(url.pathname))) {
    evento.respondWith(redePrimeiro(pedido, DADOS));
  }
});
