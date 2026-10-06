"""FastAPI service behind the analyst UI (T-028, docs/impl/api.md).

The UI talks only to this service. It reads the platform's own tables through
`ais0c_storage.repositories`, writes only what an operator changes, and reaches Temporal only to
trigger a Schedule. It sends no request to QRadar, Falcon, the gateway or a model, and has no
endpoint that closes an offense, changes a rule or runs an action (D-02, D-19).

Settings: `ais0c_api.settings`. Entry point: `python -m ais0c_api`.
"""
