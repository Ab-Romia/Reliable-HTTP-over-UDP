import logging
import struct
import socket
import random
import time

# --- Constants ---
MAX_PAYLOAD_SIZE = 1024
HEADER_FORMAT = '!IIBH H'  # SEQ, ACK_NUM, FLAGS, LENGTH, CHECKSUM
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# --- Flags ---
SYN_FLAG = 0x01
ACK_FLAG = 0x02
FIN_FLAG = 0x04
SYNACK_FLAG = SYN_FLAG | ACK_FLAG

# --- Configuration ---
TIMEOUT = 1.0
RETRANSMISSIONS = 5

# --- Connection States ---
CLOSED = 0
LISTEN = 1
SYN_SENT = 2
SYN_RECEIVED = 3
ESTABLISHED = 4
FIN_WAIT1 = 5
FIN_WAIT2 = 6
CLOSE_WAIT = 7
LAST_ACK = 8
TIME_WAIT = 9  # TIME_WAIT will be brief and lead to CLOSED

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - RUDP - %(message)s')


def checksum_calc(header_tuple, data):
    seq, ack, flags, length = header_tuple
    header_bytes = struct.pack('!IIBH', seq, ack, flags, length)
    packet = header_bytes + data
    if len(packet) % 2 == 1:
        packet += b'\x00'
    checksum = 0
    for i in range(0, len(packet), 2):
        word = (packet[i] << 8) + packet[i + 1]
        checksum += word
        while checksum >> 16:
            checksum = (checksum & 0xFFFF) + (checksum >> 16)
    return ~checksum & 0xFFFF


def pack_packet(seq, ack, flags, payload=b''):
    if len(payload) > MAX_PAYLOAD_SIZE:
        raise ValueError(f"Payload size exceeds {MAX_PAYLOAD_SIZE} bytes.")
    length = len(payload)
    checksum = checksum_calc((seq, ack, flags, length), payload)
    header = struct.pack(HEADER_FORMAT, seq, ack, flags, length, checksum)
    return header + payload


def unpack_packet(packet_bytes):
    if len(packet_bytes) < HEADER_SIZE:
        logging.warning("RUDP: Packet too short for header.")
        return None
    header = packet_bytes[:HEADER_SIZE]
    payload = packet_bytes[HEADER_SIZE:]
    seq, ack, flags, length, checksum_rcvd = struct.unpack(HEADER_FORMAT, header)
    if length != len(payload):
        logging.warning(f"RUDP: Length mismatch. Header: {length}, Actual: {len(payload)}.")
        return None
    expected_checksum = checksum_calc((seq, ack, flags, length), payload)
    if checksum_rcvd != expected_checksum:
        logging.warning(f"RUDP: Checksum mismatch. Rcvd: {checksum_rcvd}, Calc: {expected_checksum}.")
        return None
    return seq, ack, flags, payload  # Return payload directly


class Socket:
    def __init__(self, loss_rate=0.0, corruption_rate=0.0, local_addr=None):
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except socket.error as e:
            logging.warning(f"RUDP: Could not set SO_REUSEADDR: {e}")

        self.peer_addr = None
        self.seq_num = random.randint(0, 0xFFFFFFFF)  # Our current/next sequence number
        self.ack_num = 0  # Ack number we send (next expected from peer)
        self.expected_seq_num = 0  # Next data/FIN sequence number we expect from peer

        self.loss_rate = loss_rate
        self.corruption_rate = corruption_rate

        self.state = CLOSED
        self.timeout_duration = TIMEOUT
        self.max_retries = RETRANSMISSIONS

        self.last_packet_sent_for_retransmission = None  # Stores (seq, ack, flags, payload_bytes) for retransmission
        self.current_retry_count = 0

        if local_addr:
            try:
                self.udp_socket.bind(local_addr)
                logging.info(f"RUDP: Socket bound to {local_addr}")
            except socket.error as e:
                logging.error(f"RUDP: Bind failed for {local_addr}: {e}")
                raise

    def _send_packet_internal(self, seq, ack, flags, payload=b'', addr_override=None, is_retransmission=False):
        target_addr = addr_override if addr_override else self.peer_addr
        if not target_addr:
            logging.error("RUDP: No target address for sending.")
            return False

        if not is_retransmission:  # New packet
            self.last_packet_sent_for_retransmission = (seq, ack, flags, payload)

        # Use stored values for retransmission if they exist
        current_seq, current_ack, current_flags, current_payload = self.last_packet_sent_for_retransmission

        packed_data = pack_packet(current_seq, current_ack, current_flags, current_payload)

        if self.loss_rate > 0 and random.random() < self.loss_rate:
            logging.info(f"RUDP: SIMULATED LOSS to {target_addr} (SEQ={current_seq}, Flags={current_flags})")
            return True  # Pretend sent

        packet_to_send = packed_data
        if self.corruption_rate > 0 and random.random() < self.corruption_rate:
            logging.info(f"RUDP: SIMULATING CORRUPTION to {target_addr} (SEQ={current_seq}, Flags={current_flags})")
            temp_list = bytearray(packet_to_send)
            if len(temp_list) > 0:
                idx = random.randint(0, len(temp_list) - 1)
                temp_list[idx] ^= 0xFF  # Flip bits of a random byte
                packet_to_send = bytes(temp_list)

        try:
            self.udp_socket.sendto(packet_to_send, target_addr)
            return True
        except socket.error as e:
            logging.error(f"RUDP: Socket error sending packet: {e}")
            return False

    def _receive_packet_internal(self, timeout_val=None):
        try:
            self.udp_socket.settimeout(timeout_val if timeout_val is not None else self.timeout_duration)
            packet_bytes, addr = self.udp_socket.recvfrom(HEADER_SIZE + MAX_PAYLOAD_SIZE)
            unpacked = unpack_packet(packet_bytes)
            if unpacked:
                return addr, unpacked  # Returns (addr, (seq, ack, flags, payload))
            return addr, None  # Checksum/length error
        except socket.timeout:
            return None, "timeout"
        except socket.error as e:
            logging.error(f"RUDP: Socket error receiving: {e}")
            return None, "error"

    def bind(self, address):  # Already present in user's code, ensure it's consistent
        try:
            self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp_socket.bind(address)
            logging.info(f"RUDP: Socket bound to {address}")
        except socket.error as e:
            logging.error(f"RUDP: Bind failed for {address}: {e}")
            raise

    def listen(self, backlog=1):  # backlog is for TCP, not directly used here
        if self.state == CLOSED:
            try:
                self.udp_socket.getsockname()  # Check if bound
                self.state = LISTEN
                logging.info(f"RUDP: Socket listening on {self.udp_socket.getsockname()}")
                return True
            except socket.error as e:  # Not bound or other issue
                logging.error(f"RUDP: Cannot listen, socket not properly bound or error: {e}")
                return False
        logging.error(f"RUDP: Cannot listen in state {self.state}")
        return False

    def accept(self):
        if self.state != LISTEN:
            logging.error("RUDP: Accept called but not in LISTEN state.")
            return None

        logging.info("RUDP: Server accepting connections...")
        while self.state == LISTEN:  # Loop to catch a valid SYN
            addr, event_data = self._receive_packet_internal(timeout_val=None)  # Blocking wait for SYN
            if event_data and event_data != "timeout" and event_data != "error":
                seq_rcvd, _, flags_rcvd, _ = event_data
                if flags_rcvd == SYN_FLAG:
                    self.peer_addr = addr
                    self.expected_seq_num = seq_rcvd + 1  # This is client's ISN + 1
                    self.ack_num = seq_rcvd + 1  # We will ACK their SYN
                    # Server's ISN is self.seq_num (from __init__)

                    logging.info(
                        f"RUDP: SYN from {self.peer_addr} (SEQ={seq_rcvd}). Sending SYN-ACK (OurSEQ={self.seq_num}, ACK={self.ack_num}).")
                    if self._send_packet_internal(self.seq_num, self.ack_num, SYNACK_FLAG):
                        self.state = SYN_RECEIVED
                        self.current_retry_count = 0
                    else:  # Failed to send SYN-ACK
                        self.peer_addr = None
                        continue

            # Inner loop for SYN_RECEIVED state
            while self.state == SYN_RECEIVED:
                if self.current_retry_count > self.max_retries:
                    logging.error("RUDP: Max retransmissions for SYN-ACK. Handshake failed.")
                    self.state = LISTEN
                    self.peer_addr = None
                    self.last_packet_sent_for_retransmission = None
                    break

                addr, event_data = self._receive_packet_internal()
                if event_data == "timeout":
                    logging.warning("RUDP: Timeout waiting for ACK of SYN-ACK. Retransmitting.")
                    self._send_packet_internal(0, 0, 0, is_retransmission=True)  # Retransmit last_packet_sent
                    self.current_retry_count += 1
                elif event_data and event_data != "error":
                    ack_seq_rcvd, ack_ack_num_rcvd, ack_flags_rcvd, _ = event_data
                    if self.peer_addr == addr and ack_flags_rcvd == ACK_FLAG and ack_ack_num_rcvd == (self.seq_num + 1):
                        logging.info(f"RUDP: ACK for SYN-ACK rcvd. ESTABLISHED with {self.peer_addr}.")
                        self.seq_num += 1  # Consume our SYN
                        # self.ack_num is already peer's ISN+1 (what we expect from them as data/FIN seq)
                        # self.expected_seq_num is peer's ISN+1 (what we expect from them as data/FIN seq)
                        self.state = ESTABLISHED
                        self.last_packet_sent_for_retransmission = None
                        return self
                    else:
                        logging.warning(f"RUDP: Unexpected packet in SYN_RECEIVED from {addr}.")
                elif event_data == "error":  # Socket error during recv
                    self.state = LISTEN
                    self.peer_addr = None
                    break
        return None  # Should only be reached if state changes from LISTEN unexpectedly

    def connect(self, server_addr):
        if self.state != CLOSED:
            logging.error("RUDP: Connect called but socket not closed.")
            return False

        self.peer_addr = server_addr
        self.state = SYN_SENT
        self.ack_num = 0  # Client's initial SYN has no ACK num for peer data
        self.expected_seq_num = 0  # Will be set upon receiving SYN-ACK

        logging.info(f"RUDP: Sending SYN to {self.peer_addr} (OurSEQ={self.seq_num}).")
        if not self._send_packet_internal(self.seq_num, self.ack_num, SYN_FLAG):
            self.state = CLOSED
            return False
        self.current_retry_count = 0

        while self.state == SYN_SENT:
            if self.current_retry_count > self.max_retries:
                logging.error("RUDP: Max retransmissions for SYN. Connection failed.")
                self.state = CLOSED
                self.last_packet_sent_for_retransmission = None
                return False

            addr, event_data = self._receive_packet_internal()
            if event_data == "timeout":
                logging.warning("RUDP: Timeout waiting for SYN-ACK. Retransmitting SYN.")
                self._send_packet_internal(0, 0, 0, is_retransmission=True)
                self.current_retry_count += 1
            elif event_data and event_data != "error":
                seq_rcvd, ack_num_rcvd, flags_rcvd, _ = event_data
                if self.peer_addr == addr and flags_rcvd == SYNACK_FLAG and ack_num_rcvd == (self.seq_num + 1):
                    logging.info(f"RUDP: SYN-ACK rcvd (TheirSEQ={seq_rcvd}, TheirACK={ack_num_rcvd}). Sending ACK.")
                    self.seq_num += 1  # Consume our SYN
                    self.ack_num = seq_rcvd + 1  # We ACK their SYN part
                    self.expected_seq_num = seq_rcvd + 1  # Expect their data/FIN to start with this
                    if self._send_packet_internal(self.seq_num, self.ack_num, ACK_FLAG):
                        self.state = ESTABLISHED
                        self.last_packet_sent_for_retransmission = None
                        logging.info(
                            f"RUDP: Connection ESTABLISHED. NextSendSEQ={self.seq_num}, ExpectPeerSEQ={self.expected_seq_num}")
                        return True
                    else:  # Failed to send final ACK
                        self.state = CLOSED
                        return False
                else:
                    logging.warning(f"RUDP: Unexpected packet in SYN_SENT from {addr}.")
            elif event_data == "error":
                self.state = CLOSED
                return False
        return False

    def send_data(self, data):
        if self.state != ESTABLISHED:
            logging.error(f"RUDP: Cannot send data in state {self.state}")
            return False

        # self.ack_num here should be the next sequence number we expect from the peer,
        # which is what we'd put in the ACK field of our data packet.
        logging.info(f"RUDP: Sending DATA (OurSEQ={self.seq_num}, ACKpiggyback={self.ack_num}, Len={len(data)}).")
        if not self._send_packet_internal(self.seq_num, self.ack_num, 0, data):  # Flag 0 for data
            return False
        self.current_retry_count = 0

        while True:  # Stop-and-Wait loop for this segment
            if self.current_retry_count > self.max_retries:
                logging.error(f"RUDP: Max retransmissions for DATA SEQ={self.seq_num}. Send failed.")
                self.last_packet_sent_for_retransmission = None
                return False

            addr, event_data = self._receive_packet_internal()
            if event_data == "timeout":
                logging.warning(f"RUDP: Timeout waiting for ACK of DATA SEQ={self.seq_num}. Retransmitting.")
                self._send_packet_internal(0, 0, 0, is_retransmission=True)
                self.current_retry_count += 1
            elif event_data and event_data != "error":
                resp_seq, resp_ack_num, resp_flags, resp_payload = event_data
                if self.peer_addr == addr and ((resp_flags & ACK_FLAG) or resp_ack_num == self.seq_num + 1):
                    logging.info(f"RUDP: ACK for DATA SEQ={self.seq_num} rcvd.")
                    self.seq_num += 1
                    self.last_packet_sent_for_retransmission = None
                    # Basic check for piggybacked data - project doesn't require full handling here
                    if not (resp_flags & (SYN_FLAG | FIN_FLAG)) and len(resp_payload) > 0:
                        if resp_seq == self.expected_seq_num:
                            logging.info(
                                f"RUDP: Piggybacked DATA rcvd with ACK (TheirSEQ={resp_seq}). Needs receive_data call.")
                            # For simplicity, assume receive_data() will be called by app to get this.
                            # A more robust S&W might buffer it or ACK it immediately.
                            # self.ack_num = resp_seq + 1 # Update if we were to process it here
                    return True
                else:
                    logging.warning(f"RUDP: Unexpected packet while waiting for ACK of DATA SEQ={self.seq_num}.")
            elif event_data == "error":
                return False
        return False

    def receive_data(self):
        if self.state not in [ESTABLISHED, CLOSE_WAIT]:
            logging.error(f"RUDP: Cannot receive data in state {self.state}")
            return None

        while True:  # Loop to get a valid data/FIN or timeout
            addr, event_data = self._receive_packet_internal()
            if event_data == "timeout":
                logging.debug("RUDP: Timeout in receive_data.")
                return None
            elif event_data == "error":
                return None  # Socket error
            elif event_data:
                seq_rcvd, ack_num_rcvd, flags_rcvd, payload_rcvd = event_data
                if self.peer_addr != addr: logging.warning("RUDP: Packet from unknown addr."); continue

                # Handle ACK piggybacked on data/FIN (updates our sender's view if it's for our data)
                if flags_rcvd & ACK_FLAG:
                    # If ack_num_rcvd == self.seq_num -1 (our last sent data's SEQ), it's relevant.
                    # send_data() primarily handles its own ACKs. This is for robustness.
                    logging.debug(
                        f"RUDP: receive_data got packet with ACK flag: TheirSEQ={seq_rcvd}, TheirACK={ack_num_rcvd}")

                # Is it DATA we expect?
                if not (flags_rcvd & (SYN_FLAG | FIN_FLAG)) and len(payload_rcvd) > 0:
                    if seq_rcvd == self.expected_seq_num:
                        logging.info(f"RUDP: DATA rcvd (SEQ={seq_rcvd}). Sending ACK.")
                        self.ack_num = seq_rcvd + 1  # This is the ACK we will send
                        self.expected_seq_num = seq_rcvd + 1  # Next data we expect
                        self._send_packet_internal(self.seq_num, self.ack_num,
                                                   ACK_FLAG)  # Our current send_seq, acking their data
                        return payload_rcvd
                    elif seq_rcvd < self.expected_seq_num:  # Duplicate
                        logging.warning(f"RUDP: Duplicate DATA (SEQ={seq_rcvd}). Re-ACKing.")
                        self._send_packet_internal(self.seq_num, seq_rcvd + 1, ACK_FLAG)  # Ack previous again
                        continue
                    else:  # Out of order
                        logging.warning(f"RUDP: Out-of-order DATA (SEQ={seq_rcvd}). Discarding.")
                        continue
                # Is it FIN we expect?
                elif flags_rcvd & FIN_FLAG:
                    if seq_rcvd == self.expected_seq_num:
                        logging.info(f"RUDP: FIN rcvd (SEQ={seq_rcvd}). Sending ACK.")
                        self.ack_num = seq_rcvd + 1
                        self.expected_seq_num = seq_rcvd + 1
                        self._send_packet_internal(self.seq_num, self.ack_num, ACK_FLAG)
                        if self.state == ESTABLISHED:
                            self.state = CLOSE_WAIT
                        elif self.state == FIN_WAIT2:
                            self.state = CLOSED  # TIME_WAIT omitted
                        self.last_packet_sent_for_retransmission = None
                        return b''  # EOF
                    elif seq_rcvd < self.expected_seq_num:  # Duplicate FIN
                        logging.warning(f"RUDP: Duplicate FIN (SEQ={seq_rcvd}). Re-ACKing.")
                        self._send_packet_internal(self.seq_num, seq_rcvd + 1, ACK_FLAG)
                        continue
                    else:
                        logging.warning(f"RUDP: Future FIN (SEQ={seq_rcvd}). Discarding."); continue
                # Standalone ACK? (send_data should handle its ACKs)
                elif flags_rcvd & ACK_FLAG:
                    logging.debug(f"RUDP: Standalone ACK rcvd in receive_data. Ignoring.")
                    continue
                else:
                    logging.debug(
                        f"RUDP: Unexpected packet type in receive_data. Flags={flags_rcvd}. Ignoring."); continue
        return None  # Should be covered by loop

    def close(self):
        logging.info(f"RUDP: Close called. Current state: {self.state}")
        if self.state == CLOSED: logging.info("RUDP: Already closed."); return True

        original_timeout = self.udp_socket.gettimeout()  # Store to restore later
        # self.udp_socket.settimeout(self.timeout_duration) # Ensure RUDP timeout for close ops

        # --- Active Close (from ESTABLISHED) ---
        if self.state == ESTABLISHED:
            self.state = FIN_WAIT1
            logging.info(
                f"RUDP: ESTABLISHED -> FIN_WAIT1. Sending FIN (OurSEQ={self.seq_num}, ACKpeer={self.ack_num}).")
            if not self._send_packet_internal(self.seq_num, self.ack_num,
                                              FIN_FLAG):  # ack_num is next expected from peer
                self.state = CLOSED
                self._cleanup_socket(original_timeout)
                return False
            self.current_retry_count = 0

        # --- FIN_WAIT_1: Sent FIN, waiting for ACK or peer's FIN ---
        if self.state == FIN_WAIT1:
            peer_fin_ackd_by_us = False
            while self.current_retry_count <= self.max_retries:
                addr, event_data = self._receive_packet_internal()
                if event_data == "timeout":
                    logging.warning("RUDP: FIN_WAIT1 timeout. Retransmitting FIN.")
                    self._send_packet_internal(0, 0, 0, is_retransmission=True)
                    self.current_retry_count += 1
                elif event_data and event_data != "error":
                    resp_seq, resp_ack, resp_flags, _ = event_data
                    if self.peer_addr == addr:
                        if resp_flags & ACK_FLAG and resp_ack == self.seq_num+1:  # ACK for our FIN
                            logging.info(f"RUDP: FIN_WAIT1: ACK for our FIN rcvd.")
                            self.seq_num += 1
                            if peer_fin_ackd_by_us:  # If we also processed their FIN
                                self.state = CLOSED
                                logging.info("RUDP: FIN_WAIT1 -> CLOSED (Simultaneous close done).")
                            else:
                                self.state = FIN_WAIT2
                                logging.info("RUDP: FIN_WAIT1 -> FIN_WAIT2.")
                            self.last_packet_sent_for_retransmission = None
                            break
                        elif resp_flags & FIN_FLAG and resp_seq == self.expected_seq_num:  # Peer's FIN
                            logging.info(f"RUDP: FIN_WAIT1: Peer's FIN rcvd (TheirSEQ={resp_seq}). Sending ACK.")
                            self.ack_num = resp_seq + 1
                            self.expected_seq_num = resp_seq + 1
                            self._send_packet_internal(self.seq_num, self.ack_num, ACK_FLAG)
                            peer_fin_ackd_by_us = True
                            # Stay in FIN_WAIT1, still need ACK for our FIN
                        else:
                            logging.warning(f"RUDP: FIN_WAIT1: Unexpected packet.")
                elif event_data == "error":
                    self.state = CLOSED; break
            if self.state == FIN_WAIT1: logging.error("RUDP: FIN_WAIT1 max retries/error."); self.state = CLOSED

        if self.state == FIN_WAIT2:
            self.current_retry_count = 0  # Reset for this stage
            logging.info(f"RUDP: FIN_WAIT2: Waiting for peer's FIN (ExpectingTheirSEQ={self.expected_seq_num}).")
            while self.current_retry_count <= self.max_retries:  # Using retries as a form of overall timeout here
                addr, event_data = self._receive_packet_internal()
                if event_data == "timeout":
                    logging.warning("RUDP: FIN_WAIT2 timeout waiting for peer's FIN.")
                    self.current_retry_count += 1  # Just count timeouts, no retransmission from us
                elif event_data and event_data != "error":
                    resp_seq, _, resp_flags, _ = event_data
                    if self.peer_addr == addr and resp_flags & FIN_FLAG and resp_seq == self.expected_seq_num+1:
                        logging.info(f"RUDP: FIN_WAIT2: Peer's FIN rcvd (TheirSEQ={resp_seq}). Sending final ACK.")
                        self.ack_num = resp_seq + 1
                        self.expected_seq_num = resp_seq + 1
                        self._send_packet_internal(self.seq_num, self.ack_num, ACK_FLAG)
                        self.state = CLOSED
                        logging.info("RUDP: FIN_WAIT2 -> CLOSED. Connection terminated.")
                        break
                    else:
                        logging.warning(f"RUDP: FIN_WAIT2: Unexpected packet.")
                elif event_data == "error":
                    self.state = CLOSED; break
            if self.state == FIN_WAIT2: logging.error("RUDP: FIN_WAIT2 failed to get peer's FIN."); self.state = CLOSED

        # --- Passive Close (from CLOSE_WAIT) ---
        elif self.state == CLOSE_WAIT:
            self.state = LAST_ACK
            logging.info(
                f"RUDP: CLOSE_WAIT -> LAST_ACK. Sending FIN (OurSEQ={self.seq_num}, ACKpeer={self.expected_seq_num}).")
            if not self._send_packet_internal(self.seq_num, self.expected_seq_num,
                                              FIN_FLAG):  # ack is next expected from peer
                self.state = CLOSED
                self._cleanup_socket(original_timeout)
                return False
            self.current_retry_count = 0

            while self.current_retry_count <= self.max_retries:
                addr, event_data = self._receive_packet_internal()
                if event_data == "timeout":
                    logging.warning("RUDP: LAST_ACK timeout. Retransmitting FIN.")
                    self._send_packet_internal(0, 0, 0, is_retransmission=True)
                    self.current_retry_count += 1
                elif event_data and event_data != "error":
                    _, resp_ack, resp_flags, _ = event_data
                    if self.peer_addr == addr and ((resp_flags & ACK_FLAG) or resp_ack == self.seq_num + 1):
                        logging.info("RUDP: LAST_ACK: ACK for our FIN rcvd.")
                        self.seq_num += 1
                        self.state = CLOSED
                        logging.info("RUDP: LAST_ACK -> CLOSED.")
                        break
                    else:
                        logging.warning(f"RUDP: LAST_ACK: Unexpected packet.")
                elif event_data == "error":
                    self.state = CLOSED; break
            if self.state == LAST_ACK: logging.error("RUDP: LAST_ACK max retries."); self.state = CLOSED

        elif self.state == LISTEN:  # Closing a listening socket
            logging.info("RUDP: Closing LISTEN socket.")
            self.state = CLOSED

        self._cleanup_socket(original_timeout)
        return self.state == CLOSED

    def _cleanup_socket(self, original_timeout_to_restore):
        if self.state != CLOSED:  # Ensure final state is CLOSED if not already
            logging.warning(f"RUDP: Forcing state to CLOSED from {self.state} during cleanup.")
            self.state = CLOSED
        self.last_packet_sent_for_retransmission = None
        if hasattr(self.udp_socket, 'settimeout') and callable(self.udp_socket.settimeout):
            try:
                self.udp_socket.settimeout(original_timeout_to_restore)
            except:
                pass  # Socket might be closed
        if hasattr(self.udp_socket, 'close') and callable(self.udp_socket.close) and self.udp_socket.fileno() != -1:
            try:
                self.udp_socket.close()
            except:
                pass
        logging.info(f"RUDP: Socket cleanup complete. Final state: {self.state}")

