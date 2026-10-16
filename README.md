# APP FIBER CABLE v0.4 (protótipo)

App web local em Python que lê ficheiros OTDR `.sor` (Telcordia/Bellcore v1 e v2), faz o pós-processamento como um técnico faria e gera o Dossier de recette em Excel.

## Como arrancar

1. Instalar Python 3.10 ou mais recente (no Windows marcar "Add Python to PATH").
2. Copiar esta pasta para o PC e fazer duplo clique em `iniciar.bat` (Windows) ou correr `./iniciar.sh` (Linux/Mac).
3. O browser abre em http://127.0.0.1:5000. Arrastar os `.sor` para a página.

## Versão para telemóvel

A pasta `docs/` é a mesma app em ficheiros estáticos: o cálculo corre no browser do telemóvel (Pyodide, Python em WebAssembly), sem servidor, e as medidas não saem do aparelho. O GitHub Pages publica-a em https://jwpolinice-lang.github.io/fiber-cable/. Basta abrir o endereço no telemóvel e escolher "Adicionar ao ecrã inicial". Depois da primeira abertura funciona sem rede.

- Os .sor escolhem-se no seletor de ficheiros do telemóvel (Dropbox, Ficheiros, Drive) ou num .zip.
- O Excel gerado guarda-se ou partilha-se (Dropbox, email) com um toque.
- Depois de mudar o código, correr `python build_telemovel.py` para regenerar a pasta.
- Teste num Chromium com ecrã de telemóvel: 288 ficheiros em 29 s, recálculo em 4 s, Excel igual ao da versão de PC.

## O que faz

- Lê cabeçalho (cabo, origem, extremidade, OTDR, módulo, índice, impulso, rétrodiffusion), eventos e o traço.
- Deteta medidas em bouclage (fibra A em OE, bouclage, fibra B em EO no mesmo ficheiro) ou OE/EO em ficheiros separados. Aceita bouclage direta (um evento a meio) e bouclage por bobine (dois conectores simétricos a meio); o tipo é decidido pela maioria dos ficheiros.
- Nos ficheiros Viavi lê os valores precisos guardados pelo OTDR (perda, réflectance, posição, pente), que é o que o FiberCable usa para médias e arredondamentos.
- Número da fibra tirado do nome do ficheiro (`OPEX2707-007-1310.sor` -> 7), corrigível na página.
- Alinha os eventos de todas as medidas (fibras, sentidos e comprimentos de onda) em pontos comuns por distância à origem, com tolerância configurável. Mostra em que medidas cada ponto foi detetado e permite ignorar eventos fantasma.
- Calcula médias OE/EO, diferença 1550-1310, pentes por troço, réflectances, bilan de liaison e valores hors normes com limiares configuráveis.
- Eventos que o OTDR não marcou numa medida (mas que existem noutra fibra, sentido ou comprimento de onda) são calculados na curva pelo método LSA de duas retas, e as perdas dos eventos vizinhos e as pentes são recalculadas. Também funciona quando falta o evento de bouclage.
- Procura na curva degraus e picos reflexivos que nenhuma medida marcou e propõe-nos como sugestões.
- Botão "+ Adicionar evento": clicar na curva acrescenta um ponto, calculado em todas as medidas.
- Ficheiros só com curva (sem tabela de eventos) têm os eventos detetados automaticamente.
- Pontos com nomes por defeito Connecteur O, Epissure N° 1, 2... e Connecteur E, editáveis na página.
- Uma epissure reflexiva fica hors normes como "Réflectance N° k", como no FiberCable.
- Bilan = média dos bilans OE e EO, cada um com a soma das perdas mais pente x comprimentos desse sentido.
- Permite corrigir à mão perdas OE/EO e dar nome aos pontos (BPE, baie...).
- No Excel os valores fora dos limiares ficam a vermelho e os calculados na curva em itálico azul.
- Exporta Excel com as folhas do Dossier: Titre, Table Matières, Informations Générales, Informations Mesure, Epissures, Réflectances, Pentes, Hors Normes.

## Estrutura

- `fibercable/sor.py`: leitor de `.sor`
- `fibercable/lsa.py`: cálculo LSA, pentes e deteção de eventos na curva
- `fibercable/analysis.py`: separação de sentidos, alinhamento e cálculos
- `fibercable/report.py`: geração do Excel
- `app.py` + `static/index.html`: servidor e interface

## Validação

Com os 4 ficheiros do cabo OPEX2707 (fibras 7 e 8, 1310/1550, Viavi MTS 4000) os valores de perdas OE/EO, réflectances, pentes, distâncias cumulativas e hors normes coincidem com o Dossier.xls do FiberCable. Diferenças de 0,001 dB podem aparecer em médias que caem exatamente a meio (arredondamento).

O cálculo LSA reproduz as perdas do OTDR a ±0,003 dB quando usa as janelas gravadas no ficheiro, e a ±0,01 dB quando um evento é retirado e recalculado sem elas. A réflectance calculada está a ±0,1 dB da do OTDR nestes ficheiros.

Cabo OPEX4653 (fibras 145 a 288, 288 ficheiros em bouclage por bobine): perdas OE/EO, médias, pentes, réflectances e hors normes 100% iguais ao Dossier do FiberCable; bilan com diferença mediana de 0,001 dB e máxima de 0,009 dB.
