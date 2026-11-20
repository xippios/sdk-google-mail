"""``scripts/refresh_upstream.py``'s ``FETCHED.md`` and its writing, offline.

Nothing here sends a request: ``render_fetched``, ``write_snapshot`` and ``compare`` take
what a capture run fetched, here one row made up for the test alone, written under
``tmp_path``, never to ``docs/upstream/``. ``OBSERVED.md`` is the one file under
``docs/upstream/`` written by hand (conventions §3, sdk-core ADR-0028), which the table
lists in a row of its own where it exists.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "refresh_upstream.py"
FETCHED = "2026-11-20T09:00:00Z"
HAND = "written by hand: what upstream does but never documents; not fetched or checked"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("refresh_upstream", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def a_row(script: ModuleType) -> object:
    """A row of a page kept byte for byte, as a capture run makes one."""
    response = script.Response(
        url="https://developers.google.com/workspace/gmail/api/reference/quota",
        status=200,
        body=b"# Usage limits\n",
        etag=None,
        last_modified=None,
        redirects=(),
    )
    page = script.Page(response.url, "pages/quota.md", note="a note")
    return script.Row(page, response, response.body)


def render(script: ModuleType, *, observed: bool) -> str:
    text: str = script.render_fetched([a_row(script)], FETCHED, observed)
    return text


def test_fetched_md_lists_observed_md_in_a_row_of_its_own(script: ModuleType) -> None:
    """Once ``OBSERVED.md`` exists, the table lists it as written by hand."""
    text = render(script, observed=True)
    row = f"| {script.OBSERVED} | - | - | - | - | - | - | - | {HAND} |"
    assert row in text.splitlines()
    assert f"edited by hand but `{script.OBSERVED}`, where it exists" in " ".join(text.split())


def test_fetched_md_has_no_row_of_observed_md_while_there_is_none(script: ModuleType) -> None:
    text = render(script, observed=False)
    assert f"| {script.OBSERVED} |" not in text


def test_fetched_md_cites_the_standing_record_of_sdk_core(script: ModuleType) -> None:
    """The two sentences the script writes cite sdk-core ADR-0028, which restates sdk-core
    ADR-0003 at core 0.4.0, by the repository's name."""
    text = " ".join(render(script, observed=False).split())
    assert text.count("(sdk-core ADR-0028)") == 2
    assert "ADR-0003" not in text


def test_a_snapshot_leaves_observed_md_as_written_and_check_reads_it(
    script: ModuleType, tmp_path: Path
) -> None:
    """The run writes its files and ``FETCHED.md`` and never touches the notes, and
    ``--check`` renders the table with their row where the file exists."""
    notes = tmp_path / script.OBSERVED
    notes.write_text("# What Gmail does but never documents\n", encoding="utf-8")
    (tmp_path / "FETCHED.md").write_text("old\n", encoding="utf-8")
    script.write_snapshot(tmp_path, [a_row(script)], render(script, observed=True))
    assert notes.read_text(encoding="utf-8") == "# What Gmail does but never documents\n"
    assert (tmp_path / "pages/quota.md").read_bytes() == b"# Usage limits\n"
    assert script.compare(tmp_path, [a_row(script)]) == []
    notes.unlink()
    assert script.compare(tmp_path, [a_row(script)]) == ["FETCHED.md"], "the row of a file gone"


def test_no_page_is_kept_as_observed_md(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page recorded as ``OBSERVED.md`` stops the run before any request."""
    page = script.Page("https://developers.google.com/workspace/gmail/api/notes", script.OBSERVED)
    monkeypatch.setattr(script, "PAGES", (page,))
    monkeypatch.setattr(script, "SPEC_URL", "")

    def no_request(url: str, expect: int = 200) -> None:
        raise AssertionError(f"a request was sent to {url}")

    monkeypatch.setattr(script, "fetch", no_request)
    with pytest.raises(SystemExit, match="written by hand"):
        script.main(["--out", "unused"])
