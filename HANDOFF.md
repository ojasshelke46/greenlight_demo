# Handoff from feat/flight-recorder

## app/facts.py (not owned by this branch): take the ledger's transaction lock

The ledger now writes each source row and its chain entry in one transaction, serialised by `Ledger._tx_lock` on the shared connection. `app/facts.py` writes `run_facts` on that same connection from its own queue and calls `commit()` whenever it likes. If that commit lands between an events insert and its chain insert (they are separate awaits), the event row is committed before its chain entry. Normally the chain row follows in the same batch; but a crash in that window leaves an event with no chain entry, which a verifier would report as tampering.

Exact change needed:

1. In `app/ledger.py` (this branch can add it, it is a new internal accessor, no frozen signature changes):

   ```python
   @property
   def write_lock(self) -> asyncio.Lock:
       """Hold while writing and committing on the shared connection outside the ledger's own methods."""
       return self._tx_lock
   ```

2. In `app/facts.py`, pass the lock to `init` and hold it around each batch's execute and commit:

   ```python
   # app/main.py (frozen): await facts.init(ledger.db, ledger.write_lock)
   async with _write_lock:
       ...execute the batch...
       await _conn().commit()
   ```

   Step 2 touches `app/main.py`, which is frozen, so it needs the owner's go ahead.

## Notes for whoever verifies the chain (scripts/verify_export.py, app/routes/audit.py)

- A `run` entry hashes the runs row as inserted. `status`, `turn_id` and `pending_action` change later, so rebuild the snapshot as: current row, with `status = "running"`, `pending_action = null`, and `turn_id` = the run's earliest row in `turns` (`ORDER BY created_at, rowid`). `tests/test_chain.py::source_row` does exactly this.
- `ref_id` is text: the run id, the event `sequence`, or the approvals / notes autoincrement id.
- Events inserted with `INSERT OR IGNORE` that were ignored (a replayed sequence) are not chained.
- `Ledger(db_path, hmac_key=...)` gained an optional `hmac_key`; omitted, it reads `LEDGER_HMAC_KEY` from settings, so `app/main.py` needs no change.
