import socket
import time
import threading
from enum import Enum
from abc import ABC, abstractmethod
import random
from helpers import *
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - HTTP-Server - %(message)s')

class ConnectionState(Enum):
    CLOSED = 0
    LISTEN = 1
    SYN_SENT = 2
    SYN_RECEIVED = 3
    ESTABLISHED = 4
    FIN_WAIT_1 = 5
    FIN_WAIT_2 = 6
    CLOSE_WAIT = 7
    CLOSING = 8
    LAST_ACK = 9
    TIME_WAIT = 10


class State(ABC):
    @abstractmethod
    def handle_packet(self, connection, packet):
        pass

    @abstractmethod
    def handle_timeout(self, connection):
        pass

    @abstractmethod
    def enter(self, connection, prev_state=None):
        pass

    @abstractmethod
    def exit(self, connection, next_state=None):
        pass


class ClosedState(State):
    def handle_timeout(self, connection):
        connection.log("Timeout in CLOSED state. No action taken.")
        return False

    def handle_packet(self, connection, packet):
        seq_num, ack_num, flags, data = unpack_packet(packet)
        if flags & SYN_FLAG:
            connection.log(f"Received SYN in CLOSED state. Sending RST.")
            connection._send_packet(0, seq_num + 1, RST_FLAG)

    def enter(self, connection, prev_state=None):
        connection.log("Entering CLOSED state")
        connection.running = False
        if connection.udp_socket:
            try:
                connection.udp_socket.close()
            except:
                pass

        if connection.receiver_thread and connection.receiver_thread.is_alive():
            connection.receiver_thread.join(0.1)

    def exit(self, connection, next_state=None):
        connection.log(f"Exiting CLOSED state to {next_state}")
        if next_state == ConnectionState.SYN_SENT or next_state == ConnectionState.LISTEN:
            connection.seq_num = random.randint(0, 2 ** 32 - 1)
            if not connection.udp_socket or connection.udp_socket._closed:
                connection.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                connection.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if connection.local_addr:
                    connection.udp_socket.bind(connection.local_addr)


class ListenState(State):
    def handle_timeout(self, connection):
        connection.log("Timeout in LISTEN state. No action taken.")
        return False

    def handle_packet(self, connection, packet):
        seq_num, ack_num, flags, data = unpack_packet(packet)
        if flags & SYN_FLAG:
            connection.remote_addr = connection.last_addr
            connection.ack_num = seq_num + 1
            connection._send_packet(connection.seq_num, connection.ack_num, SYNACK_FLAG)
            connection.set_state(ConnectionState.SYN_RECEIVED)
            return True
        return False

    def enter(self, connection, prev_state=None):
        connection.log("Entering LISTEN state")
        connection.is_server = True
        connection.running = True
        if not connection.receiver_thread or not connection.receiver_thread.is_alive():
            connection.receiver_thread = threading.Thread(target=connection._receiver_loop)
            connection.receiver_thread.daemon = True
            connection.receiver_thread.start()

    def exit(self, connection, next_state=None):
        connection.log(f"Exiting LISTEN state to {next_state}")


class SynSentState(State):
    def handle_packet(self, connection, packet):
        seq_num, ack_num, flags, data = unpack_packet(packet)
        if (flags & SYNACK_FLAG) == SYNACK_FLAG and ack_num == connection.seq_num + 1:
            connection.ack_num = seq_num + 1
            connection.seq_num = ack_num
            connection._send_packet(connection.seq_num, connection.ack_num, ACK_FLAG)
            connection.set_state(ConnectionState.ESTABLISHED)
            connection.log("Connection established (client)")
            return True

        elif flags & SYN_FLAG:
            connection.ack_num = seq_num + 1
            connection._send_packet(connection.seq_num, connection.ack_num, SYNACK_FLAG)
            connection.set_state(ConnectionState.SYN_RECEIVED)
            return True

        return False

    def handle_timeout(self, connection):
        connection.retries += 1
        if connection.retries > MAX_RETRIES:
            connection.log("Max retries exceeded in SYN_SENT. Returning to CLOSED.")
            connection.set_state(ConnectionState.CLOSED)
            return False
        connection.log(f"Timeout in SYN_SENT. Retransmitting SYN. Retry {connection.retries}/{MAX_RETRIES}")
        connection._send_packet(connection.seq_num, 0, SYN_FLAG)
        connection.start_timer()

    def enter(self, connection, prev_state=None):
        connection.log("Entering SYN_SENT state")
        connection.retries = 0
        connection.is_server = False
        connection._send_packet(connection.seq_num, 0, SYN_FLAG)
        connection.start_timer()

    def exit(self, connection, next_state=None):
        connection.log(f"Exiting SYN_SENT state to {next_state}")
        connection.stop_timer()


class SynReceivedState(State):
    def handle_packet(self, connection, packet):
        seq_num, ack_num, flags, data = unpack_packet(packet)

        if flags & ACK_FLAG and ack_num == connection.seq_num + 1:
            connection.seq_num = ack_num
            connection.set_state(ConnectionState.ESTABLISHED)
            connection.log("Connection established (server)")
            return True

        elif flags & SYN_FLAG:
            connection.ack_num = seq_num + 1
            connection._send_packet(connection.seq_num, connection.ack_num, SYNACK_FLAG)
            return True

        return False

    def handle_timeout(self, connection):
        connection.retries += 1
        if connection.retries > MAX_RETRIES:
            connection.log("Max retries exceeded in SYN_RECEIVED. Returning to LISTEN.")
            connection.remote_addr = None
            connection.set_state(ConnectionState.LISTEN)
            return False
        connection.log(f"Timeout in SYN_RECEIVED. Retransmitting SYN-ACK. Retry {connection.retries}/{MAX_RETRIES}")
        connection._send_packet(connection.seq_num, connection.ack_num, SYNACK_FLAG)
        connection.start_timer()
        return True

    def enter(self, connection, prev_state=None):
        connection.log("Entering SYN_RECEIVED state")
        connection.retries = 0
        connection.start_timer()

    def exit(self, connection, next_state=None):
        connection.log(f"Exiting SYN_RECEIVED state to {next_state}")
        connection.stop_timer()


class EstablishedState(State):
    def handle_timeout(self, connection):
        connection.log("Timeout in ESTABLISHED state. No action taken.")
        return False

    def handle_packet(self, connection, packet):
        seq_num, ack_num, flags, data = unpack_packet(packet)
        if len(data) > 0 and seq_num == connection.ack_num:
            connection.recv_buffer += data
            connection.ack_num = seq_num + len(data)
            connection._send_packet(connection.seq_num, connection.ack_num, ACK_FLAG)
            connection.log(f"Received {len(data)} bytes of data. ACK sent.")
            return True

        elif flags & ACK_FLAG and ack_num > connection.seq_num:
            acknowledged_bytes = ack_num - connection.seq_num
            connection.log(f"Received ACK for {acknowledged_bytes} bytes")
            connection.seq_num = ack_num
            return True

        elif flags & FIN_FLAG:
            connection.ack_num = seq_num + 1
            connection._send_packet(connection.seq_num, connection.ack_num, ACK_FLAG)
            connection.log("Received FIN. Sending ACK and moving to CLOSE_WAIT")
            connection.set_state(ConnectionState.CLOSE_WAIT)
            return True

        return False

    def enter(self, connection, prev_state=None):
        connection.log("Entering ESTABLISHED state")

    def exit(self, connection, next_state=None):
        connection.log(f"Exiting ESTABLISHED state to {next_state}")


class FinWait1State(State):
    def handle_packet(self, connection, packet):
        seq_num, ack_num, flags, data = unpack_packet(packet)
        if flags & ACK_FLAG and ack_num == connection.seq_num + 1:
            connection.log("Received ACK for our FIN")
            connection.seq_num = ack_num

            if flags & FIN_FLAG:
                connection.ack_num = seq_num + 1
                connection._send_packet(connection.seq_num, connection.ack_num, ACK_FLAG)
                connection.log("Simultaneous close detected. Moving to TIME_WAIT")
                connection.set_state(ConnectionState.TIME_WAIT)
            else:
                connection.set_state(ConnectionState.FIN_WAIT_2)
            return True

        elif flags & FIN_FLAG:
            connection.ack_num = seq_num + 1
            connection._send_packet(connection.seq_num, connection.ack_num, ACK_FLAG)
            connection.set_state(ConnectionState.CLOSING)
            connection.log("Received FIN (simultaneous close). Moving to CLOSING")
            return True

        return False

    def handle_timeout(self, connection):
        connection.retries += 1
        if connection.retries > MAX_RETRIES:
            connection.log("Max retries exceeded in FIN_WAIT_1. Forcing close.")
            connection.set_state(ConnectionState.CLOSED)
            return False

        connection.log(f"Timeout in FIN_WAIT_1. Retransmitting FIN. Retry {connection.retries}/{MAX_RETRIES}")
        connection._send_packet(connection.seq_num, connection.ack_num, FIN_FLAG)
        connection.start_timer()
        return True

    def enter(self, connection, prev_state=None):
        connection.log("Entering FIN_WAIT_1 state")
        connection.retries = 0
        connection._send_packet(connection.seq_num, connection.ack_num, FIN_FLAG)
        connection.start_timer()

    def exit(self, connection, next_state=None):
        connection.log(f"Exiting FIN_WAIT_1 state to {next_state}")
        connection.stop_timer()


class FinWait2State(State):
    def handle_packet(self, connection, packet):
        seq_num, ack_num, flags, data = unpack_packet(packet)
        if flags & FIN_FLAG:
            connection.ack_num = seq_num + 1
            connection._send_packet(connection.seq_num, connection.ack_num, ACK_FLAG)
            connection.log("Received FIN in FIN_WAIT_2. Moving to TIME_WAIT")
            connection.set_state(ConnectionState.TIME_WAIT)
            return True

        return False

    def handle_timeout(self, connection):
        connection.retries += 1
        if connection.retries > MAX_RETRIES:
            connection.log("Max time in FIN_WAIT_2. Forcing close.")
            connection.set_state(ConnectionState.CLOSED)
            return False
        return True

    def enter(self, connection, prev_state=None):
        connection.log("Entering FIN_WAIT_2 state")
        connection.retries = 0
        connection.start_timer()

    def exit(self, connection, next_state=None):
        connection.log(f"Exiting FIN_WAIT_2 state to {next_state}")
        connection.stop_timer()


class CloseWaitState(State):
    def handle_packet(self, connection, packet):
        return False

    def handle_timeout(self, connection):
        return False

    def enter(self, connection, prev_state=None):
        connection.log("Entering CLOSE_WAIT state")

    def exit(self, connection, next_state=None):
        connection.log(f"Exiting CLOSE_WAIT state to {next_state}")


class ClosingState(State):
    def handle_packet(self, connection, packet):
        seq_num, ack_num, flags, data = unpack_packet(packet)
        if flags & ACK_FLAG and ack_num == connection.seq_num + 1:
            connection.log("Received ACK for our FIN in CLOSING. Moving to TIME_WAIT")
            connection.seq_num = ack_num
            connection.set_state(ConnectionState.TIME_WAIT)
            return True
        return False

    def handle_timeout(self, connection):
        connection.retries += 1
        if connection.retries > MAX_RETRIES:
            connection.log("Max retries exceeded in CLOSING. Forcing close.")
            connection.set_state(ConnectionState.CLOSED)
            return False
        connection.log(f"Timeout in CLOSING. Retransmitting FIN. Retry {connection.retries}/{MAX_RETRIES}")
        connection._send_packet(connection.seq_num, connection.ack_num, FIN_FLAG)
        connection.start_timer()
        return True

    def enter(self, connection, prev_state=None):
        connection.log("Entering CLOSING state")
        connection.retries = 0
        connection.start_timer()

    def exit(self, connection, next_state=None):
        connection.log(f"Exiting CLOSING state to {next_state}")
        connection.stop_timer()


class LastAckState(State):
    def handle_packet(self, connection, packet):
        seq_num, ack_num, flags, data = unpack_packet(packet)
        if flags & ACK_FLAG and ack_num == connection.seq_num + 1:
            connection.log("Received ACK for our FIN in LAST_ACK. Connection closed.")
            connection.seq_num = ack_num
            connection.set_state(ConnectionState.CLOSED)
            return True
        return False

    def handle_timeout(self, connection):
        connection.retries += 1
        if connection.retries > MAX_RETRIES:
            connection.log("Max retries exceeded in LAST_ACK. Forcing close.")
            connection.set_state(ConnectionState.CLOSED)
            return False
        connection.log(f"Timeout in LAST_ACK. Retransmitting FIN. Retry {connection.retries}/{MAX_RETRIES}")
        connection._send_packet(connection.seq_num, connection.ack_num, FIN_FLAG)
        connection.start_timer()
        return True

    def enter(self, connection, prev_state=None):
        connection.log("Entering LAST_ACK state")
        connection.retries = 0
        connection._send_packet(connection.seq_num, connection.ack_num, FIN_FLAG)
        connection.start_timer()

    def exit(self, connection, next_state=None):
        connection.log(f"Exiting LAST_ACK state to {next_state}")
        connection.stop_timer()


class TimeWaitState(State):
    def handle_packet(self, connection, packet):
        seq_num, ack_num, flags, data = unpack_packet(packet)
        if flags & FIN_FLAG:
            connection.log("Received duplicate FIN in TIME_WAIT. Resending ACK.")
            connection._send_packet(connection.seq_num, connection.ack_num, ACK_FLAG)
            connection.start_timer()
            return True
        return False

    def handle_timeout(self, connection):
        connection.log("TIME_WAIT completed. Moving to CLOSED.")
        connection.set_state(ConnectionState.CLOSED)
        return True

    def enter(self, connection, prev_state=None):
        connection.log("Entering TIME_WAIT state")
        connection.timeout = TIMEOUT * 2
        connection.start_timer()

    def exit(self, connection, next_state=None):
        connection.log(f"Exiting TIME_WAIT state to {next_state}")
        connection.stop_timer()


class RUDPSocket:
    def __init__(self, debug=False, loss_rate=0.0, corruption_rate=0.0):
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.local_addr = None
        self.remote_addr = None
        self.last_addr = None
        self.state = ClosedState()
        self.current_state = ConnectionState.CLOSED
        self.states = {
            ConnectionState.CLOSED: ClosedState(),
            ConnectionState.LISTEN: ListenState(),
            ConnectionState.SYN_SENT: SynSentState(),
            ConnectionState.SYN_RECEIVED: SynReceivedState(),
            ConnectionState.ESTABLISHED: EstablishedState(),
            ConnectionState.FIN_WAIT_1: FinWait1State(),
            ConnectionState.FIN_WAIT_2: FinWait2State(),
            ConnectionState.CLOSE_WAIT: CloseWaitState(),
            ConnectionState.CLOSING: ClosingState(),
            ConnectionState.LAST_ACK: LastAckState(),
            ConnectionState.TIME_WAIT: TimeWaitState(),
        }
        self.seq_num = 0
        self.ack_num = 0
        self.send_buffer = b''
        self.recv_buffer = b''
        self.timer = None
        self.timeout = TIMEOUT
        self.retries = 0
        self.is_server = False
        self.debug = debug
        self.loss_rate = loss_rate
        self.corruption_rate = corruption_rate
        self.receiver_thread = None
        self.running = False
        self.connection_queue = []

    def set_state(self, new_state):
        if new_state == self.current_state:
            return
        old_state = self.current_state
        self.state.exit(self, new_state)
        self.state = self.states[new_state]
        self.current_state = new_state
        self.state.enter(self, old_state)

    def bind(self, address):
        try:
            self.udp_socket.close()
        except:
            pass

        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_socket.bind(address)
        self.local_addr = address
        self.log(f"Socket bound to {address}")

    def listen(self):
        self.set_state(ConnectionState.LISTEN)
        self.log("Listening for incoming connections")

    def accept(self):
        while self.current_state != ConnectionState.ESTABLISHED:
            time.sleep(0.1)
            if self.current_state == ConnectionState.CLOSED:
                raise ConnectionError("Connection closed while waiting for accept")
        return self, self.remote_addr

    def connect(self, address):
        self.remote_addr = address
        self.set_state(ConnectionState.SYN_SENT)
        while self.current_state not in (ConnectionState.ESTABLISHED, ConnectionState.CLOSED):
            time.sleep(0.1)
        if self.current_state == ConnectionState.CLOSED:
            raise ConnectionError("Failed to establish connection")
        return True

    def send(self, data):
        if self.current_state != ConnectionState.ESTABLISHED:
            raise ConnectionError("Cannot send data: connection not established")
        if not isinstance(data, bytes):
            data = str(data).encode()
        data_len = len(data)
        self.send_buffer += data

        while self.send_buffer:
            chunk_size = min(len(self.send_buffer), MSS)
            chunk = self.send_buffer[:chunk_size]
            retries = 0
            sent = False

            while not sent and retries < MAX_RETRIES:
                self._send_packet(self.seq_num, self.ack_num, ACK_FLAG, chunk)
                ack_wait_time = 0
                start_seq = self.seq_num

                while ack_wait_time < self.timeout:
                    if self.current_state != ConnectionState.ESTABLISHED:
                        raise ConnectionError("Connection lost while sending data")
                    if self.seq_num > start_seq:
                        sent = True
                        break
                    time.sleep(0.1)
                    ack_wait_time += 0.1

                if not sent:
                    retries += 1

            if retries >= MAX_RETRIES:
                raise ConnectionError("Failed to send data: max retries exceeded")

            self.send_buffer = self.send_buffer[chunk_size:]

        return data_len

    def recv(self, buffer_size=MSS):
        if self.current_state != ConnectionState.ESTABLISHED:
            raise ConnectionError("Cannot receive data: connection not established")

        wait_time = 0
        while not self.recv_buffer and wait_time < TIMEOUT * 3:
            time.sleep(0.1)
            wait_time += 0.1
            if self.current_state != ConnectionState.ESTABLISHED:
                break

        data = self.recv_buffer[:buffer_size]
        self.recv_buffer = self.recv_buffer[buffer_size:]
        return data

    def close(self):
        if self.current_state == ConnectionState.CLOSED:
            return

        if self.current_state == ConnectionState.ESTABLISHED:
            self.set_state(ConnectionState.FIN_WAIT_1)
        elif self.current_state == ConnectionState.CLOSE_WAIT:
            self.set_state(ConnectionState.LAST_ACK)

        while self.current_state != ConnectionState.CLOSED:
            time.sleep(0.1)

    def abort(self):
        if self.current_state != ConnectionState.CLOSED:
            self.log("Aborting connection")
            self.set_state(ConnectionState.CLOSED)

    def start_timer(self):
        self.stop_timer()
        self.timer = threading.Timer(self.timeout, self.handle_timeout)
        self.timer.daemon = True
        self.timer.start()

    def stop_timer(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None

    def handle_timeout(self):
        if self.current_state != ConnectionState.CLOSED:
            self.state.handle_timeout(self)

    def _simulate_packet_loss(self):
        return random.random() < self.loss_rate

    def _simulate_packet_corruption(self, packet):
        if random.random() < self.corruption_rate:
            pos = random.randint(0, len(packet) - 1)
            corrupted_byte = (packet[pos] + random.randint(1, 255)) % 256
            return packet[:pos] + bytes([corrupted_byte]) + packet[pos + 1:]
        return packet

    def _receiver_loop(self):
        self.udp_socket.settimeout(0.1)
        while self.running:
            try:
                packet, addr = self.udp_socket.recvfrom(BUFFER_SIZE)

                if self._simulate_packet_loss():
                    self.log(f"Simulating packet loss from {addr}")
                    continue

                packet = self._simulate_packet_corruption(packet)

                self.log(f"Received packet from {addr}")
                self.last_addr = addr

                if (self.remote_addr is None or addr == self.remote_addr or
                        self.current_state == ConnectionState.LISTEN):
                    self.state.handle_packet(self, packet)

            except socket.timeout:
                continue
            except Exception as e:
                self.log(f"Error in receiver loop: {e}")
                if self.current_state != ConnectionState.CLOSED:
                    self.set_state(ConnectionState.CLOSED)
                break

    def _send_packet(self, seq_num, ack_num, flags, data=b''):
        if self._simulate_packet_loss():
            self.log(f"Simulating outgoing packet loss: SEQ={seq_num}, Flags={flags}")
            return

        packet = pack_packet(seq_num, ack_num, flags, data)

        packet = self._simulate_packet_corruption(packet)

        if self.remote_addr:
            self.udp_socket.sendto(packet, self.remote_addr)
            flag_str = ""
            if flags & SYN_FLAG: flag_str += "SYN "
            if flags & ACK_FLAG: flag_str += "ACK "
            if flags & FIN_FLAG: flag_str += "FIN "
            if flags & RST_FLAG: flag_str += "RST "
            self.log(f"Sent packet: SEQ={seq_num}, ACK={ack_num}, Flags={flag_str}, Data={len(data)} bytes")

    def log(self, message):
        if self.debug:
            logging.info(f"RUDPSocket: {message}")


# main
if __name__ == "__main__":
    Sock = RUDPSocket()
    Sock.bind(('localhost', 8080))
    pass

