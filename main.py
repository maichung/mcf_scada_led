# app.py - Truyền dữ liệu SCADA → Màn LED qua TCP (custom HEX packet)
import json
import threading
import time
import schedule
from flask import Flask, render_template_string, request, redirect, url_for
import pyodbc
import socket
import struct

app = Flask(__name__)

# === CẤU HÌNH KẾT NỐI SQL SERVER QUA FreeTDS ===
ODBC_DSN = 'SQLServer'  # Tên DSN trong /etc/odbc.ini
SQL_USER = 'sa'
SQL_PASS = 'Binhminh@123'

# Connection string cho pyodbc
SQL_CONN_STR = f'DSN={ODBC_DSN};UID={SQL_USER};PWD={SQL_PASS}'

# === FILE CẤU HÌNH ===
CONFIG_FILE = 'config.json'

# === TẢI & LƯU CẤU HÌNH ===
def load_config():
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

# === THỰC THI QUERY SQL (TRẢ VỀ STRING HOẶC '---') ===
def execute_query(query_sql):
    try:
        conn = pyodbc.connect(SQL_CONN_STR)
        cursor = conn.cursor()
        cursor.execute(query_sql)
        row = cursor.fetchone()
        conn.close()
        if row and row[0] is not None:
            return str(row[0]).strip()
        return '---'  # Default nếu không có dữ liệu
    except Exception as e:
        print(f"[SQL ERROR] {e}")
        return '---'

# === GỬI DỮ LIỆU QUA TCP (CUSTOM HEX PACKET) ===
def send_to_led(ip, value_str, unit_id=1, start_reg=0, max_chars=20):
    """
    Gửi packet HEX: header (12 bytes) + length (1 byte) + payload (UTF-16BE).
    - value_str pad spaces đến max_chars.
    - Ignore unit_id/start_reg (cho compat với config cũ).
    """
    if value_str == '---':
        value_str = ' ' * max_chars  # Gửi spaces nếu lỗi
    value_str = value_str[:max_chars].ljust(max_chars, ' ')
    
    # Build payload: UTF-16BE (big-endian)
    payload = b''.join(struct.pack('>H', ord(c)) for c in value_str)
    length = len(payload)
    if length > 255:
        print(f"[ERROR] Độ dài payload quá lớn: {length}")
        return False
    
    header = b'\x00\x01\x00\x00\x00\x1B\x01\x10\x00\x00\x00\x0A'
    length_byte = struct.pack('B', length)
    packet = header + length_byte + payload
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect((ip, 502))
        sock.sendall(packet)
        sock.close()
        print(f"→ Gửi '{value_str.strip()}' đến {ip} (HEX: {packet.hex().upper()})")
        return True
    except Exception as e:
        print(f"[SOCKET ERROR] {ip}: {e}")
        return False

# === GIÁM SÁT MỖI MÀN HÌNH (BACKGROUND) ===
def monitor_screen(screen):
    name = screen['name']
    ip = screen['ip']
    query = screen['query']
    interval = screen['interval']
    unit_id = screen.get('unit_id', 1)
    start_reg = screen.get('start_reg', 0)
    max_chars = screen.get('max_chars', 20)
    last_value = screen.get('last_value')

    def job():
        nonlocal last_value
        current = execute_query(query)
        if current != last_value:
            success = send_to_led(ip, current, unit_id, start_reg, max_chars)
            if success:
                last_value = current
                # Cập nhật config
                config = load_config()
                for s in config:
                    if s['name'] == name:
                        s['last_value'] = last_value
                save_config(config)

    # Lên lịch
    schedule.every(interval).seconds.do(job)

# === KHỞI ĐỘNG TẤT CẢ THREAD GIÁM SÁT ===
def start_monitoring():
    config = load_config()
    for screen in config:
        t = threading.Thread(target=run_scheduler, args=(screen,), daemon=True)
        t.start()

def run_scheduler(screen):
    monitor_screen(screen)
    while True:
        schedule.run_pending()
        time.sleep(1)

# === GIAO DIỆN WEB ===
HTML_INDEX = '''
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>SCADA → LED</title>
<style>body{font-family:Arial;margin:20px;} li{margin:10px 0;}</style>
</head><body>
<h1>Truyền Dữ Liệu SCADA → Màn LED (Modbus TCP)</h1>
<h2>Danh sách màn hình (Tối đa 3)</h2>
<ul>
{% for s in screens %}
    <li><b>{{ s.name }}</b> | IP: {{ s.ip }} | Interval: {{ s.interval }}s | 
    <i>Current: "{{ s.last_value or '---' }}"</i><br>
    <small>Query: {{ s.query }}</small><br>
    <a href="/edit/{{ loop.index0 }}">Sửa</a> | <a href="/delete/{{ loop.index0 }}" onclick="return confirm('Xóa?')">Xóa</a>
    </li><hr>
{% endfor %}
</ul>

<h2>Thêm màn hình mới</h2>
<form method="POST" action="/add">
    Tên: <input name="name" required><br><br>
    IP: <input name="ip" required placeholder="192.168.1.50"><br><br>
    Query SQL: <textarea name="query" required rows="2" cols="50" placeholder="SELECT TOP 1 masp FROM view_scada"></textarea><br><br>
    Thời gian (giây): <input name="interval" type="number" value="30" min="5" required><br><br>
    Slave ID: <input name="unit_id" type="number" value="1" min="1" max="247"><br><br>
    Register bắt đầu: <input name="start_reg" type="number" value="0"><br><br>
    Số ký tự tối đa: <input name="max_chars" type="number" value="20" min="2" max="100"><br><br>
    <button type="submit">Thêm</button>
</form>
<p><b>Ví dụ kết quả query:</b> A11S-12, U70-19, T414-1-12</p>
</body></html>
'''

HTML_EDIT = '''
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Sửa {{ s.name }}</title></head><body>
<h1>Sửa màn hình: {{ s.name }}</h1>
<form method="POST" action="/update/{{ idx }}">
    IP: <input name="ip" value="{{ s.ip }}" required><br><br>
    Query SQL: <textarea name="query" required rows="3" cols="60">{{ s.query }}</textarea><br><br>
    Interval (giây): <input name="interval" type="number" value="{{ s.interval }}" min="5" required><br><br>
    Slave ID: <input name="unit_id" type="number" value="{{ s.unit_id }}" min="1" max="247"><br><br>
    Register bắt đầu: <input name="start_reg" type="number" value="{{ s.start_reg }}"><br><br>
    Số ký tự tối đa: <input name="max_chars" type="number" value="{{ s.max_chars }}" min="2" max="100"><br><br>
    <button type="submit">Cập nhật</button> <a href="/">Hủy</a>
</form>
</body></html>
'''

@app.route('/')
def index():
    screens = load_config()
    return render_template_string(HTML_INDEX, screens=screens)

@app.route('/add', methods=['POST'])
def add():
    config = load_config()
    if len(config) >= 3:
        return "Tối đa 3 màn hình!", 400

    new_screen = {
        'name': request.form['name'],
        'ip': request.form['ip'],
        'query': request.form['query'].strip(),
        'interval': int(request.form['interval']),
        'unit_id': int(request.form.get('unit_id', 1)),
        'start_reg': int(request.form.get('start_reg', 0)),
        'max_chars': int(request.form.get('max_chars', 20)),
        'last_value': None
    }
    config.append(new_screen)
    save_config(config)
    return redirect('/')

@app.route('/edit/<int:idx>')
def edit(idx):
    config = load_config()
    if idx >= len(config):
        return "Không tìm thấy", 404
    return render_template_string(HTML_EDIT, s=config[idx], idx=idx)

@app.route('/update/<int:idx>', methods=['POST'])
def update(idx):
    config = load_config()
    if idx >= len(config):
        return "Lỗi", 400
    s = config[idx]
    s['ip'] = request.form['ip']
    s['query'] = request.form['query'].strip()
    s['interval'] = int(request.form['interval'])
    s['unit_id'] = int(request.form['unit_id'])
    s['start_reg'] = int(request.form['start_reg'])
    s['max_chars'] = int(request.form['max_chars'])
    save_config(config)
    return redirect('/')

@app.route('/delete/<int:idx>')
def delete(idx):
    config = load_config()
    if idx < len(config):
        config.pop(idx)
        save_config(config)
    return redirect('/')

# === CHẠY ỨNG DỤNG ===
if __name__ == '__main__':
    print("Khởi động hệ thống truyền SCADA → LED...")
    start_monitoring()
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
