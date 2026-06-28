#!/usr/bin/env python3
"""
extract_ram.py — Extrae eeMemory.bin de un savestate PCSX2 (.p2s)
usando zstandard (tipo de compresión 93).
"""
import struct
import zstandard
import sys

def extract_zstd_from_zip(zip_path, target_name):
    """Extrae una entrada zstd de un ZIP manualmente."""
    with open(zip_path, 'rb') as f:
        data = f.read()
    
    # Buscar el Local File Header del entry: PK\x03\x04
    offset = 0
    while offset < len(data) - 4:
        if data[offset:offset+4] == b'PK\x03\x04':
            # Local file header
            # Offset 26: filename length, 28: extra length
            fname_len = struct.unpack_from('<H', data, offset + 26)[0]
            extra_len = struct.unpack_from('<H', data, offset + 28)[0]
            fname = data[offset+30:offset+30+fname_len].decode('utf-8', errors='replace')
            
            comp_size = struct.unpack_from('<I', data, offset + 18)[0]
            
            if target_name in fname:
                data_start = offset + 30 + fname_len + extra_len
                comp_data = data[data_start:data_start + comp_size]
                
                print(f"Encontrado '{fname}': {comp_size:,} bytes comprimidos")
                
                # Descomprimir con zstd
                dctx = zstandard.ZstdDecompressor()
                decompressed = dctx.decompress(comp_data, max_output_size=64 * 1024 * 1024)
                print(f"Descomprimido: {len(decompressed):,} bytes")
                return decompressed
            
            offset += 30 + fname_len + extra_len + comp_size
        else:
            offset += 1
    
    raise FileNotFoundError(f"'{target_name}' no encontrado en el ZIP.")

if __name__ == '__main__':
    ss_path = sys.argv[1] if len(sys.argv) > 1 else 'saveStates/prologo_save.p2s'
    out_path = sys.argv[2] if len(sys.argv) > 2 else '/tmp/ee_ram.bin'
    
    print(f"Extrayendo eeMemory.bin de {ss_path}...")
    ram = extract_zstd_from_zip(ss_path, 'eeMemory.bin')
    
    with open(out_path, 'wb') as f:
        f.write(ram)
    print(f"Guardado en {out_path}")
