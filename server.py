import os
import logging
import time  # For potential delays in server loop
from reliable_udp import Socket, MAX_PAYLOAD_SIZE  # Using the refactored RUDP Socket

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - HTTP-Server - %(message)s')

SERVER_ADDRESS = '127.0.0.1'
SERVER_PORT = 8080
ROOT = './content_rudp'


def directory_server():
    if not os.path.exists(ROOT):
        os.makedirs(ROOT)

    files = {
        "index.html": "<html><body><h1>RUDP Server Test</h1><a href='/test.txt'>Test File</a><form method='POST' action='/post_test'><input type='text' name='data' value='Hello RUDP'><input type='submit' value='POST Test'></form></body></html>",
        "test.txt": "This is a test file for the RUDP HTTP server."
    }
    for name, content in files.items():
        path = os.path.join(ROOT, name)
        if not os.path.exists(path):
            with open(path, 'w') as f: f.write(content)


def _get_mime_type_server(filepath):
    if filepath.endswith(".html"): return "text/html; charset=utf-8"
    if filepath.endswith(".txt"): return "text/plain; charset=utf-8"
    return "application/octet-stream" # Default for unknown types


def _send_response(rudp_conn, status_code, status_msg, content_type, body_bytes):
    headers = [
        f"HTTP/1.0 {status_code} {status_msg}",
        "Server: RUDP-HTTP-Server/1.0",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body_bytes)}",
        "Connection: close"
    ]
    response_str = "\r\n".join(headers) + "\r\n\r\n"
    full_response = response_str.encode('utf-8') + body_bytes

    offset = 0
    while offset < len(full_response):
        chunk = full_response[offset: offset + MAX_PAYLOAD_SIZE]
        if not rudp_conn.send_data(chunk):
            logging.error("HTTP-Server: RUDP send_data failed for response chunk.")
            return False
        offset += len(chunk)
    logging.info(f"HTTP-Server: Sent response {status_code} {status_msg}")
    return True


def _handle_client_request(rudp_conn, client_addr):
    try:
        raw_request = b""
        headers_done = False
        body_len = 0
        temp_buf = b""

        while not headers_done:  # Receive headers
            seg = rudp_conn.receive_data()
            if seg is None or not seg: logging.error(f"HTTP-Server: Failed to get headers from {client_addr}"); return
            temp_buf += seg
            if b"\r\n\r\n" in temp_buf:
                headers_done = True
                header_part, _, body_start = temp_buf.partition(b"\r\n\r\n")
                raw_request = header_part + b"\r\n\r\n"
                for line in header_part.decode('utf-8', 'ignore').split('\r\n'):
                    if line.lower().startswith('content-length:'):
                        try:
                            body_len = int(line.split(':', 1)[1].strip())
                        except:
                            pass
                        break

        req_body = body_start
        while len(req_body) < body_len:  # Receive body
            seg = rudp_conn.receive_data()
            if seg is None or not seg: logging.error(f"HTTP-Server: Failed to get body from {client_addr}"); return
            req_body += seg

        decoded_headers = raw_request.decode('utf-8', 'ignore')
        logging.info(f"HTTP-Server: Request from {client_addr}:\n{decoded_headers.splitlines()[0]} ...")

        req_lines = decoded_headers.split('\r\n')
        if not req_lines or not req_lines[0]:
            _send_response(rudp_conn, 400, "Bad Request", "text/html", b"<h1>400 Bad Request</h1>")
            return

        method, path, _ = req_lines[0].split(' ', 2)

        if method.upper() == "GET":
            if path == "/": path = "/index.html"
            fpath = os.path.normpath(os.path.join(ROOT, path.lstrip('/')))
            if not fpath.startswith(os.path.normpath(ROOT)) or not os.path.isfile(fpath):
                _send_response(rudp_conn, 404, "Not Found", "text/html", b"<h1>404 Not Found</h1>")
            else:
                with open(fpath, 'rb') as f:
                    content = f.read()
                _send_response(rudp_conn, 200, "OK", _get_mime_type_server(fpath), content)
        elif method.upper() == "POST":
            logging.info(f"HTTP-Server: POST to {path}, body: {req_body.decode('utf-8', 'ignore')[:100]}")
            resp_body = b"<h1>POST OK</h1><p>Received your data.</p>"
            _send_response(rudp_conn, 200, "OK", "text/html; charset=utf-8", resp_body)
        else:
            _send_response(rudp_conn, 501, "Not Implemented", "text/html", b"<h1>501 Not Implemented</h1>")

    except Exception as e:
        logging.error(f"HTTP-Server: Error handling client {client_addr}: {e}", exc_info=True)
        try:
            _send_response(rudp_conn, 500, "Internal Server Error", "text/html", b"<h1>500 Error</h1>")
        except:
            pass  # Avoid error in error handling
    finally:
        logging.info(f"HTTP-Server: Closing connection with {client_addr}.")
        rudp_conn.close()


def main():
    directory_server()
    logging.info(f"HTTP-Server: Starting on {SERVER_ADDRESS}:{SERVER_PORT}, serving from {os.path.abspath(ROOT)}")

    while True:  # Main server loop
        rudp_listener_socket = None
        try:
            rudp_listener_socket = Socket()  # New socket for each listen cycle
            rudp_listener_socket.bind((SERVER_ADDRESS, SERVER_PORT))
            if not rudp_listener_socket.listen():
                logging.error("HTTP-Server: Failed to listen. Retrying shortly.")
                if rudp_listener_socket: rudp_listener_socket.close()
                time.sleep(1);
                continue

            # RUDP accept() modifies the listener socket to become the connection socket
            connection_socket = rudp_listener_socket.accept()
            if connection_socket:  # Successfully accepted a client
                _handle_client_request(connection_socket, connection_socket.peer_addr)
                # _handle_client_request calls close() on the connection_socket
            else:  # Accept failed
                logging.warning("HTTP-Server: RUDP accept failed. Listening again.")
                # Ensure listener socket is closed if accept didn't transition it
                if rudp_listener_socket.state == 1:  # LISTEN state const
                    rudp_listener_socket.close()

        except OSError as e:
            if e.errno == 98:  # Address already in use
                logging.critical(f"HTTP-Server: Address {SERVER_PORT} in use. Stopping.");
                break
            else:
                logging.error(f"HTTP-Server: OS Error: {e}"); time.sleep(1)
        except KeyboardInterrupt:
            logging.info("HTTP-Server: Shutting down."); break
        except Exception as e:
            logging.error(f"HTTP-Server: Unhandled error: {e}", exc_info=True); break
        finally:
            # Ensure the socket for this iteration is closed if it exists and wasn't closed by handler
            if rudp_listener_socket and rudp_listener_socket.state != 0:  # 0 is CLOSED
                rudp_listener_socket.close()
    logging.info("HTTP-Server: Stopped.")


if __name__ == "__main__":
    main()
