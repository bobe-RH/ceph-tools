# Bucket Index Recovery and Data Resync

Two scripts for recovering from a corrupted RGW bucket index and resyncing
the data to a healthy bucket.

## Problem

A corrupted bucket instance RADOS object -- typically caused by stale
`layout_logs` left behind after reshards -- makes generation-prefixed shard
lookups fail.  `init_bucket2_index.py` replaces the bad object so the bucket
becomes usable again, and `sync_bucket2.py` copies every object from the
source bucket to the destination.

## Scripts

### init_bucket2_index.py

Writes a corrected bucket instance RADOS object, removing the stale
`layout_logs` entries that cause generation-prefixed shard lookups to fail.

### sync_bucket2.py

boto3-based parallel S3 copy that streams objects directly between two RGW
endpoints using 8 workers.  Objects are read from the source and written to
the destination without touching local disk.

## Requirements

- Python 3.6+
- boto3

```
pip install boto3
```

- S3 credentials for both the source and destination RGW endpoints

## Usage

Update the placeholder credentials in each script before running.  Replace
`YOUR_ACCESS_KEY` and `YOUR_SECRET_KEY` with real values for each site.

### Rebuild the bucket index

```
python3 init_bucket2_index.py
```

### Resync objects between endpoints

```
python3 sync_bucket2.py
```

## Important

Credentials in the scripts are placeholders (`YOUR_ACCESS_KEY` /
`YOUR_SECRET_KEY`).  You must update them before use.
