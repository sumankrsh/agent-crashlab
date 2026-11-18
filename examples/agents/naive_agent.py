"""A payment agent that works perfectly when nothing goes wrong.

It pages through the approved invoices, pays each one and emails the supplier.
It has no retries, no idempotency keys and no checkpoint, so any tool failure
stops it, and a restart after a crash starts the whole job again.
"""


def run(task, tools, memory):
    cursor = None
    while True:
        page = tools.list_invoices(status="approved", cursor=cursor)
        for invoice in page["items"]:
            tools.pay_invoice(invoice_id=invoice["id"], amount=invoice["amount"])
            tools.send_email(
                to=invoice["email"],
                subject=f"Remittance advice: {invoice['id']}",
                body=f"We have paid {invoice['currency']} {invoice['amount']:,.2f} against invoice {invoice['id']}.",
            )
        cursor = page["next_cursor"]
        if cursor is None:
            return
