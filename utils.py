import logging
import random
import struct
from colorama import Fore, Style

import colorama

ENABLE_DEBUG = True
global NAME


def flags2str(flags):
    from rudp import SYN, ACK, FIN, RST

    flag_str = []
    if flags & SYN:
        flag_str.append("SYN")
    if flags & ACK:
        flag_str.append("ACK")
    if flags & FIN:
        flag_str.append("FIN")
    if flags & RST:
        flag_str.append("RST")
    return " | ".join(flag_str) if flag_str else "None"


colorama.init()


def log(message):
    if not ENABLE_DEBUG:
        return
    if "Sent" in message:
        colored_message = f"{Fore.GREEN}{message}{Style.RESET_ALL}"
    elif "Received" in message:
        colored_message = f"{Fore.YELLOW}{message}{Style.RESET_ALL}"
    elif "State change" in message:
        colored_message = f"{Fore.BLUE}{message}{Style.RESET_ALL}"
    elif "Timeout" in message or "Error" in message or "error" in message:
        colored_message = f"{Fore.RED}{message}{Style.RESET_ALL}"
    elif "Listening" in message:
        colored_message = f"{Fore.MAGENTA}{message}{Style.RESET_ALL}"
    else:
        colored_message = f"{Fore.CYAN}{message}{Style.RESET_ALL}"

    print(colored_message)


def simulate_packet_corruption(packet, error_rate):
    if random.random() < error_rate:
        pos = random.randint(0, len(packet) - 1)
        corrupted_byte = (packet[pos] + random.randint(1, 255)) % 256
        return packet[:pos] + bytes([corrupted_byte]) + packet[pos + 1:]
    return packet


def simulate_out_of_order(seq_num, out_of_order_rate):
    pass


def simulate_packet_loss(loss_rate):
    return random.random() < loss_rate


def calculate_checksum(seq_num, ack_num, flags, length, payload_data=b''):
    header = struct.pack('!IIBH', seq_num, ack_num, flags, length)
    data = header + payload_data
    if len(data) % 2 == 1:
        data += b'\x00'
    checksum = 0
    for i in range(0, len(data), 2):
        word = (data[i] << 8) + data[i + 1]
        checksum += word
        while checksum >> 16:
            checksum = (checksum & 0xFFFF) + (checksum >> 16)
    return ~checksum & 0xFFFF


def pack_packet(seq_num, ack_num, flags, data=b''):
    from rudp import MAX_SEG_SIZE, RUDP_HEADER_FORMAT
    length = len(data)
    if length > MAX_SEG_SIZE:
        logging.error(f"Payload size {length} exceeds MAX_PAYLOAD_SIZE {MAX_SEG_SIZE}.")
    checksum = calculate_checksum(seq_num, ack_num, flags, length, data)
    header = struct.pack(RUDP_HEADER_FORMAT, seq_num, ack_num, flags, length, checksum)
    return header + data


def unpack_packet(packet):
    from rudp import RUDP_HEADER_FORMAT, RUDP_HEADER_SIZE

    if len(packet) < RUDP_HEADER_SIZE:
        logging.warning("RUDP: Received packet too short for header.")
        return None
    header = packet[:RUDP_HEADER_SIZE]
    payload = packet[RUDP_HEADER_SIZE:]
    seq_num, ack_num, flags, length_from_header, checksum_from_packet = struct.unpack(RUDP_HEADER_FORMAT, header)

    if length_from_header != len(payload):
        logging.warning(f"RUDP: Payload length mismatch. Header: {length_from_header}, Actual: {len(payload)}.")
        return None
    expected_checksum = calculate_checksum(seq_num, ack_num, flags, length_from_header, payload)
    if checksum_from_packet != expected_checksum:
        logging.warning(
            f"RUDP: Checksum mismatch. Received: {checksum_from_packet}, Calculated: {expected_checksum}."
            f" Packet dropped.")
        return None
    return seq_num, ack_num, flags, payload


class Parser:
    def __init__(self, on_message_complete):
        self.headers = {}
        self.body = b""
        self.complete = False
        self.url = ""
        self.on_message_complete_cb = on_message_complete

    def on_url(self, url: bytes):
        self.url = url.decode()

    def on_header(self, name: bytes, value: bytes):
        self.headers[name.decode().lower()] = value.decode()

    def on_body(self, body: bytes):
        self.body += body  # Accumulate partial body data

    def on_message_complete(self):
        self.complete = True
        self.on_message_complete_cb(self)
