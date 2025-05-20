import asyncio
import logging
import argparse
from utils import log
from rudp import RudpSocket
import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import queue

CLIENT = ('127.0.0.1', 3000)
SERVER = ('127.0.0.1', 8080)

# Queue for communication between GUI thread and asyncio thread
message_queue = queue.Queue()
response_queue = queue.Queue()


class HttpBrowser:
    def __init__(self, root):
        self.root = root
        root.title("HTTP Browser Client")
        root.geometry("800x600")

        # Server configuration frame
        server_frame = ttk.LabelFrame(root, text="Server Configuration")
        server_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(server_frame, text="Host:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.host_entry = ttk.Entry(server_frame, width=15)
        self.host_entry.insert(0, SERVER[0])
        self.host_entry.grid(row=0, column=1, padx=5, pady=5, sticky="w")

        ttk.Label(server_frame, text="Port:").grid(row=0, column=2, padx=5, pady=5, sticky="w")
        self.port_entry = ttk.Entry(server_frame, width=6)
        self.port_entry.insert(0, str(SERVER[1]))
        self.port_entry.grid(row=0, column=3, padx=5, pady=5, sticky="w")

        # Request frame
        request_frame = ttk.LabelFrame(root, text="HTTP Request")
        request_frame.pack(fill="x", padx=10, pady=5)

        # URL path entry with address bar feel
        ttk.Label(request_frame, text="URL:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.path_entry = ttk.Entry(request_frame, width=70)
        self.path_entry.insert(0, "/index.html")
        self.path_entry.grid(row=0, column=1, padx=5, pady=5, sticky="we")

        # Method selection
        self.method_var = tk.StringVar(value="GET")
        ttk.Label(request_frame, text="Method:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        method_frame = ttk.Frame(request_frame)
        method_frame.grid(row=1, column=1, sticky="w", padx=5, pady=5)

        ttk.Radiobutton(method_frame, text="GET", variable=self.method_var, value="GET",
                        command=self.toggle_post_data).pack(side="left", padx=5)
        ttk.Radiobutton(method_frame, text="POST", variable=self.method_var, value="POST",
                        command=self.toggle_post_data).pack(side="left", padx=5)

        # POST data entry
        self.post_frame = ttk.LabelFrame(request_frame, text="POST Data")
        self.post_frame.grid(row=2, column=0, columnspan=2, padx=5, pady=5, sticky="we")
        self.post_data = scrolledtext.ScrolledText(self.post_frame, height=4, width=80)
        self.post_data.pack(fill="both", expand=True, padx=5, pady=5)

        # Initial state of POST data frame
        self.toggle_post_data()

        # Send button with browser-like styling
        self.send_button = ttk.Button(request_frame, text="Go", command=self.send_request, width=10)
        self.send_button.grid(row=0, column=2, padx=5, pady=5)

        # Response frame
        response_frame = ttk.LabelFrame(root, text="HTTP Response")
        response_frame.pack(fill="both", expand=True, padx=10, pady=5)

        # Status bar
        self.status_bar = ttk.Label(response_frame, text="Ready", anchor="w")
        self.status_bar.pack(fill="x", padx=5, pady=2)

        # Response view with tabs for raw, headers, body
        self.notebook = ttk.Notebook(response_frame)
        self.notebook.pack(fill="both", expand=True, padx=5, pady=5)

        # Raw response tab
        self.raw_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.raw_tab, text="Raw")
        self.raw_response = scrolledtext.ScrolledText(self.raw_tab)
        self.raw_response.pack(fill="both", expand=True)

        # Headers tab
        self.headers_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.headers_tab, text="Headers")
        self.headers_response = scrolledtext.ScrolledText(self.headers_tab)
        self.headers_response.pack(fill="both", expand=True)

        # Body tab
        self.body_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.body_tab, text="Body")
        self.body_response = scrolledtext.ScrolledText(self.body_tab)
        self.body_response.pack(fill="both", expand=True)

        # Start the asyncio event loop in a separate thread
        threading.Thread(target=self.run_async_loop, daemon=True).start()

        # Start the periodic check for responses
        self.check_response()

    def toggle_post_data(self):
        """Show/hide POST data based on selected method"""
        if self.method_var.get() == "POST":
            self.post_frame.grid()
        else:
            self.post_frame.grid_remove()

    def send_request(self):
        """Handle the send request button click"""
        # Disable the send button to prevent multiple requests
        self.send_button.config(state="disabled")
        self.status_bar.config(text="Sending request...")

        # Clear previous response
        self.raw_response.delete(1.0, tk.END)
        self.headers_response.delete(1.0, tk.END)
        self.body_response.delete(1.0, tk.END)

        # Prepare request parameters
        host = self.host_entry.get()
        try:
            port = int(self.port_entry.get())
        except ValueError:
            self.status_bar.config(text="Error: Invalid port number")
            self.send_button.config(state="normal")
            return

        method = self.method_var.get()
        path = self.path_entry.get()
        if not path.startswith('/'):
            path = '/' + path

        data = None
        if method == "POST":
            data = self.post_data.get(1.0, tk.END).strip()

        # Add request to queue for async thread to process
        message_queue.put({
            'host': host,
            'port': port,
            'method': method,
            'path': path,
            'data': data
        })

    def check_response(self):
        """Check for responses from the async thread"""
        try:
            # Non-blocking check for response
            if not response_queue.empty():
                response_data = response_queue.get_nowait()

                if 'error' in response_data:
                    # Handle error
                    self.status_bar.config(text=f"Error: {response_data['error']}")
                    self.raw_response.insert(tk.END, f"Error: {response_data['error']}")
                else:
                    # Display full response
                    self.raw_response.insert(tk.END, response_data['raw'])

                    # Split headers and body
                    if '\r\n\r\n' in response_data['raw']:
                        headers, body = response_data['raw'].split('\r\n\r\n', 1)
                        self.headers_response.insert(tk.END, headers)
                        self.body_response.insert(tk.END, body)

                        # Extract status for status bar
                        status_line = headers.split('\r\n')[0]
                        self.status_bar.config(text=f"Response: {status_line}")
                    else:
                        self.headers_response.insert(tk.END, "No headers found")
                        self.body_response.insert(tk.END, "No body found")
                        self.status_bar.config(text="Response received (malformed)")

                # Re-enable the send button
                self.send_button.config(state="normal")
        except:
            pass  # No response yet

        # Schedule next check
        self.root.after(100, self.check_response)

    def run_async_loop(self):
        """Run the asyncio event loop in a separate thread"""
        asyncio.run(self.async_main())

    async def async_main(self):
        """Main async function to process requests"""
        while True:
            # Wait for a message from the GUI
            while message_queue.empty():
                await asyncio.sleep(0.1)

            # Process the request
            request_data = message_queue.get()

            try:
                # Create socket with client address
                socket = RudpSocket(CLIENT)

                # Set server address
                server_addr = (request_data['host'], request_data['port'])

                # Build request based on method
                if request_data['method'] == 'GET':
                    request = f"GET {request_data['path']} HTTP/1.1\r\nHost: {request_data['host']}\r\nConnection: close\r\n\r\n"
                else:  # POST
                    data = request_data['data'] or ""
                    content_length = len(data)
                    request = f"POST {request_data['path']} HTTP/1.1\r\nHost: {request_data['host']}\r\nConnection: close\r\n" \
                              f"Content-Length: {content_length}\r\nContent-Type: application/x-www-form-urlencoded\r\n\r\n{data}"

                # Connect and send request
                await socket.connect(server_addr)
                await socket.send(server_addr, request.encode())

                # Variables to track response
                response_data = b""
                headers_received = False
                content_length = None

                # Add timeout for receiving
                timeout = 5.0  # 5 second timeout
                start_time = asyncio.get_event_loop().time()

                # Receive response with proper completion detection
                try:
                    async for addr, data in socket.recv():
                        if addr == server_addr:
                            response_data += data

                            # Parse headers if not already done
                            if not headers_received and b"\r\n\r\n" in response_data:
                                headers_part, body_part = response_data.split(b"\r\n\r\n", 1)
                                headers_received = True

                                # Extract Content-Length if present
                                for header in headers_part.split(b"\r\n"):
                                    if header.lower().startswith(b"content-length:"):
                                        content_length = int(header.split(b":", 1)[1].strip())
                                        break

                            # Check if we've received the complete response
                            if headers_received and content_length is not None:
                                headers_size = len(response_data) - len(response_data.split(b"\r\n\r\n", 1)[1])
                                body_size = len(response_data) - headers_size
                                if body_size >= content_length:
                                    break

                            # Check for timeout
                            if asyncio.get_event_loop().time() - start_time > timeout:
                                break
                except Exception as e:
                    response_queue.put({'error': f"Error during receive: {str(e)}"})
                    await socket.close()
                    continue

                # Send response back to GUI
                response_queue.put({
                    'raw': response_data.decode(errors='replace')
                })

                # Close the connection
                await socket.close()

            except Exception as e:
                # Send error back to GUI
                response_queue.put({'error': str(e)})


def main():
    # Set up logging
    logging.basicConfig(level=logging.INFO, format=f'browser: %(message)s')

    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='HTTP Browser Client')
    parser.add_argument('--host', default='127.0.0.1', help='Server host address')
    parser.add_argument('--port', type=int, default=8080, help='Server port')

    args = parser.parse_args()
    global SERVER
    SERVER = (args.host, args.port)

    # Create and run the GUI
    root = tk.Tk()
    app = HttpBrowser(root)
    root.mainloop()


if __name__ == '__main__':
    main()