import asyncio
import logging

from httptools import HttpRequestParser

from rudp import RudpSocket
from utils import Parser

SERVER = ('127.0.0.1', 8080)

send_response = False
response = ""


def on_message_complete(self):
    self.complete = True

    if self.url == '/index.html':
        with open('web/index.html', 'r') as file:
            html = file.read()

            global send_response
            global response
            response = f"HTTP/1.1 200 OK\r\nContent-Length: {len(html)}\r\n\r\n{html}"
            send_response = True


async def main():
    socket = RudpSocket(SERVER)
    socket.listen()

    parser = HttpRequestParser(Parser(on_message_complete))
    client_addr = None

    async for addr, data in socket.recv():
        # assuming 1 connection
        if client_addr is None:
            client_addr = addr

        # ignore packets from other clients
        if addr != client_addr:
            continue

        parser.feed_data(data)

        while not send_response:
            await asyncio.sleep(0.1)

        await socket.send(addr, response.encode())


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format=f'server: %(message)s')
    asyncio.run(main())
