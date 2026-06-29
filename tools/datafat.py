#!/usr/bin/env python3
"""
datafat.py — Lógica canónica de la FAT de Data.bin (Strawberry Panic!, PS2).

IMPORTANTE — formato real verificado contra el binario:

  Cabecera (offset 0x00):
    0x00: magic     u32 = 0x7878C800
    0x04: num_files u32 = 27411
    0x08: ???       u32 = 0x8000
    0x0C: data_base u32 = 0x60000

  Tabla (FAT) en offset 0x8004, num_files registros de 12 bytes:
    [id:u32] [size_field:u32] [offset:u32]

  >>> El `offset` de la fila i SÍ corresponde al archivo con `id` de la fila i.
  >>> El `size_field` de la fila i NO es el tamaño del archivo i: es el tamaño
      del archivo de la fila ANTERIOR. El tamaño real (en disco) del archivo de
      la fila i está almacenado en el `size_field` de la fila i+1.

  Esto se confirmó comparando, para los 997 scripts LZ77, el total real del
  stream (12 + comp_size del header LZ77) contra el size_field: coincide 997/997
  con la fila SIGUIENTE, nunca con la propia fila.

  El loader del juego usa ese tamaño (fila siguiente) para leer el archivo del
  CD a RAM. Por eso, al recomprimir un script y escribir el nuevo tamaño en la
  fila equivocada, el juego cargaba menos bytes de los necesarios y el stream
  quedaba truncado -> corrupción (la causa raíz del bug histórico).

Reglas de uso:
  - Para LEER el tamaño real del archivo de una fila: usar `entry['size']`.
  - Para ESCRIBIR el nuevo tamaño real: escribir en el size_field de la fila
    SIGUIENTE (ver `size_field_write_offset`).
"""

import struct

FAT_OFFSET = 0x8004
NUM_ENTRIES = 27411
ENTRY_SIZE = 12  # [id:u32, size_field:u32, offset:u32]


def read_fat_raw(source):
    """Devuelve los bytes crudos de la FAT desde una ruta o desde bytes de Data.bin."""
    if isinstance(source, (bytes, bytearray)):
        return bytes(source[FAT_OFFSET:FAT_OFFSET + NUM_ENTRIES * ENTRY_SIZE])
    with open(source, 'rb') as f:
        f.seek(FAT_OFFSET)
        return f.read(NUM_ENTRIES * ENTRY_SIZE)


def parse_entries(fat_raw):
    """
    Devuelve una lista de dicts, una por fila de la FAT, con:
      row        : índice de la fila
      id         : id del archivo
      size_field : valor crudo del campo (= tamaño del archivo de la fila anterior)
      off        : offset en Data.bin del archivo de ESTA fila
      size       : tamaño REAL del archivo de esta fila (= size_field de la fila i+1)
      is_file    : True si off > 0 (las filas con off == 0 son centinela/no-archivo)
    """
    rows = []
    for i in range(NUM_ENTRIES):
        fid, size_field, off = struct.unpack_from('<III', fat_raw, i * ENTRY_SIZE)
        rows.append({'row': i, 'id': fid, 'size_field': size_field, 'off': off})
    for i in range(NUM_ENTRIES):
        nxt = rows[i + 1]['size_field'] if i + 1 < NUM_ENTRIES else rows[i]['size_field']
        rows[i]['size'] = nxt
        rows[i]['is_file'] = rows[i]['off'] > 0
    return rows


def read_entries(source):
    """Atajo: lee y parsea la FAT desde ruta o bytes."""
    return parse_entries(read_fat_raw(source))


def find_row(rows, fid):
    """Encuentra la fila (dict) de un id de archivo real (off > 0). None si no existe."""
    for r in rows:
        if r['id'] == fid and r['off'] > 0:
            return r
    return None


def slot_capacity(rows, row):
    """
    Capacidad física del slot = distancia hasta el siguiente offset en disco.
    Es >= size real (incluye el padding de alineación a 0x800). Útil para saber
    si un stream recomprimido cabe sin pisar al archivo vecino.
    """
    offs = sorted(r['off'] for r in rows if r['off'] > 0)
    o = row['off']
    idx = offs.index(o)
    if idx + 1 < len(offs):
        return offs[idx + 1] - o
    return row['size']


def size_field_write_offset(row):
    """
    Offset absoluto (en Data.bin) donde hay que ESCRIBIR el tamaño real del
    archivo de `row`: el size_field de la fila SIGUIENTE.
    """
    return FAT_OFFSET + (row['row'] + 1) * ENTRY_SIZE + 4
