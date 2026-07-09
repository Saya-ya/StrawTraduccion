import os
import sys
from pathlib import Path


SIGNATURE = b"\x00\xC8\x78\x78\x13\x6B\x00\x00\x00\x80\x00\x00"


def build_iso(patched_bin_path, iso_in=None, iso_out=None):
    workspace = Path(__file__).parent.parent
    
    if iso_in is None:
        iso_in = workspace / 'originales' / 'Strawberry_patched.iso'
    if iso_out is None:
        iso_out = workspace / 'work' / 'Strawberry_translated.iso'
    
    iso_in = Path(iso_in)
    iso_out = Path(iso_out)
    patched_bin = Path(patched_bin_path)
    
    if not iso_in.exists():
        print(f"ERROR: ISO no encontrada: {iso_in}")
        return None
    if not patched_bin.exists():
        print(f"ERROR: Data.bin no encontrado: {patched_bin}")
        return None
    
    if not iso_out.exists():
        import shutil
        print(f"Copiando ISO base...")
        shutil.copy2(iso_in, iso_out)
    
    print(f"Buscando Data.bin en ISO...")
    target_offset = -1
    chunk_size = 16 * 1024 * 1024
    overlap = len(SIGNATURE) - 1
    
    with open(iso_out, "rb") as f:
        offset = 0
        while True:
            f.seek(offset)
            chunk = f.read(chunk_size)
            if not chunk:
                break
            idx = chunk.find(SIGNATURE)
            if idx != -1:
                target_offset = offset + idx
                break
            offset += len(chunk) - overlap
    
    if target_offset == -1:
        print("ERROR: No se encontró Data.bin en la ISO")
        return None
    
    print(f"  Data.bin en offset 0x{target_offset:X}")
    
    bin_size = os.path.getsize(patched_bin)
    print(f"Inyectando {bin_size:,} bytes...")
    
    chunk_write = 8 * 1024 * 1024
    with open(iso_out, "r+b") as f_out:
        f_out.seek(target_offset)
        with open(patched_bin, "rb") as f_in:
            written = 0
            while True:
                block = f_in.read(chunk_write)
                if not block:
                    break
                f_out.write(block)
                written += len(block)
                if written % (200 * 1024 * 1024) == 0:
                    pct = written / bin_size * 100
                    print(f"  {pct:.0f}%")
    
    final_size = os.path.getsize(iso_out)
    print(f"\nISO creada: {iso_out}")
    print(f"Tamaño: {final_size:,} bytes ({final_size / 1024 / 1024:.0f} MB)")
    return iso_out


def main():
    patched_bin = "work/Data_patched.bin"
    if len(sys.argv) > 1:
        patched_bin = sys.argv[1]
    
    result = build_iso(patched_bin)
    if result:
        print(f"\nLista para probar en PCSX2: {result}")


if __name__ == '__main__':
    main()
