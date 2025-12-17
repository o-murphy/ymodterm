import logging
from typing import Optional, Union, List, Callable, Any
from pathlib import Path
from queue import Queue, Empty
import time
from enum import Enum
from html import escape

from ymodem.Protocol import ProtocolType
from ymodem.Socket import ModemSocket


# Import necessary classes from qtpy
from qtpy.QtWidgets import (
    QApplication,
    QMainWindow,
    QLabel,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QComboBox,
    QPushButton,
    QMessageBox,
    QPlainTextEdit,
    QLineEdit,
    QCheckBox,
    QGridLayout,
    QFrame,
    QFileDialog,
    QProgressDialog,
    QListView,
    QMenu,
    QSplitter,
)
from qtpy.QtGui import QIntValidator, QTextCursor, QTextCharFormat, QColor
from qtpy.QtCore import (
    Qt,
    QObject,
    QCoreApplication,
    QSettings,
    QTimer,
    Signal,
    QStringListModel,
)
from qtpy.QtSerialPort import QSerialPort, QSerialPortInfo


logging.basicConfig(level=logging.DEBUG, format="%(message)s")

logger = logging.getLogger("ymodterm")


def parse_hex_string_to_bytes(hex_string: str) -> bytes:
    cleaned_string = hex_string.lower()
    cleaned_string = (
        cleaned_string.replace("0x", "")
        .replace(" ", "")
        .replace("\t", "")
        .replace("\n", "")
    )

    if len(cleaned_string) % 2 != 0:
        raise ValueError(f"Invalid hex string length: {len(cleaned_string)}")

    try:
        return bytes.fromhex(cleaned_string)

    except ValueError as e:
        raise ValueError(f"Invalid hex symbol: {e}")


CTRL_NAMES = {
    0x00: "^@",
    0x01: "^A",
    0x02: "^B",
    0x03: "^C",
    0x04: "^D",
    0x05: "^E",
    0x06: "^F",
    0x07: "^G",
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0B: "\\v",
    0x0C: "\\f",
    0x0D: "\\r",
    0x7F: "^?",
}


# def decode_with_hex_fallback(
#     data: bytes,
#     *,
#     hex_output: bool = False,
#     display_ctrl_chars: bool = False,
# ) -> str:
#     # 1️⃣ Hex mode
#     if hex_output:
#         return " ".join(f"{b:02X}" for b in data)

#     out: list[str] = []
#     i = 0

#     while i < len(data):
#         b = data[i]

#         # ---------- ASCII (включно з control chars) ----------
#         if b < 0x80:
#             if b < 0x20 or b == 0x7F:
#                 # control chars
#                 if display_ctrl_chars:
#                     out.append(CTRL_NAMES.get(b, f"\\x{b:02X}"))
#                 else:
#                     out.append(chr(b))  # ← ПЛЕЙН ТЕКСТ
#             else:
#                 out.append(chr(b))

#             i += 1
#             continue

#         # ---------- UTF-8 ----------
#         try:
#             char = data[i:].decode("utf-8", errors="strict")
#             out.append(char)
#             break

#         except UnicodeDecodeError as e:
#             if e.start > 0:
#                 out.append(data[i : i + e.start].decode("utf-8", errors="strict"))
#                 i += e.start
#             else:
#                 # реально битий байт
#                 out.append(f"<0x{b:02X}>")
#                 i += 1

#     return "".join(out)


def decode_with_hex_fallback(
    data: bytes,
    *,
    hex_output: bool = False,
    display_ctrl_chars: bool = False,
) -> str:
    # 1️⃣ Hex mode
    if hex_output:
        return " ".join(f"{b:02X}" for b in data)

    out: list[str] = []
    i = 0

    while i < len(data):
        b = data[i]

        # ---------- ASCII (включно з control chars) ----------
        if b < 0x80:
            if b < 0x20 or b == 0x7F:
                # control chars
                if display_ctrl_chars:
                    out.append(f"<0x{b:02X}>")
                else:
                    out.append(chr(b))
            else:
                out.append(chr(b))

            i += 1
            continue

        # ---------- UTF-8 ----------
        # Визначаємо довжину UTF-8 послідовності
        if (b & 0b11100000) == 0b11000000:
            seq_len = 2
        elif (b & 0b11110000) == 0b11100000:
            seq_len = 3
        elif (b & 0b11111000) == 0b11110000:
            seq_len = 4
        else:
            # Невалідний стартовий байт UTF-8
            out.append(f"<0x{b:02X}>")
            i += 1
            continue

        # Перевіряємо, чи вистачає байтів
        if i + seq_len > len(data):
            out.append(f"<0x{b:02X}>")
            i += 1
            continue

        # Спробуємо декодувати послідовність
        try:
            char = data[i:i + seq_len].decode("utf-8", errors="strict")
            out.append(char)
            i += seq_len
        except UnicodeDecodeError:
            # Якщо декодування не вдалося
            out.append(f"<0x{b:02X}>")
            i += 1

    return "".join(out)

_DEFAULTS = {
    "AutoReconnect": False,
    "RTS": False,
    "DTR": True,
    # Input
    "LineEnd": "CR",
    "AutoReturn": True,
    "ModemProtocol": "YModem",
    # output
    "HexOutput": False,
    "LogToFile": False,
    # Settings tab
    "Baudrate": str(QSerialPort.BaudRate.Baud9600.value),
    "DataBits": QSerialPort.DataBits.Data8.value,
    "StopBits": QSerialPort.StopBits.OneStop.value,
    "Parity": QSerialPort.Parity.NoParity.value,
    "FlowControl": QSerialPort.FlowControl.NoFlowControl.value,
    "OpenMode": QSerialPort.OpenModeFlag.ReadWrite.value,
    "DisplayCtrlChars": False,
    "ShowTimeStamp": False,
    "Logfile": (Path("~").expanduser() / ".ymodterm.log").as_posix(),
    "LogfileAppendMode": False,
    "SettingsIsVisible": False,
}

_FLOW_CTRL_ITEMS = {
    "None": QSerialPort.FlowControl.NoFlowControl,
    "Hardware": QSerialPort.FlowControl.HardwareControl,
    "Software": QSerialPort.FlowControl.SoftwareControl,
}

_STOP_BITS_ITEMS = {
    "One Stop": QSerialPort.StopBits.OneStop,
    "Two Stop": QSerialPort.StopBits.TwoStop,
    "One and Half": QSerialPort.StopBits.OneAndHalfStop,
}

_OPEN_MODE_ITEMS = {
    "Read Only": QSerialPort.OpenModeFlag.ReadOnly,
    "WriteOnly": QSerialPort.OpenModeFlag.WriteOnly,
    "Read/Write": QSerialPort.OpenModeFlag.ReadWrite,
}

_PARITY_ITEMS = {
    str(v.name).replace("Parity", "").replace("No", "None"): v
    for v in QSerialPort.Parity.__members__.values()
}

_BAUDRATE_ITEMS = [str(v.value) for v in QSerialPort.BaudRate.__members__.values()]


class QModemSocket(ModemSocket):
    def __init__(
        self,
        read,
        write,
        protocol_type=ProtocolType.YMODEM,
        protocol_type_options=None,
    ):
        super().__init__(read, write, protocol_type, protocol_type_options)
        self._canceled = False

    def cancel(self):
        """Sets the cancellation flag"""
        self._canceled = True
        logger.info("[MODEM] Transfer cancellation requested")

    def _abort(self):
        """Override _abort so it works even with cancel"""
        logger.debug("[MODEM] Calling _abort")
        return super()._abort()
    
    def _read_and_wait(self, 
                        wait_chars: List[str],
                        wait_time: int = 1
                        ) -> Optional[str]:
        start_time = time.perf_counter()
        while True:
            if self._canceled:
                return None
            t = time.perf_counter() - start_time
            if t > wait_time:
                return None
            c = self.read(1)
            if c in wait_chars:
                return c
    
    def _write_and_wait(self, 
                        write_char: str, 
                        wait_chars: List[str],
                        wait_time: int = 1
                        ) -> Optional[str]:
        start_time = time.perf_counter()
        self.write(write_char)
        while True:
            if self._canceled:
                return None
            t = time.perf_counter() - start_time
            if t > wait_time:
                return None
            c = self.read(1)
            if c in wait_chars:
                return c
    
    def send(self, paths, callback=None):
        """
        Override send to check for cancel at the beginning
        """
        if self._canceled:
            logger.info("[MODEM] Send aborted: already canceled")
            return False

        try:
            return super().send(paths, callback)
        except Exception as e:
            if self._canceled:
                logger.info("[MODEM] Send interrupted by cancellation")
                self._abort()  # Sending CAN
                return False
            raise

    def recv(self, save_directory, callback=None):
        """
        Override recv to check cancel
        """
        if self._canceled:
            logger.info("[MODEM] Recv aborted: already canceled")
            return False

        try:
            return super().recv(save_directory, callback)
        except Exception as e:
            if self._canceled:
                logger.info("[MODEM] Recv interrupted by cancellation")
                self._abort()  # Sending CAN
                return False
            raise


class QSerialPortModemAdapter(QObject):
    """QSerialPort adapter fro  ymodem.ModemSocket via queue"""

    def __init__(self, serial_port: QSerialPort):
        super().__init__()
        self.serial = serial_port
        self.logger = logger.getChild("modem_adapter")
        self.read_queue = Queue()

    def read(self, size: int, timeout: Optional[float] = 1) -> Optional[bytes]:
        if timeout is None:
            timeout = 1.0

        data = bytearray()
        start_time = time.time()

        while len(data) < size:
            remaining_time = timeout - (time.time() - start_time)

            if remaining_time <= 0:
                break

            try:
                chunk = self.read_queue.get(timeout=min(0.01, remaining_time))
                data.extend(chunk)
            except Empty:
                # Process Qt Events
                QCoreApplication.processEvents()
                continue

        if len(data) == 0:
            return None

        if len(data) > size:
            excess = bytes(data[size:])
            self.read_queue.put(excess)
            return bytes(data[:size])

        return bytes(data)

    def write(
        self, data: Union[bytes, bytearray], timeout: Optional[float] = 1
    ) -> Optional[int]:
        if timeout is None:
            timeout = 1.0

        written = self.serial.write(data)

        if written == -1:
            self.logger.warning("Write failed")
            return None

        start_time = time.time()
        while self.serial.bytesToWrite() > 0:
            if time.time() - start_time > timeout:
                self.logger.warning("Write timeout")
                return None
            QCoreApplication.processEvents()

        return written

    def clear_queue(self):
        while not self.read_queue.empty():
            try:
                self.read_queue.get_nowait()
            except Empty:
                break


class ModemTransferManager(QObject):
    progress = Signal(object)
    finished = Signal(bool)
    error = Signal(str)
    log = Signal(str)
    started = Signal()

    def __init__(self, serial_port: QSerialPort):
        super().__init__()
        self.serial_port = serial_port
        self.adapter = None
        self.modem = None  # Тепер це буде CustomModemSocket
        self._is_running = False
        self._is_cancelled = False
        self._is_finishing = False

        self._files_to_send = []
        self._save_directory = ""
        self._protocol = None
        self._options = []
        self._mode = None

    def is_running(self) -> bool:
        return self._is_running

    def put_to_queue(self, data: bytes):
        if self.adapter:
            self.adapter.read_queue.put(bytes(data))
        else:
            logger.debug("Adapter is not initialized")

    def send_files(self, files: List[str], protocol: int, options: List[str] = None):
        if options is None:
            options = []

        if self._is_running:
            self.error.emit("Transfer already running")
            return

        self._files_to_send = files
        self._protocol = protocol
        self._options = options
        self._mode = "send"
        self._is_cancelled = False
        self._is_running = True
        self._is_finishing = False

        self.adapter = QSerialPortModemAdapter(self.serial_port)
        self.adapter.clear_queue()

        self.log.emit(f"Transfer begin {len(files)} file(s)...")
        self.started.emit()

        QTimer.singleShot(100, self._start_transfer)

    def receive_files(
        self, save_directory: str, protocol: int, options: List[str] = None
    ):
        if options is None:
            options = []

        if self._is_running:
            self.error.emit("Transfer already running")
            return

        self._save_directory = save_directory
        self._protocol = protocol
        self._options = options
        self._mode = "receive"
        self._is_cancelled = False
        self._is_running = True
        self._is_finishing = False

        self.adapter = QSerialPortModemAdapter(self.serial_port)
        self.adapter.clear_queue()

        self.log.emit(f"Waiting for transfer begin, saving to: {save_directory}")
        self.started.emit()

        QTimer.singleShot(100, self._start_transfer)

    def cancel(self):
        if not self._is_running or self._is_cancelled:
            return

        self._is_cancelled = True
        logger.debug("[MODEM] Cancel requested")
        self.log.emit("⚠ Canceling transfer...")

        if self.modem:
            self.modem.cancel()

    def _start_transfer(self):
        try:
            self.modem = QModemSocket(
                read=self.adapter.read,
                write=self.adapter.write,
                protocol_type=self._protocol,
                protocol_type_options=self._options,
            )

            def progress_callback(index: int, name: str, total: int, current: int):
                if self._is_cancelled:
                    raise Exception("Transfer canceled by user")
                self.progress.emit((index, name, total, current))
                QCoreApplication.processEvents()

            if self._mode == "send":
                success = self.modem.send(
                    self._files_to_send, callback=progress_callback
                )
            else:
                success = self.modem.recv(
                    self._save_directory, callback=progress_callback
                )

            if self._is_cancelled:
                success = False

            self._finish_transfer(success)

        except Exception as e:
            error_msg = str(e).lower()

            if "cancel" in error_msg:
                logger.debug("[MODEM] Transfer canceled in exception handler")
                self._finish_transfer(False)
            else:
                logger.error("[MODEM] Transfer error: %s", str(e))
                self.error.emit(str(e))
                self._finish_transfer(False)

    def _finish_transfer(self, success: bool):
        if self._is_finishing or not self._is_running:
            logger.debug("[MODEM] Transfer already finishing or not running")
            return

        self._is_finishing = True
        self._is_running = False

        if success and not self._is_cancelled:
            self.log.emit("✓ Transfer complete success")
        elif self._is_cancelled:
            self.log.emit("⚠ Transfer canceled by user")
        else:
            if not self._is_cancelled:
                self.log.emit("✗ Transfer failed")

        if self.adapter:
            self.adapter.clear_queue()
            self.adapter = None

        self.modem = None

        final_success = success and not self._is_cancelled
        self.finished.emit(final_success)

        logger.debug(
            "[MODEM] Transfer finished: success=%s, cancelled=%s",
            success,
            self._is_cancelled,
        )


class StatefullProp(QObject):
    changed = Signal(object)

    def __init__(self, initial_value, typ=object, /, parent=None, *, objectName=None):
        super().__init__(parent, objectName=objectName)
        self.value = initial_value
        self.typ = typ

    def get(self):
        return self.value

    def set(self, value):
        if not isinstance(value, self.typ) and self.typ is not object:
            value = self.typ(value)
        if self.value != value:
            self.value = value
            self.changed.emit(value)
        logger.debug("Value changed: %s: %s" % (self.objectName(), value))

    def bind(
        self,
        value_setter: Optional[Any] = None,
        change_signal: Optional[Signal] = None,
        cast_func: Optional[Callable[[Any], Any]] = None,
    ):
        # set initial value
        if value_setter is not None:
            value_setter(self.value)

        # bind to signal
        if change_signal and isinstance(change_signal, Signal):
            if cast_func and callable(cast_func):
                change_signal.connect(lambda value: self.set(cast_func(value)))
                logger.debug(
                    "%s binded to %s with custom cast: %s"
                    % (self, change_signal, cast_func)
                )
            else:
                change_signal.connect(self.set)
                logger.debug("%s binded to %s with auto cast" % (self, change_signal))


def prop_from_defaults(key: str) -> StatefullProp:
    value = _DEFAULTS[key]
    return StatefullProp(value, value.__class__, None, objectName=key)


class AppState(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = QSettings("o-murphy", "ymodterm")

        self.rts = prop_from_defaults("RTS")
        self.dtr = prop_from_defaults("DTR")
        self.auto_reconnect = prop_from_defaults("AutoReconnect")
        self.line_end = prop_from_defaults("LineEnd")
        self.auto_return = prop_from_defaults("AutoReturn")
        self.modem_protocol = prop_from_defaults("ModemProtocol")
        self.hex_output = prop_from_defaults("HexOutput")
        self.log_to_file = prop_from_defaults("LogToFile")
        self.baudrate = prop_from_defaults("Baudrate")
        self.data_bits = prop_from_defaults("DataBits")
        self.flow_ctrl = prop_from_defaults("FlowControl")
        self.stop_bits = prop_from_defaults("StopBits")
        self.parity = prop_from_defaults("Parity")
        self.open_mode = prop_from_defaults("OpenMode")
        self.logfile_append_mode = prop_from_defaults("LogfileAppendMode")
        self.display_ctrl_chars = prop_from_defaults("DisplayCtrlChars")
        self.show_timestamp = prop_from_defaults("ShowTimeStamp")
        self.logfile = prop_from_defaults("Logfile")

    def restore_property(self, prop: StatefullProp):
        prop_name = prop.objectName()
        fallback_value = _DEFAULTS.get(prop_name, prop.value)
        prop.set(self.settings.value(prop_name, fallback_value, prop.typ))

    def restore_settings(self):
        try:
            self.restore_property(self.rts)
            self.restore_property(self.dtr)
            self.restore_property(self.auto_reconnect)
            self.restore_property(self.line_end)
            self.restore_property(self.auto_return)
            self.restore_property(self.modem_protocol)
            self.restore_property(self.hex_output)
            self.restore_property(self.log_to_file)
            self.restore_property(self.baudrate)
            self.restore_property(self.data_bits)
            self.restore_property(self.flow_ctrl)
            self.restore_property(self.stop_bits)
            self.restore_property(self.parity)
            self.restore_property(self.open_mode)
            self.restore_property(self.logfile_append_mode)
            self.restore_property(self.display_ctrl_chars)
            self.restore_property(self.logfile)

        except EOFError as e:
            logger.error("EOFError on restore_settings: %s" % e)
            self.save_settings()

    def save_property(self, prop: StatefullProp):
        self.settings.setValue(prop.objectName(), prop.get())

    def save_settings(self):
        self.save_property(self.rts)
        self.save_property(self.dtr)
        self.save_property(self.auto_reconnect)
        self.save_property(self.line_end)
        self.save_property(self.auto_return)
        self.save_property(self.modem_protocol)
        self.save_property(self.hex_output)
        self.save_property(self.log_to_file)
        self.save_property(self.baudrate)
        self.save_property(self.data_bits)
        self.save_property(self.flow_ctrl)
        self.save_property(self.stop_bits)
        self.save_property(self.parity)
        self.save_property(self.open_mode)
        self.save_property(self.logfile_append_mode)
        self.save_property(self.display_ctrl_chars)
        self.save_property(self.show_timestamp)
        self.save_property(self.logfile)

        self.settings.sync()


class SerialManagerWidget(QWidget):
    REFRESH_INTERVAL_MS = 3000

    connection_state_changed = Signal(bool)
    data_received = Signal(bytes)

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)

        self.state = state

        self.ports: dict[str, QSerialPortInfo] = {}
        self.port: Optional[QSerialPort] = None

        # <<< Create QTimer
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._timer_refresh)
        # >>>

        self.label = QLabel("Device:")

        self.select_port = QComboBox(self)
        self.select_port.setStyleSheet("combobox-popup: 0;")
        self.select_port.setMaxVisibleItems(10)

        self.connect_btn = QPushButton("Connect")

        # create widgets
        self.auto_reconnect = CheckBox("Auto Reconnect")
        self.auto_reconnect.setDisabled(True)
        self.rts = CheckBox("RTS")
        self.dtr = CheckBox("DTR")

        # bind state
        self.rts.bind(self.state.rts)
        self.dtr.bind(self.state.dtr)
        self.auto_reconnect.bind(self.state.auto_reconnect)

        self.settings_btn = QPushButton("Show Settings")

        self.settings = SettingsWidget(state, self)

        self.hlt = QHBoxLayout()
        self.hlt.setContentsMargins(0, 0, 0, 0)
        self.hlt.addWidget(self.connect_btn)
        self.hlt.addWidget(self.label)
        self.hlt.addWidget(self.select_port)
        self.hlt.addWidget(self.rts)
        self.hlt.addWidget(self.dtr)
        self.hlt.addWidget(self.auto_reconnect)
        self.hlt.addStretch()
        self.hlt.addWidget(self.settings_btn)

        self.vlt = QVBoxLayout(self)
        self.vlt.setContentsMargins(0, 0, 0, 0)
        self.vlt.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.vlt.addWidget(self.settings)
        self.vlt.addLayout(self.hlt)

        self.refresh()
        self.connect_btn.clicked.connect(self.toggle_connect)
        self.settings_btn.clicked.connect(self.toggle_settings)

        # bind callbacks
        self.state.rts.changed.connect(self._update_dtr)
        self.state.dtr.changed.connect(self._update_dtr)

        # <<< Run timer on init
        self.refresh_timer.start(self.REFRESH_INTERVAL_MS)
        # >>>

    def _update_rts(self, state: int):
        if self.port and self.port.isOpen():
            self.port.setRequestToSend(state)

    def _update_dtr(self, state: int):
        if self.port and self.port.isOpen():
            self.port.setDataTerminalReady(state)

    def _timer_refresh(self):
        if not self.port or not self.port.isOpen():
            self.refresh()

    def refresh(self):
        """Updates the list of available serial ports."""
        current_port_text = self.select_port.currentText()

        self.ports = {p.portName(): p for p in QSerialPortInfo.availablePorts()}

        # Check if was changed
        new_port_names = sorted(self.ports.keys())
        current_combo_items = [
            self.select_port.itemText(i) for i in range(self.select_port.count())
        ]

        if new_port_names != current_combo_items:
            self.select_port.clear()
            self.select_port.addItems(new_port_names)  # Sort for better visual

            # Restore last selection
            if self.port and self.port.portName() in self.ports:
                self.select_port.setCurrentText(self.port.portName())
            elif current_port_text in self.ports:
                self.select_port.setCurrentText(current_port_text)
            elif self.ports:
                # Select the last port if the list is not empty
                self.select_port.setCurrentIndex(len(self.ports) - 1)

        self.select_port.setStyleSheet("combobox-popup: 0;")
        self.select_port.setMaxVisibleItems(10)

    def setConfigutrationEnabled(self, enabled: bool):
        self.select_port.setEnabled(enabled)
        self.settings_btn.setEnabled(enabled)

    def toggle_settings(self):
        self.settings.toggle()
        self.settings_btn.setText(
            f"{'Hide' if self.settings.isVisible() else 'Show'} Settings"
        )

    def toggle_connect(self):
        """Opens or closes the serial port."""
        if self.port and self.port.isOpen():
            # === DISCONNECT Mode ===

            self.port.close()
            # We keep the QSerialPort object, but its state is "closed"
            self.connect_btn.setText("Connect")
            self.setConfigutrationEnabled(True)
            self.connection_state_changed.emit(False)

            # <<< Restart timer
            self.refresh_timer.start(self.REFRESH_INTERVAL_MS)
            # >>>

            # Refresh
            self.refresh()
        else:
            # === CONNECT Mode ===

            port_name = self.select_port.currentText()

            if not port_name or port_name not in self.ports:
                QMessageBox.warning(self, "Error", "Please select a valid port.")
                return

            # 1. Create or reuse the QSerialPort object
            if not self.port:
                self.port = QSerialPort()

            # 2. Configure the port
            self.port.setPortName(port_name)
            self.port.setBaudRate(int(self.state.baudrate.get()))
            self.port.setDataBits(QSerialPort.DataBits(self.state.data_bits.get()))
            self.port.setParity(QSerialPort.Parity(self.state.parity.get()))
            self.port.setStopBits(QSerialPort.StopBits(self.state.stop_bits.get()))
            self.port.setFlowControl(
                QSerialPort.FlowControl(self.state.flow_ctrl.get())
            )

            # 3. Attempt to open the port
            if self.port.open(QSerialPort.OpenModeFlag(self.state.open_mode.get())):
                self.connect_btn.setText("Disconnect")
                self.setConfigutrationEnabled(False)
                self.connection_state_changed.emit(True)

                # <<< Stop timer
                self.refresh_timer.stop()
                # >>>

                self._update_rts(self.state.rts.get())
                self._update_dtr(self.state.dtr.get())

                # The readyRead signal can be connected here to read data!
                self.port.readyRead.connect(self.on_ready_read)
            else:
                # Open error handling
                error_msg = (
                    f"Failed to open port {port_name}: {self.port.errorString()}"
                )
                QMessageBox.critical(self, "Connection Error", error_msg)

    def write(self, data: bytes):
        if self.port and self.port.isOpen():
            self.port.write(data)

    def on_ready_read(self, *args):
        data = self.port.readAll().data()
        self.data_received.emit(data)


class SelectLogFileWidget(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state

        self.logfile = QLineEdit(self)
        self.logfile_append_mode = CheckBox("Append")

        self.logfile.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.get_logfile = QPushButton("...")
        self.get_logfile.setFixedWidth(32)

        self.state.logfile.bind(self.logfile.setText, self.logfile.textChanged)
        self.logfile_append_mode.bind(self.state.logfile_append_mode)

        self.lt = QHBoxLayout(self)
        self.lt.setContentsMargins(0, 0, 0, 0)
        self.lt.addWidget(QLabel("Logfile:"))
        self.lt.addWidget(self.logfile)
        self.lt.addWidget(self.get_logfile)
        self.lt.addWidget(self.logfile_append_mode)

        self.get_logfile.clicked.connect(self.on_get_log_file)

    def on_get_log_file(self):
        file_dialog = QFileDialog(self, "Save log file ...")
        file_dialog.setAcceptMode(QFileDialog.AcceptSave)
        file_dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        file_dialog.setOption(
            QFileDialog.Option.DontConfirmOverwrite,
            self.state.logfile_append_mode.get(),
        )
        result = file_dialog.exec_()
        print(result)
        if result == QFileDialog.Accepted:
            files = file_dialog.selectedFiles()
            if files and len(files):
                self.state.logfile.set(files[0])


class EnumComboBox(QComboBox):
    def __init__(self, enum: Enum, parent=None):
        super().__init__(parent)
        self.prop = None
        self.enum = enum

    def bind(self, prop: StatefullProp):
        self.prop = prop
        self.prop.bind(
            self._cast_from_prop, self.currentIndexChanged, self._cast_to_prop
        )

    def _cast_to_prop(self, _: Any):
        return self.currentData().value

    def _cast_from_prop(self, value: Any):
        self.setCurrentIndex(self.findData(self.enum(value)))


class CheckBox(QCheckBox):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.prop = None

    def bind(self, prop: StatefullProp):
        self.prop = prop
        self.prop.bind(self.setChecked, self.toggled)


class SettingsWidget(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state

        self.baudrate = QComboBox(self)
        self.data_bits = EnumComboBox(QSerialPort.DataBits, self)
        self.flow_ctrl = EnumComboBox(QSerialPort.FlowControl, self)
        self.stop_bits = EnumComboBox(QSerialPort.StopBits, self)
        self.parity = EnumComboBox(QSerialPort.Parity, self)
        self.open_mode = EnumComboBox(QSerialPort.OpenModeFlag, self)
        self.display_ctrl_chars = CheckBox("Display Ctrl Characters")
        self.show_timestamp = CheckBox("Show Timestamp")
        self.logfile = SelectLogFileWidget(state, self)

        self.baudrate.setEditable(True)
        baud_validator = QIntValidator(0, 10000000, self)
        self.baudrate.lineEdit().setValidator(baud_validator)

        self.show_timestamp.setDisabled(True)

        self.baudrate.addItems(_BAUDRATE_ITEMS)

        for k, v in QSerialPort.DataBits.__members__.items():
            self.data_bits.addItem(str(v.value), v)

        for k, v in _FLOW_CTRL_ITEMS.items():
            self.flow_ctrl.addItem(k, userData=v)

        for k, v in _STOP_BITS_ITEMS.items():
            self.stop_bits.addItem(k, userData=v)

        for k, v in _PARITY_ITEMS.items():
            self.parity.addItem(k, userData=v)

        for k, v in _OPEN_MODE_ITEMS.items():
            self.open_mode.addItem(k, userData=v)

        self.display_ctrl_chars.bind(self.state.display_ctrl_chars)
        self.show_timestamp.bind(self.state.show_timestamp)

        self.state.baudrate.bind(
            self.baudrate.setCurrentText,
            self.baudrate.currentTextChanged,
        )

        self.data_bits.bind(self.state.data_bits)
        self.flow_ctrl.bind(self.state.flow_ctrl)
        self.stop_bits.bind(self.state.stop_bits)
        self.parity.bind(self.state.parity)
        self.open_mode.bind(self.state.open_mode)

        self.grid = QGridLayout()
        self.grid.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.grid.addWidget(QLabel("Baudrate"), 0, 0)
        self.grid.addWidget(self.baudrate, 0, 1)
        self.grid.addWidget(QLabel("Data Bits"), 0, 2)
        self.grid.addWidget(self.data_bits, 0, 3)

        self.grid.addWidget(self.display_ctrl_chars, 0, 4)

        self.grid.addWidget(QLabel("Flow Control"), 1, 0)
        self.grid.addWidget(self.flow_ctrl, 1, 1)
        self.grid.addWidget(QLabel("Parity"), 1, 2)
        self.grid.addWidget(self.parity, 1, 3)

        self.grid.addWidget(self.show_timestamp, 1, 4)

        self.grid.addWidget(QLabel("Open Mode"), 2, 0)
        self.grid.addWidget(self.open_mode, 2, 1)
        self.grid.addWidget(QLabel("Stop Bits"), 2, 2)
        self.grid.addWidget(self.stop_bits, 2, 3)
        self.grid.addWidget(self.logfile, 2, 4, 1, 2)

        self.vlt = QVBoxLayout(self)
        self.vlt.setContentsMargins(0, 0, 0, 0)
        self.vlt.addLayout(self.grid)
        self.vlt.addWidget(HLineWidget(self))

        self.setVisible(False)

    def toggle(self):
        self.setVisible(not self.isVisible())


class TerminalInput(QLineEdit):
    send_return = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Write to device (Enter to send)")
        self._char_map = {}  # {position: actual_char}

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        # 1. Enter
        if key == Qt.Key.Key_Return:
            self.send_return.emit()
            return

        # 2. Ctrl + A-Z (with no Alt)
        ctrl_only = (modifiers & Qt.KeyboardModifier.ControlModifier) and not (
            modifiers & Qt.KeyboardModifier.AltModifier
        )

        if ctrl_only and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            control_code = key - Qt.Key.Key_A + 1
            ctrl_name = f"^{chr(key)}"
            self._insert_visual_char(chr(control_code), ctrl_name)
            return

        # 3. Ctrl+@
        if ctrl_only and key == Qt.Key.Key_At:
            self._insert_visual_char("\x00", "^@")
            return

        # 4. Tab
        if key == Qt.Key.Key_Tab:
            self._insert_visual_char("\t", "\\t")
            return

        super().keyPressEvent(event)
        self._rebuild_char_map()

    def _insert_visual_char(self, actual_char: str, display_text: str):
        cursor_pos = self.cursorPosition()
        current_text = self.text()

        new_text = current_text[:cursor_pos] + display_text + current_text[cursor_pos:]
        self.setText(new_text)
        self.setCursorPosition(cursor_pos + len(display_text))

        self._char_map[cursor_pos] = (actual_char, len(display_text))

    def _rebuild_char_map(self):
        pass


class InputWidget(QWidget):
    send_clicked = Signal(str)
    send_file_selected = Signal(object)

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state

        self.edit = TerminalInput(self)
        self.edit.setPlaceholderText("Write to device")
        self.return_btn = QPushButton("Return ⏎")

        self.line_end = QComboBox(self)
        self.line_end.addItems(["LF", "CR", "CR/LF", "None", "Hex"])

        self.auto_return = CheckBox("Auto ⏎")

        self.modem_protocol = QComboBox(self)
        self.modem_protocol.addItems(["YModem", "YModem-G", "XModem", "ZModem"])

        self.state.line_end.bind(
            self.line_end.setCurrentText, self.line_end.currentTextChanged
        )
        self.auto_return.bind(self.state.auto_return)
        self.state.modem_protocol.bind(
            self.modem_protocol.setCurrentText, self.modem_protocol.currentTextChanged
        )

        self.send_file_btn = QPushButton("Send File ...")

        self.lt = QHBoxLayout(self)
        self.lt.setContentsMargins(0, 0, 0, 0)
        self.lt.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)
        self.lt.addWidget(self.edit)
        self.lt.addWidget(self.line_end)
        self.lt.addWidget(self.return_btn)
        self.lt.addWidget(self.auto_return)

        self.lt.addWidget(self.send_file_btn)
        self.lt.addWidget(self.modem_protocol)

        self.edit.send_return.connect(self.on_return_triggered)
        self.edit.textChanged.connect(self.on_return_triggered)
        self.return_btn.clicked.connect(self.on_return_triggered)
        self.send_file_btn.clicked.connect(self.on_send_file_clicked)

        self.state.auto_return.changed.connect(self.toggle_auto_return)
        # force state set
        self.toggle_auto_return(self.state.auto_return.get())

    def toggle_auto_return(self, state: bool):
        self.return_btn.setDisabled(state)
        try:
            if state:
                self.edit.textChanged.connect(self.on_return_triggered)
            else:
                self.edit.textChanged.disconnect(self.on_return_triggered)
        except TypeError:
            pass

    def on_return_triggered(self):
        text = self.edit.text().strip()
        if not text:
            return

        self.send_clicked.emit(text)
        self.edit.clear()

    def on_send_file_clicked(self):
        file, _ = QFileDialog.getOpenFileName(self, "Open file ...")
        if not file:
            return

        options = []
        match self.state.modem_protocol.get():
            case "YModem":
                protocol = ProtocolType.YMODEM
            case "YModem-G":
                protocol = ProtocolType.YMODEM
                options.append("g")
            case "XModem":
                protocol = ProtocolType.XMODEM
            case "ZModem":
                protocol = ProtocolType.ZMODEM
            case _:
                raise ValueError("Unsupported Transfer Protocol")

        self.send_file_selected.emit((file, protocol, options))


class HLineWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.HLine)
        self.setFrameShadow(QFrame.Sunken)


class OutputViewWidget(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state

        self.text_view = QPlainTextEdit(self)
        self.text_view.setReadOnly(True)

        self.hex_output = CheckBox("Hex Output")
        self.log_to_file = CheckBox("Logging to:")
        self.log_to_file.setDisabled(True)

        self.hex_output.bind(self.state.hex_output)
        self.log_to_file.bind(self.state.log_to_file)

        self.clear_button = QPushButton("Clear")
        self.logfile = QLabel("")

        self.state.logfile.changed.connect(self.logfile.setText)

        self.hlt = QHBoxLayout()
        self.hlt.setContentsMargins(0, 0, 0, 0)
        self.hlt.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.hlt.addWidget(self.clear_button)
        self.hlt.addWidget(self.hex_output)
        self.hlt.addWidget(self.log_to_file)
        self.hlt.addWidget(self.logfile)

        self.vlt = QVBoxLayout(self)
        self.vlt.setContentsMargins(0, 0, 0, 0)
        self.vlt.addWidget(self.text_view)
        self.vlt.addLayout(self.hlt)

        self.clear_button.clicked.connect(self.text_view.clear)

    def insertPlainBytesOrStr(
        self, data: bytes | str, prefix: str = "", suffix: str = ""
    ):
        if isinstance(data, str):
            if self.state.hex_output.get():
                output_string = data.encode("utf-8").hex(" ").upper()
            else:
                output_string = data

        elif isinstance(data, bytes):
            output_string = decode_with_hex_fallback(
                data,
                hex_output=self.state.hex_output.get(),
                display_ctrl_chars=self.state.display_ctrl_chars.get(),
            )

        else:
            return

        prepared_string = prefix + output_string + suffix
        self.text_view.insertPlainText(prepared_string)
        self.text_view.verticalScrollBar().setValue(
            self.text_view.verticalScrollBar().maximum()
        )
        self.insertToLogfile(prepared_string)

    def insertToLogfile(self, string):
        if self.state.log_to_file.get():
            QMessageBox.warning(
                self,
                "Error",
                "`insertToLogfile` not yet implemented, disable `Logging to:`",
            )
            raise NotImplementedError

    def _insert_colored_text(self, text: str, color: QColor | None):
        cursor = self.text_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextCharFormat()
        if color:
            fmt.setForeground(color)

        cursor.insertText(text, fmt)
        self.insertToLogfile(text)


class InputHistory(QListView):
    line_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.model: QStringListModel = QStringListModel()
        self.setModel(self.model)

        self.clicked.connect(self.on_click)

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.on_context_menu)

    def on_click(self, index):
        if not index.isValid():
            return
        text = index.data(Qt.DisplayRole)
        self.line_clicked.emit(text)

    def on_context_menu(self, pos):
        menu = QMenu(self)

        index = self.indexAt(pos)
        has_item = index.isValid()

        act_remove = menu.addAction("Remove item")
        act_remove.setEnabled(has_item)

        menu.addSeparator()

        act_clear = menu.addAction("Clear all")

        action = menu.exec_(self.viewport().mapToGlobal(pos))

        if action == act_remove and has_item:
            self.remove_item(index.row())

        elif action == act_clear:
            self.clear()

    def add(self, text: str):
        items = self.model.stringList()
        if text not in items:
            items.append(text)
            self.model.setStringList(items)
            self.scrollToBottom()

    def remove_item(self, row: int):
        items = self.model.stringList()
        if 0 <= row < len(items):
            items.pop(row)
            self.model.setStringList(items)

    def clear(self):
        self.model.setStringList([])


class CentralWidget(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)

        self.state = state
        self.serial_manager = SerialManagerWidget(self.state, self)

        # Worker for file send
        self.modem_manager = None
        self.progress_dialog = None
        self._transfer_in_progress = False

        self.input_history = InputHistory(self)
        self.output_view = OutputViewWidget(state, self)
        self.output_view.logfile = self.serial_manager.settings.logfile

        self.input_widget = InputWidget(self.state, self)

        self.status = QLabel(self)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.input_history)
        splitter.addWidget(self.output_view)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 7)

        self.lt = QVBoxLayout(self)
        self.lt.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.lt.addWidget(self.serial_manager)
        self.lt.addWidget(HLineWidget(self))
        self.lt.addWidget(splitter)
        self.lt.addWidget(self.input_widget)
        self.lt.addWidget(HLineWidget(self))
        self.lt.addWidget(self.status)

        self.input_history.line_clicked.connect(self.input_widget.edit.setText)
        self.serial_manager.data_received.connect(self.on_data_received)
        self.serial_manager.connection_state_changed.connect(
            self.on_connection_state_changed
        )

        self.input_widget.send_clicked.connect(self.on_send_clicked)
        self.input_widget.send_file_selected.connect(self.on_send_file_selected)

        self.on_connection_state_changed(False)  # force on init

    def on_connection_state_changed(self, state: bool):
        stop_bits = self.state.stop_bits.get()
        if stop_bits == 3:
            stop_bits = "1.5"
        text = "Device: {port}\tConnection: {baud} @ {bits}-{parity}-{stop}".format(
            port=self.serial_manager.select_port.currentText(),
            baud=self.state.baudrate.get(),
            bits=self.state.data_bits.get(),
            parity=QSerialPort.Parity(self.state.parity.get()).name[0],
            stop=stop_bits,
        )
        self.status.setText(text)

    def on_send_clicked(self, text):
        self.input_history.add(text)

        line_end = self.state.line_end.get()
        line_end_symbols = ""
        match line_end:
            case "LF":
                line_end_symbols = "\n"
            case "CR":
                line_end_symbols = "\r"
            case "CR/LF":
                line_end_symbols = "\r\n"
            case "Hex":
                pass
            case "None":
                pass
            case _:
                QMessageBox.warning(
                    self, "Error", f"Endline `{line_end}` yet not supported"
                )
                return
        if line_end == "Hex":
            try:
                data = parse_hex_string_to_bytes(text)
            except ValueError:
                QMessageBox.warning(self, "Error", "Invalid hex input")
                return
        else:
            data = (text + line_end_symbols).encode("utf-8")

        self.serial_manager.write(data)

    def on_data_received(self, data: bytes):
        if self._transfer_in_progress and self.modem_manager:
            self.modem_manager.put_to_queue(data)
        # TODO: Should I disable output during transaction?
        self.output_view.insertPlainBytesOrStr(data)

    def on_send_file_selected(self, send_data):
        file, protocol, options = send_data

        if not self.serial_manager.port or not self.serial_manager.port.isOpen():
            QMessageBox.warning(self, "Error", "Port is not opened!")
            return

        # Init Transfer manager
        self.modem_manager = ModemTransferManager(self.serial_manager.port)

        self.progress_dialog = QProgressDialog(
            "Prepare to transfer...", "Cancel", 0, 100, self
        )
        self.progress_dialog.setMinimumWidth(400)
        self.progress_dialog.setWindowTitle(f"{protocol.name} Transfer")
        self.progress_dialog.setModal(True)
        self.progress_dialog.setMinimumDuration(0)

        # Connect signals
        self.modem_manager.progress.connect(self._on_transfer_progress)
        self.modem_manager.started.connect(self._on_transfer_started)
        self.modem_manager.finished.connect(self._on_transfer_finished)
        self.modem_manager.error.connect(self._on_transfer_error)
        self.modem_manager.log.connect(self._on_transfer_log)
        self.modem_manager.started.connect(lambda: self.progress_dialog.show())

        # Connect canceling
        self.progress_dialog.canceled.connect(self.modem_manager.cancel)

        # Begin transfer
        self.modem_manager.send_files([file], protocol, options)

    def _on_transfer_progress(self, progress_: tuple[int, str, int, int]):
        _, filename, total, current = progress_

        if not self.progress_dialog:
            return

        if total > 0:
            progress = int((current / total) * 100)
            self.progress_dialog.setValue(progress)

            def format_size(size):
                for unit in ["B", "KB", "MB", "GB"]:
                    if size < 1024.0:
                        return f"{size:.1f} {unit}"
                    size /= 1024.0
                return f"{size:.1f} TB"

            self.progress_dialog.setLabelText(
                f"Transfer: {filename}\n"
                f"{format_size(current)} / {format_size(total)} ({progress}%)"
            )
        else:
            self.progress_dialog.setLabelText(
                f"Transfer: {filename}\n{current} byte(s)"
            )

    def _on_transfer_started(self):
        self._transfer_in_progress = True
        logger.info("[MODEM] Starting file transfer...")

    def _on_transfer_finished(self, success: bool):
        self._transfer_in_progress = False

        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None

        self.modem_manager = None

        if success:
            msg = "✓ File successfully transferred!"
            logger.info("[MODEM] %s", msg)
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Information)
            msg_box.setWindowTitle("Success")
            msg_box.setText(msg)
            msg_box.show()
            QTimer.singleShot(2000, msg_box.close)

    def _on_transfer_error(self, error_msg: str):
        msg = "✗ Error occured during file transfer"
        logger.error("[MODEM] %s: %s" % (msg, error_msg))
        QMessageBox.warning(self, "Error", msg)

    def _on_transfer_log(self, log_msg: str):
        logger.debug("[MODEM] %s" % log_msg)


class YModTermWindow(QMainWindow):
    """
    A simple main application window.
    """

    def __init__(self):
        super().__init__()

        self.settings = QSettings("o-murphy", "ymodterm")
        self.state = AppState(self)
        self.state.restore_settings()

        # 1. Configure the main window properties
        self.setWindowTitle("YModTerm")
        self.setGeometry(100, 100, 600, 400)  # x, y, width, height

        # 2. Create a central widget and layout
        # QMainWindow requires a central widget to host other UI elements
        central_widget = CentralWidget(self.state)
        self.setCentralWidget(central_widget)

    def closeEvent(self, event):
        self.state.save_settings()
        super().closeEvent(event)


def main():
    import sys
    import signal

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QApplication([])
    window = YModTermWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
