# Design Document: Reliable UDP (RUDP) and HTTP/1.0 Implementation

## 1. Introduction

This document outlines the design for a system that simulates TCP-like reliable data transfer over UDP, and implements a simple HTTP/1.0 server and client on top of this reliable UDP layer (RUDP). The goal is to achieve reliability mechanisms such as error detection, packet retransmission, and ordered delivery, which are absent in standard UDP.

## 2. RUDP Protocol Design

The RUDP protocol layer is implemented as a Python class (`RUDPsocket`) that wraps the standard UDP socket.

### 2.1. RUDP Packet Structure

Each RUDP packet will have a custom header followed by the data payload.

| Field                 | Size (bytes) | Description                                       |
|-----------------------|--------------|---------------------------------------------------|
| Source Port           | 2            | UDP Source Port (handled by underlying UDP socket)  |
| Destination Port      | 2            | UDP Dest Port (handled by underlying UDP socket)    |
| **RUDP Header Starts**|              |                                                   |
| Sequence Number       | 4            | Sequence number of this segment.                  |
| Acknowledgment Number | 4            | ACK number for a received segment.                |
| Flags                 | 1            | SYN, ACK, FIN flags.                              |
| Length                | 2            | Length of the data payload in this segment.       |
| Checksum              | 2            | Checksum for the RUDP header + data.              |
| **RUDP Header Ends** |              |                                                   |
| Data                  | Variable     | Payload data (up to `PAYLOAD_MSS`).               |

**Flags (1 byte):**
* `SYN (0x01)`: Synchronize sequence numbers (initiate connection).
* `ACK (0x02)`: Acknowledgment field is significant.
* `FIN (0x04)`: No more data from sender (terminate connection).
* `SYNACK (0x03)`: SYN + ACK.

**Constants:**
* `RUDP_HEADER_FORMAT = '!IIBH H'` (using `struct` module for packing/unpacking). Total 13 bytes.
* `PAYLOAD_MSS` (Maximum Segment Size for Data): e.g., 1024 bytes. This is the max size of the `Data` field.

### 2.2. Connection Establishment (3-Way Handshake)

A 3-way handshake is used to establish a connection:
1.  **Client -> Server:** SYN (Client's Initial Sequence Number `client_isn`)
2.  **Server -> Client:** SYNACK (Server's Initial Sequence Number `server_isn`, ACK = `client_isn + 1`)
3.  **Client -> Server:** ACK (ACK = `server_isn + 1`)

The `RUDPsocket` will manage connection states (e.g., `CLOSED`, `LISTEN`, `SYN_SENT`, `SYN_RCVD`, `ESTABLISHED`).

### 2.3. Data Transfer (Stop-and-Wait ARQ)

The RUDP layer uses Stop-and-Wait ARQ for sending individual data segments.
* **Segmentation:** The HTTP application layer will provide data to the `RUDPsocket`. If the data is larger than `PAYLOAD_MSS`, the HTTP layer is responsible for breaking it into chunks and calling `RUDPsocket.send_data()` for each chunk. Similarly, `RUDPsocket.receive_data()` will return one segment's payload at a time.
* **Sequence Numbers:** Each data segment sent by `RUDPsocket.send_data()` will have a unique, incrementing sequence number. This is a segment sequence number, not a byte sequence number.
* **Acknowledgments (ACKs):**
    * When the receiver gets a data segment with sequence number `X` correctly (checksum valid, in order), it sends an ACK packet with `Acknowledgment Number = X`.
    * The sender waits for an ACK for the segment it sent.
* **Retransmission:**
    * If the sender does not receive an ACK within a `DEFAULT_TIMEOUT` period, it retransmits the segment.
    * This process is repeated up to `MAX_RETRANSMISSIONS` times. If still no ACK, the connection is considered broken.
* **Duplicate Packets:** The receiver checks the sequence number of incoming data segments.
    * If it's the expected sequence number, process it and send an ACK.
    * If it's a duplicate of a previously acknowledged segment (i.e., `seq_num < expected_seq_num`), re-send the ACK for that old segment and discard the duplicate data.
    * If it's a future segment (i.e., `seq_num > expected_seq_num`), it's an out-of-order packet. In a strict stop-and-wait, this shouldn't happen if the network doesn't reorder. If it does, this implementation will discard it and wait for the expected one. The sender will eventually time out and resend the correct one.

### 2.4. Error Detection (Checksum)

* A 16-bit Internet-like checksum is calculated over the RUDP header (with checksum field set to 0 for calculation) and the data payload.
    * The RUDP header fields included are: Sequence Number, Ack Number, Flags, Length.
* The sender computes and includes the checksum in the RUDP header.
* The receiver recomputes the checksum on the received packet (header + data). If it doesn't match the checksum in the header, the packet is considered corrupted and silently dropped.
* The sender will eventually time out and retransmit the dropped packet.
* A method to simulate a false checksum will be provided for testing.

### 2.5. Connection Termination (FIN Exchange)

A graceful termination process involving FIN flags:
1.  **Initiator (Client or Server) -> Peer:** FIN (Sequence Number `X`)
2.  **Peer -> Initiator:** ACK (Acknowledgment Number `X`)
3.  **Peer -> Initiator:** FIN (Sequence Number `Y`) (If peer also has no more data to send)
4.  **Initiator -> Peer:** ACK (Acknowledgment Number `Y`)

The `RUDPsocket.close()` method will handle this.

### 2.6. Timeout and Retransmission

* `DEFAULT_TIMEOUT`: A fixed timeout value (e.g., 1 second).
* `MAX_RETRANSMISSIONS`: Maximum number of times a segment is retransmitted (e.g., 5 times).

### 2.7. Packet Loss and Corruption Simulation

The `RUDPsocket` class will include parameters/methods:
* `simulate_loss_rate` (float, 0.0 to 1.0): Probability to simulate outgoing packet loss.
* `simulate_corruption_rate` (float, 0.0 to 1.0): Probability to corrupt the checksum of an outgoing packet.
These will be used internally before a packet is actually sent via the underlying UDP socket.

## 3. HTTP/1.0 Layer Design

The HTTP server and client will use the `RUDPsocket` for communication.

### 3.1. HTTP Server (`http_server.py`)

* **Initialization:** Creates an `RUDPsocket`, binds it to a specific address and port, and calls `listen()`.
* **Accepting Connections:** Calls `RUDPsocket.accept()` to complete the RUDP handshake with an incoming client. This will be an iterative server, handling one client completely before accepting a new one.
* **Request Handling:**
    1.  Receive the HTTP request from the client using `RUDPsocket.receive_data()` repeatedly until the full request is received (indicated by headers like `Content-Length` or by parsing the request structure). HTTP/1.0 requests end with `\r\n\r\n`.
    2.  Parse the request line (method, URI, version) and headers.
    3.  **GET Method:**
        * If the URI is `/`, serve a default `index.html` file.
        * For other URIs, attempt to serve the requested file from a predefined document root directory (e.g., `./www/`).
        * If the file is found, send a `200 OK` response with appropriate headers (`Content-Type`, `Content-Length`) and the file content.
        * If the file is not found, send a `404 Not Found` response.
    4.  **POST Method:**
        * Read the request body based on `Content-Length`.
        * For this project, the server might simply echo back the POSTed data in the response or log it.
        * Send a `200 OK` response.
* **Response Generation:** Construct HTTP responses with:
    * Status line (e.g., `HTTP/1.0 200 OK`)
    * Headers (e.g., `Server: RUDP-HTTP-Server`, `Content-Type`, `Content-Length`, `Connection: close`)
    * A blank line (`\r\n`)
    * Response body (if any)
* **Sending Response:** Send the HTTP response using `RUDPsocket.send_data()` for each segment of the response.
* **Connection Closure:** After sending the response, the server calls `RUDPsocket.close()` to terminate the RUDP connection. HTTP/1.0 typically closes the connection after each request/response cycle unless `Connection: keep-alive` is used (which we will not implement for simplicity, always closing).

### 3.2. HTTP Client (`http_client.py`)

* **Initialization:** Creates an `RUDPsocket`.
* **Connecting to Server:** Calls `RUDPsocket.connect()` to establish an RUDP connection with the server's address and port.
* **Request Generation:**
    * Constructs an HTTP GET or POST request string (request line, headers, body for POST).
    * Essential headers: `Host`, `Connection: close`. For POST, `Content-Type` and `Content-Length`.
* **Sending Request:** Sends the HTTP request using `RUDPsocket.send_data()` for each segment.
* **Response Handling:**
    1.  Receives the HTTP response using `RUDPsocket.receive_data()` repeatedly.
    2.  Parses the status line and headers.
    3.  Reads the response body (if any) based on `Content-Length` or until the connection is closed by the server.
    4.  Displays the response status, headers, and body.
* **Connection Closure:** Calls `RUDPsocket.close()` after processing the response or if an error occurs.

### 3.3. HTTP Headers and Status Codes

* **Required Headers:**
    * Client requests: `Host`, `Connection: close`. For POST: `Content-Type`, `Content-Length`.
    * Server responses: `Server`, `Content-Type`, `Content-Length`, `Connection: close`.
* **Required Status Codes:**
    * `200 OK`
    * `404 Not Found`

## 4. Assumptions and Limitations

* **Single Client Server:** The basic HTTP server using one `RUDPsocket` instance will handle one client at a time (iterative server). Concurrent client handling would require threading/async programming at the application level, managing multiple `RUDPsocket` instances.
* **Stop-and-Wait Only:** No sliding window or advanced congestion control. Performance will be limited by RTT for each segment.
* **Fixed Timeout:** A fixed retransmission timeout is used. Adaptive timeouts are not implemented.
* **Basic HTTP/1.0:** Only GET and POST methods. Limited header support. No persistent connections (`Connection: keep-alive`).
* **Segmentation at HTTP Layer:** The HTTP application layer is responsible for breaking messages larger than `PAYLOAD_MSS` into chunks and using `RUDPsocket.send_data()` / `receive_data()` for each chunk. The RUDP layer itself handles reliability for these individual segments.
* **Error Handling:** Basic error handling. Robust recovery from all possible network issues is complex.
* **Security:** No encryption or security features are implemented.
* **Path Traversal:** The server should sanitize file paths to prevent path traversal attacks (e.g., ensure requested files are within the designated web root).

## 5. Test Cases

### 5.1. RUDP Layer Tests

* **T1.1: Successful Handshake:** Client connects to server, handshake completes.
* **T1.2: Successful Data Transfer (Single Segment):** Client sends a small data packet, server receives it correctly and ACKs.
* **T1.3: Successful Data Transfer (Multiple Segments):** Client sends data larger than `PAYLOAD_MSS` (requires multiple `send_data` calls), server receives all segments correctly. (This tests the application layer's segmentation with RUDP's segment-wise reliability).
* **T1.4: Packet Loss & Retransmission:**
    * Simulate loss of a data segment from client to server. Verify sender retransmits after timeout.
    * Simulate loss of an ACK segment from server to client. Verify client retransmits data after timeout.
* **T1.5: Packet Corruption & Retransmission:**
    * Simulate corruption of a data segment (bad checksum). Verify receiver drops it and sender retransmits.
    * Simulate corruption of an ACK segment. Verify sender ignores it and retransmits data after timeout.
* **T1.6: Duplicate Packet Handling:**
    * Simulate a duplicated data segment. Verify receiver ACKs it but processes it only once.
    * Simulate a duplicated ACK. Verify sender handles it gracefully (e.g., ignores if already moved on).
* **T1.7: Successful Connection Termination:** Client and server perform FIN exchange and close connection.
* **T1.8: Unilateral Close (FIN from one side):** One side closes, the other acknowledges and also closes.

### 5.2. HTTP Layer Tests

* **T2.1: GET Request for Existing File:** Client requests `index.html`, server returns `200 OK` and the file content.
* **T2.2: GET Request for Non-Existing File:** Client requests a non-existent file, server returns `404 Not Found`.
* **T2.3: GET Request for Root (`/`):** Client requests `/`, server returns `index.html` (or default page) with `200 OK`.
* **T2.4: POST Request:** Client sends data via POST, server receives it and returns `200 OK` (response body might echo data or be a simple success message).
* **T2.5: Header Verification:** Check for required headers (`Content-Type`, `Content-Length`, `Server`, `Host`, `Connection: close`) in requests and responses.

### 5.3. Integration Tests (RUDP + HTTP with Errors)

* **T3.1: HTTP GET with Packet Loss:** Perform a GET request while RUDP layer simulates packet loss. Verify the request eventually succeeds due to retransmissions.
* **T3.2: HTTP POST with Packet Corruption:** Perform a POST request while RUDP layer simulates packet corruption. Verify the request eventually succeeds.
* **T3.3: Browser Compatibility (Bonus):** Attempt to access the RUDP HTTP server using a standard web browser (e.g., Firefox, Chrome) by typing `http://localhost:port/`. Verify the page loads and Wireshark shows the RUDP packets and HTTP messages. This will heavily depend on how strictly the browser expects TCP behavior.

