"""Wire encoding from Node.JS/getnet_posintegrado/lib/Utils.js."""

import codecs
import hashlib
import hmac
import json


class ProtocolError(ValueError):
    def __init__(self, message, *, response=None):
        super().__init__(message)
        self.response = response


def compact_json(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sign_message(data):
    serialized = compact_json(data)
    signature = hashlib.sha256(serialized.encode("utf-8")).hexdigest().upper()
    return compact_json({"JsonSerialized": serialized, "Sign": signature}).encode("utf-8")


def unwrap(message):
    if "JsonSerialized" not in message:
        return message
    serialized = message["JsonSerialized"]
    signature = message.get("Sign")
    if not isinstance(serialized, str) or not isinstance(signature, str):
        raise ProtocolError("Invalid signed response", response=message)
    expected = hashlib.sha256(serialized.encode("utf-8")).hexdigest().upper()
    if not hmac.compare_digest(expected, signature.upper()):
        raise ProtocolError("Response checksum does not match", response=message)
    try:
        data = json.loads(serialized)
    except ValueError as exc:
        raise ProtocolError("Invalid inner JSON", response=message) from exc
    if isinstance(data, dict):
        return data
    # Some firmware messages carry a JSON string inside the signed JSON string.
    # Decode one additional layer only; never infer approval from scalar content.
    if isinstance(data, str):
        try:
            nested = json.loads(data)
        except ValueError:
            nested = None
        if isinstance(nested, dict):
            return nested
    # Preserve valid, checksum-verified non-object messages as opaque events.
    # They are acknowledged and displayed, but cannot complete a transaction.
    return {"ProtocolEvent": data}


class JSONStream:
    """Frame concatenated/fragmented JSON objects, including split UTF-8."""

    def __init__(self, max_bytes=1048576):
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.buffer = ""
        self.max_bytes = max_bytes

    def feed(self, chunk):
        try:
            self.buffer += self.decoder.decode(chunk)
        except UnicodeDecodeError as exc:
            raise ProtocolError("Invalid UTF-8 response") from exc
        if len(self.buffer.encode("utf-8")) > self.max_bytes:
            raise ProtocolError("Response exceeds buffer limit")
        messages = []
        while True:
            self.buffer = self.buffer.lstrip()
            if not self.buffer:
                return messages
            if self.buffer[0] != "{":
                raise ProtocolError("Expected a JSON object on the serial port")
            depth, quoted, escaped, end = 0, False, False, None
            for index, char in enumerate(self.buffer):
                if quoted:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        quoted = False
                elif char == '"':
                    quoted = True
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        end = index + 1
                        break
            if end is None:
                return messages
            try:
                messages.append(json.loads(self.buffer[:end]))
            except ValueError as exc:
                raise ProtocolError("Malformed response JSON") from exc
            self.buffer = self.buffer[end:]
