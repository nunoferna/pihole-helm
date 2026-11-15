#!/usr/bin/env python3
"""Dependency-free external UDP/TCP A-answer sampler and bounded load fixture."""
import argparse
import concurrent.futures
import json
import random
import socket
import statistics
import struct
import time


def query(server, port, protocol, name, expected, timeout=1):
    identifier = random.randrange(65536)
    packet = struct.pack('!6H', identifier, 0x100, 1, 0, 0, 0)
    packet += b''.join(bytes([len(part)]) + part.encode('ascii') for part in name.rstrip('.').split('.')) + b'\0\0\1\0\1'
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM if protocol == 'tcp' else socket.SOCK_DGRAM) as connection:
        connection.settimeout(timeout)
        if protocol == 'tcp':
            connection.connect((server, port))
            connection.sendall(struct.pack('!H', len(packet)) + packet)
            def receive(size):
                result = b''
                while len(result) < size:
                    chunk = connection.recv(size - len(result))
                    if not chunk:
                        raise ValueError('Truncated TCP DNS frame')
                    result += chunk
                return result
            response = receive(struct.unpack('!H', receive(2))[0])
        else:
            connection.connect((server, port))
            connection.send(packet)
            response = connection.recv(65535)
    header = struct.unpack('!6H', response[:12])
    if header[0] != identifier or not header[1] & 0x8000 or header[1] & 0x20f:
        raise ValueError('Invalid DNS identity, truncation or response code')
    def skip_name(offset):
        while response[offset]:
            if response[offset] & 0xc0 == 0xc0:
                return offset + 2
            offset += response[offset] + 1
        return offset + 1
    offset = 12
    for _ in range(header[2]):
        offset = skip_name(offset) + 4
    answers = []
    for _ in range(header[3]):
        offset = skip_name(offset)
        kind, family, _, length = struct.unpack('!HHIH', response[offset:offset + 10])
        offset += 10
        if kind == 1 and family == 1 and length == 4:
            answers.append(socket.inet_ntoa(response[offset:offset + 4]))
        offset += length
    if expected not in answers:
        raise ValueError('Incorrect A answer: ' + repr(answers))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True)
    parser.add_argument('--port', type=int, default=53)
    parser.add_argument('--record', action='append', required=True, help='name=expected-ip')
    parser.add_argument('--duration', type=float, default=20)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--interval', type=float, default=.1)
    parser.add_argument('--samples', action='store_true')
    args = parser.parse_args()
    deadline = time.monotonic() + args.duration
    def worker(_):
        results = []
        while time.monotonic() < deadline:
            for protocol in ('udp', 'tcp'):
                for record in args.record:
                    name, expected = record.split('=', 1)
                    start = time.monotonic()
                    result = {'utc': time.time(), 'protocol': protocol, 'name': name, 'ok': True}
                    try:
                        query(args.server, args.port, protocol, name, expected)
                    except (OSError, ValueError, struct.error, IndexError) as error:
                        result.update(ok=False, error=str(error))
                    result['ms'] = round((time.monotonic() - start) * 1000, 3)
                    results.append(result)
                    if args.samples:
                        print(json.dumps({'sample': result}), flush=True)
            if args.interval:
                time.sleep(args.interval)
        return results
    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        results = [item for batch in executor.map(worker, range(args.workers)) for item in batch]
    elapsed = time.monotonic() - start
    latencies = sorted(item['ms'] for item in results if item['ok'])
    summary = {'queries': len(results), 'failures': sum(not r['ok'] for r in results),
               'firstErrors': [r for r in results if not r['ok']][:4],
               'elapsedSeconds': round(elapsed, 3), 'observedQPS': round(len(results) / elapsed, 2),
               'p50Ms': statistics.median(latencies) if latencies else None,
               'p99Ms': latencies[min(len(latencies) - 1, int(len(latencies) * .99))] if latencies else None}
    print(json.dumps({'summary': summary}), flush=True)
    return int(summary['failures'] != 0)


if __name__ == '__main__':
    raise SystemExit(main())
