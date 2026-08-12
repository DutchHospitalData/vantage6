.. _server-diagnosing-loops:

Diagnosing retry loops
======================

The failure modes below all look the same from the outside: the server gets
slow, nodes show up as offline, and the log fills with repeating lines. They
have different causes and different fixes, and telling them apart takes about a
minute if you know what to grep for.

This page is written to be usable by someone who has never seen the codebase,
including an AI assistant working from logs alone.

.. note::

    Every loop here shares one property: **the repeating line is a symptom, not
    the cause**. Before changing anything, find out whether the repetition is a
    client that cannot succeed, or a server that cannot answer. The counting
    commands under each section are there to settle that question with a number
    instead of an impression.

The four-line triage
--------------------

Run these against a recent slice of the server log. The one that returns a big
number tells you which section to read.

.. code-block:: bash

    # 1. dead sockets: the original outage
    grep -c "Cannot send to sid" server.log

    # 2. ghost sessions: node online, server does not know it
    grep -c "is not connected to namespace" server.log

    # 3. a run that can never finish
    grep -c "attempts to generate a key for completed task" server.log

    # 4. the temporary unknown-session guard misfiring
    grep -c "closing its connection so it can identify itself" server.log

A handful of any of these is normal. Hundreds per minute is a loop.

1. Broadcasts to sockets that are gone
--------------------------------------

**Looks like** ``Cannot send to sid <id>``, repeated, with API latency climbing
into seconds and nodes dropping off in groups.

**Cause** ``on_connect`` stored the rooms it joined on the session object. When
that attribute was missing at disconnect time an ``AttributeError`` escaped
before python-socketio reached ``manager.disconnect()``, so the sid was never
removed from any room. Every later broadcast then tried to write to a socket
that no longer existed. Under load the event loop spends its time on dead
sockets, HTTP requests time out, and the timeouts cause more disconnects, which
create more stale sids.

**Confirm** the same sid appears in ``Cannot send to sid`` long after that
client disconnected:

.. code-block:: bash

    grep "Cannot send to sid" server.log | awk '{print $NF}' | sort | uniq -c | sort -rn | head

**Fix** shipped in this PR: leave every room explicitly and tolerate a session
that is already gone.

**Note** this makes the server survive the condition. It does not explain what
triggers the first stall, which is still open. Ours coincided with
``no PONG received in 3 seconds`` and multi-megabyte blob transfers happening at
the same moment.

.. _server-node-mass-drop:

Recognising a mass drop, and dating it
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When every node goes offline at once the useful question is not *that* they
left but *when*, because the shape of the answer names the cause. Ask the API
rather than the UI, which only shows the current state:

.. code-block:: python

    res = client.node.list(collaboration=<id>, per_page=100)
    for n in sorted(res["data"], key=lambda x: str(x["last_seen"])):
        print(n["id"], n["status"], n["last_seen"], n["name"])

Read the spread between the first and the last ``last_seen``:

* **All within a second or two.** The server dropped them. Look at the server
  process itself: a restart, a redeploy, or a crash.
* **Spread over several minutes.** A ping-timeout cascade. The nodes died one
  by one as each missed its own ping deadline, which points at something that
  blocked the server's event loop for a while rather than something that killed
  it.
* **Spread over hours, unrelated times.** Not one incident. Treat each node
  separately.

Then line the window up against what the platform was doing. A cascade that
starts immediately after a large task finishes, while results are being
collected and pushed to blob storage, is the signature described above.

We observed exactly this: ten of ten nodes left over a six minute window that
began about two minutes after a training task completed and ended seconds
before the next one was submitted. The server stayed responsive throughout, at
roughly 30 ms on ``/api/version``, which rules out the server being down and
leaves the event loop being blocked as the working explanation.

.. warning::

    A responsive server does not mean a healthy platform. Check node presence
    and whether a submitted task is actually being picked up. A task sitting in
    ``pending`` with no ``started_at`` while ``/api/version`` answers instantly
    is the combination to watch for.

**Why they do not come back on their own** is section 2: the reconnect fix is
node side. Until the nodes run it, a node that loses its session stays gone
until someone restarts it, however healthy the server is.

2. A node that thinks it is connected while the server disagrees
----------------------------------------------------------------

**Looks like** ``/tasks is not connected to namespace``, repeating at exactly
the node's ping interval, while the node's own log is quiet and it believes it
is online. The node stays offline in the UI and never recovers on its own.

**Cause** the transport is alive but the server has no session for that client,
so python-socketio drops every event before any handler runs. The ping that
would mark the node online is dropped along with everything else. The node has
no reason to reconnect, because from its point of view nothing failed.

**Confirm** the interval between repeats is constant and matches the ping
interval. A constant interval means a timer, not a retry with backoff:

.. code-block:: bash

    grep "is not connected to namespace" server.log | cut -c1-19 | uniq -c | tail -20

**Fix** shipped in this PR, node side: the ping worker now checks whether the
socket is actually connected and reconnects when it is not.

**Important** that fix lives in the node, so it does nothing for a node that is
already stuck and cannot be redeployed on demand. See section 4 for the
stopgap.

3. A run that is handed out forever
-----------------------------------

**Looks like** these three lines cycling for one node, for a task that finished
long ago:

.. code-block:: text

    POST api/token/container
    WARNING - Node <id> attempts to generate a key for completed task <task>
    PATCH api/run/<run>

**Cause** the server assigns ``finished_at`` from whatever the payload contains,
including nothing at all, and it selects open runs on ``finished_at IS NULL``.
A node that patches a run without that field therefore reopens it. The node
fetches it again on the next sync, tries to obtain a container token, is refused
because the task is complete, patches again, and so on.

**Confirm** the run has a status but no finish time:

.. code-block:: bash

    curl -s -H "Authorization: Bearer $TOKEN" $SERVER/api/run/<run_id> \
      | jq '{id, status, started_at, finished_at}'

``status`` set and ``finished_at: null`` is the signature.

Note that this one usually arrives in bursts rather than as a continuous loop.
The node picks up its open runs on each sync cycle, fails on the same one, and
then goes quiet until the next cycle or a restart. Poll ``started_at`` a minute
apart: if it does not move, you are between bursts, not fixed.

**Fix** shipped in this PR, node side: send ``finished_at`` explicitly when
failing a run.

**Clearing an existing one** is not possible through the API. ``PATCH
/api/run/<id>`` with ``finished_at`` returns HTTP 500 for a user token, so an
already-looping run keeps going until the node is updated. It is noisy rather
than harmful, but it does generate continuous blob traffic, which is worth
knowing if you are also chasing section 1.

4. The unknown-session guard misfiring
--------------------------------------

This section only applies if you are running the temporary guard from
``temp/socketio-abort-unknown-session``. It is not part of this PR.

**Looks like** ``closing its connection so it can identify itself again`` with
**the same client id** coming back every few seconds.

**Cause** the guard is meant to abort a stale connection once so the client
reconnects. The first version removed the socket from ``server.eio.sockets``
immediately after asking engine.io to close it. Every later abort attempt on
that client then raised ``KeyError``, which was swallowed, so the connection was
never actually closed. The client kept its transport, had all of its events
dropped including pings, and went offline. This took 9 of 10 nodes down.

**Confirm** count distinct client ids against total aborts:

.. code-block:: bash

    grep "closing its connection" server.log \
      | grep -o "Client [A-Za-z0-9_-]*" | sort | uniq -c | sort -rn | head

Healthy: each id appears once or twice. Broken: one id with dozens of hits.

**Fix** the corrected guard aborts a connection at most once, waits 30 seconds
so a client still completing its handshake is left alone, and disables itself
entirely if it fires more than 20 times in a minute. That last one matters: a
guard that runs away is worse than the problem it solves.

**Watch for** ``Disabling the unknown-session guard`` in the log. That is the
circuit breaker tripping, and it means something systematic is wrong rather than
a few stale clients.

A note on clean disconnects
---------------------------

If you are tempted to solve any of this by disconnecting a client from the
server, measure it first. In python-socketio the client only reconnects while

.. code-block:: python

    will_reconnect = self.reconnection and self.eio.state == 'connected'

A clean server-side close leaves the client engine in state ``disconnected``, so
the client treats it as final and never comes back. A polite disconnect strands
the node permanently. This was verified against a real client on
python-socketio 5.16.4 and engineio 4.13.4, and it is the reason the guard
aborts the transport instead of closing it.
