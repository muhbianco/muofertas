"""Exporta em PNG a marca escolhida (conceito 1, etiqueta mágica).

Renderiza com o Chrome/Edge já instalado, em modo headless e com perfil temporário próprio (não
toca no navegador do dono). Cada PNG é conferido pelo cabeçalho: largura e altura têm de bater.

Uso: python -I brand/exportar_png.py  → brand/png/
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gerar_marca as m  # noqa: E402

OUT = m.OUT / "png"
BROWSERS = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
)
TIMEOUT_S = 60


def full_bleed(icon: str) -> str:
    """Quadrado sem cantos arredondados: iOS e Android aplicam a própria máscara."""
    return icon.replace('rx="112"', 'rx="0"', 1)


def transparent_lockup(dark_text: bool) -> str:
    return (f'<g transform="translate(24,24) scale(0.3125)">{m.icon_tag()}</g>'
            + m.wordmark(208, 112, "ofertas com desconto de verdade", dark=not dark_text))


# nome do PNG → (svg de origem: largura, altura, corpo) e tamanho final em px
TARGETS: dict[str, tuple[int, int, str, int, int]] = {
    "icone-1024.png": (512, 512, m.icon_tag(), 1024, 1024),
    "icone-512.png": (512, 512, m.icon_tag(), 512, 512),
    "icone-192.png": (512, 512, m.icon_tag(), 192, 192),
    "icone-maskable-512.png": (512, 512, full_bleed(m.icon_tag()), 512, 512),
    "apple-touch-icon-180.png": (512, 512, full_bleed(m.icon_tag()), 180, 180),
    "favicon-48.png": (512, 512, m.icon_tag(), 48, 48),
    "favicon-32.png": (512, 512, m.icon_tag(), 32, 32),
    "logo-claro-1440x400.png": (720, 200, m.lockup(m.icon_tag(), "ofertas com desconto de verdade"), 1440, 400),
    "logo-escuro-1440x400.png": (720, 200, m.lockup(m.icon_tag(), "ofertas com desconto de verdade", dark=True), 1440, 400),
    "logo-transparente-texto-escuro-1440x400.png": (720, 200, transparent_lockup(dark_text=True), 1440, 400),
    "logo-transparente-texto-claro-1440x400.png": (720, 200, transparent_lockup(dark_text=False), 1440, 400),
    "grupo-ferramentas-640.png": (640, 640, m.avatar(m.wrench(), "FERRAMENTAS"), 640, 640),
    "grupo-maquiagem-640.png": (640, 640, m.avatar(m.lipstick(), "MAQUIAGEM"), 640, 640),
    "grupo-tenis-esporte-640.png": (640, 640, m.avatar_sport(), 640, 640),
    # O nicho virou Beleza (maquiagem, perfume, pele, cabelo): o rótulo segue o nome do grupo.
    "grupo-beleza-640.png": (640, 640, m.avatar(m.lipstick(), "BELEZA", text_length=180), 640, 640),
    "grupo-tecnologia-640.png": (640, 640, m.avatar(m.gamepad(), "TECNOLOGIA", text_length=240), 640, 640),
    "grupo-casa-mimos-640.png": (
        640, 640, m.avatar(m.house_heart(), "CASA E MIMOS", font_size=30, text_length=260), 640, 640),
    "grupo-pais-maes-filhos-640.png": (
        640, 640, m.avatar(m.teddy(), "PAIS, MÃES E FILHOS", font_size=24, text_length=284), 640, 640),
}


def browser() -> Path:
    for path in BROWSERS:
        if path.is_file():
            return path
    raise SystemExit("nenhum Chrome/Edge encontrado")


def png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path.name} não é PNG")
    return struct.unpack(">II", head[16:24])


def render(exe: Path, work: Path, profile: Path, name: str, spec: tuple[int, int, str, int, int]) -> None:
    vw, vh, body, out_w, out_h = spec
    src = work / (name + ".svg")
    # mesmo viewBox, tamanho intrínseco = tamanho final: o navegador desenha em vetor, sem reamostrar
    src.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vw} {vh}" width="{out_w}" height="{out_h}">{body}</svg>',
        encoding="utf-8",
    )
    target = OUT / name
    target.unlink(missing_ok=True)
    cmd = [
        str(exe), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
        "--no-default-browser-check", f"--user-data-dir={profile}",
        "--default-background-color=00000000", "--force-device-scale-factor=1",
        f"--window-size={out_w},{out_h}", f"--screenshot={target}", src.as_uri(),
    ]
    subprocess.run(cmd, check=True, timeout=TIMEOUT_S, capture_output=True)
    size = png_size(target)
    if size != (out_w, out_h):
        raise SystemExit(f"{name}: saiu {size}, esperado {(out_w, out_h)}")
    print(f"{name:<48} {out_w}x{out_h}")


def main() -> None:
    exe = browser()
    OUT.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="mpo-brand-"))
    profile = work / "perfil"
    try:
        # Com nomes na linha de comando, só esses (os outros PNGs ficam como estão).
        wanted = set(sys.argv[1:]) or set(TARGETS)
        unknown = wanted - set(TARGETS)
        if unknown:
            raise SystemExit(f"desconhecidos: {sorted(unknown)}")
        for name, spec in TARGETS.items():
            if name in wanted:
                render(exe, work, profile, name, spec)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
