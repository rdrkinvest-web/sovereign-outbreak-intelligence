# Outbreak Console

A local web page where you type a prompt, run it on the federation, and watch it happen:
the coordinator's questions, each node's checks on its own records, the aggregate answers
that come back, and "Patient rows sent: 0".

```bash
uv run python ui/server.py        # then open http://127.0.0.1:8765
```

- **Live on SuperGrid** reuses your `uv run flwr login supergrid` session and builds the app
  from this repo on every run (like `/load .` in `flwr chat`). The federation defaults to
  the one with the most online national nodes (`@pbggo/east-africa-moh`).
- **Offline rehearsal** runs the in-process `LocalFederation`: deterministic answers, no
  network, no models. Use it if the wifi fails.
- The Nebius chips read VM state from `scripts/nebius_vms.sh status` (read-only).
- **Export replay.json** saves a finished run; **Load a replay.json** plays it back labelled
  as a replay. The same file loads in the published replay page.

The page shows only what the coordinator receives: structured events emitted by
`agent/events.py` (switch them off with `ui-events = false` in `pyproject.toml`) and the
chat Markdown. Replies to one question arrive together, so their order on screen is
illustrative, and the local checks are drawn from each node's reported `tools_used`.
