from typing import Optional

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
)
from qtpy.QtCore import Qt, QIODevice, QTimer, Signal
from qtpy.QtSerialPort import QSerialPort, QSerialPortInfo


def decode_with_hex_fallback(data: bytes) -> str:
    result = []
    i = 0

    while i < len(data):
        byte = data[i]

        # Single-byte UTF-8 (ASCII)
        if byte < 0x80:
            result.append(chr(byte))
            i += 1
            continue

        # Try to decode as UTF-8 multi-byte
        try:
            # визначити довжину UTF-8 послідовності
            if byte & 0xE0 == 0xC0:   # 2 bytes
                seq = data[i:i+2]
                char = seq.decode("utf-8")
                result.append(char)
                i += 2
                continue
            elif byte & 0xF0 == 0xE0: # 3 bytes
                seq = data[i:i+3]
                char = seq.decode("utf-8")
                result.append(char)
                i += 3
                continue
            elif byte & 0xF8 == 0xF0: # 4 bytes
                seq = data[i:i+4]
                char = seq.decode("utf-8")
                result.append(char)
                i += 4
                continue
            else:
                raise UnicodeDecodeError("utf-8", data, i, i + 1, "invalid start byte")

        except UnicodeDecodeError:
            # Invalid byte → show as <0xXX>
            result.append(f"<0x{byte:02X}>")
            i += 1

    return "".join(result)


class SerialManagerWidget(QWidget):
    REFRESH_INTERVAL_MS = 3000

    data_received = Signal(bytes)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.BAUD_RATE = QSerialPort.BaudRate.Baud115200
        self.DATA_BITS = QSerialPort.DataBits.Data8
        self.PARITY = QSerialPort.Parity.NoParity
        self.STOP_BITS = QSerialPort.StopBits.OneStop

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

        self.rts = QCheckBox("RTS")
        self.rts.setChecked(False)

        self.dtr = QCheckBox("DTR")
        self.dtr.setChecked(False)

        self.auto_reconnect = QCheckBox("Auto Reconnect")
        self.auto_reconnect.setChecked(False)

        self.settings_btn = QPushButton("Settings")

        self.lt = QHBoxLayout(self)
        self.lt.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.lt.addWidget(self.connect_btn)
        self.lt.addWidget(self.label)
        self.lt.addWidget(self.select)
        self.lt.addWidget(self.rts)
        self.lt.addWidget(self.dtr)
        self.lt.addWidget(self.auto_reconnect)
        self.lt.addStretch()
        self.lt.addWidget(self.settings_btn)

        self.refresh()
        self.connect_btn.clicked.connect(self.toggle_connect)

        # <<< Run timer on init
        self.refresh_timer.start(self.REFRESH_INTERVAL_MS)
        # >>>

    def _timer_refresh(self):
        """Викликається таймером для оновлення списку портів, якщо порт не відкритий."""
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

    def toggle_connect(self):
        """Opens or closes the serial port."""
        if self.port and self.port.isOpen():
            # === DISCONNECT Mode ===
            self.port.close()
            # We keep the QSerialPort object, but its state is "closed"
            self.connect_btn.setText("Connect")
            self.select.setEnabled(True)  # Allow port selection

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
            self.port.setBaudRate(self.BAUD_RATE)
            self.port.setDataBits(self.DATA_BITS)
            self.port.setParity(self.PARITY)
            self.port.setStopBits(self.STOP_BITS)

            # 3. Attempt to open the port
            if self.port.open(QIODevice.OpenModeFlag.ReadWrite):
                self.connect_btn.setText("Disconnect")
                self.select.setEnabled(False)  # Block port selection during connection

                # <<< Stop timer
                self.refresh_timer.stop()
                # >>>

                # The readyRead signal can be connected here to read data!
                self.port.readyRead.connect(self.on_ready_read)
            else:
                # Open error handling
                error_msg = (
                    f"Failed to open port {port_name}: {self.port.errorString()}"
                )
                QMessageBox.critical(self, "Connection Error", error_msg)

    def on_ready_read(self, *args):
        data = self.port.readAll().data()
        self.data_received.emit(data)


class SettingsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.lt = QGridLayout(self)
        self.setFixedHeight(100)
        self.setVisible(False)

    def toggle(self):
        self.setVisible(not self.isVisible())


class TextInputWidget(QWidget):
    send_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.edit = QLineEdit(self)
        self.return_btn = QPushButton("Return")

        self.flags = QComboBox(self)
        self.flags.addItems(["LF", "CR", "CR/LF", "None", "Hex"])
        self.flags.setCurrentText("None")

        self.send_file_btn = QPushButton("Send file")

        self.protocol = QComboBox(self)
        self.protocol.addItems(["YModem", "YModem-G", "XModem"])
        self.protocol.setCurrentText("YModem")

        self.auto = QCheckBox("Auto return")

        self.lt = QHBoxLayout(self)
        self.lt.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)
        self.lt.addWidget(self.edit)
        self.lt.addWidget(self.flags)
        self.lt.addWidget(self.auto)
        self.lt.addWidget(self.return_btn)

        self.lt.addWidget(self.send_file_btn)
        self.lt.addWidget(self.protocol)

        self.return_btn.clicked.connect(self.on_click)
        self.auto.checkStateChanged.connect(self.auto_changed)
        self.edit.returnPressed.connect(self.on_click)
        
        self.auto.setChecked(True)

    def auto_changed(self, state: int):
        if state == Qt.CheckState.Checked:
            try:
                self.edit.returnPressed.disconnect(self.on_click)
                self.edit.textChanged.connect(self.on_click)
            except TypeError:
                pass
        else:
            try:
                self.edit.returnPressed.connect(self.on_click)
                self.edit.textChanged.disconnect(self.on_click)
            except TypeError:
                pass
        
    def on_click(self):
        text = self.edit.text().strip()
        print(text.encode("utf-8"))

        if not text:
            return

        selected_flag = self.flags.currentText()
        
        if selected_flag == "LF":
            text += "\n"
        elif selected_flag == "CR":
            text += "\r"
        elif selected_flag == "CR/LF":
            text += "\r\n"
                
        self.send_clicked.emit(text)
        self.edit.clear()


class CentralWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.settings = SettingsWidget(self)

        self.serial_manager = SerialManagerWidget(self)
        
        self.text_view = QPlainTextEdit(self)
        self.text_view.setReadOnly(True)

        self.text_input = TextInputWidget(self)

        self.lt = QVBoxLayout(self)
        self.lt.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.lt.addWidget(self.settings)
        self.lt.addWidget(self.serial_manager)
        self.lt.addWidget(self.text_view)
        self.lt.addWidget(self.text_input)

        self.text_input.send_clicked.connect(self.on_send_clicked)
        self.serial_manager.settings_btn.clicked.connect(self.toggle_settings)
        self.serial_manager.data_received.connect(self.on_data_received)

    def toggle_settings(self, text):
        self.settings.toggle()

    def on_send_clicked(self, text):
        self.text_view.insertPlainText(text)

    def on_data_received(self, data: bytes):
        string_data = decode_with_hex_fallback(data)
        self.text_view.insertPlainText(string_data)


class HTermWindow(QMainWindow):
    """
    A simple main application window.
    """

    def __init__(self):
        super().__init__()

        # 1. Configure the main window properties
        self.setWindowTitle("HTerm")
        self.setGeometry(100, 100, 600, 400)  # x, y, width, height

        # 2. Create a central widget and layout
        # QMainWindow requires a central widget to host other UI elements
        central_widget = CentralWidget()
        self.setCentralWidget(central_widget)


class HTermApp(QApplication):
    def __init__(self, argv):
        super().__init__(argv)


def main():
    import sys
    import signal

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = HTermApp([])
    window = HTermWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
