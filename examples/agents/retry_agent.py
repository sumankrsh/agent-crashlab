"""The naive agent plus retries with backoff: the usual first fix.

Timeouts and transient errors are retried up to five times. But a timeout
does not mean the call failed: when the reply is lost after the payment went
through, the retry pays the invoice a second time. It also has no
idempotency keys, no checkpoint and ignores the `partial` flag on list pages.
"""

from agent_crashlab import ToolError, ToolTimeout


def with_retries(tools, fn, **args):
    for attempt in range(5):
        try:
            return fn(**args)
        except ToolTimeout:
            pass
        except ToolError as exc:
            if not exc.transient:
                raise
        tools.sleep(0.5 * 2**attempt)
    return fn(**args)


def run(task, tools, memory):
    cursor = None
    while True:
        page = with_retries(tools, tools.list_invoices, status="approved", cursor=cursor)
        for invoice in page["items"]:
            with_retries(tools, tools.pay_invoice, invoice_id=invoice["id"], amount=invoice["amount"])
            with_retries(
                tools,
                tools.send_email,
                to=invoice["email"],
                subject=f"Remittance advice: {invoice['id']}",
                body=f"We have paid {invoice['currency']} {invoice['amount']:,.2f} against invoice {invoice['id']}.",
            )
        cursor = page["next_cursor"]
        if cursor is None:
            return
