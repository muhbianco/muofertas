# Marca MegaPromoOz

Escolhida em 10/10/2026: **conceito 1, etiqueta mágica**. Os conceitos 2 (lupa) e 3 (selo) ficam
aqui como histórico.

## Paleta

| Uso | Cor |
|---|---|
| Esmeralda escuro (texto, contraste) | `#0A4D3C` |
| Esmeralda (fundo do ícone) | `#0E8A62` |
| Esmeralda claro (texto sobre escuro) | `#2BB683` |
| Ouro ("Oz", brilho) | `#F2A900` |
| Creme (fundos claros) | `#FFF8E7` |

A paleta fica longe do amarelo do ML (`#FFE600`) e do laranja da Shopee (`#EE4D2D`). Nada de
marca ou logo de marketplace na identidade (termos da Shopee, §2.3a).

## Onde usar cada PNG (`png/`)

| Arquivo | Uso |
|---|---|
| `icone-512.png` / `icone-1024.png` | Logo do app no DevCenter do ML; avatar genérico |
| `grupo-ferramentas-640.png`, `grupo-maquiagem-640.png` | Foto dos grupos no WhatsApp (640×640, recorte redondo) |
| `logo-claro-1440x400.png` / `logo-escuro-1440x400.png` | Página de convite, documentos, banners |
| `logo-transparente-texto-escuro-*` / `-texto-claro-*` | Sobre fundo próprio: o texto escuro vai sobre fundo claro, e vice-versa |
| `favicon-32.png`, `favicon-48.png` | Aba do navegador do painel |
| `apple-touch-icon-180.png`, `icone-192.png`, `icone-maskable-512.png` | Atalho do painel na tela inicial (PWA) |

## Como refazer
```bash
python -I brand/gerar_marca.py
```

```bash
python -I brand/exportar_png.py
```

O primeiro comando gera os SVGs e o `preview.html`. O segundo gera os PNGs com o Chrome/Edge
instalado, em modo headless e com perfil temporário.

O texto usa Arial Black e ainda não foi convertido em curvas. Os PNGs saem certos porque são
gerados nesta máquina, mas o SVG pode variar em outro computador. Se a marca for para gráfica ou
virar SVG público, converter o texto em curvas antes.
