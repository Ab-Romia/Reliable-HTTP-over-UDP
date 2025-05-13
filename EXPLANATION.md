# Building Your RUDP and HTTP System: A Pseudocode Guide

This guide breaks down the provided `rudp.py`, `http_server.py`, and `http_client.py` files into pseudocode and explanations. It aims to clarify the logic behind each component so you can implement and understand the system effectively.

## Part 1: `rudp.py` - The Reliable UDP Layer

**Overall Purpose:**
The `rudp.py` module implements a `Socket` class that provides a TCP-like reliable data transfer service on top of the unreliable UDP protocol. It handles connection setup (3-way handshake), data transfer with acknowledgments and retransmissions (Stop-and-Wait), error detection (checksums), and connection termination (FIN exchange).

---

### 1.1. Module-Level Constants and Helper Functions

**Location:** At the beginning of `rudp.py`.

**Purpose:**
* Define constants for packet structure, flags, states, and operational parameters.
* Provide utility functions for checksum calculation, and packing/unpacking RUDP packets.

**Constants:**
* `MAX_PAYLOAD_SIZE`: Maximum data bytes per RUDP segment.
* `HEADER_FORMAT`, `HEADER_SIZE`: Define the structure of the RUDP header for packing/unpacking.
* `SYN_FLAG`, `ACK_FLAG`, `FIN_FLAG`, `SYNACK_FLAG`: Bitmasks for packet control flags.
* `TIMEOUT`, `RETRANSMISSIONS`: Default values for timeout and retry attempts.
* `CLOSED`, `LISTEN`, ..., `TIME_WAIT`: Integer constants representing RUDP connection states.

**Helper Function: `checksum_calc(header_tuple, data)`**
* **Purpose:** Calculates the 16-bit Internet checksum for a given set of header fields and payload data.
* **Pseudocode:**
    ```
    FUNCTION checksum_calc(header_fields_to_sum, payload_data_bytes):
        1. EXTRACT seq, ack, flags, length FROM header_fields_to_sum.
        2. CREATE temporary_header_bytes by packing (seq, ack, flags, length) using a format string for these fields only.
        3. COMBINE temporary_header_bytes and payload_data_bytes into `full_packet_bytes`.
        4. IF length of `full_packet_bytes` is odd, APPEND a null byte (b'\x00').
        5. INITIALIZE `sum_val` = 0.
        6. FOR each 2-byte word in `full_packet_bytes`:
            ADD word to `sum_val`.
        7. WHILE `sum_val` has bits beyond the 16th bit (carry exists):
            `sum_val` = (lower 16 bits of `sum_val`) + (bits of `sum_val` shifted right by 16).
        8. RETURN 1's complement of `sum_val` (masked to 16 bits).
    ```

**Helper Function: `pack_packet(seq, ack, flags, payload=b'')`**
* **Purpose:** Creates a complete RUDP packet (header + payload) in byte format.
* **Pseudocode:**
    ```
    FUNCTION pack_packet(sequence_num, ack_num, flags_val, payload_bytes):
        1. IF length of payload_bytes > MAX_PAYLOAD_SIZE, RAISE ValueError.
        2. payload_length = length of payload_bytes.
        3. calculated_checksum = CALL checksum_calc((sequence_num, ack_num, flags_val, payload_length), payload_bytes).
        4. CREATE RUDP_header_bytes by packing (sequence_num, ack_num, flags_val, payload_length, calculated_checksum) using HEADER_FORMAT.
        5. RETURN RUDP_header_bytes + payload_bytes.
    ```

**Helper Function: `unpack_packet(packet_bytes)`**
* **Purpose:** Parses received bytes into RUDP header fields and payload, validating checksum and length.
* **Pseudocode:**
    ```
    FUNCTION unpack_packet(raw_packet_bytes):
        1. IF length of raw_packet_bytes < HEADER_SIZE, LOG warning, RETURN None.
        2. EXTRACT header_part from raw_packet_bytes.
        3. EXTRACT payload_part from raw_packet_bytes.
        4. UNPACK header_part using HEADER_FORMAT into seq, ack, flags, length_from_header, checksum_from_packet.
        5. IF length_from_header is not equal to actual length of payload_part, LOG warning, RETURN None.
        6. calculated_expected_checksum = CALL checksum_calc((seq, ack, flags, length_from_header), payload_part).
        7. IF checksum_from_packet is not equal to calculated_expected_checksum, LOG warning, RETURN None.
        8. RETURN (seq, ack, flags, payload_part).
    ```

---

### 1.2. `Socket` Class

**Purpose:** The main class providing RUDP functionalities.

#### `__init__(self, loss_rate=0.0, corruption_rate=0.0, local_addr=None)`
* **Purpose:** Initializes a new RUDP socket.
* **Pseudocode:**
    ```
    METHOD __init__(loss_simulation_rate, corruption_simulation_rate, local_bind_address):
        1. CREATE self.udp_socket (standard UDP datagram socket).
        2. TRY to SET socket option SO_REUSEADDR on self.udp_socket.
        3. INITIALIZE self.peer_addr = None.
        4. INITIALIZE self.seq_num = random integer (this is our Initial Sequence Number - ISN).
        5. INITIALIZE self.ack_num = 0 (ACK number we will send; also tracks next expected from peer in some contexts).
        6. INITIALIZE self.expected_seq_num = 0 (strictly the next data/FIN SEQ we expect from peer).
        7. STORE loss_simulation_rate, corruption_simulation_rate.
        8. SET self.state = CLOSED.
        9. SET self.timeout_duration = TIMEOUT constant.
        10. SET self.max_retries = RETRANSMISSIONS constant.
        11. INITIALIZE self.last_packet_sent_for_retransmission = None (stores tuple: seq, ack, flags, payload).
        12. INITIALIZE self.current_retry_count = 0.
        13. IF local_bind_address is provided:
            BIND self.udp_socket to local_bind_address. LOG success or RAISE error on failure.
    ```

#### `_send_packet_internal(self, seq, ack, flags, payload=b'', addr_override=None, is_retransmission=False)`
* **Purpose:** Internal helper to send or re-send an RUDP packet, applying simulation rules.
* **Pseudocode:**
    ```
    METHOD _send_packet_internal(input_seq, input_ack, input_flags, input_payload, target_address_override, is_retransmit_flag):
        1. DETERMINE target_address (use target_address_override if provided, else self.peer_addr).
        2. IF no target_address, LOG error, RETURN False.

        3. IF is_retransmit_flag is False (it's a new packet):
            STORE (input_seq, input_ack, input_flags, input_payload) in self.last_packet_sent_for_retransmission.
        
        4. RETRIEVE actual_seq, actual_ack, actual_flags, actual_payload FROM self.last_packet_sent_for_retransmission.
        5. CREATE packet_byte_data by CALLING pack_packet(actual_seq, actual_ack, actual_flags, actual_payload).

        6. IF loss simulation is enabled AND random chance occurs:
            LOG simulated loss. RETURN True (pretend sent).
        
        7. current_packet_to_send = packet_byte_data.
        8. IF corruption simulation is enabled AND random chance occurs:
            LOG simulated corruption.
            CREATE a corrupted version of current_packet_to_send (e.g., flip a byte).
            current_packet_to_send = corrupted version.
        
        9. TRY to SEND current_packet_to_send to target_address using self.udp_socket.sendto().
        10. RETURN True on success, False on socket error (LOG error).
    ```

#### `_receive_packet_internal(self, timeout_val=None)`
* **Purpose:** Internal helper to receive a packet, set timeout, and unpack it.
* **Pseudocode:**
    ```
    METHOD _receive_packet_internal(custom_timeout):
        1. SET self.udp_socket timeout (use custom_timeout if provided, else self.timeout_duration).
        2. TRY to RECEIVE data from self.udp_socket into packet_raw_bytes, sender_address.
        3. CATCH socket.timeout: RETURN None, "timeout".
        4. CATCH socket.error: LOG error, RETURN None, "error".
        5. UNPACK packet_raw_bytes using unpack_packet helper.
        6. IF unpack successful: RETURN sender_address, (unpacked_seq, unpacked_ack, unpacked_flags, unpacked_payload).
        7. ELSE (unpack failed): RETURN sender_address, None.
    ```

#### `bind(self, address)`
* **Purpose:** Explicitly bind the socket to a local address. (Your code already has this, it's standard).
* **Pseudocode:** (As per your implementation)
    ```
    METHOD bind(local_address):
        1. TRY to SET SO_REUSEADDR on self.udp_socket.
        2. BIND self.udp_socket to local_address. LOG success or RAISE error.
    ```

#### `listen(self, backlog=1)`
* **Purpose:** Set the socket to listen for incoming connection attempts.
* **Pseudocode:**
    ```
    METHOD listen(backlog_parameter): // backlog_parameter is for API compatibility, not used by UDP
        1. IF self.state is CLOSED:
            TRY to GET self.udp_socket.getsockname() (checks if bound).
                IF successful: SET self.state = LISTEN. LOG listening. RETURN True.
                ELSE (not bound): LOG error. RETURN False.
        2. ELSE: LOG error (cannot listen in current state). RETURN False.
    ```

#### `accept(self)`
* **Purpose:** (Server-side) Waits for a client's SYN, completes the 3-way handshake.
* **State Transitions:** `LISTEN` -> `SYN_RECEIVED` -> `ESTABLISHED`.
* **Pseudocode:**
    ```
    METHOD accept():
        1. IF self.state is not LISTEN, LOG error, RETURN None.
        2. LOG "Server accepting connections...".
        3. OUTER LOOP (while self.state is LISTEN): // To retry accepting if one handshake fails
            a. RECEIVE packet (blocking, no timeout for initial SYN) using self._receive_packet_internal(timeout_val=None).
               Let received data be (sender_addr, event_details).
            b. IF event_details is valid (not "timeout", "error", or None) AND sender_addr exists:
                i.  EXTRACT seq_rcvd, _, flags_rcvd, _ FROM event_details.
                ii. IF flags_rcvd is SYN_FLAG: // Received a SYN
                    1. SET self.peer_addr = sender_addr.
                    2. SET self.expected_seq_num = seq_rcvd + 1.
                    3. SET self.ack_num = seq_rcvd + 1 (this will be the ACK field in our SYN-ACK).
                    4. LOG SYN reception.
                    5. IF _send_packet_internal(self.seq_num (server's ISN), self.ack_num, SYNACK_FLAG) is successful:
                        SET self.state = SYN_RECEIVED.
                        RESET self.current_retry_count = 0.
                    ELSE (failed to send SYN-ACK):
                        RESET self.peer_addr = None. CONTINUE OUTER LOOP.

            c. INNER LOOP (while self.state is SYN_RECEIVED): // To handle SYN-ACK retransmission and wait for final ACK
                i.  IF self.current_retry_count > self.max_retries:
                    LOG max retransmissions for SYN-ACK. SET self.state = LISTEN. RESET self.peer_addr.
                    CLEAR self.last_packet_sent_for_retransmission. BREAK INNER LOOP.
                ii. RECEIVE packet using self._receive_packet_internal() (with default RUDP timeout).
                    Let received data be (sender_addr_ack, event_details_ack).
                iii.IF event_details_ack is "timeout":
                    LOG timeout for ACK of SYN-ACK. CALL self._send_packet_internal (retransmit, using stored packet).
                    INCREMENT self.current_retry_count.
                iv. ELSE IF event_details_ack is valid AND sender_addr_ack is self.peer_addr:
                    EXTRACT _, ack_ack_num_rcvd, ack_flags_rcvd, _ FROM event_details_ack.
                    IF ack_flags_rcvd is ACK_FLAG AND ack_ack_num_rcvd is (self.seq_num + 1): // Correct ACK for our SYN-ACK
                        LOG ACK for SYN-ACK received.
                        self.seq_num += 1 (consume server's SYN).
                        SET self.state = ESTABLISHED.
                        CLEAR self.last_packet_sent_for_retransmission.
                        RETURN self (the connected socket).
                    ELSE: LOG unexpected packet in SYN_RECEIVED.
                v.  ELSE IF event_details_ack is "error": // Socket error
                    SET self.state = LISTEN. RESET self.peer_addr. BREAK INNER LOOP.
        4. RETURN None (if loop exits without establishing).
    ```

#### `connect(self, server_addr)`
* **Purpose:** (Client-side) Initiates a 3-way handshake with the server.
* **State Transitions:** `CLOSED` -> `SYN_SENT` -> `ESTABLISHED`.
* **Pseudocode:**
    ```
    METHOD connect(target_server_address):
        1. IF self.state is not CLOSED, LOG error, RETURN False.
        2. SET self.peer_addr = target_server_address.
        3. SET self.state = SYN_SENT.
        4. SET self.ack_num = 0 (client's SYN doesn't ACK prior data).
        5. SET self.expected_seq_num = 0 (will be server's ISN + 1).
        6. LOG sending SYN.
        7. IF _send_packet_internal(self.seq_num (client's ISN), self.ack_num, SYN_FLAG) is False:
            SET self.state = CLOSED. RETURN False.
        8. RESET self.current_retry_count = 0.

        9. LOOP (while self.state is SYN_SENT):
            a. IF self.current_retry_count > self.max_retries:
                LOG max retransmissions for SYN. SET self.state = CLOSED.
                CLEAR self.last_packet_sent_for_retransmission. RETURN False.
            b. RECEIVE packet using self._receive_packet_internal().
               Let received data be (sender_addr, event_details).
            c. IF event_details is "timeout":
                LOG timeout for SYN-ACK. CALL self._send_packet_internal (retransmit SYN).
                INCREMENT self.current_retry_count.
            d. ELSE IF event_details is valid AND sender_addr is self.peer_addr:
                EXTRACT seq_rcvd, ack_num_rcvd, flags_rcvd, _ FROM event_details.
                IF flags_rcvd is SYNACK_FLAG AND ack_num_rcvd is (self.seq_num + 1): // Correct SYN-ACK
                    LOG SYN-ACK received.
                    self.seq_num += 1 (consume client's SYN).
                    self.ack_num = seq_rcvd + 1 (this ACKs server's SYN part).
                    self.expected_seq_num = seq_rcvd + 1 (next data/FIN from server).
                    IF _send_packet_internal(self.seq_num, self.ack_num, ACK_FLAG) is successful: // Send final ACK
                        SET self.state = ESTABLISHED.
                        CLEAR self.last_packet_sent_for_retransmission.
                        LOG connection ESTABLISHED. RETURN True.
                    ELSE (failed to send final ACK):
                        SET self.state = CLOSED. RETURN False.
                ELSE: LOG unexpected packet in SYN_SENT.
            e. ELSE IF event_details is "error":
                SET self.state = CLOSED. RETURN False.
        10. RETURN False (if loop exits unexpectedly).
    ```

#### `send_data(self, data)`
* **Purpose:** Sends a single data segment reliably using Stop-and-Wait.
* **State Requirement:** `ESTABLISHED`.
* **Pseudocode:**
    ```
    METHOD send_data(payload_to_send):
        1. IF self.state is not ESTABLISHED, LOG error, RETURN False.
        2. LOG sending DATA.
        3. // self.ack_num should reflect the next sequence number expected from the peer,
           // which is used if this data packet also serves as an ACK (piggybacking).
        4. IF _send_packet_internal(self.seq_num, self.ack_num, 0, payload_to_send) is False: // Flag 0 for data
            RETURN False (initial send failed).
        5. RESET self.current_retry_count = 0.

        6. LOOP (Stop-and-Wait for ACK for this segment):
            a. IF self.current_retry_count > self.max_retries:
                LOG max retransmissions for DATA. CLEAR self.last_packet_sent_for_retransmission. RETURN False.
            b. RECEIVE packet using self._receive_packet_internal().
               Let received data be (sender_addr, event_details).
            c. IF event_details is "timeout":
                LOG timeout for ACK of DATA. CALL self._send_packet_internal (retransmit data).
                INCREMENT self.current_retry_count.
            d. ELSE IF event_details is valid AND sender_addr is self.peer_addr:
                EXTRACT resp_seq, resp_ack_num, resp_flags, resp_payload FROM event_details.
                IF (resp_flags & ACK_FLAG) AND resp_ack_num is self.seq_num: // Correct ACK for our data
                    LOG ACK for DATA received.
                    self.seq_num += 1 (advance our sequence number for next send).
                    CLEAR self.last_packet_sent_for_retransmission.
                    // Optional: Handle piggybacked data from peer if present in this ACK packet.
                    // IF (no SYN/FIN flags) AND (length of resp_payload > 0) AND (resp_seq is self.expected_seq_num):
                    //    LOG piggybacked data received. Process/buffer it. Update self.ack_num and self.expected_seq_num. Send separate ACK.
                    RETURN True.
                ELSE: LOG unexpected packet while waiting for ACK of DATA.
            e. ELSE IF event_details is "error": RETURN False.
        7. RETURN False (if loop exits unexpectedly).
    ```

#### `receive_data(self)`
* **Purpose:** Receives a single data segment or a FIN.
* **State Requirement:** `ESTABLISHED` or `CLOSE_WAIT`.
* **Pseudocode:**
    ```
    METHOD receive_data():
        1. IF self.state is not ESTABLISHED AND self.state is not CLOSE_WAIT, LOG error, RETURN None.

        2. LOOP (to get a valid data/FIN or timeout):
            a. RECEIVE packet using self._receive_packet_internal().
               Let received data be (sender_addr, event_details).
            b. IF event_details is "timeout": LOG debug timeout. RETURN None.
            c. ELSE IF event_details is "error": RETURN None.
            d. ELSE IF event_details is valid:
                i.  IF sender_addr is not self.peer_addr, LOG warning, CONTINUE LOOP.
                ii. EXTRACT seq_rcvd, ack_num_rcvd, flags_rcvd, payload_rcvd FROM event_details.
                iii.IF flags_rcvd & ACK_FLAG: // Packet contains an ACK
                    LOG debug "receive_data got packet with ACK flag."
                    // This ACK might be for data we sent. send_data() handles its own primary ACK wait.
                    // If ack_num_rcvd == self.seq_num - 1 (our previously sent data's SEQ), it's relevant.

                iv. IF (no SYN/FIN flags in flags_rcvd) AND (length of payload_rcvd > 0): // It's a DATA packet
                    1. IF seq_rcvd is self.expected_seq_num: // Expected data
                        LOG DATA received.
                        self.ack_num = seq_rcvd + 1 (this will be the ACK value we send).
                        self.expected_seq_num = seq_rcvd + 1 (update for next expected).
                        CALL self._send_packet_internal(self.seq_num, self.ack_num, ACK_FLAG) (send ACK).
                        RETURN payload_rcvd.
                    2. ELSE IF seq_rcvd < self.expected_seq_num: // Duplicate data
                        LOG duplicate DATA. CALL self._send_packet_internal(self.seq_num, seq_rcvd + 1, ACK_FLAG) (re-ACK old).
                        CONTINUE LOOP.
                    3. ELSE: // Out-of-order data
                        LOG out-of-order DATA. CONTINUE LOOP (discard).
                v.  ELSE IF flags_rcvd & FIN_FLAG: // It's a FIN packet
                    1. IF seq_rcvd is self.expected_seq_num: // Expected FIN
                        LOG FIN received.
                        self.ack_num = seq_rcvd + 1.
                        self.expected_seq_num = seq_rcvd + 1.
                        CALL self._send_packet_internal(self.seq_num, self.ack_num, ACK_FLAG) (send ACK for FIN).
                        IF self.state is ESTABLISHED: SET self.state = CLOSE_WAIT.
                        ELSE IF self.state is FIN_WAIT2: SET self.state = CLOSED (TIME_WAIT omitted).
                        CLEAR self.last_packet_sent_for_retransmission.
                        RETURN EMPTY_BYTES (signal EOF).
                    2. ELSE IF seq_rcvd < self.expected_seq_num: // Duplicate FIN
                        LOG duplicate FIN. CALL self._send_packet_internal(self.seq_num, seq_rcvd + 1, ACK_FLAG) (re-ACK).
                        CONTINUE LOOP.
                    3. ELSE: // Future FIN
                        LOG future FIN. CONTINUE LOOP (discard).
                vi. ELSE IF flags_rcvd & ACK_FLAG: // Standalone ACK (not for data we are actively waiting on here)
                    LOG debug "Standalone ACK received in receive_data. Ignoring."
                    CONTINUE LOOP.
                vii.ELSE: LOG debug "Unexpected packet type in receive_data." CONTINUE LOOP.
        3. RETURN None (if loop exits unexpectedly).
    ```

#### `close(self)`
* **Purpose:** Gracefully terminates the RUDP connection.
* **Handles multiple states and scenarios.**
* **Pseudocode (Simplified structure, focusing on main state transitions):**
    ```
    METHOD close():
        1. LOG "Close called. Current state: " + self.state.
        2. IF self.state is CLOSED, LOG "Already closed.", RETURN True.
        3. STORE original_socket_timeout.

        4. // --- Active Close Path (Initiated from ESTABLISHED) ---
           IF self.state is ESTABLISHED:
               SET self.state = FIN_WAIT1. LOG transition.
               IF _send_packet_internal(self.seq_num, self.ack_num, FIN_FLAG) is False: // Send FIN
                   SET self.state = CLOSED. CALL self._cleanup_socket(original_socket_timeout). RETURN False.
               RESET self.current_retry_count = 0.

        5. // --- FIN_WAIT_1 Logic ---
           IF self.state is FIN_WAIT1:
               peer_fin_processed_locally = False
               LOOP while self.current_retry_count <= self.max_retries:
                   RECEIVE packet (addr, event_data) using self._receive_packet_internal().
                   IF event_data is "timeout":
                       LOG timeout. CALL self._send_packet_internal (retransmit FIN). INCREMENT retries.
                   ELSE IF event_data is valid AND addr is self.peer_addr:
                       EXTRACT resp_seq, resp_ack, resp_flags, _ FROM event_data.
                       IF (resp_flags & ACK_FLAG) AND (resp_ack is self.seq_num): // ACK for our FIN
                           LOG ACK for our FIN received. self.seq_num += 1.
                           IF peer_fin_processed_locally: SET self.state = CLOSED (TIME_WAIT omitted). LOG "Simultaneous close done."
                           ELSE: SET self.state = FIN_WAIT2. LOG "Transition to FIN_WAIT2."
                           CLEAR self.last_packet_sent_for_retransmission. BREAK LOOP.
                       ELSE IF (resp_flags & FIN_FLAG) AND (resp_seq is self.expected_seq_num): // Peer's FIN
                           LOG Peer's FIN received. self.ack_num = resp_seq + 1. self.expected_seq_num = resp_seq + 1.
                           CALL self._send_packet_internal(self.seq_num, self.ack_num, ACK_FLAG) (ACK their FIN).
                           peer_fin_processed_locally = True. // Continue waiting for ACK of our FIN
                       ELSE: LOG unexpected packet in FIN_WAIT1.
                   ELSE IF event_data is "error": SET self.state = CLOSED. BREAK LOOP.
               IF self.state is FIN_WAIT1: LOG max retries in FIN_WAIT1. SET self.state = CLOSED.

        6. // --- FIN_WAIT_2 Logic ---
           IF self.state is FIN_WAIT2:
               RESET self.current_retry_count = 0. LOG "In FIN_WAIT2."
               LOOP while self.current_retry_count <= self.max_retries: // This loop is more about waiting than retransmitting from our side
                   RECEIVE packet (addr, event_data) using self._receive_packet_internal().
                   IF event_data is "timeout": LOG timeout. INCREMENT retries (as a timeout counter).
                   ELSE IF event_data is valid AND addr is self.peer_addr:
                       EXTRACT resp_seq, _, resp_flags, _ FROM event_data.
                       IF (resp_flags & FIN_FLAG) AND (resp_seq is self.expected_seq_num): // Peer's FIN
                           LOG Peer's FIN received in FIN_WAIT2. self.ack_num = resp_seq + 1. self.expected_seq_num = resp_seq + 1.
                           CALL self._send_packet_internal(self.seq_num, self.ack_num, ACK_FLAG) (Send final ACK).
                           SET self.state = CLOSED (TIME_WAIT omitted). LOG "Connection terminated." BREAK LOOP.
                       ELSE: LOG unexpected packet in FIN_WAIT2.
                   ELSE IF event_data is "error": SET self.state = CLOSED. BREAK LOOP.
               IF self.state is FIN_WAIT2: LOG failed to get peer's FIN in FIN_WAIT2. SET self.state = CLOSED.

        7. // --- Passive Close Path (From CLOSE_WAIT) ---
           ELSE IF self.state is CLOSE_WAIT:
               SET self.state = LAST_ACK. LOG transition.
               IF _send_packet_internal(self.seq_num, self.expected_seq_num, FIN_FLAG) is False: // Send our FIN
                   SET self.state = CLOSED. CALL self._cleanup_socket(original_socket_timeout). RETURN False.
               RESET self.current_retry_count = 0.
               LOOP while self.current_retry_count <= self.max_retries:
                   RECEIVE packet (addr, event_data) using self._receive_packet_internal().
                   IF event_data is "timeout":
                       LOG timeout. CALL self._send_packet_internal (retransmit FIN). INCREMENT retries.
                   ELSE IF event_data is valid AND addr is self.peer_addr:
                       EXTRACT _, resp_ack, resp_flags, _ FROM event_data.
                       IF (resp_flags & ACK_FLAG) AND (resp_ack is self.seq_num): // ACK for our FIN
                           LOG ACK for our FIN received in LAST_ACK. self.seq_num += 1.
                           SET self.state = CLOSED. LOG "Connection terminated (passive close)." BREAK LOOP.
                       ELSE: LOG unexpected packet in LAST_ACK.
                   ELSE IF event_data is "error": SET self.state = CLOSED. BREAK LOOP.
               IF self.state is LAST_ACK: LOG max retries in LAST_ACK. SET self.state = CLOSED.

        8. // --- Other states ---
           ELSE IF self.state is LISTEN: LOG "Closing LISTEN socket." SET self.state = CLOSED.

        9. CALL self._cleanup_socket(original_socket_timeout).
        10.RETURN self.state is CLOSED.
    ```

#### `_cleanup_socket(self, original_timeout_to_restore)`
* **Purpose:** Internal helper to ensure the socket is finally closed and original timeout restored.
* **Pseudocode:**
    ```
    METHOD _cleanup_socket(original_timeout):
        1. IF self.state is not CLOSED, LOG warning (forcing to CLOSED), SET self.state = CLOSED.
        2. CLEAR self.last_packet_sent_for_retransmission.
        3. TRY to RESTORE original_timeout on self.udp_socket (if socket is still valid).
        4. TRY to CLOSE self.udp_socket (if valid and not already closed).
        5. LOG cleanup complete.
    ```

---
## Part 2: `http_server.py` - The HTTP Server

**Overall Purpose:**
Listens for incoming HTTP requests on a specific port using the RUDP `Socket`. It parses these requests (GET, POST), interacts with the file system for GET requests, and sends back HTTP/1.0 responses. It's an iterative server.

---

### 2.1. Module-Level Setup

**Location:** At the beginning of `http_server.py`.
* **Constants:** `SERVER_ADDRESS`, `SERVER_PORT`, `WWW_ROOT`.
* **Logging:** Basic configuration.
* **Helper Function: `_ensure_www_directory_server()`**
    * **Purpose:** Creates the web content directory (`WWW_ROOT`) and sample files (`index.html`, `test.txt`) if they don't exist.
    * **Pseudocode:**
        ```
        FUNCTION _ensure_www_directory_server():
            1. IF WWW_ROOT directory does not exist, CREATE it.
            2. DEFINE a dictionary of sample files (filename: content).
            3. FOR each filename, content in the dictionary:
                a. CONSTRUCT full file path.
                b. IF file does not exist, CREATE it and WRITE content.
        ```
* **Helper Function: `_get_mime_type_server(filepath)`**
    * **Purpose:** Returns a basic MIME type string based on file extension.
    * **Pseudocode:**
        ```
        FUNCTION _get_mime_type_server(file_path_string):
            1. IF file_path_string ends with ".html" or ".htm", RETURN "text/html; charset=utf-8".
            2. IF file_path_string ends with ".txt", RETURN "text/plain; charset=utf-8".
            3. IF file_path_string ends with ".png", RETURN "image/png".
            4. (Add more types as needed)
            5. ELSE, RETURN "application/octet-stream" (default binary).
        ```

---

### 2.2. Core HTTP Handling Functions

**Helper Function: `_send_response(rudp_conn, status_code, status_msg, content_type, body_bytes)`**
* **Purpose:** Constructs a full HTTP response and sends it segment by segment using the provided RUDP connection.
* **Pseudocode:**
    ```
    FUNCTION _send_response(rudp_socket_connection, http_status_code, http_status_message, mime_content_type, response_body_data_bytes):
        1. CREATE response_status_line = "HTTP/1.0 " + status_code + " " + status_message + "\r\n".
        2. CREATE headers_dictionary = {
               "Server": "RUDP-HTTP-Server/1.0",
               "Content-Type": mime_content_type,
               "Content-Length": length of response_body_data_bytes,
               "Connection": "close"
           }.
        3. CONVERT headers_dictionary to a list of "Key: Value" strings.
        4. COMBINE response_status_line, header strings (joined by "\r\n"), and a final "\r\n\r\n" into `full_headers_string`.
        5. COMBINE `full_headers_string.encode()` and `response_body_data_bytes` into `complete_response_bytes`.
        6. INITIALIZE offset = 0.
        7. LOOP while offset < length of `complete_response_bytes`:
            a. EXTRACT chunk from `complete_response_bytes` (from offset, up to MAX_PAYLOAD_SIZE).
            b. IF rudp_socket_connection.send_data(chunk) is False, LOG error, RETURN False.
            c. INCREMENT offset by length of chunk.
        8. LOG successful response sending. RETURN True.
    ```

**Function: `_handle_client_request(rudp_conn, client_addr)`**
* **Purpose:** Manages the entire HTTP interaction for a single connected client.
* **Pseudocode:**
    ```
    FUNCTION _handle_client_request(active_rudp_connection, client_address_info):
        TRY:
            // 1. Receive HTTP Request (Headers and Body)
            INITIALIZE raw_request_accumulator_bytes = EMPTY_BYTES.
            INITIALIZE headers_are_complete = False.
            INITIALIZE expected_body_length = 0.
            INITIALIZE temporary_receive_buffer = EMPTY_BYTES.
            INITIALIZE body_chunk_from_header_packet = EMPTY_BYTES.

            LOOP while headers_are_complete is False: // Receive headers
                received_segment = active_rudp_connection.receive_data().
                IF received_segment is None or (isinstance(received_segment, bytes) and not received_segment): // RUDP error, timeout, or FIN
                    LOG error or info about connection close. RETURN.
                temporary_receive_buffer += received_segment.
                IF b"\r\n\r\n" (CRLF CRLF) is in temporary_receive_buffer:
                    headers_are_complete = True.
                    header_section_bytes, _, body_chunk_from_header_packet = temporary_receive_buffer.partition(b"\r\n\r\n").
                    raw_request_accumulator_bytes = header_section_bytes + b"\r\n\r\n".
                    PARSE header_section_bytes to find "Content-Length:" value into expected_body_length.
                    BREAK LOOP.
            
            actual_request_body_bytes = body_chunk_from_header_packet.
            LOOP while length of actual_request_body_bytes < expected_body_length: // Receive body if any
                received_segment = active_rudp_connection.receive_data().
                IF received_segment is None or (isinstance(received_segment, bytes) and not received_segment):
                    LOG error or info about connection close during body reception. RETURN.
                actual_request_body_bytes += received_segment.
            
            LOG received HTTP request headers and body preview.

            // 2. Parse Request Line
            DECODE raw_request_accumulator_bytes (headers part) into decoded_header_string.
            SPLIT decoded_header_string into request_lines.
            IF request_lines is empty or first line is empty:
                CALL _send_response with 400 Bad Request. RETURN.
            EXTRACT method, path, version from first request line. CATCH errors and send 400 if malformed.

            // 3. Process Request & Prepare Response
            IF method is "GET":
                IF path is "/", set path = "/index.html".
                CONSTRUCT full_file_path from WWW_ROOT and path. NORMALIZE path.
                SECURITY CHECK: Ensure full_file_path is within WWW_ROOT. If not, send 403 Forbidden.
                IF file exists at full_file_path AND is a file:
                    READ file content into response_body_bytes.
                    DETERMINE mime_type using _get_mime_type_server.
                    CALL _send_response with 200 OK, mime_type, response_body_bytes.
                ELSE (file not found):
                    CALL _send_response with 404 Not Found, "text/html", HTML_404_error_page_bytes.
            ELSE IF method is "POST":
                LOG POST request details.
                CREATE HTML_POST_success_page_bytes.
                CALL _send_response with 200 OK, "text/html", HTML_POST_success_page_bytes.
            ELSE (method not supported):
                CALL _send_response with 501 Not Implemented, "text/html", HTML_501_error_page_bytes.
        
        CATCH any Exception as e:
            LOG error during client handling.
            TRY to CALL _send_response with 500 Internal Server Error.
        FINALLY:
            LOG closing connection. CALL active_rudp_connection.close().
    ```

---

### 2.3. Main Server Execution (`main` function)

**Location:** The entry point of `http_server.py`.

**Purpose:**
* Initializes the server environment (e.g., web directory).
* Enters an infinite loop to iteratively handle client connections:
    * Creates a new RUDP `Socket` for each potential client.
    * Binds and listens on this socket.
    * Calls `accept()` to wait for and establish an RUDP connection.
    * If successful, passes the connected socket to `_handle_client_request`.
    * Includes error handling for common issues like "Address already in use" and graceful shutdown on `KeyboardInterrupt`.

**Pseudocode:**
FUNCTION main():1. CALL _ensure_www_directory_server().2. LOG server starting message with address, port, and WWW_ROOT.3. LOOP indefinitely (main server loop):
    a. INITIALIZE current_iteration_socket = None.
    b. TRY (to handle one client cycle):
        i.  CREATE current_iteration_socket = new RUDP Socket().
        ii. CALL current_iteration_socket.bind((SERVER_ADDRESS, SERVER_PORT)).
        iii.IF current_iteration_socket.listen() is False:
            LOG listen error. CLOSE current_iteration_socket if it exists. SLEEP briefly. CONTINUE LOOP.
        iv. connected_socket = current_iteration_socket.accept().
        v.  IF connected_socket is not None (handshake successful):
            // connected_socket is the same object as current_iteration_socket, now in ESTABLISHED state
            CALL _handle_client_request(connected_socket, connected_socket.peer_addr).
            // _handle_client_request calls close() on connected_socket.
        vi. ELSE (accept failed):
            LOG accept failed. CLOSE current_iteration_socket if it exists and is still in LISTEN state.
    c. CATCH OSError if error_number is "Address already in use":
        LOG critical error. BREAK LOOP (stop server).
    d. CATCH KeyboardInterrupt:
        LOG server shutting down. BREAK LOOP.
    e. CATCH any other Exception e:
        LOG unhandled server error. BREAK LOOP (or decide to continue).
    f. FINALLY (for the inner try block):
        // Ensure the socket for this iteration is closed if it wasn't handled properly
        IF current_iteration_socket exists AND its state is not CLOSED:
            CALL current_iteration_socket.close().
4. LOG server stopped.

---
## Part 3: `http_client.py` - The HTTP Client

**Overall Purpose:**
The HTTP client creates and sends HTTP requests (GET or POST) to the server using the RUDP `Socket`. It then receives and displays the server's response.

---

### 3.1. Module-Level Setup

**Location:** At the beginning of `http_client.py`.
* **Constants:** `DEFAULT_SERVER_HOST`, `DEFAULT_SERVER_PORT`.
* **Logging:** Basic configuration.
* **Imports:** `logging`, `sys`, your RUDP `Socket` and `MAX_PAYLOAD_SIZE`.

---

### 3.2. Core HTTP Request Function (`_execute_http_request`)

**Location:** The primary worker function in `http_client.py`.

**Purpose:**
* Handles the entire lifecycle of a single HTTP request:
    * Creates an RUDP `Socket`.
    * Connects to the server via RUDP.
    * Constructs the HTTP request string (method, path, version, headers, body).
    * Sends the request in segments using `rudp_sock.send_data()`.
    * Receives the response (headers then body) in segments using `rudp_sock.receive_data()`.
    * Prints the response.
    * Closes the RUDP connection.

**Pseudocode:**
FUNCTION _execute_http_request(http_method, request_uri, target_host, target_port, request_body_string, loss_simulation_rate, corruption_simulation_rate):1. CREATE rudp_client_socket = new RUDP Socket(loss_simulation_rate, corruption_simulation_rate).2. LOG attempting RUDP connect.3. IF rudp_client_socket.connect((target_host, target_port)) is False, LOG error, RETURN None.4. LOG RUDP connection successful.// 1. Construct HTTP Request
INITIALIZE http_headers_list.
ADD request line (method, path, "HTTP/1.0") to list.
ADD "Host: host:port" header to list.
ADD "Connection: close" header to list.
ADD "User-Agent" header to list.
INITIALIZE http_body_bytes = EMPTY_BYTES.
IF http_method is "POST" AND request_body_string exists:
    http_body_bytes = request_body_string.encode().
    ADD "Content-Type" header to list.
    ADD "Content-Length" header (with length of http_body_bytes) to list.

COMBINE http_headers_list into header_string (joined by "\r\n") + "\r\n\r\n".
full_http_request_bytes = header_string.encode() + http_body_bytes.
LOG sending HTTP request preview.

// 2. Send HTTP Request
INITIALIZE offset = 0.
LOOP while offset < length of full_http_request_bytes:
    EXTRACT chunk from full_http_request_bytes (from offset, up to MAX_PAYLOAD_SIZE).
    IF rudp_client_socket.send_data(chunk) is False, LOG error, CALL rudp_client_socket.close(), RETURN None.
    INCREMENT offset by length of chunk.
LOG full request sent.

// 3. Receive HTTP Response
INITIALIZE full_response_accumulator_bytes = EMPTY_BYTES.
INITIALIZE response_headers_are_complete = False.
INITIALIZE expected_response_body_length = -1 (unknown).
INITIALIZE temporary_receive_buffer = EMPTY_BYTES.
INITIALIZE body_chunk_from_header_packet = EMPTY_BYTES.

LOOP while response_headers_are_complete is False: // Receive headers
    received_segment = rudp_client_socket.receive_data().
    IF received_segment is None or (isinstance(received_segment, bytes) and not received_segment): // RUDP error, timeout, or FIN
        LOG error or info about server close during header reception.
        IF temporary_receive_buffer is empty: CALL rudp_client_socket.close(), RETURN None.
        full_response_accumulator_bytes = temporary_receive_buffer. // Process what was received
        response_headers_are_complete = True. // Assume this is it
        BREAK LOOP.
    temporary_receive_buffer += received_segment.
    IF b"\r\n\r\n" in temporary_receive_buffer:
        response_headers_are_complete = True.
        header_section_bytes, _, body_chunk_from_header_packet = temporary_receive_buffer.partition(b"\r\n\r\n").
        full_response_accumulator_bytes = header_section_bytes + b"\r\n\r\n".
        PARSE header_section_bytes to find "Content-Length:" value into expected_response_body_length.
        BREAK LOOP.

IF full_response_accumulator_bytes is empty AND body_chunk_from_header_packet is empty:
    LOG no response headers. CALL rudp_client_socket.close(), RETURN None.

actual_response_body_bytes = body_chunk_from_header_packet.
IF expected_response_body_length is not -1: // Known length
    LOOP while length of actual_response_body_bytes < expected_response_body_length:
        received_segment = rudp_client_socket.receive_data().
        IF received_segment is None or (isinstance(received_segment, bytes) and not received_segment):
            LOG warning about connection issue during body reception (known length). BREAK LOOP.
        actual_response_body_bytes += received_segment.
ELSE: // Unknown length
    LOOP indefinitely:
        received_segment = rudp_client_socket.receive_data().
        IF received_segment is None: LOG warning about timeout (unknown length). BREAK LOOP.
        IF (isinstance(received_segment, bytes) and not received_segment): LOG info server closed (end of body). BREAK LOOP.
        actual_response_body_bytes += received_segment.
        
full_response_accumulator_bytes += actual_response_body_bytes.
LOG response received. PRINT response details and content (decoded).

CALL rudp_client_socket.close(). LOG RUDP connection closed.
RETURN full_response_accumulator_bytes.

---

### 3.3. Main Client Execution (`main` function)

**Location:** The `if __name__ == "__main__":` block in `http_client.py`.

**Purpose:**
* Parses command-line arguments for HTTP method, path, body (for POST), target host/port, and simulation parameters (`--loss`, `--corrupt`).
* Calls `_execute_http_request` with the parsed values.

**Pseudocode:**
FUNCTION main():1. INITIALIZE default values for method, path, body, loss, corrupt, host, port.2. GET command_line_arguments (sys.argv[1:]).3. PARSE arguments:IF arguments exist, update method.IF more arguments exist, update path.IF method is "POST" AND more arguments exist (and not an option like --loss), update body.LOOP through remaining arguments to parse options like --loss, --corrupt, --host, --port, updating respective variables.4. PRINT client configuration (method, path, etc.).5. CALL _execute_http_request with all parsed/defaulted parameters.
This detailed pseudocode should guide you in implementing each file with a clear understanding of its role and logic. Remember to test each part thoroughly, especially the RUDP state transitions and error handling.
</markdown>
