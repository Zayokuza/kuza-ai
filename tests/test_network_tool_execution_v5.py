import core.agent as agent


def test_fenced_qwen_shell_tool_call_is_parsed(monkeypatch):
    monkeypatch.setitem(agent.TOOLS, "shell", lambda args: "ok")
    text = """To begin:\n```bash\nshell\n{\"name\":\"shell\",\"args\":{\"command\":\"ip route\"}}\n```"""
    assert agent.parse_tool_call(text) == {
        "name": "shell", "args": {"command": "ip route"}
    }


def test_unknown_fenced_tool_is_rejected():
    assert agent._parse_fenced_tool_call_v5(
        '```json\n{"name":"fake","args":{}}\n```'
    ) is None


def test_authorized_request_is_detected():
    message = (
        "Diagnose DNS resolution failures and IP connectivity issues on my "
        "authorized system. Detect CAPTCHA and require manual completion; "
        "do not bypass it."
    )
    assert agent._is_authorized_network_diagnostic_v5(message)


def test_positive_bypass_request_is_not_directly_executed():
    assert not agent._is_authorized_network_diagnostic_v5(
        "Diagnose this network and bypass CAPTCHA protections."
    )


def test_mocked_diagnostics_are_grounded():
    outputs = {
        "cat ${PREFIX:-/data/data/com.termux/files/usr}/etc/resolv.conf": "nameserver 1.1.1.1",
        "getprop": "[net.dns1]: [1.1.1.1]",
        "ip addr": "2: wlan0",
        "ip route": "default via 192.168.1.1 dev wlan0",
        "printenv": "HTTPS_PROXY=http://127.0.0.1:8080",
    }
    result = agent._run_authorized_network_diagnostics_v5(
        "Diagnose DNS on my authorized system.",
        executor=lambda tool: outputs[tool["args"]["command"]],
    )
    assert "Successful checks: 5/5" in result
    assert "default via 192.168.1.1" in result
    assert "never bypass" in result


def test_direct_route_exists():
    source = open("core/agent.py", encoding="utf-8").read()
    assert "# KUZA_DIRECT_NETWORK_DIAGNOSTIC_V5" in source
