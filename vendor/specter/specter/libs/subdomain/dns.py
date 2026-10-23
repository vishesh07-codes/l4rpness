# this file contains all the DNS related functions

import asyncio
import re
import secrets
import socket
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .constants import (
    DNS_CNAME_MAX,
    DNS_IN,
    DNS_PKT_MAX,
    DNS_PORT,
    DNS_QTYPE_A,
    DNS_QTYPE_AAAA,
    DNS_QTYPE_AXFR,
    DNS_QTYPE_CAA,
    DNS_QTYPE_CNAME,
    DNS_QTYPE_MX,
    DNS_QTYPE_NS,
    DNS_QTYPE_PTR,
    DNS_QTYPE_SOA,
    DNS_QTYPE_SRV,
    DNS_QTYPE_TXT,
    DNS_TO,
    DNS_TRIES,
)


class DnsError(RuntimeError):
    pass


class DnsFallback(RuntimeError):
    pass


QTYPE_NAME: Dict[int, str] = {
    DNS_QTYPE_A: "A",
    DNS_QTYPE_AAAA: "AAAA",
    DNS_QTYPE_CNAME: "CNAME",
    DNS_QTYPE_MX: "MX",
    DNS_QTYPE_TXT: "TXT",
    DNS_QTYPE_NS: "NS",
    DNS_QTYPE_SOA: "SOA",
    DNS_QTYPE_PTR: "PTR",
    DNS_QTYPE_SRV: "SRV",
    DNS_QTYPE_CAA: "CAA",
}


@dataclass
class DnsRecord:
    type: str
    name: str
    value: str
    ttl: int
    priority: Optional[int] = None
    mname: Optional[str] = None
    rname: Optional[str] = None
    serial: Optional[int] = None
    refresh: Optional[int] = None
    retry: Optional[int] = None
    expire: Optional[int] = None
    minimum: Optional[int] = None
    weight: Optional[int] = None
    port: Optional[int] = None
    flags: Optional[int] = None
    tag: Optional[str] = None
    tag_value: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "type": self.type,
            "name": self.name,
            "value": self.value,
            "ttl": self.ttl,
        }
        if self.priority is not None:
            d["priority"] = self.priority
        if self.mname is not None:
            d["mname"] = self.mname
        if self.rname is not None:
            d["rname"] = self.rname
        if self.serial is not None:
            d["serial"] = self.serial
        if self.refresh is not None:
            d["refresh"] = self.refresh
        if self.retry is not None:
            d["retry"] = self.retry
        if self.expire is not None:
            d["expire"] = self.expire
        if self.minimum is not None:
            d["minimum"] = self.minimum
        if self.weight is not None:
            d["weight"] = self.weight
        if self.port is not None:
            d["port"] = self.port
        if self.flags is not None:
            d["flags"] = self.flags
        if self.tag is not None:
            d["tag"] = self.tag
        if self.tag_value is not None:
            d["tag_value"] = self.tag_value
        return d

    def type_specific_str(self) -> str:
        parts = []
        if self.type == "MX" and self.priority is not None:
            parts.append(f"priority={self.priority}")
        if self.type == "SRV":
            if self.priority is not None:
                parts.append(f"priority={self.priority}")
            if self.weight is not None:
                parts.append(f"weight={self.weight}")
            if self.port is not None:
                parts.append(f"port={self.port}")
        if self.type == "SOA":
            if self.mname:
                parts.append(f"mname={self.mname}")
            if self.rname:
                parts.append(f"rname={self.rname}")
            if self.serial is not None:
                parts.append(f"serial={self.serial}")
            if self.refresh is not None:
                parts.append(f"refresh={self.refresh}s")
            if self.retry is not None:
                parts.append(f"retry={self.retry}s")
            if self.expire is not None:
                parts.append(f"expire={self.expire}s")
            if self.minimum is not None:
                parts.append(f"minimum={self.minimum}s")
        if self.type == "CAA":
            if self.flags is not None:
                parts.append(f"flags={self.flags}")
            if self.tag:
                parts.append(f"tag={self.tag}")
        return ", ".join(parts) if parts else ""


@dataclass
class DnsResult:
    ans: List[str]
    fallback: bool = False


def load_nameservers() -> List[str]:
    conf = Path("/etc/resolv.conf")
    if not conf.exists():
        return []
    nameservers: List[str] = []
    for line in conf.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or not line.startswith("nameserver"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            nameservers.append(parts[1])
    return nameservers


def dns_addr(nameserver: str):
    family = socket.AF_INET6 if ":" in nameserver else socket.AF_INET
    if family == socket.AF_INET6:
        return family, (nameserver, DNS_PORT, 0, 0)
    return family, (nameserver, DNS_PORT)


def encode_name(name: str) -> bytes:
    labels = [label for label in name.strip(".").split(".") if label]
    return (
        b"".join(
            len(label.encode("idna")).to_bytes(1, "big") + label.encode("idna")
            for label in labels
        )
        + b"\x00"
    )


def decode_name(packet: bytes, offset: int) -> Tuple[str, int]:
    labels: List[str] = []
    current = offset
    next_offset = None
    seen: Set[int] = set()
    while True:
        if current >= len(packet):
            raise DnsError("dns name exceeds packet bounds")
        length = packet[current]
        if length & 0xC0 == 0xC0:
            if current + 1 >= len(packet):
                raise DnsError("dns pointer truncated")
            pointer = ((length & 0x3F) << 8) | packet[current + 1]
            if pointer in seen:
                raise DnsError("dns pointer loop")
            seen.add(pointer)
            if next_offset is None:
                next_offset = current + 2
            current = pointer
            continue
        if length == 0:
            current += 1
            break
        current += 1
        if current + length > len(packet):
            raise DnsError("dns label exceeds packet bounds")
        labels.append(packet[current : current + length].decode("ascii", errors="ignore"))
        current += length
    return ".".join(labels), next_offset if next_offset is not None else current


def make_query(name: str, qtype: int) -> Tuple[int, bytes]:
    txid = secrets.randbelow(65536)
    header = struct.pack("!HHHHHH", txid, 0x0100, 1, 0, 0, 0)
    question = encode_name(name) + struct.pack("!HH", qtype, DNS_IN)
    return txid, header + question


def parse_rdata(packet: bytes, offset: int, rdlen: int, rr_type: int) -> DnsRecord:
    rdata = packet[offset : offset + rdlen]
    qname = QTYPE_NAME.get(rr_type, f"TYPE{rr_type}")

    if rr_type == DNS_QTYPE_A and rdlen == 4:
        value = socket.inet_ntop(socket.AF_INET, rdata)
        return DnsRecord(type=qname, name="", value=value, ttl=0)

    if rr_type == DNS_QTYPE_AAAA and rdlen == 16:
        value = socket.inet_ntop(socket.AF_INET6, rdata)
        return DnsRecord(type=qname, name="", value=value, ttl=0)

    if rr_type in {DNS_QTYPE_CNAME, DNS_QTYPE_NS, DNS_QTYPE_PTR}:
        cname, _ = decode_name(packet, offset)
        return DnsRecord(type=qname, name="", value=cname.strip(".").lower(), ttl=0)

    if rr_type == DNS_QTYPE_MX and rdlen >= 4:
        preference = struct.unpack("!H", rdata[:2])[0]
        mx_host, _ = decode_name(packet, offset + 2)
        return DnsRecord(
            type=qname, name="", value=mx_host.strip(".").lower(), ttl=0,
            priority=preference,
        )

    if rr_type == DNS_QTYPE_TXT:
        txt_parts: List[str] = []
        txt_off = 0
        while txt_off < rdlen:
            slen = rdata[txt_off]
            txt_off += 1
            if txt_off + slen <= rdlen:
                txt_parts.append(rdata[txt_off : txt_off + slen].decode("utf-8", errors="replace"))
                txt_off += slen
            else:
                break
        return DnsRecord(type=qname, name="", value=" ".join(txt_parts), ttl=0)

    if rr_type == DNS_QTYPE_SOA and rdlen >= 20:
        mname, pos = decode_name(packet, offset)
        rname, pos = decode_name(packet, pos)
        soa_fields = struct.unpack("!IIIII", packet[pos : pos + 20])
        serial, refresh, retry, expire, minimum = soa_fields
        return DnsRecord(
            type=qname, name="", value=mname.strip(".").lower(), ttl=0,
            mname=mname.strip(".").lower(),
            rname=rname.strip(".").lower().replace(".", "@", 1),
            serial=serial, refresh=refresh, retry=retry,
            expire=expire, minimum=minimum,
        )

    if rr_type == DNS_QTYPE_SRV and rdlen >= 7:
        priority, weight, port = struct.unpack("!HHH", rdata[:6])
        target, _ = decode_name(packet, offset + 6)
        return DnsRecord(
            type=qname, name="", value=f"{target.strip('.').lower()}:{port}", ttl=0,
            priority=priority, weight=weight, port=port,
        )

    if rr_type == DNS_QTYPE_CAA and rdlen >= 2:
        flags = rdata[0]
        tag_len = rdata[1]
        tag = rdata[2 : 2 + tag_len].decode("ascii", errors="replace")
        tag_value = rdata[2 + tag_len:].decode("utf-8", errors="replace")
        return DnsRecord(
            type=qname, name="", value=tag_value, ttl=0,
            flags=flags, tag=tag, tag_value=tag_value,
        )

    return DnsRecord(type=qname, name="", value=rdata.hex(), ttl=0)


def parse_response(
    packet: bytes, txid: int, qtype: int
) -> Tuple[List[str], List[str], bool, int]:
    if len(packet) < 12:
        raise DnsError("dns packet too short")
    response_id, flags, qdcount, ancount, _nscount, _arcount = struct.unpack(
        "!HHHHHH", packet[:12]
    )
    if response_id != txid:
        raise DnsError("dns txid mismatch")
    truncated = bool(flags & 0x0200)
    rcode = flags & 0x000F
    offset = 12
    for _ in range(qdcount):
        _, offset = decode_name(packet, offset)
        offset += 4
        if offset > len(packet):
            raise DnsError("dns question truncated")

    answers: List[str] = []
    cnames: List[str] = []
    for _ in range(ancount):
        name, offset = decode_name(packet, offset)
        if offset + 10 > len(packet):
            raise DnsError("dns answer header truncated")
        rr_type, rr_class, rttl, rdlen = struct.unpack(
            "!HHLH", packet[offset : offset + 10]
        )
        offset += 10
        if offset + rdlen > len(packet):
            raise DnsError("dns rdata truncated")
        rdata_offset = offset
        offset += rdlen
        if rr_class != DNS_IN:
            continue
        if rr_type == qtype:
            if qtype == DNS_QTYPE_A and rdlen == 4:
                answers.append(socket.inet_ntop(socket.AF_INET, packet[rdata_offset : rdata_offset + 4]))
            elif qtype == DNS_QTYPE_AAAA and rdlen == 16:
                answers.append(socket.inet_ntop(socket.AF_INET6, packet[rdata_offset : rdata_offset + 16]))
        elif rr_type == DNS_QTYPE_CNAME:
            cname, _ = decode_name(packet, rdata_offset)
            if cname:
                cnames.append(cname.lower().strip("."))
    return answers, cnames, truncated, rcode


def parse_response_records(packet: bytes, txid: int) -> List[DnsRecord]:
    if len(packet) < 12:
        raise DnsError("dns packet too short")
    response_id, flags, qdcount, ancount, _nscount, _arcount = struct.unpack(
        "!HHHHHH", packet[:12]
    )
    if response_id != txid:
        raise DnsError("dns txid mismatch")
    truncated = bool(flags & 0x0200)
    offset = 12
    for _ in range(qdcount):
        _, offset = decode_name(packet, offset)
        offset += 4
        if offset > len(packet):
            raise DnsError("dns question truncated")

    records: List[DnsRecord] = []
    for _ in range(ancount):
        name, offset = decode_name(packet, offset)
        if offset + 10 > len(packet):
            raise DnsError("dns answer header truncated")
        rr_type, rr_class, rttl, rdlen = struct.unpack(
            "!HHLH", packet[offset : offset + 10]
        )
        offset += 10
        if offset + rdlen > len(packet):
            raise DnsError("dns rdata truncated")
        if rr_class != DNS_IN:
            offset += rdlen
            continue
        rdata_offset = offset
        rec = parse_rdata(packet, rdata_offset, rdlen, rr_type)
        rec.name = name.lower().rstrip(".")
        rec.ttl = rttl
        records.append(rec)
        offset += rdlen
    return records


def make_tcp_query(name: str, qtype: int) -> bytes:
    txid = secrets.randbelow(65536)
    header = struct.pack("!HHHHHH", txid, 0x0100, 1, 0, 0, 0)
    question = encode_name(name) + struct.pack("!HH", qtype, DNS_IN)
    dns_pkt = header + question
    return struct.pack("!H", len(dns_pkt)) + dns_pkt


def parse_tcp_response(packet: bytes, txid: int) -> List[DnsRecord]:
    records: List[DnsRecord] = []
    offset = 0
    while offset < len(packet):
        if offset + 2 > len(packet):
            break
        msglen = struct.unpack("!H", packet[offset : offset + 2])[0]
        offset += 2
        if offset + msglen > len(packet):
            break
        try:
            recs = parse_response_records(packet[offset : offset + msglen], txid)
            records.extend(recs)
        except DnsError:
            pass
        offset += msglen
    return records


def _dns_query_udp(family, addr, query, txid, timeout, qtype):
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(query, addr)
        packet, _ = sock.recvfrom(DNS_PKT_MAX)
        return packet
    finally:
        sock.close()


def _dns_query_tcp(family, addr, query, connect_timeout, read_timeout):
    import select
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(connect_timeout)
    try:
        sock.connect(addr)
        sock.sendall(query)
        response = b""
        while True:
            ready = select.select([sock], [], [], read_timeout)
            if not ready[0]:
                raise TimeoutError("dns tcp read timeout")
            chunk = sock.recv(65536)
            if not chunk:
                break
            response += chunk
        return response
    finally:
        sock.close()


class DnsResolver:
    def __init__(self, timeout: float = DNS_TO):
        self._to = timeout
        self._ns = load_nameservers()

    async def _query(self, nameserver: str, name: str, qtype: int) -> DnsResult:
        loop = asyncio.get_running_loop()
        txid, query = make_query(name, qtype)
        family, addr = dns_addr(nameserver)
        try:
            packet = await loop.run_in_executor(
                None, _dns_query_udp, family, addr, query, txid, self._to, qtype
            )
        except (OSError, asyncio.TimeoutError, DnsError):
            return DnsResult([], fallback=True)
        answers, cnames, truncated, rcode = parse_response(packet, txid, qtype)
        if truncated:
            return DnsResult([], fallback=True)
        if answers:
            return DnsResult(answers)
        if cnames:
            return DnsResult(cnames)
        if rcode in {2, 5}:
            return DnsResult([], fallback=True)
        return DnsResult([])

    async def _query_records(self, nameserver: str, name: str, qtype: int) -> List[DnsRecord]:
        loop = asyncio.get_running_loop()
        txid, query = make_query(name, qtype)
        family, addr = dns_addr(nameserver)
        try:
            packet = await loop.run_in_executor(
                None, _dns_query_udp, family, addr, query, txid, self._to, qtype
            )
        except (OSError, asyncio.TimeoutError, DnsError):
            return []
        return parse_response_records(packet, txid)

    async def _lookup(self, name: str, qtype: int, depth: int = 0) -> DnsResult:
        if depth > DNS_CNAME_MAX or not self._ns:
            return DnsResult([], fallback=True)
        need_fallback = False
        for nameserver in self._ns:
            for _ in range(DNS_TRIES):
                try:
                    result = await self._query(nameserver, name, qtype)
                except (OSError, asyncio.TimeoutError, DnsError):
                    need_fallback = True
                    continue
                if result.ans:
                    if qtype in {DNS_QTYPE_A, DNS_QTYPE_AAAA} and all(
                        not re.match(r"^\d+\.\d+\.\d+\.\d+$", value) and ":" not in value
                        for value in result.ans
                    ):
                        return await self._lookup(result.ans[0], qtype, depth + 1)
                    return result
                if result.fallback:
                    need_fallback = True
                    continue
                return result
        return DnsResult([], fallback=need_fallback)

    async def resolve(self, host: str) -> str:
        ipv4, ipv6 = await asyncio.gather(
            self._lookup(host, DNS_QTYPE_A),
            self._lookup(host, DNS_QTYPE_AAAA),
        )
        if ipv4.ans:
            return ipv4.ans[0]
        if ipv6.ans:
            return ipv6.ans[0]
        if ipv4.fallback or ipv6.fallback:
            raise DnsFallback(host)
        return ""

    async def resolve_record(self, host: str, qtype: int) -> List[DnsRecord]:
        if not self._ns:
            return []
        seen_names: Set[str] = set()
        for nameserver in self._ns:
            for _ in range(DNS_TRIES):
                try:
                    records = await self._query_records(nameserver, host, qtype)
                except (OSError, asyncio.TimeoutError, DnsError):
                    continue
                if records:
                    deduped = []
                    for r in records:
                        key = f"{r.type}:{r.name}:{r.value}"
                        if key not in seen_names:
                            seen_names.add(key)
                            deduped.append(r)
                    if deduped:
                        return deduped
                    break
                break
        return []

    async def resolve_all(self, host: str) -> List[DnsRecord]:
        qtypes = [
            DNS_QTYPE_A, DNS_QTYPE_AAAA, DNS_QTYPE_MX, DNS_QTYPE_TXT,
            DNS_QTYPE_NS, DNS_QTYPE_SOA, DNS_QTYPE_CNAME, DNS_QTYPE_PTR,
            DNS_QTYPE_SRV, DNS_QTYPE_CAA,
        ]
        results = await asyncio.gather(
            *[self.resolve_record(host, qt) for qt in qtypes],
            return_exceptions=True,
        )
        all_records: List[DnsRecord] = []
        seen: Set[str] = set()
        for r in results:
            if isinstance(r, Exception):
                continue
            for rec in r:
                key = f"{rec.type}:{rec.name}:{rec.value}"
                if key not in seen:
                    seen.add(key)
                    all_records.append(rec)
        return sorted(all_records, key=lambda x: x.type)

    async def resolve_ptr(self, ip: str) -> str:
        parts = ip.split(".")
        if len(parts) != 4:
            return ""
        arpa = ".".join(reversed(parts)) + ".in-addr.arpa"
        records = await self.resolve_record(arpa, DNS_QTYPE_PTR)
        for rec in records:
            if rec.type == "PTR" and rec.value:
                return rec.value
        return ""

    async def resolve_axfr(self, host: str) -> List[DnsRecord]:
        if not self._ns:
            return []
        loop = asyncio.get_running_loop()
        for nameserver in self._ns:
            query = make_tcp_query(host, DNS_QTYPE_AXFR)
            family, addr = dns_addr(nameserver)
            try:
                response = await loop.run_in_executor(
                    None, _dns_query_tcp, family, addr, query, self._to * 2, self._to
                )
                if response:
                    txid = struct.unpack("!H", query[2:4])[0]
                    records = parse_tcp_response(response, txid)
                    if records:
                        return records
            except (OSError, asyncio.TimeoutError, DnsError, struct.error):
                continue
        return []
