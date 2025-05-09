# http_client.py
import logging
import sys
from rudp import RUDPsocket, PAYLOAD_MSS  # Make sure rudp.py is in the same directory

# Configure basic logging for the client
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - HTTP-Client - %(message)s')

SERVER_ADDRESS = '127.0.0.1'
SERVER_PORT = 8080


def make_http_request(method, path, host, port, body=None, simulate_loss=0.0, simulate_corruption=0.0):
    client_socket = RUDPsocket(simulate_loss_rate=simulate_loss, simulate_corruption_rate=simulate_corruption)

    logging.info(f"Attempting to connect to {host}:{port}...")
    if not client_socket.connect((host, port)):
        logging.error("Failed to connect to the server.")
        return

    logging.info(f"Successfully connected to {host}:{port} via RUDP.")

    # 1. Construct HTTP Request
    request_lines = [
        f"{method.upper()} {path} HTTP/1.0",
        f"Host: {host}:{port}",
        f"Connection: close",  # Important for HTTP/1.0 and this server
        f"User-Agent: RUDP-HTTP-Client/1.0"
    ]

    if method.upper() == "POST" and body:
        # Ensure body is bytes
        if isinstance(body, str):
            body_bytes = body.encode('utf-8')
        else:
            body_bytes = body

        request_lines.append(f"Content-Type: application/x-www-form-urlencoded")  # Or other appropriate type
        request_lines.append(f"Content-Length: {len(body_bytes)}")
    else:
        body_bytes = b""

    request_header_str = "\r\n".join(request_lines) + "\r\n\r\n"
    full_request = request_header_str.encode('utf-8') + body_bytes

    logging.info(f"Sending HTTP Request:\n---\n{full_request.decode('utf-8', errors='ignore')[:500]}...\n---")

    # 2. Send HTTP Request in segments
    offset = 0
    while offset < len(full_request):
        chunk = full_request[offset: offset + PAYLOAD_MSS]
        if not client_socket.send_data(chunk):
            logging.error("Failed to send request chunk to server.")
            client_socket.close()
            return
        offset += len(chunk)
    logging.info("Full HTTP request sent.")

    # 3. Receive HTTP Response
    http_response_bytes = b""
    headers_received = False
    response_content_length = -1  # Default to unknown, rely on close or chunked encoding (not supported)

    # Receive headers first
    temp_buffer = b""
    while not headers_received:
        segment = client_socket.receive_data()
        if segment is None:  # Timeout or critical RUDP error
            logging.error("Failed to receive response headers from server (RUDP timeout/error).")
            client_socket.close()
            return
        if not segment:  # FIN received, connection closed by server
            logging.info("Connection closed by server while waiting for headers.")
            # This might be valid if server sends empty response and closes (e.g. for some errors)
            # Or if the request was bad and server closed immediately.
            if not temp_buffer:  # Nothing received at all
                logging.error("No response received from server before close.")
                client_socket.close()
                return
            # If some data was received, try to parse it
            headers_received = True  # Assume what we have is it
            http_response_bytes = temp_buffer
            break

        temp_buffer += segment
        if b"\r\n\r\n" in temp_buffer:
            headers_received = True
            header_part, _, body_start_part = temp_buffer.partition(b"\r\n\r\n")
            http_response_bytes = header_part + b"\r\n\r\n"

            # Parse Content-Length from headers
            try:
                header_str = header_part.decode('utf-8', errors='ignore')
                for line in header_str.split('\r\n'):
                    if line.lower().startswith('content-length:'):
                        response_content_length = int(line.split(':')[1].strip())
                        break
            except Exception as e:
                logging.warning(f"Could not parse Content-Length from response: {e}")
            break  # Exit header receiving loop

    if not headers_received and temp_buffer:  # Server closed connection after sending partial headers
        logging.warning("Connection closed by server after partial headers. Processing what was received.")
        http_response_bytes = temp_buffer  # Process what we got

    if not http_response_bytes:
        logging.error("No response data received at all.")
        client_socket.close()
        return

    # Receive body
    # body_start_part might contain the beginning of the body if it came with the header segment
    current_body_bytes = body_start_part if 'body_start_part' in locals() else b""

    if response_content_length != -1:  # We know the length
        while len(current_body_bytes) < response_content_length:
            remaining_to_read = response_content_length - len(current_body_bytes)
            segment = client_socket.receive_data()  # Max PAYLOAD_MSS
            if segment is None:
                logging.error("Failed to receive response body (RUDP timeout/error).")
                break
            if not segment:  # Connection closed prematurely
                logging.warning("Connection closed by server before full body received (based on Content-Length).")
                break
            current_body_bytes += segment
    else:  # Content-Length not found or invalid, read until connection close
        logging.info("Content-Length not found or invalid. Reading response body until connection closes.")
        while True:
            segment = client_socket.receive_data()
            if segment is None:  # Timeout
                logging.warning("Timeout while reading response body (no Content-Length). Assuming end of data.")
                break
            if not segment:  # FIN received, means end of data
                logging.info("Connection closed by server, assuming end of response body.")
                break
            current_body_bytes += segment

    http_response_bytes += current_body_bytes

    logging.info(f"\n\n--- HTTP Response Received (Total {len(http_response_bytes)} bytes) ---")
    # Try to decode and print nicely. If binary (like image), it might look messy.
    try:
        response_str_decoded = http_response_bytes.decode('utf-8', errors='ignore')
        print(response_str_decoded)
        # If you want to save binary content, check Content-Type and save to a file
        # For example:
        # if b"Content-Type: image/png" in http_response_bytes:
        # with open("received_image.png", "wb") as f:
        #    img_data_offset = http_response_bytes.find(b"\r\n\r\n") + 4
        #    f.write(http_response_bytes[img_data_offset:])
        #    print("\n[Saved image as received_image.png]")

    except UnicodeDecodeError:
        print(http_response_bytes)  # Print raw bytes if not decodable
    logging.info("--- End of HTTP Response ---")

    client_socket.close()
    logging.info("Connection closed.")


if __name__ == "__main__":
    # Default request: GET /index.html
    req_method = "GET"
    req_path = "/index.html"
    req_body = None
    loss_rate = 0.0
    corruption_rate = 0.0

    # Basic command-line argument parsing
    # python http_client.py GET /test.txt
    # python http_client.py POST /submitform "name=test&data=123"
    # python http_client.py GET /image.png --loss 0.1 --corrupt 0.05

    args = sys.argv[1:]
    if len(args) >= 2:
        req_method = args[0].upper()
        req_path = args[1]
        if req_method == "POST" and len(args) >= 3:
            if not args[2].startswith("--"):  # Assuming body is the third arg if not an option
                req_body = args[2]
                args = args[3:]  # Remaining args are options
            else:
                args = args[2:]
        else:
            args = args[2:]

    # Parse options like --loss and --corrupt
    i = 0
    while i < len(args):
        if args[i] == "--loss" and i + 1 < len(args):
            try:
                loss_rate = float(args[i + 1])
                i += 1
            except ValueError:
                print(f"Invalid value for --loss: {args[i + 1]}")
        elif args[i] == "--corrupt" and i + 1 < len(args):
            try:
                corruption_rate = float(args[i + 1])
                i += 1
            except ValueError:
                print(f"Invalid value for --corrupt: {args[i + 1]}")
        i += 1

    print(f"Client Config: Method={req_method}, Path={req_path}, Loss={loss_rate}, Corruption={corruption_rate}")
    if req_body:
        print(f"Body: {req_body[:100]}...")

    make_http_request(req_method, req_path, SERVER_ADDRESS, SERVER_PORT, body=req_body,
                      simulate_loss=loss_rate, simulate_corruption=corruption_rate)

    # Example Test Cases (can be uncommented or run via command line)
    # print("\n--- Test Case: GET /index.html ---")
    # make_http_request("GET", "/index.html", SERVER_ADDRESS, SERVER_PORT)

    # print("\n--- Test Case: GET /test.txt ---")
    # make_http_request("GET", "/test.txt", SERVER_ADDRESS, SERVER_PORT)

    # print("\n--- Test Case: GET /nonexistent.html (404) ---")
    # make_http_request("GET", "/nonexistent.html", SERVER_ADDRESS, SERVER_PORT)

    # print("\n--- Test Case: POST data ---")
    # make_http_request("POST", "/submit", SERVER_ADDRESS, SERVER_PORT, body="field1=value1&field2=value2")

    # print("\n--- Test Case: GET /index.html with 20% packet loss ---")
    # make_http_request("GET", "/index.html", SERVER_ADDRESS, SERVER_PORT, simulate_loss=0.2)

    # print("\n--- Test Case: GET /test.txt with 10% checksum corruption ---")
    # make_http_request("GET", "/test.txt", SERVER_ADDRESS, SERVER_PORT, simulate_corruption=0.1)

