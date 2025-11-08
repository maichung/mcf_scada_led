import json
import threading
import time
import schedule
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for, jsonify
from pymodbus.client.sync import ModbusTcpClient
from pyodbc import connect

app = Flask(__name__)

# Connection string SQL Server (chỉnh sửa theo DB của bạn)
SQL_CONN_STR = 'DRIVER={ODBC Driver 18 for SQL Server};SERVER=172.16.100.34;DATABASE=BinhMinhDN;UID=sa;PWD=Binhminh@123;TrustServerCertificate=yes;'

# File config
CONFIG_FILE = 'config.json'

# Load config từ JSON
def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

# Save config to JSON
def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

# Query SQL Server (trả về string hoặc None nếu trống)
def execute_query(query_sql):
    try:
        conn = connect(SQL_CONN_STR)
        cursor = conn.cursor()
        cursor.execute(query_sql)
        row = cursor.fetchone()
        result = row[0] if row else None
        conn.close()
        return str(result) if result else None
    except Exception as e:
        print(f"SQL Error: {e}")
        return None

# Gửi dữ liệu Modbus TCP (ASCII string đến register 0)
def send_to_led(ip, value):
    if not value:
        value = ''  # Gửi trống nếu None
    try:
        client = ModbusTcpClient(ip, port=502)
        client.connect()
        # Encode string thành bytes ASCII, viết vào multiple registers (giả sử 5 registers cho 10 chars)
        bytes_data = value.encode('ascii')[:10]  # Max 10 chars
        # Pad với spaces nếu ngắn
        bytes_data += b' ' * (10 - len(bytes_data))
        # Write multiple registers (function 16), mỗi byte 1 register (thực tế Modbus 16-bit, cần adjust nếu cần)
        # Giả sử gửi 5 registers (10 bytes)
        regs = [int.from_bytes(bytes_data[i:i+2], 'big') for i in range(0, 10, 2)]
        client.write_registers(0, regs[:5], unit=1)  # Unit=1 là slave ID mặc định
        client.close()
        print(f"Sent '{value}' to {ip}")
        return True
    except Exception as e:
        print(f"Modbus Error to {ip}: {e}")
        return False

# Background task cho mỗi màn hình
def monitor_screen(config_item):
    name = config_item['name']
    ip = config_item['ip']
    query = config_item['query']
    interval = config_item['interval']
    last_value = config_item.get('last_value', None)

    def job():
        nonlocal last_value
        current_value = execute_query(query)
        if current_value != last_value:
            if send_to_led(ip, current_value):
                last_value = current_value
                # Update config
                config = load_config()
                for item in config:
                    if item['name'] == name:
                        item['last_value'] = last_value
                        save_config(config)
                        break

    schedule.every(interval).seconds.do(job)
    while True:
        schedule.run_pending()
        time.sleep(1)

# Khởi động threads cho tất cả màn hình
def start_monitoring():
    config = load_config()
    for item in config:
        thread = threading.Thread(target=monitor_screen, args=(item,), daemon=True)
        thread.start()

# Web GUI Templates (HTML đơn giản)
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head><title>Quản lý Màn hình LED - Truyền dữ liệu SCADA</title></head>
<body>
    <h1>Quản lý Truyền Dữ Liệu SCADA đến Màn Hình LED</h1>
    <h2>Danh sách Màn hình (Max 3)</h2>
    <ul>
    {% for screen in screens %}
        <li>{{ screen.name }} - IP: {{ screen.ip }} | Query: {{ screen.query }} | Interval: {{ screen.interval }}s | Last: {{ screen.last_value or 'None' }}
        <a href="/edit/{{ screen.name }}">Sửa</a> | <a href="/delete/{{ screen.name }}">Xóa</a>
        </li>
    {% endfor %}
    </ul>
    <h2>Thêm Màn hình Mới</h2>
    <form method="POST" action="/add">
        Tên: <input name="name" required><br>
        IP: <input name="ip" required><br>
        Query SQL: <input name="query" required placeholder="SELECT TOP 1 masp FROM view"><br>
        Interval (giây): <input name="interval" type="number" value="30" required><br>
        <input type="submit" value="Thêm">
    </form>
    <p>Ví dụ query kết quả: A11S-12, U70-19, T414-1-12</p>
</body>
</html>
'''

EDIT_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head><title>Sửa Màn hình</title></head>
<body>
    <h1>Sửa {{ name }}</h1>
    <form method="POST" action="/update/{{ name }}">
        IP: <input name="ip" value="{{ ip }}" required><br>
        Query SQL: <input name="query" value="{{ query }}" required><br>
        Interval (giây): <input name="interval" type="number" value="{{ interval }}" required><br>
        <input type="submit" value="Cập nhật">
    </form>
    <a href="/">Quay lại</a>
</body>
</html>
'''

@app.route('/')
def index():
    screens = load_config()
    return render_template_string(HTML_TEMPLATE, screens=screens)

@app.route('/add', methods=['POST'])
def add_screen():
    config = load_config()
    if len(config) >= 3:
        return "Đã đủ 3 màn hình!", 400
    new_item = {
        'name': request.form['name'],
        'ip': request.form['ip'],
        'query': request.form['query'],
        'interval': int(request.form['interval']),
        'last_value': None
    }
    config.append(new_item)
    save_config(config)
    return redirect(url_for('index'))

@app.route('/edit/<name>')
def edit_screen(name):
    config = load_config()
    screen = next((item for item in config if item['name'] == name), None)
    if not screen:
        return "Không tìm thấy!", 404
    return render_template_string(EDIT_TEMPLATE, name=name, ip=screen['ip'], query=screen['query'], interval=screen['interval'])

@app.route('/update/<name>', methods=['POST'])
def update_screen(name):
    config = load_config()
    for item in config:
        if item['name'] == name:
            item['ip'] = request.form['ip']
            item['query'] = request.form['query']
            item['interval'] = int(request.form['interval'])
            break
    save_config(config)
    return redirect(url_for('index'))

@app.route('/delete/<name>')
def delete_screen(name):
    config = load_config()
    config = [item for item in config if item['name'] != name]
    save_config(config)
    return redirect(url_for('index'))

if __name__ == '__main__':
    # Khởi động monitoring background
    start_monitoring()
    # Chạy web server
    app.run(host='0.0.0.0', port=5000, debug=False)
