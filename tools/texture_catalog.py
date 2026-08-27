#!/usr/bin/env python3
"""Genera un catálogo HTML navegable de las texturas extraídas.

Lee <dir>/textures.json (producido por extract_all_textures.py) y escribe
<dir>/index.html con miniaturas + filtros (tamaño, formato, transparencia,
strips de texto, rango de IDs, score UI) y toggle de fondo (checker/negro/blanco)
para poder ver fuentes/textos blancos.

Uso:
    python3 dev/texture_catalog.py --dir dev/output/all_textures
    # abrir dev/output/all_textures/index.html
"""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

DEV = Path(__file__).resolve().parent
ROOT = DEV.parent
DEFAULT_DIR = ROOT / "work_texturas" / "output" / "all_textures"

HTML_TEMPLATE = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Catálogo de texturas — StrawTraduccion</title>
<style>
  :root { --bg:#1e1e1e; --panel:#2a2a2a; --fg:#eee; --muted:#9aa; --accent:#4a9; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, sans-serif; background:var(--bg); color:var(--fg); }
  header { position:sticky; top:0; z-index:10; background:var(--panel); padding:10px 14px;
           border-bottom:1px solid #000; box-shadow:0 2px 8px rgba(0,0,0,.4); }
  h1 { font-size:15px; margin:0 0 8px 0; }
  .controls { display:flex; flex-wrap:wrap; gap:10px 16px; align-items:center; font-size:12px; }
  .controls label { display:flex; gap:4px; align-items:center; color:var(--muted); }
  .controls input[type=number], .controls input[type=text], .controls select {
     background:#111; color:var(--fg); border:1px solid #444; border-radius:4px; padding:3px 6px; }
  .controls input[type=number]{ width:70px; }
  .controls input[type=text]{ width:120px; }
  .count { color:var(--accent); font-weight:600; }
  .grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(150px,1fr)); gap:10px; padding:14px; }
  .card { background:var(--panel); border:1px solid #000; border-radius:6px; padding:6px; font-size:11px; }
  .thumb { display:flex; align-items:center; justify-content:center; height:150px; border-radius:4px; overflow:hidden; }
  .thumb img { max-width:100%; max-height:150px; image-rendering: pixelated; }
  .bg-checker .thumb { background-image:
      linear-gradient(45deg,#666 25%,transparent 25%),
      linear-gradient(-45deg,#666 25%,transparent 25%),
      linear-gradient(45deg,transparent 75%,#666 75%),
      linear-gradient(-45deg,transparent 75%,#666 75%);
      background-size:16px 16px; background-position:0 0,0 8px,8px -8px,-8px 0; background-color:#999; }
  .bg-black .thumb { background:#000; }
  .bg-white .thumb { background:#fff; }
  .bg-magenta .thumb { background:#f0f; }
  .meta { margin-top:5px; line-height:1.35; }
  .meta .id { font-weight:700; color:#fff; }
  .meta .dim { color:var(--accent); }
  .tag { display:inline-block; background:#143; color:#9f9; border-radius:3px; padding:0 4px; margin:1px 2px 0 0; font-size:10px; }
  .score { float:right; background:#333; border-radius:3px; padding:0 5px; }
  .empty { padding:40px; text-align:center; color:var(--muted); }
</style>
</head>
<body class="bg-checker">
<header>
  <h1>Catálogo de texturas — <span class="count" id="count">0</span> visibles</h1>
  <div class="controls">
    <label>Fondo
      <select id="bg">
        <option value="bg-checker">Damero</option>
        <option value="bg-black">Negro</option>
        <option value="bg-white">Blanco</option>
        <option value="bg-magenta">Magenta</option>
      </select>
    </label>
    <label>Orden
      <select id="sort">
        <option value="score_ui">Score UI ↓</option>
        <option value="id">ID ↑</option>
        <option value="width">Ancho ↓</option>
        <option value="height">Alto ↓</option>
        <option value="aspect">Aspect ↓</option>
        <option value="unique_colors">Colores ↑</option>
      </select>
    </label>
    <label>Formato
      <select id="fmt">
        <option value="">todos</option>
        <option value="INDEX4">INDEX4 (4bpp)</option>
        <option value="INDEX8">INDEX8 (8bpp)</option>
      </select>
    </label>
    <label>ID desde <input type="number" id="idmin"></label>
    <label>ID hasta <input type="number" id="idmax"></label>
    <label>Ancho ≥ <input type="number" id="wmin"></label>
    <label>Alto ≤ <input type="number" id="hmax"></label>
    <label>Aspect ≥ <input type="number" id="armin" step="0.1"></label>
    <label>Colores ≤ <input type="number" id="cmax"></label>
    <label>Score UI ≥ <input type="number" id="smin" value="0"></label>
    <label><input type="checkbox" id="onlyalpha"> con alpha</label>
    <label><input type="checkbox" id="onlystrip"> solo strips</label>
    <label>Buscar ID/tag <input type="text" id="q"></label>
    <button id="reset">Reset</button>
  </div>
</header>
<div class="grid" id="grid"></div>
<div class="empty" id="empty" style="display:none">Sin resultados con esos filtros.</div>
<script>
const DATA = __DATA__;
const grid = document.getElementById('grid');
const els = id => document.getElementById(id);
function num(v){ return v===''||v===null||v===undefined ? null : parseFloat(v); }

function apply(){
  const fmt = els('fmt').value;
  const idmin = num(els('idmin').value), idmax = num(els('idmax').value);
  const wmin = num(els('wmin').value), hmax = num(els('hmax').value);
  const armin = num(els('armin').value), cmax = num(els('cmax').value);
  const smin = num(els('smin').value);
  const onlyalpha = els('onlyalpha').checked, onlystrip = els('onlystrip').checked;
  const q = els('q').value.trim().toLowerCase();
  const sort = els('sort').value;

  let rows = DATA.filter(r => {
    if (fmt && r.image_type_name !== fmt) return false;
    if (idmin!==null && r.id < idmin) return false;
    if (idmax!==null && r.id > idmax) return false;
    if (wmin!==null && r.width < wmin) return false;
    if (hmax!==null && r.height > hmax) return false;
    if (armin!==null && r.aspect < armin) return false;
    if (cmax!==null && (r.unique_colors===null || r.unique_colors > cmax)) return false;
    if (smin!==null && r.score_ui < smin) return false;
    if (onlyalpha && !(r.alpha_ratio > 0.005)) return false;
    if (onlystrip && !(r.aspect >= 2 && r.height <= 96)) return false;
    if (q){
      const hay = ('id'+r.id+' '+r.image_type_name+' '+(r.ui_reasons||'')).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  const desc = new Set(['score_ui','width','height','aspect']);
  rows.sort((a,b)=>{
    let x=a[sort], y=b[sort];
    if (x===null) x=-1; if (y===null) y=-1;
    return desc.has(sort) ? (y-x) : (x-y);
  });

  els('count').textContent = rows.length;
  els('empty').style.display = rows.length ? 'none':'block';
  grid.innerHTML = rows.slice(0, 4000).map(r => {
    const tags = (r.ui_reasons||'').split(',').filter(Boolean).slice(0,4)
                  .map(t=>`<span class="tag">${t}</span>`).join('');
    const img = r.png ? `<img loading="lazy" src="${r.png}">` : '<span style="color:#f88">sin PNG</span>';
    const cols = r.unique_colors===null?'-':r.unique_colors;
    const al = r.alpha_ratio===null?'-':r.alpha_ratio;
    const nested = r.nested_lz77_offset === null || r.nested_lz77_offset === undefined
      ? '' : ` · L0x${r.nested_lz77_offset.toString(16).toUpperCase()}`;
    return `<div class="card">
      <div class="thumb">${img}</div>
      <div class="meta">
        <span class="score">UI ${r.score_ui}</span>
        <span class="id">ID ${r.id}</span>${nested} · T${r.tim2_index} P${r.picture_index}<br>
        <span class="dim">${r.width}×${r.height}</span> · ${r.image_type_name}<br>
        colores ${cols} · alpha ${al}<br>${tags}
      </div>
    </div>`;
  }).join('');
}

els('bg').addEventListener('change', e => { document.body.className = e.target.value; });
['fmt','idmin','idmax','wmin','hmax','armin','cmax','smin','q','sort'].forEach(id=>{
  els(id).addEventListener('input', apply);
});
['onlyalpha','onlystrip'].forEach(id=> els(id).addEventListener('change', apply));
els('reset').addEventListener('click', ()=>{
  ['idmin','idmax','wmin','hmax','armin','cmax','q'].forEach(id=> els(id).value='');
  els('smin').value='0'; els('fmt').value=''; els('onlyalpha').checked=false; els('onlystrip').checked=false;
  apply();
});
apply();
</script>
</body>
</html>
"""


def build_catalog(out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    json_path = out_dir / "textures.json"
    if not json_path.exists():
        raise FileNotFoundError(f"No existe {json_path}. Ejecuta primero extract_all_textures.py")
    records = json.loads(json_path.read_text(encoding="utf-8"))
    # Solo campos necesarios para el HTML (mantener liviano).
    slim = [{
        "id": r["id"],
        "tim2_index": r["tim2_index"],
        "picture_index": r["picture_index"],
        "nested_lz77_offset": r.get("nested_lz77_offset"),
        "width": r["width"],
        "height": r["height"],
        "aspect": r["aspect"],
        "image_type_name": r["image_type_name"],
        "unique_colors": r["unique_colors"],
        "alpha_ratio": r["alpha_ratio"],
        "score_ui": r["score_ui"],
        "ui_reasons": r["ui_reasons"],
        "png": r["png"],
    } for r in records]
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(slim, ensure_ascii=False))
    out_html = out_dir / "index.html"
    out_html.write_text(html, encoding="utf-8")
    return out_html


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Genera index.html a partir de textures.json")
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help="Directorio con textures.json y png/")
    args = ap.parse_args(argv)
    out = build_catalog(Path(args.dir))
    print(f"Catálogo generado: {out}")
    print(f"Ábrelo en el navegador: file://{out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
