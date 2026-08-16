import json
import queue
import logging
from typing import List, Dict, Any, Tuple, Optional
import redis

from app.config import settings

logger = logging.getLogger(__name__)

STREAM_KEY = "risk_events_stream"
CONSUMER_GROUP = "risk_trigger_group"
CONSUMER_NAME = "worker_1"

class RedisEventQueue:
    """
    Redis Streams Queue:
    Events land in the Redis stream queue (`risk_events_stream`).
    Includes an in-memory queue fallback if Redis connection is unavailable.
    """
    def __init__(self, redis_url: str = settings.REDIS_URL):
        self.redis_url = redis_url
        self.client: Optional[redis.Redis] = None
        self.use_fallback = False
        self._fallback_queue = queue.Queue()
        self._init_redis()

    def _init_redis(self):
        try:
            self.client = redis.Redis.from_url(self.redis_url, decode_responses=True, socket_timeout=2.0)
            self.client.ping()
            
            # Create consumer group if it doesn't exist
            try:
                self.client.xgroup_create(STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True)
            except redis.exceptions.ResponseError as err:
                if "BUSYGROUP" not in str(err):
                    pass
            print(f"[RedisQueue] Successfully connected to Redis Stream at {self.redis_url}")
        except Exception as e:
            print(f"[RedisQueue] Warning: Redis server unreachable ({e}). Using in-memory fallback stream queue.")
            self.use_fallback = True
            self.client = None

    def publish(
        self,
        entity_name: str,
        event_type: str,
        category: Optional[str] = None,
        severity: Optional[str] = None,
        source: str = "External_Feed",
        raw_payload: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Publishes a raw incoming event payload to the Redis Stream.
        """
        payload = {
            "entity_name": entity_name,
            "event_type": event_type,
            "category": category or "",
            "severity": severity or "",
            "source": source,
            "raw_payload": raw_payload or {}
        }
        
        payload_str = json.dumps(payload)

        if not self.use_fallback and self.client:
            try:
                msg_id = self.client.xadd(STREAM_KEY, {"payload": payload_str})
                return str(msg_id)
            except Exception as e:
                print(f"[RedisQueue] Error adding to Redis stream ({e}). Falling back to internal queue.")
                self.use_fallback = True

        # In-memory fallback
        import uuid
        msg_id = f"fallback-{uuid.uuid4().hex[:8]}"
        self._fallback_queue.put((msg_id, payload))
        return msg_id

    def consume(self, count: int = 10) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Pulls a batch of unprocessed events from the queue.
        Returns a list of tuples: (msg_id, payload_dict)
        """
        results = []

        if not self.use_fallback and self.client:
            try:
                # Read from Consumer Group or Stream
                entries = self.client.xreadgroup(
                    groupname=CONSUMER_GROUP,
                    consumername=CONSUMER_NAME,
                    streams={STREAM_KEY: ">"},
                    count=count,
                    block=1000
                )
                
                if entries:
                    for stream_name, msgs in entries:
                        for msg_id, data in msgs:
                            raw_payload_str = data.get("payload", "{}")
                            try:
                                payload_dict = json.loads(raw_payload_str)
                            except Exception:
                                payload_dict = {}
                            results.append((msg_id, payload_dict))
                return results
            except Exception as e:
                print(f"[RedisQueue] Error consuming from Redis stream ({e}). Checking fallback queue.")
                self.use_fallback = True

        # Consume from in-memory fallback
        while not self._fallback_queue.empty() and len(results) < count:
            try:
                msg_id, payload = self._fallback_queue.get_nowait()
                results.append((msg_id, payload))
            except queue.Empty:
                break

        return results

    def ack(self, msg_id: str):
        """
        Acknowledges message processing in Redis stream.
        """
        if not self.use_fallback and self.client and not msg_id.startswith("fallback-"):
            try:
                self.client.xack(STREAM_KEY, CONSUMER_GROUP, msg_id)
            except Exception:
                pass


# Global Singleton Instance
event_queue = RedisEventQueue()
