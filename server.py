import asyncio
import logging
import argparse

import os
from httptools import HttpRequestParser

from rudp import RudpSocket
from utils import Parser, log


async def main(args):
    SERVER = (args.host, args.port)
    socket = RudpSocket(SERVER)
    socket.listen()

    while True:
        send_response = False
        response = ""
        client_addr = None
        parser = None

        def on_message_complete(self: Parser):
            nonlocal parser, send_response, response
            self.complete = True

            request_method = parser.get_method().decode()

            if request_method == 'GET':
                # Prevent path traversal by normalizing the path
                safe_path = os.path.normpath(self.url.lstrip('/'))
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
                post_data = self.body.decode('utf-8', errors='replace') if self.body else "No data"
                log(f"POST received to {self.url}")
                log(f"POST data: {post_data}")

                response_body = f"Received POST data for {self.url}: {post_data}"
                response = f"HTTP/1.1 200 OK\r\nContent-Length: {len(response_body)}\r\nContent-Type: text/plain\r\n\r\n{response_body}"
                send_response = True

            else:
                response = "HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot Found"
                send_response = True

        parser = HttpRequestParser(Parser(on_message_complete))

        try:
            # Handle a single client connection
            async for addr, data in socket.recv():
                if client_addr is None:
                    client_addr = addr
                    log(f"New connection from {addr[0]}:{addr[1]}")

                # ignore packets from other clients
                if addr != client_addr:
                    continue

                parser.feed_data(data)

                if send_response:
                    log(f"Sending {parser.get_method()} response to {addr[0]}:{addr[1]}")
                    await socket.send(addr, response.encode())
                    log("Response sent, ready for new connections")
                    break  # Exit this connection's loop to reset for next client

            log("Waiting for next client connection...")
        except Exception as e:
            log(f"Error handling client: {e}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='HTTP Server supporting GET and POST requests')
    parser.add_argument('--host', default='127.0.0.1', help='Server host address')
    parser.add_argument('--port', type=int, default=8080, help='Server port')

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format=f'server: %(message)s')
    asyncio.run(main(args))
