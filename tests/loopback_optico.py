"""
tests/loopback_optico.py
Test de loopback óptico completo para QR-NET.

Prueba la cadena física completa sin necesitar dos máquinas:

  [datos]
     │
     ▼
  Grid64Codec.encode_grid()       ← codificación a imagen QR
     │
     ▼
  cv2.imshow("QR-NET TX")         ← renderizado en pantalla
     │
     ▼ (luz real o captura de pantalla)
  ScreenCaptureCamera.capture()   ← captura del pixel buffer del sistema
     │
     ▼
  Grid64Codec.decode_grid()       ← decodificación desde imagen
     │
     ▼
  [datos recuperados] == [datos originales]  ← verificación

Fases:
  [1] Loopback en memoria    — encode → decode sin pantalla
  [2] Loopback visual        — encode → pantalla → captura → decode

Cómo correrlo:
    python tests/loopback_optico.py

Dependencias:
    pip install pillow          (captura de pantalla en Windows/macOS)
    pip install mss             (alternativa multiplataforma, más rápida)
"""
from __future__ import annotations

import sys
import os
import time
import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from transmision.implementation.color_palette import ColorPalette
from transmision.implementation.grid import Grid64Codec, _IMG_SIDE
from transmision.implementation.compressor import ZstdCompressor

WINDOW_NAME = "QR-NET TX"


# ── Captura de pantalla ────────────────────────────────────────────────────────

def capture_window(window_name: str) -> np.ndarray | None:
    """
    Captura el contenido de la ventana OpenCV usando PIL.ImageGrab.
    Retorna imagen BGR (H, W, 3) o None si la ventana no existe.
    """
    try:
        rect = cv2.getWindowImageRect(window_name)
    except Exception:
        return None

    x, y, w, h = rect
    if w <= 0 or h <= 0:
        return None

    # Intentar con mss (más rápido y preciso)
    try:
        import mss
        with mss.mss() as sct:
            monitor = {"top": y, "left": x, "width": w, "height": h}
            shot = sct.grab(monitor)
            frame = np.array(shot)
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    except ImportError:
        pass

    # Fallback: PIL.ImageGrab (viene con Pillow)
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        frame = np.array(img)
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    except Exception:
        return None


def show_and_capture(img: np.ndarray, wait_ms: int = 500) -> np.ndarray | None:
    """
    Muestra img en la ventana QR-NET TX, espera wait_ms ms y captura la pantalla.
    """
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, _IMG_SIDE, _IMG_SIDE)
    cv2.imshow(WINDOW_NAME, img)
    cv2.waitKey(wait_ms)   # tiempo para que el sistema renderice la ventana
    return capture_window(WINDOW_NAME)


# ── Fase 1: loopback en memoria ───────────────────────────────────────────────

def fase1_memoria(grid: Grid64Codec, codec: ColorPalette, datos: bytes) -> bool:
    print("\n" + "─" * 55)
    print("  Fase 1 — Loopback en memoria (sin pantalla)")
    print("─" * 55)
    print(f"  Datos originales : {datos!r}  ({len(datos)} bytes)")

    # Encode
    img_encoded = grid.encode_grid(datos, codec)
    print(f"  Imagen generada  : {img_encoded.shape}  dtype={img_encoded.dtype}")

    # Decode inmediato desde la misma imagen
    try:
        recuperados = grid.decode_grid(img_encoded, codec)
    except Exception as e:
        print(f"  ERROR en decode  : {e}")
        return False

    ok = recuperados == datos
    print(f"  Datos recuperados: {recuperados!r}")
    print(f"  Resultado        : {'OK' if ok else 'FAIL — los datos no coinciden'}")
    return ok


# ── Fase 2: loopback visual ───────────────────────────────────────────────────

def fase2_visual(grid: Grid64Codec, codec: ColorPalette, datos: bytes) -> bool:
    print("\n" + "─" * 55)
    print("  Fase 2 — Loopback visual (pantalla → captura)")
    print("─" * 55)
    print("  Mostrando QR en pantalla y capturando con screenshot...")

    img_encoded = grid.encode_grid(datos, codec)

    # Mostrar en pantalla y capturar
    captura = show_and_capture(img_encoded, wait_ms=600)
    if captura is None:
        print("  ERROR: no se pudo capturar la ventana.")
        print("  Asegurate de que la ventana 'QR-NET TX' sea visible en pantalla.")
        return False

    print(f"  Captura obtenida : {captura.shape}")

    # Decode desde la captura (puede incluir bordes de ventana)
    try:
        recuperados = grid.decode_grid(captura, codec)
    except Exception as e:
        print(f"  ERROR en decode  : {e}")
        print("  Posible causa: la ventana estaba tapada o muy pequeña.")
        return False

    ok = recuperados == datos
    print(f"  Datos originales : {datos!r}")
    print(f"  Datos recuperados: {recuperados!r}")
    print(f"  Resultado        : {'OK' if ok else 'FAIL — los datos no coinciden'}")
    return ok


# ── Fase 3: loopback con compresión ──────────────────────────────────────────

def fase3_compresion(grid: Grid64Codec, codec: ColorPalette,
                     compressor: ZstdCompressor, datos: bytes) -> bool:
    print("\n" + "─" * 55)
    print("  Fase 3 — Loopback visual con compresion zstd")
    print("─" * 55)

    comprimido = compressor.compress(datos)
    print(f"  Original   : {len(datos)} bytes")
    print(f"  Comprimido : {len(comprimido)} bytes")

    img = grid.encode_grid(comprimido, codec)
    captura = show_and_capture(img, wait_ms=600)
    if captura is None:
        print("  ERROR: no se pudo capturar la ventana.")
        return False

    try:
        raw = grid.decode_grid(captura, codec)
        recuperados = compressor.decompress(raw)
    except Exception as e:
        print(f"  ERROR: {e}")
        return False

    ok = recuperados == datos
    print(f"  Datos recuperados: {recuperados!r}")
    print(f"  Resultado        : {'OK' if ok else 'FAIL'}")
    return ok


# ── Fase 4: múltiples mensajes (simula stream de frames) ─────────────────────

def fase4_stream(grid: Grid64Codec, codec: ColorPalette,
                 compressor: ZstdCompressor) -> bool:
    print("\n" + "─" * 55)
    print("  Fase 4 — Stream de frames (5 mensajes)")
    print("─" * 55)

    mensajes = [
        b"Frame 0: inicio de transmision",
        b"Frame 1: datos de red QR-NET",
        b"Frame 2: protocolo Rainbow R1",
        b"Frame 3: loopback optico OK",
        b"Frame 4: fin de stream",
    ]

    resultados = []
    for i, msg in enumerate(mensajes):
        comprimido = compressor.compress(msg)
        img = grid.encode_grid(comprimido, codec)
        captura = show_and_capture(img, wait_ms=400)
        if captura is None:
            print(f"  [{i}] ERROR: captura fallida")
            resultados.append(False)
            continue
        try:
            raw = grid.decode_grid(captura, codec)
            recuperado = compressor.decompress(raw)
            ok = recuperado == msg
        except Exception as e:
            print(f"  [{i}] ERROR decode: {e}")
            resultados.append(False)
            continue

        simbolo = "OK" if ok else "FAIL"
        print(f"  [{i}] {simbolo}  {msg!r}")
        resultados.append(ok)

    total_ok = sum(resultados)
    print(f"\n  {total_ok}/{len(mensajes)} frames correctos")
    return all(resultados)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  QR-NET — Test de loopback optico")
    print("=" * 55)
    print()
    print("  Este test codifica datos en QR, los muestra en")
    print("  pantalla, captura la ventana con screenshot y")
    print("  los decodifica. Verifica la cadena completa")
    print("  encode → pantalla → captura → decode.")
    print()
    print("  IMPORTANTE: no tapes la ventana 'QR-NET TX'")
    print("  mientras el test esta corriendo.")
    print()
    input("  Presiona ENTER para comenzar...")

    # Configuracion
    n_colors = 2   # blanco/negro — más robusto en captura de pantalla
    codec      = ColorPalette(n_colors=n_colors)
    grid       = Grid64Codec()
    compressor = ZstdCompressor(level=3)

    max_bytes  = grid.max_payload_bytes_for(codec)
    print(f"\n  Color depth  : {n_colors} colores ({codec.bits_per_cell} bit/módulo)")
    print(f"  Max payload  : {max_bytes} bytes por frame")

    datos = b"Hola desde QR-NET loopback optico!"

    # Ejecutar fases
    r1 = fase1_memoria(grid, codec, datos)
    r2 = fase2_visual(grid, codec, datos)
    r3 = fase3_compresion(grid, codec, compressor, datos)
    r4 = fase4_stream(grid, codec, compressor)

    # Cerrar ventana
    cv2.destroyAllWindows()

    # Resumen
    print("\n" + "=" * 55)
    print("  Resumen final")
    print("=" * 55)
    resultados = [
        ("Fase 1 — Loopback en memoria  ", r1),
        ("Fase 2 — Loopback visual      ", r2),
        ("Fase 3 — Loopback + zstd      ", r3),
        ("Fase 4 — Stream de 5 frames   ", r4),
    ]
    for nombre, ok in resultados:
        simbolo = "OK" if ok else "FAIL"
        print(f"  {nombre}: {simbolo}")

    total = sum(ok for _, ok in resultados)
    print(f"\n  {total}/{len(resultados)} fases pasaron")

    if total == len(resultados):
        print()
        print("  La cadena optica completa funciona correctamente.")
        print("  Capa 1 verificada end-to-end.")
    else:
        print()
        print("  Algunas fases fallaron.")
        print("  Verifica que la ventana 'QR-NET TX' estuviera visible.")


if __name__ == "__main__":
    main()