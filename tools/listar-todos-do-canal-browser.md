abrir a tela de membros do crupo no gchat, deixar a tela no topo, abrir o console no (f12) e colar o codigo a seguir:

ps: rola a pagina de vagar ate o fim para capturar os membros, ao terminar de rolar digite: "parar()" e ENTER

console.clear();
console.log("🟢 MODO DE CAPTURA JSON ATIVADO (RADAR) 🟢");
console.log("1. Vá para a janela de membros do chat.");
console.log("2. ROLE A LISTA DEVAGAR DE CIMA ATÉ O FINAL.");
console.log("3. Quando chegar no último membro, volte ao console e digite: parar()");

// Usando um objeto comum para facilitar a conversão para JSON depois
window.membrosGoogleChat = {};

window.intervaloCaptura = setInterval(() => {
    let elementos = document.querySelectorAll('[data-member-id], [data-hovercard-id], [data-user-id]');
    
    elementos.forEach(el => {
        // Ignora qualquer coisa no menu lateral
        if (el.closest('[role="navigation"]') || el.closest('nav') || el.closest('[aria-label*="Navegação"]')) return;
        
        let idText = el.getAttribute('data-member-id') || el.getAttribute('data-hovercard-id') || el.getAttribute('data-user-id');
        
        // Ignora salas e grupos
        if (!idText || idText.includes('space/')) return;
        
        // Pega APENAS os números do ID (tira o "users/") para bater com o seu exemplo
        let match = idText.match(/\d+/);
        if (!match) return;
        let idNumerico = match[0];
        
        // Pega o nome limpo
        let nomeEl = el.querySelector('span[dir="auto"]') || el.querySelector('span') || el;
        let textos = nomeEl.innerText ? nomeEl.innerText.trim().split('\n') : [];
        let nome = textos.length > 0 ? textos[0].trim() : el.getAttribute('aria-label');
        
        // Adiciona ao objeto se for um nome válido
        if (nome && nome !== "Membro" && !nome.includes("Proprietário") && !nome.includes("Adicionar") && nome !== "Convidado") {
            // Se o nome ainda não estiver no objeto, adiciona
            if (!window.membrosGoogleChat[nome]) {
                window.membrosGoogleChat[nome] = idNumerico;
                console.log(`🎣 Pescado: ${nome}`);
            }
        }
    });
}, 500);

window.parar = function() {
    clearInterval(window.intervaloCaptura);
    console.clear();
    
    let total = Object.keys(window.membrosGoogleChat).length;
    console.log(`=== 🏆 ${total} MEMBROS EXTRAÍDOS COM SUCESSO ===\n`);
    
    // Converte o objeto do Javascript para um texto JSON formatado (com indentação de 2 espaços)
    let jsonFinal = JSON.stringify(window.membrosGoogleChat, null, 2);
    
    // Imprime o JSON na tela
    console.log(jsonFinal);
    
    console.log("\n✅ Fim da extração! O código JSON gerado acima está pronto para copiar.");
};
