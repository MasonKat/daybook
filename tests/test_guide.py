"""G3: the operations guide renders as printable A4 HTML, one routine per page, naming the real buttons."""
import re

from click.testing import CliRunner

from console import guide as G
from console.cli import cli
from console.config import strings


def test_guide_pages_and_buttons(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(cli, ["--db", str(tmp_path / "x.sqlite"), "guide"])
    assert r.exit_code == 0
    html = (tmp_path / "cards" / "운영가이드.html").read_text(encoding="utf-8")
    titles = re.findall(r"<h2>(.*?)</h2>", html)
    assert titles[:4] == ["수업 전 (10분)", "수업 중", "수업 끝", "토요일 (30~40분)"] and "문제가 생기면" in titles
    assert html.count("<section>") == len(titles) and "size: A4" in html
    S = strings()
    for key in ("board_checkin_for", "board_sub_for", "board_moment", "board_teacher_view", "t_publish_do", "t_deploy_do",
                "g_doctor", "g_backup_run", "r_export", "m_rotate", "m_intro_p", "lv_open"):
        assert S[key] in html, key                      # the guide names the buttons exactly as the screens show them
    problems = re.search(r"<h2>문제가 생기면</h2>\s*<ol>(.*?)</ol>", html, re.S).group(1)
    assert problems.count("<li>") == 10
    assert "/s/" not in html and "/p/" not in html     # no link ever on paper


def test_markdown_subset():
    out = G.to_html("# T\n\n## A\n\n1. one **b**\n   - sub `c`\n2. two\n\npara <x>\n")
    assert "<ol><li>one <b>b</b><ul><li>sub <code>c</code></li></ul></li><li>two</li></ol>" in out.replace("\n", "")
    assert "<p>para &lt;x&gt;</p>" in out
