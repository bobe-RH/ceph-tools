#!/usr/bin/env python3
import rados
cluster = rados.Rados(conffile='/etc/ceph/ceph.conf')
cluster.connect()
ioctx = cluster.open_ioctx('us-east.rgw.buckets.index')
marker = '8f62d44e-52aa-47f9-8f8f-4b4ba9608b9c.2616585.2'
for i in range(11):
    oid = '.dir.%s.%d' % (marker, i)
    try:
        ioctx.execute(oid, 'rgw', 'bucket_init_index', b'')
        print('OK shard %d' % i)
    except Exception as e:
        print('shard %d error: %s' % (i, e))
ioctx.close()
cluster.shutdown()
