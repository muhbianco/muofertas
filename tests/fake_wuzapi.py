"""WuzAPI falsa (httpx.MockTransport), com as duas portas e os formatos do spec 1.0.9."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

ADMIN = "admin-token-de-teste"
SESSION = "session-token-de-teste"


def group(jid: str, name: str, members: int, *, announce: bool = False) -> dict[str, Any]:
    people = [
        {"IsAdmin": i == 0, "IsSuperAdmin": i == 0, "JID": f"55119000000{i:02d}@s.whatsapp.net"}
        for i in range(members)
    ]
    return {"JID": jid, "Name": name, "IsAnnounce": announce, "Participants": people}


@dataclass
class FakeWuzapi:
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    logged_in: bool = False
    jid: str = ""
    groups: list[dict[str, Any]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    down: bool = False
    # envio: ok | refuse (500) | timeout (depois de enviar) | connect (nem conectou)
    send_mode: str = "ok"
    image_mode: str = "ok"  # ok | refuse (400: a foto não saiu, o texto pode ir)
    sent: list[dict[str, Any]] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    @staticmethod
    def _ok(data: Any, code: int = 200) -> httpx.Response:
        return httpx.Response(code, json={"code": code, "data": data, "success": True})

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if self.down:
            raise httpx.ConnectError("wuzapi fora", request=request)
        if path.startswith("/admin/"):
            if request.headers.get("Authorization") != ADMIN:
                return httpx.Response(401, json={"error": "unauthorized"})
            if request.method == "POST" and path == "/admin/users":
                body = json.loads(request.content)
                if body.get("token") != SESSION:
                    return httpx.Response(400, json={"error": "token"})
                self.sessions["abc123"] = body
                return self._ok({"id": "abc123", "name": body["name"]}, 201)
            if request.method == "GET" and path.startswith("/admin/users/"):
                sid = path.rsplit("/", 1)[1]
                if sid not in self.sessions:
                    return httpx.Response(404, json={"error": "user not found"})
                return self._ok(
                    [
                        {
                            "id": sid,
                            "jid": self.jid if self.logged_in else "",
                            "connected": True,
                            "loggedIn": self.logged_in,
                            "token": SESSION,  # a porta admin devolve o token: nunca exibir
                        }
                    ]
                )
            return httpx.Response(404)
        if request.headers.get("token") != SESSION:
            return httpx.Response(401, json={"error": "unauthorized"})
        if path == "/session/connect":
            return self._ok({"details": "Connected!"})
        if path == "/session/status":
            return self._ok({"Connected": True, "LoggedIn": self.logged_in})
        if path == "/session/qr":
            qr = "" if self.logged_in else "data:image/png;base64,iVBORw0KGgo="
            return self._ok({"QRCode": qr})
        if path == "/session/pairphone":
            return self._ok({"LinkingCode": "9H3J-H3J8"})
        if path == "/session/logout":
            self.logged_in = False
            return self._ok({"Details": "Logged out"})
        if path == "/chat/send/text":
            body = json.loads(request.content)
            if self.send_mode == "connect":
                raise httpx.ConnectError("sem rota", request=request)
            if self.send_mode == "refuse":
                return httpx.Response(500, json={"error": "failed to send"})
            self.sent.append(body)
            if self.send_mode == "timeout":
                raise httpx.ReadTimeout("sem resposta", request=request)
            return self._ok({"Details": "Sent", "Id": f"MSG{len(self.sent)}"})
        if path == "/chat/send/image":
            body = json.loads(request.content)
            if self.image_mode == "refuse":
                return httpx.Response(400, json={"error": "could not decode image"})
            self.sent.append(body)
            return self._ok({"Details": "Sent", "Id": f"IMG{len(self.sent)}"})
        if path == "/group/list":
            return self._ok({"Groups": self.groups})
        if path == "/group/invitelink":
            jid = request.url.params.get("groupJID", "")
            return self._ok({"InviteLink": f"https://chat.whatsapp.com/{jid[:6]}X"})
        if path == "/group/announce":
            body = json.loads(request.content)
            for g in self.groups:
                if g["JID"] == body["GroupJID"]:
                    g["IsAnnounce"] = body["Announce"]
            return self._ok({"Details": "ok"})
        return httpx.Response(404)
