"""
tests/hardware_demo.py
Demo de transmisión real usando DispositivoLuzAdaptador.

Cómo correrlo:
    # Solo webcam del laptop (sin celular)
    python tests/hardware_demo.py

    # Con celular como cámara IP (IP Webcam app)
    python tests/hardware_demo.py --url http://192.168.1.105:8080/video

Argumentos:
    --url     URL del stream del celular o índice de webcam (default: 0)
    --msg     Mensaje a transmitir
    --colors  Color depth: 2, 4, 8 o 16  (default: 2)
    --fps     Frames QR por segundo       (default: 2)
"""
from __future__ import annotations

import argparse
import sys
import os
import time
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from transmision.implementation.camera import OpenCVCamera
from transmision.implementation.color_palette import ColorPalette
from transmision.implementation.grid import Grid64Codec
from transmision.implementation.compressor import ZstdCompressor
from transmision.implementation.queue import FifoFrameQueue
from transmision.adapter import DispositivoLuzAdaptador
from transmision.frames import HandshakeFrame, FrameType
from common.other import CompressionAlgorithm


def parse_args():
    p = argparse.ArgumentParser(description="QR-NET hardware demo")
    p.add_argument("--url",    default="0",
                   help="URL del celular o indice de webcam (default: 0)")
    p.add_argument("--msg",    default="Hola desde QR-NET!",
                   help="Mensaje a transmitir")
    p.add_argument("--colors", type=int, default=2, choices=[2, 4, 8, 16],
                   help="Color depth (default: 2)")
    p.add_argument("--fps",    type=float, default=2.0,
                   help="Frames QR por segundo (default: 2)")
    return p.parse_args()


def check_camera(device) -> bool:
    """Verifica rapidamente si la camara es accesible."""
    if sys.platform == "win32" and isinstance(device, int):
        cap = cv2.VideoCapture(device, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(device)
    ok = cap.isOpened()
    cap.release()
    return ok


def main():
    args   = parse_args()
    device = int(args.url) if args.url.isdigit() else args.url
    interval_ms  = int(1000 / args.fps)
    color_depth  = {2: 0, 4: 1, 8: 2, 16: 3}[args.colors]

    print("=" * 55)
    print("  QR-NET -- Demo con hardware real")
    print("=" * 55)
    print(f"\n  Camara    : {device}")
    print(f"  Colores   : {args.colors}")
    print(f"  Intervalo : {interval_ms} ms entre frames")
    print(f"  Mensaje   : {args.msg!r}")

    # ── Paso 0: verificar camara ──────────────────────────────────────────────
    print("\n[0] Verificando acceso a la camara...")
    if not check_camera(device):
        print("    ERROR: no se pudo abrir la camara.")
        print()
        print("    Posibles causas en Windows:")
        print("    * Otra app la tiene tomada (Teams, Zoom, navegador).")
        print("      Cierralas y vuelve a intentar.")
        print("    * Falta permiso: Configuracion > Privacidad > Camara.")
        print("    * Si usas celular: verifica la URL y la misma WiFi.")
        return
    print("    OK")

    # ── Construir adaptador ───────────────────────────────────────────────────
    mac_local = os.urandom(6)

    adaptador = DispositivoLuzAdaptador(
        mac         = mac_local,
        camera      = OpenCVCamera(device_id=device, fps=args.fps),
        color_codec = ColorPalette(n_colors=args.colors),
        grid_codec  = Grid64Codec(),
        compressor  = ZstdCompressor(level=3),
        frame_queue = FifoFrameQueue(maxsize=32),
        window_name = "QR-NET TX",
    )

    # Inyectar handshake simulado para que is_available() retorne True.
    # En produccion esto lo hace el 3-way handshake con el par.
    fake_hs = HandshakeFrame(
        frame_type  = FrameType.ACK,
        src_mac     = mac_local,
        dst_mac     = b'\xff' * 6,
        color_depth = color_depth,
        grid_size   = 8,
        ecc_level   = 2,
        sync_method = 3,
        interval_ms = interval_ms,
        compression = CompressionAlgorithm.ZSTD,
    )
    adaptador._negotiated  = fake_hs
    adaptador._interval_ms = interval_ms
    adaptador._peer_mac    = b'\xff' * 6

    # ── Paso 1: abrir camara ──────────────────────────────────────────────────
    print("\n[1] Abriendo camara...")
    try:
        adaptador.start()
        print("    OK")
    except Exception as e:
        print(f"    ERROR: {e}")
        return

    # ── Paso 2: mostrar QR de handshake ──────────────────────────────────────
    print("\n[2] Mostrando QR de handshake en pantalla...")
    print("    Busca la ventana 'QR-NET TX' y apunta el celular ahi.")
    print("    (Presiona ENTER para continuar)")
    adaptador._display_handshake(fake_hs)
    input()

    # ── Paso 3: transmitir datos ──────────────────────────────────────────────
    print("\n[3] Transmitiendo datos en QR...")
    ok = adaptador.send(args.msg.encode())
    if ok:
        print(f"    OK -- {len(args.msg.encode())} bytes transmitidos en QR.")
    else:
        print("    WARN: send() retorno False (revisa is_available)")
    print("    (Presiona ENTER para continuar)")
    input()

    # ── Paso 4: esperar recepcion ─────────────────────────────────────────────
    print("\n[4] Esperando datos entrantes (10 segundos)...")
    print("    Para probar: muestra otro QR frente a la camara.")
    deadline = time.time() + 10
    received = None
    while time.time() < deadline:
        received = adaptador.receive()
        if received:
            break
        print(f"    Esperando... {int(deadline - time.time())}s  ", end="\r")
        time.sleep(0.3)
    print()

    if received:
        print(f"    Recibido: {received!r}")
    else:
        print("    No se recibieron datos (normal si no hay otro nodo).")

    # ── Cerrar ────────────────────────────────────────────────────────────────
    adaptador.stop()

    print("\n" + "=" * 55)
    print("  Resumen")
    print("=" * 55)
    print(f"  Camara abierta : OK")
    print(f"  QR renderizado : OK  <- ya lo viste en pantalla")
    print(f"  send()         : {'OK' if ok else 'FAIL'}")
    print(f"  receive()      : {'OK -- ' + repr(received) if received else 'Sin datos (necesitas otro nodo)'}")


if __name__ == "__main__":
    main()