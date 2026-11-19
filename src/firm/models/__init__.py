"""Model calls: the one gateway every call goes through (firm rule 8, M3 plan R3).

`ModelGateway.chat` resolves a role to its model (config, never code),
checks the data-egress rules (private work stays local unless the owner
opted in for the run; cloud spend within its monthly budget), calls the
provider, and writes a MODEL_CALL ledger event for every call, refused or
not. Provider adapters (`ollama.py`) are the only code that talks to a
model server. A shared library like `bus`: it imports only `core`.
"""
