"""SOAP 1.2 + WS-Addressing envelopes and MTOM/XOP packaging (binary-safe)."""
from __future__ import annotations

import base64
import re
import uuid
from dataclasses import dataclass, field
from urllib.parse import unquote

from lxml import etree

from .model import NS


def q(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


def envelope(action: str, body_child: etree._Element, *, to: str | None = None,
             relates_to: str | None = None) -> etree._Element:
    env = etree.Element(q("soap", "Envelope"), nsmap={"soap": NS["soap"], "wsa": NS["wsa"]})
    header = etree.SubElement(env, q("soap", "Header"))
    a = etree.SubElement(header, q("wsa", "Action"))
    a.text = action
    a.set(q("soap", "mustUnderstand"), "1")
    etree.SubElement(header, q("wsa", "MessageID")).text = f"urn:uuid:{uuid.uuid4()}"
    if relates_to:
        etree.SubElement(header, q("wsa", "RelatesTo")).text = relates_to
    if to:
        etree.SubElement(header, q("wsa", "To")).text = to
    if not relates_to:
        reply = etree.SubElement(header, q("wsa", "ReplyTo"))
        etree.SubElement(reply, q("wsa", "Address")).text = "http://www.w3.org/2005/08/addressing/anonymous"
    body = etree.SubElement(env, q("soap", "Body"))
    body.append(body_child)
    return env


def fault(reason: str, code: str = "Sender") -> etree._Element:
    env = etree.Element(q("soap", "Envelope"), nsmap={"soap": NS["soap"]})
    body = etree.SubElement(env, q("soap", "Body"))
    f = etree.SubElement(body, q("soap", "Fault"))
    c = etree.SubElement(f, q("soap", "Code"))
    etree.SubElement(c, q("soap", "Value")).text = f"soap:{code}"
    r = etree.SubElement(f, q("soap", "Reason"))
    t = etree.SubElement(r, q("soap", "Text"))
    t.text = reason
    t.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
    return env


def to_bytes(el: etree._Element) -> bytes:
    return etree.tostring(el, xml_declaration=True, encoding="UTF-8")


@dataclass
class SoapMessage:
    envelope: etree._Element
    attachments: dict[str, bytes] = field(default_factory=dict)   # content-id (no brackets) -> bytes

    @property
    def action(self) -> str:
        a = self.envelope.find("soap:Header/wsa:Action", NS)
        return (a.text or "").strip() if a is not None else ""

    @property
    def message_id(self) -> str:
        m = self.envelope.find("soap:Header/wsa:MessageID", NS)
        return (m.text or "").strip() if m is not None else ""

    @property
    def body(self) -> etree._Element:
        body = self.envelope.find("soap:Body", NS)
        if body is None or len(body) == 0:
            raise ValueError("SOAP message has no Body content")
        return body[0]

    def binary(self, el: etree._Element) -> bytes:
        """Content of an element carrying either an xop:Include or inline base64."""
        inc = el.find("xop:Include", NS)
        if inc is not None:
            cid = unquote(inc.get("href", "")).removeprefix("cid:")
            if cid not in self.attachments:
                raise ValueError(f"xop:Include references missing MIME part {cid}")
            return self.attachments[cid]
        return base64.b64decode((el.text or "").strip())


# --------------------------------------------------------------------- MTOM

def build_mtom(env: etree._Element, action: str, parts: dict[str, tuple[bytes, str]]) -> tuple[bytes, str]:
    """Serialise an MTOM/XOP message. ``parts`` maps content-id -> (bytes, mime)."""
    boundary = f"MIMEBoundary_{uuid.uuid4().hex}"
    root_id = f"root.message@{uuid.uuid4().hex[:12]}.scdpoc"
    out = [f"--{boundary}\r\n".encode(),
           b'Content-Type: application/xop+xml; charset=UTF-8; type="application/soap+xml"\r\n',
           b"Content-Transfer-Encoding: binary\r\n",
           f"Content-ID: <{root_id}>\r\n\r\n".encode(), to_bytes(env), b"\r\n"]
    for cid, (data, mime) in parts.items():
        out += [f"--{boundary}\r\n".encode(), f"Content-Type: {mime}\r\n".encode(),
                b"Content-Transfer-Encoding: binary\r\n", f"Content-ID: <{cid}>\r\n\r\n".encode(), data, b"\r\n"]
    out.append(f"--{boundary}--\r\n".encode())
    ctype = (f'multipart/related; type="application/xop+xml"; boundary="{boundary}"; start="<{root_id}>"; '
             f'start-info="application/soap+xml"; action="{action}"')
    return b"".join(out), ctype


def xop_include(parent: etree._Element, cid: str) -> None:
    etree.SubElement(parent, q("xop", "Include"), href=f"cid:{cid}", nsmap={"xop": NS["xop"]})


def _param(ctype: str, name: str) -> str | None:
    m = re.search(rf'{name}\s*=\s*"([^"]*)"', ctype, re.IGNORECASE) or re.search(rf"{name}\s*=\s*([^;\s]+)", ctype, re.IGNORECASE)
    return m.group(1) if m else None


def parse_message(body: bytes, content_type: str) -> SoapMessage:
    ctype = content_type or ""
    if ctype.lower().startswith("multipart/related"):
        boundary = _param(ctype, "boundary")
        if not boundary:
            raise ValueError("multipart/related without boundary")
        start = (_param(ctype, "start") or "").strip("<>")
        delim = b"--" + boundary.encode()
        chunks = body.split(delim)
        parts: dict[str, bytes] = {}
        order: list[str] = []
        for chunk in chunks[1:]:
            if chunk.startswith(b"--"):
                break
            chunk = chunk[2:] if chunk.startswith(b"\r\n") else chunk.lstrip(b"\n")
            sep = b"\r\n\r\n" if b"\r\n\r\n" in chunk else b"\n\n"
            head, _, payload = chunk.partition(sep)
            if payload.endswith(b"\r\n"):
                payload = payload[:-2]
            elif payload.endswith(b"\n"):
                payload = payload[:-1]
            headers = {}
            for line in head.decode("latin-1").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            cid = headers.get("content-id", f"part{len(order)}").strip("<>")
            parts[cid] = payload
            order.append(cid)
        root_id = start if start in parts else order[0]
        root_xml = parts.pop(root_id)
        return SoapMessage(etree.fromstring(root_xml, parser=_parser()), parts)
    return SoapMessage(etree.fromstring(body, parser=_parser()))


def _parser() -> etree.XMLParser:
    # Hardened parser: no network, no entity expansion (XXE), no huge trees.
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_blank_text=True)
