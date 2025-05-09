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
    # Pack header fields for checksum calculation (checksum field is zero here)
    header_for_checksum = struct.pack('!IIBH', seq_num, ack_num, flags, length_of_data)

    packet_bytes = header_for_checksum + data_payload

    # If odd length, pad with a zero byte for checksum calculation
    if len(packet_bytes) % 2 != 0:
        packet_bytes += b'\x00'

    checksum = 0
    for i in range(0, len(packet_bytes), 2):
        word = (packet_bytes[i] << 8) + packet_bytes[i + 1]
        checksum += word
        # Add carry
        while checksum >> 16:
            checksum = (checksum & 0xFFFF) + (checksum >> 16)

    return (~checksum) & 0xFFFF  # One's complement


class RUDPsocket:
    def __init__(self, simulate_loss_rate=0.0, simulate_corruption_rate=0.0, local_addr=None):
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.peer_addr = None  # (host, port) of the connected peer

        # Sequence numbers
        self.seq_num = random.randint(0, 0xFFFFFFFF)  # Initial send sequence number (segment based)
        self.ack_num = 0  # Expected ack for our sent data / Next sequence number expected from peer
        self.expected_seq_num_from_peer = 0  # Next data segment sequence number expected from peer

        self.state = CLOSED
        self.timeout = DEFAULT_TIMEOUT
        self.max_retries = MAX_RETRANSMISSIONS

        self.simulate_loss_rate = simulate_loss_rate
        self.simulate_corruption_rate = simulate_corruption_rate

        self.receive_buffer = b""  # Buffer for reassembling segmented data (not used in this stop-and-wait per segment)
        self.last_received_ack_num_for_data = -1  # Tracks ACK for data segments we send
        self.last_sent_data_segment = None  # (header_tuple, payload) for retransmission
        self.last_sent_control_packet = None  # For retransmitting control packets like FIN/SYN
        self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if local_addr:
            self.udp_socket.bind(local_addr)
            logging.info(f"Socket bound to {local_addr}")

    def _pack_packet(self, seq, ack, flags, payload=b''):
        """Packs an RUDP packet."""
        if len(payload) > PAYLOAD_MSS:
            raise ValueError(f"Payload size {len(payload)} exceeds MSS {PAYLOAD_MSS}")

        length = len(payload)
        # Calculate checksum with checksum field as 0
        checksum = calculate_checksum((seq, ack, flags, length), payload)

        header = struct.pack(RUDP_HEADER_FORMAT, seq, ack, flags, length, checksum)
        return header + payload

    def _unpack_packet(self, packet_bytes):
        """Unpacks an RUDP packet and verifies checksum."""
        if len(packet_bytes) < RUDP_HEADER_LENGTH:
            logging.warning("Received packet too short for RUDP header.")
            return None  # Packet too short

        header = packet_bytes[:RUDP_HEADER_LENGTH]
        payload = packet_bytes[RUDP_HEADER_LENGTH:]

        seq, ack_val, flags, length, checksum_recv = struct.unpack(RUDP_HEADER_FORMAT, header)

        if length != len(payload):
            logging.warning(f"Packet length mismatch: header says {length}, actual payload is {len(payload)}.")
            return None  # Length mismatch

        # Verify checksum
        expected_checksum = calculate_checksum((seq, ack_val, flags, length), payload)
        if checksum_recv != expected_checksum:
            logging.warning(
                f"Checksum mismatch: received {checksum_recv}, calculated {expected_checksum}. Packet dropped.")
            return None  # Checksum error

        return seq, ack_val, flags, payload

    def _send_raw_packet(self, packet_bytes, addr):
        """Applies simulation rules and sends the packet via UDP."""
        if self.simulate_loss_rate > 0 and random.random() < self.simulate_loss_rate:
            logging.info(f"SIMULATED PACKET LOSS to {addr}")
            return len(packet_bytes)  # Pretend it was sent

        if self.simulate_corruption_rate > 0 and random.random() < self.simulate_corruption_rate:
            logging.info(f"SIMULATING CHECKSUM CORRUPTION for packet to {addr}")
            # Corrupt by flipping a bit in the checksum (last two bytes)
            # This is a simplistic corruption. A real corruption could be anywhere.
            if len(packet_bytes) >= RUDP_HEADER_LENGTH:
                # Corrupt the checksum part of the header
                header_list = list(packet_bytes[:RUDP_HEADER_LENGTH])
                # Example: flip a bit in the first byte of checksum
                header_list[-2] = header_list[-2] ^ 0x01  # XOR with 00000001
                corrupted_header = bytes(header_list)
                packet_bytes = corrupted_header + packet_bytes[RUDP_HEADER_LENGTH:]
            else:  # very short packet, just corrupt first byte if possible
                if packet_bytes:
                    packet_list = list(packet_bytes)
                    packet_list[0] = packet_list[0] ^ 0x01
                    packet_bytes = bytes(packet_list)

        return self.udp_socket.sendto(packet_bytes, addr)

    def bind(self, address):
        self.udp_socket.bind(address)
        logging.info(f"Socket bound to {address}")

    def listen(self, backlog=1):  # Backlog not really used like TCP
        if self.state == CLOSED:
            self.state = LISTEN
            logging.info(f"Socket listening on {self.udp_socket.getsockname()}")
        else:
            logging.error("Socket is not closed, cannot listen.")
            raise ConnectionError("Socket not closed")

    def accept(self):
        """Waits for a client connection (3-way handshake). Returns new 'connection' info or self."""
        if self.state != LISTEN:
            logging.error("Socket is not in LISTEN state.")
            raise ConnectionError("Socket not listening")

        logging.info("Accepting connections...")
        self.udp_socket.settimeout(None)  # Blocking wait for SYN

        while True:  # Loop to wait for a valid SYN
            try:
                syn_packet, client_addr = self.udp_socket.recvfrom(PAYLOAD_MSS + RUDP_HEADER_LENGTH)
                unpacked = self._unpack_packet(syn_packet)
                if unpacked:
                    seq_rcvd, _, flags_rcvd, _ = unpacked
                    if flags_rcvd == SYN_FLAG:
                        logging.info(f"SYN received from {client_addr} with SEQ={seq_rcvd}.")
                        self.peer_addr = client_addr
                        self.expected_seq_num_from_peer = seq_rcvd + 1  # Expect data or FIN after handshake
                        self.ack_num = seq_rcvd + 1  # Acknowledging their SYN

                        # Send SYN-ACK
                        self.seq_num = random.randint(0, 0xFFFFFFFF)  # Our initial sequence number for this connection
                        synack_pkt = self._pack_packet(self.seq_num, self.ack_num, SYNACK_FLAG)
                        self._send_raw_packet(synack_pkt, self.peer_addr)
                        logging.info(f"Sent SYN-ACK to {self.peer_addr} with SEQ={self.seq_num}, ACK={self.ack_num}.")
                        self.state = SYN_RCVD

                        # Wait for ACK for our SYN-ACK
                        self.udp_socket.settimeout(self.timeout)
                        for _ in range(self.max_retries + 1):  # +1 attempt for initial send
                            try:
                                ack_packet, addr = self.udp_socket.recvfrom(RUDP_HEADER_LENGTH)
                                if addr != self.peer_addr: continue  # Packet from wrong peer

                                unpacked_ack = self._unpack_packet(ack_packet)
                                if unpacked_ack:
                                    ack_seq, ack_ack_val, ack_flags, _ = unpacked_ack
                                    # We expect an ACK for our SYN's sequence number + 1
                                    # And their sequence number should be our ACK + 1 (or just our ACK)
                                    if ack_flags == ACK_FLAG and ack_ack_val == (self.seq_num + 1):
                                        logging.info(
                                            f"ACK received for SYN-ACK from {self.peer_addr}. ACK_VAL={ack_ack_val}.")
                                        self.state = ESTABLISHED
                                        self.seq_num += 1  # Our SYN is consumed
                                        # self.expected_seq_num_from_peer is already set
                                        logging.info(f"Connection ESTABLISHED with {self.peer_addr}.")
                                        logging.info(
                                            f"Server: Next send SEQ={self.seq_num}, Next expected SEQ from client={self.expected_seq_num_from_peer}")
                                        return self  # Or a new socket object if we were threading
                                    else:
                                        logging.warning(
                                            f"Received unexpected ACK in SYN_RCVD: Flags={ack_flags}, ACK_VAL={ack_ack_val}, Expected ACK_VAL={self.seq_num + 1}")
                            except socket.timeout:
                                logging.warning("Timeout waiting for ACK of SYN-ACK. Retransmitting SYN-ACK.")
                                self._send_raw_packet(synack_pkt, self.peer_addr)  # Resend SYN-ACK

                        logging.error("Failed to establish connection after multiple retries (no ACK for SYN-ACK).")
                        self.state = LISTEN  # Go back to listening
                        self.peer_addr = None
                        return None  # Or raise error
            except socket.timeout:  # Should not happen if timeout is None initially
                continue
            except Exception as e:
                logging.error(f"Error during accept: {e}")
                continue  # Keep listening
        return None  # Should not be reached if listen is indefinite

    def connect(self, address):
        """Establishes a connection with the server (3-way handshake)."""
        if self.state != CLOSED:
            logging.error("Socket is not closed, cannot connect.")
            raise ConnectionError("Socket not closed")

        self.peer_addr = address
        self.state = SYN_SENT

        # Send SYN
        # self.seq_num is already initialized
        syn_pkt = self._pack_packet(self.seq_num, 0, SYN_FLAG)
        self._send_raw_packet(syn_pkt, self.peer_addr)
        logging.info(f"Sent SYN to {self.peer_addr} with SEQ={self.seq_num}.")

        self.udp_socket.settimeout(self.timeout)
        for i in range(self.max_retries + 1):
            try:
                synack_packet, server_addr = self.udp_socket.recvfrom(PAYLOAD_MSS + RUDP_HEADER_LENGTH)
                if server_addr != self.peer_addr:
                    logging.warning(f"Received packet from unexpected address {server_addr}")
                    continue

                unpacked = self._unpack_packet(synack_packet)
                if unpacked:
                    seq_rcvd, ack_rcvd, flags_rcvd, _ = unpacked
                    if flags_rcvd == SYNACK_FLAG and ack_rcvd == (self.seq_num + 1):
                        logging.info(f"SYN-ACK received from {self.peer_addr} with SEQ={seq_rcvd}, ACK={ack_rcvd}.")
                        self.ack_num = seq_rcvd + 1  # This is the server's ISN + 1
                        self.expected_seq_num_from_peer = seq_rcvd + 1  # Server's first data/FIN will have this seq_num

                        # Send ACK for SYN-ACK
                        ack_pkt = self._pack_packet(self.seq_num + 1, self.ack_num, ACK_FLAG)  # Our SEQ increments
                        self._send_raw_packet(ack_pkt, self.peer_addr)
                        logging.info(
                            f"Sent ACK for SYN-ACK to {self.peer_addr} with SEQ={self.seq_num + 1}, ACK={self.ack_num}.")

                        self.state = ESTABLISHED
                        self.seq_num += 1  # Our SYN is consumed
                        logging.info(f"Connection ESTABLISHED with {self.peer_addr}.")
                        logging.info(
                            f"Client: Next send SEQ={self.seq_num}, Next expected SEQ from server={self.expected_seq_num_from_peer}")
                        return True
                    else:
                        logging.warning(
                            f"Received unexpected packet in SYN_SENT: Flags={flags_rcvd}, ACK={ack_rcvd}, Expected ACK for our SEQ+1={self.seq_num + 1}")
            except socket.timeout:
                if i < self.max_retries:
                    logging.warning("Timeout waiting for SYN-ACK. Retransmitting SYN.")
                    self._send_raw_packet(syn_pkt, self.peer_addr)  # Resend SYN
                else:
                    logging.error("Connection failed: No SYN-ACK received after multiple retries.")
                    self.state = CLOSED
                    self.peer_addr = None
                    return False
            except Exception as e:
                logging.error(f"Error during connect: {e}")
                self.state = CLOSED
                self.peer_addr = None
                return False

        self.state = CLOSED  # Fallback
        return False

    def send_data(self, data_payload):
        """Sends a single data segment using Stop-and-Wait."""
        if self.state != ESTABLISHED:
            logging.error("Cannot send data: Connection not established.")
            return False

        if not data_payload:
            logging.warning("send_data called with empty payload.")
            return True  # Nothing to send

        # self.seq_num is the sequence number for this new data segment
        data_pkt_header_tuple = (self.seq_num, self.ack_num, 0, len(data_payload))  # ACK num is last acked from peer
        data_pkt = self._pack_packet(self.seq_num, self.ack_num, 0, data_payload)  # Flags = 0 for data
        self.last_sent_data_segment = (data_pkt_header_tuple, data_payload)

        self.udp_socket.settimeout(self.timeout)
        for i in range(self.max_retries + 1):
            self._send_raw_packet(data_pkt, self.peer_addr)
            logging.info(
                f"Sent DATA to {self.peer_addr}: SEQ={self.seq_num}, ACK={self.ack_num}, LEN={len(data_payload)}")

            try:
                ack_packet, addr = self.udp_socket.recvfrom(RUDP_HEADER_LENGTH)
                if addr != self.peer_addr:
                    continue

                unpacked_ack = self._unpack_packet(ack_packet)
                if unpacked_ack:
                    ack_seq, ack_ack_val, ack_flags, _ = unpacked_ack
                    # We expect an ACK for the data segment we just sent (self.seq_num + 1 is wrong here for S&W)
                    # The ACK number should acknowledge the sequence number of the data packet sent.
                    # For stop-and-wait, the ACK value should be our self.seq_num.
                    # TCP ACKs next expected byte. For segment S&W, ACKing current segment's number is simpler.
                    # Let's adopt: ACK value = sequence number of the packet being ACKed.
                    if ack_flags == ACK_FLAG and ack_ack_val == self.seq_num:
                        logging.info(
                            f"Received ACK for DATA: SEQ_RCVD={ack_seq}, ACK_VAL={ack_ack_val} (matches our SEQ={self.seq_num})")
                        self.seq_num += 1  # Move to next sequence number for next send_data call
                        self.last_sent_data_segment = None
                        return True
                    else:
                        logging.warning(
                            f"Received unexpected ACK/packet: Flags={ack_flags}, ACK_VAL={ack_ack_val}. Expected ACK for SEQ={self.seq_num}")
                        # It could be a data packet from the other side, or a duplicate ACK.
                        # This simple S&W doesn't handle simultaneous data transfer well without more state.
                        # For now, we are strictly waiting for the ACK of our data.
            except socket.timeout:
                if i < self.max_retries:
                    logging.warning(f"Timeout waiting for ACK of DATA (SEQ={self.seq_num}). Retransmitting.")
                else:
                    logging.error(
                        f"Failed to send DATA (SEQ={self.seq_num}): No ACK received after {self.max_retries} retries.")
                    self.state = CLOSED  # Or some error state
                    return False
            except Exception as e:
                logging.error(f"Error during send_data's ACK wait: {e}")
                return False
        return False  # Should be covered by loop/timeout logic

    def receive_data(self):
        """Receives a single data segment. Handles ACKing."""
        if self.state != ESTABLISHED and self.state != CLOSE_WAIT:  # Can receive data while in CLOSE_WAIT
            logging.error(f"Cannot receive data: Connection not established or in wrong state ({self.state}).")
            return None

        self.udp_socket.settimeout(self.timeout * (self.max_retries + 2))  # Longer timeout for receiving general data

        while True:  # Loop to get a valid data packet
            try:
                packet, addr = self.udp_socket.recvfrom(PAYLOAD_MSS + RUDP_HEADER_LENGTH)
                if addr != self.peer_addr:
                    logging.warning(f"receive_data: Discarding packet from unknown source {addr}")
                    continue

                unpacked = self._unpack_packet(packet)
                if unpacked:
                    seq_rcvd, ack_val_rcvd, flags_rcvd, payload_rcvd = unpacked

                    # Handle ACKs for our data if this is an ACK packet piggybacked or standalone
                    if flags_rcvd & ACK_FLAG:
                        # This part is tricky if we are also sending.
                        # For pure receive, we are focused on data packets from peer.
                        # If this ACK is for our last sent data, update our sender state (not handled here directly)
                        logging.debug(f"receive_data: Saw ACK flag, ACK_VAL={ack_val_rcvd}")

                    if not (flags_rcvd & (SYN_FLAG | FIN_FLAG)):  # It's a data packet (flags == 0 or just ACK)
                        if seq_rcvd == self.expected_seq_num_from_peer:
                            logging.info(
                                f"Received DATA from {self.peer_addr}: SEQ={seq_rcvd}, LEN={len(payload_rcvd)}. Expected SEQ={self.expected_seq_num_from_peer}")

                            # Send ACK for received data
                            # The ACK number acknowledges the sequence number of the data packet received.
                            ack_to_send = seq_rcvd
                            # Our sequence number for the ACK packet itself doesn't advance our data stream.
                            # Use current self.seq_num for the ACK packet's own sequence number.
                            ack_pkt = self._pack_packet(self.seq_num, ack_to_send, ACK_FLAG)
                            self._send_raw_packet(ack_pkt, self.peer_addr)
                            logging.info(f"Sent ACK for received data: Our_SEQ={self.seq_num}, ACK_VAL={ack_to_send}")

                            self.expected_seq_num_from_peer += 1  # Expect next segment
                            self.ack_num = ack_to_send  # Update last ack sent for their data
                            return payload_rcvd
                        elif seq_rcvd < self.expected_seq_num_from_peer:
                            logging.warning(
                                f"Received DUPLICATE/OLD DATA: SEQ={seq_rcvd} (Expected {self.expected_seq_num_from_peer}). Re-ACKing.")
                            # Re-send ACK for the segment they are likely retransmitting
                            ack_to_send = seq_rcvd
                            ack_pkt = self._pack_packet(self.seq_num, ack_to_send, ACK_FLAG)
                            self._send_raw_packet(ack_pkt, self.peer_addr)
                            # Do not return payload, as it's a duplicate
                        else:  # seq_rcvd > self.expected_seq_num_from_peer
                            logging.warning(
                                f"Received OUT-OF-ORDER DATA: SEQ={seq_rcvd} (Expected {self.expected_seq_num_from_peer}). Discarding.")
                            # In stop-and-wait, we discard out-of-order packets. Sender will time out for the expected one.

                    elif flags_rcvd & FIN_FLAG:  # Handle FIN if received during data phase
                        logging.info(f"FIN received from {self.peer_addr} while expecting data: SEQ={seq_rcvd}")
                        if seq_rcvd == self.expected_seq_num_from_peer:
                            self.expected_seq_num_from_peer += 1  # Consume FIN's sequence number
                            ack_for_fin = seq_rcvd
                            # Send ACK for FIN
                            ack_pkt = self._pack_packet(self.seq_num, ack_for_fin, ACK_FLAG)
                            self._send_raw_packet(ack_pkt, self.peer_addr)
                            logging.info(f"Sent ACK for peer's FIN: Our_SEQ={self.seq_num}, ACK_VAL={ack_for_fin}")

                            if self.state == ESTABLISHED:
                                self.state = CLOSE_WAIT
                                logging.info("State changed to CLOSE_WAIT.")
                            elif self.state == FIN_WAIT_2:  # Our FIN was already ACKed, now they send FIN
                                self.state = TIME_WAIT  # Or CLOSED if not implementing TIME_WAIT fully
                                logging.info("Both FINs exchanged. Connection closing.")
                                # For simplicity, go to CLOSED after short delay or immediately
                                # time.sleep(0.1) # Simulate TIME_WAIT
                                # self.state = CLOSED
                            return b''  # Signal EOF / FIN received
                        elif seq_rcvd < self.expected_seq_num_from_peer:  # Duplicate FIN
                            logging.warning(f"Received duplicate FIN (SEQ={seq_rcvd}). Re-ACKing.")
                            ack_pkt = self._pack_packet(self.seq_num, seq_rcvd, ACK_FLAG)
                            self._send_raw_packet(ack_pkt, self.peer_addr)

                    # else: other flags or combinations not handled in this simplified receive_data focus
                else:  # Checksum error or malformed packet
                    logging.debug("receive_data: Unpack failed (checksum error or malformed). Packet dropped.")
                    # No ACK sent for bad packets

            except socket.timeout:
                logging.warning("Timeout waiting for data in receive_data.")
                return None  # Indicate timeout
            except Exception as e:
                logging.error(f"Error during receive_data: {e}")
                return None
        return None  # Should not be reached

    def close(self):
        """Closes the RUDP connection gracefully."""
        if self.state == ESTABLISHED:  # We are initiating the close
            self.state = FIN_WAIT_1
            fin_pkt_header = (self.seq_num, self.ack_num, FIN_FLAG)  # ack_num is last acked from peer
            fin_pkt = self._pack_packet(self.seq_num, self.ack_num, FIN_FLAG)
            self.last_sent_control_packet = fin_pkt

            self.udp_socket.settimeout(self.timeout)
            for i in range(self.max_retries + 1):
                self._send_raw_packet(fin_pkt, self.peer_addr)
                logging.info(f"Sent FIN to {self.peer_addr}: SEQ={self.seq_num}, ACK={self.ack_num}")
                try:
                    ack_packet, addr = self.udp_socket.recvfrom(RUDP_HEADER_LENGTH)
                    if addr != self.peer_addr: continue

                    unpacked_ack = self._unpack_packet(ack_packet)
                    if unpacked_ack:
                        ack_seq, ack_ack_val, ack_flags, _ = unpacked_ack
                        if ack_flags == ACK_FLAG and ack_ack_val == self.seq_num:  # ACK for our FIN
                            logging.info(f"Received ACK for our FIN from {self.peer_addr}: ACK_VAL={ack_ack_val}")
                            self.seq_num += 1  # Our FIN is consumed
                            self.state = FIN_WAIT_2
                            self.last_sent_control_packet = None
                            break  # Move to wait for their FIN
                        # Potentially handle piggybacked FIN+ACK here too
                        elif ack_flags == (FIN_FLAG | ACK_FLAG) and ack_ack_val == self.seq_num:
                            logging.info(
                                f"Received FIN+ACK for our FIN from {self.peer_addr}: Their_SEQ={ack_seq}, ACK_VAL={ack_ack_val}")
                            self.seq_num += 1  # Our FIN consumed
                            self.expected_seq_num_from_peer = ack_seq + 1  # Their FIN consumed

                            # Send final ACK for their FIN
                            final_ack_pkt = self._pack_packet(self.seq_num, self.expected_seq_num_from_peer - 1,
                                                              ACK_FLAG)
                            self._send_raw_packet(final_ack_pkt, self.peer_addr)
                            logging.info(
                                f"Sent final ACK for peer's FIN+ACK: Our_SEQ={self.seq_num}, ACK_VAL={self.expected_seq_num_from_peer - 1}")
                            self.state = TIME_WAIT  # Or directly to CLOSED
                            logging.info("Connection closed (FIN+ACK received). State: TIME_WAIT -> CLOSED")
                            # time.sleep(0.1) # Simulate TIME_WAIT
                            self.state = CLOSED
                            self.udp_socket.close()
                            return True
                    else:  # Checksum error on ACK
                        logging.warning("Checksum error on ACK for FIN. Retrying FIN.")

                except socket.timeout:
                    if i < self.max_retries:
                        logging.warning("Timeout waiting for ACK of FIN. Retransmitting FIN.")
                    else:
                        logging.error("Failed to get ACK for FIN after multiple retries.")
                        self.state = CLOSED  # Force close
                        self.udp_socket.close()
                        return False

            # Now in FIN_WAIT_2, wait for their FIN
            if self.state == FIN_WAIT_2:
                logging.info("In FIN_WAIT_2, waiting for peer's FIN.")
                self.udp_socket.settimeout(self.timeout * (self.max_retries + 2))  # Longer wait
                for _ in range(self.max_retries + 1):  # Allow for peer to take time
                    try:
                        peer_fin_packet, addr = self.udp_socket.recvfrom(RUDP_HEADER_LENGTH + PAYLOAD_MSS)
                        if addr != self.peer_addr: continue

                        unpacked_fin = self._unpack_packet(peer_fin_packet)
                        if unpacked_fin:
                            fin_seq, _, fin_flags, _ = unpacked_fin
                            if fin_flags & FIN_FLAG and fin_seq == self.expected_seq_num_from_peer:
                                logging.info(f"Received FIN from peer: SEQ={fin_seq}")
                                self.expected_seq_num_from_peer += 1  # Consume their FIN

                                # Send final ACK
                                final_ack_pkt = self._pack_packet(self.seq_num, fin_seq, ACK_FLAG)
                                self._send_raw_packet(final_ack_pkt, self.peer_addr)
                                logging.info(
                                    f"Sent final ACK for peer's FIN: Our_SEQ={self.seq_num}, ACK_VAL={fin_seq}")

                                self.state = TIME_WAIT  # Transition to TIME_WAIT
                                logging.info("Connection closing. State: TIME_WAIT -> CLOSED")
                                # time.sleep(0.1) # Simulate TIME_WAIT briefly
                                self.state = CLOSED
                                self.udp_socket.close()
                                return True
                            elif fin_flags & FIN_FLAG and fin_seq < self.expected_seq_num_from_peer:  # Duplicate FIN
                                logging.warning(f"Received duplicate peer FIN (SEQ={fin_seq}). Re-ACKing.")
                                final_ack_pkt = self._pack_packet(self.seq_num, fin_seq, ACK_FLAG)
                                self._send_raw_packet(final_ack_pkt, self.peer_addr)

                    except socket.timeout:
                        logging.warning("Timeout waiting for peer's FIN in FIN_WAIT_2.")
                        # If peer doesn't send FIN, we eventually give up.
                logging.error("Did not receive FIN from peer in FIN_WAIT_2. Forcing close.")
                self.state = CLOSED
                self.udp_socket.close()
                return False


        elif self.state == CLOSE_WAIT:  # Peer initiated close, we ACKed their FIN, now we send our FIN
            self.state = LAST_ACK
            # self.seq_num is our current sequence for sending.
            # self.ack_num should be their FIN's seq + 1 (already set if ack_for_fin was correct)
            fin_pkt = self._pack_packet(self.seq_num, self.ack_num, FIN_FLAG)
            self.last_sent_control_packet = fin_pkt

            self.udp_socket.settimeout(self.timeout)
            for i in range(self.max_retries + 1):
                self._send_raw_packet(fin_pkt, self.peer_addr)
                logging.info(f"Sent our FIN (in CLOSE_WAIT): SEQ={self.seq_num}, ACK={self.ack_num}")
                try:
                    ack_packet, addr = self.udp_socket.recvfrom(RUDP_HEADER_LENGTH)
                    if addr != self.peer_addr: continue

                    unpacked_ack = self._unpack_packet(ack_packet)
                    if unpacked_ack:
                        _, ack_ack_val, ack_flags, _ = unpacked_ack
                        if ack_flags == ACK_FLAG and ack_ack_val == self.seq_num:  # ACK for our FIN
                            logging.info(f"Received ACK for our FIN (in LAST_ACK): ACK_VAL={ack_ack_val}")
                            self.seq_num += 1  # Our FIN consumed
                            self.state = CLOSED
                            self.last_sent_control_packet = None
                            logging.info("Connection closed successfully.")
                            self.udp_socket.close()
                            return True
                except socket.timeout:
                    if i < self.max_retries:
                        logging.warning("Timeout waiting for ACK of our FIN (in LAST_ACK). Retransmitting FIN.")
                    else:
                        logging.error("Failed to get ACK for our FIN in LAST_ACK. Forcing close.")
                        self.state = CLOSED
                        self.udp_socket.close()
                        return False
            logging.error("Exited FIN retransmission loop in LAST_ACK without success.")
            self.state = CLOSED
            self.udp_socket.close()
            return False

        elif self.state == CLOSED:
            logging.info("Connection already closed.")
            self.udp_socket.close()  # Ensure underlying socket is closed
            return True
        else:
            logging.warning(f"Close called in unexpected state: {self.state}. Forcing close.")
            self.state = CLOSED
            self.udp_socket.close()
            return False
        return True  # Fallback
