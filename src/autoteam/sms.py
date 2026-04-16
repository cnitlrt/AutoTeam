"""SMS verification provider abstraction + multi-provider chain.

Providers are stored in ``data/sms_providers.json`` (see sms_store). The
list is ordered — the top entry is tried first, the next one is tried
only if the previous fails (out of stock, insufficient funds, wrong
service, …). Each rental carries the originating provider id so cancel /
complete / status queries route back to the right provider.

Currently supported provider types:
  - ``getatext`` — getatext.com
  - ``smspool`` — smspool.net

Adding a new type:
  1. Implement a class with the SMSProvider interface below.
  2. Register it in ``_PROVIDER_CLASSES``.
  3. (Optional) advertise its capabilities to the frontend.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import requests

from autoteam import sms_store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data + errors
# ---------------------------------------------------------------------------


@dataclass
class SMSRental:
    id: str
    number: str
    service: str
    price: float
    provider_id: str
    provider_type: str

    def as_dict(self) -> dict:
        return asdict(self)


class SMSUnavailable(RuntimeError):
    """Provider can't rent a number right now (out of stock, bad service,
    insufficient funds, ...). The chain will try the next provider."""


class SMSTimeout(RuntimeError):
    """We waited out the rental's TTL without getting a code."""


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


class SMSProvider(Protocol):
    type_name: str
    id: str
    label: str

    def rent_number(self, service: str, **kwargs: Any) -> SMSRental: ...
    def poll_code(self, rental_id: str, timeout: int = 300) -> str: ...
    def rental_status(self, rental_id: str) -> dict: ...
    def cancel(self, rental_id: str) -> None: ...
    def mark_complete(self, rental_id: str) -> None: ...
    def get_balance(self) -> float: ...
    def list_services(self) -> list[dict]: ...


# ---------------------------------------------------------------------------
# Getatext implementation
# ---------------------------------------------------------------------------


class GetatextProvider:
    type_name = "getatext"
    BASE = "https://getatext.com/api/v1"

    def __init__(self, api_key: str, *, id: str = "", label: str = ""):
        self.api_key = api_key
        self.id = id or "getatext"
        self.label = label or "getatext"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Auth": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # ---- helpers -------------------------------------------------------

    def _post(self, path: str, json: dict | None = None, ok_status: tuple[int, ...] = (200, 201)) -> dict:
        r = self.session.post(f"{self.BASE}{path}", json=json or {}, timeout=20)
        try:
            data = r.json()
        except ValueError:
            data = {"errors": r.text or f"HTTP {r.status_code}"}
        if r.status_code not in ok_status:
            raise SMSUnavailable(data.get("errors") or f"HTTP {r.status_code}")
        return data

    def _get(self, path: str) -> dict:
        r = self.session.get(f"{self.BASE}{path}", timeout=15)
        r.raise_for_status()
        return r.json()

    # ---- API -----------------------------------------------------------

    def rent_number(self, service: str, **kwargs: Any) -> SMSRental:
        payload: dict = {"service": service}
        for k in ("max_price", "carrier", "keep_carrier", "lock_area_code", "area_codes"):
            if kwargs.get(k) is not None:
                payload[k] = kwargs[k]
        logger.info("[SMS/%s] 租号 service=%s", self.label, service)
        data = self._post("/rent-a-number", json=payload, ok_status=(201,))
        # getatext returns numbers as raw digits (e.g. "4104287665" — US without
        # the country code prefix). Keep exactly what they return; callers strip
        # non-digits when typing into react-phone-number-input inputs that have
        # a country already selected.
        number = str(data.get("number") or "")
        rental = SMSRental(
            id=str(data.get("id")),
            number=number,
            service=str(data.get("service_name") or service),
            price=float(data.get("price") or 0),
            provider_id=self.id,
            provider_type=self.type_name,
        )
        logger.info(
            "[SMS/%s] 已租用 %s (id=%s, price=$%.2f, balance=%s)",
            self.label,
            rental.number,
            rental.id,
            rental.price,
            data.get("new_balance"),
        )
        return rental

    def rental_status(self, rental_id: str) -> dict:
        return self._post("/rental-status", json={"id": int(rental_id)}, ok_status=(200,))

    def poll_code(self, rental_id: str, timeout: int = 300, poll_interval: float = 5.0) -> str:
        deadline = time.time() + timeout
        last_status: str | None = None
        while time.time() < deadline:
            try:
                data = self.rental_status(rental_id)
            except SMSUnavailable as exc:
                logger.warning("[SMS/%s] 查询状态失败 id=%s: %s", self.label, rental_id, exc)
                time.sleep(poll_interval)
                continue
            status = data.get("status")
            code = data.get("code")
            if code:
                return str(code)
            if status and status != last_status:
                logger.info("[SMS/%s] rental %s 状态: %s", self.label, rental_id, status)
                last_status = status
            if status in ("cancelled", "expired", "timeout", "closed"):
                raise SMSTimeout(f"rental {rental_id} ended without code: {status}")
            time.sleep(poll_interval)
        raise SMSTimeout(f"rental {rental_id} timed out after {timeout}s")

    def cancel(self, rental_id: str) -> None:
        try:
            self._post("/cancel-rental", json={"id": int(rental_id)}, ok_status=(200,))
            logger.info("[SMS/%s] 已取消 rental %s", self.label, rental_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[SMS/%s] 取消失败 id=%s: %s", self.label, rental_id, exc)

    def mark_complete(self, rental_id: str) -> None:
        try:
            self._post(f"/rental-status/{rental_id}/completed", ok_status=(200,))
            logger.info("[SMS/%s] 已完成 rental %s", self.label, rental_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[SMS/%s] 标记完成失败 id=%s: %s", self.label, rental_id, exc)

    def get_balance(self) -> float:
        data = self._get("/balance")
        return float(data.get("balance") or 0)

    def list_services(self) -> list[dict]:
        try:
            data = self._get("/prices-info")
        except Exception as exc:
            logger.warning("[SMS/%s] 获取服务列表失败: %s", self.label, exc)
            return []
        # Real response: {"status": "success", "prices": [...]}
        if isinstance(data, dict):
            for key in ("prices", "services"):
                if isinstance(data.get(key), list):
                    return data[key]
            # Single-object shape fallback
            if "api_name" in data:
                return [data]
        if isinstance(data, list):
            return data
        return []


# ---------------------------------------------------------------------------
# SMSPool implementation
# ---------------------------------------------------------------------------


class SMSPoolProvider:
    type_name = "smspool"
    BASE = "https://api.smspool.net"

    # SMSPool uses numeric service IDs. This maps common names we use
    # (like "chatgpt") to the SMSPool service ID. The mapping is populated
    # lazily from the service list endpoint on first use.
    _service_cache: dict[str, str] | None = None

    def __init__(self, api_key: str, *, id: str = "", label: str = ""):
        self.api_key = api_key
        self.id = id or "smspool"
        self.label = label or "smspool"
        self.session = requests.Session()

    # ---- helpers -------------------------------------------------------

    def _post(self, path: str, data: dict | None = None) -> dict:
        payload = dict(data or {})
        payload["key"] = self.api_key
        r = self.session.post(f"{self.BASE}{path}", data=payload, timeout=20)
        try:
            result = r.json()
        except ValueError:
            raise SMSUnavailable(r.text or f"HTTP {r.status_code}")
        if isinstance(result, dict) and result.get("success") == 0:
            raise SMSUnavailable(result.get("message") or "SMSPool error")
        if r.status_code not in (200, 201):
            raise SMSUnavailable(f"HTTP {r.status_code}")
        return result

    def _resolve_service_id(self, service: str) -> str:
        """Map a service name (e.g. 'chatgpt', 'openai') to an SMSPool
        numeric service ID. Falls through to the raw value if not found."""
        if service.isdigit():
            return service
        if self._service_cache is None:
            self._build_service_cache()
        cache = self._service_cache or {}
        key = service.lower().strip()
        if key in cache:
            return cache[key]
        # Try partial match
        for name, sid in cache.items():
            if key in name or name in key:
                return sid
        return service  # pass through as-is, SMSPool will reject if invalid

    def _build_service_cache(self):
        try:
            r = self.session.post(f"{self.BASE}/service/retrieve_all", data={}, timeout=15)
            services = r.json()
            if isinstance(services, list):
                cache: dict[str, str] = {}
                for s in services:
                    name = str(s.get("name") or "").lower().strip()
                    sid = str(s.get("ID") or s.get("id") or "")
                    if name and sid:
                        cache[name] = sid
                SMSPoolProvider._service_cache = cache
        except Exception as exc:
            logger.warning("[SMS/%s] 获取 service 列表失败: %s", self.label, exc)
            SMSPoolProvider._service_cache = {}

    # ---- API -----------------------------------------------------------

    def rent_number(self, service: str, **kwargs: Any) -> SMSRental:
        service_id = self._resolve_service_id(service)
        payload: dict = {
            "country": kwargs.get("country", "US"),
            "service": service_id,
        }
        if kwargs.get("max_price") is not None:
            payload["max_price"] = kwargs["max_price"]
        if kwargs.get("area_codes"):
            payload["areacode"] = kwargs["area_codes"]
        logger.info("[SMS/%s] 租号 service=%s (id=%s)", self.label, service, service_id)
        data = self._post("/purchase/sms", payload)
        number = str(data.get("number") or data.get("phonenumber") or "")
        order_id = str(data.get("order_id") or data.get("orderid") or "")
        rental = SMSRental(
            id=order_id,
            number=number,
            service=str(data.get("service") or service),
            price=float(data.get("price") or data.get("cost") or 0),
            provider_id=self.id,
            provider_type=self.type_name,
        )
        logger.info(
            "[SMS/%s] 已租用 %s (order=%s, price=$%.2f)",
            self.label,
            rental.number,
            rental.id,
            rental.price,
        )
        return rental

    def rental_status(self, rental_id: str) -> dict:
        data = self._post("/sms/check", {"orderid": rental_id})
        # SMSPool returns: {"success":1,"message":{"TimeLeft":"...","sms":"code","full_sms":"...","status":N}}
        # or just {"status": N, "sms": "...", ...}
        msg = data.get("message", data)
        if isinstance(msg, dict):
            status_code = msg.get("status")
            sms_text = msg.get("sms") or msg.get("code") or ""
            full_sms = msg.get("full_sms") or ""
        else:
            status_code = data.get("status")
            sms_text = str(msg) if msg else ""
            full_sms = ""
        # Map SMSPool status codes: 1=pending, 2=cancelled, 3=completed, 4=expired/refunded
        status_map = {1: "pending", 2: "cancelled", 3: "completed", 4: "expired"}
        status_str = status_map.get(int(status_code), str(status_code)) if status_code is not None else "unknown"
        return {
            "id": rental_id,
            "status": status_str,
            "code": sms_text if status_str == "completed" else None,
            "sms": sms_text,
            "full_sms": full_sms,
            "number": data.get("phonenumber") or "",
        }

    def poll_code(self, rental_id: str, timeout: int = 300, poll_interval: float = 5.0) -> str:
        deadline = time.time() + timeout
        last_status: str | None = None
        while time.time() < deadline:
            try:
                data = self.rental_status(rental_id)
            except SMSUnavailable as exc:
                logger.warning("[SMS/%s] 查询状态失败 order=%s: %s", self.label, rental_id, exc)
                time.sleep(poll_interval)
                continue
            status = data.get("status")
            code = data.get("code")
            if code:
                return str(code)
            if status and status != last_status:
                logger.info("[SMS/%s] order %s 状态: %s", self.label, rental_id, status)
                last_status = status
            if status in ("cancelled", "expired"):
                raise SMSTimeout(f"order {rental_id} ended without code: {status}")
            time.sleep(poll_interval)
        raise SMSTimeout(f"order {rental_id} timed out after {timeout}s")

    def cancel(self, rental_id: str) -> None:
        try:
            self._post("/sms/cancel", {"orderid": rental_id})
            logger.info("[SMS/%s] 已取消 order %s", self.label, rental_id)
        except Exception as exc:
            logger.warning("[SMS/%s] 取消失败 order=%s: %s", self.label, rental_id, exc)

    def mark_complete(self, rental_id: str) -> None:
        # SMSPool doesn't have an explicit "complete" endpoint for one-time SMS.
        # Orders auto-complete when the code is received.
        logger.debug("[SMS/%s] mark_complete is a no-op for SMSPool (order=%s)", self.label, rental_id)

    def get_balance(self) -> float:
        data = self._post("/request/balance", {})
        return float(data.get("balance") or 0)

    def list_services(self) -> list[dict]:
        try:
            r = self.session.post(f"{self.BASE}/service/retrieve_all", data={}, timeout=15)
            services = r.json()
            if isinstance(services, list):
                return [
                    {
                        "service_name": s.get("name", ""),
                        "api_name": str(s.get("ID") or s.get("id", "")),
                        "price": s.get("price", "-"),
                        "stock": None,
                        "ttl": None,
                        "multiple_sms": False,
                    }
                    for s in services
                ]
        except Exception as exc:
            logger.warning("[SMS/%s] 获取服务列表失败: %s", self.label, exc)
        return []


# ---------------------------------------------------------------------------
# Provider registry + chain
# ---------------------------------------------------------------------------


_PROVIDER_CLASSES: dict[str, type] = {
    "getatext": GetatextProvider,
    "smspool": SMSPoolProvider,
}


def provider_types() -> list[dict]:
    """Metadata describing types we can create, for the UI."""
    return [
        {
            "type": "getatext",
            "name": "getatext.com",
            "api_key_label": "API Key",
            "help": "从 getatext.com 用户设置页获取 API Key",
        },
        {
            "type": "smspool",
            "name": "smspool.net",
            "api_key_label": "API Key",
            "help": "从 smspool.net Dashboard → API 页获取 API Key",
        },
    ]


def instantiate_provider(entry: dict) -> SMSProvider | None:
    cls = _PROVIDER_CLASSES.get(entry.get("type", ""))
    if not cls:
        logger.warning("[SMS] 未知 provider 类型: %s", entry.get("type"))
        return None
    try:
        return cls(entry["api_key"], id=entry["id"], label=entry.get("label") or entry["type"])
    except Exception as exc:
        logger.warning("[SMS] 创建 provider 失败 id=%s: %s", entry.get("id"), exc)
        return None


def list_live_providers(*, include_disabled: bool = False) -> list[SMSProvider]:
    out: list[SMSProvider] = []
    for entry in sms_store.list_providers():
        if not include_disabled and not entry.get("enabled", True):
            continue
        provider = instantiate_provider(entry)
        if provider is not None:
            out.append(provider)
    return out


def get_provider_by_id(provider_id: str) -> SMSProvider | None:
    entry = sms_store.get_provider(provider_id)
    if not entry:
        return None
    return instantiate_provider(entry)


class SMSChain:
    """Tries providers in order. ``rent_number`` falls through on
    ``SMSUnavailable`` until one succeeds."""

    def __init__(self, providers: list[SMSProvider]):
        self.providers = providers

    def __bool__(self) -> bool:
        return bool(self.providers)

    def rent_number(self, service: str, **kwargs: Any) -> SMSRental:
        if not self.providers:
            raise SMSUnavailable("未配置 SMS 提供商")
        errors: list[str] = []
        for provider in self.providers:
            try:
                return provider.rent_number(service, **kwargs)
            except SMSUnavailable as exc:
                logger.warning("[SMS/%s] 租号失败: %s — 尝试下一个提供商", provider.label, exc)
                errors.append(f"{provider.label}: {exc}")
        raise SMSUnavailable("; ".join(errors) or "所有提供商均不可用")


def get_sms_chain() -> SMSChain:
    return SMSChain(list_live_providers())


def default_service() -> str:
    return os.environ.get("SMS_SERVICE_OPENAI") or "chatgpt"


# ---------------------------------------------------------------------------
# Backwards-compat helpers used by older callers
# ---------------------------------------------------------------------------


def get_sms_provider() -> SMSChain | None:
    """Legacy: return an object exposing ``rent_number``. Rentals returned
    carry provider_id so callers should look up the specific provider via
    ``get_provider_by_id`` for cancel/complete/status."""
    chain = get_sms_chain()
    return chain if chain else None
