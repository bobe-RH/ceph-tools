#!/usr/bin/env python3
import boto3
import sys
import time
import threading

AK = 'YOUR_ACCESS_KEY'
SK = 'YOUR_SECRET_KEY'
BUCKET = 'bucket2'
SRC_ENDPOINT = 'http://ceph5'
DST_ENDPOINT = 'http://ceph12'
WORKERS = 8

src = boto3.client('s3', endpoint_url=SRC_ENDPOINT, aws_access_key_id=AK, aws_secret_access_key=SK, region_name='us-west')
dst = boto3.client('s3', endpoint_url=DST_ENDPOINT, aws_access_key_id=AK, aws_secret_access_key=SK, region_name='us-east')

print('Listing destination objects...')
dst_keys = set()
paginator = dst.get_paginator('list_objects_v2')
for page in paginator.paginate(Bucket=BUCKET):
    for obj in page.get('Contents', []):
        dst_keys.add(obj['Key'])
print('Destination has %d objects' % len(dst_keys))

print('Listing source objects...')
src_objects = []
for page in paginator.paginate(Bucket=BUCKET):
    pass
src_paginator = src.get_paginator('list_objects_v2')
src_objects = []
for page in src_paginator.paginate(Bucket=BUCKET):
    for obj in page.get('Contents', []):
        if obj['Key'] not in dst_keys:
            src_objects.append((obj['Key'], obj['Size']))
print('Source has %d objects to copy' % len(src_objects))

if not src_objects:
    print('Nothing to copy.')
    sys.exit(0)

total_bytes = sum(s for _, s in src_objects)
print('Total to transfer: %.2f GiB across %d objects' % (total_bytes / (1024**3), len(src_objects)))

copied = {'count': 0, 'bytes': 0, 'errors': 0}
lock = threading.Lock()
start = time.time()

def copy_one(key, size):
    try:
        s = boto3.client('s3', endpoint_url=SRC_ENDPOINT, aws_access_key_id=AK, aws_secret_access_key=SK, region_name='us-west')
        d = boto3.client('s3', endpoint_url=DST_ENDPOINT, aws_access_key_id=AK, aws_secret_access_key=SK, region_name='us-east')
        resp = s.get_object(Bucket=BUCKET, Key=key)
        d.upload_fileobj(resp['Body'], BUCKET, key)
        with lock:
            copied['count'] += 1
            copied['bytes'] += size
            if copied['count'] % 100 == 0:
                elapsed = time.time() - start
                rate = copied['bytes'] / elapsed / (1024**2)
                print('%d/%d copied (%.1f GiB, %.1f MiB/s, %d errors)' % (copied['count'], len(src_objects), copied['bytes'] / (1024**3), rate, copied['errors']))
    except Exception as e:
        with lock:
            copied['errors'] += 1
            if copied['errors'] <= 10:
                print('ERROR %s: %s' % (key, e))

from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    futures = [pool.submit(copy_one, key, size) for key, size in src_objects]
    for f in futures:
        f.result()

elapsed = time.time() - start
print('Done: %d copied, %d errors, %.2f GiB in %.0f seconds (%.1f MiB/s)' % (copied['count'], copied['errors'], copied['bytes'] / (1024**3), elapsed, copied['bytes'] / elapsed / (1024**2)))
