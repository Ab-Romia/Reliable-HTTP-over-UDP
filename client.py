import logging
import sys
from reliable_udp import Socket, MAX_PAYLOAD_SIZE  # Using the refactored RUDP Socket

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - HTTP-Client - %(message)s')

DEFAULT_SERVER_HOST = '127.0.0.1'
DEFAULT_SERVER_PORT = 8080


def _execute_http_request(method, path, host, port, body_str=None, loss=0.0, corrupt=0.0):
    rudp_sock = Socket(loss_rate=loss, corruption_rate=corrupt)
    logging.info(f"HTTP-Client: Attempting RUDP connect to {host}:{port}...")
    if not rudp_sock.connect((host, port)):
        logging.error("HTTP-Client: RUDP connection failed.");
        return None

    logging.info(f"HTTP-Client: RUDP connected to {host}:{port}.")

    headers = [
        f"{method.upper()} {path} HTTP/1.0",
        f"Host: {host}:{port}",
        "Connection: close",
        "User-Agent: RUDP-HTTP-Client/Project"
    ]
    body_bytes = b""
    if method.upper() == "POST" and body_str:
        body_bytes = body_str.encode('utf-8')
        headers.append("Content-Type: application/x-www-form-urlencoded")
        headers.append(f"Content-Length: {len(body_bytes)}")

    req_str = "\r\n".join(headers) + "\r\n\r\n"
    full_req = req_str.encode('utf-8') + body_bytes
    logging.info(f"HTTP-Client: Sending request ({len(full_req)} bytes): {req_str.splitlines()[0]} ...")

    offset = 0
    while offset < len(full_req):  # Send request in chunks
        chunk = full_req[offset: offset + MAX_PAYLOAD_SIZE]
        if not rudp_sock.send_data(chunk):
            logging.error("HTTP-Client: RUDP send_data failed for request.");
            rudp_sock.close();
            return None
        offset += len(chunk)
    logging.info("HTTP-Client: Full request sent.")

    resp_bytes = b""
    headers_done = False
    body_len = -1
    temp_buf = b""
    initial_body = b""

    while not headers_done:  # Receive headers
        seg = rudp_sock.receive_data()
        if seg is None or not seg: logging.error("HTTP-Client: Failed to get headers."); rudp_sock.close(); return None
        temp_buf += seg
        if b"\r\n\r\n" in temp_buf:
            headers_done = True
            header_part, _, initial_body = temp_buf.partition(b"\r\n\r\n")
            resp_bytes = header_part + b"\r\n\r\n"
            for line in header_part.decode('utf-8', 'ignore').split('\r\n'):
                if line.lower().startswith('content-length:'):
                    try:
                        body_len = int(line.split(':', 1)[1].strip())
                    except:
                        pass; break
            break

    if not resp_bytes and not initial_body: logging.error(
        "HTTP-Client: No response headers."); rudp_sock.close(); return None

    current_body = initial_body
    if body_len != -1:  # Known length
        while len(current_body) < body_len:
            seg = rudp_sock.receive_data()
            if seg is None or not seg: logging.warning("HTTP-Client: Connection issue getting body (known len)."); break
            current_body += seg
    else:  # Unknown length
        while True:
            seg = rudp_sock.receive_data()
            if seg is None: logging.warning("HTTP-Client: Timeout getting body (unknown len)."); break
            if not seg: logging.info("HTTP-Client: Server closed connection (end of body)."); break
            current_body += seg

    resp_bytes += current_body
    logging.info(f"HTTP-Client: Response received ({len(resp_bytes)} bytes).")
    print("\n--- SERVER RESPONSE ---")
    try:
        print(resp_bytes.decode('utf-8', errors='replace'))
    except:
        print(resp_bytes)  # Raw if decode fails
    print("--- END OF RESPONSE ---\n")

    rudp_sock.close()
    logging.info("HTTP-Client: RUDP connection closed.")
    return resp_bytes


def main():
    method, path, body, loss, corrupt = "GET", "/index.html", None, 0.0, 0.0
    host, port = DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT

    args = sys.argv[1:]
    if args: method = args.pop(0).upper()
    if args: path = args.pop(0)
    if method == "POST" and args and not args[0].startswith("--"): body = args.pop(0)

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--loss" and i + 1 < len(args):
            loss = float(args[i + 1]); i += 1
        elif arg == "--corrupt" and i + 1 < len(args):
            corrupt = float(args[i + 1]); i += 1
        elif arg == "--host" and i + 1 < len(args):
            host = args[i + 1]; i += 1
        elif arg == "--port" and i + 1 < len(args):
            port = int(args[i + 1]); i += 1
        else:
            print(f"Unknown option: {arg}")
        i += 1

    print(f"HTTP-Client: Requesting {method} {path} from {host}:{port} (Loss={loss * 100}%, Corrupt={corrupt * 100}%)")
    if body: print(f"Body: {body[:100]}...")

    _execute_http_request(method, path, host, port, body_str=body, loss=loss, corrupt=corrupt)


if __name__ == "__main__":
    main()
