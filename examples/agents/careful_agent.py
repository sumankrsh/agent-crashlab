"""The retry agent with the three fixes that make retries safe.

FIX 1: every payment and email carries an idempotency key derived from the
       invoice number, so a retry or a duplicated request can't pay twice.
FIX 2: progress is checkpointed in `memory`, which survives a crash, so a
       restart skips the work already done.
FIX 3: a list page marked `partial` is fetched again instead of trusted.
"""

from agent_crashlab import ToolError, ToolTimeout


def with_retries(tools, fn, attempts=8, **args):
    for attempt in range(attempts):
        try:
            return fn(**args)
        except ToolTimeout:
            pass
        except ToolError as exc:
            if not exc.transient:
                raise
        tools.sleep(min(0.5 * 2**attempt, 30))
    return fn(**args)


def run(task, tools, memory):
    done = memory.setdefault("done", {})  # invoice id -> list of finished steps

    cursor = memory.get("cursor")
    while True:
        page = with_retries(tools, tools.list_invoices, status="approved", cursor=cursor)
        if page.get("partial"):  # FIX 3
            continue
        for invoice in page["items"]:
            steps = done.setdefault(invoice["id"], [])
            if "paid" not in steps:
                with_retries(
                    tools,
                    tools.pay_invoice,
                    invoice_id=invoice["id"],
                    amount=invoice["amount"],
                    idempotency_key=f"pay-{invoice['id']}",  # FIX 1
                )
                steps.append("paid")  # FIX 2
            if "emailed" not in steps:
                with_retries(
                    tools,
                    tools.send_email,
                    to=invoice["email"],
                    subject=f"Remittance advice: {invoice['id']}",
                    body=f"We have paid {invoice['currency']} {invoice['amount']:,.2f} against invoice {invoice['id']}.",
                    idempotency_key=f"remit-{invoice['id']}",  # FIX 1
                )
                steps.append("emailed")
        cursor = page["next_cursor"]
        if cursor is None:
            return
        memory["cursor"] = cursor  # FIX 2
