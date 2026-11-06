"""
Binary format definitions, packing, and safe unpacking for KVDB.

Design Specification:
- File Header: 16 bytes
    [magic: 8 bytes b"KVDB0001"]
    [version: u32, little-endian = 1]
    [reserved: u32, little-endian = 0]
- Record Header: 19 bytes (little-endian)
    [rec_len: u32]   - total length of record including 19B header, payload, and 4B CRC
    [type: u8]       - 1=PUT, 2=DELETE, 3=COMMIT
    [txid: u64]      - 64-bit transaction id
    [key_len: u16]   - length of key in bytes (0 for COMMIT, 1..1024 for PUT/DELETE)
    [val_len: u32]   - length of value in bytes (0 for COMMIT/DELETE, 0..1MB for PUT)
- Payload: [key: key_len bytes][value: val_len bytes]
- Checksum: 4 bytes (little-endian u32)
    CRC32 covering all bytes preceding it:
    zlib.crc32(rec_len..value) & 0xffffffff
"""

import struct
import zlib

FILE_MAGIC = b"KVDB0001"
FILE_VERSION = 1
FILE_RESERVED = 0

FILE_HEADER_STRUCT = struct.Struct("<8sII")
FILE_HEADER_SIZE = FILE_HEADER_STRUCT.size  # 16 bytes

TYPE_PUT = 1
TYPE_DELETE = 2
TYPE_COMMIT = 3
VALID_TYPES = (TYPE_PUT, TYPE_DELETE, TYPE_COMMIT)

MIN_KEY_LEN = 1
MAX_KEY_LEN = 1024
MAX_VALUE_LEN = 1024 * 1024  # 1 MB
MAX_BATCH_SIZE = 10000

# Fixed portion of record header: rec_len (4) + type (1) + txid (8) + key_len (2) + val_len (4) = 19 bytes
RECORD_HEADER_STRUCT = struct.Struct("<IBQHI")
RECORD_HEADER_SIZE = RECORD_HEADER_STRUCT.size  # 19 bytes

CRC_STRUCT = struct.Struct("<I")
CRC_SIZE = CRC_STRUCT.size  # 4 bytes

MIN_RECORD_SIZE = RECORD_HEADER_SIZE + CRC_SIZE  # 23 bytes (for COMMIT)


def encode_file_header() -> bytes:
    """Pack the 16-byte file header."""
    return FILE_HEADER_STRUCT.pack(FILE_MAGIC, FILE_VERSION, FILE_RESERVED)


def is_valid_file_header(data: bytes) -> bool:
    """Check if the provided 16 bytes match the valid file header."""
    if len(data) < FILE_HEADER_SIZE:
        return False
    try:
        magic, version, _ = FILE_HEADER_STRUCT.unpack(data[:FILE_HEADER_SIZE])
        return magic == FILE_MAGIC and version == FILE_VERSION
    except struct.error:
        return False


def encode_record(rec_type: int, txid: int, key: bytes = b"", value: bytes = b"") -> bytes:
    """
    Encode a single record into bytes according to specification:
    [rec_len u32][type u8][txid u64][key_len u16][val_len u32][key][value][crc32 u32]
    """
    klen = len(key)
    vlen = len(value)
    rec_len = RECORD_HEADER_SIZE + klen + vlen + CRC_SIZE
    header = RECORD_HEADER_STRUCT.pack(rec_len, rec_type, txid, klen, vlen)
    body = key + value
    crc = zlib.crc32(header + body) & 0xffffffff
    trailer = CRC_STRUCT.pack(crc)
    return header + body + trailer


def decode_record_from_bytes(data: bytes):
    """
    Safely decode a record from raw bytes.
    NEVER raises on corrupted or truncated bytes; returns None instead.
    
    Returns:
        tuple (rec_type, txid, key, value, rec_len) if valid.
        None if corrupted, truncated, bad CRC, or invalid fields.
    """
    if len(data) < MIN_RECORD_SIZE:
        return None

    try:
        rec_len, rec_type, txid, klen, vlen = RECORD_HEADER_STRUCT.unpack_from(data, 0)
    except struct.error:
        return None

    # Length consistency check
    expected_len = RECORD_HEADER_SIZE + klen + vlen + CRC_SIZE
    if rec_len != expected_len or len(data) < rec_len:
        return None

    # Semantic constraints
    if rec_type not in VALID_TYPES:
        return None

    if rec_type == TYPE_COMMIT:
        if klen != 0 or vlen != 0:
            return None
    elif rec_type == TYPE_DELETE:
        if not (MIN_KEY_LEN <= klen <= MAX_KEY_LEN) or vlen != 0:
            return None
    elif rec_type == TYPE_PUT:
        if not (MIN_KEY_LEN <= klen <= MAX_KEY_LEN) or vlen > MAX_VALUE_LEN:
            return None

    # Checksum verification over all bytes before the CRC
    body_end = rec_len - CRC_SIZE
    expected_crc = zlib.crc32(memoryview(data)[:body_end]) & 0xffffffff
    actual_crc = CRC_STRUCT.unpack_from(data, body_end)[0]

    if expected_crc != actual_crc:
        return None

    key_bytes = bytes(data[RECORD_HEADER_SIZE : RECORD_HEADER_SIZE + klen])
    val_bytes = bytes(data[RECORD_HEADER_SIZE + klen : body_end])

    return rec_type, txid, key_bytes, val_bytes, rec_len

