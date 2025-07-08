#!/user/bin/env python

# Copyright (c) 2024，WuChao D-Robotics.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# 注意: 此程序在RDK板端端运行
# Attention: This program runs on RDK board.

import cv2
import numpy as np
from scipy.special import softmax
# from scipy.special import expit as sigmoid
from hobot_dnn import pyeasy_dnn as dnn  # BSP Python API

from time import time, sleep
import argparse
import logging
import socket
import threading
import base64
import json
import queue
import datetime
import serial
import struct

logging.basicConfig(
    level=logging.DEBUG,
    format='[%(name)s] [%(asctime)s.%(msecs)03d] [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S')
logger = logging.getLogger("RDK_YOLO")

command_queue = queue.Queue()
frame_queue = queue.Queue(maxsize=5)
exit_event = threading.Event()
sending_allowed = threading.Event()
capture_frame_event = threading.Event()
gp_location = {"latitude": None, "longitude": None}
gps_location_sent = False
temp_humidity_data = {"temperature": None, "humidity": None}
temp_humidity_lock = threading.Lock()


def calculate_modbus_crc(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def read_temperature_humidity(port, baudrate, device_id=1):
    logger.info(f"Temperature/Humidity reader started on {port} at {baudrate} baud")

    try:
        ser = serial.Serial(port, baudrate, timeout=1,
                            bytesize=8, parity='N', stopbits=1)
        logger.info("Temperature/Humidity serial port opened successfully")
    except Exception as e:
        logger.error(f"Failed to open temperature/humidity serial port: {e}")
        return

    read_command = bytearray([device_id, 0x03, 0x00, 0x00, 0x00, 0x02])
    crc = calculate_modbus_crc(read_command)
    read_command.extend([crc & 0xFF, (crc >> 8) & 0xFF])

    while not exit_event.is_set():
        try:
            ser.write(read_command)

            response = ser.read(9)

            if len(response) == 9:
                received_crc = (response[8] << 8) | response[7]
                calculated_crc = calculate_modbus_crc(response[:-2])

                if received_crc == calculated_crc:
                    temp_raw = struct.unpack('>h', response[3:5])[0]
                    humidity_raw = struct.unpack('>h', response[5:7])[0]

                    temperature = temp_raw * 0.01
                    humidity = humidity_raw * 0.01

                    with temp_humidity_lock:
                        temp_humidity_data["temperature"] = round(temperature, 2)
                        temp_humidity_data["humidity"] = round(humidity, 2)

                    logger.debug(f"Temperature: {temperature:.2f}°C, Humidity: {humidity:.2f}%")
                else:
                    logger.warning("CRC check failed for temperature/humidity data")
            else:
                logger.warning(f"Incomplete response: received {len(response)} bytes")

            sleep(10)

        except Exception as e:
            logger.error(f"Error reading temperature/humidity: {e}")
            sleep(10)

    ser.close()
    logger.info("Temperature/Humidity reader exiting")


def periodic_temp_humidity_sender():
    logger.info("Periodic temperature/humidity sender started")

    while not exit_event.is_set():
        try:
            sleep(10)

            if sending_allowed.is_set():
                with temp_humidity_lock:
                    temp = temp_humidity_data.get("temperature")
                    humidity = temp_humidity_data.get("humidity")

                if temp is not None and humidity is not None:
                    data = {
                        "type": "Tem_Hum",
                        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "temperature": temp,
                        "humidity": humidity
                    }

                    try:
                        if frame_queue.full():
                            logger.warning("Frame queue full, discarding oldest")
                            try:
                                frame_queue.get_nowait()
                                frame_queue.task_done()
                            except queue.Empty:
                                pass

                        frame_queue.put(data)
                        logger.info(f"Queued temperature/humidity data: {temp}°C, {humidity}%")

                    except Exception as e:
                        logger.error(f"Failed to queue temperature/humidity data: {e}")
                else:
                    logger.debug("No valid temperature/humidity data available")
            else:
                logger.debug("Sending not allowed, skipping temperature/humidity data")

        except Exception as e:
            logger.error(f"Error in periodic temperature/humidity sender: {e}")

    logger.info("Periodic temperature/humidity sender exiting")


def command_receiver(port):
    logger.info(f"Command receiver started on port {port}")

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind(('0.0.0.0', port))
            server_socket.listen(1)
            server_socket.settimeout(1.0)

            logger.info(f"Command server listening on port {port}")

            while not exit_event.is_set():
                try:
                    client_socket, addr = server_socket.accept()
                    logger.info(f"Command connection from {addr}")
                    client_socket.settimeout(1.0)

                    try:
                        while not exit_event.is_set():
                            command = client_socket.recv(1)
                            if not command:
                                break

                            command_str = command.decode('utf-8')
                            logger.info(f"Received command: {command_str}")

                            command_queue.put(command_str)

                            client_socket.sendall(b'C')

                    except socket.timeout:
                        pass
                    except Exception as e:
                        logger.error(f"Command socket error: {e}")
                    finally:
                        client_socket.close()
                        logger.info("Command connection closed")

                except socket.timeout:
                    continue
                except Exception as e:
                    logger.error(f"Server socket error: {e}")
                    sleep(1)

    except Exception as e:
        logger.error(f"Failed to start command server: {e}")

    logger.info("Command receiver exiting")


def data_sender(server_ip, server_port):
    logger.info(f"Data sender thread started. Target: {server_ip}:{server_port}")

    while not exit_event.is_set():
        try:
            data = frame_queue.get(timeout=1.0)

            if not sending_allowed.is_set():
                logger.debug("Sending not allowed, discarding data")
                frame_queue.task_done()
                continue

            logger.info(f"Preparing to send data: type={data['type']}")

            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(5.0)
                    logger.info(f"Connecting to {server_ip}:{server_port}")

                    try:
                        sock.connect((server_ip, server_port))
                        logger.info("TCP connection established")
                    except Exception as e:
                        logger.error(f"TCP connection failed: {e}")
                        continue

                    json_data = json.dumps(data).encode('utf-8')
                    data_size = len(json_data)
                    size_bytes = data_size.to_bytes(4, byteorder='big')
                    logger.info(f"Sending {len(json_data)} bytes via TCP")

                    sock.sendall(size_bytes + json_data)

                    logger.info(f"Sent {data['type']} data, size: {data_size} bytes")

            except Exception as e:
                logger.error(f"TCP socket error: {e}")

            frame_queue.task_done()

        except queue.Empty:
            continue

    logger.info("Data sender thread exiting")


def read_gps_location(gps_serial):
    if gps_serial is None:
        return

    try:
        line = gps_serial.readline().decode('ascii', errors='ignore').strip()
        if line.startswith('$GNRMC'):
            parts = line.split(',')
            if len(parts) < 13 or parts[2] == 'A':  # Check if data is valid
                # Latitude
                lat = parts[3]
                lat_dir = parts[4]
                if lat:
                    lat_deg = float(lat[:2])
                    lat_min = float(lat[2:])
                    lat_total = lat_deg + lat_min / 60
                    if lat_dir == 'S':
                        lat_total = -lat_total
                    gp_location["latitude"] = round(lat_total, 2)

                # Longitude
                lon = parts[5]
                lon_dir = parts[6]
                if lon:
                    lon_deg = float(lon[:3])
                    lon_min = float(lon[3:])
                    lon_total = lon_deg + lon_min / 60
                    if lon_dir == 'W':
                        lon_total = -lon_total
                    gp_location["longitude"] = round(lon_total, 2)

    except Exception as e:
        logger.error(f"Error reading GPS data: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-path', type=str, default='/home/sunrise/Desktop/RDK_FIRE/x5m.bin',
                        help="""Path to BPU Quantized *.bin Model.
                                RDK X3(Module): Bernoulli2.
                                RDK Ultra: Bayes.
                                RDK X5(Module): Bayes-e.
                                RDK S100: Nash-e.
                                RDK S100P: Nash-m.""")
    parser.add_argument('--camera-id', type=int, default=0, help='Camera device ID (default=0)')
    parser.add_argument('--classes-num', type=int, default=3, help='Classes Num to Detect.')
    parser.add_argument('--nms-thres', type=float, default=0.5, help='IoU threshold.')
    parser.add_argument('--score-thres', type=float, default=0.65, help='confidence threshold.')
    parser.add_argument('--reg', type=int, default=16, help='DFL reg layer.')
    # parser.add_argument('--server-ip', type=str, default='192.168.58.62', help='Server IP for sending data')
    parser.add_argument('--server-ip', type=str, default='192.168.112.218', help='Server IP for sending data')
    parser.add_argument('--server-port', type=int, default=8888, help='Server port for sending data')
    parser.add_argument('--command-port', type=int, default=8080, help='Command listening port')
    parser.add_argument('--gps-port', type=str, default='/dev/ttyUSB1', help='GPS serial port')
    parser.add_argument('--gps-baudrate', type=int, default=115200, help='GPS baudrate')
    parser.add_argument('--temp-humidity-port', type=str, default='/dev/ttyUSB0',
                        help='Temperature/Humidity sensor serial port')
    parser.add_argument('--temp-humidity-baudrate', type=int, default=9600, help='Temperature/Humidity sensor baudrate')
    parser.add_argument('--temp-humidity-id', type=int, default=1, help='Temperature/Humidity sensor Modbus ID')
    opt = parser.parse_args()
    logger.info(opt)

    detection_enabled = False
    last_send_time = 0
    send_interval = 4
    fire_detected = False
    first_fire_frame = None
    first_fire_sent = False

    command_thread = threading.Thread(
        target=command_receiver,
        args=(opt.command_port,),
        daemon=True
    )
    command_thread.start()
    logger.info("Command receiver thread started")

    data_thread = threading.Thread(
        target=data_sender,
        args=(opt.server_ip, opt.server_port),
        daemon=True
    )
    data_thread.start()
    logger.info("Data sender thread started")

    temp_humidity_thread = threading.Thread(
        target=read_temperature_humidity,
        args=(opt.temp_humidity_port, opt.temp_humidity_baudrate, opt.temp_humidity_id),
        daemon=True
    )
    temp_humidity_thread.start()
    logger.info("Temperature/Humidity reader thread started")

    periodic_sender_thread = threading.Thread(
        target=periodic_temp_humidity_sender,
        daemon=True
    )
    periodic_sender_thread.start()
    logger.info("Periodic temperature/humidity sender thread started")

    cap = cv2.VideoCapture(opt.camera_id)
    if not cap.isOpened():
        logger.error("Can not open the camera!")
        exit(1)

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    model = YOLO11_Detect(opt)

    frame_count = 0
    start_time = time()
    fps = 0

    try:
        gps_serial = serial.Serial(opt.gps_port, opt.gps_baudrate, timeout=1)
        logger.info(f"GPS serial port {opt.gps_port} opened at {opt.gps_baudrate} baud")
    except Exception as e:
        logger.error(f"Failed to open GPS serial port: {e}")
        gps_serial = None

    while True:
        ret, frame = cap.read()
        if not ret:
            logger.error("NO FRAME!")
            break

        while not command_queue.empty():
            try:
                command = command_queue.get_nowait()
                if command == 'A':
                    detection_enabled = True
                    sending_allowed.set()
                    read_gps_location(gps_serial)
                    logger.info("Command A received: Detection enabled")
                    if gp_location["latitude"] is not None and gp_location["longitude"] is not None:
                        data = {
                            "type": "GPS_LOCATION",
                            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "latitude": gp_location["latitude"],
                            "longitude": gp_location["longitude"]
                        }
                        try:
                            if frame_queue.full():
                                logger.warning("Frame queue full, discarding oldest")
                                try:
                                    frame_queue.get_nowait()
                                    frame_queue.task_done()
                                except queue.Empty:
                                    pass

                            frame_queue.put(data)
                            logger.info("Queued GPS location data for sending")
                            gps_location_sent = True
                        except Exception as e:
                            logger.error(f"Failed to queue GPS data: {e}")
                    else:
                        logger.warning("GPS data not available")
                elif command == 'B':
                    detection_enabled = False
                    sending_allowed.clear()
                    fire_detected = False
                    first_fire_frame = None
                    first_fire_sent = False
                    logger.info("Command B received: Detection disabled")
                elif command == 'O':
                    sending_allowed.clear()
                    logger.info("Command O received: Sending disabled")
                elif command == 'S':
                    sending_allowed.set()
                    logger.info("Command S received: Sending enabled")
                elif command == 'K':
                    if not sending_allowed.is_set():
                        sending_allowed.set()
                        logger.info("Command K: Auto-enabled sending for capture")
                    capture_frame_event.set()
                    logger.info("Command K received: Capture current frame requested")
            except queue.Empty:
                break

        if capture_frame_event.is_set():
            capture_frame_event.clear()

            success, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if success:
                image_base64 = base64.b64encode(buffer).decode('utf-8')

                with temp_humidity_lock:
                    temp = temp_humidity_data.get("temperature")
                    humidity = temp_humidity_data.get("humidity")

                data = {
                    "type": "USER SCREENSHOT",
                    "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "detections": fire_detections,
                    "image": image_base64,
                    "temperature": temp,
                    "humidity": humidity
                }

                try:
                    if frame_queue.full():
                        logger.warning("Frame queue full, discarding oldest")
                        try:
                            frame_queue.get_nowait()
                            frame_queue.task_done()
                        except queue.Empty:
                            pass

                    frame_queue.put(data)
                    logger.info("Queued manual capture frame for sending")

                except Exception as e:
                    logger.error(f"Failed to queue capture data: {e}")
            else:
                logger.error("Failed to encode capture frame to JPEG!")

        if detection_enabled:
            input_tensor = model.preprocess_yuv420sp(frame)
            outputs = model.c2numpy(model.forward(input_tensor))
            results = model.postProcess(outputs)

            current_frame_has_fire = False
            current_frame_has_smoke = False
            max_confidence = 0.0
            fire_detections = []

            for class_id, score, x1, y1, x2, y2 in results:
                print("(%d, %d, %d, %d) -> %s: %.2f" % (x1, y1, x2, y2, coco_names[class_id], score))
                draw_detection(frame, (x1, y1, x2, y2), score, class_id)

                if class_id == 0:  # fire
                    current_frame_has_fire = True
                    max_confidence = max(max_confidence, float(score))
                    fire_detections.append({
                        "class": coco_names[class_id],
                        "confidence": round(float(score), 2)
                    })
                elif class_id == 1:  # smoke
                    current_frame_has_smoke = True
                    max_confidence = max(max_confidence, float(score))
                    fire_detections.append({
                        "class": coco_names[class_id],
                        "confidence": round(float(score), 2)
                    })

            if current_frame_has_fire or current_frame_has_smoke:
                if not fire_detected:
                    fire_detected = True
                    first_fire_frame = frame.copy()
                    first_fire_sent = False
                    logger.info("First fire/smoke detection!")

                if sending_allowed.is_set():
                    current_time = time()

                    should_send = (not first_fire_sent) or (current_time - last_send_time >= send_interval)

                    if should_send:
                        success, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        if success:
                            image_base64 = base64.b64encode(buffer).decode('utf-8')

                            with temp_humidity_lock:
                                temp = temp_humidity_data.get("temperature")
                                humidity = temp_humidity_data.get("humidity")

                            data = {
                                "type": "A1",
                                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "detections": fire_detections,
                                "image": image_base64,
                                # "temperature": temp,
                                # "humidity": humidity
                            }

                            try:
                                if frame_queue.full():
                                    logger.warning("Frame queue full, discarding oldest")
                                    try:
                                        frame_queue.get_nowait()
                                        frame_queue.task_done()
                                    except queue.Empty:
                                        pass

                                frame_queue.put(data)
                                logger.info(f"Queued fire/smoke detection data for sending")

                                last_send_time = current_time
                                first_fire_sent = True

                            except Exception as e:
                                logger.error(f"Failed to queue data: {e}")
                        else:
                            logger.error("Failed to encode frame to JPEG!")

        frame_count += 1
        if frame_count >= 5:
            end_time = time()
            fps = frame_count / (end_time - start_time)
            frame_count = 0
            start_time = end_time

        fps_text = f"FPS: {fps:.1f}"
        cv2.putText(frame, fps_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (0, 0, 255), 1, cv2.LINE_AA)

        detection_status = f"Detect: {'ON' if detection_enabled else 'OFF'}"
        cv2.putText(frame, detection_status, (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 255, 0) if detection_enabled else (0, 0, 255),
                    1, cv2.LINE_AA)

        send_status = f"Send: {'ON' if sending_allowed.is_set() else 'OFF'}"
        cv2.putText(frame, send_status, (10, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 255, 0) if sending_allowed.is_set() else (0, 0, 255),
                    1, cv2.LINE_AA)

        if fire_detected:
            fire_status = "Fire/Smoke: Detected"
            cv2.putText(frame, fire_status, (10, 120),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 0, 255), 1, cv2.LINE_AA)

        with temp_humidity_lock:
            temp = temp_humidity_data.get("temperature")
            humidity = temp_humidity_data.get("humidity")

        if temp is not None and humidity is not None:
            temp_text = f"Temp: {temp:.1f}C  Humid: {humidity:.1f}%"
            cv2.putText(frame, temp_text, (10, 150),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (255, 255, 0), 1, cv2.LINE_AA)

        cv2.imshow("RDK Fire Detection", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

    exit_event.set()
    sending_allowed.clear()

    command_thread.join(timeout=2.0)
    data_thread.join(timeout=2.0)
    temp_humidity_thread.join(timeout=2.0)
    periodic_sender_thread.join(timeout=2.0)
    logger.info("All threads joined")


class YOLO11_Detect():
    def __init__(self, opt):
        try:
            begin_time = time()
            self.quantize_model = dnn.load(opt.model_path)
            logger.debug("\033[1;31m" + "Load D-Robotics Quantize model time = %.2f ms" % (
                    1000 * (time() - begin_time)) + "\033[0m")
        except Exception as e:
            logger.error("? Failed to load model file: %s" % (opt.model_path))
            logger.error(e)
            exit(1)

        self.s_bboxes_scale = self.quantize_model[0].outputs[1].properties.scale_data[np.newaxis, :]
        self.m_bboxes_scale = self.quantize_model[0].outputs[3].properties.scale_data[np.newaxis, :]
        self.l_bboxes_scale = self.quantize_model[0].outputs[5].properties.scale_data[np.newaxis, :]
        self.weights_static = np.array([i for i in range(16)]).astype(np.float32)[np.newaxis, np.newaxis, :]

        self.s_anchor = np.stack([np.tile(np.linspace(0.5, 79.5, 80), reps=80),
                                  np.repeat(np.arange(0.5, 80.5, 1), 80)], axis=0).transpose(1, 0)
        self.m_anchor = np.stack([np.tile(np.linspace(0.5, 39.5, 40), reps=40),
                                  np.repeat(np.arange(0.5, 40.5, 1), 40)], axis=0).transpose(1, 0)
        self.l_anchor = np.stack([np.tile(np.linspace(0.5, 19.5, 20), reps=20),
                                  np.repeat(np.arange(0.5, 20.5, 1), 20)], axis=0).transpose(1, 0)

        self.input_image_size = 640
        self.SCORE_THRESHOLD = opt.score_thres
        self.NMS_THRESHOLD = opt.nms_thres
        self.CONF_THRES_RAW = -np.log(1 / self.SCORE_THRESHOLD - 1)
        self.input_H, self.input_W = self.quantize_model[0].inputs[0].properties.shape[2:4]
        self.REG = opt.reg
        self.CLASSES_NUM = opt.classes_num

    def preprocess_yuv420sp(self, img):
        RESIZE_TYPE = 0
        LETTERBOX_TYPE = 1
        PREPROCESS_TYPE = LETTERBOX_TYPE

        begin_time = time()
        self.img_h, self.img_w = img.shape[0:2]
        if PREPROCESS_TYPE == RESIZE_TYPE:
            input_tensor = cv2.resize(img, (self.input_W, self.input_H), interpolation=cv2.INTER_NEAREST)
            input_tensor = self.bgr2nv12(input_tensor)
            self.y_scale = 1.0 * self.input_H / self.img_h
            self.x_scale = 1.0 * self.input_W / self.img_w
            self.y_shift = 0
            self.x_shift = 0
        elif PREPROCESS_TYPE == LETTERBOX_TYPE:
            self.x_scale = min(1.0 * self.input_H / self.img_h, 1.0 * self.input_W / self.img_w)
            self.y_scale = self.x_scale

            if self.x_scale <= 0 or self.y_scale <= 0:
                raise ValueError("Invalid scale factor.")

            new_w = int(self.img_w * self.x_scale)
            self.x_shift = (self.input_W - new_w) // 2
            x_other = self.input_W - new_w - self.x_shift

            new_h = int(self.img_h * self.y_scale)
            self.y_shift = (self.input_H - new_h) // 2
            y_other = self.input_H - new_h - self.y_shift

            input_tensor = cv2.resize(img, (new_w, new_h))
            input_tensor = cv2.copyMakeBorder(input_tensor, self.y_shift, y_other, self.x_shift, x_other,
                                              cv2.BORDER_CONSTANT, value=[127, 127, 127])
            input_tensor = self.bgr2nv12(input_tensor)
        else:
            logger.error(f"illegal PREPROCESS_TYPE = {PREPROCESS_TYPE}")
            exit(-1)

        return input_tensor

    def bgr2nv12(self, bgr_img):
        height, width = bgr_img.shape[0], bgr_img.shape[1]
        area = height * width
        yuv420p = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2YUV_I420).reshape((area * 3 // 2,))
        y = yuv420p[:area]
        uv_planar = yuv420p[area:].reshape((2, area // 4))
        uv_packed = uv_planar.transpose((1, 0)).reshape((area // 2,))
        nv12 = np.zeros_like(yuv420p)
        nv12[:height * width] = y
        nv12[height * width:] = uv_packed
        return nv12

    def forward(self, input_tensor):
        quantize_outputs = self.quantize_model[0].forward(input_tensor)
        return quantize_outputs

    def c2numpy(self, outputs):
        outputs = [dnnTensor.buffer for dnnTensor in outputs]
        return outputs

    def postProcess(self, outputs):
        begin_time = time()
        s_clses = outputs[0].reshape(-1, self.CLASSES_NUM)
        s_bboxes = outputs[1].reshape(-1, self.REG * 4)
        m_clses = outputs[2].reshape(-1, self.CLASSES_NUM)
        m_bboxes = outputs[3].reshape(-1, self.REG * 4)
        l_clses = outputs[4].reshape(-1, self.CLASSES_NUM)
        l_bboxes = outputs[5].reshape(-1, self.REG * 4)

        s_max_scores = np.max(s_clses, axis=1)
        s_valid_indices = np.flatnonzero(s_max_scores >= self.CONF_THRES_RAW)
        s_ids = np.argmax(s_clses[s_valid_indices, :], axis=1)
        s_scores = s_max_scores[s_valid_indices]

        m_max_scores = np.max(m_clses, axis=1)
        m_valid_indices = np.flatnonzero(m_max_scores >= self.CONF_THRES_RAW)
        m_ids = np.argmax(m_clses[m_valid_indices, :], axis=1)
        m_scores = m_max_scores[m_valid_indices]

        l_max_scores = np.max(l_clses, axis=1)
        l_valid_indices = np.flatnonzero(l_max_scores >= self.CONF_THRES_RAW)
        l_ids = np.argmax(l_clses[l_valid_indices, :], axis=1)
        l_scores = l_max_scores[l_valid_indices]

        s_scores = 1 / (1 + np.exp(-s_scores))
        m_scores = 1 / (1 + np.exp(-m_scores))
        l_scores = 1 / (1 + np.exp(-l_scores))

        s_bboxes_float32 = s_bboxes[s_valid_indices, :].astype(np.float32) * self.s_bboxes_scale
        m_bboxes_float32 = m_bboxes[m_valid_indices, :].astype(np.float32) * self.m_bboxes_scale
        l_bboxes_float32 = l_bboxes[l_valid_indices, :].astype(np.float32) * self.l_bboxes_scale

        s_ltrb_indices = np.sum(softmax(s_bboxes_float32.reshape(-1, 4, 16), axis=2) * self.weights_static, axis=2)
        s_anchor_indices = self.s_anchor[s_valid_indices, :]
        s_x1y1 = s_anchor_indices - s_ltrb_indices[:, 0:2]
        s_x2y2 = s_anchor_indices + s_ltrb_indices[:, 2:4]
        s_dbboxes = np.hstack([s_x1y1, s_x2y2]) * 8

        m_ltrb_indices = np.sum(softmax(m_bboxes_float32.reshape(-1, 4, 16), axis=2) * self.weights_static, axis=2)
        m_anchor_indices = self.m_anchor[m_valid_indices, :]
        m_x1y1 = m_anchor_indices - m_ltrb_indices[:, 0:2]
        m_x2y2 = m_anchor_indices + m_ltrb_indices[:, 2:4]
        m_dbboxes = np.hstack([m_x1y1, m_x2y2]) * 16

        l_ltrb_indices = np.sum(softmax(l_bboxes_float32.reshape(-1, 4, 16), axis=2) * self.weights_static, axis=2)
        l_anchor_indices = self.l_anchor[l_valid_indices, :]
        l_x1y1 = l_anchor_indices - l_ltrb_indices[:, 0:2]
        l_x2y2 = l_anchor_indices + l_ltrb_indices[:, 2:4]
        l_dbboxes = np.hstack([l_x1y1, l_x2y2]) * 32

        dbboxes = np.concatenate((s_dbboxes, m_dbboxes, l_dbboxes), axis=0)
        scores = np.concatenate((s_scores, m_scores, l_scores), axis=0)
        ids = np.concatenate((s_ids, m_ids, l_ids), axis=0)

        hw = (dbboxes[:, 2:4] - dbboxes[:, 0:2])
        xyhw2 = np.hstack([dbboxes[:, 0:2], hw])

        results = []
        for i in range(self.CLASSES_NUM):
            id_indices = ids == i
            if not np.any(id_indices):
                continue
            indices = cv2.dnn.NMSBoxes(xyhw2[id_indices, :], scores[id_indices], self.SCORE_THRESHOLD,
                                       self.NMS_THRESHOLD)
            if len(indices) == 0:
                continue
            for indic in indices:
                x1, y1, x2, y2 = dbboxes[id_indices, :][indic]
                x1 = int((x1 - self.x_shift) / self.x_scale)
                y1 = int((y1 - self.y_shift) / self.y_scale)
                x2 = int((x2 - self.x_shift) / self.x_scale)
                y2 = int((y2 - self.y_shift) / self.y_scale)

                x1 = max(0, min(x1, self.img_w))
                x2 = max(0, min(x2, self.img_w))
                y1 = max(0, min(y1, self.img_h))
                y2 = max(0, min(y2, self.img_h))

                results.append((i, scores[id_indices][indic], x1, y1, x2, y2))

        return results


coco_names = [
    "fire", "smoke", "tower"
]

rdk_colors = [
    (56, 56, 255), (151, 157, 255), (31, 112, 255), (29, 178, 255), (49, 210, 207), (10, 249, 72), (23, 204, 146),
    (134, 219, 61),
    (52, 147, 26), (187, 212, 0), (168, 153, 44), (255, 194, 0), (147, 69, 52), (255, 115, 100), (236, 24, 0),
    (255, 56, 132),
    (133, 0, 82), (255, 56, 203), (200, 149, 255), (199, 55, 255)]


def draw_detection(img, bbox, score, class_id) -> None:
    x1, y1, x2, y2 = bbox
    color = rdk_colors[class_id % 20]
    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
    label = f"{coco_names[class_id]}: {score:.2f}"
    (label_width, label_height), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    label_x, label_y = x1, y1 - 10 if y1 - 10 > label_height else y1 + 10
    cv2.rectangle(
        img, (label_x, label_y - label_height), (label_x + label_width, label_y + label_height), color, cv2.FILLED
    )
    cv2.putText(img, label, (label_x, label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)


if __name__ == "__main__":
    main()
