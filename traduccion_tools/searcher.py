#!/usr/bin/env python3
"""
searcher.py - Buscador de textos para los archivos CSV del proyecto de traducción.

Uso:
  python searcher.py "texto a buscar"
  python searcher.py --elf "texto a buscar"
"""

import csv
import sys
import argparse
from pathlib import Path

def search_csv(csv_path, query, exact=False):
    path = Path(csv_path)
    if not path.exists():
        print(f"[!] Archivo no encontrado: {csv_path}")
        return
        
    print(f"\nBuscando '{query}' en {csv_path}...")
    print("-" * 80)
    
    count = 0
    query_lower = query.lower()
    
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        header = next(reader, None)
        
        for i, row in enumerate(reader):
            if len(row) < 4:
                continue
                
            orig = row[3]
            trans = row[4] if len(row) > 4 else ""
            
            match = False
            if exact:
                match = (query == orig) or (query == trans)
            else:
                match = (query_lower in orig.lower()) or (query_lower in trans.lower())
                
            if match:
                fid = row[1]
                offset = row[2]
                print(f"[{fid}] Offset: {offset}")
                print(f"  JAP: {orig}")
                if trans:
                    print(f"  ESP: {trans}")
                print("-" * 40)
                count += 1
                
    print(f"Total encontrados: {count}")

def main():
    parser = argparse.ArgumentParser(description="Busca frases en los CSV de traducción.")
    parser.add_argument("query", help="Texto en japonés o español a buscar")
    parser.add_argument("-e", "--elf", action="store_true", help="Buscar en elf_strings.csv (Menús) en lugar de dialogo.csv (Historia)")
    parser.add_argument("-x", "--exact", action="store_true", help="Búsqueda exacta (sensible a mayúsculas y minúsculas)")
    
    args = parser.parse_args()
    
    if args.elf:
        search_csv("textos/elf_strings.csv", args.query, args.exact)
    else:
        search_csv("textos/dialogo.csv", args.query, args.exact)

if __name__ == "__main__":
    main()
