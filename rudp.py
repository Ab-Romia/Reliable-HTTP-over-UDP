# rudp.py
import socket
import struct
import random
import time
import logging

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - RUDP - %(message)s')

# RUDP Header Format:
# Sequence Number (I: 4 bytes, unsigned int)
# Acknowledgement Number (I: 4 bytes, unsigned int)
# Flags (B: 1 byte, unsigned char)
# Length (H: 2 bytes, unsigned short) -> length of payload
# Checksum (H: 2 bytes, unsigned short)
RUDP_HEADER_FORMAT = '!IIBH H'
RUDP_HEADER_LENGTH = struct.calcsize(RUDP_HEADER_FORMAT)

# Flags
SYN_FLAG = 0x01
ACK_FLAG = 0x02
FIN_FLAG = 0x04
SYNACK_FLAG = SYN_FLAG | ACK_FLAG  # 0x03

# Max payload size per RUDP segment
PAYLOAD_MSS = 1024

# Default timeout for retransmissions (in seconds)
DEFAULT_TIMEOUT = 1.0  # 1 second
MAX_RETRANSMISSIONS = 5

# Connection States (simplified)
CLOSED = 0
LISTEN = 1
SYN_SENT = 2
SYN_RCVD = 3
ESTABLISHED = 4
FIN_WAIT_1 = 5
FIN_WAIT_2 = 6
CLOSE_WAIT = 7
LAST_ACK = 8
TIME_WAIT = 9  # Not fully implemented, but good to have as a concept


def calculate_checksum(header_fields, data_payload):
    """
    Calculates a 16-bit checksum for the RUDP header fields and data payload.
    header_fields is a tuple: (seq_num, ack_num, flags, length_of_data)
    The checksum field itself in the header is considered 0 during calculation.
    """
    seq_num, ack_num, flags, length_of_data = header_fields
    header_for_checksum = struct.pack('!IIBH', seq_num, ack_num, flags, length_of_data)
    packet_bytes = header_for_checksum + data_payload
    if len(packet_bytes) % 2 != 0:
        packet_bytes += b'\x00'
    checksum = 0
    for i in range(0, len(packet_bytes), 2):
        word = (packet_bytes[i] << 8) + packet_bytes[i + 1]
        checksum += word
        while checksum >> 16:
            checksum = (checksum & 0xFFFF) + (checksum >> 16)
    return (~checksum) & 0xFFFF


class RUDPsocket:
    def __init__(self, simulate_loss_rate=0.0, simulate_corruption_rate=0.0, local_addr=None):
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except Exception as e:
            logging.warning(f"Could not set SO_REUSEADDR: {e}")

        self.peer_addr = None
        self.seq_num = random.randint(0, 0xFFFFFFFF)
        self.ack_num = 0
        self.expected_seq_num_from_peer = 0
        self.state = CLOSED
        self.timeout = DEFAULT_TIMEOUT
        self.max_retries = MAX_RETRANSMISSIONS
        self.simulate_loss_rate = simulate_loss_rate
        self.simulate_corruption_rate = simulate_corruption_rate
        self.receive_buffer = b""
        self.last_received_ack_num_for_data = -1
        self.last_sent_data_segment = None
        self.last_sent_control_packet = None
        self.peer_fin_processed_during_fin_wait_1 = False  # Flag for simultaneous close

        if local_addr:
            try:
                self.udp_socket.bind(local_addr)
            except OSError as e:
                logging.error(f"RUDP Error binding to {local_addr}: {e}", exc_info=True)
                raise

    def _pack_packet(self, seq, ack, flags, payload=b''):
        if len(payload) > PAYLOAD_MSS:
            raise ValueError(f"Payload size {len(payload)} exceeds MSS {PAYLOAD_MSS}")
        length = len(payload)
        checksum = calculate_checksum((seq, ack, flags, length), payload)
        header = struct.pack(RUDP_HEADER_FORMAT, seq, ack, flags, length, checksum)
        return header + payload

    def _unpack_packet(self, packet_bytes):
        if len(packet_bytes) < RUDP_HEADER_LENGTH:
            logging.warning("Received packet too short for RUDP header.")
            return None
        header = packet_bytes[:RUDP_HEADER_LENGTH]
        payload = packet_bytes[RUDP_HEADER_LENGTH:]
        seq, ack_val, flags, length, checksum_recv = struct.unpack(RUDP_HEADER_FORMAT, header)
        if length != len(payload):
            logging.warning(f"Packet length mismatch: header says {length}, actual payload is {len(payload)}.")
            return None
        expected_checksum = calculate_checksum((seq, ack_val, flags, length), payload)
        if checksum_recv != expected_checksum:
            logging.warning(
                f"Checksum mismatch: received {checksum_recv}, calculated {expected_checksum}. Packet dropped.")
            return None
        return seq, ack_val, flags, payload

    def _send_raw_packet(self, packet_bytes, addr):
        if self.simulate_loss_rate > 0 and random.random() < self.simulate_loss_rate:
            logging.info(f"SIMULATED PACKET LOSS to {addr}")
            return len(packet_bytes)
        if self.simulate_corruption_rate > 0 and random.random() < self.simulate_corruption_rate:
            logging.info(f"SIMULATING CHECKSUM CORRUPTION for packet to {addr}")
            if len(packet_bytes) >= RUDP_HEADER_LENGTH:
                header_list = list(packet_bytes[:RUDP_HEADER_LENGTH])
                header_list[-2] = header_list[-2] ^ 0x01
                corrupted_header = bytes(header_list)
                packet_bytes = corrupted_header + packet_bytes[RUDP_HEADER_LENGTH:]
            elif packet_bytes:
                packet_list = list(packet_bytes)
                packet_list[0] = packet_list[0] ^ 0x01
                packet_bytes = bytes(packet_list)
        return self.udp_socket.sendto(packet_bytes, addr)

    def bind(self, address):
        try:
            self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except Exception as e:
            logging.warning(f"Could not set SO_REUSEADDR during explicit bind: {e}")
        try:
            self.udp_socket.bind(address)
            logging.info(f"Socket bound to {address}")
        except OSError as e:
            logging.error(f"RUDP Error during explicit bind to {address}: {e}", exc_info=True)
            raise

    def listen(self, backlog=1):
        if self.state == CLOSED or self.state == LISTEN:
            try:
                self.udp_socket.getsockname()
            except (OSError, socket.error) as e:
                logging.error(f"Cannot listen: Socket not bound or unusable. Error: {e}. Please bind the socket first.",
                              exc_info=True)
                if self.udp_socket.fileno() == -1:
                    logging.error("Socket seems to be closed or invalid. Cannot listen.")
                return False
            self.state = LISTEN
            logging.info(f"Socket listening on {self.udp_socket.getsockname()}")
            return True
        else:
            logging.error(f"Socket is not in a state to listen (current state: {self.state}). Cannot listen.")
            return False

    def accept(self):
        if self.state != LISTEN:
            logging.error("Socket is not in LISTEN state.")
            return None
        logging.info("Accepting connections...")
        initial_accept_timeout = None
        self.udp_socket.settimeout(initial_accept_timeout)
        while True:
            try:
                syn_packet, client_addr = self.udp_socket.recvfrom(PAYLOAD_MSS + RUDP_HEADER_LENGTH)
                unpacked = self._unpack_packet(syn_packet)
                if unpacked:
                    seq_rcvd, _, flags_rcvd, _ = unpacked
                    if flags_rcvd == SYN_FLAG:
                        logging.info(f"SYN received from {client_addr} with SEQ={seq_rcvd}.")
                        self.peer_addr = client_addr
                        self.expected_seq_num_from_peer = seq_rcvd + 1
                        self.ack_num = seq_rcvd + 1
                        self.seq_num = random.randint(0, 0xFFFFFFFF)
                        synack_pkt = self._pack_packet(self.seq_num, self.ack_num, SYNACK_FLAG)
                        self._send_raw_packet(synack_pkt, self.peer_addr)
                        logging.info(f"Sent SYN-ACK to {self.peer_addr} with SEQ={self.seq_num}, ACK={self.ack_num}.")
                        self.state = SYN_RCVD
                        self.udp_socket.settimeout(self.timeout)
                        for attempt in range(self.max_retries + 1):
                            if attempt > 0:
                                self._send_raw_packet(synack_pkt, self.peer_addr)
                                logging.warning(
                                    f"Retransmitting SYN-ACK to {self.peer_addr} (Attempt {attempt + 1}/{self.max_retries + 1}).")
                            try:
                                ack_packet, addr = self.udp_socket.recvfrom(RUDP_HEADER_LENGTH)
                                if addr != self.peer_addr:
                                    logging.warning(
                                        f"Accept: Received packet from unexpected address {addr} while waiting for ACK of SYN-ACK.")
                                    continue
                                unpacked_ack = self._unpack_packet(ack_packet)
                                if unpacked_ack:
                                    ack_seq_rcvd, ack_ack_val, ack_flags, _ = unpacked_ack
                                    if ack_flags == ACK_FLAG and ack_ack_val == (self.seq_num + 1):
                                        logging.info(
                                            f"ACK received for SYN-ACK from {self.peer_addr}. Client_SEQ={ack_seq_rcvd}, ACK_VAL={ack_ack_val}.")
                                        self.state = ESTABLISHED
                                        self.seq_num += 1
                                        logging.info(f"Connection ESTABLISHED with {self.peer_addr}.")
                                        logging.info(
                                            f"Server: Next send SEQ={self.seq_num}, Next expected SEQ from client={self.expected_seq_num_from_peer}")
                                        return self
                                    else:
                                        logging.warning(
                                            f"Received unexpected ACK/packet in SYN_RCVD: Flags={ack_flags}, Their_SEQ={ack_seq_rcvd}, ACK_VAL={ack_ack_val}. Expected ACK_VAL for our SEQ+1 ({self.seq_num + 1})")
                            except socket.timeout:
                                if attempt < self.max_retries:
                                    logging.warning("Timeout waiting for ACK of SYN-ACK.")
                                else:
                                    logging.error(
                                        "Failed to establish connection: No ACK for SYN-ACK after multiple retries.")
                                    self.state = LISTEN
                                    self.peer_addr = None
                                    self.udp_socket.settimeout(initial_accept_timeout)
                                    break
                        if self.state != ESTABLISHED:
                            continue
            except socket.timeout:
                logging.debug("Timeout waiting for an initial SYN in accept().")
                continue
            except Exception as e:
                logging.error(f"Error during accept's main loop: {e}", exc_info=True)
                if self.state != LISTEN:
                    self.state = LISTEN
                    self.udp_socket.settimeout(initial_accept_timeout)
                continue
        return None

    def connect(self, address):
        if self.state != CLOSED:
            logging.error("Socket is not closed, cannot connect.")
            raise ConnectionError("Socket not closed")
        self.peer_addr = address
        self.state = SYN_SENT
        syn_pkt = self._pack_packet(self.seq_num, 0, SYN_FLAG)
        self.udp_socket.settimeout(self.timeout)
        for i in range(self.max_retries + 1):
            self._send_raw_packet(syn_pkt, self.peer_addr)
            if i == 0:
                logging.info(f"Sent SYN to {self.peer_addr} with SEQ={self.seq_num}.")
            else:
                logging.warning(f"Retransmitting SYN to {self.peer_addr} (Attempt {i + 1}/{self.max_retries + 1}).")
            try:
                synack_packet, server_addr = self.udp_socket.recvfrom(PAYLOAD_MSS + RUDP_HEADER_LENGTH)
                if server_addr != self.peer_addr:
                    logging.warning(f"Connect: Received packet from unexpected address {server_addr}")
                    continue
                unpacked = self._unpack_packet(synack_packet)
                if unpacked:
                    seq_rcvd, ack_rcvd, flags_rcvd, _ = unpacked
                    if flags_rcvd == SYNACK_FLAG and ack_rcvd == (self.seq_num + 1):
                        logging.info(
                            f"SYN-ACK received from {self.peer_addr} with Server_SEQ={seq_rcvd}, ACK_for_our_SYN={ack_rcvd}.")
                        self.expected_seq_num_from_peer = seq_rcvd + 1
                        self.ack_num = seq_rcvd + 1
                        ack_pkt = self._pack_packet(self.seq_num + 1, self.ack_num, ACK_FLAG)
                        self._send_raw_packet(ack_pkt, self.peer_addr)
                        logging.info(
                            f"Sent ACK for SYN-ACK to {self.peer_addr} with Our_SEQ={self.seq_num + 1}, ACK_VAL={self.ack_num}.")
                        self.state = ESTABLISHED
                        self.seq_num += 1
                        logging.info(f"Connection ESTABLISHED with {self.peer_addr}.")
                        logging.info(
                            f"Client: Next send SEQ={self.seq_num}, Next expected SEQ from server={self.expected_seq_num_from_peer}")
                        return True
                    else:
                        logging.warning(
                            f"Received unexpected packet in SYN_SENT: Flags={flags_rcvd}, Server_SEQ={seq_rcvd}, ACK_VAL={ack_rcvd}. Expected SYNACK and ACK for our SEQ+1 ({self.seq_num + 1})")
            except socket.timeout:
                if i < self.max_retries:
                    logging.warning("Timeout waiting for SYN-ACK.")
                else:
                    logging.error("Connection failed: No SYN-ACK received after multiple retries.")
                    self.state = CLOSED
                    self.peer_addr = None
                    return False
            except Exception as e:
                logging.error(f"Error during connect: {e}", exc_info=True)
                self.state = CLOSED
                self.peer_addr = None
                return False
        self.state = CLOSED
        return False

    def send_data(self, data_payload):
        if self.state != ESTABLISHED:
            logging.error("Cannot send data: Connection not established.")
            return False
        if not data_payload:
            logging.warning("send_data called with empty payload.")
            return True
        data_pkt = self._pack_packet(self.seq_num, self.ack_num, 0, data_payload)
        self.udp_socket.settimeout(self.timeout)
        for i in range(self.max_retries + 1):
            self._send_raw_packet(data_pkt, self.peer_addr)
            if i == 0:
                logging.info(
                    f"Sent DATA to {self.peer_addr}: SEQ={self.seq_num}, ACK_in_pkt={self.ack_num}, LEN={len(data_payload)}")
            else:
                logging.warning(
                    f"Retransmitting DATA to {self.peer_addr}: SEQ={self.seq_num} (Attempt {i + 1}/{self.max_retries + 1})")
            try:
                response_packet, addr = self.udp_socket.recvfrom(RUDP_HEADER_LENGTH + PAYLOAD_MSS)
                if addr != self.peer_addr:
                    continue
                unpacked_resp = self._unpack_packet(response_packet)
                if unpacked_resp:
                    resp_seq, resp_ack_val, resp_flags, resp_payload = unpacked_resp
                    if resp_flags & ACK_FLAG and resp_ack_val == self.seq_num:
                        logging.info(
                            f"Received ACK for our DATA (SEQ={self.seq_num}): Their_Pkt_SEQ={resp_seq}, ACK_VAL={resp_ack_val}")
                        self.seq_num += 1
                        if not (resp_flags & (SYN_FLAG | FIN_FLAG)) and len(resp_payload) > 0:
                            if resp_seq == self.expected_seq_num_from_peer:
                                logging.info(
                                    f"Piggybacked DATA received with ACK: Their_SEQ={resp_seq}, LEN={len(resp_payload)}. Processing it.")
                                ack_for_their_data = resp_seq
                                ack_pkt = self._pack_packet(self.seq_num, ack_for_their_data, ACK_FLAG)
                                self._send_raw_packet(ack_pkt, self.peer_addr)
                                logging.info(
                                    f"Sent ACK for piggybacked data: Our_Pkt_SEQ={self.seq_num}, ACK_VAL={ack_for_their_data}")
                                self.expected_seq_num_from_peer += 1
                                self.ack_num = ack_for_their_data
                            else:
                                logging.warning(
                                    f"Piggybacked DATA received, but not expected SEQ. Their_SEQ={resp_seq}, Expected={self.expected_seq_num_from_peer}")
                        return True
                    else:
                        logging.warning(
                            f"send_data: Received packet not an ACK for our SEQ={self.seq_num}. Flags={resp_flags}, ACK_VAL={resp_ack_val}, Their_SEQ={resp_seq}")
                        if not (resp_flags & (SYN_FLAG | FIN_FLAG | ACK_FLAG)) and len(resp_payload) > 0:  # Pure data
                            if resp_seq == self.expected_seq_num_from_peer:
                                logging.info(
                                    f"send_data: While waiting for ACK, received DATA from peer: SEQ={resp_seq}. Sending ACK.")
                                ack_for_their_data = resp_seq
                                ack_pkt = self._pack_packet(self.seq_num, ack_for_their_data, ACK_FLAG)
                                self._send_raw_packet(ack_pkt, self.peer_addr)
                                self.expected_seq_num_from_peer += 1
                                self.ack_num = ack_for_their_data
            except socket.timeout:
                if i < self.max_retries:
                    logging.warning(f"Timeout waiting for ACK of DATA (SEQ={self.seq_num}).")
                else:
                    logging.error(
                        f"Failed to send DATA (SEQ={self.seq_num}): No ACK received after {self.max_retries} retries.")
                    return False
            except Exception as e:
                logging.error(f"Error during send_data's ACK wait: {e}", exc_info=True)
                return False
        return False

    def receive_data(self):
        if not (self.state == ESTABLISHED or self.state == CLOSE_WAIT):
            logging.error(
                f"Cannot receive data: Connection not in ESTABLISHED or CLOSE_WAIT state (State: {self.state}).")
            return None
        self.udp_socket.settimeout(self.timeout * 1.5 if self.timeout else None)
        try:
            packet, addr = self.udp_socket.recvfrom(PAYLOAD_MSS + RUDP_HEADER_LENGTH)
            if addr != self.peer_addr:
                logging.warning(f"receive_data: Discarding packet from unknown source {addr}")
                return self.receive_data()
            unpacked = self._unpack_packet(packet)
            if unpacked:
                seq_rcvd, ack_val_rcvd, flags_rcvd, payload_rcvd = unpacked
                if flags_rcvd & ACK_FLAG:
                    logging.debug(
                        f"receive_data: Packet from peer has ACK flag. Their_SEQ={seq_rcvd}, ACK_VAL={ack_val_rcvd}")
                if not (flags_rcvd & (SYN_FLAG | FIN_FLAG)) and len(payload_rcvd) > 0:
                    if seq_rcvd == self.expected_seq_num_from_peer:
                        logging.info(
                            f"Received DATA from {self.peer_addr}: SEQ={seq_rcvd}, LEN={len(payload_rcvd)}. Expected SEQ={self.expected_seq_num_from_peer}")
                        ack_to_send_for_their_data = seq_rcvd
                        ack_pkt = self._pack_packet(self.seq_num, ack_to_send_for_their_data, ACK_FLAG)
                        self._send_raw_packet(ack_pkt, self.peer_addr)
                        logging.info(
                            f"Sent ACK for received data: Our_Pkt_SEQ={self.seq_num}, ACK_VAL={ack_to_send_for_their_data}")
                        self.expected_seq_num_from_peer += 1
                        self.ack_num = ack_to_send_for_their_data
                        return payload_rcvd
                    elif seq_rcvd < self.expected_seq_num_from_peer:
                        logging.warning(
                            f"Received DUPLICATE/OLD DATA: SEQ={seq_rcvd} (Expected {self.expected_seq_num_from_peer}). Re-ACKing.")
                        ack_to_send_for_their_data = seq_rcvd
                        ack_pkt = self._pack_packet(self.seq_num, ack_to_send_for_their_data, ACK_FLAG)
                        self._send_raw_packet(ack_pkt, self.peer_addr)
                        return self.receive_data()
                    else:
                        logging.warning(
                            f"Received OUT-OF-ORDER DATA: SEQ={seq_rcvd} (Expected {self.expected_seq_num_from_peer}). Discarding for now.")
                        return self.receive_data()
                elif flags_rcvd & FIN_FLAG:
                    logging.info(f"FIN received from {self.peer_addr}: Their_SEQ={seq_rcvd}, Flags={bin(flags_rcvd)}")
                    # Check if FIN sequence number is expected (or the one before if it's a FIN+ACK for last data)
                    # The expected_seq_num_from_peer is the *next* data/control segment.
                    if seq_rcvd == self.expected_seq_num_from_peer:
                        ack_for_their_fin = seq_rcvd
                        self.expected_seq_num_from_peer += 1
                        ack_pkt = self._pack_packet(self.seq_num, ack_for_their_fin, ACK_FLAG)
                        self._send_raw_packet(ack_pkt, self.peer_addr)
                        logging.info(
                            f"Sent ACK for peer's FIN: Our_Pkt_SEQ={self.seq_num}, ACK_VAL={ack_for_their_fin}")
                        if self.state == ESTABLISHED:
                            self.state = CLOSE_WAIT
                            logging.info("State changed to CLOSE_WAIT.")
                        elif self.state == FIN_WAIT_2:
                            self.state = TIME_WAIT
                            logging.info("Both FINs exchanged. State: FIN_WAIT_2 -> TIME_WAIT.")
                        return b''
                    elif seq_rcvd < self.expected_seq_num_from_peer:
                        logging.warning(f"Received duplicate/old FIN (SEQ={seq_rcvd}). Re-ACKing.")
                        ack_pkt = self._pack_packet(self.seq_num, seq_rcvd, ACK_FLAG)
                        self._send_raw_packet(ack_pkt, self.peer_addr)
                        return self.receive_data()
                    else:
                        logging.warning(
                            f"Received future FIN (SEQ={seq_rcvd}, Expected {self.expected_seq_num_from_peer}). Ignoring for now.")
                        return self.receive_data()
                elif flags_rcvd == ACK_FLAG and not payload_rcvd:
                    logging.debug(
                        f"receive_data: Received a standalone ACK from peer. Their_SEQ={seq_rcvd}, ACK_VAL={ack_val_rcvd}.")
                    return self.receive_data()
                else:
                    logging.debug(
                        f"receive_data: Received packet not DATA or FIN for us. Flags={bin(flags_rcvd)}. Trying again.")
                    return self.receive_data()
            else:
                logging.debug("receive_data: Unpack failed. Packet dropped. Trying again.")
                return self.receive_data()
        except socket.timeout:
            logging.debug("Timeout waiting for data in receive_data.")
            return None
        except Exception as e:
            logging.error(f"Error during receive_data: {e}", exc_info=True)
            return None

    def close(self):
        logging.info(f"Close called. Current state: {self.state}, Peer: {self.peer_addr}")
        if not self.peer_addr and self.state != LISTEN:
            logging.warning(
                "Close called but no peer_addr is set and not in LISTEN. Assuming already closed or uninitialized.")
            self.state = CLOSED
            if hasattr(self.udp_socket, 'close') and callable(self.udp_socket.close):
                try:
                    if self.udp_socket.fileno() != -1: self.udp_socket.close()
                except Exception as e_close:
                    logging.debug(f"Exception closing socket in no-peer scenario: {e_close}")
            return True

        original_timeout = self.udp_socket.gettimeout()
        self.udp_socket.settimeout(self.timeout)
        self.peer_fin_processed_during_fin_wait_1 = False  # Reset flag for this close attempt

        active_close_initiated = False

        if self.state == ESTABLISHED:
            active_close_initiated = True
            self.state = FIN_WAIT_1
            fin_pkt = self._pack_packet(self.seq_num, self.ack_num, FIN_FLAG)
            logging.info(f"Initiating close (sending FIN): Our_SEQ={self.seq_num}, ACK_in_FIN_pkt={self.ack_num}")
            self._send_raw_packet(fin_pkt, self.peer_addr)

            acked_our_fin = False
            for i in range(self.max_retries + 1):
                if i > 0:
                    logging.warning(
                        f"Timeout in FIN_WAIT_1. Retransmitting our FIN (Attempt {i + 1}/{self.max_retries + 1}). Our_SEQ={self.seq_num}")
                    self._send_raw_packet(fin_pkt, self.peer_addr)

                try:
                    response_packet, addr = self.udp_socket.recvfrom(RUDP_HEADER_LENGTH + PAYLOAD_MSS)
                    if addr != self.peer_addr: continue
                    unpacked_resp = self._unpack_packet(response_packet)
                    if unpacked_resp:
                        resp_seq, resp_ack_val, resp_flags, _ = unpacked_resp

                        # Scenario 1: Received ACK for our FIN
                        if resp_flags & ACK_FLAG and resp_ack_val == self.seq_num:
                            logging.info(
                                f"FIN_WAIT_1: Received ACK for our FIN (SEQ={self.seq_num}). Their_Pkt_SEQ={resp_seq}.")
                            self.seq_num += 1  # Consume our FIN
                            acked_our_fin = True
                            # If peer's FIN was also part of this packet or processed earlier
                            if resp_flags & FIN_FLAG and resp_seq == self.expected_seq_num_from_peer:  # FIN+ACK for our FIN
                                logging.info("FIN_WAIT_1: Peer's FIN also present (FIN+ACK). Their_FIN_SEQ={resp_seq}.")
                                ack_for_their_fin = resp_seq
                                self.expected_seq_num_from_peer += 1
                                final_ack_pkt = self._pack_packet(self.seq_num, ack_for_their_fin, ACK_FLAG)
                                self._send_raw_packet(final_ack_pkt, self.peer_addr)
                                logging.info(
                                    f"Sent final ACK for peer's FIN part. Our_Pkt_SEQ={self.seq_num}, ACK_VAL={ack_for_their_fin}.")
                                self.state = TIME_WAIT
                                logging.info("Connection closed (FIN+ACK). State: TIME_WAIT -> CLOSED")
                            elif self.peer_fin_processed_during_fin_wait_1:
                                logging.info(
                                    "FIN_WAIT_1: Our FIN ACKed and peer's FIN was processed earlier. Transitioning to TIME_WAIT.")
                                # We already ACKed their FIN when self.peer_fin_processed_during_fin_wait_1 was set.
                                self.state = TIME_WAIT
                            else:
                                logging.info(
                                    "FIN_WAIT_1: Our FIN ACKed. Peer's FIN not yet processed. Transitioning to FIN_WAIT_2.")
                                self.state = FIN_WAIT_2
                            break  # Exit FIN_WAIT_1 retry loop

                        # Scenario 2: Received peer's FIN (without ACK for our FIN yet, or standalone)
                        elif resp_flags & FIN_FLAG and resp_seq == self.expected_seq_num_from_peer:
                            logging.info(
                                f"FIN_WAIT_1: Received peer's FIN (Their_SEQ={resp_seq}). Our FIN (SEQ={self.seq_num}) not ACKed by this packet.")
                            ack_for_their_fin = resp_seq
                            self.expected_seq_num_from_peer += 1
                            ack_pkt = self._pack_packet(self.seq_num, ack_for_their_fin,
                                                        ACK_FLAG)  # Use our current FIN's SEQ
                            self._send_raw_packet(ack_pkt, self.peer_addr)
                            logging.info(
                                f"Sent ACK for peer's standalone FIN. Our_Pkt_SEQ={self.seq_num}, ACK_VAL={ack_for_their_fin}. Still in FIN_WAIT_1.")
                            self.peer_fin_processed_during_fin_wait_1 = True
                            # Loop continues, waiting for ACK of *our* FIN.

                        # Scenario 3: Duplicate of peer's FIN if already processed
                        elif resp_flags & FIN_FLAG and self.peer_fin_processed_during_fin_wait_1 and resp_seq < self.expected_seq_num_from_peer:
                            logging.warning(
                                f"FIN_WAIT_1: Received duplicate of already processed peer's FIN (SEQ={resp_seq}). Re-ACKing.")
                            ack_pkt = self._pack_packet(self.seq_num, resp_seq, ACK_FLAG)
                            self._send_raw_packet(ack_pkt, self.peer_addr)
                            # Loop continues

                    else:
                        logging.warning("FIN_WAIT_1: Checksum error on packet. Retrying our FIN.")
                except socket.timeout:
                    if i == self.max_retries:
                        logging.error(
                            "FIN_WAIT_1: Max retries. Failed to get ACK for our FIN or process peer's FIN. Forcing close.")
                        self.state = CLOSED
                        if hasattr(self.udp_socket, 'close') and callable(
                            self.udp_socket.close) and self.udp_socket.fileno() != -1: self.udp_socket.close()
                        return False
            # --- End of FIN_WAIT_1 Loop ---

            if self.state == FIN_WAIT_2:  # Our FIN was ACKed, peer's FIN not yet processed (or seen)
                logging.info("In FIN_WAIT_2, waiting for peer's FIN (peer_fin_processed_during_fin_wait_1 was false).")
                for attempt_peer_fin in range(self.max_retries + 1):
                    if attempt_peer_fin > 0:
                        logging.warning(
                            f"Timeout in FIN_WAIT_2 waiting for peer's FIN. (Attempt {attempt_peer_fin + 1}/{self.max_retries + 1})")
                        # No retransmission from our side here, just waiting.
                    try:
                        peer_fin_packet, addr = self.udp_socket.recvfrom(RUDP_HEADER_LENGTH + PAYLOAD_MSS)
                        if addr != self.peer_addr: continue
                        unpacked_fin = self._unpack_packet(peer_fin_packet)
                        if unpacked_fin:
                            fin_seq, _, fin_flags, _ = unpacked_fin
                            if fin_flags & FIN_FLAG and fin_seq == self.expected_seq_num_from_peer:
                                logging.info(f"FIN_WAIT_2: Received peer's FIN: Their_SEQ={fin_seq}")
                                self.expected_seq_num_from_peer += 1
                                final_ack_pkt = self._pack_packet(self.seq_num, fin_seq,
                                                                  ACK_FLAG)  # Use our updated self.seq_num
                                self._send_raw_packet(final_ack_pkt, self.peer_addr)
                                logging.info(
                                    f"Sent final ACK for peer's FIN. Our_Pkt_SEQ={self.seq_num}, ACK_VAL={fin_seq}.")
                                self.state = TIME_WAIT
                                break  # Break from FIN_WAIT_2 loop
                            elif fin_flags & FIN_FLAG and fin_seq < self.expected_seq_num_from_peer:
                                logging.warning(f"FIN_WAIT_2: Received duplicate peer FIN (SEQ={fin_seq}). Re-ACKing.")
                                final_ack_pkt = self._pack_packet(self.seq_num, fin_seq, ACK_FLAG)
                                self._send_raw_packet(final_ack_pkt, self.peer_addr)
                                # If this duplicate completes the sequence, should also transition.
                                # This case implies the original expected FIN was already processed.
                                # This logic path might be redundant if FIN_WAIT_1 handles it.
                                # For now, just re-ACK. If it was the one we needed, the next loop might timeout.
                            else:
                                logging.debug(
                                    f"FIN_WAIT_2: Received packet is not the expected FIN. Flags={bin(fin_flags)}, SEQ={fin_seq}, Expected={self.expected_seq_num_from_peer}")

                        # else: checksum error, loop continues to recv
                    except socket.timeout:
                        if attempt_peer_fin == self.max_retries:
                            logging.error("FIN_WAIT_2: Did not receive peer's FIN after retries. Forcing close.")
                            self.state = CLOSED
                            break  # Break from FIN_WAIT_2 loop
                # After FIN_WAIT_2 loop
                if self.state == TIME_WAIT:
                    logging.info("Connection fully closed via FIN_WAIT_2. State: TIME_WAIT -> CLOSED")
                else:  # Likely timed out
                    logging.error(f"Exited FIN_WAIT_2, state is {self.state}. Forcing close.")
                    self.state = CLOSED

            elif self.state == TIME_WAIT:  # Transitioned directly from FIN_WAIT_1
                logging.info(
                    "Connection closed (handled in FIN_WAIT_1 due to early peer FIN). State: TIME_WAIT -> CLOSED")

            elif not acked_our_fin:
                logging.error("Our FIN was not acknowledged after FIN_WAIT_1 loop. Forcing close.")
                self.state = CLOSED

            # Finalize state to CLOSED if it reached TIME_WAIT or needs forcing
            if self.state == TIME_WAIT or self.state != CLOSED:  # If not already forced closed by a failure path
                if self.state != CLOSED:  # If it was TIME_WAIT or some other intermediate state from FIN_WAIT_1/2
                    logging.info(f"Transitioning from {self.state} to CLOSED.")
                # time.sleep(0.01) # Simulate TIME_WAIT if desired
                self.state = CLOSED

            if hasattr(self.udp_socket, 'close') and callable(
                self.udp_socket.close) and self.udp_socket.fileno() != -1: self.udp_socket.close()
            return self.state == CLOSED


        elif self.state == CLOSE_WAIT:
            self.state = LAST_ACK
            fin_pkt = self._pack_packet(self.seq_num, self.expected_seq_num_from_peer, FIN_FLAG)
            logging.info(
                f"Sending our FIN (from CLOSE_WAIT to LAST_ACK): Our_SEQ={self.seq_num}, ACK_in_pkt={self.expected_seq_num_from_peer}")
            self._send_raw_packet(fin_pkt, self.peer_addr)
            for i in range(self.max_retries + 1):
                if i > 0:
                    logging.warning(
                        f"Timeout waiting for ACK of our FIN (in LAST_ACK). Retransmitting FIN (Attempt {i + 1}/{self.max_retries + 1}).")
                    self._send_raw_packet(fin_pkt, self.peer_addr)
                try:
                    ack_packet, addr = self.udp_socket.recvfrom(RUDP_HEADER_LENGTH)
                    if addr != self.peer_addr: continue
                    unpacked_ack = self._unpack_packet(ack_packet)
                    if unpacked_ack:
                        _, ack_ack_val, ack_flags, _ = unpacked_ack
                        if ack_flags == ACK_FLAG and ack_ack_val == self.seq_num:
                            logging.info(f"Received ACK for our FIN (in LAST_ACK): ACK_VAL={ack_ack_val}")
                            self.seq_num += 1
                            self.state = CLOSED
                            logging.info("Connection closed successfully (passive close complete).")
                            if hasattr(self.udp_socket, 'close') and callable(
                                self.udp_socket.close) and self.udp_socket.fileno() != -1: self.udp_socket.close()
                            return True
                        else:
                            logging.warning(
                                f"Unexpected packet received in LAST_ACK. Flags={ack_flags}, ACK_VAL={ack_ack_val}, Expected ACK for {self.seq_num}")
                except socket.timeout:
                    if i == self.max_retries:
                        logging.error("Max retries: Failed to get ACK for our FIN in LAST_ACK. Forcing close.")
                        self.state = CLOSED
                        if hasattr(self.udp_socket, 'close') and callable(
                            self.udp_socket.close) and self.udp_socket.fileno() != -1: self.udp_socket.close()
                        return False
            logging.error("Exited FIN retransmission loop in LAST_ACK without success. Forcing close.")
            self.state = CLOSED
            if hasattr(self.udp_socket, 'close') and callable(
                self.udp_socket.close) and self.udp_socket.fileno() != -1: self.udp_socket.close()
            return False

        elif self.state == LISTEN:
            logging.info("Closing a listening socket.")
            self.state = CLOSED
            if hasattr(self.udp_socket, 'close') and callable(
                self.udp_socket.close) and self.udp_socket.fileno() != -1: self.udp_socket.close()
            return True

        elif self.state == CLOSED:
            logging.info("Connection already closed.")
            if hasattr(self.udp_socket, 'close') and callable(self.udp_socket.close):
                try:
                    if self.udp_socket.fileno() != -1: self.udp_socket.close()
                except Exception as e:
                    logging.debug(f"Exception while trying to close already closed socket: {e}")
            return True
        else:
            logging.warning(f"Close called in an intermediate or unexpected state: {self.state}. Forcing close.")
            self.state = CLOSED
            if hasattr(self.udp_socket, 'close') and callable(
                self.udp_socket.close) and self.udp_socket.fileno() != -1: self.udp_socket.close()
            return False

        if original_timeout is not None:
            try:
                if self.udp_socket.fileno() != -1: self.udp_socket.settimeout(original_timeout)
            except Exception as e:
                logging.debug(f"Could not restore original timeout: {e}")

        if active_close_initiated and self.state != CLOSED:
            logging.error(
                f"Active close initiated but connection not fully closed. Final state: {self.state}. Forcing to CLOSED.")
            self.state = CLOSED
            if hasattr(self.udp_socket, 'close') and callable(
                self.udp_socket.close) and self.udp_socket.fileno() != -1: self.udp_socket.close()
            return False

        return self.state == CLOSED
