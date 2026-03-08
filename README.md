# Geo-Distributed NoSQL — Quorum-Replicated Key-Value & Product Store

A distributed systems prototype built with **Python (FastAPI)** and **Docker**, simulating a geo-distributed NoSQL database across multiple named regions. It implements quorum-based reads and writes, last-write-wins conflict resolution, tombstone-based soft deletes, anti-entropy synchronization, and realistic inter-region network latency using **Toxiproxy**.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
   - [Node Internals](#node-internals)
   - [Docker Container Architecture](#docker-container-architecture)
   - [Multi-Machine Cluster Layout](#multi-machine-cluster-layout)
3. [How Latency is Simulated](#how-latency-is-simulated)
4. [How Consistency is Preserved](#how-consistency-is-preserved)
   - [Quorum Writes (W)](#quorum-writes-w)
   - [Quorum Reads (R)](#quorum-reads-r)
   - [Last-Write-Wins (LWW)](#last-write-wins-lww)
   - [Anti-Entropy Sync](#anti-entropy-sync)
   - [Tombstone Deletes](#tombstone-deletes)
5. [Data Model](#data-model)
6. [Project Structure](#project-structure)
7. [API Reference](#api-reference)
8. [Web UI](#web-ui)
9. [Running the Project](#running-the-project)
   - [Single Machine (3 Nodes via Docker Compose)](#single-machine-3-nodes-via-docker-compose)
   - [Multi-Machine Cluster (Up to 9 Nodes)](#multi-machine-cluster-up-to-9-nodes)
10. [Shell Scripts Reference](#shell-scripts-reference)
11. [Quick Verification & Testing](#quick-verification--testing)
12. [Troubleshooting](#troubleshooting)
13. [Current Limitations](#current-limitations)

---

## Overview

This project simulates what a geo-distributed, eventually-consistent NoSQL data store looks like at an infrastructure level — without any external consensus library (no Raft, no Paxos, no ZooKeeper).

Key characteristics:

- Every node is **identical** — any node can accept reads or writes and acts as the coordinator.
- **Quorum consensus** ensures that a write or read requires acknowledgement from a majority of nodes before succeeding.
- **Toxiproxy** injects configurable, randomized network latency between nodes to simulate real-world geographic distances.
- **Periodic anti-entropy** reconciles any diverged state across nodes every 30 seconds.
- A **per-node Web UI** allows interactive product management directly in the browser.

---

## Architecture

### Node Internals

Each node is a single `FastAPI` (`uvicorn`) process that:

1. Maintains an **in-memory state** dictionary split into two namespaces:
   - `kv` — a generic key-value store
   - `products` — a structured product inventory
2. Persists state to a local `store.json` file after every mutation.
3. On startup, loads `store.json` and immediately pulls the full state from all known peers (`/internal/fullstate`).
4. Spawns a **background daemon thread** that re-syncs peer state every 30 seconds.
5. Forwards every write to all peer nodes via `/internal/replicate` or `/internal/replicate_product`.

```
                      ┌──────────────────────────────────┐
                      │         FastAPI Node             │
                      │                                  │
  Client Request ──►  │  coordinator_put / get           │
                      │        │                         │
                      │  ┌─────▼──────┐                  │
                      │  │ In-Memory  │ ◄── load/persist │
                      │  │   State    │     store.json   │
                      │  │ { kv, products } │            │
                      │  └─────┬──────┘                  │
                      │        │ replicate               │
                      └────────┼─────────────────────────┘
                               │
               ┌───────────────┼───────────────┐
               ▼               ▼               ▼
          Peer Node A     Peer Node B     Peer Node C
         /internal/      /internal/      /internal/
          replicate       replicate       replicate
```

### Docker Container Architecture

In the **single-machine** mode (`docker-compose.yml`), four containers run on a shared Docker bridge network (`geo-net`):

```
┌─────────────────────────────────────────────────────────────────┐
│                     Docker Network: geo-net                     │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │   mumbai     │    │   virginia   │    │  frankfurt   │       │
│  │  (port 5001) │    │  (port 5002) │    │  (port 5003) │       │
│  │              │    │              │    │              │       │
│  │ FastAPI app  │    │ FastAPI app  │    │ FastAPI app  │       │
│  │ NODE_NAME=   │    │ NODE_NAME=   │    │ NODE_NAME=   │       │
│  │   mumbai     │    │   virginia   │    │  frankfurt   │       │
│  │              │◄──►│              │◄──►│              │       │
│  │ PEERS=       │    │ PEERS=       │    │ PEERS=       │       │
│  │toxiproxy:8666│    │toxiproxy:8668│    │toxiproxy:8670│       │
│  │toxiproxy:8667│    │toxiproxy:8669│    │toxiproxy:8671│       │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘       │
│         │                  │                   │                │
│         └──────────────────┼───────────────────┘                │
│                            │                                    │
│                  ┌─────────▼──────────┐                         │
│                  │     toxiproxy      │                         │
│                  │   (port 8474 API)  │                         │
│                  │                    │                         │ 
│                  │  Proxied Routes:   │                         │
│                  │  8666 → virginia   │                         │
│                  │  8667 → frankfurt  │                         │
│                  │  8668 → mumbai     │                         │
│                  │  8669 → frankfurt  │                         │
│                  │  8670 → mumbai     │                         │
│                  │  8671 → virginia   │                         │
│                  └────────────────────┘                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

Host machine:
  localhost:5001  →  mumbai
  localhost:5002  →  virginia
  localhost:5003  →  frankfurt
  localhost:8474  →  toxiproxy control API
```

**Why do nodes talk through Toxiproxy?**

Each node's `PEERS` environment variable lists Toxiproxy ports instead of the direct container hostnames. This allows Toxiproxy to intercept every inter-node message and inject latency, making routing between `mumbai → virginia` go through `toxiproxy:8666` rather than directly to `virginia:5000`.

### Multi-Machine Cluster Layout

When `start-cluster.sh` is used across physical machines, each machine runs 3 geo-named nodes directly (no Toxiproxy in this mode) connected via the actual network:

```
  Machine 1 (e.g., 10.0.0.11)       Machine 2 (e.g., 10.0.0.12)       Machine 3 (e.g., 10.0.0.13)
  ┌──────────────────────────┐       ┌──────────────────────────┐       ┌────────────────────────────┐
  │  Mumbai   :5001          │       │  Virginia  :5001         │       │  London    :5001           │
  │  Chennai  :5002          │◄─────►│  New York  :5002         │◄─────►│  Paris     :5002           │
  │  Bangalore:5003          │       │  Washington:5003         │       │  Berlin    :5003           │
  └──────────────────────────┘       └──────────────────────────┘       └────────────────────────────┘
           │                                    │                                   │
           └────────────────────────────────────┴───────────────────────────────────┘
                                    All 9 nodes form one cluster
                              (each node's PEERS includes all 8 others)
```

---

## How Latency is Simulated

Latency simulation is handled by **[Toxiproxy](https://github.com/Shopify/toxiproxy)** — a fault injection proxy by Shopify.

### How it works

`setup-proxies.sh` performs the following steps via Toxiproxy's REST API (`localhost:8474`):

**Step 1 — Create named proxy routes for every region pair:**

| Proxy Name              | Listens On      | Forwards To      |
|-------------------------|-----------------|------------------|
| `mumbai_to_virginia`    | `0.0.0.0:8666`  | `virginia:5000`  |
| `mumbai_to_frankfurt`   | `0.0.0.0:8667`  | `frankfurt:5000` |
| `virginia_to_mumbai`    | `0.0.0.0:8668`  | `mumbai:5000`    |
| `virginia_to_frankfurt` | `0.0.0.0:8669`  | `frankfurt:5000` |
| `frankfurt_to_mumbai`   | `0.0.0.0:8670`  | `mumbai:5000`    |
| `frankfurt_to_virginia` | `0.0.0.0:8671`  | `virginia:5000`  |

**Step 2 — Inject a randomized latency "toxic" on each proxy:**

```bash
LATENCY=$((RANDOM % 300 + 100))   # 100–400 ms
JITTER=$((RANDOM % 50))           # 0–50 ms jitter
```

Each proxy gets its own randomly chosen latency value, so different region pairs experience different delays — just like real geographic routing. Example output from `setup-proxies.sh`:

```
Adding latency to mumbai_to_virginia → 247ms ± 33ms
Adding latency to mumbai_to_frankfurt → 185ms ± 12ms
Adding latency to virginia_to_mumbai → 310ms ± 47ms
...
```

**Step 3 — Nodes route through Toxiproxy:**

When `mumbai` replicates a write to `virginia`, it does not call `virginia:5000` directly. It instead calls `toxiproxy:8666`, which forwards to `virginia:5000` with the injected latency applied. This means all internal replication traffic experiences realistic per-link delays.

### Effect on Quorum Operations

Because each replication call crosses Toxiproxy, the latency metrics returned in write/read API responses reflect real simulated inter-region round-trip times:

```json
{
  "status": "write_success",
  "acks": 3,
  "total_time_seconds": 0.423,
  "peer_latencies": [
    ["toxiproxy:8666", 0.247],
    ["toxiproxy:8667", 0.185]
  ]
}
```

---

## How Consistency is Preserved

This project implements **quorum-based eventual consistency** — a design pattern used by systems like Apache Cassandra and Amazon DynamoDB.

### Quorum Writes (W)

```
N = total nodes known to coordinator (self + number of peers)
W = floor(N / 2) + 1   (strict majority)
```

**Write path:**

1. Client sends a `POST /put` or `POST /products` to any node (the coordinator).
2. Coordinator writes to its own local state immediately.
3. Coordinator calls `/internal/replicate` (or `/internal/replicate_product`) on every peer in parallel (sequentially in current implementation).
4. Each peer applies the value and responds `200 OK`.
5. Coordinator counts acknowledgements (acks). If `acks >= W`, the write is confirmed and `store.json` is persisted.
6. If `acks < W`, a `500 Write quorum not met` error is returned to the client.

**Example with 3 nodes (N=3, W=2):**

```
Client → mumbai (coordinator writes locally)
           ├── replicate → virginia  ✓ (ack 1 + self = 2 >= W=2 → SUCCESS)
           └── replicate → frankfurt ✓ (ack bonus)
```

### Quorum Reads (R)

```
R = floor(N / 2) + 1   (strict majority)
```

**Read path:**

1. Client sends `GET /get/{key}` to any node.
2. Coordinator reads its own local value.
3. Coordinator calls `/internal/get/{key}` on every peer.
4. All received responses are collected.
5. If `len(responses) >= R`, the value with the **highest timestamp** is returned.
6. If fewer than R responses are collected, `500 Read quorum not met` is returned.

This ensures the client always gets the most recent write, even if one node is stale.

### Last-Write-Wins (LWW)

Every write operation attaches a Unix timestamp (`time.time()`) to the stored value:

```json
{
  "kv": {
    "price": { "value": "99.99", "timestamp": 1710000100.512 }
  }
}
```

When multiple versions of the same key are collected during a quorum read or anti-entropy sync, the entry with the **largest timestamp wins**:

```python
latest = max(responses, key=lambda x: x["timestamp"])
```

This resolves concurrent write conflicts deterministically — no manual merge logic required.

### Anti-Entropy Sync

Beyond quorum operations, every node runs a **background thread** that wakes up every 30 seconds and calls `sync_from_peers()`:

```
Node startup
    │
    ├── load_store()         # Load last persisted state from disk
    ├── sync_from_peers()    # Immediately pull state from all peers
    └── threading.Thread(target=periodic_sync, daemon=True).start()
                                        │
                                   every 30s:
                                   sync_from_peers()
```

`sync_from_peers()` fetches `/internal/fullstate` from each peer, then merges:
- For the `kv` store: keeps whichever version (local or peer) has the higher timestamp.
- For `products`: same timestamp-based LWW merge, including soft-deleted entries.

This guarantees **eventual convergence** — even if a node missed writes while it was offline or partitioned, it will catch up on the next sync cycle.

### Tombstone Deletes

When a product is deleted, it is **not removed** from the state. Instead, a tombstone flag is set:

```json
{
  "product_id": "p1",
  "deleted": true,
  "timestamp": 1710000222.801
}
```

This tombstone entry is replicated to all peers just like any other update. Without tombstones, a deleted product could be "resurrected" during anti-entropy sync if a peer still has the old non-deleted entry with an older timestamp. The `deleted: true` flag with a newer timestamp propagates the deletion safely across the cluster.

The `GET /products` endpoint filters out tombstoned entries before returning to clients:

```python
products = [p for p in state["products"].values() if not p.get("deleted", False)]
```

---

## Data Model

State is stored in memory and persisted to `store.json` on each node:

```json
{
  "kv": {
    "some-key": {
      "value": "some-value",
      "timestamp": 1710000100.512
    }
  },
  "products": {
    "p1": {
      "product_id": "p1",
      "name": "Tea Pack",
      "description": "250g Green Tea",
      "price": 10.5,
      "stock": 42,
      "timestamp": 1710000200.123,
      "deleted": false
    }
  }
}
```

**Field descriptions:**

| Field        | Type    | Description                                              |
|--------------|---------|----------------------------------------------------------|
| `value`      | string  | Stored value for the KV entry                            |
| `timestamp`  | float   | Unix epoch timestamp used for LWW conflict resolution    |
| `product_id` | string  | Unique product identifier (used as the map key)          |
| `price`      | float   | Must be >= 0 (validated by Pydantic)                     |
| `stock`      | int     | Must be >= 0; decremented on purchase                    |
| `deleted`    | bool    | Tombstone flag; `true` means logically deleted           |

---

## Project Structure

```
geo-distributed-nosql/
│
├── app/
│   ├── server.py              # Core FastAPI server: quorum logic, replication,
│   │                          #   anti-entropy sync, product CRUD, KV store
│   ├── requirements.txt       # Python deps: fastapi, uvicorn, requests, pydantic
│   └── static/
│       └── index.html         # Per-node browser UI (vanilla HTML/CSS/JS)
│
├── Dockerfile                 # python:3.11-slim, installs deps, runs uvicorn
├── docker-compose.yml         # 3 geo nodes (mumbai, virginia, frankfurt) + toxiproxy
│
├── run.sh                     # One-shot: compose down → up --build → setup-proxies
├── setup-proxies.sh           # Creates Toxiproxy routes + injects random latency
├── start-cluster.sh           # Role-based launcher for multi-machine deployment
├── stop-cluster.sh            # Stops named containers for a given machine role
├── terminate.sh               # Thin wrapper around stop-cluster.sh
│
├── write-quorum-test.sh       # curl: POST /put to node on port 5001
├── read-quorum-test.sh        # curl: GET /get/x from node on port 5002
│
└── commands.txt               # Manual command reference for real machine deployments
```

---

## API Reference

### Public Endpoints

#### Key-Value Store

| Method | Endpoint          | Description                                  |
|--------|-------------------|----------------------------------------------|
| `POST` | `/put`            | Quorum write — stores a key-value pair        |
| `GET`  | `/get/{key}`      | Quorum read — returns latest value for key    |
| `GET`  | `/keys`           | Returns the full local KV store              |

**PUT request body:**
```json
{ "key": "price", "value": "99.99" }
```

**PUT response:**
```json
{
  "status": "write_success",
  "acks": 3,
  "total_time_seconds": 0.412,
  "peer_latencies": [["toxiproxy:8666", 0.247], ["toxiproxy:8667", 0.185]]
}
```

#### Product Store

| Method   | Endpoint                          | Description                              |
|----------|-----------------------------------|------------------------------------------|
| `GET`    | `/products`                       | List all active (non-deleted) products   |
| `POST`   | `/products`                       | Create a new product (quorum write)      |
| `PUT`    | `/products/{product_id}`          | Update name, description, or price      |
| `POST`   | `/products/{product_id}/stock`    | Add stock units                          |
| `POST`   | `/products/{product_id}/purchase` | Purchase — reduces stock                 |
| `DELETE` | `/products/{product_id}`          | Soft delete via tombstone replication    |

**Create product body:**
```json
{
  "product_id": "sku-001",
  "name": "Tea Pack",
  "description": "250g Green Tea",
  "price": 10.5,
  "stock": 100
}
```

**Add stock body:**
```json
{ "amount": 50 }
```

**Purchase body:**
```json
{ "quantity": 2 }
```

#### Health & Utility

| Method | Endpoint    | Description                                  |
|--------|-------------|----------------------------------------------|
| `GET`  | `/`         | Serves the per-node Web UI (`index.html`)    |
| `GET`  | `/health`   | Returns node name, status, and peer list     |

**Health response:**
```json
{ "node": "mumbai", "status": "healthy", "peers": ["toxiproxy:8666", "toxiproxy:8667"] }
```

### Internal Node-to-Node Endpoints

These are used exclusively for peer replication and state exchange. Do not call them from client code.

| Method | Endpoint                          | Description                                      |
|--------|-----------------------------------|--------------------------------------------------|
| `POST` | `/internal/replicate`             | Receives a replicated KV entry from coordinator  |
| `POST` | `/internal/replicate_product`     | Receives a replicated product entry              |
| `GET`  | `/internal/get/{key}`             | Returns a single KV value (used in quorum reads) |
| `GET`  | `/internal/fullstate`             | Returns complete `{kv, products}` state snapshot |

---

## Web UI

Each node serves an identical browser UI at its root URL (`/`). The UI connects to the **same node it was loaded from** using relative API paths.

**Features:**
- **Create Product** — fill in ID, name, description, price, and initial stock
- **Products Table** — live table listing all active products with ID, name, price, stock
- **Update Product Info** — update name, description, or price by ID
- **Add Stock** — increase inventory count for any product
- **Purchase Product** — buy a given quantity; decrements stock
- **Delete Product** — soft-delete via tombstone

**Local URLs (default single-machine setup):**

| Node      | URL                         |
|-----------|-----------------------------|
| Mumbai    | http://localhost:5001       |
| Virginia  | http://localhost:5002       |
| Frankfurt | http://localhost:5003       |

To observe replication in action, create a product via one node's UI and load products on another — they should match (allowing for a short propagation window).

---

## Running the Project

### Prerequisites

- **Docker Engine** (v20+)
- **Docker Compose v2** (`docker compose` subcommand — not legacy `docker-compose`)
- **`jq`** — required by `setup-proxies.sh` to parse Toxiproxy's proxy list

Install `jq` on Ubuntu/Debian:
```bash
sudo apt-get install -y jq
```

---

### Single Machine (3 Nodes via Docker Compose)

This is the standard local development and demo setup. All three nodes run as containers on your machine, connected through Toxiproxy for simulated latency.

**Start everything:**
```bash
./run.sh
```

`run.sh` is a convenience script that executes:
```bash
docker compose down         # Tear down any existing containers
docker compose up -d --build  # Build image and start 4 containers (3 nodes + toxiproxy)
./setup-proxies.sh          # Configure Toxiproxy routes and inject random latency
```

**Alternatively, step by step:**
```bash
docker compose up -d --build
./setup-proxies.sh
```

**Stop:**
```bash
docker compose down
```

---

### Multi-Machine Cluster (Up to 9 Nodes)

`start-cluster.sh` supports deploying 3 nodes per machine, for a total of up to 9 nodes across 3 machines.

**Node layout per machine role:**

| Role       | Node 1     | Node 2      | Node 3         | Port Map               |
|------------|------------|-------------|----------------|------------------------|
| `machine1` | Mumbai     | Chennai     | Bangalore      | 5001, 5002, 5003       |
| `machine2` | Virginia   | New York    | Washington DC  | 5001, 5002, 5003       |
| `machine3` | London     | Paris       | Berlin         | 5001, 5002, 5003       |

**Start on each machine (run simultaneously):**

```bash
# On machine 1:
./start-cluster.sh machine1 10.0.0.11 10.0.0.12 10.0.0.13

# On machine 2:
./start-cluster.sh machine2 10.0.0.11 10.0.0.12 10.0.0.13

# On machine 3:
./start-cluster.sh machine3 10.0.0.11 10.0.0.12 10.0.0.13
```

The script automatically:
1. Builds the `geo-quorum` Docker image.
2. Creates a shared Docker network `geo-net`.
3. Launches 3 containers with `PEERS` pre-configured to include all 6 remote node addresses on the other machines.

**Two-machine mode (6 nodes total):**
```bash
./start-cluster.sh machine1 10.0.0.11 10.0.0.12
./start-cluster.sh machine2 10.0.0.11 10.0.0.12
```

**Single machine mode (3 local nodes, no remote peers):**
```bash
./start-cluster.sh machine1 10.0.0.11
```

**Stop cluster:**
```bash
./stop-cluster.sh machine1      # Stop Mumbai, Chennai, Bangalore
./stop-cluster.sh machine2      # Stop Virginia, New York, Washington DC
./stop-cluster.sh machine3      # Stop London, Paris, Berlin
# or stop everything at once:
./stop-cluster.sh all
```

**Alternative stop using the wrapper:**
```bash
./terminate.sh machine1
```

**Network Requirements (multi-machine mode):**
- All machines must allow inbound TCP on ports **5001**, **5002**, **5003**.
- Verify connectivity before starting: `curl http://<peer-ip>:5001/health`

---

## Shell Scripts Reference

| Script                | Purpose                                                                              |
|-----------------------|--------------------------------------------------------------------------------------|
| `run.sh`              | One-command local start: compose down → build → up → setup-proxies                  |
| `setup-proxies.sh`    | Configures Toxiproxy: creates 6 named proxy routes, injects 100–400ms random latency |
| `start-cluster.sh`    | Role-based multi-machine node launcher (machine1/2/3 roles, 3 nodes each)            |
| `stop-cluster.sh`     | Stops and removes containers for a given machine role or all roles                   |
| `terminate.sh`        | Thin wrapper around `stop-cluster.sh` for scripted teardowns                         |
| `write-quorum-test.sh`| Quick test: `POST /put` with `key=x, value=hello` to `localhost:5001`               |
| `read-quorum-test.sh` | Quick test: `GET /get/x` from `localhost:5002`                                       |

---

## Quick Verification & Testing

**Check all nodes are healthy:**
```bash
curl http://localhost:5001/health
curl http://localhost:5002/health
curl http://localhost:5003/health
```

**Write a key-value pair to node 1:**
```bash
curl -X POST http://localhost:5001/put \
  -H "Content-Type: application/json" \
  -d '{"key":"city","value":"Mumbai"}'
```

**Read it from node 2 (demonstrates cross-node quorum read):**
```bash
curl http://localhost:5002/get/city
```

**Run the bundled quorum tests:**
```bash
./write-quorum-test.sh
./read-quorum-test.sh
```

**Create a product on Mumbai, read from Frankfurt:**
```bash
# Write to Mumbai (port 5001)
curl -X POST http://localhost:5001/products \
  -H "Content-Type: application/json" \
  -d '{"product_id":"p1","name":"Tea Pack","description":"250g","price":10.5,"stock":50}'

# Read from Frankfurt (port 5003)
curl http://localhost:5003/products
```

**Purchase and verify stock reduction:**
```bash
curl -X POST http://localhost:5001/products/p1/purchase \
  -H "Content-Type: application/json" \
  -d '{"quantity":5}'

curl http://localhost:5002/products
```

**Soft delete and verify tombstone propagation:**
```bash
curl -X DELETE http://localhost:5001/products/p1

# Product should be absent from both nodes
curl http://localhost:5001/products
curl http://localhost:5003/products
```

**Observe real-time logs with simulated latency:**
```bash
docker logs -f mumbai
docker logs -f virginia
```

---

## Troubleshooting

### `docker compose` fails with `http+docker` error
Use the Compose v2 plugin subcommand. Do **not** use the legacy `docker-compose` binary:
```bash
docker compose up -d --build    # correct
docker-compose up -d --build    # incorrect (legacy)
```

### `setup-proxies.sh` fails or produces no output
Install `jq`:
```bash
sudo apt-get update && sudo apt-get install -y jq
```

### Nodes not syncing data
1. Verify all containers are running: `docker ps`
2. Test container-to-container connectivity:
   ```bash
   docker exec mumbai curl -s http://virginia:5000/health
   ```
3. Check that Toxiproxy proxies were created:
   ```bash
   curl http://localhost:8474/proxies
   ```

### Nodes on different machines not seeing each other
1. Verify firewall rules allow ports 5001–5003 inbound on each machine.
2. Test reachability: `curl http://<peer-ip>:5001/health`
3. Verify the `PEERS` env var in each container includes all remote node addresses:
   ```bash
   docker inspect <container_name> | grep -A5 PEERS
   ```

### Write quorum not met
You need at least W nodes (majority) to be reachable and healthy. Check:
```bash
docker ps        # All expected containers running?
docker logs <node>   # Any connection errors in the logs?
```

---

## Current Limitations

| Limitation                         | Details                                                                             |
|------------------------------------|-------------------------------------------------------------------------------------|
| No distributed consensus           | No Raft or Paxos — leadership or split-brain scenarios are not handled              |
| Last-write-wins only               | Concurrent writes to the same key may silently overwrite each other                 |
| Clock skew sensitivity             | LWW relies on `time.time()` — clock drift across machines can cause stale reads     |
| Sequential peer replication        | Replication to peers is done one-by-one, not in parallel (increases write latency)  |
| No authentication or TLS           | All inter-node and client traffic is plain HTTP                                     |
| No region-aware routing            | Clients are not redirected to the nearest node; any node accepts any request        |
| Tombstones never garbage collected | Deleted products remain in `store.json` forever                                     |
| Single store file                  | `store.json` is not sharded; all data lives in one file per node                   |

---

## Technology Stack

| Component       | Technology                    |
|-----------------|-------------------------------|
| Node server     | Python 3.11, FastAPI, Uvicorn |
| HTTP client     | `requests` (peer replication) |
| Data validation | Pydantic v2                   |
| Containerization| Docker, Docker Compose v2     |
| Latency proxy   | Shopify Toxiproxy             |
| Persistence     | JSON file (`store.json`)      |
| Web UI          | Vanilla HTML, CSS, JavaScript |

---

## License / Purpose

Educational prototype demonstrating quorum replication, eventual consistency, anti-entropy synchronization, and geo-distributed NoSQL behavior simulation — without any third-party consensus library.
