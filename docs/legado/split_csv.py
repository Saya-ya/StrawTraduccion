#!/usr/bin/env python3
"""Divide el CSV de dialogo en archivos por script o por script/seccion.

Uso:
  python tools/split_csv.py                  # Por script (default)
  python tools/split_csv.py --by-section     # Por script/seccion
  python tools/split_csv.py --csv texto/dialogo.csv  # CSV personalizado
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path


def split_by_script(csv_path: Path, out_dir: Path):
    """Divide en un CSV por file_id."""
    groups = defaultdict(list)
    header = None

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            groups[row[1]].append(row)

    total = sum(len(v) for v in groups.values())
    print(f"Fuente: {csv_path}")
    print(f"Scripts unicos: {len(groups)}")
    print(f"Textos totales: {total:,}")

    out_dir.mkdir(parents=True, exist_ok=True)

    for fid, rows in sorted(groups.items(), key=lambda x: -len(x[1])):
        safe_name = fid.replace("/", "_").replace("\\", "_")
        out_path = out_dir / f"{safe_name}.csv"
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f_out:
            writer = csv.writer(f_out)
            writer.writerow(header)
            writer.writerows(rows)

    index_path = out_dir / "_indice.txt"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(f"Fuente: {csv_path}\n")
        f.write(f"Scripts: {len(groups)}\n")
        f.write(f"Textos: {total:,}\n\n")
        for fid, rows in sorted(groups.items(), key=lambda x: -len(x[1])):
            src = rows[0][0]
            f.write(f"  {fid}  ({src})  {len(rows):,} textos\n")

    print(f"Archivos en: {out_dir}/ ({len(groups)} CSVs)")
    print(f"Indice: {index_path}")


def split_by_section(csv_path: Path, out_dir: Path):
    """Divide en un CSV por file_id/seccion para agrupar por escenas."""
    groups = defaultdict(list)
    header = None

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        section_idx = header.index('section') if 'section' in header else None
        has_sections = section_idx is not None

        for row in reader:
            file_id = row[1]
            key = f"{file_id}/sec_{row[section_idx]}" if has_sections else file_id
            groups[key].append(row)

    total = sum(len(v) for v in groups.values())
    mode = "script/seccion" if has_sections else "script (sin secciones)"
    print(f"Fuente: {csv_path}")
    print(f"Modo: {mode}")
    print(f"Grupos unicos: {len(groups)}")
    print(f"Textos totales: {total:,}")

    out_dir.mkdir(parents=True, exist_ok=True)

    for key, rows in sorted(groups.items(), key=lambda x: -len(x[1])):
        safe_name = key.replace("/", "_").replace("\\", "_")
        out_path = out_dir / f"{safe_name}.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f_out:
            writer = csv.writer(f_out)
            writer.writerow(header)
            writer.writerows(rows)

    index_path = out_dir / "_indice.txt"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(f"Fuente: {csv_path}\n")
        f.write(f"Grupos: {len(groups)}\n")
        f.write(f"Textos: {total:,}\n")
        f.write(f"Modo: {mode}\n\n")
        for key, rows in sorted(groups.items(), key=lambda x: -len(x[1])):
            f.write(f"  {key}  {len(rows):,} textos\n")

    print(f"Archivos en: {out_dir}/ ({len(groups)} CSVs)")
    print(f"Indice: {index_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Divide el CSV de dialogo en archivos mas pequenos"
    )
    parser.add_argument("--csv", type=str, default=None,
                        help="Ruta al CSV fuente (default: busca en textos/)")
    parser.add_argument("--by-section", action="store_true",
                        help="Dividir por script/seccion (requiere columna 'section')")
    parser.add_argument("--by-script", action="store_true",
                        help="Dividir por script/file_id (default)")
    parser.add_argument("--out", type=str, default="textos/por_script",
                        help="Directorio de salida")
    args = parser.parse_args()

    out_dir = Path(args.out)

    if args.csv:
        csv_path = Path(args.csv)
    else:
        candidates = [
            Path("textos/dialogo.csv"),
            Path("textos/dialogo.csv.bak"),
            Path("textos/dialogo.csv.bak2"),
        ]
        csv_path = next((c for c in candidates if c.exists()), None)

    if not csv_path:
        print("ERROR: No se encontro dialogo.csv en textos/")
        print("Ejecuta primero: python tools/dialogue_order.py")
        return

    if args.by_section:
        split_by_section(csv_path, out_dir)
    else:
        split_by_script(csv_path, out_dir)


if __name__ == "__main__":
    main()
