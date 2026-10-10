"""Gera as propostas de marca da MegaPromoOz em SVG (só biblioteca padrão).

Paleta "Oz": esmeralda + ouro. De propósito longe do amarelo do ML (#FFE600) e do laranja da
Shopee (#EE4D2D): a Shopee veda a marca dela na mídia do afiliado, e o grupo não pode parecer
canal oficial de marketplace.

Uso: python -I brand/gerar_marca.py  → brand/*.svg + brand/preview.html
"""

from __future__ import annotations

import base64
import math
from pathlib import Path

OUT = Path(__file__).resolve().parent
EMERALD_DEEP = "#0A4D3C"
EMERALD = "#0E8A62"
EMERALD_LIGHT = "#2BB683"
GOLD = "#F2A900"
CREAM = "#FFF8E7"
DARK_BG = "#101814"
FONT = "'Arial Black','Segoe UI Black','Helvetica Neue',Arial,sans-serif"
FONT_TAG = "'Segoe UI',Arial,sans-serif"


def sparkle(cx: float, cy: float, r: float, fill: str) -> str:
    return (f'<path d="M{cx},{cy - r} Q{cx},{cy} {cx + r},{cy} Q{cx},{cy} {cx},{cy + r} '
            f'Q{cx},{cy} {cx - r},{cy} Q{cx},{cy} {cx},{cy - r} Z" fill="{fill}"/>')


def svg(w: int, h: int, body: str, title: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img"><title>{title}</title>{body}</svg>\n')


# ---------------------------------------------------------------- conceito 1: etiqueta mágica

def icon_tag() -> str:
    tag = (f'<path d="M168,160 H372 Q392,160 392,180 V332 Q392,352 372,352 H168 L104,256 Z" '
           f'fill="{CREAM}" stroke="{CREAM}" stroke-width="18" stroke-linejoin="round"/>'
           f'<circle cx="160" cy="256" r="17" fill="{EMERALD}"/>'
           f'<text x="270" y="300" text-anchor="middle" font-family="{FONT}" font-weight="900" '
           f'font-size="138" fill="{EMERALD_DEEP}">%</text>')
    return (f'<rect width="512" height="512" rx="112" fill="{EMERALD}"/>'
            f'<g transform="rotate(-14 256 256)">{tag}</g>'
            + sparkle(398, 132, 54, GOLD) + sparkle(438, 214, 22, GOLD))


# ---------------------------------------------------------------- conceito 2: lupa caçadora

def icon_lens() -> str:
    return (f'<rect width="512" height="512" rx="112" fill="{EMERALD_DEEP}"/>'
            f'<line x1="318" y1="318" x2="404" y2="404" stroke="{GOLD}" stroke-width="56" stroke-linecap="round"/>'
            f'<circle cx="228" cy="228" r="128" fill="{EMERALD}" stroke="{GOLD}" stroke-width="36"/>'
            f'<text x="228" y="276" text-anchor="middle" font-family="{FONT}" font-weight="900" '
            f'font-size="132" fill="{CREAM}">%</text>'
            + sparkle(370, 100, 40, CREAM))


# ---------------------------------------------------------------- conceito 3: selo verificado

def rosette(cx: float, cy: float, r_out: float, r_in: float, n: int, fill: str) -> str:
    pts = []
    for i in range(2 * n):
        r = r_out if i % 2 == 0 else r_in
        a = math.pi * i / n - math.pi / 2
        pts.append(f"{cx + r * math.cos(a):.1f},{cy + r * math.sin(a):.1f}")
    return (f'<polygon points="{" ".join(pts)}" fill="{fill}" stroke="{fill}" '
            f'stroke-width="14" stroke-linejoin="round"/>')


def icon_seal() -> str:
    return (f'<rect width="512" height="512" rx="112" fill="{CREAM}"/>'
            + rosette(256, 248, 196, 172, 18, EMERALD)
            + f'<circle cx="256" cy="248" r="138" fill="{CREAM}"/>'
            f'<text x="256" y="300" text-anchor="middle" font-family="{FONT}" font-weight="900" '
            f'font-size="150" fill="{EMERALD_DEEP}">%</text>'
            f'<circle cx="372" cy="372" r="64" fill="{GOLD}" stroke="{CREAM}" stroke-width="14"/>'
            f'<path d="M342,374 L364,396 L404,350" fill="none" stroke="{EMERALD_DEEP}" stroke-width="18" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')


# ---------------------------------------------------------------- logos horizontais

def wordmark(x: int, base: int, tagline: str, dark: bool) -> str:
    main = CREAM if dark else EMERALD_DEEP
    tag_color = EMERALD_LIGHT if dark else EMERALD
    return (f'<text x="{x}" y="{base}" font-family="{FONT}" font-weight="900" font-size="76" '
            f'textLength="372" lengthAdjust="spacingAndGlyphs" fill="{main}">MegaPromo</text>'
            f'<text x="{x + 374}" y="{base}" font-family="{FONT}" font-weight="900" font-size="76" '
            f'textLength="104" lengthAdjust="spacingAndGlyphs" fill="{GOLD}">Oz</text>'
            f'<text x="{x + 3}" y="{base + 42}" font-family="{FONT_TAG}" font-weight="700" font-size="24" '
            f'textLength="478" lengthAdjust="spacingAndGlyphs" fill="{tag_color}">{tagline}</text>')


def lockup(icon: str, tagline: str, dark: bool = False) -> str:
    bg = f'<rect width="720" height="200" rx="24" fill="{DARK_BG if dark else CREAM}"/>'
    return bg + f'<g transform="translate(24,24) scale(0.3125)">{icon}</g>' + wordmark(208, 112, tagline, dark)


def lockup_lens_letter(dark: bool = False) -> str:
    """Conceito 2: o "O" de Oz vira a lente (anel dourado com brilho). Sem cabo: com cabo lia "Qz"."""
    main = CREAM if dark else EMERALD_DEEP
    bg = f'<rect width="640" height="200" rx="24" fill="{DARK_BG if dark else CREAM}"/>'
    return (bg + f'<text x="32" y="118" font-family="{FONT}" font-weight="900" font-size="80" '
            f'textLength="384" lengthAdjust="spacingAndGlyphs" fill="{main}">MegaPromo</text>'
            f'<circle cx="460" cy="92" r="34" fill="none" stroke="{GOLD}" stroke-width="14"/>'
            + sparkle(460, 92, 13, GOLD)
            + f'<text x="516" y="118" font-family="{FONT}" font-weight="900" font-size="80" '
            f'textLength="52" lengthAdjust="spacingAndGlyphs" fill="{GOLD}">z</text>'
            f'<text x="35" y="160" font-family="{FONT_TAG}" font-weight="700" font-size="24" '
            f'textLength="478" lengthAdjust="spacingAndGlyphs" fill="{EMERALD_LIGHT if dark else EMERALD}">'
            f'caçamos o desconto de verdade</text>')


# ---------------------------------------------------------------- fotos dos grupos (recorte em círculo)

def wrench() -> str:
    return (f'<g transform="rotate(45 320 300)">'
            f'<rect x="296" y="250" width="48" height="250" rx="24" fill="{EMERALD_DEEP}"/>'
            f'<circle cx="320" cy="216" r="84" fill="{EMERALD_DEEP}"/>'
            f'<rect x="296" y="120" width="48" height="96" rx="10" fill="{CREAM}"/></g>')


def lipstick() -> str:
    return (f'<rect x="262" y="330" width="116" height="150" rx="16" fill="{EMERALD_DEEP}"/>'
            f'<rect x="272" y="292" width="96" height="46" rx="6" fill="{GOLD}"/>'
            f'<path d="M286,292 V196 Q286,168 312,152 L354,128 V292 Z" fill="{EMERALD}"/>')


def sneaker() -> str:
    """Tênis de lado com linhas de velocidade. Sem swoosh nem três listras: nada de marca alheia."""
    upper = ('M176,374 L170,304 Q166,254 214,248 L262,242 Q282,270 314,278 '
             'L378,302 Q452,322 480,348 Q492,362 486,374 Z')
    laces = "".join(
        f'<line x1="{cx + 6}" y1="{cy - 12}" x2="{cx - 6}" y2="{cy + 12}" stroke="{CREAM}" '
        f'stroke-width="9" stroke-linecap="round"/>'
        for cx, cy in ((302, 282), (328, 293), (354, 304))
    )
    return (f'<rect x="118" y="314" width="40" height="11" rx="5.5" fill="{EMERALD}"/>'
            f'<rect x="130" y="338" width="28" height="11" rx="5.5" fill="{EMERALD}"/>'
            f'<path d="{upper}" fill="{EMERALD_DEEP}"/>' + laces
            + f'<rect x="178" y="262" width="18" height="44" rx="7" fill="{GOLD}"/>'
            f'<rect x="160" y="368" width="334" height="42" rx="21" fill="{GOLD}"/>'
            f'<rect x="184" y="394" width="286" height="7" rx="3.5" fill="{EMERALD_DEEP}" opacity=".35"/>')


def avatar(glyph: str, label: str, font_size: int = 32, text_length: int = 264) -> str:
    return (f'<rect width="640" height="640" fill="{EMERALD}"/>'
            f'<circle cx="320" cy="300" r="214" fill="{CREAM}"/>' + glyph
            + f'<rect x="166" y="500" width="308" height="70" rx="35" fill="{GOLD}"/>'
            f'<text x="320" y="{535 + font_size * 3 // 8}" text-anchor="middle" font-family="{FONT}" '
            f'font-weight="900" font-size="{font_size}" textLength="{text_length}" '
            f'lengthAdjust="spacingAndGlyphs" fill="{EMERALD_DEEP}">{label}</text>'
            + sparkle(486, 138, 40, GOLD))


def avatar_sport() -> str:
    return avatar(sneaker(), "TÊNIS E ESPORTE", font_size=27, text_length=280)


# ---------------------------------------------------------------- saída

FILES = {
    "1-etiqueta-icone.svg": (512, 512, icon_tag(), "MegaPromoOz, etiqueta mágica (ícone)"),
    "1-etiqueta-logo.svg": (720, 200, lockup(icon_tag(), "ofertas com desconto de verdade"),
                            "MegaPromoOz, etiqueta mágica"),
    "1-etiqueta-logo-escuro.svg": (720, 200, lockup(icon_tag(), "ofertas com desconto de verdade", dark=True),
                                   "MegaPromoOz, etiqueta mágica, fundo escuro"),
    "2-lupa-icone.svg": (512, 512, icon_lens(), "MegaPromoOz, lupa caçadora (ícone)"),
    "2-lupa-logo.svg": (640, 200, lockup_lens_letter(), "MegaPromoOz, lupa no O de Oz"),
    "2-lupa-logo-escuro.svg": (640, 200, lockup_lens_letter(dark=True), "MegaPromoOz, lupa, fundo escuro"),
    "3-selo-icone.svg": (512, 512, icon_seal(), "MegaPromoOz, selo verificado (ícone)"),
    "3-selo-logo.svg": (720, 200, lockup(icon_seal(), "desconto verificado, oferta de verdade"),
                        "MegaPromoOz, selo verificado"),
    "avatar-ferramentas.svg": (640, 640, avatar(wrench(), "FERRAMENTAS"), "MegaPromoOz Ferramentas, foto do grupo"),
    "avatar-maquiagem.svg": (640, 640, avatar(lipstick(), "MAQUIAGEM"), "MegaPromoOz Maquiagem, foto do grupo"),
    "avatar-tenis-esporte.svg": (640, 640, avatar_sport(), "MegaPromoOz Tênis e Esporte, foto do grupo"),
}
SMALL = ("1-etiqueta-icone.svg", "2-lupa-icone.svg", "3-selo-icone.svg", "avatar-ferramentas.svg",
         "avatar-maquiagem.svg", "avatar-tenis-esporte.svg")


def data_uri(name: str) -> str:
    """SVG embutido: a prévia abre sozinha, até como snapshot sem a pasta ao lado."""
    w, h, body, title = FILES[name]
    return "data:image/svg+xml;base64," + base64.b64encode(svg(w, h, body, title).encode("utf-8")).decode()


def preview_html() -> str:
    cards = "".join(
        f'<figure><img src="{data_uri(name)}" alt="{title}" style="width:{240 if w == h else 560}px">'
        f'<figcaption>{name}</figcaption></figure>'
        for name, (w, h, _b, title) in FILES.items())
    small = []
    for name in SMALL:
        radius = "50%" if name.startswith("avatar") else "22%"
        uri = data_uri(name)
        imgs = "".join(f'<img src="{uri}" style="width:{s}px;height:{s}px;border-radius:{radius}">' for s in (96, 48, 32))
        small.append(f'<figure><div class="row">{imgs}</div><figcaption>{name} em 96/48/32 px</figcaption></figure>')
    style = ("body{font-family:Segoe UI,Arial,sans-serif;background:#f4f1e8;margin:24px;color:#1d2b25}"
             "section{display:flex;flex-wrap:wrap;gap:20px}"
             "figure{background:#fff;border:1px solid #ddd;border-radius:12px;padding:14px;margin:0;align-self:flex-start}"
             "figcaption{font-size:12px;color:#556;margin-top:6px}"
             ".row{display:flex;gap:14px;align-items:center}h2{font-weight:600}")
    return ('<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>MegaPromoOz, marca</title>'
            f'<style>{style}</style><h2>Propostas</h2><section>{cards}</section>'
            f'<h2>Tamanhos pequenos (foto do grupo e favicon)</h2><section>{"".join(small)}</section></html>\n')


def main() -> None:
    for name, (w, h, body, title) in FILES.items():
        (OUT / name).write_text(svg(w, h, body, title), encoding="utf-8", newline="\n")
    (OUT / "preview.html").write_text(preview_html(), encoding="utf-8", newline="\n")
    print("\n".join(sorted(p.name for p in OUT.glob("*.svg"))))


if __name__ == "__main__":
    main()
