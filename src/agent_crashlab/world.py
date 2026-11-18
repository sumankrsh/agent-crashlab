"""The sandbox: an accounts-payable backend whose side effects are all recorded.

The backend is deliberately ordinary. It pays what it is asked to pay, sends
what it is asked to send, and honours an ``idempotency_key`` the way most
payment APIs do: the same key with the same arguments returns the first
result without doing anything again. Everything else is up to the agent.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ToolConflict, ToolError

READ = "read"
WRITE = "write"

TOOLS: dict[str, tuple[str, str]] = {
    "list_invoices": (READ, "list_invoices(status=None, cursor=None) -> {items, next_cursor, partial}"),
    "get_invoice": (READ, "get_invoice(invoice_id) -> invoice"),
    "list_payments": (READ, "list_payments(invoice_id=None) -> {items}"),
    "pay_invoice": (WRITE, "pay_invoice(invoice_id, amount, idempotency_key=None) -> {payment_id, status}"),
    "send_email": (WRITE, "send_email(to, subject, body, idempotency_key=None) -> {message_id}"),
}

STATUSES = {"approved", "pending", "disputed"}


class ScenarioError(ValueError):
    """A scenario file is missing fields or has bad values."""


@dataclass(frozen=True)
class Invoice:
    id: str
    supplier: str
    email: str
    amount: float
    status: str
    currency: str = "GBP"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "supplier": self.supplier,
            "email": self.email,
            "amount": self.amount,
            "currency": self.currency,
            "status": self.status,
        }


@dataclass(frozen=True)
class Task:
    """What the agent is asked to do. It finds the details through its tools."""

    scenario: str
    description: str


@dataclass(frozen=True)
class Scenario:
    name: str
    task: str
    invoices: tuple[Invoice, ...]
    page_size: int = 4

    @property
    def to_pay(self) -> list[Invoice]:
        return [inv for inv in self.invoices if inv.status == "approved"]

    def expected(self) -> dict[str, dict[str, Any]]:
        """Every side effect a correct run makes, exactly once each."""
        out: dict[str, dict[str, Any]] = {}
        for inv in self.to_pay:
            out[f"pay:{inv.id}"] = {"invoice_id": inv.id, "amount": inv.amount}
            out[f"email:{inv.id}"] = {"to": inv.email}
        return out

    def new_backend(self) -> PayablesBackend:
        return PayablesBackend(self)

    def task_for_agent(self) -> Task:
        return Task(scenario=self.name, description=self.task)


BUILTIN_DIR = Path(__file__).parent / "scenarios"


def builtin_scenarios() -> list[str]:
    return sorted(p.stem for p in BUILTIN_DIR.glob("*.yaml"))


def load_scenario(path: str | Path) -> Scenario:
    """Load a scenario from a YAML file, or a built-in one by name (``month-end``)."""
    p = Path(path)
    if not p.is_file() and str(path) in builtin_scenarios():
        p = BUILTIN_DIR / f"{path}.yaml"
    if not p.is_file():
        raise ScenarioError(f"scenario file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{p}: not valid YAML: {exc}") from exc
    return scenario_from_dict(data, source=str(p))


def scenario_from_dict(data: Any, source: str = "scenario") -> Scenario:
    if not isinstance(data, dict):
        raise ScenarioError(f"{source}: expected a mapping at the top level")
    problems: list[str] = []
    name = data.get("name")
    task = data.get("task")
    if not isinstance(name, str) or not name.strip():
        problems.append("'name' is required")
    if not isinstance(task, str) or not task.strip():
        problems.append("'task' is required")
    page_size = data.get("page_size", 4)
    if not isinstance(page_size, int) or isinstance(page_size, bool) or page_size < 1:
        problems.append("'page_size' must be a positive whole number")
    raw = data.get("invoices")
    invoices: list[Invoice] = []
    if not isinstance(raw, list) or not raw:
        problems.append("'invoices' must be a non-empty list")
        raw = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        where = f"invoices[{i}]"
        if not isinstance(item, dict):
            problems.append(f"{where} must be a mapping")
            continue
        missing = [k for k in ("id", "supplier", "email", "amount", "status") if k not in item]
        if missing:
            problems.append(f"{where} is missing {', '.join(missing)}")
            continue
        if item["id"] in seen:
            problems.append(f"{where}: duplicate id {item['id']!r}")
        seen.add(str(item["id"]))
        if item["status"] not in STATUSES:
            problems.append(f"{where}: status must be approved, pending or disputed")
        amount = item["amount"]
        if not isinstance(amount, int | float) or isinstance(amount, bool) or amount <= 0:
            problems.append(f"{where}: amount must be a positive number")
            continue
        invoices.append(
            Invoice(
                id=str(item["id"]),
                supplier=str(item["supplier"]),
                email=str(item["email"]),
                amount=round(float(amount), 2),
                status=str(item["status"]),
                currency=str(item.get("currency", "GBP")),
            )
        )
    if problems:
        raise ScenarioError(f"{source}: " + "; ".join(problems))
    if not any(inv.status == "approved" for inv in invoices):
        raise ScenarioError(f"{source}: at least one invoice must be approved, or there is nothing to do")
    return Scenario(name=name.strip(), task=" ".join(task.split()), invoices=tuple(invoices), page_size=page_size)


@dataclass
class Effect:
    """One side effect the backend applied."""

    key: str
    tool: str
    args: dict[str, Any]
    call: int  # index of the agent call that caused it
    delivery: int = 1  # 2 for the second copy of a duplicated request


@dataclass
class PayablesBackend:
    scenario: Scenario
    invoices: dict[str, dict[str, Any]] = field(init=False)
    effects: list[Effect] = field(default_factory=list)
    _keys: dict[str, tuple[str, str, Any]] = field(default_factory=dict)
    _next_id: int = 1
    deduplicated: int = 0  # requests answered from the idempotency store without a new effect

    def __post_init__(self) -> None:
        self.invoices = {inv.id: inv.as_dict() for inv in self.scenario.invoices}
        self._pattern = re.compile("|".join(re.escape(i) for i in sorted(self.invoices, key=len, reverse=True)))

    @staticmethod
    def kind(tool: str) -> str:
        return TOOLS[tool][0]

    def call(self, tool: str, args: dict[str, Any], call: int, delivery: int = 1) -> Any:
        if tool not in TOOLS:
            raise ToolError(tool, "no such tool", transient=False)
        handler = getattr(self, f"_{tool}")
        key = args.get("idempotency_key")
        if TOOLS[tool][0] == WRITE and key is not None:
            fingerprint = json.dumps({k: v for k, v in args.items() if k != "idempotency_key"}, sort_keys=True)
            if key in self._keys:
                seen_tool, seen_fp, result = self._keys[key]
                if seen_tool != tool or seen_fp != fingerprint:
                    raise ToolConflict(tool, f"idempotency key {key!r} was already used with different arguments")
                self.deduplicated += 1
                return copy.deepcopy(result)
            result = handler(call=call, delivery=delivery, **args)
            self._keys[key] = (tool, fingerprint, copy.deepcopy(result))
            return result
        return handler(call=call, delivery=delivery, **args)

    # read tools ---------------------------------------------------------

    def _list_invoices(self, call: int, delivery: int, status: str | None = None, cursor: str | None = None) -> dict:
        rows = [dict(v) for v in self.invoices.values() if status is None or v["status"] == status]
        try:
            start = int(cursor or 0)
        except (TypeError, ValueError):
            raise ToolError("list_invoices", f"bad cursor {cursor!r}", transient=False) from None
        page = rows[start : start + self.scenario.page_size]
        end = start + len(page)
        return {"items": page, "next_cursor": str(end) if end < len(rows) else None, "partial": False}

    def _get_invoice(self, call: int, delivery: int, invoice_id: str) -> dict:
        if invoice_id not in self.invoices:
            raise ToolError("get_invoice", f"unknown invoice {invoice_id!r}", transient=False)
        return dict(self.invoices[invoice_id])

    def _list_payments(self, call: int, delivery: int, invoice_id: str | None = None) -> dict:
        rows = [
            {"invoice_id": e.args["invoice_id"], "amount": e.args["amount"], "payment_id": e.args["payment_id"]}
            for e in self.effects
            if e.tool == "pay_invoice" and (invoice_id is None or e.args["invoice_id"] == invoice_id)
        ]
        return {"items": rows}

    # write tools --------------------------------------------------------

    def _pay_invoice(
        self, call: int, delivery: int, invoice_id: str, amount: float, idempotency_key: str | None = None
    ) -> dict:
        if invoice_id not in self.invoices:
            raise ToolError("pay_invoice", f"unknown invoice {invoice_id!r}", transient=False)
        if not isinstance(amount, int | float) or isinstance(amount, bool) or amount <= 0:
            raise ToolError("pay_invoice", f"bad amount {amount!r}", transient=False)
        payment_id = f"PAY-{self._next_id:04d}"
        self._next_id += 1
        self.effects.append(
            Effect(
                key=f"pay:{invoice_id}",
                tool="pay_invoice",
                args={"invoice_id": invoice_id, "amount": round(float(amount), 2), "payment_id": payment_id},
                call=call,
                delivery=delivery,
            )
        )
        return {"payment_id": payment_id, "status": "paid"}

    def _send_email(
        self, call: int, delivery: int, to: str, subject: str, body: str = "", idempotency_key: str | None = None
    ) -> dict:
        if not isinstance(to, str) or "@" not in to:
            raise ToolError("send_email", f"bad recipient {to!r}", transient=False)
        match = self._pattern.search(f"{subject}\n{body}") if self.invoices else None
        key = f"email:{match.group(0)}" if match else f"email:{to}:{subject}"
        message_id = f"MSG-{self._next_id:04d}"
        self._next_id += 1
        self.effects.append(
            Effect(
                key=key,
                tool="send_email",
                args={"to": to, "subject": subject, "message_id": message_id},
                call=call,
                delivery=delivery,
            )
        )
        return {"message_id": message_id}
