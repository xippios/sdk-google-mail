"""FastAPI router for Gmail.

One route per task in ``tasks.__all__``, at ``/google-mail/<task>``, but those
``tasks.BULK_TASKS`` names, which core skips: the router is passed ``tasks.__all__`` whole
(ADR-0019). A task whose parameters are all scalars is a ``GET`` with query parameters; a
task with any non-scalar parameter is a ``POST`` whose JSON body is keyed by parameter
name (ADR-0007). No logic lives here (ADR-0001). The consumer mounts it; it is never
served standalone.
"""

from xippios.core.http import router_from_tasks

from xippios.google.mail import tasks
from xippios.google.mail._client import SOURCE

router = router_from_tasks(SOURCE, [getattr(tasks, name) for name in tasks.__all__])

__all__ = ["router"]
