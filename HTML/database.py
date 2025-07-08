import socket
import threading
import os
import time
import re
import base64
import json
from json.decoder import JSONDecodeError
from mysql.connector import pooling
from flask import Flask, send_file, jsonify, Response, request
from flask_cors import CORS
from io import BytesIO
from PIL import Image
from datetime import datetime
import numpy as np

app = Flask(__name__)
CORS(app)

# ------------------ 公共配置 ------------------
# 图片数据库配置
db_config_image = {
    'host': 'localhost',
    'user': 'root',
    'password': 'tian200361',
    'database': 'string_dbA1'
}

# 检测记录数据库配置
db_config_detection = {
    'host': 'localhost',
    'user': 'root',
    'password': 'tian200361',
    'database': 'string_dbA1'
}

# 控制指令数据库配置
db_config_control = {
    'host': 'localhost',
    'user': 'root',
    'password': 'tian200361',
    'database': 'control_system'
}

# GPS定位数据库配置（使用与检测记录相同的数据库）
db_config_location = {
    'host': 'localhost',
    'user': 'root',
    'password': 'tian200361',
    'database': 'string_dbA1'
}

# 目标设备配置
DEVICE_IP = "192.168.58.113"
DEVICE_PORT = 8080

# 图片保存路径
SAVE_FOLDER = r"E:\Serve Photoes"
os.makedirs(SAVE_FOLDER, exist_ok=True)
print(f"图片将保存到: {SAVE_FOLDER}")

# 字符串本地保存路径
STRING_SAVE_FOLDER = r"E:\Serve String"
os.makedirs(STRING_SAVE_FOLDER, exist_ok=True)
print(f"字符串将保存到: {STRING_SAVE_FOLDER}")

# 初始化数据库连接池
db_pool_image = pooling.MySQLConnectionPool(
    pool_name="image_pool",
    pool_size=3,
    **db_config_image
)
db_pool_detection = pooling.MySQLConnectionPool(
    pool_name="detection_pool",
    pool_size=2,
    **db_config_detection
)
db_pool_control = pooling.MySQLConnectionPool(
    pool_name="control_pool",
    pool_size=2,
    **db_config_control
)
db_pool_location = pooling.MySQLConnectionPool(
    pool_name="location_pool",
    pool_size=2,
    **db_config_location
)


# ------------------ 添加GPS相关数据库操作 ------------------
def execute_sql_location(query, params=None, fetch=False, fetch_one=False, fetch_all=False):
    """GPS定位数据库操作函数"""
    conn = None
    try:
        conn = db_pool_location.get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, params)
        if fetch:
            if fetch_all:
                return cursor.fetchall()
            elif fetch_one:
                return cursor.fetchone()
            else:
                return cursor.fetchone()
        conn.commit()
        return True
    except Exception as e:
        print(f"GPS定位数据库操作失败: {str(e)}")
        if fetch:
            return None
        return False
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()


def get_next_location_id():
    """获取下一个GPS定位记录ID"""
    result = execute_sql_location(
        "SELECT MAX(id) as max_id FROM device_locations",
        fetch=True,
        fetch_one=True
    )
    if result is None or result.get('max_id') is None:
        return 1
    return result['max_id'] + 1


def save_gps_location(latitude, longitude, timestamp=None):
    """保存GPS定位信息到数据库"""
    next_id = get_next_location_id()
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 确保经纬度是Python原生类型
    if isinstance(latitude, np.generic):
        latitude = latitude.item()
    elif not isinstance(latitude, (int, float)):
        try:
            latitude = float(latitude)
        except:
            latitude = 0.0

    if isinstance(longitude, np.generic):
        longitude = longitude.item()
    elif not isinstance(longitude, (int, float)):
        try:
            longitude = float(longitude)
        except:
            longitude = 0.0

    return execute_sql_location(
        "INSERT INTO device_locations (id, latitude, longitude, timestamp) VALUES (%s, %s, %s, %s)",
        (next_id, latitude, longitude, timestamp)
    )


def get_latest_location():
    """获取最新GPS定位记录"""
    return execute_sql_location(
        "SELECT * FROM device_locations ORDER BY id DESC LIMIT 1",
        fetch=True,
        fetch_one=True
    )


def get_location_history(limit=100):
    """获取GPS定位历史记录"""
    return execute_sql_location(
        "SELECT * FROM device_locations ORDER BY id DESC LIMIT %s",
        (limit,),
        fetch=True,
        fetch_all=True
    )


# ------------------ 原有数据库操作函数保持不变 ------------------
def execute_sql_image(query, params=None, fetch=False, fetch_one=False):
    """图片数据库操作函数"""
    conn = None
    try:
        conn = db_pool_image.get_connection()
        cursor = conn.cursor()
        cursor.execute(query, params)
        if fetch:
            if fetch_one:
                result = cursor.fetchone()
                return result[0] if result else None
            return cursor.fetchall()
        conn.commit()
        return True
    except Exception as e:
        print(f"图片数据库操作失败: {str(e)}")
        return False
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()


def execute_sql_detection(query, params=None, fetch=False, fetch_one=False, fetch_all=False):
    """检测记录数据库操作函数"""
    conn = None
    try:
        conn = db_pool_detection.get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, params)
        if fetch:
            if fetch_all:
                return cursor.fetchall()
            elif fetch_one:
                return cursor.fetchone()
            else:
                return cursor.fetchone()
        conn.commit()
        return True
    except Exception as e:
        print(f"检测记录数据库操作失败: {str(e)}")
        if fetch:
            return None
        return False
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()


def execute_sql_control(query, params=None, fetch=False, fetch_one=False):
    """控制指令数据库操作函数"""
    conn = None
    try:
        conn = db_pool_control.get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, params)
        if fetch:
            if fetch_one:
                return cursor.fetchone()
            return cursor.fetchall()
        conn.commit()
        return True
    except Exception as e:
        print(f"控制指令数据库操作失败: {str(e)}")
        if fetch:
            return None
        return False
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()


def get_next_image_id():
    """获取下一个图片ID"""
    max_id = execute_sql_image(
        "SELECT MAX(id) FROM images",
        fetch=True,
        fetch_one=True
    )
    return 1 if max_id is None else max_id + 1


def get_next_detection_id():
    """获取下一个检测记录ID"""
    result = execute_sql_detection(
        "SELECT MAX(id) as max_id FROM detection_records",
        fetch=True,
        fetch_one=True
    )
    if result is None or result.get('max_id') is None:
        return 1
    return result['max_id'] + 1


def save_image(image_binary, timestamp=None):
    """保存二进制图片到数据库"""
    next_id = get_next_image_id()
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return execute_sql_image(
        "INSERT INTO images (id, image_data, timestamp) VALUES (%s, %s, %s)",
        (next_id, image_binary, timestamp)
    )


def get_latest_image():
    """获取最新二进制图片"""
    return execute_sql_image(
        "SELECT image_data FROM images ORDER BY id DESC LIMIT 1",
        fetch=True,
        fetch_one=True
    )


# 修改 save_detection_record 函数
def save_detection_record(class_name, confidence):
    """保存检测记录到数据库，返回是否为火灾/烟雾"""
    next_id = get_next_detection_id()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if isinstance(confidence, np.generic):
        confidence = confidence.item()
    elif not isinstance(confidence, (int, float)):
        try:
            confidence = float(confidence)
        except:
            confidence = 0.0

    # 检测是否为火灾或烟雾
    is_fire_or_smoke = class_name.lower() in ['fire', 'smoke']
    if is_fire_or_smoke:
        print(f"🔥 检测到火灾/烟雾: {class_name}, 置信度: {confidence:.2f}")

    success = execute_sql_detection(
        "INSERT INTO detection_records (id, timestamp, class, confidence, created_at) VALUES (%s, %s, %s, %s, NOW())",
        (next_id, timestamp, class_name, confidence)
    )

    return success, is_fire_or_smoke


def get_latest_detection():
    """获取最新检测记录"""
    return execute_sql_detection(
        "SELECT * FROM detection_records ORDER BY id DESC LIMIT 1",
        fetch=True,
        fetch_one=True
    )


def extract_json_fields(json_str):
    """从JSON字符串中提取需要的字段"""
    try:
        result = {}

        type_match = re.search(r'"type"\s*:\s*"([^"]+)"', json_str)
        if type_match:
            result['type'] = type_match.group(1)

        time_match = re.search(r'"timestamp"\s*:\s*"([^"]+)"', json_str)
        if time_match:
            result['time'] = time_match.group(1)

        detections_match = re.search(r'"detections"\s*:\s*\[', json_str)
        if detections_match:
            start_pos = detections_match.end() - 1

            bracket_count = 0
            end_pos = -1
            for i in range(start_pos, len(json_str)):
                if json_str[i] == '[':
                    bracket_count += 1
                elif json_str[i] == ']':
                    bracket_count -= 1
                    if bracket_count == 0:
                        end_pos = i + 1
                        break

            if end_pos != -1:
                detections_str = json_str[start_pos:end_pos]
                try:
                    result['detections'] = json.loads(detections_str)
                except:
                    print("解析detections数组失败，尝试修复")
                    result['detections'] = fix_detections_array(detections_str)

        return result

    except Exception as e:
        print(f"正则提取失败: {str(e)}")

        try:
            images_pos = json_str.find('"images"')
            if images_pos > 0:
                truncated = json_str[:images_pos]
                truncated = truncated.rstrip().rstrip(',')
                truncated += '}'
                return json.loads(truncated)
        except:
            pass

    return None


def fix_detections_array(detections_str):
    """修复可能不完整的detections数组"""
    try:
        return json.loads(detections_str)
    except:
        if not detections_str.strip().endswith(']'):
            last_brace = detections_str.rfind('}')
            if last_brace != -1:
                detections_str = detections_str[:last_brace + 1] + ']'

        try:
            return json.loads(detections_str)
        except:
            return []


# ------------------ Socket服务逻辑（修改以支持GPS数据） ------------------
def handle_client(client_socket):
    """处理设备客户端请求（支持GPS/图片/字符串多类型）"""
    try:
        client_socket.settimeout(10.0)

        all_data = b''
        fire_smoke_detected = False

        while True:
            try:
                chunk = client_socket.recv(4096)
                if not chunk:
                    break
                all_data += chunk
            except socket.timeout:
                break

        if not all_data:
            client_socket.send(b"Error: No data received")
            return

        try:
            received_str = all_data.decode('utf-8', errors='ignore').strip()
            print(f"接收到数据长度: {len(received_str)} 字符")
            print(f"接收到的原始数据: {received_str}")  # 添加调试信息

            # 尝试清理数据中的非JSON字符
            # 查找JSON的开始位置（以 { 开始）
            json_start = received_str.find('{')
            if json_start != -1:
                # 提取从第一个 { 开始的数据
                cleaned_str = received_str[json_start:]
                print(f"清理后的数据: {cleaned_str}")

                # 尝试解析清理后的JSON
                try:
                    json_data = json.loads(cleaned_str)

                    # 处理GPS定位数据
                    if json_data.get('type') == 'GPS_LOCATION':
                        print(f"接收到GPS定位数据: lat={json_data.get('latitude')}, lon={json_data.get('longitude')}")

                        latitude = json_data.get('latitude')
                        longitude = json_data.get('longitude')
                        timestamp = json_data.get('timestamp')

                        if latitude is not None and longitude is not None:
                            if save_gps_location(latitude, longitude, timestamp):
                                print(f"GPS定位保存成功: {latitude}, {longitude}")
                                client_socket.send(b"Success: GPS location saved")
                            else:
                                print("GPS定位保存失败")
                                client_socket.send(b"Error: Failed to save GPS location")
                        else:
                            print("GPS数据缺少经纬度信息")
                            client_socket.send(b"Error: Missing latitude or longitude")
                        return

                    # 如果不是GPS数据，但是有效的JSON，继续处理其他类型
                    elif json_data.get('type') and json_data.get('detections'):
                        # 处理检测数据
                        print(f"成功解析检测数据: type={json_data.get('type')}")

                        detections = json_data.get('detections', [])
                        for detection in detections:
                            class_name = detection.get('class', 'Unknown')
                            confidence = detection.get('confidence', 0.0)

                            if save_detection_record(class_name, confidence):
                                print(f"检测记录保存成功: {class_name}, {confidence}")
                            else:
                                print(f"检测记录保存失败: {class_name}, {confidence}")

                        # 如果有图片数据，保存图片
                        if json_data.get('image'):
                            time_str = json_data.get('timestamp')
                            extract_and_save_image(received_str, time_str)

                        client_socket.send(b"Success: Data processed")
                        return

                except json.JSONDecodeError as e:
                    print(f"JSON解析失败: {str(e)}")
                    # 继续尝试其他处理方式
                    pass

            # 原有的检测数据处理逻辑（处理不完整的JSON）
            if '"type"' in received_str and '"detections"' in received_str:
                extracted_data = extract_json_fields(received_str)

                if extracted_data:
                    print(f"成功提取数据: type={extracted_data.get('type')}, time={extracted_data.get('time')}")

                    detections = extracted_data.get('detections', [])
                    for detection in detections:
                        class_name = detection.get('class', 'Unknown')
                        confidence = detection.get('confidence', 0.0)

                        success, is_fire_smoke = save_detection_record(class_name, confidence)
                        if success:
                            print(f"检测记录保存成功: {class_name}, {confidence}")
                            if is_fire_smoke:
                                fire_smoke_detected = True
                        else:
                            print(f"检测记录保存失败: {class_name}, {confidence}")

                    time_str = extracted_data.get('time')
                    extract_and_save_image(received_str, time_str)
                    save_extracted_data(extracted_data)

                    client_socket.send(b"Success: Data processed")
                else:
                    print("无法提取所需数据")
                    client_socket.send(b"Error: Failed to extract data")
            else:
                print("接收到非标准JSON数据，数据内容:", received_str[:100])  # 只打印前100个字符
                client_socket.send(b"Error: Invalid data format")

        except UnicodeDecodeError:
            print("接收到二进制数据")
            handle_binary_image(all_data)
            client_socket.send(b"Success: Binary data received")

    except Exception as e:
        print(f"处理客户端时发生错误: {str(e)}")
        try:
            client_socket.send(f"Error: {str(e)}".encode())
        except:
            pass
    finally:
        client_socket.close()


def extract_and_save_image(json_str, time_str=None):
    """从JSON字符串中提取images字段并保存"""
    try:
        images_match = re.search(r'"image"\s*:\s*"([^"]+)"', json_str)
        if images_match:
            image_base64 = images_match.group(1)

            image_binary = base64.b64decode(image_base64)
            print(f"提取到图片，大小: {len(image_binary)} 字节")

            if time_str:
                filename = time_str.replace(" ", "_").replace(":", "-") + ".jpg"
            else:
                filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".jpg"

            save_path = os.path.join(SAVE_FOLDER, filename)

            try:
                image = Image.open(BytesIO(image_binary))
                if image.mode in ('RGBA', 'LA', 'P'):
                    rgb_image = Image.new('RGB', image.size, (255, 255, 255))
                    if image.mode == 'RGBA':
                        rgb_image.paste(image, mask=image.split()[3])
                    else:
                        rgb_image.paste(image)
                    image = rgb_image

                image.save(save_path, 'JPEG', quality=95)
                print(f"图片已保存到: {save_path}")

                if save_image(image_binary, time_str):
                    print("图片成功保存到数据库")
                else:
                    print("图片保存到数据库失败")

            except Exception as e:
                print(f"图片格式转换失败，尝试直接保存: {str(e)}")
                with open(save_path, 'wb') as f:
                    f.write(image_binary)
                print(f"图片已直接保存到: {save_path}")

    except Exception as e:
        print(f"提取图片失败: {str(e)}")


def save_extracted_data(data):
    """保存提取的数据到本地文件"""
    try:
        timestamp = int(time.time() * 1000)
        filename = f"extracted_data_{timestamp}.json"
        save_path = os.path.join(STRING_SAVE_FOLDER, filename)

        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"提取的数据已保存到: {save_path}")
    except Exception as e:
        print(f"保存提取数据失败: {str(e)}")


def handle_binary_image(image_data):
    """处理纯二进制图片数据"""
    try:
        timestamp = int(time.time() * 1000)
        filename = f"binary_image_{timestamp}.png"
        save_path = os.path.join(SAVE_FOLDER, filename)
        with open(save_path, 'wb') as f:
            f.write(image_data)
        print(f"二进制图片已保存到: {save_path}")

        if save_image(image_data):
            print("二进制图片成功保存到数据库")
    except Exception as e:
        print(f"处理二进制图片失败: {str(e)}")


def run_socket_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', 8888))
    server.listen(5)
    print("Socket Server running on port 8888...")
    while True:
        client, addr = server.accept()
        print(f"Accepted connection from {addr}")
        thread = threading.Thread(target=handle_client, args=(client,))
        thread.start()


# ------------------ Flask服务逻辑（添加GPS相关接口） ------------------
@app.route('/get_latest_location', methods=['GET'])
def flask_get_latest_location():
    """获取最新GPS定位"""
    try:
        location = get_latest_location()
        if not location:
            return jsonify({"status": "error", "message": "No location record found"}), 404

        # 确保所有字段存在并提供默认值
        location_data = {
            "id": int(location.get("id", 0)),
            "latitude": float(location.get("latitude", 0.0)),
            "longitude": float(location.get("longitude", 0.0)),
            "timestamp": str(location.get("timestamp", "")),
            "created_at": str(location.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        }

        return jsonify({
            "status": "success",
            "location": location_data
        })

    except Exception as e:
        print(f"获取位置时出错: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"内部服务器错误: {str(e)}"
        }), 500


@app.route('/get_location_history', methods=['GET'])
def flask_get_location_history():
    """获取GPS定位历史"""
    limit = request.args.get('limit', 100, type=int)
    locations = get_location_history(limit)

    if not locations:
        return jsonify({
            "status": "success",
            "locations": []
        })

    location_list = []
    for loc in locations:
        location_list.append({
            "id": int(loc["id"]),
            "latitude": float(loc["latitude"]),
            "longitude": float(loc["longitude"]),
            "timestamp": str(loc["timestamp"]),
            "created_at": str(loc["created_at"])
        })

    return jsonify({
        "status": "success",
        "count": len(location_list),
        "locations": location_list
    })


@app.route('/get_location_by_time', methods=['GET'])
def get_location_by_time():
    """根据时间范围获取GPS定位"""
    start_time = request.args.get('start_time')
    end_time = request.args.get('end_time')

    if not start_time or not end_time:
        return jsonify({
            "status": "error",
            "message": "Missing start_time or end_time parameter"
        }), 400

    locations = execute_sql_location(
        """SELECT * FROM device_locations 
           WHERE timestamp BETWEEN %s AND %s 
           ORDER BY timestamp DESC""",
        (start_time, end_time),
        fetch=True,
        fetch_all=True
    )

    if not locations:
        return jsonify({
            "status": "success",
            "locations": []
        })

    location_list = []
    for loc in locations:
        location_list.append({
            "id": int(loc["id"]),
            "latitude": float(loc["latitude"]),
            "longitude": float(loc["longitude"]),
            "timestamp": str(loc["timestamp"]),
            "created_at": str(loc["created_at"])
        })

    return jsonify({
        "status": "success",
        "count": len(location_list),
        "locations": location_list
    })


@app.route('/get_image_list', methods=['GET'])
def get_image_list():
    """获取所有图片的时间戳列表"""
    conn = None
    cursor = None
    try:
        conn = db_pool_image.get_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT timestamp FROM images GROUP BY timestamp ORDER BY MAX(id) DESC")
        rows = cursor.fetchall()

        seen = set()
        timestamps = []
        for row in rows:
            ts = row['timestamp']
            if ts not in seen:
                seen.add(ts)
                timestamps.append(ts)

        return jsonify({
            "status": "success",
            "images": [{"timestamp": ts} for ts in timestamps]
        })

    except Exception as e:
        print(f"获取图片列表错误: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"获取图片列表失败: {str(e)}"
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


def send_command_to_device(command_char):
    """向嵌入式设备发送指令"""
    try:
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.settimeout(5.0)

        client_socket.connect((DEVICE_IP, DEVICE_PORT))
        print(f"已连接到设备 {DEVICE_IP}:{DEVICE_PORT}")

        client_socket.send(command_char.encode('utf-8'))
        print(f"已发送指令: {command_char}")

        response = client_socket.recv(1024)
        print(f"设备响应: {response.decode()}")

        return True, "指令发送成功"
    except Exception as e:
        return False, f"指令发送失败: {str(e)}"
    finally:
        client_socket.close()


@app.route('/get_image_by_timestamp', methods=['GET'])
def get_image_by_timestamp():
    """根据时间戳获取图片"""
    from datetime import datetime
    timestamp = request.args.get('timestamp')
    if not timestamp:
        return jsonify({"status": "error", "message": "缺少时间戳参数"}), 400

    try:
        formats = ['%a, %d %b %Y %H:%M:%S %Z', '%Y-%m-%d %H:%M:%S']
        db_timestamp = None
        for fmt in formats:
            try:
                dt = datetime.strptime(timestamp, fmt)
                db_timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
                break
            except ValueError:
                continue

        if db_timestamp is None:
            db_timestamp = timestamp

        conn = db_pool_image.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT image_data FROM images WHERE timestamp = %s ORDER BY id DESC LIMIT 1",
            (db_timestamp,)
        )
        result = cursor.fetchone()

        if not result:
            return jsonify({
                "status": "error",
                "message": f"未找到时间戳为 {timestamp} 的图片"
            }), 404

        image_binary = result[0]
        return Response(
            image_binary,
            mimetype='image/jpeg',
            headers={'Content-Length': str(len(image_binary))}
        )

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"获取图片失败: {str(e)}"
        }), 500
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()


@app.route('/get_latest_image', methods=['GET'])
def flask_get_latest_image():
    """获取最新图片"""
    image_binary = get_latest_image()
    if not image_binary:
        return jsonify({"status": "error", "message": "No image found"}), 404

    return Response(
        image_binary,
        mimetype='image/jpeg',
        headers={'Content-Length': str(len(image_binary))}
    )


@app.route('/get_latest_detection', methods=['GET'])
def flask_get_latest_detection():
    """获取最新检测记录"""
    detection = get_latest_detection()
    if not detection:
        return jsonify({"status": "error", "message": "No detection record found"}), 404

    required_keys = ["id", "timestamp", "class", "confidence", "created_at"]
    for key in required_keys:
        if key not in detection:
            return jsonify({"status": "error", "message": f"Missing key '{key}' in detection record"}), 500

    detection_data = {
        "id": int(detection["id"]),
        "timestamp": str(detection["timestamp"]),
        "class": str(detection["class"]),
        "confidence": float(detection["confidence"]),
        "created_at": str(detection["created_at"])
    }

    return jsonify({
        "status": "success",
        "detection": detection_data
    })


@app.route('/get_image/<int:image_id>', methods=['GET'])
def get_image_by_id(image_id):
    """根据ID获取图片"""
    try:
        conn = db_pool_image.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT image_data FROM images WHERE id = %s", (image_id,))
        result = cursor.fetchone()

        if not result:
            return jsonify({
                "status": "error",
                "message": f"Image with ID {image_id} not found"
            }), 404

        image_binary = result[0]

        return Response(
            image_binary,
            mimetype='image/jpeg',
            headers={'Content-Length': str(len(image_binary))}
        )

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error fetching image: {str(e)}"
        }), 500
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()


@app.route('/send_command/<command_key>', methods=['POST'])
def send_command(command_key):
    """发送控制指令到设备"""
    valid_commands = ['A', 'B', 'O', 'S', 'K']
    if command_key not in valid_commands:
        return jsonify({
            "status": "error",
            "message": f"Invalid command key '{command_key}'. Valid keys are: {', '.join(valid_commands)}"
        }), 400

    try:
        command_mapping = {
            'A': 'A',  # 开始检测
            'B': 'B',  # 停止检测
            'O': 'O',  # 停止发送
            'S': 'S',  # 继续发送
            'K': 'K'  # 截取图片
        }
        command_char = command_mapping[command_key]

        # 发送指令到设备
        success, message = send_command_to_device(command_char)

        if success:
            # 记录指令发送历史
            execute_sql_control(
                "INSERT INTO command_history (command_key, command_char, sent_at) VALUES (%s, %s, NOW())",
                (command_key, command_char)
            )

            # 如果是开始检测指令A，返回特殊标记表示将接收GPS数据
            if command_key == 'A':
                return jsonify({
                    "status": "success",
                    "message": message,
                    "command": command_char,
                    "expecting_gps": True  # 添加标记表示预期接收GPS数据
                })
            # 如果是截图指令，返回特殊标记
            elif command_key == 'K':
                return jsonify({
                    "status": "success",
                    "message": message,
                    "command": command_char,
                    "is_capture": True
                })
            else:
                return jsonify({
                    "status": "success",
                    "message": message,
                    "command": command_char
                })
        else:
            return jsonify({
                "status": "error",
                "message": message
            }), 500

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Failed to send command: {str(e)}"
        }), 500


def check_recent_fire_smoke_detection(seconds=60):
    """检查最近指定秒数内是否有火灾/烟雾检测记录"""
    result = execute_sql_detection(
        """SELECT COUNT(*) as count FROM detection_records 
           WHERE LOWER(class) IN ('fire', 'smoke') 
           AND timestamp >= NOW() - INTERVAL %s SECOND""",
        (seconds,),
        fetch=True,
        fetch_one=True
    )
    return result and result.get('count', 0) > 0


# 修改 /check_fire_alert 接口
@app.route('/check_fire_alert', methods=['GET'])
def check_fire_alert():
    """检查火灾警报状态"""
    # 检查最近60秒内是否有火灾/烟雾检测记录
    has_fire_alert = check_recent_fire_smoke_detection(60)

    if has_fire_alert:
        print("🚨 火灾警报状态检查: 检测到火灾警报!")
        response = {
            "status": "success",
            "alert": "Y"
        }
    else:
        print("✅ 火灾警报状态检查: 当前无火灾警报")
        response = {
            "status": "success",
            "alert": "N"
        }

    return jsonify(response)


if __name__ == '__main__':
    # 启动Socket服务器线程
    socket_thread = threading.Thread(target=run_socket_server)
    socket_thread.daemon = True
    socket_thread.start()

    # 启动Flask应用
    app.run(debug=True, host='0.0.0.0', port=5000)
