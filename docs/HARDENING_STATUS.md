# Kuza 2.1 Hardening Status

This document supersedes stale pre-hardening TODO claims.

## Incorporated

- Local 7B agent, 0.5B planner, and Nomic embedding server
- Persistent daemon and SQLite task queue
- File, shell, web, Holehe, memory, RAG, peer, Git, GUI, and pipeline capabilities
- Read-only evidence guards, checkpoints, linting, and anti-fabrication checks

## Hardened in 2.1.0

- GUI defaults to loopback and requires a token for non-loopback binding
- Persistent task cancellation is terminal and cannot be overwritten
- One shared action authorization policy handles interactive, daemon, YOLO, and cancellation behavior
- Peer-generated changes use the normal file safety layer
- Sidecar uses one worker pool
- Long-term memory and RAG use the same Nomic embedding service
- Operational scripts and GUI/CI files receive self-modification checkpoints
- Source writes are atomic
- Daemon process cleanup targets exact tracked processes
- Git staging rejects likely secrets and validates branch names
- A tracked `kuza` launcher is installed and verified
- CI includes hardening regression tests
