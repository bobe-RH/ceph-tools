# RGW Admin API Bucket Lister

Lists all RGW buckets with owner, object count, and size via the Admin REST
API.

## Features

- Pure Python stdlib -- uses hand-rolled AWS SigV4 request signing (no
  awscurl, no boto3)
- Auto-starts and stops an `oc port-forward` to the RGW service

## Requirements

- Python 3.6+ (stdlib only, no third-party packages)
- `oc` CLI logged in to the target OpenShift cluster
- An RGW user with admin caps (`buckets=read`)

### Setting up admin caps

```
radosgw-admin caps add --uid=<user> --caps="buckets=read"
```

## Configuration

Update the following variables in the script before use:

- `ACCESS_KEY` -- RGW admin user access key
- `SECRET_KEY` -- RGW admin user secret key
- `REGION` -- RGW region (e.g. `us-east-1`)
- `PORT_FORWARD_CMD` -- the `oc port-forward` command for your environment

## Usage

```
python3 rgw_bucket_list.py
```

## Output

A formatted table with four columns:

```
BUCKET              OWNER           OBJECTS    SIZE
my-bucket           admin                42    1.2 GiB
another-bucket      user1              108    3.4 GiB
```
