import type { ProgressEvent } from '@bh/core';
import type { Db, GenerationStatus } from './db.js';

export type StoredEvent = { seq: number; event: ProgressEvent };

/** What subscribers receive: a stored progress event, or the end of the run. */
export type BusMessage = ({ kind: 'event' } & StoredEvent) | { kind: 'done'; status: GenerationStatus };

export interface EventBus {
  /** Stores the event (next seq for this job) and pushes it to subscribers. */
  emit(generationId: string, event: ProgressEvent): StoredEvent;
  /** Announces that the run ended in `status`. Not stored: late readers use the job's status. */
  finish(generationId: string, status: GenerationStatus): void;
  /** Stored events with `seq > afterSeq`, in order. */
  history(generationId: string, afterSeq: number): StoredEvent[];
  /** Returns the unsubscribe function. */
  subscribe(generationId: string, listener: (m: BusMessage) => void): () => void;
}

export function createEventBus(db: Db): EventBus {
  const insert = db.prepare(
    `INSERT INTO events (generation_id, seq, event_json)
     VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE generation_id = ?), ?) RETURNING seq`,
  );
  const select = db.prepare(`SELECT seq, event_json FROM events WHERE generation_id = ? AND seq > ? ORDER BY seq`);
  const listeners = new Map<string, Set<(m: BusMessage) => void>>();

  const publish = (id: string, message: BusMessage): void => {
    for (const listener of [...(listeners.get(id) ?? [])]) {
      try {
        listener(message);
      } catch {
        // one broken subscriber must not stop the job
      }
    }
  };

  return {
    emit(id, event) {
      const { seq } = insert.get(id, id, JSON.stringify(event)) as { seq: number };
      publish(id, { kind: 'event', seq, event });
      return { seq, event };
    },
    finish(id, status) {
      publish(id, { kind: 'done', status });
    },
    history(id, afterSeq) {
      return (select.all(id, afterSeq) as { seq: number; event_json: string }[]).map((r) => ({
        seq: r.seq,
        event: JSON.parse(r.event_json) as ProgressEvent,
      }));
    },
    subscribe(id, listener) {
      let set = listeners.get(id);
      if (!set) listeners.set(id, (set = new Set()));
      set.add(listener);
      return () => {
        set.delete(listener);
        if (set.size === 0) listeners.delete(id);
      };
    },
  };
}
