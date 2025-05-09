# http_server.py
import os
import logging
from rudp import RUDPsocket, PAYLOAD_MSS  # Make sure rudp.py is in the same directory or Python path

# Configure basic logging for the server
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - HTTP-Server - %(message)s')

SERVER_ADDRESS = '127.0.0.1'
SERVER_PORT = 8080
WWW_ROOT = './www'  # Directory for website files

# Create a dummy index.html and other files for testing
if not os.path.exists(WWW_ROOT):
    os.makedirs(WWW_ROOT)
if not os.path.exists(os.path.join(WWW_ROOT, 'index.html')):
    with open(os.path.join(WWW_ROOT, 'index.html'), 'w') as f:
        f.write("<html><body><h1>Hello from RUDP HTTP Server!</h1><p>This is index.html.</p></body></html>")
if not os.path.exists(os.path.join(WWW_ROOT, 'test.txt')):
    with open(os.path.join(WWW_ROOT, 'test.txt'), 'w') as f:
        f.write("This is a test file served over RUDP.")
if not os.path.exists(os.path.join(WWW_ROOT, 'image.png')):  # Placeholder for image test
    try:
        # Create a tiny valid PNG (1x1 transparent pixel)
        # You might need Pillow for more complex images, or just use a pre-existing small image
        from PIL import Image

        img = Image.new('RGBA', (1, 1), (0, 0, 0, 0))  # Transparent
        img.save(os.path.join(WWW_ROOT, 'image.png'), "PNG")
    except ImportError:
        # Create a dummy text file if Pillow is not available
        with open(os.path.join(WWW_ROOT, 'image.png'), 'w') as f:
            f.write("This should be an image, but Pillow is not installed.")
        logging.warning("Pillow not installed. image.png is a text file.")


def handle_http_request(rudp_conn, client_address):
    """Handles a single HTTP request from a connected client."""
    try:
        # 1. Receive HTTP Request
        # HTTP requests are text, ending with \r\n\r\n
        # We need to receive data until we find this marker.
        http_request_bytes = b""
        headers_received = False
        content_length = 0

        # First, get headers
        while not headers_received:
            segment = rudp_conn.receive_data()
            if segment is None:  # Timeout or critical error
                logging.error(f"Failed to receive request headers from {client_address}.")
                return
            if not segment:  # FIN received or connection closed prematurely by client
                logging.info(f"Connection closed by {client_address} while receiving headers.")
                return

            http_request_bytes += segment
            if b"\r\n\r\n" in http_request_bytes:
                headers_received = True
                header_part, _, body_start_part = http_request_bytes.partition(b"\r\n\r\n")
                http_request_bytes = header_part + b"\r\n\r\n"  # Store only headers for now
                # Check for Content-Length
                header_lines = header_part.decode('utf-8', errors='ignore').split('\r\n')
                for line in header_lines:
                    if line.lower().startswith('content-length:'):
                        try:
                            content_length = int(line.split(':')[1].strip())
                        except ValueError:
                            logging.warning("Invalid Content-Length header.")
                            # Potentially send 400 Bad Request here
                break  # Exit header receiving loop

        # If there's a body (POST request), receive it
        body_bytes = body_start_part  # Part of body might have been received with headers
        while len(body_bytes) < content_length:
            remaining_bytes_to_read = content_length - len(body_bytes)
            segment = rudp_conn.receive_data()  # Max PAYLOAD_MSS per call
            if segment is None:
                logging.error(f"Failed to receive request body from {client_address}.")
                return
            if not segment:
                logging.info(f"Connection closed by {client_address} while receiving body.")
                return
            body_bytes += segment

        http_request_str = http_request_bytes.decode('utf-8', errors='ignore')
        logging.info(f"Received HTTP request from {client_address}:\n---\n{http_request_str[:500]}...\n---")
        if content_length > 0:
            logging.info(f"Received POST body ({content_length} bytes). First 100: {body_bytes[:100]}")

        # 2. Parse HTTP Request
        request_lines = http_request_str.split('\r\n')
        if not request_lines:
            send_error_response(rudp_conn, 400, "Bad Request", "Empty request.")
            return

        request_line = request_lines[0]
        try:
            method, path, version = request_line.split(' ')
        except ValueError:
            send_error_response(rudp_conn, 400, "Bad Request", "Malformed request line.")
            return

        if version.upper() not in ["HTTP/1.0", "HTTP/1.1"]:  # We aim for 1.0 compatibility
            send_error_response(rudp_conn, 505, "HTTP Version Not Supported", f"{version} not supported.")
            return

        # 3. Process Request
        response_body = b""
        status_code = 200
        status_message = "OK"
        content_type = "text/html; charset=utf-8"  # Default

        if method.upper() == "GET":
            if path == "/":
                path = "/index.html"  # Default page

            file_path = os.path.join(WWW_ROOT, path.lstrip('/'))
            file_path = os.path.normpath(file_path)  # Normalize path

            # Security: Check if the path is still within WWW_ROOT
            if not file_path.startswith(os.path.normpath(WWW_ROOT)):
                send_error_response(rudp_conn, 403, "Forbidden", "Access denied.")
                return

            if os.path.exists(file_path) and os.path.isfile(file_path):
                with open(file_path, 'rb') as f:
                    response_body = f.read()

                if file_path.endswith(".html") or file_path.endswith(".htm"):
                    content_type = "text/html; charset=utf-8"
                elif file_path.endswith(".txt"):
                    content_type = "text/plain; charset=utf-8"
                elif file_path.endswith(".jpg") or file_path.endswith(".jpeg"):
                    content_type = "image/jpeg"
                elif file_path.endswith(".png"):
                    content_type = "image/png"
                elif file_path.endswith(".css"):
                    content_type = "text/css"
                elif file_path.endswith(".js"):
                    content_type = "application/javascript"
                else:
                    content_type = "application/octet-stream"  # Generic binary
                logging.info(f"Serving file: {file_path} with Content-Type: {content_type}")
            else:
                status_code = 404
                status_message = "Not Found"
                response_body = f"<html><body><h1>404 Not Found</h1><p>The requested resource {path} was not found.</p></body></html>".encode(
                    'utf-8')
                content_type = "text/html; charset=utf-8"
                logging.warning(f"File not found: {file_path}")

        elif method.upper() == "POST":
            # For this project, echo back the POST data or a success message
            status_code = 200
            status_message = "OK"
            response_body = f"<html><body><h1>POST Request Received</h1><p>Server received your POST data:</p><pre>{body_bytes.decode('utf-8', errors='ignore')}</pre></body></html>".encode(
                'utf-8')
            content_type = "text/html; charset=utf-8"
            logging.info(f"Processed POST request to {path}.")

        else:
            status_code = 501
            status_message = "Not Implemented"
            response_body = f"<html><body><h1>501 Not Implemented</h1><p>Method {method} is not implemented by this server.</p></body></html>".encode(
                'utf-8')
            content_type = "text/html; charset=utf-8"
            logging.warning(f"Method not implemented: {method}")

        # 4. Send HTTP Response
        response_headers = [
            f"HTTP/1.0 {status_code} {status_message}",
            f"Server: RUDP-HTTP-Server/1.0",
            f"Content-Type: {content_type}",
            f"Content-Length: {len(response_body)}",
            f"Connection: close"  # HTTP/1.0 default, but explicit
        ]
        response_str = "\r\n".join(response_headers) + "\r\n\r\n"
        full_response = response_str.encode('utf-8') + response_body

        # Send response in segments
        offset = 0
        while offset < len(full_response):
            chunk = full_response[offset: offset + PAYLOAD_MSS]
            if not rudp_conn.send_data(chunk):
                logging.error(f"Failed to send response chunk to {client_address}.")
                return  # Error in sending
            offset += len(chunk)
        logging.info(f"Sent HTTP response ({status_code}) to {client_address}. Total size: {len(full_response)} bytes.")

    except Exception as e:
        logging.error(f"Error handling request from {client_address}: {e}", exc_info=True)
        try:
            send_error_response(rudp_conn, 500, "Internal Server Error", "An error occurred on the server.")
        except Exception as e_resp:
            logging.error(f"Failed to send 500 error response: {e_resp}")
    finally:
        logging.info(f"Closing connection with {client_address}.")
        rudp_conn.close()


def send_error_response(rudp_conn, code, message, body_text):
    """Helper to send an error response."""
    body = f"<html><body><h1>{code} {message}</h1><p>{body_text}</p></body></html>".encode('utf-8')
    headers = [
        f"HTTP/1.0 {code} {message}",
        f"Server: RUDP-HTTP-Server/1.0",
        f"Content-Type: text/html; charset=utf-8",
        f"Content-Length: {len(body)}",
        f"Connection: close"
    ]
    response_str = "\r\n".join(headers) + "\r\n\r\n"
    full_response = response_str.encode('utf-8') + body

    offset = 0
    while offset < len(full_response):
        chunk = full_response[offset: offset + PAYLOAD_MSS]
        if not rudp_conn.send_data(chunk):
            logging.error(f"Failed to send error response chunk ({code}).")
            break
        offset += len(chunk)
    logging.info(f"Sent error response {code} {message}.")


def main():
    # Set simulate_loss_rate or simulate_corruption_rate for testing RUDP reliability
    # Example: server_socket = RUDPsocket(simulate_loss_rate=0.1) # 10% packet loss
    server_socket = RUDPsocket(local_addr=(SERVER_ADDRESS, SERVER_PORT))
    server_socket.listen()
    logging.info(f"RUDP HTTP Server listening on {SERVER_ADDRESS}:{SERVER_PORT}")
    logging.info(f"Serving files from: {os.path.abspath(WWW_ROOT)}")

    try:
        while True:
            try:
                # For this project, accept() in RUDPsocket is simplified and the same socket
                # instance handles the connection after handshake.
                # A more robust server would spawn a new thread/socket for each client.
                # Here, we make it iterative: handle one client fully.

                # The RUDPsocket's accept() method will block until a handshake is complete.
                # It modifies the server_socket instance to be "connected" to the client.
                # This is a simplification for the project.
                # To handle multiple clients, you'd typically have a listening socket that,
                # upon receiving a SYN, creates a *new* RUDPsocket for that client connection.
                # Our current RUDPsocket.accept() returns `self` or `None`.

                # Let's re-instantiate or reset for each client for cleaner state.
                # This is a workaround for the current RUDPsocket design for an iterative server.
                # A better RUDP accept() would return a new 'connected' socket.

                # For this project, we'll reuse the same server_socket instance after it completes a connection.
                # This means it can only handle one client at a time and needs to be "reset" conceptually.
                # The RUDPsocket.accept() as written modifies its own state to ESTABLISHED with one peer.
                # After client disconnects (close() is called), server_socket.state should go back to LISTEN.
                # This is not fully implemented in the RUDP close() logic to revert to LISTEN.

                # Simplification: Create a new RUDPsocket for each accept cycle if the previous one closed.
                # This is not ideal but fits the current RUDP class structure for an iterative server.

                # Let's assume the RUDPsocket instance `server_socket` is the listening socket.
                # When `accept()` succeeds, it transitions `server_socket` to be the connection socket.
                # After `handle_http_request` (which calls `rudp_conn.close()`),
                # we need a way for `server_socket` to go back to listening.

                # The current RUDP `accept` modifies `self` to become the connection.
                # This is not standard for a listening socket.
                # A true listening socket would create a new socket for the connection.
                # Given the prompt, we'll use this simplified model.
                # The server will handle one client, then the RUDP socket closes.
                # To accept another, we'd need to re-initialize and re-listen.

                # Let's make the server truly iterative:
                # One RUDPsocket instance per client connection attempt.

                current_connection_socket = RUDPsocket(local_addr=(SERVER_ADDRESS, SERVER_PORT))
                # simulate packet loss/corruption on server side if desired
                # current_connection_socket.simulate_loss_rate = 0.1 # 10% loss
                # current_connection_socket.simulate_corruption_rate = 0.05 # 5% corruption

                current_connection_socket.listen()  # Put it in listen state

                # The accept method in RUDPsocket is blocking and then the same object handles the connection.
                # This is a bit unusual for a server socket, but we'll work with it.
                # It returns `self` if successful.
                if current_connection_socket.accept():  # This blocks until a client connects and handshake is done
                    logging.info(f"Accepted connection from {current_connection_socket.peer_addr}")
                    handle_http_request(current_connection_socket, current_connection_socket.peer_addr)
                    # handle_http_request calls current_connection_socket.close()
                    logging.info(
                        f"Finished handling client {current_connection_socket.peer_addr}. Waiting for new connection.")
                else:
                    logging.warning("Failed to accept a client connection. Retrying listen.")
                    # If accept failed to establish, the socket might be in a weird state.
                    # Re-creating it for the next listen cycle is safer.

                # The RUDPsocket is closed by handle_http_request.
                # The loop will create a new one for the next client.
            except OSError as e:
                if e.errno == 98:
                    logging.error(f"Address already in use: {SERVER_ADDRESS}:{SERVER_PORT}.")
                    break
                else:
                    logging.error(f"Socket error: {e}", exc_info=True)

    except KeyboardInterrupt:
        logging.info("HTTP Server shutting down.")
    except Exception as e:
        logging.error(f"HTTP Server encountered a fatal error: {e}", exc_info=True)
    finally:
        # server_socket.close() # Original server_socket is not used this way anymore
        logging.info("Server stopped.")


if __name__ == "__main__":
    main()
