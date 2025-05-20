import asyncio
import heapq
import random
import socket as skt
import struct
import threading
from enum import Enum
from typing import Callable

from utils import pack_packet, unpack_packet, log, flags2str

# Constants for flags, sizes, etc.
BUFFER_SIZE = 4096
MAX_SEG_SIZE = 1024
RUDP_HEADER_FORMAT = '!IIBHH'
RUDP_HEADER_SIZE = struct.calcsize(RUDP_HEADER_FORMAT)
TIMEOUT = 1.0
MAX_RETRIES = 5
INFLIGHT_COUNT = 10

LOSS_RATE = 0.05
CORRUPTION_RATE = 0.0

SYN = 0x1
ACK = 0x2
SYN_ACK = SYN | ACK
FIN = 0x4
RST = 0x8


# Connection States (simplified)
class ConState(Enum):
    CLOSED = 0
    LISTEN = 1
    SYN_SENT = 2
    SYN_RECEIVED = 3
    ESTABLISHED = 4
    FIN_WAIT_1 = 5
    FIN_WAIT_2 = 6
    CLOSE_WAIT = 7
    LAST_ACK = 8
    TIME_WAIT = 9
    CLOSING = 10

    def __str__(self):
        return self.name


class Connection:
    def __init__(self, address=None, sender_callback: Callable = print):
        self.remote_addr = address
        self._state = ConState.LISTEN
        self.seq_num = 0
        self.ack_num = 0
        self.inflight_last_seq_num = 0

        # Min heap to store the seq_num of out-of-order packets
        self.out_of_order_heap = []
        heapq.heapify(self.out_of_order_heap)

        self.send_buffer = b''
        self.recv_buffer = b''

        self.sender_callback = sender_callback

        self.timer = None
        self.retries = 0

    async def connect(self, address):
        self.remote_addr = address
        self.state(ConState.SYN_SENT)

        while self.state() != ConState.ESTABLISHED:
            if self.state() == ConState.CLOSED:
                raise ConnectionError("Connection closed")
            await asyncio.sleep(0.01)

    async def send(self, data):
        if self.state() != ConState.ESTABLISHED:
            raise ConnectionError("Connection closed")

        if not isinstance(data, bytes):
            data = str(data).encode()

        self.send_buffer += data

        self._start_timer(TIMEOUT)
        self._send_burst()

        while self.send_buffer:
            await asyncio.sleep(0.01)

    def recv(self):
        data = self.recv_buffer
        self.recv_buffer = b''
        return data

    def data_available(self):
        return len(self.recv_buffer)

    def close(self):
        match self.state():
            case ConState.CLOSED:
                pass
            case ConState.LISTEN:
                self.state(ConState.CLOSED)
            case ConState.SYN_SENT:
                self._send_packet(RST)
                self.state(ConState.CLOSED)
            case ConState.CLOSE_WAIT:
                self.seq_num += 1
                self.state(ConState.LAST_ACK)
            case ConState.ESTABLISHED:
                self.state(ConState.FIN_WAIT_1)

    def handle_packet(self, packet, address):
        seq_num, ack_num, flags, data = unpack_packet(packet)

        log(f"Received packet: SEQ={seq_num}, ACK={ack_num}, [Flags={flags2str(flags)}]"
            f", Data={len(data)} bytes")

        if flags & RST:
            self._stop_timer()
            self.state(ConState.LISTEN)

        syn = flags & SYN
        ack = flags & ACK
        fin = flags & FIN
        rst = flags & RST

        if ack and ack_num > self.seq_num + 1 and self.state() != ConState.ESTABLISHED:
            log(f"Received ACK for {ack_num} but expected {self.seq_num + 1}.")
            return

        if seq_num < self.ack_num:
            log(f"Received out-of-order packet: SEQ={seq_num}, expected {self.ack_num}")
            return

        # reset retries if ack_num is correct
        if ack_num == self.seq_num + 1:
            self.retries = 0

        match self.state():
            case ConState.CLOSED:
                log(f"Received packet in CLOSED state. Ignoring.")
                return

            case ConState.SYN_SENT if syn and ack:
                self.ack_num = seq_num + 1
                self.seq_num += 1
                self.state(ConState.ESTABLISHED)

            case ConState.LISTEN \
                 | ConState.SYN_SENT if syn:
                self.remote_addr = address
                self.seq_num = random.randint(0, 2 ** 32 - 1)
                self.ack_num = seq_num + 1
                self.state(ConState.SYN_RECEIVED)

            case ConState.SYN_RECEIVED if ack:
                self.seq_num += 1
                self.state(ConState.ESTABLISHED)

            case ConState.ESTABLISHED if len(data) > 0:
                # Duplicate packet check
                if seq_num < self.ack_num or seq_num in [sn for sn, _ in self.out_of_order_heap]:
                    log(f"Received duplicate packet: SEQ={seq_num}, Data={len(data)} bytes")
                    self._send_packet(ACK)
                else:
                    heapq.heappush(self.out_of_order_heap, (seq_num, data))

                    # heap is not empty and next in heap is expected seq_num
                    while (self.out_of_order_heap
                           and self.out_of_order_heap[0][0] == self.ack_num):
                        seq_num, data = heapq.heappop(self.out_of_order_heap)
                        self.recv_buffer += data
                        self.ack_num += len(data)

                self._send_packet(ACK)

            case ConState.ESTABLISHED if ack:
                # Handle ACK
                if ack_num > self.seq_num:
                    acknowledged_bytes = ack_num - self.seq_num
                    log(f"Received ACK for {acknowledged_bytes} bytes")
                    self.seq_num = ack_num
                    self.send_buffer = self.send_buffer[acknowledged_bytes:]
                    if self.inflight_last_seq_num == ack_num:
                        self._start_timer(TIMEOUT)
                        self._send_burst()
                else:
                    log(f"Received duplicate ACK for {ack_num}. Ignoring.")
            case ConState.ESTABLISHED if fin:
                self.seq_num = ack_num + 1
                self.state(ConState.CLOSE_WAIT)

            # Connection closing
            case ConState.FIN_WAIT_1 if fin and ack:
                self.seq_num = ack_num + 1
                self.ack_num = seq_num + 1
                self.state(ConState.TIME_WAIT)
            case ConState.FIN_WAIT_1 if ack:
                self.seq_num = ack_num
                self.ack_num = seq_num + 1
                self.state(ConState.FIN_WAIT_2)
            case ConState.FIN_WAIT_1 if fin:
                self.state(ConState.CLOSING)

            case ConState.FIN_WAIT_2 if fin:
                self.state(ConState.TIME_WAIT)
            case ConState.CLOSING if ack:
                self.seq_num = ack_num
                self.state(ConState.TIME_WAIT)

            case ConState.LAST_ACK if ack:
                self.seq_num = ack_num + 1
                self.state(ConState.CLOSED)
            case _ if rst:
                self.state(ConState.CLOSED)
            case _:
                log(f"Unhandled or invalid state")
                self._send_packet(RST)

        return True

    def state(self, new_state: ConState = None, retransmission=False) -> ConState:
        # get current state
        if new_state is None \
                or (new_state == self._state and not retransmission):
            return self._state

        # set new state

        if retransmission:
            self.retries += 1
        else:
            self.retries = 0

        self._stop_timer()
        old_state = self._state
        self._state = new_state
        timeout = TIMEOUT

        match new_state:
            case ConState.CLOSED:
                timeout = None
            case ConState.ESTABLISHED if old_state == ConState.SYN_RECEIVED:
                timeout = TIMEOUT
            case ConState.ESTABLISHED if old_state == ConState.SYN_SENT:
                self._send_packet(ACK)
                timeout = TIMEOUT
            case ConState.SYN_SENT:
                self.seq_num = random.randint(0, 2 ** 32 - 1)
                self.ack_num = 0
                self._send_packet(SYN)
            case ConState.SYN_RECEIVED:
                self._send_packet(SYN_ACK)
            case ConState.FIN_WAIT_1 \
                 | ConState.LAST_ACK:
                self._send_packet(FIN)
            case ConState.CLOSE_WAIT \
                 | ConState.CLOSING:
                self._send_packet(ACK)
            case ConState.TIME_WAIT:
                self._send_packet(ACK)
                timeout = TIMEOUT * 2

        log(f"State change: {old_state} -> {new_state}")

        if timeout:
            self._start_timer(timeout)
        return self._state

    def _send_packet(self, flags, data=b'', seq_num=None):
        if not self.remote_addr:
            raise ConnectionError("No remote address set")

        seq_num = seq_num if seq_num is not None else self.seq_num
        packet = pack_packet(seq_num, self.ack_num, flags, data)

        # Call the sender to send the packet
        self.sender_callback(packet, self.remote_addr)

        log(f"Sent packet: SEQ={seq_num}, ACK={self.ack_num}, [Flags={flags2str(flags)}]"
            f", Data={len(data)} bytes")

    def _send_burst(self):
        if not self.send_buffer:
            return

        sent_bytes = 0

        for i in range(1, INFLIGHT_COUNT):
            chunk_size = min(len(self.send_buffer) - sent_bytes, MAX_SEG_SIZE)
            if chunk_size <= 0:
                break

            chunk = self.send_buffer[sent_bytes:sent_bytes + chunk_size]
            self._send_packet(ACK, chunk, seq_num=self.seq_num + sent_bytes)
            sent_bytes += chunk_size
            self.inflight_last_seq_num = self.seq_num + sent_bytes

    def _handle_timeout(self):
        match self.state():
            case ConState.CLOSED \
                 | ConState.LISTEN \
                 | ConState.CLOSE_WAIT:
                log(f"Timeout in {self.state()}. Ignoring.")
                return False

        self.retries += 1
        if self.retries > MAX_RETRIES:
            log("Max retries exceeded in LAST_ACK. Forcing close.")
            self.state(ConState.CLOSED)
            return False

        log(f"Timeout in {self.state()}. Retrying {self.retries}/{MAX_RETRIES}")

        match self.state():
            case ConState.SYN_SENT:
                self._send_packet(SYN)
            case ConState.SYN_RECEIVED:
                self._send_packet(SYN_ACK)
            case ConState.FIN_WAIT_1 \
                 | ConState.CLOSING \
                 | ConState.LAST_ACK:
                self._send_packet(FIN)
            case ConState.TIME_WAIT:
                self.state(ConState.CLOSED)
                return True
            case ConState.ESTABLISHED:
                #  resend unacknowledged packets
                if self.send_buffer:
                    self._send_burst()
                else:
                    self._send_packet(ACK)

        self._start_timer()
        return True

    def _stop_timer(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None

    def _start_timer(self, timeout=TIMEOUT):
        if self.timer:
            self.timer.cancel()
        self.timer = threading.Timer(timeout * 1 + random.random(), self._handle_timeout)
        self.timer.daemon = True
        self.timer.start()


class RudpSocket:
    def __init__(self, local_address):
        self.loop = asyncio.get_event_loop()

        self.udp_socket = skt.socket(skt.AF_INET, skt.SOCK_DGRAM)
        self.udp_socket.setsockopt(skt.SOL_SOCKET, skt.SO_REUSEADDR, 1)
        self.udp_socket.setblocking(False)
        self.udp_socket.bind(local_address)

        self.connections: dict[tuple[str, int], Connection] = {}

        self._global_listener = None
        self.closing = False
        self.listening = False
        self.recv_queue = asyncio.Queue()

    async def connect(self, address: tuple[str, int]):
        """Connect to a remote address"""
        if address in self.connections:
            raise ConnectionError("Already connected to this address")

        self.listen()

        conn = self.create_or_get_connection(address)
        await conn.connect(address)

        log(f"Connected to {address}")

        return conn

    def listen(self):
        """Main listener for all incoming connections"""

        if self.listening:
            return

        self.listening = True
        self._global_listener = self.loop.create_task(self._listen_helper())

        log("Listening for incoming packets...")

    async def send(self, address: tuple[str, int], data: bytes):
        """Send data and ensure we're listening for responses"""
        if conn := self.connections.get(address):
            await conn.send(data)
        else:
            raise ConnectionError("No connection to send data")

    async def recv(self):
        while True:
            yield await self.recv_queue.get()

    async def get_connection(self, address):
        return self.connections[address]

    def create_or_get_connection(self, address: tuple[str, int]) -> Connection:
        """Get or create a connection"""
        if address not in self.connections:
            self.connections[address] = Connection(address, self._send_raw_packet)

        return self.connections[address]

    async def close(self):
        """Clean up all resources"""
        # Close all connections
        for conn in self.connections.values():
            conn.close()

        self.closing = True

        await asyncio.wait_for(self._global_listener, timeout=None)
        self.udp_socket.close()

    def _send_raw_packet(self, packet, address):
        async def test():
            if random.random() < LOSS_RATE:
                log(f"Simulated packet loss")
                return
            packet_bytes = packet
            if random.random() < CORRUPTION_RATE:
                packet_list= bytearray(packet_bytes)
                idx = random.randint(0, len(packet_list) - 1)
                packet_list[idx] = packet_list[idx]^0xFF
                packet_bytes= bytes(packet_list)
                log(f"Simulated corrupted byte")

            await self.loop.sock_sendto(self.udp_socket, packet_bytes, address)

        self.loop.create_task(test())

    async def _listen_helper(self):
        while True:
            try:
                # If connection is closed, remove it
                for addr, conn in list(self.connections.items()):
                    if conn.state() == ConState.CLOSED:
                        log(f"Connection closed: {addr}")
                        del self.connections[addr]

                packet, addr = await asyncio.wait_for(self.loop.sock_recvfrom(self.udp_socket, BUFFER_SIZE), 0.01)
                if not packet:
                    continue

                conn = self.create_or_get_connection(addr)

                conn.handle_packet(packet, addr)

                if conn.state() == ConState.CLOSE_WAIT:
                    conn.close()

                # recv buffers
                if conn.data_available():
                    await self.recv_queue.put((addr, conn.recv()))

                if not self.connections and self.closing:
                    break

            except BlockingIOError:
                await asyncio.sleep(0.01)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Global listener error: {e}")
                break
