"""V2 核心逻辑测试。

用 Node 直接跑 JS 模块（零依赖，不需要 jsdom 之类的测试框架）。
验证：搜索引擎、URL 识别、计算器、配置存取。
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

NODE = r"C:\Users\Syx21\.dsh\dsh-runtimes\dsh-next-primary-runtime\dependencies\node\bin\node.exe"


@pytest.fixture(scope="module")
def node():
    if not Path(NODE).exists():
        pytest.skip("node 不可用")
    return NODE


def run_js(node, code):
    """在 Node 里执行一段 JS，返回 stdout。"""
    harness = """
    // 最小环境垫片：提供 localStorage，让 config.js 能跑
    const store = {};
    global.localStorage = {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: k => { delete store[k]; },
    };
    global.window = global;
    """ + "\n".join(
        f"require({str(ROOT / 'src' / 'static' / 'web' / f)!r});"
        for f in ("config.js", "search.js")
    ) + "\n" + code

    r = subprocess.run([node, "-e", harness], capture_output=True, text=True,
                       cwd=str(ROOT), timeout=30)
    if r.returncode != 0:
        print("STDERR:", r.stderr[:1500])
    assert r.returncode == 0, r.stderr[:800]
    return r.stdout.strip()


class TestSearchEngine:
    def test_bing_is_default(self, node):
        out = run_js(node, r"""
            const cfg = StartPageConfig.get();
            console.log(StartPageSearch.buildSearchUrl('hello', cfg));
        """)
        assert out == "https://www.bing.com/search?q=hello"

    def test_engine_switch(self, node):
        out = run_js(node, r"""
            StartPageConfig.set({engine:'google'});
            const cfg = StartPageConfig.get();
            console.log(StartPageSearch.buildSearchUrl('hello world', cfg));
        """)
        assert out == "https://www.google.com/search?q=hello%20world"

    def test_custom_engine_placeholder(self, node):
        out = run_js(node, r"""
            StartPageConfig.set({useCustomEngine:true, customEngineUrl:'https://x.com/s?q={}'});
            const cfg = StartPageConfig.get();
            console.log(StartPageSearch.buildSearchUrl('hi', cfg));
        """)
        assert out == "https://x.com/s?q=hi"

    def test_chinese_query_encoded(self, node):
        out = run_js(node, r"""
            StartPageConfig.reset();
            const cfg = StartPageConfig.get();
            console.log(StartPageSearch.buildSearchUrl('天气', cfg));
        """)
        assert "q=" in out and "%" in out  # 中文被编码


class TestUrlDetection:
    def test_plain_word_is_search(self, node):
        out = run_js(node, r"""
            console.log(StartPageSearch.looksLikeUrl('hello'));
        """)
        assert out == "false"

    def test_domain_is_url(self, node):
        out = run_js(node, r"""
            console.log(StartPageSearch.looksLikeUrl('example.com'));
        """)
        assert out == "true"

    def test_full_url_is_url(self, node):
        out = run_js(node, r"""
            console.log(StartPageSearch.looksLikeUrl('https://a.b/c'));
        """)
        assert out == "true"

    def test_resolve_url_gets_protocol(self, node):
        out = run_js(node, r"""
            StartPageConfig.reset();
            const cfg = StartPageConfig.get();
            const r = StartPageSearch.resolve('example.com', cfg);
            console.log(r.type + '|' + r.value);
        """)
        assert out == "url|https://example.com"


class TestCalculator:
    def test_simple_math(self, node):
        out = run_js(node, r"""
            console.log(StartPageSearch.tryCalculate('=1+2'));
        """)
        assert out == "3"

    def test_precedence(self, node):
        out = run_js(node, r"""
            console.log(StartPageSearch.tryCalculate('=1+2*3'));
        """)
        assert out == "7"

    def test_not_an_expression(self, node):
        out = run_js(node, r"""
            console.log(StartPageSearch.tryCalculate('hello'));
        """)
        assert out == "null"

    def test_malformed_returns_null(self, node):
        out = run_js(node, r"""
            console.log(StartPageSearch.tryCalculate('=1+'));
        """)
        assert out == "null"


class TestConfig:
    def test_defaults_have_bing(self, node):
        out = run_js(node, r"""
            StartPageConfig.reset();
            console.log(StartPageConfig.get().engine);
        """)
        assert out == "bing"

    def test_set_persists(self, node):
        out = run_js(node, r"""
            StartPageConfig.set({engine:'baidu'});
            console.log(StartPageConfig.get().engine);
        """)
        assert out == "baidu"

    def test_partial_update_keeps_others(self, node):
        out = run_js(node, r"""
            StartPageConfig.reset();
            StartPageConfig.set({engine:'baidu'});
            console.log(StartPageConfig.get().openInNewTab);
        """)
        assert out == "false"  # 默认值保留


class TestSecurity:
    def test_calc_rejects_code_injection(self, node):
        """=后面必须是算术，不能执行任意代码。"""
        out = run_js(node, r"""
            console.log(StartPageSearch.tryCalculate('=process.exit(1)'));
        """)
        assert out == "null"

    def test_calc_rejects_strings(self, node):
        out = run_js(node, r"""
            console.log(StartPageSearch.tryCalculate('="abc"'));
        """)
        assert out == "null"
