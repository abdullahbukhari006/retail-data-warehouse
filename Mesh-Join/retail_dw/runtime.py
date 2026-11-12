"""Coordinate independent workers for verification and warehouse loading."""

from pathlib import Path
from time import monotonic

from retail_dw.execution import RunControl
from retail_dw.buffer import PriorityStreamBuffer
from retail_dw.meshjoin import MeshJoinThread
from retail_dw.online import OnlineThread
from retail_dw.periodic import PeriodicThread


def check_threads(config, data_dir, online_file, settings, limit=1000, hours=48):
    return execute_threads(config, data_dir, online_file, settings, limit, hours)


def run_warehouse(config, data_dir, online_file, settings, limit=None, hours=48, interactive=False,
                  initial_mode="both", console_factory=None):
    result = execute_threads(config, data_dir, online_file, settings, limit, hours, warehouse=True,
                             interactive=interactive, initial_mode=initial_mode, console_factory=console_factory)
    from retail_dw.warehouse import warehouse_summary

    result["warehouse"] = warehouse_summary(config)
    return result


def execute_threads(config, data_dir, online_file, settings, limit, hours, warehouse=False,
                    interactive=False, initial_mode="both", console_factory=None):
    data_dir = Path(data_dir).resolve()
    online_file = Path(online_file).resolve()
    if (limit is not None and limit <= 0) or not 1 <= hours <= settings.periodic_groups:
        raise ValueError("Thread execution needs a positive online limit and 1–48 periodic hours.")
    for path in (online_file, data_dir / "assigned_sales.csv"):
        if not path.is_file():
            raise ValueError(f"Thread input does not exist: {path}")
    control = RunControl(settings)
    control.set_mode(initial_mode)
    control.set_stats(online_inserted=0, periodic_prepared=0, periodic_inserted=0,
                      already_loaded=0, already_prepared=0)
    control.shedding_enabled = True
    buffer = PriorityStreamBuffer(maxsize=settings.stream_buffer_limit)
    console = None
    if interactive:
        from retail_dw.commands import OperatorConsole

        console = (console_factory or OperatorConsole)(control, buffer)
    periodic = PeriodicThread(config, buffer, control, hours, commit_releases=warehouse, warehouse=warehouse)
    online = OnlineThread(online_file, data_dir / "assigned_sales.csv", buffer, control, limit)

    def verify_output(output):
        online_count = 0
        periodic_ids = []
        for enriched in output:
            transaction = enriched.transaction
            if (enriched.customer["customer_id"] != transaction.customer_id
                    or enriched.product["product_id"] != transaction.product_id):
                raise ValueError("A threaded MESHJOIN result has an incorrect master match.")
            if transaction.sales_mode == "Online":
                online_count += 1
            else:
                periodic_ids.append(transaction.source_line_id)
        control.add_stats(enriched_online=online_count, enriched_periodic=len(periodic_ids))

    join = MeshJoinThread(config, buffer, verify_output, control,
                          on_ack=periodic.acknowledge, shedding=True, hours=hours, warehouse=warehouse)
    started = []
    cancelled = False
    began = monotonic()
    try:
        join.start()
        started.append(join)
        while not control.ready.wait(0.05):
            if join.finished.is_set():
                break
        if not control.abort.is_set():
            if console is not None:
                console.start()
            for worker in (online, periodic):
                worker.start()
                started.append(worker)
            while not control.abort.is_set():
                if console is not None:
                    console.poll()
                if control.stop.is_set():
                    cancelled = not join.finished.is_set()
                    break
                if (console is None and online.finished.is_set() and control.speed() == "peak"
                        and control.snapshot()["shed_eligible_pending"]):
                    control.set_speed("non-peak")
                    control.set_stats(automatic_recovery=True)
                if online.finished.is_set() and periodic.finished.is_set():
                    control.producers_done.set()
                if join.finished.wait(0.05):
                    if console is None:
                        break
                    if not control.snapshot().get("workers_complete"):
                        control.set_stats(workers_complete=True)
                        console.write("Selected input completed. Status remains available; enter quit to close.")
                    # A completed join no longer supplies the coordinator wait.
                    control.stop.wait(0.05)
    except KeyboardInterrupt:
        cancelled = True
        control.stop.set()
        control.set_speed("non-peak")
    finally:
        control.stop.set()
        for worker in (online, periodic):
            if worker in started:
                worker.join()
        control.producers_done.set()
        if join in started:
            join.join()
    if not control.errors.empty():
        name, error = control.errors.get_nowait()
        raise RuntimeError(f"{name} failed: {error}") from error
    result = control.snapshot()
    result.update(stream_buffer=buffer.qsize(), cancelled=cancelled,
                  elapsed_seconds=round(monotonic() - began, 3))
    if (result["online_submitted"] != result["enriched_online"]
            or result["periodic_submitted"] != result["enriched_periodic"]
            or result.get("hash_transactions", 0) or result.get("queue_chunks", 0)
            or result["stream_buffer"] or result["shed_eligible_pending"]):
        raise ValueError("Threaded input/output counts or final buffers did not reconcile.")
    return result
