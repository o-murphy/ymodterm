import logging
from typing import Optional, Union, List
from pathlib import Path
from ymodem.Protocol import ProtocolType
from queue import Queue, Empty
import time


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

#         # ---------- ASCII ----------
#         if b < 0x80:
#             # Control characters
#             if b < 0x20 or b == 0x7F:
#                 if display_ctrl_chars:
#                     out.append(CTRL_NAMES.get(b, f"<0x{b:02X}>"))
#                 else:
#                     out.append(f"<0x{b:02X}>")
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
#                 out.append(
#                     data[i : i + e.start].decode("utf-8", errors="strict")
#                 )
#                 i += e.start
#             else:
#                 out.append(f"<0x{b:02X}>")
#                 i += 1

#     return "".join(out)


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
                    out.append(CTRL_NAMES.get(b, f"\\x{b:02X}"))
                else:
                    out.append(chr(b))  # ← ПЛЕЙН ТЕКСТ
            else:
                out.append(chr(b))

            i += 1
            continue

        # ---------- UTF-8 ----------
        try:
            char = data[i:].decode("utf-8", errors="strict")
            out.append(char)
            break

        except UnicodeDecodeError as e:
            if e.start > 0:
                out.append(data[i : i + e.start].decode("utf-8", errors="strict"))
                i += e.start
            else:
                # реально битий байт
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
    "Baudrate": QSerialPort.BaudRate.Baud9600,
    "DataBits": QSerialPort.DataBits.Data8,
    "StopBits": QSerialPort.StopBits.OneStop,
    "Parity": QSerialPort.Parity.NoParity,
    "FlowControl": QSerialPort.FlowControl.NoFlowControl,
    "OpenMode": QSerialPort.OpenModeFlag.ReadWrite,
    "DisplayCtrlChars": False,
    "ShowTimeStamp": False,
    "Logfile": None,
    "LogfileAppendMode": False,
}


class QSerialPortModemAdapter(QObject):
    """QSerialPort adapter fro  ymodem.ModemSocket via queue"""

    def __init__(self, serial_port: QSerialPort):
        super().__init__()
        self.serial = serial_port
        self.logger = logging.getLogger("QSerialPortModemAdapter")
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
    progress = Signal(object)  # (index, filename, total, current)
    finished = Signal(bool)  # success
    error = Signal(str)  # error message
    log = Signal(str)  # log message
    started = Signal()  # transfer started

    def __init__(self, serial_port: QSerialPort):
        super().__init__()
        self.serial_port = serial_port
        self.adapter = None
        self.modem = None
        self._is_running = False
        self._is_cancelled = False

        self.timer = QTimer()
        self.timer.timeout.connect(self._process_chunk)
        self.timer.setInterval(0)

        self._transfer_iterator = None
        self._files_to_send = []
        self._save_directory = ""
        self._protocol = None
        self._options = []
        self._mode = None  # 'send' or 'receive'

    def is_running(self) -> bool:
        return self._is_running

    def put_to_queue(self, data: bytes):
        if self.adapter:
            self.adapter.read_queue.put(bytes(data))
        else:
            # raise RuntimeError("Adapter is not initialized")
            print("Adapter is not initialized")

    def send_files(self, files: List[str], protocol: int, options: List[str] = []):
        if self._is_running:
            self.error.emit("Transfer already running")
            return

        self._files_to_send = files
        self._protocol = protocol
        self._options = options
        self._mode = "send"
        self._is_cancelled = False
        self._is_running = True

        # Init Adapter
        self.adapter = QSerialPortModemAdapter(self.serial_port)
        self.adapter.clear_queue()

        self.log.emit(f"Transfer begin {len(files)} file(s)...")
        self.started.emit()

        QTimer.singleShot(100, self._start_transfer)

    def receive_files(
        self, save_directory: str, protocol: int, options: List[str] = []
    ):
        if self._is_running:
            self.error.emit("Transfer already running")
            return

        self._save_directory = save_directory
        self._protocol = protocol
        self._options = options
        self._mode = "receive"
        self._is_cancelled = False
        self._is_running = True

        # Init Adapter
        self.adapter = QSerialPortModemAdapter(self.serial_port)
        self.adapter.clear_queue()

        self.log.emit(f"Waiting for transfer begin, saving to: {save_directory}")
        self.started.emit()

        QTimer.singleShot(100, self._start_transfer)

    def cancel(self):
        self._is_cancelled = True
        self.log.emit("Transfer canceling...")

    def _start_transfer(self):
        try:
            from ymodem.Socket import ModemSocket

            self.modem = ModemSocket(
                read=self.adapter.read,
                write=self.adapter.write,
                protocol_type=self._protocol,
                protocol_type_options=self._options,
                packet_size=1024,
            )

            # Iterator for step by step processing
            self._last_progress = [0, "", 0, 0]

            def progress_callback(index: int, name: str, total: int, current: int):
                if self._is_cancelled:
                    raise Exception("Transfer canceled")
                self._last_progress = [index, name, total, current]
                self.progress.emit((index, name, total, current))
                # Process Qt Events
                QCoreApplication.processEvents()

            if self._mode == "send":
                success = self.modem.send(
                    self._files_to_send, callback=progress_callback
                )
            else:
                success = self.modem.recv(
                    self._save_directory, callback=progress_callback
                )

            # Finalization
            self._finish_transfer(success)

        except Exception as e:
            if "скасована" in str(e).lower() or "cancel" in str(e).lower():
                self.log.emit("⚠ Transfer canceled by user")
                self._finish_transfer(False)
            else:
                error_msg = f"Error: {str(e)}"
                self.log.emit(error_msg)
                self.error.emit(error_msg)
                self._finish_transfer(False)

    def _finish_transfer(self, success: bool):
        self._is_running = False

        if success and not self._is_cancelled:
            self.log.emit("✓ Transfer complete success")
        elif self._is_cancelled:
            self.log.emit("⚠ Transfer canceled by user")
        else:
            self.log.emit("✗ Transfer error")

        if self.adapter:
            self.adapter = None

        self.modem = None
        self.finished.emit(success and not self._is_cancelled)

    def _process_chunk(self):
        # Chunk processing not used here
        pass


class SerialManagerWidget(QWidget):
    REFRESH_INTERVAL_MS = 3000

    connection_state_changed = Signal(bool)
    data_received = Signal(bytes)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.ports: dict[str, QSerialPortInfo] = {}
        self.port: Optional[QSerialPort] = None

        # <<< Create QTimer
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._timer_refresh)
        # >>>

        self.label = QLabel("Device:")

        self.select = QComboBox(self)
        self.select.setStyleSheet("combobox-popup: 0;")
        self.select.setMaxVisibleItems(10)

        self.connect_btn = QPushButton("Connect")

        self._rts = QCheckBox("RTS")
        self._rts.setChecked(False)

        self._dtr = QCheckBox("DTR")
        self._dtr.setChecked(True)

        self._auto_reconnect = QCheckBox("Auto Reconnect")
        self._auto_reconnect.setChecked(False)
        self._auto_reconnect.setDisabled(True)

        self._settings_btn = QPushButton("Show Settings")

        self.settings = SettingsWidget(self)

        self.hlt = QHBoxLayout()
        self.hlt.setContentsMargins(0, 0, 0, 0)
        # self.hlt.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.hlt.addWidget(self.connect_btn)
        self.hlt.addWidget(self.label)
        self.hlt.addWidget(self.select)
        self.hlt.addWidget(self._rts)
        self.hlt.addWidget(self._dtr)
        self.hlt.addWidget(self._auto_reconnect)
        self.hlt.addStretch()
        self.hlt.addWidget(self._settings_btn)

        self.vlt = QVBoxLayout(self)
        self.vlt.setContentsMargins(0, 0, 0, 0)
        self.vlt.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.vlt.addWidget(self.settings)
        self.vlt.addLayout(self.hlt)

        self.refresh()
        self.connect_btn.clicked.connect(self.toggle_connect)
        self._settings_btn.clicked.connect(self.toggle_settings)

        self._rts.stateChanged.connect(self._update_rts)
        self._dtr.stateChanged.connect(self._update_dtr)

        # <<< Run timer on init
        self.refresh_timer.start(self.REFRESH_INTERVAL_MS)
        # >>>

    @property
    def rts(self):
        return self._rts.isChecked()

    @rts.setter
    def rts(self, value):
        self._rts.setChecked(value)

    @property
    def dtr(self):
        return self._dtr.isChecked()

    @dtr.setter
    def dtr(self, value):
        self._dtr.setChecked(value)

    @property
    def auto_reconnect(self):
        return self._auto_reconnect.isChecked()

    @auto_reconnect.setter
    def auto_reconnect(self, value):
        self._auto_reconnect.setChecked(value)

    def _update_rts(self, state: int):
        if self.port and self.port.isOpen():
            self.port.setRequestToSend(self._rts.isChecked())
        print(f"RTS: {'High' if state else 'Low'}")

    def _update_dtr(self, state: int):
        if self.port and self.port.isOpen():
            self.port.setDataTerminalReady(self._dtr.isChecked())
        print(f"DTR: {'High' if state else 'Low'}")

    def _timer_refresh(self):
        if not self.port or not self.port.isOpen():
            self.refresh()

    def refresh(self):
        """Updates the list of available serial ports."""
        current_port_text = self.select.currentText()

        self.ports = {p.portName(): p for p in QSerialPortInfo.availablePorts()}

        # Check if was changed
        new_port_names = sorted(self.ports.keys())
        current_combo_items = [
            self.select.itemText(i) for i in range(self.select.count())
        ]

        if new_port_names != current_combo_items:
            self.select.clear()
            self.select.addItems(new_port_names)  # Sort for better visual

            # Restore last selection
            if self.port and self.port.portName() in self.ports:
                self.select.setCurrentText(self.port.portName())
            elif current_port_text in self.ports:
                self.select.setCurrentText(current_port_text)
            elif self.ports:
                # Select the last port if the list is not empty
                self.select.setCurrentIndex(len(self.ports) - 1)

        self.select.setStyleSheet("combobox-popup: 0;")
        self.select.setMaxVisibleItems(10)

    def setConfigutrationEnabled(self, enabled: bool):
        self.select.setEnabled(enabled)
        self._settings_btn.setEnabled(enabled)

    def toggle_settings(self):
        self.settings.toggle()
        self._settings_btn.setText(
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

            port_name = self.select.currentText()

            if not port_name or port_name not in self.ports:
                QMessageBox.warning(self, "Error", "Please select a valid port.")
                return

            # 1. Create or reuse the QSerialPort object
            if not self.port:
                self.port = QSerialPort()

            # 2. Configure the port
            self.port.setPortName(port_name)
            self.port.setBaudRate(self.settings.baudrate)
            self.port.setDataBits(self.settings.data_bits)
            self.port.setParity(self.settings.parity)
            self.port.setStopBits(self.settings.stop_bits)
            self.port.setFlowControl(self.settings.flow_ctrl)

            # 3. Attempt to open the port
            # if self.port.open(QIODevice.OpenModeFlag.ReadWrite):
            if self.port.open(self.settings.open_mode):
                self.connect_btn.setText("Disconnect")
                self.setConfigutrationEnabled(False)
                self.connection_state_changed.emit(True)

                # <<< Stop timer
                self.refresh_timer.stop()
                # >>>

                self._update_rts(self._rts.isChecked())
                self._update_dtr(self._dtr.isChecked())

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
    logfile_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._logfile = QLineEdit(self)
        self._logfile.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._get_logfile = QPushButton("...")
        self._get_logfile.setFixedWidth(32)
        self._append = QCheckBox("Append")

        self.lt = QHBoxLayout(self)
        self.lt.setContentsMargins(0, 0, 0, 0)
        self.lt.addWidget(QLabel("Logfile:"))
        self.lt.addWidget(self._logfile)
        self.lt.addWidget(self._get_logfile)
        self.lt.addWidget(self._append)

        self._get_logfile.clicked.connect(self.on_get_log_file)
        self._logfile.textChanged.connect(self.logfile_changed.emit)

        initial_log_path = (Path("~").expanduser() / ".ymodterm.log").as_posix()
        self._logfile.setText(initial_log_path)

    @property
    def logfile(self) -> tuple[str, str]:
        file_mode = "a" if self._append.isChecked() else "w"
        return (self._logfile.text(), file_mode)

    def on_get_log_file(self):
        file_dialog = QFileDialog(self, "Save log file ...")
        file_dialog.setAcceptMode(QFileDialog.AcceptSave)
        file_dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        file_dialog.setOption(
            QFileDialog.Option.DontConfirmOverwrite, self._append.isChecked()
        )  # ⚡ вимикає стандартний попереджувальний діалог
        file_dialog.exec_()
        files = file_dialog.selectedFiles()
        if files and len(files):
            self._logfile.setText(files[0])


class SettingsWidget(QWidget):
    logfile_changed = Signal(str)
    display_ctrl_chars_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._baud = QComboBox(self)
        self._baud.setEditable(True)
        baud_validator = QIntValidator(0, 10000000, self)
        self._baud.lineEdit().setValidator(baud_validator)
        for k, v in QSerialPort.BaudRate.__members__.items():
            self._baud.addItem(str(v.value), userData=v)
        self._baud.setCurrentIndex(self._baud.findData(QSerialPort.BaudRate.Baud115200))

        self._data_bits = QComboBox(self)
        for k, v in QSerialPort.DataBits.__members__.items():
            self._data_bits.addItem(str(v.value), userData=v)
        self._data_bits.setCurrentIndex(
            self._data_bits.findData(QSerialPort.DataBits.Data8)
        )

        self._flow_ctrl = QComboBox(self)
        for k, v in {
            "None": QSerialPort.FlowControl.NoFlowControl,
            "Hardware": QSerialPort.FlowControl.HardwareControl,
            "Software": QSerialPort.FlowControl.SoftwareControl,
        }.items():
            self._flow_ctrl.addItem(k.replace("Flow", ""), userData=v)
        self._flow_ctrl.setCurrentIndex(
            self._flow_ctrl.findData(QSerialPort.FlowControl.NoFlowControl)
        )

        self._parity = QComboBox(self)
        for k, v in QSerialPort.Parity.__members__.items():
            _name = str(v.name).replace("Parity", "").replace("No", "None")
            self._parity.addItem(_name, userData=v)

        self._open_mode = QComboBox(self)
        for k, v in {
            "Read Only": QSerialPort.OpenModeFlag.ReadOnly,
            "WriteOnly": QSerialPort.OpenModeFlag.WriteOnly,
            "Read/Write": QSerialPort.OpenModeFlag.ReadWrite,
        }.items():
            self._open_mode.addItem(k, userData=v)
        self._open_mode.setCurrentIndex(
            self._open_mode.findData(QSerialPort.OpenModeFlag.ReadWrite)
        )

        self._stop_bits = QComboBox(self)
        for k, v in QSerialPort.StopBits.__members__.items():
            self._stop_bits.addItem(str(v.value), userData=v)

        self._display_ctrl_chars = QCheckBox("Display Ctrl Characters")

        self._show_timestamp = QCheckBox("Show Timestamp")
        self._show_timestamp.setDisabled(True)

        self._logfile = SelectLogFileWidget(self)

        self.grid = QGridLayout()
        self.grid.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.grid.addWidget(QLabel("Baudrate"), 0, 0)
        self.grid.addWidget(self._baud, 0, 1)
        self.grid.addWidget(QLabel("Data Bits"), 0, 2)
        self.grid.addWidget(self._data_bits, 0, 3)

        self.grid.addWidget(self._display_ctrl_chars, 0, 4)

        self.grid.addWidget(QLabel("Flow Control"), 1, 0)
        self.grid.addWidget(self._flow_ctrl, 1, 1)
        self.grid.addWidget(QLabel("Parity"), 1, 2)
        self.grid.addWidget(self._parity, 1, 3)

        self.grid.addWidget(self._show_timestamp, 1, 4)

        self.grid.addWidget(QLabel("Open Mode"), 2, 0)
        self.grid.addWidget(self._open_mode, 2, 1)
        self.grid.addWidget(QLabel("Stop Bits"), 2, 2)
        self.grid.addWidget(self._stop_bits, 2, 3)
        self.grid.addWidget(self._logfile, 2, 4, 1, 2)

        self.vlt = QVBoxLayout(self)
        self.vlt.setContentsMargins(0, 0, 0, 0)
        self.vlt.addLayout(self.grid)
        self.vlt.addWidget(HLineWidget(self))
        self.setVisible(False)

        self._logfile.logfile_changed.connect(self.logfile_changed.emit)
        self._display_ctrl_chars.checkStateChanged.connect(
            self.display_ctrl_chars_changed.emit
        )

    def toggle(self):
        self.setVisible(not self.isVisible())

    @property
    def baudrate(self) -> int:
        cur_data = self._baud.currentData()

        if (
            cur_data is not None
            and str(cur_data.value if hasattr(cur_data, "value") else cur_data)
            == self._baud.currentText()
        ):
            return cur_data

        try:
            return int(self._baud.currentText())
        except ValueError:
            return 0

    @baudrate.setter
    def baudrate(self, value):
        if (found := self._baud.findData(value)) >= 0:
            self._baud.setCurrentIndex(found)
        else:
            self._baud.setCurrentText(str(value))

    @property
    def data_bits(self):
        return self._data_bits.currentData()

    @data_bits.setter
    def data_bits(self, value):
        self._data_bits.setCurrentIndex(self._data_bits.findData(value))

    @property
    def parity(self):
        return self._parity.currentData()

    @parity.setter
    def parity(self, value):
        self._parity.setCurrentIndex(self._parity.findData(value))

    @property
    def stop_bits(self):
        return self._stop_bits.currentData()

    @stop_bits.setter
    def stop_bits(self, value):
        self._stop_bits.setCurrentIndex(self._stop_bits.findData(value))

    @property
    def open_mode(self):
        return self._open_mode.currentData()

    @open_mode.setter
    def open_mode(self, value):
        self._open_mode.setCurrentIndex(self._open_mode.findData(value))

    @property
    def flow_ctrl(self):
        return self._flow_ctrl.currentData()

    @flow_ctrl.setter
    def flow_ctrl(self, value):
        self._flow_ctrl.setCurrentIndex(self._flow_ctrl.findData(value))

    @property
    def display_ctrl_chars(self):
        return self._display_ctrl_chars.isChecked()

    @display_ctrl_chars.setter
    def display_ctrl_chars(self, value):
        self._display_ctrl_chars.setChecked(value)

    @property
    def show_timestamp(self):
        return self._show_timestamp.isChecked()

    @show_timestamp.setter
    def show_timestamp(self, value):
        self._show_timestamp.setChecked(value)

    @property
    def logfile(self):
        return self._logfile._logfile.text()

    @logfile.setter
    def logfile(self, text):
        self._logfile._logfile.setText(text)

    @property
    def log_file_append_mode(self):
        return self._logfile._append.isChecked()

    @log_file_append_mode.setter
    def log_file_append_mode(self, value):
        return self._logfile._append.setChecked(value)


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

        # 2. Ctrl + A-Z (БЕЗ Alt)
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

        # 5. Всі інші
        super().keyPressEvent(event)
        self._rebuild_char_map()

    def _insert_visual_char(self, actual_char: str, display_text: str):
        """Вставляє символ з візуальним відображенням."""
        cursor_pos = self.cursorPosition()
        current_text = self.text()

        new_text = current_text[:cursor_pos] + display_text + current_text[cursor_pos:]
        self.setText(new_text)
        self.setCursorPosition(cursor_pos + len(display_text))

        # Зберігаємо маппінг
        self._char_map[cursor_pos] = (actual_char, len(display_text))

    def _rebuild_char_map(self):
        """Перебудовує char_map після редагування."""
        # Складно відслідковувати зміни в QLineEdit, тому просто очищаємо
        # при звичайному вводі
        pass

    def get_actual_text(self) -> str:
        """Повертає текст з реальними керуючими символами."""
        if not self._char_map:
            return self.text()

        text = self.text()
        result = []
        i = 0

        while i < len(text):
            # Перевіряємо чи є маппінг
            if i in self._char_map:
                actual_char, length = self._char_map[i]
                result.append(actual_char)
                i += length
            else:
                result.append(text[i])
                i += 1

        return "".join(result)


class InputWidget(QWidget):
    send_clicked = Signal(object)
    send_file_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.edit = TerminalInput(self)
        self.edit.setPlaceholderText("Write to device")
        self._return_btn = QPushButton("Return ⏎")

        self._line_end = QComboBox(self)
        self._line_end.addItems(["LF", "CR", "CR/LF", "None", "Hex"])
        self._line_end.setCurrentText("CR")

        self._send_file_btn = QPushButton("Send File ...")

        self._protocol = QComboBox(self)
        self._protocol.addItems(["YModem", "YModem-G", "XModem"])
        self._protocol.setCurrentText("YModem")

        self._auto_return = QCheckBox("Auto ⏎")

        self.lt = QHBoxLayout(self)
        self.lt.setContentsMargins(0, 0, 0, 0)
        self.lt.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)
        self.lt.addWidget(self.edit)
        self.lt.addWidget(self._line_end)
        self.lt.addWidget(self._return_btn)
        self.lt.addWidget(self._auto_return)

        self.lt.addWidget(self._send_file_btn)
        self.lt.addWidget(self._protocol)

        self.edit.send_return.connect(self.on_return_clicked)
        self._return_btn.clicked.connect(self.on_return_clicked)
        self._send_file_btn.clicked.connect(self.on_send_file_clicked)
        self._auto_return.checkStateChanged.connect(self.toggle_auto_return)

        self._auto_return.setChecked(True)

    @property
    def protocol(self):
        return self._protocol.currentText()

    @protocol.setter
    def protocol(self, value):
        self._protocol.setCurrentIndex(self._protocol.findText(value))

    @property
    def auto_return(self):
        return self._auto_return.isChecked()

    @auto_return.setter
    def auto_return(self, value):
        self._auto_return.setChecked(value)

    @property
    def line_end(self):
        return self._line_end.currentText()

    @line_end.setter
    def line_end(self, value):
        self._line_end.setCurrentText(value)

    def toggle_auto_return(self, state: int):
        is_checked = state == Qt.CheckState.Checked
        self._return_btn.setDisabled(is_checked)
        try:
            if is_checked:
                self.edit.send_return.disconnect(self.on_return_clicked)
                self.edit.textChanged.connect(self.on_return_clicked)
            else:
                self.edit.send_return.connect(self.on_return_clicked)
                self.edit.textChanged.disconnect(self.on_return_clicked)
        except TypeError:
            pass

    def on_return_clicked(self):
        text = self.edit.text().strip()
        if not text:
            return

        selected_flag = self._line_end.currentText()
        self.send_clicked.emit((text, selected_flag))
        self.edit.clear()

    def on_send_file_clicked(self):
        file, _ = QFileDialog.getOpenFileName(self, "Open file ...")
        if not file:
            return

        options = []
        match self._protocol.currentText():
            case "YModem":
                protocol = ProtocolType.YMODEM
            case "YModem-G":
                protocol = ProtocolType.YMODEM
                options.append("g")
            case "XModem":
                protocol = ProtocolType.XMODEM
            case _:
                raise ValueError("Unsupported Transfer Protocol")

        self.send_file_selected.emit((file, protocol, options))


class HLineWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.HLine)
        self.setFrameShadow(QFrame.Sunken)


class OutputViewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.display_ctrl_chars = False

        self.text_view = QPlainTextEdit(self)
        self.text_view.setReadOnly(True)

        self._clear_button = QPushButton("Clear")
        self._hex_output = QCheckBox("Hex Output")
        self._log_to_file = QCheckBox("Logging to:")
        self._log_to_file.setDisabled(True)
        self._logfile = QLabel("")

        self.hlt = QHBoxLayout()
        self.hlt.setContentsMargins(0, 0, 0, 0)
        self.hlt.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.hlt.addWidget(self._clear_button)
        self.hlt.addWidget(self._hex_output)
        self.hlt.addWidget(self._log_to_file)
        self.hlt.addWidget(self._logfile)

        self.vlt = QVBoxLayout(self)
        self.vlt.setContentsMargins(0, 0, 0, 0)
        self.vlt.addWidget(self.text_view)
        self.vlt.addLayout(self.hlt)

        self._clear_button.clicked.connect(self.text_view.clear)

    @property
    def hex_output(self):
        return self._hex_output.isChecked()

    @hex_output.setter
    def hex_output(self, value):
        self._hex_output.setChecked(value)

    @property
    def log_to_file(self):
        return self._log_to_file.isChecked()

    @log_to_file.setter
    def log_to_file(self, value):
        self._log_to_file.setChecked(value)

    def insertPlainBytesOrStr(
        self, data: bytes | str, prefix: str = "", suffix: str = ""
    ):
        if isinstance(data, str):
            if self.hex_output:
                output_string = data.encode("utf-8").hex(" ").upper()
            else:
                output_string = data

        elif isinstance(data, bytes):
            output_string = decode_with_hex_fallback(
                data,
                hex_output=self.hex_output,
                display_ctrl_chars=self.display_ctrl_chars,
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
        if self._log_to_file.isChecked():
            QMessageBox.warning(
                self,
                "Error",
                "`insertToLogfile` not yet implemented, disable `Logging to:`",
            )
            raise NotImplementedError

    def setShowCtrlChars(self, enabled: int = 0):
        self.display_ctrl_chars = enabled == Qt.CheckState.Checked

    def setLogFile(self, text):
        self._logfile.setText(text)

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
    def __init__(self, parent=None):
        super().__init__(parent)

        self.serial_manager = SerialManagerWidget(self)

        # Worker for file send
        self.modem_manager = None
        self.progress_dialog = None

        self.input_history = InputHistory(self)
        self.output_view = OutputViewWidget(self)
        self.output_view.setLogFile(self.serial_manager.settings.logfile)
        self.output_view.setShowCtrlChars(
            self.serial_manager.settings.display_ctrl_chars
        )

        self.input_widget = InputWidget(self)

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
        self.serial_manager.settings.logfile_changed.connect(
            self.output_view.setLogFile
        )
        self.serial_manager.settings.display_ctrl_chars_changed.connect(
            self.output_view.setShowCtrlChars
        )

        self.input_widget.send_clicked.connect(self.on_send_clicked)
        self.input_widget.send_file_selected.connect(self.on_send_file_selected)

        self.on_connection_state_changed(False)  # force on init

    def on_connection_state_changed(self, state: bool):
        baud = self.serial_manager.settings.baudrate
        text = "Device: {port}\tConnection: {baud} @ {bits}-{parity}-{stop}".format(
            port=self.serial_manager.select.currentText(),
            baud=baud.value if hasattr(baud, "value") else baud,
            bits=self.serial_manager.settings.data_bits.value,
            parity=self.serial_manager.settings.parity.name[0],
            stop=self.serial_manager.settings.stop_bits.value,
        )
        self.status.setText(text)

    def on_send_clicked(self, data_obj):
        text, selected_flag = data_obj
        self.input_history.add(text)

        line_end = ""
        match selected_flag:
            case "LF":
                line_end = "\n"
            case "CR":
                line_end = "\r"
            case "CR/LF":
                line_end = "\r\n"
            case "Hex":
                pass
            case "None":
                pass
            case _:
                QMessageBox.warning(
                    self, "Error", f"Endline `{selected_flag}` yet not supported"
                )
                return
        if selected_flag == "Hex":
            try:
                data = parse_hex_string_to_bytes(text)
            except ValueError:
                QMessageBox.warning(self, "Error", "Invalid hex input")
                return
        else:
            data = (text + line_end).encode("utf-8")

        self.serial_manager.write(data)

    def on_data_received(self, data: bytes):
        self.output_view.insertPlainBytesOrStr(data)

    def on_send_file_selected(self, send_data):
        file, protocol, options = send_data

        if not self.serial_manager.port or not self.serial_manager.port.isOpen():
            from qtpy.QtWidgets import QMessageBox

            QMessageBox.warning(self, "Error", "Port is not opened!")
            return

        # Створюємо менеджер передачі
        self.modem_manager = ModemTransferManager(self.serial_manager.port)

        self.progress_dialog = QProgressDialog(
            "Prepare to transfer...", "Cancel", 0, 100, self
        )
        self.progress_dialog.setFixedWidth(400)
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
        """Progress bar updates"""

        index, filename, total, current = progress_

        if not self.progress_dialog:
            return

        if total > 0:
            progress = int((current / total) * 100)
            self.progress_dialog.setValue(progress)

            # Форматуємо розмір
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
        self.serial_manager.data_received.connect(self.modem_manager.put_to_queue)
        # self.serial_manager.data_received.disconnect(self.on_data_received)

    def _on_transfer_finished(self, success: bool):
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None

        self.serial_manager.data_received.disconnect(self.modem_manager.put_to_queue)
        self.modem_manager = None
        # self.serial_manager.data_received.connect(self.on_data_received)

        if success:
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Information)
            msg_box.setWindowTitle("Success")
            msg_box.setText("✓ File successfully transfered!")
            msg_box.show()
            
            QTimer.singleShot(2000, msg_box.close)
        else:
            QMessageBox.warning(self, "Error", "✗ Error occured during file transfer")

    def _on_transfer_error(self, error_msg: str):
        self.output_view.insertPlainBytesOrStr(f"{error_msg}", prefix="\n[ERROR] ", suffix="\n")
        self.modem_manager.cancel()

    def _on_transfer_log(self, log_msg: str):
        self.output_view.insertPlainBytesOrStr(f"{log_msg}", prefix="[YMODEM] ", suffix="\n")


class YModTermWindow(QMainWindow):
    """
    A simple main application window.
    """

    def __init__(self):
        super().__init__()

        self.settings = QSettings("o-murphy", "ymodterm")

        # 1. Configure the main window properties
        self.setWindowTitle("YModTerm")
        self.setGeometry(100, 100, 600, 400)  # x, y, width, height

        # 2. Create a central widget and layout
        # QMainWindow requires a central widget to host other UI elements
        central_widget = CentralWidget()
        self.setCentralWidget(central_widget)

        self.restore_settings()

    def restore_settings(self):
        central_widget: CentralWidget = self.centralWidget()

        # Serial manager
        manager_w = central_widget.serial_manager
        manager_w.rts = self.settings.value("RTS", _DEFAULTS.get("RTS", False), bool)
        manager_w.dtr = self.settings.value("DTR", _DEFAULTS.get("DTR", True), bool)
        manager_w.auto_reconnect = self.settings.value(
            "AutoReconnect", _DEFAULTS.get("AutoReconnect", False), bool
        )

        # Input
        input_w: InputWidget = central_widget.input_widget
        input_w.line_end = self.settings.value(
            "LineEnd", _DEFAULTS.get("LineEnd", "CR"), str
        )
        input_w.auto_return = self.settings.value(
            "AutoReturn", _DEFAULTS.get("AutoReturn", True), bool
        )
        input_w.protocol = self.settings.value(
            "ModemProtocol", _DEFAULTS.get("ModemProtocol", "YModem"), str
        )

        # Output
        output_w: OutputViewWidget = central_widget.output_view
        output_w.hex_output = self.settings.value(
            "HexOutput", _DEFAULTS.get("HexOutput", False), bool
        )
        output_w.log_to_file = self.settings.value(
            "LogToFile", _DEFAULTS.get("LogToFile", False), bool
        )

        # Settings
        settings_w: SettingsWidget = central_widget.serial_manager.settings
        settings_w.baudrate = self.settings.value(
            "Baudrate", _DEFAULTS.get("Baudrate", QSerialPort.BaudRate.Baud115200), int
        )
        settings_w.data_bits = self.settings.value(
            "DataBits", _DEFAULTS.get("DataBits", QSerialPort.DataBits.Data8), int
        )
        settings_w.stop_bits = self.settings.value(
            "StopBits", _DEFAULTS.get("StopBits", QSerialPort.StopBits.OneStop), int
        )
        settings_w.parity = self.settings.value(
            "Parity", _DEFAULTS.get("Parity", QSerialPort.Parity.NoParity), int
        )
        settings_w.flow_ctrl = self.settings.value(
            "FlowControl",
            _DEFAULTS.get("FlowControl", QSerialPort.FlowControl.NoFlowControl),
            int,
        )
        settings_w.open_mode = self.settings.value(
            "OpenMode",
            _DEFAULTS.get("OpenMode", QSerialPort.OpenModeFlag.ReadWrite),
            int,
        )
        settings_w.display_ctrl_chars = self.settings.value(
            "DisplayCtrlChars", _DEFAULTS.get("DisplayCtrlChars", False), bool
        )
        settings_w.show_timestamp = self.settings.value(
            "ShowTimeStamp", _DEFAULTS.get("ShowTimeStamp", False), bool
        )
        settings_w.logfile = self.settings.value(
            "Logfile", _DEFAULTS.get("Logfile", ""), str
        )
        settings_w.log_file_append_mode = self.settings.value(
            "LogfileAppendMode", _DEFAULTS.get("LogfileAppendMode", ""), bool
        )

    def save_settings(self):
        central_widget: CentralWidget = self.centralWidget()

        # Serial manager
        manager_w: SerialManagerWidget = central_widget.serial_manager
        self.settings.setValue("AutoReconnect", manager_w.auto_reconnect)
        self.settings.setValue("RTS", manager_w.rts)
        self.settings.setValue("DTR", manager_w.dtr)

        # Input
        input_w: InputWidget = central_widget.input_widget
        self.settings.setValue("LineEnd", input_w.line_end)
        self.settings.setValue("AutoReturn", input_w.auto_return)
        self.settings.setValue("ModemProtocol", input_w.protocol)

        # Output
        output_w: OutputViewWidget = central_widget.output_view
        self.settings.setValue("HexOutput", output_w.hex_output)
        self.settings.setValue("LogToFile", output_w.log_to_file)

        # Settings
        settings_w: SettingsWidget = central_widget.serial_manager.settings
        self.settings.setValue("Baudrate", settings_w.baudrate)
        self.settings.setValue("DataBits", settings_w.data_bits)
        self.settings.setValue("StopBits", settings_w.stop_bits)
        self.settings.setValue("Parity", settings_w.parity)
        self.settings.setValue("FlowControl", settings_w.flow_ctrl)
        self.settings.setValue("OpenMode", settings_w.open_mode)
        self.settings.setValue("DisplayCtrlChars", settings_w.display_ctrl_chars)
        self.settings.setValue("ShowTimeStamp", settings_w.show_timestamp)
        self.settings.setValue("Logfile", settings_w.logfile)
        self.settings.setValue("LogfileAppendMode", settings_w.log_file_append_mode)

        # Apply QSettings
        self.settings.sync()

    def closeEvent(self, event):
        self.save_settings()
        super().closeEvent(event)


class HTermApp(QApplication):
    def __init__(self, argv):
        super().__init__(argv)


def main():
    import sys
    import signal

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = HTermApp([])
    window = YModTermWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
