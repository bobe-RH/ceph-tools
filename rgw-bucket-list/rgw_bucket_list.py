#!/usr/bin/env python3
# RGW Admin API Bucket Lister
# Lists all RGW buckets with owner, object count, and size via the Admin REST API.
#
# Requirements:
#   - Python 3.6+ (stdlib only, no pip packages needed)
#   - oc CLI logged into the OCP cluster
#   - RGW user with admin caps: radosgw-admin caps add --uid=<user> --caps="buckets=read"
#   - Access key / secret key for that user (set in ACCESS_KEY / SECRET_KEY below)
#
# The script automatically starts and stops an oc port-forward to the RGW service.
#
# Usage:
#   python3 rgw_bucket_list.py
import hmac
import hashlib
import datetime
import json
import subprocess
import sys
import time
import atexit
import signal

try:
    from urllib.request import Request, urlopen
    from urllib.parse import urlencode, quote
    from urllib.error import HTTPError
except ImportError:
    from urllib2 import Request, urlopen, HTTPError
    from urllib import urlencode, quote

ENDPOINT = 'http://localhost:8080'
ACCESS_KEY = 'YOUR_ACCESS_KEY'
SECRET_KEY = 'YOUR_SECRET_KEY'
REGION = 'us'
PORT_FORWARD_CMD = ['oc', 'port-forward', 'svc/rook-ceph-rgw-multisite-store', '8080:80', '-n', 'openshift-storage']
port_forward_proc = None


def start_port_forward():
    global port_forward_proc
    port_forward_proc = subprocess.Popen(PORT_FORWARD_CMD, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    atexit.register(stop_port_forward)
    time.sleep(2)
    if port_forward_proc.poll() is not None:
        err = port_forward_proc.stderr.read().decode('utf-8').strip()
        print('port-forward failed: %s' % err, file=sys.stderr)
        sys.exit(1)


def stop_port_forward():
    global port_forward_proc
    if port_forward_proc and port_forward_proc.poll() is None:
        port_forward_proc.terminate()
        port_forward_proc.wait()


def format_size(kb):
    if kb is None or kb < 0:
        return 'N/A'
    b = kb * 1024
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if abs(b) < 1024.0:
            if b == int(b):
                return '%d %s' % (int(b), unit)
            return '%.1f %s' % (b, unit)
        b /= 1024.0
    return '%.1f PiB' % b


def sign(key, msg):
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()


def admin_request(path, params=None):
    host = ENDPOINT.replace('http://', '').replace('https://', '')
    now = datetime.datetime.utcnow()
    datestamp = now.strftime('%Y%m%d')
    amzdate = now.strftime('%Y%m%dT%H%M%SZ')

    query = ''
    if params:
        sorted_params = sorted(params.items())
        query = '&'.join('%s=%s' % (quote(str(k), safe=''), quote(str(v), safe='')) for k, v in sorted_params)

    payload_hash = hashlib.sha256(b'').hexdigest()
    canonical_headers = 'host:%s\n' % host
    signed_headers = 'host'

    canonical_request = 'GET\n%s\n%s\n%s\n%s\n%s' % (
        path, query, canonical_headers, signed_headers, payload_hash)

    credential_scope = '%s/%s/s3/aws4_request' % (datestamp, REGION)
    string_to_sign = 'AWS4-HMAC-SHA256\n%s\n%s\n%s' % (
        amzdate, credential_scope,
        hashlib.sha256(canonical_request.encode()).hexdigest())

    signing_key = sign(
        sign(sign(sign(('AWS4' + SECRET_KEY).encode('utf-8'), datestamp), REGION), 's3'),
        'aws4_request')
    signature = hmac.new(signing_key, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

    authorization = 'AWS4-HMAC-SHA256 Credential=%s/%s,SignedHeaders=%s,Signature=%s' % (
        ACCESS_KEY, credential_scope, signed_headers, signature)

    url = '%s%s' % (ENDPOINT, path)
    if query:
        url += '?' + query

    req = Request(url, headers={
        'Host': host,
        'X-Amz-Date': amzdate,
        'X-Amz-Content-Sha256': payload_hash,
        'Authorization': authorization,
    })

    try:
        resp = urlopen(req)
        return json.loads(resp.read().decode('utf-8'))
    except HTTPError as e:
        body = e.read().decode('utf-8')
        print('Error %d: %s' % (e.code, body), file=sys.stderr)
        sys.exit(1)


def main():
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    start_port_forward()
    buckets = admin_request('/admin/bucket')
    if not buckets:
        print('No buckets found.')
        return

    rows = []
    for bname in sorted(buckets):
        try:
            info = admin_request('/admin/bucket', {'bucket': bname, 'stats': 'true'})
            owner = info.get('owner', 'N/A')
            usage = info.get('usage', {})
            main_usage = usage.get('rgw.main', {})
            num_objects = str(main_usage.get('num_objects', 0))
            size_kb = main_usage.get('size_kb', 0)
            rows.append((bname, owner, num_objects, format_size(size_kb)))
        except Exception as e:
            rows.append((bname, 'ERROR', '-', str(e)))

    col_b = max(len('BUCKET'), max(len(r[0]) for r in rows)) + 2
    col_o = max(len('OWNER'), max(len(r[1]) for r in rows)) + 2
    col_n = max(len('OBJECTS'), max(len(r[2]) for r in rows))
    col_s = max(len('SIZE'), max(len(r[3]) for r in rows))

    fmt = '%-*s  %-*s  %*s  %*s'
    header = fmt % (col_b, 'BUCKET', col_o, 'OWNER', col_n, 'OBJECTS', col_s, 'SIZE')
    print(header)
    print('-' * len(header))
    for r in rows:
        print(fmt % (col_b, r[0], col_o, r[1], col_n, r[2], col_s, r[3]))


if __name__ == '__main__':
    main()
