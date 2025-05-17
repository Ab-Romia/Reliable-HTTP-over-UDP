import socket
import time
import threading
from enum import Enum
from abc import ABC, abstractmethod
import random
import logging

# Constants for flags, sizes, etc.
SYN_FLAG = 0x1
ACK_FLAG = 0x2
SYNACK_FLAG = SYN_FLAG | ACK_FLAG
FIN_FLAG = 0x4
RST_FLAG = 0x8
MSS = 1024  # Maximum segment size
BUFFER_SIZE = 4096
TIMEOUT = 1.0
MAX_RETRIES = 5


def calculate_checksum(data):
    checksum = 0
    for i in range(0, len(data), 2):
        if i + 1 < len(data):
            checksum += (data[i] << 8) + data[i + 1]
        else:
            checksum += data[i] << 8
    checksum = (checksum >> 16) + (checksum & 0xFFFF)
    checksum = ~checksum & 0xFFFF
    return checksum


def verify_checksum(data, received_checksum):
    calculated_checksum = calculate_checksum(data)
    return calculated_checksum == received_checksum


def pack_packet(seq_num, ack_num, flags, data=b''):
    header = seq_num.to_bytes(4, byteorder='big')
    header += ack_num.to_bytes(4, byteorder='big')
    header += flags.to_bytes(1, byteorder='big')
    checksum = calculate_checksum(header + data)
    header += checksum.to_bytes(2, byteorder='big')
    return header + data


def unpack_packet(packet):
    seq_num = int.from_bytes(packet[0:4], byteorder='big')
    ack_num = int.from_bytes(packet[4:8], byteorder='big')
    flags = int.from_bytes(packet[8:9], byteorder='big')
    received_checksum = int.from_bytes(packet[9:11], byteorder='big')
    data = packet[11:]

    # Verify checksum (set checksum bytes to zero for verification)
    header = packet[0:9] + b'\x00\x00'
    if verify_checksum(header + data, received_checksum):
        return seq_num, ack_num, flags, data
    else:
        return seq_num, ack_num, flags, b''  # Return empty data if checksum fails