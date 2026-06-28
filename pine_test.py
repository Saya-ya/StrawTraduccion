"""
pine_test.py — Script de diagnóstico para la API PINE de PCSX2.

Uso:
    python pine_test.py

Qué hace:
1. Detecta el sistema operativo.
2. Intenta conectar al socket correcto:
   - Windows -> TCP 127.0.0.1:28011
   - Linux   -> Unix socket en $XDG_RUNTIME_DIR/pcsx2.sock (o /tmp/pcsx2.sock)
   - macOS   -> Unix socket en $TMPDIR/pcsx2.sock (o /tmp/pcsx2.sock)
3. Envía MsgStatus (funciona incluso sin juego cargado) para confirmar
   que el servidor PINE responde.
4. Si hay un juego cargado, también prueba MsgVersion, MsgTitle y MsgID.

Requisitos previos en PCSX2:
- Settings -> Advanced -> PINE Settings -> Enable (slot por defecto: 28011)
- Reiniciar PCSX2 después de activarlo (no solo el juego)
"""

import os
import platform
import socket
import struct
import sys

DEFAULT_SLOT = 28011

# --- Opcodes IPC de PINE (de PINE.cpp en el repo de PCSX2) ---
MSG_READ8 = 0x00
MSG_READ16 = 0x01
MSG_READ32 = 0x02
MSG_READ64 = 0x03
MSG_WRITE8 = 0x04
MSG_WRITE16 = 0x05
MSG_WRITE32 = 0x06
MSG_WRITE64 = 0x07
MSG_VERSION = 0x08
MSG_SAVESTATE = 0x09
MSG_LOADSTATE = 0x0A
MSG_TITLE = 0x0B
MSG_ID = 0x0C
MSG_UUID = 0x0D
MSG_GAMEVERSION = 0x0E
MSG_STATUS = 0x0F

IPC_OK = 0x00
IPC_FAIL = 0xFF

STATUS_NAMES = {0: "Running (juego corriendo)", 1: "Paused (en pausa)", 2: "Shutdown (sin juego cargado)"}


def get_unix_socket_path(slot=DEFAULT_SLOT):
    """Replica la lógica de PINE.cpp para encontrar el socket en Linux/macOS."""
    if platform.system() == "Darwin":
        runtime_dir = os.environ.get("TMPDIR")
    else:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")

    base = runtime_dir if runtime_dir else "/tmp"
    path = os.path.join(base, "pcsx2.sock")
    if slot != DEFAULT_SLOT:
        path += f".{slot}"
    return path


def is_wsl():
    """Detecta si estamos corriendo dentro de WSL (no Linux nativo)."""
    if "WSL_DISTRO_NAME" in os.environ or "WSL_INTEROP" in os.environ:
        return True
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except FileNotFoundError:
        return False


def get_wsl_host_ip():
    """Obtiene la IP del host Windows visto desde WSL2 (gateway por defecto)."""
    with open("/proc/net/route") as f:
        for line in f.readlines()[1:]:
            fields = line.strip().split()
            # Destination 00000000 = ruta por defecto
            if fields[1] == "00000000":
                gateway_hex = fields[2]
                # la IP viene en hex, little-endian
                gateway_int = int(gateway_hex, 16)
                ip = socket.inet_ntoa(struct.pack("<I", gateway_int))
                return ip
    raise RuntimeError("No se pudo determinar la IP del host Windows desde WSL.")


def connect_pine(slot=DEFAULT_SLOT, timeout=3.0, host_ip=None):
    """Devuelve un socket conectado, probando el método correcto según el entorno."""
    system = platform.system()

    if system == "Windows" or is_wsl():
        if host_ip is None:
            if is_wsl():
                host_ip = get_wsl_host_ip()
                print(f"[*] Detectado WSL. IP del host Windows: {host_ip}")
            else:
                host_ip = "127.0.0.1"
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        addr = (host_ip, slot)
        print(f"[*] Conectando vía TCP a {addr} ...")
        sock.connect(addr)
        return sock

    # Linux nativo / macOS
    path = get_unix_socket_path(slot)
    print(f"[*] Conectando vía Unix socket: {path} ...")
    if not os.path.exists(path):
        # Fallback explícito a /tmp, por si XDG_RUNTIME_DIR/TMPDIR no coincide
        fallback = "/tmp/pcsx2.sock" + ("" if slot == DEFAULT_SLOT else f".{slot}")
        print(f"    No existe ahí. Probando fallback: {fallback}")
        path = fallback
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"No se encontró el socket de PINE en ninguna ruta esperada.\n"
                f"Verifica que PCSX2 esté abierto con PINE activado "
                f"(Settings -> Advanced -> PINE Settings -> Enable) y reiniciado."
            )

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(path)
    return sock


def send_command(sock, opcode, payload=b""):
    """Construye y envía un mensaje IPC, devuelve la respuesta cruda (sin el size header)."""
    command_bytes = bytes([opcode]) + payload
    total_size = 4 + len(command_bytes)  # el size incluye los 4 bytes del propio size
    message = struct.pack("<I", total_size) + command_bytes
    sock.sendall(message)

    # leer el size de la respuesta (4 bytes)
    size_bytes = recv_exact(sock, 4)
    reply_size = struct.unpack("<I", size_bytes)[0]
    rest = recv_exact(sock, reply_size - 4)

    status_code = rest[0]
    data = rest[1:]
    return status_code, data


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("El socket se cerró antes de recibir todos los datos esperados.")
        buf += chunk
    return buf


def main():
    print(f"Sistema operativo detectado: {platform.system()}")

    try:
        sock = connect_pine()
    except Exception as e:
        print(f"\n[FALLO] No se pudo conectar: {e}")
        print("\nChecklist:")
        print("  1. ¿PCSX2 está abierto (no solo instalado)?")
        print("  2. Settings -> Advanced -> PINE Settings -> casilla 'Enable' marcada?")
        print("  3. ¿Reiniciaste PCSX2 *completo* después de activarlo?")
        print("  4. ¿El slot configurado es 28011 (el default)? Si lo cambiaste, pásalo aquí.")
        if is_wsl():
            print("  5. Estás en WSL: revisa el Firewall de Windows. Puede estar bloqueando")
            print("     conexiones entrantes al puerto 28011 desde la subred de WSL.")
            print("     Prueba: New-NetFirewallRule -DisplayName 'PINE PCSX2' -Direction Inbound")
            print("     -LocalPort 28011 -Protocol TCP -Action Allow  (en PowerShell admin)")
        sys.exit(1)

    print("[OK] Conexión establecida con el socket de PINE.\n")

    # MsgStatus funciona siempre, incluso sin juego cargado
    status_code, data = send_command(sock, MSG_STATUS)
    if status_code == IPC_OK:
        status_val = struct.unpack("<I", data)[0]
        print(f"[OK] MsgStatus -> {STATUS_NAMES.get(status_val, status_val)}")
    else:
        print("[FALLO] MsgStatus devolvió IPC_FAIL (raro, no debería pasar).")
        sock.close()
        return

    # Los siguientes requieren una VM activa (juego cargado)
    for name, opcode in [("MsgVersion", MSG_VERSION), ("MsgTitle", MSG_TITLE), ("MsgID", MSG_ID)]:
        status_code, data = send_command(sock, opcode)
        if status_code == IPC_OK:
            if opcode == MSG_VERSION or opcode == MSG_TITLE or opcode == MSG_ID:
                # formato: 4 bytes longitud + string null-terminated
                str_len = struct.unpack("<I", data[:4])[0]
                text = data[4:4 + str_len].split(b"\x00")[0].decode("utf-8", errors="replace")
                print(f"[OK] {name} -> {text}")
        else:
            print(f"[INFO] {name} -> IPC_FAIL (probablemente no hay juego cargado todavía)")

    sock.close()
    print("\nTodo funcionando. Ya puedes usar send_command() para leer/escribir memoria"
          " (MSG_READ8/16/32/64, MSG_WRITE8/16/32/64) con direcciones del espacio de la EE.")


if __name__ == "__main__":
    main()
