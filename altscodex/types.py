# DeOAuth get_token 응답을 정규화한 슬롯 정보 TypedDict 정의
"""Typed return values for the AltsCodex SDK."""

from __future__ import annotations

from typing import Optional, TypedDict


class SlotInfo(TypedDict, total=False):
    """Slot (account) information returned by the DeOAuth server.

    Use ``id`` as your stable user identifier.
    """

    id: Optional[str]
    access_token: Optional[str]
    content_address: Optional[str]
    token_nickname: Optional[str]
    tr_cnt: Optional[int]
    code: Optional[str]
