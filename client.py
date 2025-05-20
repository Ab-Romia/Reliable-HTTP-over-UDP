import asyncio
import logging

from rudp import RudpSocket

CLIENT = ('127.0.0.1', 3000)
SERVER = ('127.0.0.1', 8080)


async def main():
    socket = RudpSocket(CLIENT)
    request = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"

    await socket.connect(SERVER)
    await socket.send(SERVER, request)
    # Receive response
    async for addr, data in socket.recv():
        if addr == SERVER:
            print(f"Received from server: {data.decode()}")
    await socket.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format=f'client: %(message)s')
    asyncio.run(main())
