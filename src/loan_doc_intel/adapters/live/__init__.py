"""``live`` profile adapters: the shared local model on the laptop stack.

Under live every port binds the SDK-free ``local`` adapter except the model port, which
calls the fleet's local open-weight model through :mod:`hex_service_kit.localmodel`. The
deterministic cross-validation still decides every check; the model only normalises income
figures from the extracts.
"""
