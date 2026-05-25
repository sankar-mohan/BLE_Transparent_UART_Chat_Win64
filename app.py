import asyncio
import threading
import logging
import webbrowser

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
from bleak import BleakScanner, BleakClient

# =========================================================
# LOGGING
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger(__name__)

# =========================================================
# FLASK
# =========================================================
app = Flask(__name__)
app.config["SECRET_KEY"] = "secret"

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading"
)

# =========================================================
# BLE UUIDs
# =========================================================
SERVICE_UUID = "49535343-FE7D-4AE5-8FA9-9FAFD205E455"

TX_CHAR_UUID = "49535343-1E4D-4BD9-BA61-23C647249616"
RX_CHAR_UUID = "49535343-8841-43F4-A8D4-ECBE34729BB3"

# =========================================================
# BLE MANAGER
# =========================================================
class BLEManager:

    def __init__(self):

        self.client = None

        self.loop = asyncio.new_event_loop()

        self.lock = threading.Lock()

        self.thread = threading.Thread(
            target=self.run_loop,
            daemon=True
        )

        self.thread.start()

    # -----------------------------------------------------
    # RUN ASYNC LOOP
    # -----------------------------------------------------
    def run_loop(self):

        asyncio.set_event_loop(self.loop)

        self.loop.run_forever()

    # -----------------------------------------------------
    # SCAN DEVICES
    # -----------------------------------------------------
    async def scan(self):

        logger.info("Scanning BLE devices...")

        devices = await BleakScanner.discover(timeout=5.0)

        result = []

        for dev in devices:

            result.append({
                "name": dev.name or "Unknown",
                "address": dev.address
            })

        logger.info(f"Found {len(result)} devices")

        return result

    # -----------------------------------------------------
    # CONNECT DEVICE
    # -----------------------------------------------------
    async def connect(self, address):

        try:

            with self.lock:

                # Disconnect previous client
                if self.client and self.client.is_connected:

                    logger.info("Disconnecting previous device")

                    await self.client.disconnect()

                logger.info(f"Connecting to {address}")

                self.client = BleakClient(address)

                await self.client.connect(timeout=10.0)

                if not self.client.is_connected:

                    return {
                        "success": False,
                        "error": "Connection failed"
                    }

                logger.info("BLE Connected")

                await asyncio.sleep(1)

                # Validate UART service
                services = self.client.services

                has_uart = any(
                    s.uuid.lower() == SERVICE_UUID.lower()
                    for s in services
                )

                if not has_uart:

                    await self.client.disconnect()

                    return {
                        "success": False,
                        "error": "UART service not found"
                    }

                logger.info("UART Service Found")

                # Enable notifications
                await self.client.start_notify(
                    TX_CHAR_UUID,
                    self.notification_handler
                )

                logger.info("Notifications Enabled")

                socketio.emit(
                    "status",
                    "CONNECTED"
                )

                return {
                    "success": True
                }

        except Exception as e:

            logger.exception("BLE Connect Error")

            return {
                "success": False,
                "error": str(e)
            }

    # -----------------------------------------------------
    # DISCONNECT DEVICE
    # -----------------------------------------------------
    async def disconnect(self):

        try:

            with self.lock:

                if self.client and self.client.is_connected:

                    logger.info("Stopping notifications")

                    try:
                        await self.client.stop_notify(
                            TX_CHAR_UUID
                        )
                    except:
                        pass

                    logger.info("Disconnecting BLE device")

                    await self.client.disconnect()

                    logger.info("BLE Disconnected")

                self.client = None

                socketio.emit(
                    "status",
                    "DISCONNECTED"
                )

                return {
                    "success": True
                }

        except Exception as e:

            logger.exception("Disconnect Error")

            return {
                "success": False,
                "error": str(e)
            }

    # -----------------------------------------------------
    # NOTIFICATION HANDLER
    # -----------------------------------------------------
    def notification_handler(self, sender, data):

        try:

            message = data.decode(
                "utf-8",
                errors="ignore"
            )

            logger.info(f"RX : {message}")

            # IMPORTANT:
            # Send plain string to webpage
            socketio.emit(
                "message",
                message
            )

        except Exception as e:

            logger.exception(
                f"Notification Error : {e}"
            )

    # -----------------------------------------------------
    # SEND MESSAGE
    # -----------------------------------------------------
    async def send_message(self, message):

        try:

            if not self.client:

                return {
                    "success": False,
                    "error": "BLE not connected"
                }

            if not self.client.is_connected:

                return {
                    "success": False,
                    "error": "BLE disconnected"
                }

            payload = (
                message + "\r\n"
            ).encode("utf-8")

            await self.client.write_gatt_char(
                RX_CHAR_UUID,
                payload
            )

            logger.info(f"TX : {message}")

            return {
                "success": True
            }

        except Exception as e:

            logger.exception("BLE Write Error")

            return {
                "success": False,
                "error": str(e)
            }


# =========================================================
# BLE MANAGER INSTANCE
# =========================================================
ble_manager = BLEManager()

# =========================================================
# ROUTES
# =========================================================
@app.route("/")
def index():

    return render_template("index.html")


# ---------------------------------------------------------
# SCAN BLE
# ---------------------------------------------------------
@app.route("/scan_ble", methods=["GET"])
def scan_ble():

    future = asyncio.run_coroutine_threadsafe(
        ble_manager.scan(),
        ble_manager.loop
    )

    devices = future.result()

    return jsonify(devices)


# ---------------------------------------------------------
# CONNECT BLE
# ---------------------------------------------------------
@app.route("/connect_ble", methods=["POST"])
def connect_ble():

    data = request.get_json()

    if not data:

        return jsonify({
            "success": False,
            "error": "Missing JSON"
        })

    address = data.get("address")

    if not address:

        return jsonify({
            "success": False,
            "error": "Missing address"
        })

    future = asyncio.run_coroutine_threadsafe(
        ble_manager.connect(address),
        ble_manager.loop
    )

    result = future.result()

    return jsonify(result)


# ---------------------------------------------------------
# DISCONNECT BLE
# ---------------------------------------------------------
@app.route("/disconnect_ble", methods=["POST"])
def disconnect_ble():

    future = asyncio.run_coroutine_threadsafe(
        ble_manager.disconnect(),
        ble_manager.loop
    )

    result = future.result()

    return jsonify(result)


# ---------------------------------------------------------
# CONNECTION STATUS
# ---------------------------------------------------------
@app.route("/connection_status", methods=["GET"])
def connection_status():

    connected = False
    address = None

    if ble_manager.client and ble_manager.client.is_connected:
        connected = True
        address = ble_manager.client.address

    return jsonify({
        "connected": connected,
        "address": address
    })


# =========================================================
# SOCKET EVENTS
# =========================================================
@socketio.on("connect")
def socket_connect():

    logger.info("Web Client Connected")


@socketio.on("disconnect")
def socket_disconnect():

    logger.info("Web Client Disconnected")


# ---------------------------------------------------------
# MESSAGE FROM WEBPAGE
# ---------------------------------------------------------
@socketio.on("message")
def handle_message(message):

    logger.info(f"WEB TX : {message}")

    future = asyncio.run_coroutine_threadsafe(
        ble_manager.send_message(message),
        ble_manager.loop
    )

    result = future.result()

    if not result["success"]:

        socketio.emit(
            "message",
            f"ERROR : {result['error']}"
        )


# =========================================================
# MAIN
# =========================================================
def open_browser():
    webbrowser.open("http://localhost:5000")

if __name__ == "__main__":

    logger.info("Starting BLE Flask WebApp")

    threading.Timer(1.5, open_browser).start()

    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=False
    )