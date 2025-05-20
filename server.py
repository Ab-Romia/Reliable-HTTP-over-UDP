import asyncio
import logging
import argparse

import os
from httptools import HttpRequestParser

from rudp import RudpSocket
from utils import Parser, log

SERVER = ('127.0.0.1', 8080)


async def main(args):
    socket = RudpSocket(SERVER)
    socket.listen()
    log(f"Server listening on {SERVER[0]}:{SERVER[1]}")

    while True:
        # Reset state for new connection
        client_addr = None
        send_response = False
        response = ""
        request_method = None
        request_url = None
        received_data = b""

        # Create a new parser for each connection
        def on_url(self, url):
            nonlocal request_url
            request_url = url.decode('utf-8')

        def on_header(self, name, value):
            # Store headers if needed
            pass

        def on_headers_complete(self):
            nonlocal request_method
            pass

        def on_body(self, body):
            nonlocal received_data
            received_data += body
            log(f"Received body data: {body.decode('utf-8', errors='replace')}")

        def on_message_complete(self):
            self.complete = True
            nonlocal send_response, response, request_method, request_url, received_data

            if request_method == 'GET':
                # Prevent path traversal by normalizing the path
                safe_path = os.path.normpath(request_url.lstrip('/'))
                file_path = os.path.join('web', safe_path)

                if os.path.isfile(file_path):
                    with open(file_path, 'r', encoding='utf-8', errors='replace') as file:
                        content = file.read()
                        mime_type = 'text/html' if file_path.endswith('.html') else 'text/plain'
                        response = f"HTTP/1.1 200 OK\r\nContent-Length: {len(content)}\r\nContent-Type: {mime_type}\r\n\r\n{content}"
                else:
                    response = "HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot Found"
                send_response = True

            elif request_method == 'POST':
                post_data = received_data.decode('utf-8', errors='replace') if received_data else "No data"
                log(f"POST received to {request_url}")
                log(f"POST data: {post_data}")

                response_body = f"Received POST data for {request_url}: {post_data}"
                response = f"HTTP/1.1 200 OK\r\nContent-Length: {len(response_body)}\r\nContent-Type: text/plain\r\n\r\n{response_body}"
                send_response = True

            else:
                response = "HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot Found"
                send_response = True

        parser = Parser()
        parser.on_url = on_url.__get__(parser)
        parser.on_header = on_header.__get__(parser)
        parser.on_headers_complete = on_headers_complete.__get__(parser)
        parser.on_body = on_body.__get__(parser)
        parser.on_message_complete = on_message_complete.__get__(parser)
        http_parser = HttpRequestParser(parser)

        try:
            # Handle a single client connection
            async for addr, data in socket.recv():
                if client_addr is None:
                    client_addr = addr
                    log(f"New connection from {addr[0]}:{addr[1]}")

                    # Extract method from the first line of request
                    if data.startswith(b'GET'):
                        request_method = 'GET'
                        log(f"Detected GET request")
                    elif data.startswith(b'POST'):
                        request_method = 'POST'
                        log(f"Detected POST request")

                # ignore packets from other clients
                if addr != client_addr:
                    continue

                http_parser.feed_data(data)

                if send_response:
                    log(f"Sending {request_method} response to {addr[0]}:{addr[1]}")
                    await socket.send(addr, response.encode())
                    log("Response sent, ready for new connections")
                    break  # Exit this connection's loop to reset for next client

            log("Waiting for next client connection...")
        except Exception as e:
            log(f"Error handling client: {e}")

            # Continue to accept new connections even after an error


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='HTTP Server supporting GET and POST requests')
    parser.add_argument('--host', default='127.0.0.1', help='Server host address')
    parser.add_argument('--port', type=int, default=8080, help='Server port')

    args = parser.parse_args()
    SERVER = (args.host, args.port)

    logging.basicConfig(level=logging.INFO, format=f'server: %(message)s')
    asyncio.run(main(args))