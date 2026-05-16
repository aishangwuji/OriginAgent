import pytest

from OpenHome.agent.tools.message import MessageTool
from OpenHome.bus.events import OutboundMessage

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.asyncio
async def test_message_tool_returns_error_when_no_target_context() -> None:
    tool = MessageTool()
    result = await tool.execute(content="test")
    assert result == "Error: No target channel/chat specified"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "not a list",
        [["ok"], "row-not-a-list"],
        [["ok", 42]],
        [[None]],
    ],
)
async def test_message_tool_rejects_malformed_buttons(bad) -> None:
    """``buttons`` must be ``list[list[str]]``; the tool validates the shape
    up front so a malformed LLM payload errors visibly instead of slipping
    into the channel layer where Telegram would silently reject the frame."""
    tool = MessageTool()
    result = await tool.execute(
        content="hi", channel="telegram", chat_id="1", buttons=bad,
    )
    assert result == "Error: buttons must be a list of list of strings"


@pytest.mark.asyncio
async def test_message_tool_marks_channel_delivery_only_when_enabled() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    token = tool.set_cross_target_grant(True)

    try:
        await tool.execute(content="normal", channel="telegram", chat_id="1")
        delivery_token = tool.set_record_channel_delivery(True)
        try:
            await tool.execute(content="cron", channel="telegram", chat_id="1")
        finally:
            tool.reset_record_channel_delivery(delivery_token)
    finally:
        tool.reset_cross_target_grant(token)

    assert sent[0].metadata == {}
    assert sent[1].metadata == {"_record_channel_delivery": True}


@pytest.mark.asyncio
async def test_message_tool_records_media_deliveries_for_allowed_file(tmp_path) -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    attachment = workspace / "generated.png"
    attachment.write_bytes(_PNG)
    tool = MessageTool(send_callback=_send, workspace=workspace)

    await tool.execute(
        content="image",
        channel="websocket",
        chat_id="chat-1",
        media=["generated.png"],
    )

    assert sent[0].metadata == {"_record_channel_delivery": True}


@pytest.mark.asyncio
async def test_message_tool_inherits_metadata_for_same_target() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    slack_meta = {"slack": {"thread_ts": "111.222", "channel_type": "channel"}}
    tool.set_context("slack", "C123", metadata=slack_meta)

    await tool.execute(content="thread reply")

    assert sent[0].metadata == slack_meta


@pytest.mark.asyncio
async def test_message_tool_does_not_inherit_metadata_for_cross_target() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    tool.set_context(
        "slack",
        "C123",
        metadata={"slack": {"thread_ts": "111.222", "channel_type": "channel"}},
    )
    token = tool.set_cross_target_grant(True)

    try:
        await tool.execute(content="channel reply", channel="slack", chat_id="C999")
    finally:
        tool.reset_cross_target_grant(token)

    assert sent[0].metadata == {}


@pytest.mark.asyncio
async def test_message_tool_rejects_cross_target_without_runtime_grant() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    tool.set_context("slack", "C123")

    with pytest.raises(Exception) as exc:
        await tool.execute(content="channel reply", channel="slack", chat_id="C999")

    assert "Cross-target" in str(exc.value)
    assert sent == []


@pytest.mark.asyncio
async def test_message_tool_resolves_relative_media_paths(tmp_path) -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    attachment = workspace / "output" / "image.png"
    attachment.parent.mkdir()
    attachment.write_bytes(_PNG)
    tool = MessageTool(send_callback=_send, workspace=workspace)

    await tool.execute(
        content="see attached",
        channel="telegram",
        chat_id="1",
        media=["output/image.png"],
    )

    expected = str(attachment.resolve())
    assert sent[0].media == [expected]


@pytest.mark.asyncio
async def test_message_tool_resolves_relative_media_paths_from_active_workspace(tmp_path) -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    attachment = workspace / "output" / "image.png"
    attachment.parent.mkdir()
    attachment.write_bytes(_PNG)
    tool = MessageTool(send_callback=_send, workspace=workspace)

    await tool.execute(
        content="see attached",
        channel="telegram",
        chat_id="1",
        media=["output/image.png"],
    )

    assert sent[0].media == [str(attachment.resolve())]


@pytest.mark.asyncio
async def test_message_tool_rejects_absolute_media_path_outside_allowed_roots(tmp_path) -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(_PNG)
    tool = MessageTool(send_callback=_send, workspace=workspace)

    result = await tool.execute(
        content="see attached",
        channel="telegram",
        chat_id="1",
        media=[str(outside)],
    )

    assert result == "Error: media attachment is outside allowed attachment roots"
    assert sent == []


@pytest.mark.asyncio
async def test_message_tool_allows_explicit_attachment_root(tmp_path) -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    artifact = artifacts / "image.png"
    artifact.write_bytes(_PNG)
    tool = MessageTool(send_callback=_send, workspace=workspace, attachment_roots=[artifacts])

    await tool.execute(
        content="see attached",
        channel="telegram",
        chat_id="1",
        media=[str(artifact)],
    )

    assert sent[0].media == [str(artifact.resolve())]


@pytest.mark.asyncio
async def test_message_tool_rejects_url_media_paths() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)

    url = "https://example.com/image.png"

    result = await tool.execute(
        content="see attached",
        channel="telegram",
        chat_id="1",
        media=[url],
    )

    assert result == "Error: HTTP/HTTPS media attachments are not allowed"
    assert sent == []


@pytest.mark.asyncio
async def test_message_tool_rejects_media_symlink(tmp_path) -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = workspace / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    tool = MessageTool(send_callback=_send, workspace=workspace)

    result = await tool.execute(
        content="see attached",
        channel="telegram",
        chat_id="1",
        media=["link.txt"],
    )

    assert result == "Error: media attachments may not be symlinks"
    assert sent == []


@pytest.mark.asyncio
async def test_message_tool_rejects_oversized_media(tmp_path) -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    attachment = workspace / "large.bin"
    attachment.write_bytes(b"x" * 4)
    tool = MessageTool(send_callback=_send, workspace=workspace, max_media_bytes=3)

    result = await tool.execute(
        content="see attached",
        channel="telegram",
        chat_id="1",
        media=["large.bin"],
    )

    assert result == "Error: media attachment exceeds maximum size (3 bytes)"
    assert sent == []


@pytest.mark.asyncio
async def test_message_tool_rejects_too_many_media_items(tmp_path) -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = MessageTool(send_callback=_send, workspace=workspace, max_media_count=1)

    result = await tool.execute(
        content="see attached",
        channel="telegram",
        chat_id="1",
        media=["a.png", "b.png"],
    )

    assert result == "Error: media may contain at most 1 attachments"
    assert sent == []


@pytest.mark.asyncio
async def test_message_tool_rejects_button_limits() -> None:
    tool = MessageTool()

    too_many_rows = [["x"]] * 6
    too_many_cols = [["x"] * 6]
    too_long_label = [["x" * 65]]

    assert await tool.execute(content="hi", channel="telegram", chat_id="1", buttons=too_many_rows) == (
        "Error: buttons may contain at most 5 rows"
    )
    assert await tool.execute(content="hi", channel="telegram", chat_id="1", buttons=too_many_cols) == (
        "Error: each button row may contain at most 5 labels"
    )
    assert await tool.execute(content="hi", channel="telegram", chat_id="1", buttons=too_long_label) == (
        "Error: button labels may be at most 64 characters"
    )


@pytest.mark.asyncio
async def test_message_tool_resolves_mixed_allowed_media_paths(tmp_path) -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    relative = workspace / "output" / "relative.png"
    relative.parent.mkdir()
    relative.write_bytes(_PNG)
    artifact = artifacts / "absolute.png"
    artifact.write_bytes(_PNG)
    tool = MessageTool(send_callback=_send, workspace=workspace, attachment_roots=[artifacts])

    await tool.execute(
        content="see attached",
        channel="telegram",
        chat_id="1",
        media=[
            "output/relative.png",
            str(artifact),
        ],
    )

    assert sent[0].media == [
        str(relative.resolve()),
        str(artifact.resolve()),
    ]
@pytest.mark.asyncio
async def test_message_allows_verified_png_attachment(tmp_path):
    sent = []

    async def send(msg):
        sent.append(msg)

    image = tmp_path / "image.bin"
    image.write_bytes(_PNG)
    tool = MessageTool(send_callback=send, default_channel="cli", default_chat_id="direct", workspace=tmp_path)

    result = await tool.execute("hi", media=["image.bin"])

    assert "Message sent" in result
    assert sent[0].media == [str(image.resolve())]


@pytest.mark.asyncio
async def test_message_rejects_text_disguised_as_png(tmp_path):
    async def send(msg):
        raise AssertionError("should not send")

    fake = tmp_path / "secret.png"
    fake.write_text('{"token":"secret"}', encoding="utf-8")
    tool = MessageTool(send_callback=send, default_channel="cli", default_chat_id="direct", workspace=tmp_path)

    result = await tool.execute("hi", media=["secret.png"])

    assert result == "Error: attachment type is not allowed by message media policy"


@pytest.mark.asyncio
async def test_message_rejects_blocked_secret_suffix(tmp_path):
    async def send(msg):
        raise AssertionError("should not send")

    secret = tmp_path / ".env"
    secret.write_text("TOKEN=secret", encoding="utf-8")
    tool = MessageTool(send_callback=send, default_channel="cli", default_chat_id="direct", workspace=tmp_path)

    result = await tool.execute("hi", media=[".env"])

    assert result == "Error: attachment type is not allowed by message media policy"
