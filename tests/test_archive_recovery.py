import json

from forge.archive import Archive


def test_archive_ignores_truncated_final_line(tmp_path):
    path = tmp_path / "archive.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"text": "low", "score": 1.0, "gen": 0}',
                '{"text": "high", "score": 5.0, "gen": 1}',
                '{"text": "broken", "score": 9.0',
            ]
        )
    )

    archive = Archive(str(path), capacity=10)

    assert [item["text"] for item in archive.items] == ["high", "low"]
    assert archive.best["score"] == 5.0


def test_archive_physically_truncates_broken_tail(tmp_path):
    path = tmp_path / "archive.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"text": "low", "score": 1.0, "gen": 0}',
                '{"text": "high", "score": 5.0, "gen": 1}',
                '{"text": "broken", "score": 9.0',
            ]
        )
    )

    Archive(str(path), capacity=10)

    content = path.read_text()
    assert content.endswith("\n")
    assert "broken" not in content

    archive = Archive(str(path), capacity=10)
    archive.add({"text": "new", "score": 7.0, "gen": 2})

    lines = path.read_text().splitlines()
    assert len(lines) == 3
    for line in lines:
        json.loads(line)  # each line parses independently; no concatenation corruption
