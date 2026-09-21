<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​‌‌​‌‌‌​​‌​​​​​‌​‌‌​‌​​​​‌​‌​‌‌​​‌​​​‌‌‌​​‌‌‌​​‌​‌‌‌​​​​​‌‌​​​‌‌​‌‌​​​‌‌​‌‌​​​‌‌​‌​​‌‌‌​​‌​‌‌‌‌‌​‌‌​‌‌‌​​‌‌​‌​​‌​‌​​​‌​‌​‌​​​​​‌​​‌‌​​​‌​‌‌‌‌​​​​‌​‌‌​​‌​​‌‌​​‌​​‌​​‌​‌​​‌‌‌‌​​​⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.nAhVG9pcccN_niEA1xY2Jx
-->
# Repo map: mini_src

files: 5  nodes: 11  edges: 12
languages: python=3, md=1

## Most depended-on files
- pkg/audit.py (in=1)

## Most called symbols
- pkg/audit.py::audit_event (function, in=1)

## Top entry points
- `route_request` - pkg/gateway.py (reach 1)

---

### [cite: .env:1-5] `.env` (seed)
# file: .env (, 5 lines)
# deployment ledger credential store
ACME_DEPLOYMENT_LEDGER_TOKEN=abc123deadbeef
ACME_DEPLOYMENT_LEDGER_SECRET=zzz999notreal
DEPLOYMENT_LEDGER_CREDENTIAL=deployment ledger credential inbound request handler


### [cite: docs/notes.md:1-10] `docs/notes.md` (seed)
# file: docs/notes.md (md, 10 lines)
# Notes

The gateway dispatches an inbound request to a handler.
# Notes

The gateway dispatches an inbound request to a handler.
# Notes

The gateway dispatches an inbound request to a handler.


### [cite: pkg/audit.py:2-7] `audit_event` (seed)
# file: pkg/audit.py
# function: audit_event  (lines 2-7, python)
# called by: pkg/gateway.py::route_request
# doc: Append one entry to the tamper evident journal of handled events.      The journal is append only so that a later reader can replay it verbatim.
def audit_event(name):
    """Append one entry to the tamper evident journal of handled events.

    The journal is append only so that a later reader can replay it verbatim.
    """
    return {"event": name}

### [cite: pkg/gateway.py:5-13] `route_request` (seed)
# file: pkg/gateway.py
# function: route_request  (lines 5-13, python)
# entry point: nothing in this repo calls it — a flow starts here
# calls: pkg/audit.py::audit_event
# calls (outside the repo): get
# doc: Dispatch one inbound request to its handler and record the outcome.      The router keeps no state of its own, which makes it safe to call from any     worker thread, and it deliberately never touches a socket or a clock.
def route_request(request):
    """Dispatch one inbound request to its handler and record the outcome.

    The router keeps no state of its own, which makes it safe to call from any
    worker thread, and it deliberately never touches a socket or a clock.
    """
    handler = request.get("handler")
    audit_event(handler)
    return handler


