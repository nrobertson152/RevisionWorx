# Shared filesystem research prototype

This code is **not production safe** and is not integrated with the desktop application. It is a local POSIX multi-process demonstration, not an SMB/NFS network validation.

Run: `cd prototype && python -m unittest -v`.

It demonstrates mkdir-based serialized checkout, explicit tokens, immutable SHA-256 blobs, PREPARED and COMMIT markers, journal sequence replay, crash injection and post-crash projection recovery. The mutex persists after some crashes and must **never** be removed automatically: unsafe deletion can create split-brain. Multi-file manifests, authoritative transactional metadata, true monotonic fencing generations, safe network partition recovery, actual SMB/NFS tests and production-level administrative auditing remain unimplemented. The recommended production direction is an on-premise coordinator with a local durable database.
