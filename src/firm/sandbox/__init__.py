"""Run untrusted strategy code in a locked-down worker process (M3 plan, R2).

Agent-written strategies never run inside the firm's own processes. Each
runs in a separate Python process that:

- is wrapped in an OS sandbox (`sandbox-exec` on macOS, `bubblewrap` on
  Linux): no network, no reading of the owner's home folder, the repo or
  the stores, no writing outside its own temporary folder, no starting
  other programs. With neither available, nothing runs (fail closed);
- starts with a clean environment (no keys, no `.env`);
- has CPU, memory and wall-clock limits;
- may import only an allow-list of modules (numpy, pandas, ...). This is
  hygiene and an early, clear error; the OS sandbox is the boundary.

Bars go in and signals come out over a pipe (Arrow for the bars). A shared
library like `bus`: it imports only `core`.
"""
