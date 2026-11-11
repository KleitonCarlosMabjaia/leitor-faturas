// Aplica o tema escolhido (claro/escuro) antes de a página aparecer, para não "piscar".
// Corre no <head>; sem escolha guardada, segue o sistema operativo.
(function () {
  try {
    var tema = localStorage.getItem("tema");
    if (tema === "claro" || tema === "escuro") document.documentElement.dataset.tema = tema;
  } catch (e) {
    // Sem acesso ao armazenamento (janela privada, por exemplo): segue o sistema
  }
})();
