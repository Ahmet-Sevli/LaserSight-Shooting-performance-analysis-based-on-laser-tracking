import cv2
import numpy as np
import time
import serial
import math

# ----------------- AYARLAR -----------------
SERIAL_PORT = 'COM4' 
BAUD_RATE = 9600
CAMERA_URL = "http://<CAMERA_IP>:8080/video"

TARGET_WIDTH = 640
TARGET_HEIGHT = 480
PLAYBACK_SPEED = 0.5
# -------------------------------------------

# --- 1. CİHAZ BAĞLANTILARI ---
try:
    arduino = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.01)
    iot_connected = True
    print("✓ NodeMCU bağlantısı başarılı!")
except:
    print("⚠ NodeMCU bağlantısı bulunamadı!")
    iot_connected = False

cap = cv2.VideoCapture(CAMERA_URL)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

if not cap.isOpened():
    print("✗ HATA: Kameraya bağlanılamadı!")
    exit()

# --- 2. GELİŞMİŞ KALİBRASYON DEĞİŞKENLERİ ---
points = []
matrix = None
target_circles = []

def select_points(event, x, y, flags, param):
    global points, matrix, target_circles
    
    if event == cv2.EVENT_LBUTTONDOWN:
        if len(points) < 7:
            points.append((x, y))
            
            count = len(points)
            if count <= 4:
                print(f"Köşe {count}/4 eklendi.")
            elif count == 5:
                print("✓ MERKEZ noktası belirlendi.")
            elif count == 6:
                print("✓ 1. Halka yarıçapı belirlendi.")
            elif count == 7:
                print("✓ 2. Halka yarıçapı belirlendi. Kalibrasyon bitti!")

cv2.namedWindow("Kalibrasyon")
cv2.setMouseCallback("Kalibrasyon", select_points)

print("\n--- SIRAYLA TIKLAYIN ---")
print("1-4: Kağıdın Köşeleri (Sol Üstten Saat Yönünde)")
print("5: Hedefin Tam Merkezi (12 Puan)")
print("6: 1. Halka Çeperi (Yeşil Çizilecek)")
print("7: 2. Halka Çeperi (Mor Çizilecek)")

while len(points) < 7:
    ret, frame = cap.read()
    if not ret: break
    frame = cv2.resize(frame, (800, 600))
    
    for i, pt in enumerate(points):
        color = (0, 255, 0) if i < 4 else (255, 0, 0)
        cv2.circle(frame, pt, 5, color, -1)
        cv2.putText(frame, str(i+1), (pt[0]+10, pt[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    cv2.imshow("Kalibrasyon", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'): exit()

# Geometrik Hesaplamalar
pts1 = np.float32(points[:4])
pts2 = np.float32([[0, 0], [TARGET_WIDTH, 0], [TARGET_WIDTH, TARGET_HEIGHT], [0, TARGET_HEIGHT]])
matrix = cv2.getPerspectiveTransform(pts1, pts2)

extra_points = np.array([points[4:]], dtype='float32')
warped_extra = cv2.perspectiveTransform(extra_points, matrix)[0]

center = (int(warped_extra[0][0]), int(warped_extra[0][1]))
r1 = int(math.sqrt((warped_extra[1][0]-warped_extra[0][0])**2 + (warped_extra[1][1]-warped_extra[0][1])**2))
r2 = int(math.sqrt((warped_extra[2][0]-warped_extra[0][0])**2 + (warped_extra[2][1]-warped_extra[0][1])**2))

cv2.destroyWindow("Kalibrasyon")

# --- 3. KAYIT DÖNGÜSÜ ---
recording = True
trajectory = [] 
serial_buffer = ""
start_time = time.time()

print("\nKayıt başladı. Tetiğe basana kadar veriler toplanıyor...")

while recording:
    ret, frame = cap.read()
    if not ret: break
    frame = cv2.resize(frame, (800, 600))

    if iot_connected and arduino.in_waiting > 0:
        serial_buffer += arduino.read(arduino.in_waiting).decode('utf-8', errors='ignore')
        if "TETIK" in serial_buffer:
            print("✓ ATIŞ YAPILDI!")
            recording = False
            break

    warped = cv2.warpPerspective(frame, matrix, (TARGET_WIDTH, TARGET_HEIGHT))
    
    hsv = cv2.cvtColor(warped, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([0, 100, 100]), np.array([10, 255, 255])) + \
           cv2.inRange(hsv, np.array([160, 100, 100]), np.array([180, 255, 255]))
    
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        c = max(contours, key=cv2.contourArea)
        if cv2.contourArea(c) > 3:
            M = cv2.moments(c)
            if M["m00"] != 0:
                cx, cy = int(M["m10"]/M["m00"]), int(M["m01"]/M["m00"])
                trajectory.append((cx, cy, time.time() - start_time))
                cv2.circle(warped, (cx, cy), 5, (0, 255, 0), -1)

    cv2.circle(warped, center, 5, (255, 0, 0), -1)
    cv2.circle(warped, center, r1, (0, 255, 0), 2)
    cv2.circle(warped, center, r2, (255, 0, 255), 2)

    cv2.imshow("Canli Hedef", warped)
    if cv2.waitKey(1) & 0xFF == ord('q'): break

cap.release()
cv2.destroyAllWindows()

# --- 4. PLAYBACK VE ANALİZ ---
print("\nAnaliz Ekranı Hazırlanıyor...")

canvas = np.zeros((TARGET_HEIGHT, TARGET_WIDTH, 3), dtype=np.uint8)

cv2.circle(canvas, center, 5, (255, 0, 0), -1) 
cv2.circle(canvas, center, r1, (0, 255, 0), 2)  
cv2.circle(canvas, center, r2, (255, 0, 255), 2) 

if trajectory:
    # --- YENİ: SCATT MATEMATİĞİ (TİTREME VE TETİK EZME HESAPLAMALARI) ---
    total_time = trajectory[-1][2] 
    hold_points = []
    trigger_points = []
    
    # Atışın son 0.2 saniyesini "tetik ezme" anı, öncesini "nişan alma" anı kabul ediyoruz.
    for pt in trajectory:
        x, y, t = pt
        if t < total_time - 0.2:
            hold_points.append((x, y))
        else:
            trigger_points.append((x, y))
            
    # 1. Titreme Skoru (Hold Stability) Hesapla
    tremor_score = 0
    if len(hold_points) > 2:
        pts_arr = np.array(hold_points)
        std_x = np.std(pts_arr[:, 0])
        std_y = np.std(pts_arr[:, 1])
        tremor_score = (std_x + std_y) / 2
        avg_hold_x = int(np.mean(pts_arr[:, 0]))
        avg_hold_y = int(np.mean(pts_arr[:, 1]))
    else:
        avg_hold_x, avg_hold_y = center[0], center[1]

    # 2. Tetik Ezme (Trigger Jerk) Hesapla
    jerk_score = 0
    if len(trigger_points) > 0:
        final_x, final_y = trigger_points[-1][0], trigger_points[-1][1]
        jerk_score = math.hypot(final_x - avg_hold_x, final_y - avg_hold_y)
    # ---------------------------------------------------------------------

    prev_x, prev_y, prev_t = trajectory[0]
    total_points_len = max(1, len(trajectory)) 
    
    # İstatistik Sayaçları
    count_r1 = 0
    count_r2 = 0
    count_out = 0
    
    for i, (x, y, t) in enumerate(trajectory):
        delay = (t - prev_t) * PLAYBACK_SPEED
        if delay > 0:
            time.sleep(delay)
            
        # Mesafe Hesaplama (Bu nokta hedefin neresinde?)
        distance = math.hypot(x - center[0], y - center[1])
        if distance <= r1:
            count_r1 += 1
        elif distance <= r2:
            count_r2 += 1
        else:
            count_out += 1
            
        # Anlık Yüzdeleri Hesapla
        current_total = i + 1
        pct_r1 = (count_r1 / current_total) * 100
        pct_r2 = (count_r2 / current_total) * 100
        pct_out = (count_out / current_total) * 100
            
        # İzleri Çiz (Gradyan Rengiyle)
        intensity = int(50 + (205 * (i / total_points_len)))
        line_color = (0, 0, intensity) 
        cv2.line(canvas, (prev_x, prev_y), (x, y), line_color, 2, cv2.LINE_AA) 
        
        display_frame = canvas.copy()
        
        # SAYDAM Lazer Başı (Overlay Mantığı)
        overlay = display_frame.copy()
        cv2.circle(overlay, (x, y), 9, (0, 255, 255), -1) 
        cv2.addWeighted(overlay, 0.5, display_frame, 0.5, 0, display_frame) 
        
        # YÜZDE İstatistiklerini Yazdır (Sağ Üst Köşe)
        text_x = TARGET_WIDTH - 210
        cv2.putText(display_frame, f"Merkez (Yesil): %{pct_r1:.1f}", (text_x, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        cv2.putText(display_frame, f"2. Halka (Mor): %{pct_r2:.1f}", (text_x, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1)
        cv2.putText(display_frame, f"Karavana: %{pct_out:.1f}", (text_x, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
        
        # HATA İstatistiklerini Yazdır (Sol Üst Köşe)
        cv2.putText(display_frame, f"El Titremesi: {tremor_score:.1f} px", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        jerk_color = (0, 0, 255) if jerk_score > 15 else (0, 255, 0)
        cv2.putText(display_frame, f"Tetik Ezme: {jerk_score:.1f} px", (15, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, jerk_color, 1)

        cv2.imshow("SCATT Playback", display_frame)
        cv2.waitKey(1)
        
        prev_x, prev_y, prev_t = x, y, t

print("\n✓ Playback bitti.")
print("Kapatmak için Playback penceresi seçiliyken klavyeden HERHANGİ BİR TUŞA basın.")
cv2.waitKey(0)
cv2.destroyAllWindows()