"""Generic Modbus TCP Simulator engine package.

See REQUIREMENTS.md for the full specification. This package implements the
Engine layer: state machine, config/signal loaders, register store, Modbus TCP
servers, network manager, and the Flask REST API that is the contract between
the Engine and any client (web UI, scripts, tests).
"""

__version__ = "3.0.0"
