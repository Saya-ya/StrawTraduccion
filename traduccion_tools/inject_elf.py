import os
import sys

def inject():
    iso_path = 'work/Strawberry_translated.iso'
    elf_path = 'work/SLPS_256.11_translated'
    orig_elf_path = 'originales/SLPS_256.11'
    
    if not os.path.exists(iso_path) or not os.path.exists(elf_path) or not os.path.exists(orig_elf_path):
        print("Missing files.")
        return
        
    with open(orig_elf_path, 'rb') as f:
        elf_sig = f.read(4096)
        
    print("Searching for ELF in ISO...")
    with open(iso_path, 'r+b') as f:
        # read chunks and find sig
        chunk_size = 16 * 1024 * 1024
        overlap = len(elf_sig) - 1
        offset = 0
        target_offset = -1
        
        while True:
            f.seek(offset)
            chunk = f.read(chunk_size)
            if not chunk:
                break
            idx = chunk.find(elf_sig)
            if idx != -1:
                target_offset = offset + idx
                break
            offset += len(chunk) - overlap
            
        if target_offset == -1:
            print("Could not find ELF in ISO.")
            return
            
        print(f"Found ELF at offset: 0x{target_offset:X}")
        
        with open(elf_path, 'rb') as e:
            elf_data = e.read()
            
        f.seek(target_offset)
        f.write(elf_data)
        print("ELF successfully injected!")

inject()
