from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import uvicorn
import asyncio
import os
from rudp import RudpSocket

app = FastAPI()

# Create templates directory if it doesn't exist
os.makedirs("templates", exist_ok=True)

# Initialize templates
templates = Jinja2Templates(directory="templates")

# Your HTTP server details
SERVER = ('127.0.0.1', 8080)


async def send_request_to_server(method, path, data=None):
    """Helper function to send requests to your custom HTTP server"""
    try:
        client_socket = RudpSocket(('127.0.0.1', 3000))

        # Build request
        if method == 'GET':
            request = f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        else:  # POST
            content_length = len(data) if data else 0
            request = f"POST {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n" \
                      f"Content-Length: {content_length}\r\nContent-Type: application/x-www-form-urlencoded\r\n\r\n"
            if data:
                request += data

        print(f"Connecting to server at {SERVER}")
        await client_socket.connect(SERVER)
        print(f"Sending {method} request to {path}")
        await client_socket.send(SERVER, request.encode())

        # Variables to track response
        response_data = b""

        # Add timeout for receive operations
        timeout = 5.0  # 5 seconds
        start_time = asyncio.get_event_loop().time()

        # Receive with timeout
        async for addr, data in client_socket.recv():
            if addr == SERVER:
                print(f"Received {len(data)} bytes from server")
                response_data += data

                # Simple check if we have a complete HTTP response
                if b"\r\n\r\n" in response_data:
                    if method == "GET" or response_data.count(b"\r\n\r\n") > 0:
                        print("Complete response detected")
                        break

            # Check timeout
            if asyncio.get_event_loop().time() - start_time > timeout:
                print("Request timed out")
                break

        # Close the connection
        await client_socket.close()
        print("Connection closed")

        # Return whatever we received
        return response_data.decode(errors='replace')

    except Exception as e:
        print(f"Error communicating with server: {e}")
        return f"Error communicating with server: {e}"


# Create index.html template
with open("templates/index.html", "w") as f:
    f.write("""
<!DOCTYPE html>
<html>
<head>
    <title>UDP HTTP Server Demo</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; }
        .container { max-width: 800px; margin: 0 auto; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; }
        input, textarea { width: 100%; padding: 8px; box-sizing: border-box; }
        button { padding: 10px 15px; background: #4CAF50; color: white; border: none; cursor: pointer; }
        pre { background: #f4f4f4; padding: 15px; border-radius: 5px; overflow: auto; }
    </style>
</head>
<body>
    <div class="container">
        <h1>UDP HTTP Server Demo</h1>

        <h2>GET Request</h2>
        <form action="/get-request" method="get">
            <div class="form-group">
                <label for="get-path">Path:</label>
                <input type="text" id="get-path" name="path" value="/index.html" required>
            </div>
            <button type="submit">Send GET Request</button>
        </form>

        <h2>POST Request</h2>
        <form action="/post-request" method="post">
            <div class="form-group">
                <label for="post-path">Path:</label>
                <input type="text" id="post-path" name="path" value="/submit" required>
            </div>
            <div class="form-group">
                <label for="post-data">Data:</label>
                <textarea id="post-data" name="data" rows="5" required>name=John&age=30</textarea>
            </div>
            <button type="submit">Send POST Request</button>
        </form>

        {% if response %}
        <h2>Response</h2>
        <pre>{{ response }}</pre>
        {% endif %}
    </div>
</body>
</html>
    """)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "response": None})


@app.get("/get-request", response_class=HTMLResponse)
async def get_request(request: Request, path: str):
    print(f"Processing GET request to {path}")
    response = await send_request_to_server('GET', path)
    return templates.TemplateResponse("index.html", {"request": request, "response": response})


@app.post("/post-request", response_class=HTMLResponse)
async def post_request(request: Request, path: str = Form(...), data: str = Form(...)):
    print(f"Processing POST request to {path}")
    response = await send_request_to_server('POST', path, data)
    return templates.TemplateResponse("index.html", {"request": request, "response": response})


if __name__ == "__main__":
    uvicorn.run("web_interface:app", host="127.0.0.1", port=5000, log_level="info")