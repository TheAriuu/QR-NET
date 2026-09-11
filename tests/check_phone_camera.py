# tests/check_phone_camera.py
import cv2

URL = "http://192.168.1.105:8080/video"  # ← cambia por tu IP

cap = cv2.VideoCapture(URL)
if not cap.isOpened():
    print("ERROR: no se pudo conectar. Verifica la IP y que estés en la misma WiFi.")
else:
    print("Conectado. Mostrando video... presiona Q para salir.")
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        cv2.imshow("Camara del celular", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
cap.release()
cv2.destroyAllWindows()