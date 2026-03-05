import os
import time
import json
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict
import logging

STORE_FILE = "store.json"
def load_store():
    global store
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE, "r") as f:
                store = json.load(f)
            logger.info(f"[{NODE_NAME}] Loaded {len(store)} records from disk")
        except Exception:
            logger.warning(f"[{NODE_NAME}] Failed to load store.json, starting empty")
            store = {}

def persist_store():
    with open(STORE_FILE, "w") as f:
        json.dump(store, f)
    
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI()

store: Dict[str, Dict] = {}
load_store()

# Configuration
NODE_NAME = os.getenv("NODE_NAME", "node")
PEERS = os.getenv("PEERS", "").split(",") if os.getenv("PEERS") else []
N = len(PEERS) + 1   # peers + self
W = (N // 2) + 1
R = (N // 2) + 1

# In-memory store
store: Dict[str, Dict] = {}

class Item(BaseModel):
    key: str
    value: str

import threading

def periodic_sync():
    while True:
        time.sleep(30)
        sync_from_peers()

@app.on_event("startup")
def startup():
    load_store()
    sync_from_peers()
    threading.Thread(target=periodic_sync, daemon=True).start()

@app.get("/health")
def health():
    return {"node": NODE_NAME, "status": "healthy"}

@app.post("/internal/replicate")
def internal_replicate(item: Item):
    timestamp = time.time()
    store[item.key] = {"value": item.value, "timestamp": timestamp}
    persist_store()
    return {"status": "replicated", "node": NODE_NAME}

@app.get("/internal/fullstore")
def full_store():
    return store

def sync_from_peers():
    logger.info(f"[{NODE_NAME}] Starting peer synchronization")

    for peer in PEERS:
        try:
            response = requests.get(f"http://{peer}/internal/fullstore", timeout=5)
            if response.status_code != 200:
                continue

            peer_store = response.json()

            for key, value in peer_store.items():
                if key not in store or value["timestamp"] > store[key]["timestamp"]:
                    store[key] = value

            logger.info(f"[{NODE_NAME}] Synced data from {peer}")

        except Exception as e:
            logger.warning(f"[{NODE_NAME}] Failed to sync from {peer}: {e}")

    persist_store()

@app.post("/put")
def put(item: Item):
    start_time = time.time()
    logger.info(f"[{NODE_NAME}] WRITE requested for key={item.key}")

    timestamp = time.time()
    store[item.key] = {"value": item.value, "timestamp": timestamp}

    acks = 1
    peer_latencies = []

    for peer in PEERS:
        peer_start = time.time()
        try:
            response = requests.post(
                f"http://{peer}/internal/replicate",
                json={"key": item.key, "value": item.value},
                timeout=5
            )

            latency = time.time() - peer_start
            peer_latencies.append((peer, latency))

            if response.status_code == 200:
                acks += 1
                logger.info(f"[{NODE_NAME}] ACK from {peer} in {latency:.3f}s")
        except Exception as e:
            latency = time.time() - peer_start
            logger.warning(f"[{NODE_NAME}] FAILED contacting {peer} after {latency:.3f}s")

    total_time = time.time() - start_time

    logger.info(
        f"[{NODE_NAME}] WRITE completed | acks={acks} | total_time={total_time:.3f}s"
    )

    if acks >= W:
        persist_store()
        return {
            "status": "write_success",
            "acks": acks,
            "total_time_seconds": round(total_time, 3),
            "peer_latencies": peer_latencies,
        }
    else:
        raise HTTPException(status_code=500, detail="Write quorum not met")


@app.get("/get/{key}")
def get(key: str):
    start_time = time.time()
    logger.info(f"[{NODE_NAME}] READ requested for key={key}")

    responses = []

    if key in store:
        responses.append(store[key])

    for peer in PEERS:
        peer_start = time.time()
        try:
            response = requests.get(f"http://{peer}/internal/get/{key}", timeout=5)
            latency = time.time() - peer_start

            if response.status_code == 200:
                responses.append(response.json())
                logger.info(f"[{NODE_NAME}] READ from {peer} in {latency:.3f}s")
        except Exception:
            latency = time.time() - peer_start
            logger.warning(f"[{NODE_NAME}] FAILED reading from {peer} after {latency:.3f}s")

    if len(responses) < R:
        raise HTTPException(status_code=500, detail="Read quorum not met")

    latest = max(responses, key=lambda x: x["timestamp"])
    total_time = time.time() - start_time

    logger.info(
        f"[{NODE_NAME}] READ completed | total_time={total_time:.3f}s"
    )

    return {
        "key": key,
        "value": latest["value"],
        "total_time_seconds": round(total_time, 3),
    }


@app.get("/internal/get/{key}")
def internal_get(key: str):
    if key not in store:
        raise HTTPException(status_code=404, detail="Key not found")
    return store[key]